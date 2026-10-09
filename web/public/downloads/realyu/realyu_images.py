"""Realyu image MCP: official SDK transport and official preview helper.

Only a Realyu API key is used. Originals stay on disk; only a small preview is
returned to the conversation. stdout belongs exclusively to the MCP SDK.
"""
import os
from pathlib import Path
import sys

# The checksum-verified installer supplies a fixed dependency directory on Unix. Frozen
# Windows builds carry their own modules and never load an environment override.
if not getattr(sys, 'frozen', False):
    runtime = os.environ.get('REALYU_IMAGE_RUNTIME')
    if runtime:
        runtime_path = Path(runtime)
        if not runtime_path.is_absolute() or not runtime_path.is_dir():
            raise RuntimeError('REALYU_IMAGE_RUNTIME must be an absolute dependency directory')
        sys.path.insert(0, str(runtime_path.resolve()))
    # Python -I removes the script directory; these are installer-owned modules.
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from contextlib import redirect_stdout
import base64
import hashlib
import io
import json
import logging
import threading
import ssl
import time
from typing import Annotated, Literal
import uuid
import warnings

from mcp.server import MCPServer
from mcp.types import CallToolResult, ImageContent, TextContent, ToolAnnotations
from openai import APIConnectionError, APITimeoutError, DefaultHttpxClient, OpenAI
from pydantic import Field
from PIL import Image
import httpx2
import realyu_image_official as official
import realyu_image_support as support

BASE = support.BASE
TOOL_VERSION = support.TOOL_VERSION
MAX_IMAGE_BYTES = support.MAX_IMAGE_BYTES
MAX_RESPONSE_BYTES = support.MAX_RESPONSE_BYTES
PREVIEW_MAX_DIM = 512
PREVIEW_MAX_BYTES = 2 * 1024 * 1024
OFFICIAL_SHA256 = 'b4345cf835e5b593b97df6bb2b70bda493b8a97eee4cabbc99b2fa57dba9b448'
if not getattr(sys, 'frozen', False):
    if hashlib.sha256(Path(official.__file__).read_bytes()).hexdigest() != OFFICIAL_SHA256:
        raise RuntimeError('Official image helper integrity check failed')
# Ignore OPENAI_LOG debug configuration: credentials and request bodies must not
# appear in client diagnostics, even if the surrounding shell enabled SDK debug.
for logger_name in ('openai', 'httpx2', 'httpcore2', 'httpx', 'httpcore'):
    logging.getLogger(logger_name).setLevel(logging.WARNING)
_HELPER_OUTPUT_LOCK = threading.Lock()
Quality = Literal['auto', 'low', 'medium', 'high']
Size = Literal['auto', '1024x1024', '1024x1536', '1536x1024', '2048x2048',
    '2048x1152', '1152x2048', '2560x1440', '1440x2560', '3840x2160',
    '2160x3840', '3824x2144', '2144x3824']
Prompt = Annotated[str, Field(min_length=1, max_length=16000)]


class BoundedResponseStream(httpx2.SyncByteStream):
    """Bound the HTTP body before the official SDK decodes SSE events."""
    def __init__(self, stream):
        self.stream = stream

    def __iter__(self):
        received = 0
        for chunk in self.stream:
            received += len(chunk)
            if received > MAX_RESPONSE_BYTES:
                raise ValueError('The image response exceeded the supported size')
            yield chunk

    def close(self):
        self.stream.close()


def bound_response(response):
    if response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
        raise ValueError('Unexpected compressed image response')
    size = response.headers.get('Content-Length')
    if size and int(size) > MAX_RESPONSE_BYTES:
        raise ValueError('The image response exceeded the supported size')
    response.stream = BoundedResponseStream(response.stream)


def sdk_client(key):
    return OpenAI(api_key=key, base_url=BASE, max_retries=0, timeout=300,
        http_client=DefaultHttpxClient(trust_env=False, follow_redirects=False,
            verify=ssl.create_default_context(cafile=os.environ.get('SSL_CERT_FILE') or None),
            event_hooks={'response': [bound_response]}),
        default_headers={'User-Agent': 'Realyu-Codex-Images/' + TOOL_VERSION,
                         'Accept-Encoding': 'identity'})


def validate_image_bytes(data):
    mime = support.image_mime(data)
    with warnings.catch_warnings():
        warnings.simplefilter('error', Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            image.verify()
    return mime


def call_image(name, args, diagnostic):
    diagnostic['stage'] = 'input'
    prompt = args.get('prompt')
    if not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 16000:
        raise ValueError('Provide an image prompt of 1 to 16000 characters')
    quality, size = args.get('quality', 'auto'), args.get('size', 'auto')
    content = [{'type': 'input_text', 'text': prompt}]
    if name == 'edit_image':
        raw_path = args.get('image_path')
        if not isinstance(raw_path, str) or not Path(raw_path).is_absolute():
            raise ValueError('Provide an absolute image file path')
        selected = Path(raw_path).resolve(strict=True)
        if not selected.is_file() or selected.stat().st_size > MAX_IMAGE_BYTES:
            raise ValueError('The selected image must be a file no larger than 20 MB')
        with selected.open('rb') as source:
            image = source.read(MAX_IMAGE_BYTES + 1)
        if len(image) > MAX_IMAGE_BYTES:
            raise ValueError('The selected image exceeds 20 MB')
        mime = validate_image_bytes(image)
        content.append({'type': 'input_image', 'image_url': 'data:' + mime + ';base64,' + base64.b64encode(image).decode()})
    elif name != 'generate_image':
        raise ValueError('Unknown image tool')
    diagnostic['stage'] = 'credentials'
    key = support.personal_key()
    diagnostic['stage'] = 'output_directory'
    output = support.check_output_directory()
    encoded = None
    completed = False
    diagnostic.update(stage='connection', request_attempted=True)
    # Preserve 1.4.16's streaming path and gateway heartbeats. The non-streaming
    # Images bridge can exceed Cloudflare's idle timeout during a long edit.
    with sdk_client(key) as client:
        with client.responses.with_streaming_response.create(model='gpt-5.6-luna',
            instructions='Generate or edit the requested image.',
            input=[{'role': 'user', 'content': content}],
            tools=[{'type': 'image_generation', 'model': 'gpt-image-2',
                    'quality': quality, 'size': size, 'output_format': 'png'}],
            tool_choice={'type': 'image_generation'}, stream=True, store=False) as response:
            support.response_metadata(response, diagnostic)
            diagnostic['stage'] = 'response'
            for event in response.parse():
                if event.type in ('error', 'response.failed', 'response.incomplete', 'response.cancelled'):
                    raise ValueError('Image generation did not complete')
                if event.type == 'response.output_item.done':
                    item = event.item
                    if item.type == 'image_generation_call' and item.result:
                        encoded = item.result
                if event.type == 'response.completed':
                    completed = event.response.status == 'completed'
                    for item in event.response.output or []:
                        if item.type == 'image_generation_call' and item.result:
                            encoded = item.result
                    break
    if not completed or not isinstance(encoded, str) or not encoded:
        raise ValueError('Image response ended without completed image data')
    image = base64.b64decode(encoded, validate=True)
    if validate_image_bytes(image) != 'image/png':
        raise ValueError('The image service did not return the requested PNG')
    diagnostic.update(stage='save', image_received=True)
    target = output.resolve() / (uuid.uuid4().hex + '.png')
    # Reuse the reviewed upstream routine verbatim. Suppress its path-bearing
    # progress messages; only the successful MCP result exposes the saved paths.
    with _HELPER_OUTPUT_LOCK, redirect_stdout(io.StringIO()):
        official._decode_write_and_downscale([encoded], [target], force=False,
            downscale_max_dim=PREVIEW_MAX_DIM, downscale_suffix='-preview', output_format='png')
    preview = official._derive_downscale_path(target, '-preview')
    preview_bytes = preview.read_bytes()
    if len(preview_bytes) > PREVIEW_MAX_BYTES:
        raise ValueError('Generated preview exceeded the supported size')
    metadata = {'file': str(target), 'bytes': len(image), 'original_file': str(target),
        'preview_file': str(preview), 'preview_bytes': len(preview_bytes),
        'model': 'gpt-image-2', 'request_id': diagnostic.get('request_id'),
        'note': 'The attached image is a small preview. Use file for the full-quality original and as image_path for further edits. Do not load the full original into the conversation merely to display it.'}
    return CallToolResult(content=[TextContent(text=json.dumps(metadata, ensure_ascii=False)),
        ImageContent(data=base64.b64encode(preview_bytes).decode(), mime_type='image/png')])


def execute_image(name, args):
    diagnostic = {'version': TOOL_VERSION, 'id': uuid.uuid4().hex, 'stage': 'input',
                  'request_attempted': False, 'image_received': False}
    started = time.monotonic()
    try:
        return call_image(name, args, diagnostic)
    except (Exception, SystemExit) as error:
        diagnostic['seconds'] = round(time.monotonic() - started, 2)
        details = support.error_details(error, diagnostic)
        if isinstance(error, APITimeoutError):
            details['code'] = 'TIMEOUT'
        elif isinstance(error, APIConnectionError):
            details['code'] = 'CONNECTION_ERROR'
        return CallToolResult(is_error=True, content=[TextContent(text=
            'Image request failed. No automatic retry was made. Diagnostic: ' + json.dumps(details))])


server = MCPServer('image-generation', version=TOOL_VERSION, log_level='ERROR')
write_annotation = ToolAnnotations(read_only_hint=False, destructive_hint=False,
    idempotent_hint=False, open_world_hint=True)


@server.tool(title='Generate image', annotations=write_annotation)
def generate_image(prompt: Prompt, quality: Quality = 'auto', size: Size = 'auto') -> CallToolResult:
    """Generate an image. Saves the full-quality PNG and returns its path with a small preview. Share the original file; do not reload it just to display it."""
    return execute_image('generate_image', {'prompt': prompt, 'quality': quality, 'size': size})


@server.tool(title='Edit image', annotations=write_annotation)
def edit_image(prompt: Prompt, image_path: str, quality: Quality = 'auto', size: Size = 'auto') -> CallToolResult:
    """Edit the user's local PNG, JPEG or WebP (up to 20 MB). Pass the original file path, not its preview. Preserves the source and returns a new original plus a small preview."""
    return execute_image('edit_image', {'prompt': prompt, 'image_path': image_path, 'quality': quality, 'size': size})


@server.tool(title='Diagnose image connection', annotations=ToolAnnotations(
    read_only_hint=False, destructive_hint=False, idempotent_hint=True, open_world_hint=True))
def diagnose_image() -> CallToolResult:
    """Check Realyu API-key configuration, output storage and HTTPS access without generating or charging for an image. Zero balance is not an authentication failure."""
    return CallToolResult.model_validate(support.diagnose_image())


def main():
    server.run(transport='stdio')


if __name__ == '__main__':
    main()


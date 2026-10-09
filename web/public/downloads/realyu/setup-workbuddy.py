#!/usr/bin/env python3
"""RealYu WorkBuddy macOS setup. Run using the pinned private bootstrap runtime."""

import getpass
import http.client
import json
import os
from pathlib import Path
import plistlib
import re
import ssl
import stat
import sys
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import warnings


VERSION = '1.2.0'
ENDPOINT = 'https://api.realyu.fun/v1/chat/completions'
MODEL_ORDER = (
    'gpt-6.1-sol', 'gpt-6-astra', 'gpt-6-sol', 'gpt-6-luna',
    'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', 'gpt-5.5',
)
MAX_BYTES = 1024 * 1024


class SetupError(Exception):
    """Fixed error codes only; never include configuration, keys or HTTP bodies."""


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise SetupError('KEY_REDIRECT_BLOCKED')


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON property')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('non-finite JSON value')


def parse_json(raw, code):
    try:
        return json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object,
                          parse_constant=reject_constant)
    except (UnicodeError, ValueError, RecursionError):
        raise SetupError(code) from None


def discover_workbuddy(candidates=None):
    candidates = candidates or (Path('/Applications/WorkBuddy.app'),
                                Path.home() / 'Applications/WorkBuddy.app')
    for app in candidates:
        if not app.is_dir():
            continue
        try:
            with (app / 'Contents/Info.plist').open('rb') as stream:
                info = plistlib.load(stream)
            version = info.get('CFBundleShortVersionString', '')
            name = info.get('CFBundleDisplayName', info.get('CFBundleName', ''))
            if name != 'WorkBuddy' or not re.fullmatch(r'\d+\.\d+\.\d+(?:\.\d+)?', version):
                raise ValueError()
            if tuple(map(int, version.split('.')))[:3] < (5, 6, 2):
                raise SetupError('WORKBUDDY_VERSION_UNSUPPORTED')
        except (OSError, ValueError, TypeError, plistlib.InvalidFileException):
            raise SetupError('WORKBUDDY_INSTALL_INVALID') from None
        return app
    raise SetupError('WORKBUDDY_NOT_FOUND')


def api_request(key, resource):
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{16,256}', key):
        raise SetupError('KEY_FORMAT_INVALID')
    url = {'key': 'https://api.realyu.fun/api/client/key-check',
           'models': 'https://api.realyu.fun/v1/models'}[resource]
    request = urllib.request.Request(url, headers={
        'Authorization': 'Bearer ' + key, 'User-Agent': 'Realyu-WorkBuddy-Setup/' + VERSION})
    context = ssl.create_default_context(cafile=os.environ.get('REALYU_CA_BUNDLE') or None)
    # Reuse the Codex installer's standard urllib transport and redirect policy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
                                        urllib.request.HTTPSHandler(context=context))
    try:
        with opener.open(request, timeout=30) as response:
            if response.status != 200:
                raise SetupError('KEY_REDIRECT_OR_HTTP_ERROR')
            raw = response.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise SetupError('KEY_RESPONSE_INVALID')
            return parse_json(raw, 'KEY_RESPONSE_INVALID')
    except urllib.error.HTTPError as error:
        code = {401: 'KEY_REJECTED', 403: 'KEY_ACCESS_DENIED', 429: 'KEY_RATE_LIMITED'}.get(
            error.code, 'KEY_REDIRECT_OR_HTTP_ERROR')
        error.close()
        raise SetupError(code) from None
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        raise SetupError('KEY_NETWORK_ERROR') from None


def available_models(key):
    data = api_request(key, 'key')
    usage = data.get('data') if isinstance(data, dict) else None
    if (not isinstance(data, dict) or data.get('code') is not True or
            not isinstance(usage, dict) or usage.get('object') != 'token_usage' or
            type(usage.get('expires_at')) is not int):
        raise SetupError('KEY_RESPONSE_INVALID')
    if 0 < usage['expires_at'] <= time.time():
        raise SetupError('KEY_EXPIRED')
    data = api_request(key, 'models')
    if not isinstance(data, dict) or not isinstance(data.get('data'), list):
        raise SetupError('MODELS_RESPONSE_INVALID')
    ids = set()
    for model in data['data']:
        if not isinstance(model, dict) or not isinstance(model.get('id'), str):
            raise SetupError('MODELS_RESPONSE_INVALID')
        if re.fullmatch(r'gpt-[0-9][a-z0-9._-]{0,79}', model['id']):
            ids.add(model['id'])
    if not ids:
        raise SetupError('MODELS_NONE_AVAILABLE')
    if len(ids) > 64:
        raise SetupError('MODELS_RESPONSE_INVALID')
    rank = {model: index for index, model in enumerate(MODEL_ORDER)}
    return sorted(ids, key=lambda model: (rank.get(model, len(rank)), model))


def reject_links(path):
    for item in (path, *path.parents):
        if item.is_symlink():
            raise SetupError('CONFIG_LINK_UNSUPPORTED')


def read_snapshot(path):
    reject_links(path)
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    except FileNotFoundError:
        return None
    with os.fdopen(descriptor, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_BYTES:
            raise SetupError('CONFIG_UNSUPPORTED')
        raw = stream.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise SetupError('CONFIG_UNSUPPORTED')
    return (info.st_dev, info.st_ino, info.st_mtime_ns, raw)


def merge_models(snapshot, key, ids):
    document = [] if snapshot is None else parse_json(snapshot[3], 'CONFIG_JSON_INVALID')
    if isinstance(document, list):
        models = document
    elif isinstance(document, dict) and isinstance(document.get('models'), list):
        models = document['models']
        if 'availableModels' in document:
            values = document['availableModels']
            if (not isinstance(values, list) or any(not isinstance(value, str) or not value for value in values)
                    or len(values) != len(set(values))):
                raise SetupError('CONFIG_UNSUPPORTED')
    else:
        raise SetupError('CONFIG_UNSUPPORTED')
    by_id = {}
    for model in models:
        if (not isinstance(model, dict) or not isinstance(model.get('id'), str) or
                not model['id'].strip() or model['id'].strip() != model['id'] or len(model['id']) > 256 or
                any(ord(char) < 32 for char in model['id'])):
            raise SetupError('CONFIG_UNSUPPORTED')
        identifier = model['id'].removeprefix('custom-local:')
        if not identifier:
            raise SetupError('CONFIG_UNSUPPORTED')
        if identifier in by_id:
            raise SetupError('CONFIG_DUPLICATE_MODEL')
        by_id[identifier] = model
    for identifier in ids:
        model = by_id.get(identifier)
        if model is not None and model.get('url') != ENDPOINT:
            raise SetupError('CONFIG_MODEL_CONFLICT')
        if model is None:
            model = {}
            models.append(model)
        model.update(id=identifier, name=identifier, vendor='RealYu API', url=ENDPOINT, apiKey=key,
                     maxInputTokens=200000, maxOutputTokens=4096, supportsToolCall=True,
                     supportsImages=False, supportsReasoning=False, useCustomProtocol=False)
    if ids:
        selected = set(ids)
        managed = {model['id']: model for model in models if model['id'] in selected}
        models[:] = [model for model in models if model['id'] not in selected] + [managed[identifier] for identifier in ids]
    if isinstance(document, dict) and document.get('availableModels'):
        for identifier in ids:
            if identifier not in document['availableModels']:
                document['availableModels'].append(identifier)
    return (json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def install(key, directory):
    if not re.fullmatch(r'sk-[A-Za-z0-9_-]{16,256}', key):
        raise SetupError('KEY_FORMAT_INVALID')
    directory = Path(os.path.abspath(directory))
    path = directory / 'models.json'
    before = read_snapshot(path)
    # Reject malformed local data before making an authenticated request.
    merge_models(before, key, [])
    ids = available_models(key)
    content = merge_models(before, key, ids)
    if len(content) > MAX_BYTES:
        raise SetupError('CONFIG_UNSUPPORTED')
    reject_links(path)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock = directory / '.realyu-config-lock'
    try:
        lock.mkdir(mode=0o700)
    except FileExistsError:
        raise SetupError('CONFIG_BUSY') from None
    temporary = None
    try:
        descriptor, filename = tempfile.mkstemp(prefix='.realyu-', suffix='.tmp', dir=directory)
        temporary = Path(filename)
        with os.fdopen(descriptor, 'wb') as stream:
            os.chmod(temporary, 0o600)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if read_snapshot(path) != before:
            raise SetupError('CONFIG_CHANGED_RETRY')
        if before is not None:
            backup = directory / ('models.realyu-backup-' + uuid.uuid4().hex + '.json')
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as stream:
                stream.write(before[3])
                stream.flush()
                os.fsync(stream.fileno())
            if read_snapshot(path) != before:
                raise SetupError('CONFIG_CHANGED_RETRY')
            os.replace(temporary, path)
        else:
            # Hard-link publication is atomic and fails if another writer created models.json.
            try:
                os.link(temporary, path)
            except FileExistsError:
                raise SetupError('CONFIG_CHANGED_RETRY') from None
        return ids
    finally:
        # Cleanup cannot turn a successful atomic commit into a reported failure.
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
        try:
            lock.rmdir()
        except OSError:
            pass


def main():
    key = ''
    try:
        if sys.platform != 'darwin':
            raise SetupError('WORKBUDDY_PLATFORM_UNSUPPORTED')
        discover_workbuddy()
        if len(sys.argv) > 2:
            raise SetupError('KEY_ARGUMENT_INVALID')
        if len(sys.argv) == 2:
            key = sys.argv[1]
        elif not sys.stdin.isatty():
            key = sys.stdin.readline(300).strip()
        if not key:
            # A missing terminal must fail instead of echoing the key through a fallback prompt.
            with open('/dev/tty', 'r+') as terminal:
                with warnings.catch_warnings():
                    warnings.simplefilter('error', getpass.GetPassWarning)
                    key = getpass.getpass('API Key: ', stream=terminal).strip()
        directory = (os.environ.get('WORKBUDDY_CONFIG_DIR') or os.environ.get('CODEBUDDY_CONFIG_DIR')
                     or str(Path.home() / '.workbuddy'))
        install(key, directory)
        print('设置完成。请在 WorkBuddy 中选择 RealYu API 模型。')
        return 0
    except SetupError as error:
        print('设置未完成：' + str(error), file=sys.stderr)
    except getpass.GetPassWarning:
        print('设置未完成：KEY_HIDDEN_INPUT_UNAVAILABLE', file=sys.stderr)
    except (OSError, ValueError, TypeError):
        print('设置未完成：CONFIG_WRITE_FAILED', file=sys.stderr)
    finally:
        key = ''
    return 1


if __name__ == '__main__':
    raise SystemExit(main())

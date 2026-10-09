"""Exactly one image generation and two edits on an isolated real candidate.

No retries. A failed generation leaves edits NOT_RUN. Raw images and responses
remain private, and visual comparison must be performed before acceptance.
"""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

import httpx
from PIL import Image

from bootstrap_windows import ROOT, PRIVATE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', required=True, type=Path)
    args = parser.parse_args()
    fixture = args.fixture.resolve(strict=True)
    if not fixture.is_relative_to(PRIVATE.resolve()):
        raise RuntimeError('Fixture must be inside the isolated private test directory')
    config = json.loads(fixture.read_text(encoding='utf-8'))
    target = urlsplit(config['base_url'])
    if target.hostname != '127.0.0.1' or target.port in (18300, 18301, 28502):
        raise RuntimeError('Use a new isolated candidate, not the parent evidence instance')
    output = fixture.parent / 'legacy-images-first'
    output.mkdir(exist_ok=False)
    report = {'source_run': fixture.parent.name + '/' + output.name, 'gateway_sha256': config['gateway_sha256'],
        'scope': 'actual isolated RealYu -> stock Sub2API -> real OAuth Images API', 'production_changed': False,
        'automatic_retries': 0, 'maximum_image_requests': 3, 'model': 'gpt-image-2', 'visual_review': 'REQUIRED', 'checks': []}
    client = httpx.Client(base_url=config['base_url'], headers={'Authorization': 'Bearer ' + config['api_key']},
        timeout=300, trust_env=False, follow_redirects=False)
    source_image = None

    def save():
        (output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    prompt = 'Create a clean editorial photograph of a sunlit office desk with an open cream notebook, a dark green desk lamp and a small succulent in a terracotta pot. Natural materials, realistic lighting, no people, no lettering.'
    edit = 'Preserve the exact desk, open cream notebook, green lamp, succulent, camera angle and composition. Add a bright red ceramic coffee mug beside the notebook. Change nothing else.'
    try:
        for label in ('generation', 'multipart-edit', 'json-images-edit'):
            row = {'case': label, 'status': 'NOT_RUN'}
            report['checks'].append(row)
            if label != 'generation' and source_image is None:
                row['reason'] = 'Generation did not produce a usable image; no edit request sent'
                save(); continue
            started = time.monotonic()
            try:
                common = {'model': 'gpt-image-2', 'prompt': prompt if label == 'generation' else edit,
                    'size': '1024x1024', 'quality': 'low', 'response_format': 'b64_json'}
                if label == 'generation':
                    response = client.post('/images/generations', json={**common, 'n': 1})
                elif label == 'multipart-edit':
                    response = client.post('/images/edits', data=common,
                        files={'image': ('original.png', source_image.read_bytes(), 'image/png')})
                else:
                    response = client.post('/images/edits', json={**common, 'images': [{
                        'image_url': 'data:image/png;base64,' + base64.b64encode(source_image.read_bytes()).decode()}]})
                row.update(http_status=response.status_code, request_id=response.headers.get('X-Oneapi-Request-Id'),
                    elapsed_seconds=round(time.monotonic() - started, 3))
                (output / (label + '.response.json')).write_bytes(response.content)
                value = response.json()
                if response.status_code != 200:
                    row.update(status='FAIL', error=value.get('error', {'message': 'HTTP failure'}))
                else:
                    data = value.get('data', [])
                    if len(data) != 1 or not data[0].get('b64_json'):
                        raise AssertionError('Expected one actual base64 image')
                    blob = base64.b64decode(data[0]['b64_json'], validate=True)
                    with Image.open(io.BytesIO(blob)) as image:
                        image.load()
                        dimensions, fmt = list(image.size), image.format
                    if min(dimensions) <= 256:
                        raise AssertionError('Image unexpectedly small')
                    file = output / (label + ('.png' if fmt == 'PNG' else '.jpg'))
                    file.write_bytes(blob)
                    row.update(status='VISUAL_REVIEW_REQUIRED', image_file=file.name, image_bytes=len(blob),
                        sha256=hashlib.sha256(blob).hexdigest(), dimensions=dimensions, format=fmt,
                        requested_dimensions=[1024, 1024], requested_size_honored=dimensions == [1024, 1024])
                    if label == 'generation':
                        source_image = file
                    else:
                        row['reference_sha256'] = hashlib.sha256(source_image.read_bytes()).hexdigest()
            except Exception as exc:
                row.update(status='FAIL', failure_type=type(exc).__name__)
                (output / (label + '.failure.txt')).write_text(str(exc), encoding='utf-8')
            save()
            print(json.dumps({k: row.get(k) for k in ('case', 'status', 'http_status', 'request_id', 'dimensions', 'elapsed_seconds')}), flush=True)
    finally:
        client.close()
        save()
    print(json.dumps({'source_run': report['source_run'], 'visual_review': 'REQUIRED'}), flush=True)
    return 1 if any(row['status'] == 'FAIL' for row in report['checks']) else 0


if __name__ == '__main__':
    sys.exit(main())

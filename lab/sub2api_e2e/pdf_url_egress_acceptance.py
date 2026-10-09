"""One real PDF URL request through an explicitly configured gateway egress."""
import argparse
import json
from pathlib import Path
import time
from urllib.parse import urlsplit

import httpx
import openai

from bootstrap_windows import ROOT, PRIVATE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--fixture', required=True, type=Path)
    args = parser.parse_args()
    fixture = args.fixture.resolve(strict=True)
    if not fixture.is_relative_to(PRIVATE.resolve()):
        raise RuntimeError('Use a private isolated candidate fixture')
    config = json.loads(fixture.read_text(encoding='utf-8'))
    if urlsplit(config['base_url']).hostname != '127.0.0.1' or not config.get('gateway_egress'):
        raise RuntimeError('This retest requires an explicit isolated gateway egress')
    output = fixture.parent / 'pdf-url-egress-first.json'
    if output.exists():
        raise RuntimeError('Preserve first evidence; do not overwrite or repeat')
    report = {'source_run': fixture.parent.name + '/' + output.name, 'gateway_sha256': config['gateway_sha256'],
        'scope': 'single actual RealYu -> stock Sub2API -> real upstream public PDF URL request',
        'production_changed': False, 'automatic_retries': 0, 'maximum_model_requests': 1,
        'gateway_download_egress': 'explicit local HTTP proxy; local Sub2API bypassed with NO_PROXY',
        'tls_verification_disabled': False, 'original_direct_egress_failure': 'portable-api-summary.json: gpt-6_1-sol-pdf-url',
        'status': 'FAIL'}
    started = time.monotonic()
    client = openai.OpenAI(api_key=config['api_key'], base_url=config['base_url'], max_retries=0,
        timeout=120, http_client=httpx.Client(trust_env=False, timeout=120))
    try:
        raw = client.responses.with_raw_response.create(model='gpt-6.1-sol', input=[{'role': 'user', 'content': [
            {'type': 'input_file', 'file_url': 'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf'},
            {'type': 'input_text', 'text': 'Read the attached PDF. Return only its exact visible text, with no explanation.'}]}])
        response = raw.parse()
        report.update(http_status=raw.status_code, request_id=raw.headers.get('X-Oneapi-Request-Id'),
            response_id=response.id, terminal_status=response.status, output_items=len(response.output),
            text=response.output_text, elapsed_seconds=round(time.monotonic() - started, 3))
        if response.status != 'completed' or not response.output or ' '.join(response.output_text.split()).strip().lower() != 'dummy pdf file':
            raise AssertionError('PDF text contract failed')
        report.update(status='PASS_WITH_CONFIGURED_EGRESS', document_text_verified=True)
    except Exception as exc:
        (fixture.parent / 'pdf-url-egress-failure.txt').write_text(str(exc), encoding='utf-8')
        report.update(failure_type=type(exc).__name__, http_status=getattr(exc, 'status_code', report.get('http_status')))
    finally:
        client.close()
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
        (ROOT / 'lab/sub2api_e2e/pdf-url-egress-summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report), flush=True)
    return 0 if report['status'].startswith('PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())

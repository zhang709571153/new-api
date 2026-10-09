"""Official SDK acceptance: delta-only output is never a passing final response.

Run against both direct Sub2API and the complete candidate RealYu chain.
The API key is supplied only through REALYU_API_KEY in the process environment.
"""
import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import sys
import time
from urllib.parse import urlsplit

import openai


def check_stream(client, model, prompt, marker=None, *, expected_terms=(), search=False):
    deltas = []
    done = []
    errors = []
    options = {'tools': [{'type': 'web_search'}], 'tool_choice': 'required'} if search else {}
    with client.responses.stream(model=model, input=prompt, **options) as stream:
        for event in stream:
            if event.type == 'response.output_text.delta':
                deltas.append(event.delta)
            if event.type == 'response.output_item.done':
                done.append(event.item)
            if event.type in ('error', 'response.failed'):
                errors.append(event.type)
        final = stream.get_final_response()
    delta = ''.join(deltas)
    final_text = final.output_text
    assert not errors, 'SSE returned failure/error event'
    assert final.status == 'completed', 'Response terminal status is not completed'
    assert final.output, 'Terminal output is empty despite streamed items'
    assert final_text == delta, 'SDK final text and streamed text differ'
    if marker is not None:
        assert marker == final_text.strip(), 'Exact synthetic marker mismatch'
    assert all(term in final_text for term in expected_terms), 'Expected file content missing from SDK final text'
    final_ids = {item.id for item in final.output if getattr(item, 'id', None)}
    assert all(getattr(item, 'id', None) in final_ids for item in done if getattr(item, 'id', None)), 'Final output lost completed items'
    if search:
        assert any(item.type == 'web_search_call' for item in final.output), 'Final response lost actual search call'
        annotations = [annotation for item in final.output if item.type == 'message' for part in item.content
                       if part.type == 'output_text' for annotation in part.annotations]
        assert any(getattr(annotation, 'type', None) == 'url_citation' for annotation in annotations), 'Final response lost search citations'
    return {'response_id': final.id, 'output_types': [item.type for item in final.output],
        'delta_characters': len(delta), 'final_characters': len(final_text), 'done_items': len(done)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--upstream', choices=('mock', 'real'), required=True)
    parser.add_argument('--allow-external', action='store_true')
    parser.add_argument('--extended', action='store_true', help='Add actual PDF and forced web-search final-response checks')
    args = parser.parse_args()
    host = urlsplit(args.base_url).hostname
    if host not in ('127.0.0.1', 'localhost', '::1') and not args.allow_external:
        parser.error('External endpoint requires --allow-external; candidate defaults to loopback only')
    if args.output.exists():
        parser.error('Output already exists; preserve initial failure and use a fresh path')
    key = os.environ.get('REALYU_API_KEY', '').strip()
    if not key:
        parser.error('Set REALYU_API_KEY only in this process environment')
    client = openai.OpenAI(api_key=key, base_url=args.base_url, max_retries=0, timeout=120)
    report = {'sdk_version': openai.__version__, 'base_url': args.base_url, 'model': args.model,
        'upstream': args.upstream, 'tested_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'automatic_retries': 0, 'checks': [], 'desktop_e2e_performed': False}
    cases = ['responses-stream', 'responses-nonstream', 'chat-nonstream']
    if args.upstream == 'mock':
        cases.append('empty-terminal-reconstruction')
    elif args.extended:
        cases.extend(('pdf-stream', 'web-search-stream'))
    for name in cases:
        marker = 'E2E_' + secrets.token_hex(12)
        prompt = 'Reply exactly ' + marker
        if name == 'empty-terminal-reconstruction':
            prompt += '\nMOCK_EMPTY_TERMINAL'
        started = time.monotonic()
        result = {'name': name, 'status': 'FAIL'}
        try:
            if name == 'pdf-stream':
                fixtures = Path(__file__).parent / 'portable/fixtures'
                expected = json.loads((fixtures / 'expected.json').read_text())
                data = 'data:application/pdf;base64,' + base64.b64encode((fixtures / 'reading-fixture.pdf').read_bytes()).decode()
                content = [{'role': 'user', 'content': [
                    {'type': 'input_file', 'filename': 'reading-fixture.pdf', 'file_data': data},
                    {'type': 'input_text', 'text': 'Read both pages. Return the verification code, combined number of blue and green boxes, and warehouse name.'}]}]
                result.update(check_stream(client, args.model, content, expected_terms=(expected['code'], str(expected['total']), expected['warehouse'])))
            elif name == 'web-search-stream':
                result.update(check_stream(client, args.model, 'Search the official Python documentation for pathlib Path.read_text. Give one brief factual sentence with a source citation.', search=True))
            elif name in ('responses-stream', 'empty-terminal-reconstruction'):
                result.update(check_stream(client, args.model, prompt, marker))
            elif name == 'responses-nonstream':
                response = client.responses.create(model=args.model, input=prompt, stream=False)
                assert response.status == 'completed' and response.output_text.strip() == marker, 'Nonstream final text mismatch'
            else:
                response = client.chat.completions.create(model=args.model, messages=[{'role': 'user', 'content': prompt}], stream=False)
                assert response.choices[0].message.content.strip() == marker, 'Chat final text mismatch'
            result['status'] = 'PASS'
        except Exception as exc:
            result['error'] = (type(exc).__name__ + ': ' + str(exc)).replace(key, '[REDACTED]')[:1000]
        result['seconds'] = round(time.monotonic() - started, 3)
        report['checks'].append(result)
    report['status'] = 'PASS' if all(row['status'] == 'PASS' for row in report['checks']) else 'FAIL'
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    sys.exit(main())

"""Bounded real-upstream feature probes. No automatic retries or production writes.

Keys come from the process environment. Use --cases to repeat only a changed
feature and keep the initial report; this script never overwrites evidence.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlsplit

import openai


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--model', default='gpt-6.1-sol')
    parser.add_argument('--cases', default='tool-roundtrip,image-input,json-schema,compact-v2,websocket,image-generation')
    args = parser.parse_args()
    assert urlsplit(args.base_url).hostname in ('127.0.0.1', 'localhost', '::1')
    assert not args.output.exists(), 'Preserve initial evidence; use a fresh report path'
    key = os.environ['REALYU_API_KEY']
    client = openai.OpenAI(api_key=key, base_url=args.base_url, max_retries=0, timeout=150)
    report = {'upstream': 'real', 'base_url': args.base_url, 'model': args.model,
              'sdk_version': openai.__version__, 'tested_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'automatic_retries': 0, 'desktop_e2e_performed': False, 'checks': []}

    def complete(response):
        assert response.status == 'completed' and response.output, 'Missing completed terminal output'
        return response

    for case in args.cases.split(','):
        row = {'name': case, 'status': 'FAIL'}
        started = time.monotonic()
        try:
            if case == 'tool-roundtrip':
                marker = 'TOOL_' + secrets.token_hex(12)
                question = [{'role': 'user', 'content': 'Call read_marker once and then repeat its returned marker exactly.'}]
                tools = [{'type': 'function', 'name': 'read_marker', 'description': 'Read a test marker.',
                          'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}, 'strict': True}]
                first = complete(client.responses.create(model=args.model, input=question, tools=tools,
                    tool_choice={'type': 'function', 'name': 'read_marker'}, include=['reasoning.encrypted_content'], store=False))
                calls = [item for item in first.output if item.type == 'function_call']
                assert len(calls) == 1 and calls[0].name == 'read_marker'
                history = question + [item.model_dump(exclude_none=True) for item in first.output]
                history.append({'type': 'function_call_output', 'call_id': calls[0].call_id, 'output': marker})
                final = complete(client.responses.create(model=args.model, input=history, tools=tools, tool_choice='none', store=False))
                assert final.output_text.strip() == marker, 'Tool output failed round trip'
                row.update(response_ids=[first.id, final.id], call_id_preserved=True,
                           reasoning_items=sum(item.type == 'reasoning' for item in first.output))
            elif case == 'image-input':
                fixture = Path(__file__).parent / 'portable/fixtures/page2.png'
                data = 'data:image/png;base64,' + base64.b64encode(fixture.read_bytes()).decode()
                final = complete(client.responses.create(model=args.model, input=[{'role': 'user', 'content': [
                    {'type': 'input_image', 'image_url': data}, {'type': 'input_text', 'text': 'Read the warehouse name shown in this image. Reply only with the name.'}]}]))
                assert 'NORTH-QUAY' in final.output_text
                row.update(response_id=final.id, image_content_verified=True)
            elif case == 'json-schema':
                final = complete(client.responses.create(model=args.model, input='Return an object with count exactly zero and enabled false.',
                    text={'format': {'type': 'json_schema', 'name': 'explicit_zero', 'strict': True, 'schema': {
                        'type': 'object', 'properties': {'count': {'type': 'integer'}, 'enabled': {'type': 'boolean'}},
                        'required': ['count', 'enabled'], 'additionalProperties': False}}}))
                assert json.loads(final.output_text) == {'count': 0, 'enabled': False}
                row.update(response_id=final.id, schema_verified=True)
            elif case == 'compact-v2':
                marker = 'COMPACT_' + secrets.token_hex(12)
                with client.responses.stream(model=args.model, instructions='You are a helpful coding assistant.', store=False,
                    input=[{'role':'user','content':'Remember the project verification marker: '+marker},
                           {'role':'assistant','content':'I will retain the project verification marker.'},
                           {'type':'compaction_trigger'}],
                    extra_headers={'x-codex-beta-features':'remote_compaction_v2'}) as stream:
                    done = []
                    for event in stream:
                        if event.type in ('error','response.failed'):
                            raise AssertionError('Native compaction returned an error event')
                        if event.type == 'response.output_item.done' and event.item.type == 'compaction':
                            done.append(event.item)
                    compact = stream.get_final_response()
                assert compact.status == 'completed'
                items = [item for item in compact.output if item.type == 'compaction']
                assert items and done, 'Native compaction item missing from done or final output'
                assert {item.id for item in items} == {item.id for item in done}
                history = [item.model_dump(exclude_none=True) for item in compact.output]
                history.append({'role':'user','content':'What was the project verification marker? Reply exactly with it.'})
                final = complete(client.responses.create(model=args.model,input=history))
                assert final.output_text.strip() == marker, 'Native compact replay lost marker'
                row.update(response_id=final.id,compact_id=compact.id,native_compaction_v2_verified=True)
            elif case == 'compact':
                marker = 'COMPACT_' + secrets.token_hex(12)
                compact = client.responses.compact(model=args.model, input=[
                    {'role': 'user', 'content': 'Remember the project verification marker: ' + marker},
                    {'role': 'assistant', 'content': 'I will retain the project verification marker.'}])
                assert compact.output and any(item.type == 'compaction' for item in compact.output), 'Missing compaction item'
                history = [item.model_dump(exclude_none=True) for item in compact.output]
                history.append({'role': 'user', 'content': 'What was the project verification marker? Reply exactly with it.'})
                final = complete(client.responses.create(model=args.model, input=history))
                assert final.output_text.strip() == marker, 'Compacted conversation lost marker'
                row.update(response_id=final.id, compact_id=compact.id, compact_replay_verified=True)
            elif case == 'websocket':
                from websockets.sync.client import connect
                marker = 'WS_' + secrets.token_hex(12)
                endpoint = args.base_url.replace('http://', 'ws://').replace('https://', 'wss://').rstrip('/') + '/responses?model=' + args.model
                headers = {'Authorization': 'Bearer ' + key, 'OpenAI-Beta': 'responses_websockets=2026-02-06', 'Originator': 'codex_cli_rs'}
                def receive(ws):
                    delta = []
                    while True:
                        event = json.loads(ws.recv(timeout=90))
                        if event.get('type') in ('error', 'response.failed'):
                            raise AssertionError(json.dumps(event)[:800])
                        if event.get('type') == 'response.output_text.delta':
                            delta.append(event['delta'])
                        if event.get('type') == 'response.completed':
                            final = event['response']
                            text = ''.join(part.get('text', '') for item in final.get('output', []) if item.get('type') == 'message' for part in item.get('content', []) if part.get('type') == 'output_text')
                            assert final.get('status') == 'completed' and text and text == ''.join(delta), 'WS terminal output mismatch'
                            return final, text
                with connect(endpoint, additional_headers=headers, open_timeout=20, close_timeout=5, max_size=16*1024*1024, proxy=None) as ws:
                    ws.send(json.dumps({'type':'response.create','model':args.model,'input':[{'role':'user','content':'Remember '+marker+'. Reply only ACK.'}],'store':False}))
                    first, text = receive(ws)
                    assert text.strip() == 'ACK'
                    ws.send(json.dumps({'type':'response.create','model':args.model,'previous_response_id':first['id'],'input':[{'role':'user','content':'Repeat the remembered marker exactly.'}],'store':False}))
                    second, text = receive(ws)
                    assert text.strip() == marker, 'WS second turn lost context'
                row.update(response_ids=[first['id'],second['id']], same_socket_two_turns_verified=True)
            elif case == 'image-generation':
                final = complete(client.responses.create(model=args.model, input='Generate one simple flat image: a blue square centered on a white background, no text.',
                    tools=[{'type':'image_generation','size':'1024x1024','quality':'low','output_format':'png'}], tool_choice='required'))
                images = [item for item in final.output if item.type == 'image_generation_call' and getattr(item, 'result', None)]
                assert len(images) == 1, 'Expected exactly one image'
                data = base64.b64decode(images[0].result, validate=True)
                assert data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>100, 'Invalid image payload'
                output = args.output.with_suffix('.png')
                assert not output.exists()
                output.write_bytes(data)
                row.update(response_id=final.id, image_bytes=len(data), image_sha256=hashlib.sha256(data).hexdigest())
            else:
                raise ValueError('Unknown case')
            row['status'] = 'PASS'
        except Exception as error:
            row['error'] = (type(error).__name__ + ': ' + str(error)).replace(key, '[REDACTED]')[:1200]
        row['seconds'] = round(time.monotonic()-started,3)
        report['checks'].append(row)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2),encoding='utf-8')
        print(json.dumps(row,ensure_ascii=False),flush=True)
    report['status'] = 'PASS' if all(row['status']=='PASS' for row in report['checks']) else 'FAIL'
    args.output.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())

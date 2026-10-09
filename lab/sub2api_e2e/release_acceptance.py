"""Bounded release smoke; dry-run by default and never provisions customer state.

Private JSON supplies the exact target and dedicated operator subjects. Ledger
access is SQLite read-only. No request retry, service mutation or credential
refresh is performed. Old acceptance artifacts are never overwritten.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import secrets
import sqlite3
import time
from urllib.parse import quote, urlsplit

import httpx
import openai

from strict_sdk import check_stream


CASES = ('catalog', 'responses-stream', 'responses-nonstream', 'chat',
         'pdf-inline', 'pdf-url', 'web-search', 'tool-roundtrip', 'websocket',
         'subjects', 'failed-request', 'all-text-models', 'image-generation')


def ledger_snapshot(path, subjects):
    """Read only the dedicated subjects, their payers and their usage records."""
    con = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    try:
        tokens, users = {}, {}
        for subject in subjects:
            row = con.execute('SELECT id,user_id,workspace_user_id,key,status,used_quota,remain_quota '
                              'FROM tokens WHERE id=?', (subject['token_id'],)).fetchone()
            assert row and row['status'] == 1, 'Dedicated test token is absent or disabled'
            assert row['key'].removeprefix('sk-') == subject['api_key'].removeprefix('sk-'), 'Token ID/key mismatch'
            assert row['user_id'] == subject['billing_owner_id'], 'Unexpected billing owner'
            effective_user = row['workspace_user_id'] or row['user_id']
            assert effective_user == subject['user_id'], 'Unexpected workspace member'
            tokens[str(row['id'])] = {k: row[k] for k in ('id', 'user_id', 'workspace_user_id', 'used_quota', 'remain_quota')}
            for uid in (row['user_id'], effective_user):
                user = con.execute('SELECT id,quota,used_quota FROM users WHERE id=?', (uid,)).fetchone()
                assert user, 'Dedicated test user missing'
                users[str(uid)] = dict(user)
        ids = [s['token_id'] for s in subjects]
        query = 'SELECT id,request_id,upstream_request_id,user_id,token_id,workspace_team_id,quota,other FROM logs WHERE type=2 AND token_id IN (' + ','.join('?' for _ in ids) + ') ORDER BY id'
        logs = []
        for row in con.execute(query, ids):
            item = dict(row)
            other = json.loads(item.pop('other') or '{}')
            item['stream_status'] = other.get('stream_status')
            item['billing_source'] = other.get('billing_source')
            logs.append(item)
        return {'tokens': tokens, 'users': users, 'logs': logs}
    finally:
        con.close()


def ledger_reconciliation(before, after):
    """Wallet/subscription test subjects must converge after async refunds/logs."""
    old_ids = {row['id'] for row in before['logs']}
    logs = [row for row in after['logs'] if row['id'] not in old_ids]
    checks = {}
    for token_id, token in before['tokens'].items():
        charged = sum(row['quota'] for row in logs if row['token_id'] == int(token_id))
        current = after['tokens'][token_id]
        checks['token_' + token_id + '_used'] = current['used_quota'] - token['used_quota'] == charged
        checks['token_' + token_id + '_remaining'] = token['remain_quota'] - current['remain_quota'] == charged
    for user_id, user in before['users'].items():
        rows = [row for row in logs if row['user_id'] == int(user_id)]
        assert all(row['billing_source'] in ('wallet', 'subscription') for row in rows), 'Mixed funding requires separate reconciliation'
        current = after['users'][user_id]
        checks['payer_' + user_id + '_used'] = current['used_quota'] - user['used_quota'] == sum(row['quota'] for row in rows)
        checks['payer_' + user_id + '_wallet'] = user['quota'] - current['quota'] == sum(row['quota'] for row in rows if row['billing_source'] == 'wallet')
    return checks


def observe_settlement(path, subjects, before, timeout=10):
    """Wait on exact financial invariants, never replay an HTTP/model request."""
    deadline = time.monotonic() + timeout
    observations = 0
    while True:
        after = ledger_snapshot(path, subjects)
        checks = ledger_reconciliation(before, after)
        observations += 1
        if all(checks.values()) or time.monotonic() >= deadline:
            return after, checks, observations
        time.sleep(0.1)


def websocket_dialogue(base_url, model, key, reserve):
    from websockets.sync.client import connect
    endpoint = base_url.replace('https://', 'wss://').replace('http://', 'ws://').rstrip('/') + '/responses?model=' + quote(model)
    marker = 'RELEASE_WS_' + secrets.token_hex(8)
    headers = {'Authorization': 'Bearer ' + key, 'OpenAI-Beta': 'responses_websockets=2026-02-06', 'Originator': 'codex_cli_rs'}
    results = []
    with connect(endpoint, additional_headers=headers, open_timeout=20, close_timeout=5,
                 max_size=16 * 1024 * 1024, proxy=None) as ws:
        for turn in range(2):
            reserve()
            payload = {'type': 'response.create', 'model': model, 'store': False,
                       'input': [{'role': 'user', 'content': 'Remember ' + marker + '. Reply only ACK.' if turn == 0 else 'Repeat the remembered marker exactly.'}]}
            if turn:
                payload['previous_response_id'] = results[0]['response_id']
            ws.send(json.dumps(payload))
            deltas = []
            deadline = time.monotonic() + 120
            while True:
                remaining = deadline - time.monotonic()
                assert remaining > 0, 'WS terminal response deadline exceeded'
                event = json.loads(ws.recv(timeout=remaining))
                assert event.get('type') not in ('error', 'response.failed'), 'WS returned a failure event'
                if event.get('type') == 'response.output_text.delta':
                    deltas.append(event['delta'])
                if event.get('type') == 'response.completed':
                    final = event['response']
                    text = ''.join(p.get('text', '') for item in final.get('output', []) if item.get('type') == 'message' for p in item.get('content', []) if p.get('type') == 'output_text')
                    assert final.get('status') == 'completed' and text == ''.join(deltas), 'WS terminal body mismatch'
                    assert text.strip() == ('ACK' if turn == 0 else marker), 'WS continuation lost content'
                    results.append({'response_id': final['id'], 'terminal_status': final['status']})
                    break
    return {'turns': results, 'same_socket_continuation': True}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--secret-file', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--cases', default=','.join(CASES))
    parser.add_argument('--run', action='store_true', help='Execute the selected requests; otherwise validate local inputs only')
    args = parser.parse_args()
    config = json.loads(args.secret_file.read_text(encoding='utf-8-sig'))
    base = config['base_url'].rstrip('/')
    target = urlsplit(base)
    assert target.scheme in ('http', 'https') and not target.username and not target.password
    assert target.path.rstrip('/') == '/v1' and not target.query and not target.fragment
    assert target.scheme == 'https' or target.hostname in ('127.0.0.1', 'localhost', '::1'), 'Non-loopback target requires verified TLS'
    subjects = config['subjects']
    assert subjects and config.get('dedicated_operator_subjects') is True, 'Dedicated operator subjects must be explicitly identified'
    assert len({s['api_key'] for s in subjects}) == len(subjects), 'Subjects must use different API keys'
    selected = args.cases.split(',')
    assert all(case in CASES for case in selected), 'Unknown case'
    assert len(selected) == len(set(selected)), 'Duplicate cases are not allowed'
    if 'subjects' in selected:
        assert len(subjects) >= 3 and any(s['workspace_team_id'] for s in subjects), 'Subject acceptance requires two personal subjects and one team member'
        assert len({(s['user_id'], s['workspace_team_id']) for s in subjects}) == len(subjects), 'Duplicate subject identity'
    text_models = config.get('text_models', ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-sol', 'gpt-6-luna',
                                           'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', 'gpt-5.5'])
    assert len(text_models) <= 8 and len(subjects) <= 4, 'Bounded acceptance supports at most eight text models and four subjects'
    binary = Path(config['gateway_binary']).resolve(strict=True)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert digest == config['gateway_sha256'], 'Release binary hash mismatch'
    db = Path(config['ledger_db']).resolve(strict=True)
    before = ledger_snapshot(db, subjects)
    assert not args.output.exists(), 'Preserve first results; choose a new output directory'
    request_budget = {case: 1 for case in CASES}
    request_budget.update({'catalog': 0, 'websocket': 2, 'tool-roundtrip': 2,
                           'subjects': len(subjects), 'all-text-models': len(text_models)})
    plan = {'status': 'PREPARED', 'cases': selected, 'network_requests_executed': 0,
            'gateway_sha256': digest, 'subject_count': len(subjects),
            'automatic_retries': 0, 'maximum_model_requests': sum(request_budget[case] for case in selected),
            'scope': 'Dedicated operator identities; existing customer state is never provisioned or adjusted'}
    if not args.run:
        print(json.dumps(plan, indent=2))
        return 0
    args.output.mkdir(parents=True)
    report = {**plan, 'status': 'RUNNING', 'tested_at_utc': dt.datetime.now(dt.timezone.utc).isoformat(),
              'sdk_version': openai.__version__, 'checks': [], 'requests': [],
              'desktop_e2e_performed': False, 'long_term_stability_proven': False}
    report.pop('network_requests_executed')
    model = config.get('model', 'gpt-6.1-sol')
    current = {'case': None, 'model_requests': 0}

    def save():
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    def reserve():
        current['model_requests'] += 1
        assert current['model_requests'] <= plan['maximum_model_requests'], 'Model request budget exhausted'

    def request_hook(request):
        if request.method == 'POST' and request.url.path.endswith(('/responses', '/chat/completions', '/images/generations')):
            reserve()

    def response_hook(response):
        report['requests'].append({'case': current['case'], 'method': response.request.method,
                                   'http_status': response.status_code,
                                   'request_id': response.headers.get('X-Oneapi-Request-Id')})

    transport = httpx.Client(trust_env=False, timeout=120, follow_redirects=False,
                             event_hooks={'request': [request_hook], 'response': [response_hook]})
    clients = [openai.OpenAI(api_key=s['api_key'], base_url=base, max_retries=0, timeout=120, http_client=transport) for s in subjects]
    client = clients[0]
    save()
    try:
        health = transport.get(base.removesuffix('/v1') + '/api/status').json()
        assert health.get('success') and health['data']['version'] == config['expected_version'], 'Target version does not match the declared release'
        for name in selected:
            current['case'] = name
            row = {'name': name, 'status': 'FAIL'}
            started = time.monotonic()
            marker = 'RELEASE_' + secrets.token_hex(8)
            try:
                if name == 'catalog':
                    for actor in clients:
                        listing = actor.models.list()
                        for model_name in text_models + ['gpt-image-2']:
                            item = next((x for x in listing.data if x.id == model_name), None)
                            assert item, 'Authorized model absent from catalog: ' + model_name
                            detail = actor.models.retrieve(model_name)
                            assert detail.model_dump() == item.model_dump(), 'Model detail differs from its catalog entry'
                        try:
                            actor.models.retrieve('realyu-nonexistent-' + secrets.token_hex(8))
                        except openai.NotFoundError:
                            pass
                        else:
                            raise AssertionError('Unknown model must return 404')
                    row['subjects_checked'] = len(clients)
                elif name == 'responses-stream':
                    row.update(check_stream(client, model, 'Reply exactly ' + marker, marker))
                elif name == 'responses-nonstream':
                    final = client.responses.create(model=model, input='Reply exactly ' + marker)
                    assert final.status == 'completed' and final.output_text.strip() == marker, 'Nonstream terminal mismatch'
                    row['response_id'] = final.id
                elif name == 'chat':
                    final = client.chat.completions.create(model=model, messages=[{'role': 'user', 'content': 'Reply exactly ' + marker}])
                    assert final.choices[0].message.content.strip() == marker, 'Chat content mismatch'
                elif name == 'pdf-inline':
                    folder = Path(__file__).parent / 'portable/fixtures'
                    expected = json.loads((folder / 'expected.json').read_text())
                    data = 'data:application/pdf;base64,' + base64.b64encode((folder / 'reading-fixture.pdf').read_bytes()).decode()
                    content = [{'role': 'user', 'content': [{'type': 'input_file', 'filename': 'reading-fixture.pdf', 'file_data': data},
                               {'type': 'input_text', 'text': 'Read both pages. Return the verification code, combined number of blue and green boxes, and warehouse name.'}]}]
                    row.update(check_stream(client, model, content, expected_terms=(expected['code'], str(expected['total']), expected['warehouse'])))
                elif name == 'pdf-url':
                    content = [{'role': 'user', 'content': [{'type': 'input_file', 'file_url': 'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf'},
                               {'type': 'input_text', 'text': 'Return only the exact visible PDF text.'}]}]
                    row.update(check_stream(client, model, content, expected_terms=('Dummy PDF file',)))
                    row['tls_verification_disabled'] = False
                elif name == 'web-search':
                    row.update(check_stream(client, model, 'Search the official Python documentation for pathlib Path.read_text. Give one brief factual sentence with a source citation.', search=True))
                elif name == 'tool-roundtrip':
                    history = [{'role': 'user', 'content': 'Call read_marker then return only the tool result.'}]
                    tools = [{'type': 'function', 'name': 'read_marker', 'parameters': {'type': 'object', 'properties': {}, 'required': [], 'additionalProperties': False}, 'strict': True}]
                    first = client.responses.create(model=model, input=history, tools=tools, tool_choice={'type': 'function', 'name': 'read_marker'}, include=['reasoning.encrypted_content'], store=False)
                    calls = [item for item in first.output if item.type == 'function_call']
                    assert first.status == 'completed' and len(calls) == 1, 'Missing terminal function call'
                    history += [item.model_dump(exclude_none=True) for item in first.output]
                    history.append({'type': 'function_call_output', 'call_id': calls[0].call_id, 'output': marker})
                    final = client.responses.create(model=model, input=history, tools=tools, tool_choice='none', store=False)
                    assert final.status == 'completed' and final.output_text.strip() == marker, 'Tool result/terminal mismatch'
                    row['response_ids'] = [first.id, final.id]
                elif name == 'websocket':
                    row.update(websocket_dialogue(base, model, subjects[0]['api_key'], reserve))
                elif name == 'subjects':
                    row['subjects'] = []
                    for subject, actor in zip(subjects, clients):
                        subject_marker = 'RELEASE_SUBJECT_' + secrets.token_hex(8)
                        result = check_stream(actor, model, 'Reply exactly ' + subject_marker, subject_marker)
                        row['subjects'].append({'label': subject['label'], 'user_id': subject['user_id'],
                                                'workspace_team_id': subject['workspace_team_id'], **result})
                    row['limit'] = 'Request authorization and final content only; upstream ownership isolation requires separate mapping/negative evidence.'
                elif name == 'failed-request':
                    failed_before = ledger_snapshot(db, subjects)
                    try:
                        client.responses.create(model=model, input='Reply OK', reasoning={'effort': 'realyu_invalid_acceptance_value'})
                    except openai.APIStatusError as exc:
                        assert 400 <= exc.status_code < 500, 'Expected bounded validation denial'
                        row['http_status'] = exc.status_code
                    else:
                        raise AssertionError('Invalid reasoning value unexpectedly accepted')
                    failed_after, _, row['settlement_observations'] = observe_settlement(db, subjects, failed_before)
                    assert failed_before['users'] == failed_after['users'] and failed_before['tokens'] == failed_after['tokens'], 'Denied request changed dedicated balances'
                    row['no_charge_verified'] = True
                    row['pre_reservation_refund_verified'] = False
                    row['limit'] = 'No-charge denial is not proof of pre-reservation refund; correlate reserve/refund server logs separately.'
                elif name == 'all-text-models':
                    row['models'] = []
                    for model_name in text_models:
                        result = {'model': model_name, 'status': 'FAIL'}
                        try:
                            result.update(check_stream(client, model_name, 'Reply exactly ' + marker, marker))
                            result['status'] = 'PASS'
                        except Exception as exc:
                            result['error_type'] = type(exc).__name__
                            result['http_status'] = getattr(exc, 'status_code', None)
                        row['models'].append(result)
                        print(json.dumps({'model': model_name, 'status': result['status']}), flush=True)
                    assert all(x['status'] == 'PASS' for x in row['models']), 'At least one text model failed its complete SSE contract'
                elif name == 'image-generation':
                    from PIL import Image
                    from io import BytesIO
                    prompt = config.get('image_prompt', 'One flat vector image of a blue square centered on a white background. No text.')
                    quality = config.get('image_quality', 'low')
                    assert quality in ('auto', 'low', 'medium', 'high'), 'Unknown image quality'
                    result = client.images.generate(model='gpt-image-2', prompt=prompt,
                                                    n=1, size='1024x1024', quality=quality, response_format='b64_json')
                    assert len(result.data) == 1 and result.data[0].b64_json, 'Expected exactly one image payload'
                    data = base64.b64decode(result.data[0].b64_json, validate=True)
                    with Image.open(BytesIO(data)) as img:
                        img.verify()
                    with Image.open(BytesIO(data)) as img:
                        row['dimensions'] = list(img.size)
                        extension = img.format.lower()
                    assert extension in ('png', 'jpeg', 'webp'), 'Unexpected image encoding'
                    (args.output / ('generated-image.' + extension)).write_bytes(data)
                    row.update(image_bytes=len(data), image_sha256=hashlib.sha256(data).hexdigest(), visual_review='NOT_RUN',
                               image_prompt=prompt, requested_quality=quality,
                               reported_usage=result.usage.model_dump() if getattr(result, 'usage', None) else None)
                row['status'] = 'PASS'
            except Exception as exc:
                row['error_type'] = type(exc).__name__
                row['http_status'] = getattr(exc, 'status_code', row.get('http_status'))
                if isinstance(exc, AssertionError):
                    row['assertion'] = str(exc)
            row['seconds'] = round(time.monotonic() - started, 3)
            report['checks'].append(row)
            save()
            print(json.dumps({'name': name, 'status': row['status'], 'seconds': row['seconds']}), flush=True)
            if row['status'] != 'PASS' and config.get('stop_on_failure') is True:
                report['remaining_cases_not_run'] = selected[len(report['checks']):]
                break
    finally:
        transport.close()
        after, accounting, observations = observe_settlement(db, subjects, before)
        old_ids = {row['id'] for row in before['logs']}
        new_logs = [row for row in after['logs'] if row['id'] not in old_ids]
        report['ledger'] = {'before': {'users': before['users'], 'tokens': before['tokens']},
                            'after': {'users': after['users'], 'tokens': after['tokens']}, 'new_usage': new_logs,
                            'reconciliation': accounting, 'settlement_observations': observations,
                            'scope': 'Dedicated identities, bounded read-only observation of token and payer invariants; subscription rows require separate exact readback'}
        report['model_requests_executed'] = current['model_requests']
        report['http_responses_observed'] = len(report['requests'])
        report['status'] = 'PASS' if len(report['checks']) == len(selected) and all(row['status'] == 'PASS' for row in report['checks']) and all(accounting.values()) else 'FAIL'
        save()
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())

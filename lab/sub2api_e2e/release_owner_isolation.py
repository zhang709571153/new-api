"""Bounded four-attempt real WS isolation check on dedicated operator subjects.

Source personal identity remains connected. A different person and the source
person's team identity attempt to continue its response, then the original
identity must still continue correctly. No key/permissions/service mutations.
"""
import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import secrets
import time
from urllib.parse import quote, urlsplit

import httpx
from websockets.sync.client import connect

from release_acceptance import ledger_snapshot, observe_settlement


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--secret-file', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    cfg = json.loads(args.secret_file.read_text(encoding='utf-8-sig'))
    assert cfg.get('dedicated_operator_subjects') is True
    base = cfg['base_url'].rstrip('/')
    url = urlsplit(base)
    assert url.path == '/v1' and not url.query and not url.fragment
    assert url.scheme == 'https' or (url.scheme == 'http' and url.hostname == '127.0.0.1')
    binary = Path(cfg['gateway_binary']).resolve(strict=True)
    digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    assert digest == cfg['gateway_sha256']
    subjects = cfg['subjects']
    team = next(s for s in subjects if s['workspace_team_id'])
    source = next(s for s in subjects if not s['workspace_team_id'] and s['user_id'] == team['user_id'])
    outsider = next(s for s in subjects if not s['workspace_team_id'] and s['user_id'] != source['user_id'])
    assert len({s['api_key'] for s in (source, team, outsider)}) == 3
    database = Path(cfg['ledger_db']).resolve(strict=True)
    before = ledger_snapshot(database, subjects)
    assert not args.output.exists(), 'Preserve first-failure output'
    report = {'status': 'PREPARED', 'gateway_sha256': digest, 'maximum_model_attempts': 8,
              'automatic_retries': 0, 'checks': [], 'scope': 'Dedicated real WS and HTTP response ownership'}
    if not args.run:
        print(json.dumps(report))
        return 0
    args.output.mkdir(parents=True)
    report.update(status='RUNNING', started_at=dt.datetime.now(dt.timezone.utc).isoformat())
    model = cfg.get('model', 'gpt-6.1-sol')
    endpoint = base.replace('https://', 'wss://').replace('http://', 'ws://') + '/responses?model=' + quote(model)
    marker = 'OWNER_PRIVATE_' + secrets.token_hex(12)

    def save():
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2))

    def socket(subject):
        return connect(endpoint, additional_headers={'Authorization': 'Bearer ' + subject['api_key'],
            'OpenAI-Beta': 'responses_websockets=2026-02-06', 'Originator': 'codex_cli_rs'},
            open_timeout=20, close_timeout=5, max_size=16 * 1024 * 1024, proxy=None)

    def turn(ws, label, previous=None, negative=False):
        payload = {'type': 'response.create', 'model': model, 'store': False,
                   'input': [{'role': 'user', 'content': 'Remember ' + marker + '. Reply only ACK.'
                               if previous is None else 'Repeat the remembered marker exactly.'}]}
        if previous:
            payload['previous_response_id'] = previous
        ws.send(json.dumps(payload))
        deltas, events = [], []
        deadline = time.monotonic() + 120
        while True:
            event = json.loads(ws.recv(timeout=max(.1, deadline - time.monotonic())))
            events.append(event)
            (args.output / (label + '.json')).write_text(json.dumps(events))
            if negative:
                assert marker not in json.dumps(event), 'Cross-subject retained content exposed'
            if event.get('type') == 'response.output_text.delta':
                deltas.append(event['delta'])
            if event.get('type') in ('error', 'response.failed'):
                assert negative, 'Authorized source turn failed'
                error = event.get('error', event.get('response', {}).get('error', {})) or {}
                status = event.get('status', error.get('status', error.get('status_code')))
                known_denial = error.get('code') in ('response_not_found', 'previous_response_not_found',
                    'invalid_previous_response_id', 'previous_response_id_not_found', 'permission_denied', 'forbidden', 'unauthorized')
                assert (status is not None and 400 <= int(status) < 500) or (status is None and known_denial), 'Not an ownership-compatible client denial'
                assert not deltas, 'Denied scope received text'
                return {'label': label, 'status': 'PASS', 'denied': True,
                        'http_status': status, 'error_type': error.get('type'), 'error_code': error.get('code')}
            if event.get('type') == 'response.completed':
                assert not negative, 'Cross-subject continuation was accepted'
                final = event['response']
                text = ''.join(p.get('text', '') for item in final.get('output', [])
                               if item.get('type') == 'message' for p in item.get('content', []) if p.get('type') == 'output_text')
                assert final.get('status') == 'completed' and text == ''.join(deltas)
                assert text.strip() == ('ACK' if previous is None else marker)
                return {'label': label, 'status': 'PASS', 'response_id': final['id']}
            assert time.monotonic() < deadline, 'Terminal response deadline exceeded'

    save()
    try:
        with httpx.Client(trust_env=False, timeout=20) as client:
            health = client.get(base.removesuffix('/v1') + '/api/status').json()
        assert health['data']['version'] == cfg['expected_version']
        with socket(source) as ws:
            first = turn(ws, 'source-first')
            report['checks'].append(first)
            save()
            denial_before = ledger_snapshot(database, subjects)
            public_id = first['response_id']
            assert public_id.startswith('resp_ry1_'), 'Response ID lacks subject authentication'
            encoded = public_id.removeprefix('resp_ry1_').split('.')[0]
            raw_id = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4)).decode()
            tampered = public_id[:-1] + ('0' if public_id[-1] != '0' else '1')
            with httpx.Client(trust_env=False, timeout=20) as client:
                for label, subject, response_id in (
                        ('http-different-person', outsider, public_id),
                        ('http-same-person-team', team, public_id),
                        ('http-unsigned-id', source, raw_id),
                        ('http-tampered-id', source, tampered)):
                    response = client.post(base + '/responses', headers={'Authorization': 'Bearer ' + subject['api_key']},
                        json={'model': model, 'input': 'Repeat the remembered marker.', 'previous_response_id': response_id,
                              'store': False, 'stream': False})
                    assert response.status_code == 403, 'HTTP ownership denial must be explicit'
                    assert marker not in response.text, 'HTTP denial exposed retained content'
                    report['checks'].append({'label': label, 'status': 'PASS', 'denied': True,
                        'http_status': response.status_code, 'request_id': response.headers.get('X-Oneapi-Request-Id')})
                    save()
            for label, subject in (('different-person', outsider), ('same-person-team', team)):
                with socket(subject) as foreign:
                    result = turn(foreign, label, first['response_id'], negative=True)
                report['checks'].append(result)
                save()
            denied_after, _, observations = observe_settlement(database, subjects, denial_before)
            for subject in (source, outsider, team):
                token = str(subject['token_id'])
                assert denial_before['tokens'][token] == denied_after['tokens'][token], 'Denied subject was charged'
            report['denied_token_no_charge'] = True
            report['denial_observations'] = observations
            report['checks'].append(turn(ws, 'source-continues', first['response_id']))
        report['status'] = 'PASS'
    except Exception as exc:
        report.update(status='FAIL', error_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            report['assertion'] = str(exc)
    finally:
        after, checks, observations = observe_settlement(database, subjects, before)
        report['ledger'] = {'before': before, 'after': after, 'reconciliation': checks,
                            'settlement_observations': observations}
        if not all(checks.values()):
            report['status'] = 'FAIL'
        save()
    print(json.dumps({'status': report['status'], 'completed_checks': len(report['checks'])}))
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())

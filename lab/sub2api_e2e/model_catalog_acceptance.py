"""Real gateway HTTP model catalog regression, with zero inference.

The upstream is an observation-only loopback control fixture. This test proves
local authorization/read semantics and no upstream calls, not stock inference.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time

from bootstrap_windows import ROOT, PRIVATE, FLAGS
from projection_control_fixture import ProjectionControlFixture

sys.path.insert(0, str(ROOT / 'lab'))
import verify_provider_release as client
sys.path.insert(0, str(ROOT / 'deploy/sub2api'))
from bootstrap_route import bootstrap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gateway', required=True, type=Path)
    parser.add_argument('--port', type=int, default=28503)
    args = parser.parse_args()
    if args.port < 1024 or args.port in (18300, 18301):
        raise RuntimeError('Only an isolated local port is allowed')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise RuntimeError('Candidate port occupied')
    run = PRIVATE / ('model-catalog-' + secrets.token_hex(5))
    run.mkdir()
    binary = args.gateway.resolve(strict=True)
    database = run / 'new-api.db'
    secret, password = secrets.token_hex(32), secrets.token_urlsafe(24)
    env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower() and not k.startswith('REALYU_SUB2API')
        and k not in ('SQL_DSN', 'LOG_SQL_DSN', 'REDIS_CONN_STRING', 'REALYU_UPSTREAM_DRIVER')}
    env.update(PORT=str(args.port), BIND_ADDRESS='127.0.0.1', SQLITE_PATH=str(database) +
        '?_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate', SESSION_SECRET=secret,
        CRYPTO_SECRET=secret, GIN_MODE='release', SESSION_COOKIE_SECURE='false', BATCH_UPDATE_ENABLED='false',
        NODE_TYPE='master', VERSION='realyu-sub2api-catalog-e2e')
    client.BASE = 'http://127.0.0.1:' + str(args.port)
    report = {'run_id': run.name, 'scope': 'real gateway HTTP authorization and catalog; observation-only upstream fixture',
        'gateway_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'production_changed': False,
        'model_requests_executed': 0, 'checks': {}, 'requests': [], 'all_passed': False}
    processes, sessions, receipts = [], [], []

    def check(name, passed):
        report['checks'][name] = bool(passed)
        print(json.dumps({'check': name, 'passed': bool(passed)}), flush=True)
        if not passed:
            raise AssertionError(name)

    def stop(p):
        if p.poll() is None:
            p.terminate(); p.wait(timeout=20)

    def start(name):
        with (run / (name + '.log')).open('ab') as log:
            p = subprocess.Popen([str(binary), '--log-dir', str(run / 'logs')], cwd=run, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
        processes.append(p)
        receipts.append({'pid': p.pid, 'executable': str(binary), 'name': name})
        (run / 'processes.json').write_text(json.dumps(receipts), encoding='utf-8')
        for _ in range(100):
            if p.poll() is not None:
                raise RuntimeError('Candidate startup failed')
            try:
                client.API().call('/api/setup'); return p
            except Exception:
                time.sleep(.2)
        raise RuntimeError('Candidate startup timeout')

    def ledger():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            return {'users': db.execute('select id,quota,used_quota,request_count from users order by id').fetchall(),
                'tokens': db.execute('select id,remain_quota,used_quota from tokens order by id').fetchall(),
                'consume': db.execute('select count(*),coalesce(sum(quota),0) from logs where type=2').fetchone(),
                'subscriptions': db.execute('select id,amount_used from user_subscriptions order by id').fetchall()}

    def request(actor, label, path, body=None):
        code, value, headers, _ = actor.request(path, body)
        (run / (label + '.json')).write_text(json.dumps(value), encoding='utf-8')
        report['requests'].append({'label': label, 'path': path, 'http_status': code,
            'request_id': headers.get('X-Oneapi-Request-Id'),
            'error_code': value.get('error', {}).get('code') if isinstance(value, dict) else None})
        return code, value

    with ProjectionControlFixture() as observer:
        try:
            p = start('bootstrap')
            admin = client.API()
            admin.call('/api/setup', {'username': 'catalogadmin', 'password': password, 'confirmPassword': password,
                'SelfUseModeEnabled': False, 'DemoSiteEnabled': False})
            aid = admin.login('catalogadmin', password)['id']; sessions.append(admin)
            admin.call('/api/option/', {'key': 'ModelRatio', 'value': json.dumps({
                'gpt-6.1-sol': 1, 'catalog-other': 1, 'catalog-private': 1})}, 'PUT')
            admin.call('/api/user/', {'username': 'cataloguser', 'password': password, 'display_name': 'Catalog fixture', 'role': 1})
            actor = client.API(); uid = actor.login('cataloguser', password)['id']; sessions.append(actor)
            actor.call('/api/workspace/key', {})
            normal = client.API(actor.call('/api/workspace/key/reveal', {})['api_key'])
            actor.call('/api/token/', {'name': 'restricted-empty', 'expired_time': -1, 'remain_quota': 0,
                'unlimited_quota': False, 'model_limits_enabled': True, 'model_limits': 'gpt-6.1-sol', 'group': ''})
            tokens = actor.call('/api/token/')
            tid = next(t['id'] for t in tokens['items'] if t['name'] == 'restricted-empty')
            key_result = actor.call('/api/token/' + str(tid) + '/key', {})
            restricted = client.API('sk-' + key_result['key'].removeprefix('sk-'))
            pools = []
            for name, models, group in (('sub2api-default-catalog', 'gpt-6.1-sol,catalog-other', 'default'),
                ('sub2api-private-catalog', 'catalog-private', 'catalog-private')):
                route = bootstrap({'candidate_url': client.BASE, 'expected_version': 'realyu-sub2api-catalog-e2e',
                    'admin_access_token': 'fixture-adapter', 'admin_user_id': aid,
                    'route': {'name': name, 'base_url': observer.base_url, 'models': models,
                        'group': group, 'sub2api_group_id': 1}}, admin)
                pools.append({'channel_id': route['channel_id'], 'base_url': observer.base_url, 'group_id': 1})
            bindings = run / 'bindings.json'
            bindings.write_text(json.dumps({'version': 1, 'namespace': run.name, 'identity_secret': secret,
                'admin_api_key': 'fixture-admin', 'pools': pools, 'provision': {'enabled': True, 'mode': 'prewarmed'}, 'bindings': []}), encoding='utf-8')
            stop(p)
            env.update(REALYU_UPSTREAM_DRIVER='sub2api', REALYU_SUB2API_BINDINGS_FILE=str(bindings))
            p = start('catalog-enabled')
            initial = ledger()
            check('fresh_customer_wallet_is_zero', next(row for row in initial['users'] if row[0] == uid)[1:] == (0, 0, 0))
            check('restricted_key_has_zero_remaining_and_used_quota', next(row for row in initial['tokens'] if row[0] == tid)[1:] == (0, 0))
            code, listing = request(normal, 'normal-list', '/v1/models')
            descriptors = {row['id']: row for row in listing.get('data', [])}
            check('list_contains_exact_authorized_custom_models', code == 200 and set(descriptors) == {'gpt-6.1-sol', 'catalog-other'})
            for model in ('gpt-6.1-sol', 'catalog-other'):
                code, detail = request(normal, 'normal-detail-' + model, '/v1/models/' + model)
                check('detail_matches_list_' + model, code == 200 and detail == descriptors[model])
            code, limited = request(restricted, 'restricted-list', '/v1/models')
            check('zero_balance_restricted_key_can_list_only_allowed_model', code == 200 and [x['id'] for x in limited.get('data', [])] == ['gpt-6.1-sol'])
            code, detail = request(restricted, 'restricted-allowed-detail', '/v1/models/gpt-6.1-sol')
            check('zero_balance_restricted_key_can_read_allowed_detail', code == 200 and detail == descriptors['gpt-6.1-sol'])
            for label, api, model in (('model-restricted', restricted, 'catalog-other'),
                ('group-restricted', normal, 'catalog-private'), ('unknown', normal, 'never-offered')):
                code, detail = request(api, label, '/v1/models/' + model)
                check(label + '_returns_404_model_not_found', code == 404 and detail.get('error', {}).get('code') == 'model_not_found')
            code, _ = request(client.API(), 'missing-auth', '/v1/models/gpt-6.1-sol')
            check('model_detail_requires_authentication', code == 401)
            code, _ = request(client.API('sk-fixture-invalid'), 'invalid-auth', '/v1/models/gpt-6.1-sol')
            check('model_detail_rejects_invalid_authentication', code == 401)
            code, _ = request(restricted, 'zero-quota-inference', '/v1/responses', {'model': 'gpt-6.1-sol', 'input': 'Must never reach upstream'})
            check('read_only_exception_does_not_authorize_zero_quota_inference', code in (401, 403))
            check('all_catalog_and_denial_requests_leave_ledger_unchanged', ledger() == initial)
            check('no_upstream_management_login_or_inference_requests', len(observer.snapshot()['events']) == 0)
            report['all_passed'] = all(report['checks'].values())
        except Exception as exc:
            (run / 'failure.txt').write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
            report['failure'] = {'type': type(exc).__name__, 'after_check': next(reversed(report['checks']), 'startup')}
        finally:
            for session in sessions:
                session.logout()
            for process in reversed(processes):
                stop(process)
            report['owned_processes_stopped'] = all(p.poll() is not None for p in processes)
            (run / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
            public = ROOT / 'lab/sub2api_e2e/model-catalog-summary.json'
            if public.exists():
                old = json.loads(public.read_text(encoding='utf-8'))
                public.with_name('model-catalog-' + old['run_id'] + '.json').write_text(json.dumps(old, indent=2), encoding='utf-8')
            public.write_text(json.dumps(report, indent=2), encoding='utf-8')
            print(json.dumps({'all_passed': report['all_passed'], 'run_id': run.name, 'failure': report.get('failure')}), flush=True)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())

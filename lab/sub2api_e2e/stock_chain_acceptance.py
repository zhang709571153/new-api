"""Real RealYu -> stock Sub2API -> synthetic upstream acceptance.

Uses the already reviewed private prewarming queue and fresh local RealYu data.
No production data or actual model calls. Requires the isolated SDK venv.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import openai

from bootstrap_windows import PRIVATE, ROOT, FLAGS, get_json
from strict_sdk import check_stream

sys.path.insert(0, str(ROOT / 'lab'))
import verify_provider_release as client
sys.path.insert(0, str(ROOT / 'deploy/sub2api'))
from bootstrap_route import bootstrap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gateway', required=True, type=Path)
    parser.add_argument('--port', type=int, default=28501)
    parser.add_argument('--run', type=Path)
    parser.add_argument('--team-only', action='store_true')
    parser.add_argument('--worker', type=Path)
    args = parser.parse_args()
    source = args.run or Path(json.loads((PRIVATE / 'stock-chain-current.json').read_text())['run_dir'])
    source = source.resolve(strict=True)
    if not source.is_relative_to(PRIVATE.resolve()):
        raise RuntimeError('Private queue must belong to this isolated test directory')
    run = source / ('attempt-' + secrets.token_hex(5))
    run.mkdir()
    config = json.loads((source / 'bindings.json').read_text())
    binary = args.gateway.resolve(strict=True)
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise RuntimeError('Gateway port occupied')
    client.BASE = 'http://127.0.0.1:' + str(args.port)
    database = run / 'new-api.db'
    report = {'scope': 'actual RealYu and stock Sub2API with synthetic upstream', 'real_model_test': False,
        'production_changed': False, 'gateway_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'sub2api_version': '0.2.15', 'sdk_version': openai.__version__, 'checks': {}, 'all_passed': False}
    password, secret = secrets.token_urlsafe(24), secrets.token_hex(32)
    env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower() and k not in
           ('SQL_DSN', 'LOG_SQL_DSN', 'REDIS_CONN_STRING', 'REALYU_UPSTREAM_DRIVER', 'REALYU_SUB2API_BINDINGS_FILE')}
    sqlite_dsn = str(database) + '?_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate'
    report['sqlite_dsn_options'] = '_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate'
    env.update(PORT=str(args.port), BIND_ADDRESS='127.0.0.1', SQLITE_PATH=sqlite_dsn, SESSION_SECRET=secret,
        CRYPTO_SECRET=secret, GIN_MODE='release', SESSION_COOKIE_SECURE='false', BATCH_UPDATE_ENABLED='false',
        NODE_TYPE='master', RELAY_USER_CONCURRENCY='10', VERSION='realyu-sub2api-stock-chain')
    processes, sessions = [], []

    def save():
        (run / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    def check(name, value):
        report['checks'][name] = bool(value)
        save()
        print(json.dumps({'check': name, 'passed': bool(value)}), flush=True)
        if not value:
            raise AssertionError(name)

    def management(path):
        request = urllib.request.Request(config['pools'][0]['base_url'] + '/api/v1' + path,
            headers={'x-api-key': config['admin_api_key']})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), client.NoRedirect())
        with opener.open(request, timeout=20) as response:
            result = json.load(response)
        if result.get('code') != 0:
            raise RuntimeError('Stock management rejected read')
        return result['data']

    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20)

    def start(label):
        with (run / (label + '.log')).open('ab') as log:
            process = subprocess.Popen([str(binary), '--log-dir', str(run / 'logs')], cwd=run, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
        processes.append(process)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('Candidate exited; inspect private log')
            try:
                client.API().call('/api/setup')
                return process
            except Exception:
                time.sleep(0.2)
        raise RuntimeError('Candidate startup timeout')

    def ledger():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            return {'users': db.execute('select id,quota,used_quota,request_count from users order by id').fetchall(),
                'tokens': db.execute('select id,remain_quota,used_quota from tokens order by id').fetchall(),
                'consume': db.execute('select count(*),coalesce(sum(quota),0) from logs where type=2').fetchone(),
                'subscriptions': db.execute('select id,amount_used from user_subscriptions order by id').fetchall()}

    def settled_ledger(expected_count):
        # Delivery can precede the asynchronous consume-log insert. Do not pace
        # requests or retry inference; only bound the final observation window.
        deadline = time.monotonic() + 5
        while True:
            result = ledger()
            if result['consume'][0] >= expected_count or time.monotonic() >= deadline:
                return result
            time.sleep(.05)

    def sdk_cases(label, base, key):
        sdk = openai.OpenAI(api_key=key, base_url=base, max_retries=0, timeout=30)
        marker = 'E2E_' + secrets.token_hex(8)
        streamed = check_stream(sdk, 'gpt-6.1-sol', 'Reply exactly ' + marker, marker)
        check(label + '_sdk_stream_final_matches_delta', streamed['final_characters'] == len(marker))
        response = sdk.responses.create(model='gpt-6.1-sol', input='Reply exactly ' + marker, stream=False)
        check(label + '_sdk_nonstream_final', response.status == 'completed' and response.output_text.strip() == marker)
        chat = sdk.chat.completions.create(model='gpt-6.1-sol', messages=[{'role': 'user', 'content': 'Reply exactly ' + marker}])
        check(label + '_sdk_chat_nonstream_final', chat.choices[0].message.content.strip() == marker)
        return sdk

    try:
        upstream = {}
        for user_id in (2, 3):
            payload = '\0'.join((config['namespace'], 'email', str(user_id), '0')).encode()
            email = hmac.new(config['identity_secret'].encode(), payload, hashlib.sha256).hexdigest()[:40] + '@realyu.invalid'
            users = management('/admin/users?search=' + urllib.parse.quote(email) + '&page_size=100')['items']
            user = next(row for row in users if row['email'] == email)
            keys = management('/admin/users/' + str(user['id']) + '/api-keys?page_size=100')['items']
            upstream[user_id] = {'id': user['id'], 'keys': {key['group_id']: key['key'] for key in keys}}
        check('stock_prewarmed_customers_have_distinct_upstream_users', upstream[2]['id'] != upstream[3]['id'])
        check('each_stock_customer_has_both_dedicated_pool_keys', all(len(row['keys']) == 2 for row in upstream.values()))
        group_a, group_b = (pool['group_id'] for pool in config['pools'])
        if not args.team_only:
            sdk_cases('stock_sub2api', 'http://127.0.0.1:28082/v1', upstream[2]['keys'][group_a])
            other_pool = openai.OpenAI(api_key=upstream[2]['keys'][group_b], base_url='http://127.0.0.1:28082/v1', max_retries=0, timeout=30)
            check_stream(other_pool, 'gpt-6.1-sol', 'Reply exactly E2E_SECOND_POOL', 'E2E_SECOND_POOL')
            check('stock_second_pool_inference', True)

        process = start('bootstrap')
        admin = client.API()
        admin.call('/api/setup', {'username': 'e2eadmin', 'password': password, 'confirmPassword': password,
            'SelfUseModeEnabled': False, 'DemoSiteEnabled': False})
        admin_profile = admin.login('e2eadmin', password)
        sessions.append(admin)
        for key, value in {'RetryTimes': '0', 'quota_setting.trust_quota_usd': '0',
            'ModelRatio': json.dumps({'gpt-6.1-sol': 1}), 'CompletionRatio': json.dumps({'gpt-6.1-sol': 1})}.items():
            admin.call('/api/option/', {'key': key, 'value': value}, 'PUT')
        people = {}
        for name, expected_id in ((('alpha', 2), ('beta', 3), ('gamma', 4)) if args.team_only else (('alpha', 2), ('beta', 3))):
            account = client.API()
            admin.call('/api/user/', {'username': name, 'password': password, 'display_name': name, 'role': 1})
            profile = account.login(name, password)
            sessions.append(account)
            check(name + '_fresh_user_matches_reviewed_queue', profile['id'] == expected_id)
            admin.call('/api/user/manage', {'id': profile['id'], 'action': 'add_quota', 'mode': 'add', 'value': 5_000_000})
            account.call('/api/workspace/key', {})
            people[expected_id] = {'api': account, 'key': account.call('/api/workspace/key/reveal', {})['api_key']}
        if args.team_only:
            if not args.worker:
                raise RuntimeError('Team prewarming requires a worker executable')
            team = admin.call('/api/subscription/admin/users/2/team-provision', {'name': 'Actual stock Sub identity E2E', 'weekly_usd': 10})['team']
            owner = people[2]['api']
            for user_id in (3, 4):
                invite = owner.call('/api/workspace/team/invites', {})
                people[user_id]['api'].call('/api/workspace/team/join', {'code': invite['code']})
                owner.call('/api/workspace/team/members/' + str(user_id), {'allowance_usd': 2}, 'PATCH')
            for person in people.values():
                person['personal_key'] = person['key']
                person['key'] = person['api'].call('/api/workspace/key/reveal', {})['api_key']
            queue = json.loads((source / 'queue.json').read_text())
            queue['tasks'] = [t for t in queue['tasks'] if t['workspace_team_id'] == 0] + [
                {'realyu_user_id': uid, 'workspace_team_id': team['id'], 'channel_id': ch} for uid in (2, 3, 4) for ch in (1, 2)]
            (source / 'queue.json').write_text(json.dumps(queue), encoding='utf-8')
            job_env = {**env, 'REALYU_UPSTREAM_DRIVER': 'sub2api', 'REALYU_SUB2API_BINDINGS_FILE': str(source / 'bindings.json')}
            result = subprocess.run([str(args.worker.resolve()), '--queue', str(source / 'queue.json'), '--state', str(source / 'progress.json')],
                cwd=run, env=job_env, capture_output=True, timeout=45, creationflags=FLAGS)
            (run / 'team-prewarm.stdout').write_bytes(result.stdout)
            (run / 'team-prewarm.stderr').write_bytes(result.stderr)
            check('actual_stock_team_subject_prewarming_completed', result.returncode == 0)
            team_ids = []
            for user_id in (2, 3, 4):
                payload = '\0'.join((config['namespace'], 'email', str(user_id), '0', 'team:' + str(team['id']))).encode()
                email = hmac.new(config['identity_secret'].encode(), payload, hashlib.sha256).hexdigest()[:40] + '@realyu.invalid'
                users = management('/admin/users?search=' + urllib.parse.quote(email) + '&page_size=100')['items']
                team_ids.append(next(row['id'] for row in users if row['email'] == email))
            check('stock_team_members_and_personal_scopes_have_distinct_internal_users', len(set(team_ids)) == 3
                and not set(team_ids).intersection(row['id'] for row in upstream.values()))
            report['team_upstream_user_ids'] = team_ids
            report['workspace_team_id'] = team['id']
        for index, pool in enumerate(config['pools']):
            boot = {'candidate_url': client.BASE, 'expected_version': 'realyu-sub2api-stock-chain',
                'admin_access_token': 'fixture-adapter', 'admin_user_id': admin_profile['id'],
                'route': {'name': 'sub2api-stock-' + str(index), 'base_url': pool['base_url'],
                    'models': 'mock-secondary' if args.team_only and index else 'gpt-6.1-sol',
                    'group': 'default', 'sub2api_group_id': pool['group_id']}}
            receipt = bootstrap(boot, admin)
            check('channel_' + str(index) + '_matches_reviewed_queue', receipt['channel_id'] == pool['channel_id'])
        stop(process)
        env.update(REALYU_UPSTREAM_DRIVER='sub2api', REALYU_SUB2API_BINDINGS_FILE=str(source / 'bindings.json'),
            REALYU_SUB2API_ADMIN_URL='http://127.0.0.1:28082')
        process = start('stock-mode')
        initial = ledger()
        first = sdk_cases('realyu_alpha', client.BASE + '/v1', people[2]['key'])
        sdk_cases('realyu_beta', client.BASE + '/v1', people[3]['key'])
        if args.team_only:
            sdk_cases('realyu_gamma', client.BASE + '/v1', people[4]['key'])
        after = settled_ledger(initial['consume'][0] + (9 if args.team_only else 6))
        cost = after['consume'][1] - initial['consume'][1]
        wallet_delta = sum(u[1] for u in initial['users']) - sum(u[1] for u in after['users'])
        key_delta = sum(t[2] for t in after['tokens']) - sum(t[2] for t in initial['tokens'])
        check('successful_gateway_requests_each_settle_once', after['consume'][0] - initial['consume'][0] == (9 if args.team_only else 6))
        if args.team_only:
            subscription_delta = sum(row[1] for row in after['subscriptions']) - sum(row[1] for row in initial['subscriptions'])
            check('shared_payer_subscription_key_and_ledger_reconcile_exactly', cost > 0 and subscription_delta == key_delta == cost and wallet_delta == 0)
            with sqlite3.connect(database) as db:
                attribution = db.execute('select distinct l.user_id,t.workspace_user_id from logs l join tokens t on l.token_id=t.id where l.type=2 order by t.workspace_user_id').fetchall()
            check('team_bills_preserve_payer_and_individual_member_attribution', attribution == [(2, 2), (2, 3), (2, 4)])
            marker = 'E2E_OWNER_' + secrets.token_hex(8)
            prior = first.responses.create(model='gpt-6.1-sol', input='Reply exactly ' + marker, store=True)
            same = first.responses.create(model='gpt-6.1-sol', previous_response_id=prior.id, input='Reply exactly E2E_SAME_OWNER')
            check('same_team_subject_can_continue_owned_http_response', same.output_text.strip() == 'E2E_SAME_OWNER')
            before_cross = settled_ledger(after['consume'][0] + 2)
            before_calls = get_json('http://127.0.0.1:28084/observations')['calls']
            for label, key in (('different_member', people[3]['key']), ('same_member_personal_scope', people[2]['personal_key'])):
                response = client.API(key).request('/v1/responses', {'model': 'gpt-6.1-sol', 'previous_response_id': prior.id,
                    'input': 'Repeat the retained secret', 'stream': False})
                check(label + '_cannot_continue_team_response', response[0] in (400, 403, 404) and marker not in response[3].decode(errors='replace'))
            for _ in range(20):
                if ledger() == before_cross:
                    break
                time.sleep(.1)
            check('cross_subject_denials_do_not_execute_upstream_or_charge', ledger() == before_cross
                and get_json('http://127.0.0.1:28084/observations')['calls'] == before_calls)
            before_parallel = ledger()

            def independent_request(user_id):
                marker = 'E2E_PARALLEL_' + str(user_id) + '_' + secrets.token_hex(6)
                sdk = openai.OpenAI(api_key=people[user_id]['key'], base_url=client.BASE + '/v1', max_retries=0, timeout=30)
                try:
                    response = sdk.responses.create(model='gpt-6.1-sol', input='Reply exactly ' + marker)
                    return response.status == 'completed' and response.output_text.strip() == marker
                finally:
                    sdk.close()

            # Only synthetic model calls: two bounded waves, three distinct
            # customer subjects. No retries or pacing between either wave.
            with ThreadPoolExecutor(max_workers=3) as executor:
                outcomes = list(executor.map(independent_request, (2, 3, 4, 2, 3, 4)))
            check('three_subject_concurrency_returns_six_isolated_finals', all(outcomes) and len(outcomes) == 6)
            after_parallel = settled_ledger(before_parallel['consume'][0] + 6)
            parallel_cost = after_parallel['consume'][1] - before_parallel['consume'][1]
            check('concurrent_successes_settle_once_without_wallet_fallback', after_parallel['consume'][0] - before_parallel['consume'][0] == 6
                and parallel_cost == 660
                and sum(t[2] for t in after_parallel['tokens']) - sum(t[2] for t in before_parallel['tokens']) == parallel_cost
                and sum(s[1] for s in after_parallel['subscriptions']) - sum(s[1] for s in before_parallel['subscriptions']) == parallel_cost
                and sum(u[1] for u in after_parallel['users']) == sum(u[1] for u in before_parallel['users']))
        else:
            check('gateway_wallet_key_and_ledger_reconcile_exactly', cost > 0 and wallet_delta == key_delta == cost)
        report['gateway_charge_quota'] = cost
        snapshot = ledger()
        stop(process)
        process = start('cache-reset')
        check('gateway_restart_retains_balances_and_keys', ledger() == snapshot and
            people[2]['api'].call('/api/workspace/key/reveal', {})['api_key'] == people[2]['key'])
        marker = 'E2E_RESTART_' + secrets.token_hex(5)
        check_stream(first, 'gpt-6.1-sol', 'Reply exactly ' + marker, marker)
        check('gateway_restart_recovers_prewarmed_identity', True)
        with sqlite3.connect(database) as db:
            check('sqlite_integrity', db.execute('pragma integrity_check').fetchone()[0] == 'ok')
        report['all_passed'] = all(report['checks'].values())
    except Exception as exc:
        # Raw errors can include internal keys; preserve detail only privately.
        (run / 'failure.txt').write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
        report['failure'] = {'type': type(exc).__name__, 'after_check': next(reversed(report['checks']), 'startup')}
    finally:
        for session in sessions:
            session.logout()
        for process in reversed(processes):
            stop(process)
        report['owned_processes_stopped'] = all(p.poll() is not None for p in processes)
        save()
        print(json.dumps({'report': str(run / 'summary.json'), 'all_passed': report['all_passed'], 'failure': report.get('failure')}), flush=True)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())

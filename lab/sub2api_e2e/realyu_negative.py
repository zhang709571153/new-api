"""Actual candidate HTTP negative paths against the isolated stock Sub2API.

Fresh SQLite and suite-owned users only. This proves rejection/accounting
boundaries, not successful Sub2API inference or production acceptance.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
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
from urllib.parse import urlsplit

from bootstrap_windows import PRIVATE, ROOT, get_json

sys.path.insert(0, str(ROOT / 'lab'))
import verify_provider_release as client
sys.path.insert(0, str(ROOT / 'deploy/sub2api'))
from bootstrap_route import bootstrap, Client as BootstrapClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--port', type=int, default=28500)
    parser.add_argument('--bootstrap-only', action='store_true')
    parser.add_argument('--team-scope', action='store_true', help='Target shared-payer team member rejection and refund paths')
    parser.add_argument('--prewarmed-control-url', help='Explicit loopback fake management fixture, not stock Sub2API')
    args = parser.parse_args()
    binary = args.binary.resolve(strict=True)
    sub_base_url = 'http://127.0.0.1:28082'
    if args.prewarmed_control_url:
        target = urlsplit(args.prewarmed_control_url)
        if (target.scheme != 'http' or target.hostname != '127.0.0.1' or not target.port or target.port < 1024
                or target.port in (18300, 18301, 28082) or target.path or target.query or target.fragment
                or target.username or target.password):
            raise RuntimeError('Use an explicitly isolated loopback fake management fixture')
        sub_base_url = args.prewarmed_control_url
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise RuntimeError('Candidate port is occupied')
    client.BASE = f'http://127.0.0.1:{args.port}'
    run = PRIVATE / ('negative-' + secrets.token_hex(5))
    run.mkdir(parents=True)
    config_file = run / 'sub2api-bindings.json'
    database = run / 'new-api.db'
    report = {'scope': 'actual candidate rejection and accounting paths', 'all_passed': False, 'checks': {},
        'candidate_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'production_changed': False,
        'sub2api_successful_inference': False, 'real_upstream_e2e_performed': False,
        'actual_sub2api_management_rejection': '401 without a configured system key; separately observed admin JWT guard is 423'}
    if args.prewarmed_control_url:
        report.pop('actual_sub2api_management_rejection')
        report['management_backend'] = 'loopback protocol fixture, not stock Sub2API'
        report['prewarmed_http_scope'] = 'actual RealYu HTTP and ledger with fake management reads; no successful inference'
    password = secrets.token_urlsafe(24)
    secret = secrets.token_hex(32)
    env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower() and k.upper() not in
           ('SQL_DSN', 'LOG_SQL_DSN', 'REDIS_CONN_STRING', 'REALYU_UPSTREAM_DRIVER', 'REALYU_SUB2API_BINDINGS_FILE')}
    env.update(PORT=str(args.port), BIND_ADDRESS='127.0.0.1', SQLITE_PATH=str(database),
        SESSION_SECRET=secret, CRYPTO_SECRET=secret, GIN_MODE='release', SESSION_COOKIE_SECURE='false',
        BATCH_UPDATE_ENABLED='false', NODE_TYPE='master', RELAY_USER_CONCURRENCY='10', VERSION='realyu-sub2api-e2e-local')
    processes = []
    sessions = []

    def save():
        (run / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')

    def check(name, value):
        report['checks'][name] = bool(value)
        save()
        print(json.dumps({'check': name, 'passed': bool(value)}), flush=True)
        if not value:
            raise AssertionError(name)

    def start(label):
        with (run / (label + '.log')).open('ab') as log:
            process = subprocess.Popen([str(binary), '--log-dir', str(run / 'logs')], cwd=run, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        processes.append(process)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError('Candidate startup failed; inspect private log')
            try:
                client.API().call('/api/setup')
                return process
            except Exception:
                time.sleep(0.2)
        raise RuntimeError('Candidate startup timed out')

    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20)

    def ledger():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            return {'users': db.execute('select id,quota,used_quota,request_count from users order by id').fetchall(),
                'tokens': db.execute('select id,remain_quota,used_quota from tokens order by id').fetchall(),
                'consume': db.execute('select count(*),coalesce(sum(quota),0) from logs where type=2').fetchone(),
                'subscriptions': db.execute('select id,amount_used from user_subscriptions order by id').fetchall(),
                'team_wallets': db.execute('select team_id,quota from workspace_team_accounts order by team_id').fetchall(),
                'nonzero_member_weekly_usage': db.execute('select team_id,user_id,subscription_id,weekly_reset_at,used_quota from workspace_member_weekly_usages where used_quota != 0 order by team_id,user_id,subscription_id,weekly_reset_at').fetchall(),
                'pending_subscription_reservations': db.execute("select count(*) from subscription_pre_consume_records where status='consumed'").fetchone()}

    def unchanged(before):
        # Wait for any asynchronous refund, then compare every relevant counter.
        for _ in range(30):
            if ledger() == before:
                return True
            time.sleep(0.1)
        return False

    try:
        process = start('fixture-bootstrap')
        admin = client.API()
        admin.call('/api/setup', {'username': 'e2eadmin', 'password': password, 'confirmPassword': password,
            'SelfUseModeEnabled': False, 'DemoSiteEnabled': False})
        admin_profile = admin.login('e2eadmin', password)
        sessions.append(admin)
        for key, value in {'RetryTimes': '0', 'quota_setting.trust_quota_usd': '0',
            'ModelRatio': json.dumps({'gpt-6.1-sol': 1}), 'CompletionRatio': json.dumps({'gpt-6.1-sol': 1})}.items():
            admin.call('/api/option/', {'key': key, 'value': value}, 'PUT')
        people = {}
        for name in (('owner', 'alpha', 'beta') if args.team_scope else ('alpha', 'beta')):
            account = client.API()
            admin.call('/api/user/', {'username': 'e2e_' + name, 'password': password, 'display_name': name, 'role': 1})
            profile = account.login('e2e_' + name, password)
            sessions.append(account)
            admin.call('/api/user/manage', {'id': profile['id'], 'action': 'add_quota', 'mode': 'add', 'value': 5_000_000})
            account.call('/api/workspace/key', {})
            people[name] = {'id': profile['id'], 'client': account,
                'key': account.call('/api/workspace/key/reveal', {})['api_key']}
        check('fresh_users_have_distinct_personal_keys', people['alpha']['key'] != people['beta']['key'])
        if args.team_scope:
            owner = people['owner']['client']
            # Current business rules provision teams through an administrator
            # service-credit grant. This route creates no payment or wallet move.
            team = admin.call('/api/subscription/admin/users/' + str(people['owner']['id']) + '/team-provision',
                {'name': 'Isolated Sub2API subject acceptance', 'weekly_usd': 10})['team']
            for name in ('alpha', 'beta'):
                invite = owner.call('/api/workspace/team/invites', {})
                people[name]['client'].call('/api/workspace/team/join', {'code': invite['code']})
                owner.call('/api/workspace/team/members/' + str(people[name]['id']), {'allowance_usd': 1.5}, 'PATCH')
            for person in people.values():
                person['key'] = person['client'].call('/api/workspace/key/reveal', {})['api_key']
                person['token_id'] = person['client'].call('/api/workspace')['api_key']['id']
            check('team_members_have_distinct_keys', len({p['key'] for p in people.values()}) == len(people))
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
                identities = {name: db.execute('select t.user_id,t.workspace_user_id,m.team_id from tokens t join workspace_members m on m.token_id=t.id where t.id=?',
                    (person['token_id'],)).fetchone() for name, person in people.items()}
            check('team_fixture_has_shared_billing_owner_and_distinct_members',
                all(row == (people['owner']['id'], people[name]['id'], team['id']) for name, row in identities.items()))
            report['subject_fixture'] = {'team_id': team['id'], 'billing_owner_id': people['owner']['id'],
                'authenticated_member_ids': {name: p['id'] for name, p in people.items()},
                'successful_upstream_identity_projection_verified': False}
            report['scope'] = 'actual candidate shared-payer team rejection and accounting paths; successful upstream identity projection not verified'
        bootstrap_config = {'candidate_url': client.BASE, 'expected_version': 'realyu-sub2api-e2e-local',
            'admin_access_token': 'adapter-uses-existing-authenticated-fixture-session', 'admin_user_id': admin_profile['id'],
            'route': {'name': 'sub2api-isolated-route', 'base_url': sub_base_url,
                      'models': 'gpt-6.1-sol', 'group': 'default', 'sub2api_group_id': 1}}
        created = bootstrap(bootstrap_config, admin)
        repeated = bootstrap(bootstrap_config, admin)
        check('deployment_bootstrap_real_api_creates_route', created['created'] is True)
        check('deployment_bootstrap_idempotent_readback', repeated['created'] is False and created['channel_id'] == repeated['channel_id'])
        def management_token_action(scope, method):
            # Use the ordinary dashboard password-proof flow for this disposable
            # fixture, rather than seeding a token or bypassing sensitive auth.
            proof = admin.call('/api/verify', {'scope': scope, 'method': 'password', 'password': password})
            status, value, _, _ = admin.request('/api/user/token', {}, method,
                {'X-Security-Proof': proof['proof_token']})
            if status != 200 or not isinstance(value, dict) or value.get('success') is not True:
                raise RuntimeError('Fixture access-token operation failed: HTTP ' + str(status))
            return value.get('data')

        management_token = management_token_action('access_token.generate', 'POST')
        try:
            bootstrap_config['admin_access_token'] = management_token
            standalone = bootstrap(bootstrap_config, BootstrapClient(bootstrap_config))
            check('deployment_bootstrap_standalone_bearer_transport', standalone['created'] is False and standalone['channel_id'] == created['channel_id'])
        finally:
            management_token_action('access_token.revoke', 'DELETE')
        check('fixture_admin_access_token_revoked', admin.call('/api/user/token/status').get('exists') is False)
        report['deployment_bootstrap_auth_scope'] = 'fixture session adapter and standalone Client bearer transport both exercised; fixture token revoked'
        if args.bootstrap_only:
            report['scope'] = 'actual deployment bootstrap API and standalone bearer transport'
            report['all_passed'] = all(report['checks'].values())
            return 0
        channel_id = created['channel_id']
        # Higher-priority legacy route must never be selected after driver switch.
        admin.call('/api/channel/', {'mode': 'single', 'channel': {'type': 1, 'name': 'legacy-decoy',
            'key': 'mock-only-legacy-decoy', 'base_url': 'http://127.0.0.1:28083',
            'models': 'gpt-6.1-sol', 'group': 'default', 'status': 1, 'priority': 999, 'weight': 1}})
        stop(process)
        env.update(REALYU_UPSTREAM_DRIVER='sub2api', REALYU_SUB2API_BINDINGS_FILE=str(config_file),
                   REALYU_SUB2API_ADMIN_URL=sub_base_url)
        process = start('sub2api-mode')
        status = admin.call('/api/channel/sub2api/status')
        check('sub2api_status_enabled_unconfigured', status['enabled'] is True and status['configured'] is False)
        for label, path, body, method in (
            ('legacy_list', '/api/channel/', None, 'GET'),
            ('legacy_create', '/api/channel/', {'mode': 'single', 'channel': {}}, 'POST'),
            ('legacy_key', f'/api/channel/{channel_id}/key', {}, 'POST'),
            ('legacy_refresh', f'/api/channel/{channel_id}/codex/refresh', {}, 'POST')):
            response = admin.request(path, body, method)
            check(label + '_denied_410', response[0] == 410 and response[1].get('code') == 'sub2api_managed')
        response = people['alpha']['client'].request('/api/channel/sub2api/status')
        check('ordinary_customer_cannot_read_management_status', response[0] in (401, 403) or response[1].get('success') is False)
        original_calls = get_json('http://127.0.0.1:28083/observations')['calls']
        configurations = [('missing', None), ('malformed', '{bad-json'), ('unbound', {
            'version': 1, 'namespace': 'isolated-negative', 'identity_secret': secret, 'admin_api_key': 'deliberately-invalid-test-management-key',
            'pools': [{'channel_id': channel_id, 'base_url': sub_base_url, 'group_id': 1}],
            'provision': {'enabled': False, 'initial_balance': 100, 'concurrency': 2}, 'bindings': []})]
        configurations.append(('actual_sub2api_management_401', {**configurations[-1][1], 'provision': {'enabled': True, 'initial_balance': 100, 'concurrency': 2}}))
        if args.team_scope:
            # Config parsing behavior is covered by the previous full run. These
            # selected cases exercise the new subject path without repeating it.
            configurations = [configurations[0], configurations[-1]]
        if args.prewarmed_control_url:
            cfg = dict(configurations[-1][1])
            cfg['admin_api_key'] = 'fixture-admin'
            cfg['provision'] = {**cfg['provision'], 'mode': 'prewarmed'}
            configurations = [('prewarmed_missing_identity', cfg)]
        for label, cfg in configurations:
            if cfg is not None:
                config_file.write_text(cfg if isinstance(cfg, str) else json.dumps(cfg), encoding='utf-8')
            before = ledger()
            for name, person in people.items():
                api = client.API(person['key'])
                response = api.request('/v1/responses', {'model': 'gpt-6.1-sol', 'stream': True, 'input': 'Reply exactly E2E_NEGATIVE', 'max_output_tokens': 20})
                raw = response[3].decode(errors='replace')
                check(label + '_' + name + '_safe_503', response[0] == 503 and 'deliberately-invalid-test-management-key' not in raw
                    and 'fixture-admin' not in raw and 'fixture-sensitive-message' not in raw and secret not in raw)
                models = api.request('/v1/models?client_version=0.160.0')
                check(label + '_' + name + '_codex_catalog_fails_closed', models[0] == 503)
            check(label + '_no_charge_or_reservation', unchanged(before))
            check(label + '_legacy_decoy_not_called', get_json('http://127.0.0.1:28083/observations')['calls'] == original_calls)
        if args.team_scope:
            before = ledger()
            def concurrent_rejection(person):
                result = client.API(person['key']).request('/v1/responses',
                    {'model': 'gpt-6.1-sol', 'stream': True, 'input': 'E2E_CONCURRENT_NEGATIVE', 'max_output_tokens': 20})
                return result[0]
            with ThreadPoolExecutor(max_workers=3) as workers:
                statuses = list(workers.map(concurrent_rejection, people.values()))
            check('concurrent_shared_payer_failures_safe_503', statuses == [503] * len(people))
            check('concurrent_shared_payer_failures_preserve_all_ledgers', unchanged(before))
            old_member_key = people['alpha']['key']
            people['alpha']['client'].call('/api/workspace/team/leave', {'team_id': team['id']})
            people['alpha']['client'].call('/api/workspace/key', {})
            personal_key = people['alpha']['client'].call('/api/workspace/key/reveal', {})['api_key']
            check('former_team_key_denied_after_leave', client.API(old_member_key).denied('/v1/responses',
                {'model': 'gpt-6.1-sol', 'stream': True, 'input': 'E2E_NEGATIVE', 'max_output_tokens': 20}))
            personal_token_id = people['alpha']['client'].call('/api/workspace')['api_key']['id']
            with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
                personal_subject = db.execute('select user_id,workspace_user_id from tokens where id=?', (personal_token_id,)).fetchone()
                active_membership = db.execute('select count(*) from workspace_members where user_id=?', (people['alpha']['id'],)).fetchone()[0]
            check('leaving_team_restores_personal_subject', personal_subject == (people['alpha']['id'], 0) and active_membership == 0)
            before_personal = ledger()
            response = client.API(personal_key).request('/v1/responses',
                {'model': 'gpt-6.1-sol', 'stream': True, 'input': 'E2E_NEGATIVE', 'max_output_tokens': 20})
            check('restored_personal_subject_safe_503', response[0] == 503)
            check('restored_personal_subject_failure_not_charged', unchanged(before_personal))
            check('team_lifecycle_and_failures_no_consume_logs', ledger()['consume'] == before['consume'])
            people['alpha']['key'] = personal_key
        snapshot = ledger()
        stop(process)
        process = start('restart-state')
        check('restart_preserves_wallet_key_and_usage', ledger() == snapshot and people['alpha']['client'].call('/api/workspace/key/reveal', {})['api_key'] == people['alpha']['key'])
        with sqlite3.connect(database) as db:
            check('sqlite_integrity', db.execute('pragma integrity_check').fetchone()[0] == 'ok')
        report['all_passed'] = all(report['checks'].values())
    except Exception as exc:
        report['failure'] = (type(exc).__name__ + ': ' + str(exc)).replace(password, '[REDACTED]').replace(secret, '[REDACTED]')[:600]
    finally:
        for api in sessions:
            api.logout()
        if database.exists():
            with sqlite3.connect(database) as db:
                if db.execute("select count(*) from sqlite_master where type='table' and name='user_sessions'").fetchone()[0]:
                    report['remaining_isolated_sessions'] = db.execute('select count(*) from user_sessions where status=1').fetchone()[0]
        # Failed logout can leave a session in this disposable, stopped local DB;
        # report that fact rather than treating response-body success as cleanup.
        for process in reversed(processes):
            stop(process)
        report['owned_processes_stopped'] = all(process.poll() is not None for process in processes)
        save()
        print(json.dumps({'report': str(run / 'summary.json'), 'all_passed': report['all_passed'], 'failure': report.get('failure')}, indent=2))
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())

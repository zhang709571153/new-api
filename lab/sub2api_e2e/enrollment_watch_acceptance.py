"""Actual loopback RealYu scanner -> durable watcher -> stock Sub2API acceptance.

Only the model upstream is synthetic. All customer data is freshly created via
normal APIs; queue files must be published by the running gateway itself.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

import openai

from bootstrap_windows import ROOT, PRIVATE, FLAGS, get_json

sys.path.insert(0, str(ROOT / 'lab'))
import verify_provider_release as client
sys.path.insert(0, str(ROOT / 'deploy/sub2api'))
from bootstrap_route import bootstrap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gateway', required=True, type=Path)
    parser.add_argument('--worker', required=True, type=Path)
    parser.add_argument('--port', type=int, default=28503)
    args = parser.parse_args()
    if args.port < 1024 or args.port in (18300, 18301):
        raise RuntimeError('Only an isolated local port is allowed')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise RuntimeError('Candidate port occupied')
    run = PRIVATE / ('enrollment-watch-' + secrets.token_hex(5))
    run.mkdir()
    binary = args.gateway.resolve(strict=True)
    worker = run / 'worker.exe'
    shutil.copyfile(args.worker.resolve(strict=True), worker)
    sub = json.loads(Path(json.loads((PRIVATE / 'current.json').read_text())['credentials_file']).read_text(encoding='utf-8-sig'))
    password, secret = secrets.token_urlsafe(24), secrets.token_hex(32)
    config = {'version': 1, 'namespace': run.name, 'identity_secret': secret,
        'admin_api_key': sub['admin_api_key'], 'pools': [{'channel_id': 1, 'base_url': 'http://127.0.0.1:28082', 'group_id': 2}],
        'provision': {'enabled': True, 'mode': 'prewarmed', 'initial_balance': 100, 'concurrency': 3}, 'bindings': []}
    bindings = run / 'bindings.json'
    bindings.write_text(json.dumps(config), encoding='utf-8')
    digest = hashlib.sha256(bindings.read_bytes()).hexdigest()
    queue, state = run / 'queue', run / 'state'
    queue.mkdir(); state.mkdir()
    database = run / 'new-api.db'
    dsn = str(database) + '?_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate'
    env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower() and not k.startswith('REALYU_SUB2API')
        and k not in ('SQL_DSN', 'LOG_SQL_DSN', 'REDIS_CONN_STRING', 'REALYU_UPSTREAM_DRIVER')}
    env.update(PORT=str(args.port), BIND_ADDRESS='127.0.0.1', SQLITE_PATH=dsn, SESSION_SECRET=secret,
        CRYPTO_SECRET=secret, GIN_MODE='release', SESSION_COOKIE_SECURE='false', BATCH_UPDATE_ENABLED='false',
        NODE_TYPE='master', RELAY_USER_CONCURRENCY='10', VERSION='realyu-sub2api-enrollment-e2e')
    client.BASE = 'http://127.0.0.1:' + str(args.port)
    report = {'run_id': run.name, 'production_changed': False, 'actual_model_upstream': False,
        'scope': 'actual gateway scanner and worker processes with stock Sub2API and mocked model upstream',
        'gateway_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(), 'config_sha256': digest,
        'checks': {}, 'all_passed': False}
    processes, sessions, process_receipts = [], [], []

    def save():
        (run / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    def check(name, passed):
        report['checks'][name] = bool(passed)
        save()
        print(json.dumps({'check': name, 'passed': bool(passed)}), flush=True)
        if not passed:
            raise AssertionError(name)

    def wait_until(predicate, seconds=45):
        deadline = time.monotonic() + seconds
        while True:
            result = predicate()
            if result or time.monotonic() >= deadline:
                return result
            time.sleep(.2)

    def stop(process):
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=20)

    def launch(exe, argv, name):
        with (run / (name + '.log')).open('ab') as log:
            p = subprocess.Popen([str(exe), *argv], cwd=run, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
        processes.append(p)
        process_receipts.append({'pid': p.pid, 'executable': str(exe), 'name': name})
        (run / 'processes.json').write_text(json.dumps(process_receipts, indent=2), encoding='utf-8')
        return p

    def gateway(name):
        p = launch(binary, ['--log-dir', str(run / 'logs')], name)
        def healthy():
            if p.poll() is not None:
                raise RuntimeError('Gateway exited; inspect private log')
            try:
                client.API().call('/api/setup')
                return True
            except Exception:
                return False
        if not wait_until(healthy, 20):
            raise RuntimeError('Gateway startup timeout')
        return p

    def ledger():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            return {'wallets': db.execute('select id,quota,used_quota from users order by id').fetchall(),
                'tokens': db.execute('select id,used_quota from tokens order by id').fetchall(),
                'consume': db.execute('select count(*),coalesce(sum(quota),0) from logs where type=2').fetchone(),
                'subscriptions': db.execute('select id,amount_used from user_subscriptions order by id').fetchall()}

    def remote_users():
        req = urllib.request.Request('http://127.0.0.1:28082/api/v1/admin/users?page_size=100',
            headers={'x-api-key': config['admin_api_key']})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), client.NoRedirect())
        with opener.open(req, timeout=20) as response:
            rows = json.load(response)['data']['items']
        return sorted(row['id'] for row in rows if str(row.get('notes', '')).startswith('realyu-projection:' + run.name + ':'))

    def progress():
        try:
            return json.loads((state / (digest + '.json')).read_text())
        except (FileNotFoundError, json.JSONDecodeError, PermissionError):
            return {}

    def tasks_done(count):
        tasks = progress().get('tasks', {})
        return len(tasks) == count and all(row['status'] == 'DONE' for row in tasks.values())

    def jobs():
        return {p.stem for p in (queue / digest).glob('*.json')}

    def status(actor, scope='current'):
        return actor.call('/api/workspace/sub2api/status?scope=' + scope)

    try:
        p = gateway('bootstrap')
        admin = client.API()
        admin.call('/api/setup', {'username': 'e2ewatch', 'password': password, 'confirmPassword': password,
            'SelfUseModeEnabled': False, 'DemoSiteEnabled': False})
        admin_id = admin.login('e2ewatch', password)['id']; sessions.append(admin)
        for key, value in {'RetryTimes': '0', 'quota_setting.trust_quota_usd': '0',
            'RegisterEnabled': 'true', 'PasswordRegisterEnabled': 'true', 'EmailVerificationEnabled': 'false',
            'ModelRatio': json.dumps({'gpt-6.1-sol': 1}), 'CompletionRatio': json.dumps({'gpt-6.1-sol': 1})}.items():
            admin.call('/api/option/', {'key': key, 'value': value}, 'PUT')
        people = {}
        for name in ('watchowner', 'watchmember'):
            admin.call('/api/user/', {'username': name, 'password': password, 'display_name': name, 'role': 1})
            actor = client.API(); uid = actor.login(name, password)['id']; sessions.append(actor)
            admin.call('/api/user/manage', {'id': uid, 'action': 'add_quota', 'mode': 'add', 'value': 5_000_000})
            actor.call('/api/workspace/key', {})
            people[uid] = actor
        team = admin.call('/api/subscription/admin/users/2/team-provision', {'name': 'Automatic enrollment E2E', 'weekly_usd': 10})['team']
        invite = people[2].call('/api/workspace/team/invites', {})
        people[3].call('/api/workspace/team/join', {'code': invite['code']})
        people[2].call('/api/workspace/team/members/3', {'allowance_usd': 2}, 'PATCH')
        key = people[3].call('/api/workspace/key/reveal', {})['api_key']
        bootstrap({'candidate_url': client.BASE, 'expected_version': 'realyu-sub2api-enrollment-e2e',
            'admin_access_token': 'fixture-adapter', 'admin_user_id': admin_id,
            'route': {'name': 'sub2api-auto-enrollment', 'base_url': 'http://127.0.0.1:28082',
                'models': 'gpt-6.1-sol', 'group': 'default', 'sub2api_group_id': 2}}, admin)
        stop(p)
        env.update(REALYU_UPSTREAM_DRIVER='sub2api', REALYU_SUB2API_BINDINGS_FILE=str(bindings),
            REALYU_SUB2API_QUEUE_DIR=str(queue), REALYU_SUB2API_STATE_DIR=str(state))
        p = gateway('enrollment-enabled')
        expected = {'1-0-1', '2-0-1', '3-0-1', '2-' + str(team['id']) + '-1', '3-' + str(team['id']) + '-1'}
        check('scanner_enqueues_personal_and_team_without_status_requests', wait_until(lambda: jobs() == expected))
        check('web_does_not_provision_or_write_worker_progress', remote_users() == [] and not progress())
        initial = ledger()
        before_calls = get_json('http://127.0.0.1:28084/observations')['calls']
        rejected = client.API(key).request('/v1/responses', {'model': 'gpt-6.1-sol', 'input': 'Reply exactly E2E_NOT_READY'})
        check('unready_inference_fails_safely_without_charge_or_upstream', rejected[0] == 503 and ledger() == initial
            and remote_users() == [] and get_json('http://127.0.0.1:28084/observations')['calls'] == before_calls)
        missing = status(people[3])
        check('missing_worker_is_visible_to_customer', missing['reason'] == 'worker_unavailable' and missing['pending_count'] == 1)
        w = launch(worker, ['--watch'], 'worker-first')
        check('worker_publishes_heartbeat', wait_until(lambda: (state / 'heartbeat.json').exists(), 10))
        active = status(people[3])
        check('customer_sees_pending_or_ready_with_worker_timestamp', active['status'] in ('pending', 'ready') and bool(active.get('worker_last_seen')))
        check('watcher_projects_all_five_scopes_into_actual_sub', wait_until(lambda: tasks_done(5), 55))
        identities = remote_users()
        check('five_scopes_have_distinct_internal_users', len(identities) == 5 and len(set(identities)) == 5)
        check('worker_provisioning_never_changes_customer_ledger', ledger() == initial)
        check('personal_and_team_status_are_independently_ready', all(status(people[uid], scope)['status'] == 'ready'
            for uid in (2, 3) for scope in ('personal', 'team')))
        sdk = openai.OpenAI(api_key=key, base_url=client.BASE + '/v1', max_retries=0, timeout=30)
        response = sdk.responses.create(model='gpt-6.1-sol', input='Reply exactly E2E_AUTO_READY')
        check('automatically_enrolled_member_can_infer', response.status == 'completed' and response.output_text.strip() == 'E2E_AUTO_READY')
        check('successful_member_inference_settles_once', wait_until(lambda: ledger()['consume'][0] == 1, 5))
        billed = ledger()
        check('automatically_enrolled_member_charges_team_subscription', billed['subscriptions'][0][1] == billed['consume'][1] == 110
            and sum(x[1] for x in billed['wallets']) == sum(x[1] for x in initial['wallets']))
        # Create a real account while the scanner and worker are already running.
        newcomer = client.API()
        newcomer.call('/api/user/register', {'username': 'watchregistered', 'password': password})
        uid = newcomer.login('watchregistered', password)['id']; sessions.append(newcomer)
        check('registration_succeeds_without_manual_upstream_action', uid == 4)
        check('background_scan_discovers_new_registration_without_status_poll', wait_until(lambda: '4-0-1' in jobs(), 40))
        check('watcher_projects_new_registration', wait_until(lambda: tasks_done(6), 20))
        invite = people[2].call('/api/workspace/team/invites', {})
        newcomer.call('/api/workspace/team/join', {'code': invite['code']})
        people[2].call('/api/workspace/team/members/4', {'allowance_usd': 2}, 'PATCH')
        check('background_scan_discovers_joined_member_without_status_poll', wait_until(lambda: '4-' + str(team['id']) + '-1' in jobs(), 40))
        check('watcher_projects_new_team_membership', wait_until(lambda: tasks_done(7), 20))
        check('new_member_personal_and_team_scopes_both_ready', status(newcomer, 'personal')['status'] == 'ready'
            and status(newcomer, 'team')['status'] == 'ready' and len(remote_users()) == 7)
        prior = progress(); ids = remote_users(); snapshot = ledger()
        stop(w)
        w = launch(worker, ['--watch'], 'worker-restart')
        check('watcher_restart_retains_done_progress', wait_until(lambda: progress() == prior, 10))
        # Observe at least one real five-second scan without pacing inference.
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if w.poll() is not None:
                raise RuntimeError('Restarted watcher exited')
            time.sleep(.2)
        check('watcher_restart_does_not_recreate_users_or_charge', progress() == prior and remote_users() == ids and ledger() == snapshot)
        stop(p); p = gateway('gateway-restart')
        check('gateway_restart_recovers_ready_status_and_published_jobs', status(newcomer)['status'] == 'ready'
            and jobs() == expected | {'4-0-1', '4-' + str(team['id']) + '-1'} and ledger() == snapshot)
        report['all_passed'] = all(report['checks'].values())
    except Exception as exc:
        (run / 'failure.txt').write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
        report['failure'] = {'type': type(exc).__name__, 'after_check': next(reversed(report['checks']), 'startup')}
    finally:
        for actor in sessions:
            actor.logout()
        for process in reversed(processes):
            stop(process)
        report['owned_processes_stopped'] = all(p.poll() is not None for p in processes)
        save()
        public = ROOT / 'lab/sub2api_e2e/enrollment-watch-summary.json'
        if public.exists():
            old = json.loads(public.read_text())
            public.with_name('enrollment-watch-' + old['run_id'] + '.json').write_text(json.dumps(old, indent=2), encoding='utf-8')
        public.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'all_passed': report['all_passed'], 'run_id': run.name, 'failure': report.get('failure')}), flush=True)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())

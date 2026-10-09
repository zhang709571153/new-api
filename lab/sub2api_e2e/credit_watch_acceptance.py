"""Stock Sub2API internal-credit worker acceptance without model calls.

Uses one fresh synthetic subject in two mock pools. The worker must add internal
credit once, retain an official audit trail and avoid repeating it on restart.
No RealYu customer database, wallet or payment API is used by this fixture.
"""
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

from bootstrap_windows import ROOT, PRIVATE, FLAGS, get_json


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', required=True, type=Path)
    args = parser.parse_args()
    run = PRIVATE / ('credit-watch-' + secrets.token_hex(5)); run.mkdir()
    worker = run / 'worker.exe'; shutil.copyfile(args.worker.resolve(strict=True), worker)
    credentials = json.loads(Path(json.loads((PRIVATE / 'current.json').read_text())['credentials_file']).read_text(encoding='utf-8-sig'))
    config = {'version': 1, 'namespace': run.name, 'identity_secret': secrets.token_hex(32),
        'admin_api_key': credentials['admin_api_key'], 'pools': [
            {'channel_id': 1, 'base_url': 'http://127.0.0.1:28082', 'group_id': 2},
            {'channel_id': 2, 'base_url': 'http://127.0.0.1:28082', 'group_id': 3}],
        'provision': {'enabled': True, 'mode': 'prewarmed', 'initial_balance': 1, 'concurrency': 3},
        'credit': {'enabled': True, 'low_watermark': 20, 'topup_amount': 100,
            'check_interval_seconds': 15, 'idempotency_window_seconds': 3600}, 'bindings': []}
    bindings = run / 'bindings.json'; bindings.write_text(json.dumps(config), encoding='utf-8')
    digest = hashlib.sha256(bindings.read_bytes()).hexdigest()
    queue, state = run / 'queue', run / 'state'; (queue / digest).mkdir(parents=True); state.mkdir()
    for channel in (1, 2):
        task = {'realyu_user_id': 7001, 'workspace_team_id': 0, 'channel_id': channel}
        (queue / digest / ('7001-0-' + str(channel) + '.json')).write_text(
            json.dumps({'version': 1, 'config_sha256': digest, 'task': task}), encoding='utf-8')
    env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower() and not k.startswith('REALYU_SUB2API')
        and k not in ('SQL_DSN', 'LOG_SQL_DSN', 'REDIS_CONN_STRING')}
    env.update(REALYU_UPSTREAM_DRIVER='sub2api', REALYU_SUB2API_BINDINGS_FILE=str(bindings),
        REALYU_SUB2API_QUEUE_DIR=str(queue), REALYU_SUB2API_STATE_DIR=str(state))
    report = {'run_id': run.name, 'scope': 'actual worker and stock Sub2API internal allowance; no RealYu database or model execution',
        'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(), 'sub2api_version': '0.2.15',
        'production_changed': False, 'customer_payment_or_wallet_mutation': False, 'checks': {}, 'all_passed': False}
    processes, receipts = [], []

    def check(name, condition):
        report['checks'][name] = bool(condition)
        print(json.dumps({'check': name, 'passed': bool(condition)}), flush=True)
        if not condition: raise AssertionError(name)

    def read(name):
        try: return json.loads((state / name).read_text(encoding='utf-8'))
        except (FileNotFoundError, json.JSONDecodeError, PermissionError): return {}

    def wait_until(predicate, seconds):
        deadline = time.monotonic() + seconds
        while True:
            answer = predicate()
            if answer or time.monotonic() >= deadline: return answer
            time.sleep(.2)

    def launch(name):
        with (run / (name + '.log')).open('ab') as log:
            p = subprocess.Popen([str(worker), '--watch'], cwd=run, env=env, stdin=subprocess.DEVNULL,
                stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
        processes.append(p); receipts.append({'pid': p.pid, 'executable': str(worker), 'name': name})
        (run / 'processes.json').write_text(json.dumps(receipts), encoding='utf-8')
        return p

    def stop(p):
        if p.poll() is None: p.terminate(); p.wait(timeout=20)

    def management(path):
        req = urllib.request.Request('http://127.0.0.1:28082/api/v1' + path, headers={'x-api-key': config['admin_api_key']})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as response:
            return json.load(response)['data']

    def user():
        payload = '\0'.join((config['namespace'], 'email', '7001', '0')).encode()
        email = hmac.new(config['identity_secret'].encode(), payload, hashlib.sha256).hexdigest()[:40] + '@realyu.invalid'
        rows = management('/admin/users?search=' + urllib.parse.quote(email))['items']
        return next(row for row in rows if row['email'] == email)

    before_calls = get_json('http://127.0.0.1:28084/observations')['calls']
    try:
        p = launch('first')
        def both_done():
            tasks = read(digest + '.json').get('tasks', {})
            return len(tasks) == 2 and all(row['status'] == 'DONE' for row in tasks.values())
        check('two_pool_projection_jobs_complete', wait_until(both_done, 45))
        check('one_credit_operation_confirmed', wait_until(lambda: any(row['status'] == 'CONFIRMED'
            for row in read('credit.json').get('operations', [])), 40))
        credit = read('credit.json'); operations = credit['operations']
        check('same_subject_multiple_pools_credit_deduplicated', len(operations) == 1)
        operation = operations[0]; upstream = user()
        keys = management('/admin/users/' + str(upstream['id']) + '/api-keys?page_size=100')['items']
        check('one_stock_user_retains_two_pool_keys', len(keys) == 2 and {row['group_id'] for row in keys} == {2, 3})
        check('additive_credit_preserves_initial_balance', upstream['balance'] == 101
            and operation['before_balance'] == 1 and operation['after_balance'] == 101 and operation['amount'] == 100)
        audit = management('/admin/users/' + str(upstream['id']) + '/balance-history?page_size=100')
        expected = 'realyu-internal-credit:' + run.name + ':' + operation['operation_id']
        records = [row for row in audit['items'] if row.get('notes') == expected]
        (run / 'official-audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')
        check('official_sub_audit_records_exact_worker_operation', len(records) == 1 and records[0].get('value') == 100)
        heartbeat = read('heartbeat.json')
        check('worker_credit_status_is_visible', heartbeat.get('credit_status') in ('ready', 'checking'))
        stop(p); p = launch('restart')
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if p.poll() is not None: raise RuntimeError('Restarted worker exited')
            time.sleep(.2)
        after = read('credit.json'); after_user = user()
        after_audit = management('/admin/users/' + str(upstream['id']) + '/balance-history?page_size=100')
        check('restart_keeps_single_confirmed_operation_id', len(after['operations']) == 1
            and after['operations'][0]['operation_id'] == operation['operation_id'] and after['operations'][0]['status'] == 'CONFIRMED')
        check('restart_does_not_repeat_add_or_create_audit', after_user['balance'] == 101
            and len([r for r in after_audit['items'] if r.get('notes') == expected]) == 1)
        check('credit_worker_sends_no_model_requests', get_json('http://127.0.0.1:28084/observations')['calls'] == before_calls)
        report['internal_credit'] = {'before_balance': 1, 'add_amount': 100, 'after_balance': 101,
            'official_audit_rows': 1, 'operations_after_restart': 1, 'shared_pool_count': 2}
        report['all_passed'] = all(report['checks'].values())
    except Exception as exc:
        (run / 'failure.txt').write_text(type(exc).__name__ + ': ' + str(exc), encoding='utf-8')
        report['failure'] = {'type': type(exc).__name__, 'after_check': next(reversed(report['checks']), 'startup')}
    finally:
        for p in reversed(processes): stop(p)
        report['owned_processes_stopped'] = all(p.poll() is not None for p in processes)
        (run / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        public = ROOT / 'lab/sub2api_e2e/credit-watch-summary.json'
        if public.exists():
            old = json.loads(public.read_text(encoding='utf-8'))
            public.with_name('credit-watch-' + old['run_id'] + '.json').write_text(json.dumps(old, indent=2), encoding='utf-8')
        public.write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'all_passed': report['all_passed'], 'run_id': run.name, 'failure': report.get('failure')}), flush=True)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

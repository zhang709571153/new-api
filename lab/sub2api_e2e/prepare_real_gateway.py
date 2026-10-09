"""Prepare a fresh local RealYu -> existing isolated real Sub2API group.

Creates only synthetic local customer data; no production DB or OAuth import.
Leaves a reviewed candidate running for the parent acceptance agent.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit

from bootstrap_windows import ROOT, PRIVATE, FLAGS

sys.path.insert(0, str(ROOT / 'lab'))
import verify_provider_release as client
sys.path.insert(0, str(ROOT / 'deploy/sub2api'))
from bootstrap_route import bootstrap


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--gateway', type=Path, required=True)
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--group', type=int, required=True)
    parser.add_argument('--port', type=int, default=28502)
    parser.add_argument('--models', default='gpt-6.1-sol', help='Comma-separated explicitly offered models for this isolated route')
    parser.add_argument('--egress-proxy', help='Explicit loopback HTTP proxy for isolated gateway outbound downloads')
    args = parser.parse_args()
    models = [name.strip() for name in args.models.split(',') if name.strip()]
    if not models or len(models) != len(set(models)):
        raise RuntimeError('A nonempty unique model list is required')
    if args.egress_proxy:
        proxy = urlsplit(args.egress_proxy)
        if proxy.scheme != 'http' or proxy.hostname != '127.0.0.1' or not proxy.port or proxy.port < 1024 or proxy.username or proxy.password or proxy.path not in ('', '/'):
            raise RuntimeError('Only an explicit credential-free loopback HTTP proxy is allowed')
    if args.group <= 0 or args.port in (18300, 18301) or args.port < 1024:
        raise RuntimeError('Invalid isolated target')
    with socket.socket() as probe:
        if probe.connect_ex(('127.0.0.1', args.port)) == 0:
            raise RuntimeError('Candidate port is occupied')
    run = PRIVATE / ('real-gateway-' + secrets.token_hex(5))
    run.mkdir()
    credentials_path = Path(json.loads((PRIVATE / 'current.json').read_text())['credentials_file'])
    sub = json.loads(credentials_path.read_text(encoding='utf-8-sig'))
    binary = args.gateway.resolve(strict=True)
    # Freeze a copy so a concurrent build cannot change the executable mid-run.
    worker = run / 'prewarm.exe'
    shutil.copyfile(args.worker, worker)
    secret, password = secrets.token_hex(32), secrets.token_urlsafe(24)
    client.BASE = 'http://127.0.0.1:' + str(args.port)
    receipt = {'base_url': client.BASE + '/v1', 'group_id': args.group, 'gateway_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
        'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(), 'production_changed': False, 'real_model_calls': 0,
        'admin_username': 'e2ereal', 'admin_password': password, 'processes': []}
    config = {'version': 1, 'namespace': run.name, 'identity_secret': secret, 'admin_api_key': sub['admin_api_key'],
        'pools': [], 'provision': {'enabled': True, 'mode': 'prewarmed', 'initial_balance': 100, 'concurrency': 3}, 'bindings': []}
    binding_file = run / 'bindings.json'
    env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower() and k not in
        ('SQL_DSN', 'LOG_SQL_DSN', 'REDIS_CONN_STRING', 'REALYU_UPSTREAM_DRIVER', 'REALYU_SUB2API_BINDINGS_FILE')}
    sqlite_dsn = str(run / 'new-api.db') + '?_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate'
    env.update(PORT=str(args.port), BIND_ADDRESS='127.0.0.1', SQLITE_PATH=sqlite_dsn, SESSION_SECRET=secret,
        CRYPTO_SECRET=secret, GIN_MODE='release', SESSION_COOKIE_SECURE='false', BATCH_UPDATE_ENABLED='false',
        NODE_TYPE='master', RELAY_USER_CONCURRENCY='3', VERSION='realyu-sub2api-real-e2e')
    if args.egress_proxy:
        env.update(HTTP_PROXY=args.egress_proxy, HTTPS_PROXY=args.egress_proxy, NO_PROXY='localhost,127.0.0.1,::1')
        receipt['gateway_egress'] = {'proxy': args.egress_proxy, 'no_proxy': env['NO_PROXY'], 'tls_verification_disabled': False}
    processes = []
    ready = False

    def save():
        (run / 'fixture.json').write_text(json.dumps(receipt, indent=2), encoding='utf-8')

    def start(name):
        with (run / (name + '.log')).open('ab') as log:
            p = subprocess.Popen([str(binary), '--log-dir', str(run / 'logs')], cwd=run, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
        processes.append(p)
        receipt['processes'].append({'pid': p.pid, 'executable': str(binary), 'name': name})
        save()
        for _ in range(100):
            if p.poll() is not None:
                raise RuntimeError('Candidate exited; inspect private log')
            try:
                client.API().call('/api/setup')
                return p
            except Exception:
                time.sleep(.2)
        raise RuntimeError('Candidate startup timeout')

    admin = person = None
    try:
        p = start('fixture-bootstrap')
        admin = client.API()
        admin.call('/api/setup', {'username': 'e2ereal', 'password': password, 'confirmPassword': password,
            'SelfUseModeEnabled': False, 'DemoSiteEnabled': False})
        profile = admin.login('e2ereal', password)
        for key, value in {'RetryTimes': '0', 'quota_setting.trust_quota_usd': '0',
            'ModelRatio': json.dumps({'gpt-6.1-sol': 1}), 'CompletionRatio': json.dumps({'gpt-6.1-sol': 1})}.items():
            admin.call('/api/option/', {'key': key, 'value': value}, 'PUT')
        if 'gpt-image-2' in models:
            admin.call('/api/option/', {'key': 'ModelPrice', 'value': json.dumps({'gpt-image-2': .02})}, 'PUT')
            receipt['synthetic_fixture_image_price_usd'] = .02
        admin.call('/api/user/', {'username': 'realtester', 'password': password, 'display_name': 'Isolated Real E2E', 'role': 1})
        person = client.API()
        user = person.login('realtester', password)
        admin.call('/api/user/manage', {'id': user['id'], 'action': 'add_quota', 'mode': 'add', 'value': 5_000_000})
        person.call('/api/workspace/key', {})
        receipt.update(user_id=user['id'], api_key=person.call('/api/workspace/key/reveal', {})['api_key'])
        boot = {'candidate_url': client.BASE, 'expected_version': 'realyu-sub2api-real-e2e', 'admin_access_token': 'fixture-adapter',
            'admin_user_id': profile['id'], 'route': {'name': 'sub2api-real-e2e', 'base_url': 'http://127.0.0.1:28082',
            'models': ','.join(models), 'group': 'default', 'sub2api_group_id': args.group}}
        route = bootstrap(boot, admin)
        config['pools'] = [{'channel_id': route['channel_id'], 'base_url': route['base_url'], 'group_id': args.group}]
        binding_file.write_text(json.dumps(config), encoding='utf-8')
        receipt['bindings_file'] = str(binding_file)
        queue = run / 'queue.json'
        queue.write_text(json.dumps({'version': 1, 'tasks': [{'realyu_user_id': user['id'], 'workspace_team_id': 0,
            'channel_id': route['channel_id']}]}), encoding='utf-8')
        admin.logout()
        person.logout()
        p.terminate()
        p.wait(timeout=20)
        env.update(REALYU_UPSTREAM_DRIVER='sub2api', REALYU_SUB2API_BINDINGS_FILE=str(binding_file),
            REALYU_SUB2API_ADMIN_URL='http://127.0.0.1:28082')
        job = subprocess.run([str(worker), '--queue', str(queue), '--state', str(run / 'progress.json')], cwd=run,
            env=env, capture_output=True, timeout=40, creationflags=FLAGS)
        (run / 'prewarm.stdout').write_bytes(job.stdout)
        (run / 'prewarm.stderr').write_bytes(job.stderr)
        if job.returncode:
            raise RuntimeError('Prewarming failed; inspect private state')
        p = start('real-candidate')
        receipt['ready'] = ready = True
        save()
        print(json.dumps({'ready': True, 'base_url': receipt['base_url'], 'private_fixture': str(run / 'fixture.json'),
            'gateway_pid': p.pid, 'gateway_sha256': receipt['gateway_sha256']}), flush=True)
        return 0
    finally:
        if not ready:
            for actor in (person, admin):
                if actor:
                    actor.logout()
            for p in reversed(processes):
                if p.poll() is None:
                    p.terminate()
                    p.wait(timeout=20)
            save()


if __name__ == '__main__':
    sys.exit(main())

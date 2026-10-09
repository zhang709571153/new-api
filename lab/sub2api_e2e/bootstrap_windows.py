"""Pinned, loopback-only Sub2API integration environment; never touches SCM.

All downloaded binaries, credentials, DB data, process receipts and logs are
stored in ignored .lab/sub2api-e2e. Redis is a community Windows build, used
only for this local test; deployment support needs separate validation.
"""
from __future__ import annotations

import argparse
import atexit
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = ROOT / '.lab/sub2api-e2e'
ASSETS = {
    'sub2api': ('https://github.com/Wei-Shaw/sub2api/releases/download/v0.2.15/sub2api_0.2.15_windows_amd64.zip',
                '1a95286596a10c268b17e67d508ade717b4ca1dfde2b4f4c295ab2d31f1dc5d1'),
    'redis': ('https://github.com/redis-windows/redis-windows/releases/download/8.10.2/Redis-8.10.2-Windows-x64-msys2.zip',
              '7c8cebd50347eaa1d9e784da842ed47a4f33394637835531d6614777b950ee85'),
}
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def get_json(url, data=None, token=None):
    headers = {'Content-Type': 'application/json', 'User-Agent': 'RealYu-Isolated-Sub2API-E2E'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    req = urllib.request.Request(url, data=None if data is None else json.dumps(data).encode(), headers=headers)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(req, timeout=15) as response:
        return json.load(response)


def prepare_assets():
    for name, (url, digest) in ASSETS.items():
        archive = PRIVATE / 'downloads' / (name + '.zip')
        archive.parent.mkdir(parents=True, exist_ok=True)
        if not archive.exists():
            with urllib.request.urlopen(url, timeout=90) as source, archive.open('wb') as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f'{name} archive SHA256 mismatch; not executing')
        target = (PRIVATE / 'bin' / name).resolve()
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as bundle:
            for item in bundle.infolist():
                destination = (target / item.filename).resolve()
                if not destination.is_relative_to(target):
                    raise RuntimeError('Archive traversal rejected')
            bundle.extractall(target)
    (PRIVATE / 'artifact-checksums.json').write_text(json.dumps(
        {n: {'url': v[0], 'sha256': v[1]} for n, v in ASSETS.items()}, indent=2), encoding='utf-8')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--pg-port', type=int, default=28432)
    parser.add_argument('--redis-port', type=int, default=28379)
    parser.add_argument('--sub-port', type=int, default=28082)
    args = parser.parse_args()
    PRIVATE.mkdir(parents=True, exist_ok=True)
    for port in (args.pg_port, args.redis_port, args.sub_port):
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', port)) == 0:
                raise RuntimeError(f'Port {port} occupied; refusing to reuse it')
    prepare_assets()
    run = PRIVATE / ('run-' + secrets.token_hex(5))
    run.mkdir()
    state = {'run_dir': str(run), 'host': '127.0.0.1', 'postgres_port': args.pg_port,
             'redis_port': args.redis_port, 'sub2api_port': args.sub_port,
             'admin_email': 'isolated-admin@example.test', 'admin_password': secrets.token_urlsafe(32),
             'database_password': secrets.token_urlsafe(32), 'redis_password': secrets.token_urlsafe(32),
             'jwt_secret': secrets.token_hex(32), 'processes': [], 'ready': False}
    credentials = run / 'credentials.json'
    owned_processes = []

    def save():
        credentials.write_text(json.dumps(state, indent=2), encoding='utf-8')
        (PRIVATE / 'current.json').write_text(json.dumps({'credentials_file': str(credentials)}, indent=2), encoding='utf-8')

    def start(name, command, env=None):
        with (run / (name + '.log')).open('ab') as log:
            process = subprocess.Popen([str(x) for x in command], cwd=run, env=env,
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, creationflags=FLAGS)
        state['processes'].append({'name': name, 'pid': process.pid, 'executable': str(command[0])})
        owned_processes.append((name, process))
        save()
        return process

    def cleanup_failed_start():
        if state['ready']:
            return
        for name, process in reversed(owned_processes):
            if process.poll() is not None:
                continue
            if name == 'postgres':
                subprocess.run([str(args.pg_bin / 'pg_ctl.exe'), '-D', str(run / 'postgres-data'), '-m', 'fast', 'stop'],
                    capture_output=True, timeout=30, creationflags=FLAGS)
            else:
                process.terminate()
                process.wait(timeout=20)
        state['failed_start_cleanup_completed'] = True
        save()

    atexit.register(cleanup_failed_start)

    save()
    pwfile = run / 'postgres-password.txt'
    pwfile.write_text(state['database_password'], encoding='utf-8')
    result = subprocess.run([str(args.pg_bin / 'initdb.exe'), '-D', str(run / 'postgres-data'), '-U', 'sub2api_e2e',
        '-A', 'scram-sha-256', '--pwfile=' + str(pwfile), '--no-locale', '-E', 'UTF8'],
        capture_output=True, timeout=120, creationflags=FLAGS)
    (run / 'postgres-init.log').write_bytes(result.stdout + result.stderr)
    if result.returncode:
        raise RuntimeError('PostgreSQL initdb failed; inspect private log')
    start('postgres', [args.pg_bin / 'postgres.exe', '-D', run / 'postgres-data', '-h', '127.0.0.1', '-p', args.pg_port])
    env = os.environ.copy()
    env['PGPASSWORD'] = state['database_password']
    for _ in range(60):
        result = subprocess.run([str(args.pg_bin / 'psql.exe'), '-X', '-h', '127.0.0.1', '-p', str(args.pg_port),
            '-U', 'sub2api_e2e', '-d', 'postgres', '-v', 'ON_ERROR_STOP=1', '-c', 'CREATE DATABASE sub2api_e2e;'],
            capture_output=True, env=env, timeout=10, creationflags=FLAGS)
        if result.returncode == 0:
            break
        time.sleep(0.5)
    else:
        raise RuntimeError('PostgreSQL startup/create database failed')
    redis_data = run / 'redis-data'
    redis_data.mkdir()
    redis_config = run / 'redis.conf'
    redis_config.write_text(f'bind 127.0.0.1\nport {args.redis_port}\nprotected-mode yes\nrequirepass {state["redis_password"]}\n'
        'save ""\nappendonly no\ndir "./redis-data"\n', encoding='utf-8')
    redis = next((PRIVATE / 'bin/redis').rglob('redis-server.exe'))
    # The community build is MSYS2; relative paths avoid its Win32 path parser.
    redis_process = start('redis', [redis, 'redis.conf'])
    for _ in range(30):
        if redis_process.poll() is not None:
            raise RuntimeError('Redis exited; inspect private redis.log')
        with socket.socket() as probe:
            if probe.connect_ex(('127.0.0.1', args.redis_port)) == 0:
                break
        time.sleep(0.2)
    else:
        raise RuntimeError('Redis startup timed out')
    sub = next((PRIVATE / 'bin/sub2api').rglob('sub2api.exe'))
    sub_data = run / 'sub2api-data'
    sub_data.mkdir()
    clean = {k: v for k, v in os.environ.items() if not k.upper().startswith(('DATABASE_', 'REDIS_', 'ADMIN_', 'JWT_', 'SERVER_', 'AUTO_SETUP', 'DATA_DIR', 'TOKEN_REFRESH_', 'RUN_MODE'))}
    clean.update(AUTO_SETUP='true', DATA_DIR=str(sub_data), DATABASE_HOST='127.0.0.1', DATABASE_PORT=str(args.pg_port),
        DATABASE_USER='sub2api_e2e', DATABASE_PASSWORD=state['database_password'], DATABASE_DBNAME='sub2api_e2e', DATABASE_SSLMODE='disable',
        REDIS_HOST='127.0.0.1', REDIS_PORT=str(args.redis_port), REDIS_PASSWORD=state['redis_password'], REDIS_DB='0',
        ADMIN_EMAIL=state['admin_email'], ADMIN_PASSWORD=state['admin_password'], SERVER_HOST='127.0.0.1',
        SERVER_PORT=str(args.sub_port), SERVER_MODE='release', JWT_SECRET=state['jwt_secret'], RUN_MODE='standard',
        TOKEN_REFRESH_ENABLED='false', TZ='UTC')
    process = start('sub2api', [sub], clean)
    for _ in range(180):
        if process.poll() is not None:
            raise RuntimeError('Sub2API exited; inspect private sub2api.log')
        try:
            login = get_json(f'http://127.0.0.1:{args.sub_port}/api/v1/auth/login',
                {'email': state['admin_email'], 'password': state['admin_password']})
            if login.get('data', {}).get('access_token'):
                state['admin_access_token'] = login['data']['access_token']
                state['ready'] = True
                save()
                print(json.dumps({'ready': True, 'base_url': f'http://127.0.0.1:{args.sub_port}',
                    'credentials_file': str(credentials), 'versions': {'sub2api': '0.2.15', 'redis': '8.10.2-community-windows'}}, indent=2))
                return
        except Exception:
            pass
        time.sleep(1)
    raise RuntimeError('Sub2API setup/login timed out; inspect private logs')


if __name__ == '__main__':
    main()

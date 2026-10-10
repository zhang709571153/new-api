"""Run exactly one worker; Windows SCM, not this launcher, owns recovery.

No secrets or request bodies are written to lifecycle receipts. WinSW owns the
worker tree, stdout/stderr rotation, graceful stop and forced-stop deadline.
"""
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time


def write_json(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value), encoding='utf-8')
    os.replace(temp, path)


def plan(role, config, base):
    release, root = Path(config['release']), Path(config['observability'])
    private = release / '.lab'
    python = str(base / 'python' / 'python.exe')
    env = os.environ.copy()
    for key in list(env):
        if key.upper() in {'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'PYTHONPATH', 'PYTHONHOME', 'REDIS_CONN_STRING'}:
            env.pop(key)
    env.update(PYTHONUNBUFFERED='1', PYTHONDONTWRITEBYTECODE='1')
    pid_file = None
    if role == 'api' and 'singlecore_api' in config:
        # Only the API worker changes. The transparent bridge, its admission
        # gate, and the two tunnel connectors keep their existing addresses.
        native = config['singlecore_api']
        if not isinstance(native, dict) or set(native) != {'exe', 'env_file', 'working_dir', 'sha'}:
            raise ValueError('Invalid native API manifest')
        binary, env_file, cwd = (Path(native[k]) for k in ('exe', 'env_file', 'working_dir'))
        if not all(p.is_absolute() for p in (binary, env_file, cwd)) or not cwd.is_dir():
            raise ValueError('Native API paths must be absolute and installed')
        if not re.fullmatch(r'[0-9a-f]{64}', native['sha']):
            raise ValueError('Native API executable must be pinned')
        with binary.open('rb') as stream:
            if hashlib.file_digest(stream, 'sha256').hexdigest() != native['sha']:
                raise ValueError('Native API executable changed')
        native_env = json.loads(env_file.read_text('utf-8-sig'))
        if not isinstance(native_env, dict) or any(
            not isinstance(k, str) or not re.fullmatch(r'[A-Z][A-Z0-9_]*', k)
            or not isinstance(v, str) for k, v in native_env.items()
        ):
            raise ValueError('Native API environment must be a string map')
        if any(k in native_env for k in ('PYTHONPATH', 'PYTHONHOME', 'SQLITE_PATH', 'REDIS_CONN_STRING')):
            raise ValueError('Legacy interpreter or database override is not allowed')
        required = {'SERVER_HOST': '127.0.0.1', 'SERVER_PORT': '18300',
                    'DATABASE_HOST': '127.0.0.1', 'DATABASE_PORT': '28490',
                    'DATABASE_DBNAME': 'realyu_singlecore_20261010_candidate',
                    'DATABASE_USER': 'realyu_singlecore_owner_20261010',
                    'REDIS_HOST': '127.0.0.1', 'REDIS_PORT': '28391', 'REDIS_DB': '1',
                    'REALYU_FUNDING_ENABLED': 'true', 'REALYU_WS_FUNDING_ENABLED': 'true'}
        if any(native_env.get(k) != value for k, value in required.items()):
            raise ValueError('Native API listener, authority, or funding policy mismatch')
        for k in ('DATABASE_PASSWORD', 'REDIS_PASSWORD', 'JWT_SECRET',
                  'REALYU_LEGACY_IDENTITY_SECRET', 'REALYU_LEGACY_NAMESPACE'):
            if not native_env.get(k):
                raise ValueError('Native API private configuration is incomplete')
        for key in list(env):
            if key.upper() in {'SQLITE_PATH', 'SQL_DSN', 'REDIS_CONN_STRING', 'PORT', 'NEWAPI_PORT',
                               'SESSION_SECRET', 'REALYU_UPSTREAM_DRIVER', 'REALYU_SUB2API_BINDINGS_FILE',
                               'REALYU_SUB2API_QUEUE_DIR', 'REALYU_SUB2API_STATE_DIR', 'REALYU_SUB2API_ADMIN_URL'}:
                env.pop(key)
        env.update(native_env)
        args = [str(binary)]
        pid_file = cwd / 'singlecore-api.pid'
    elif role == 'bridge':
        # The edge only forwards bytes and owns admission/observability. It
        # needs neither a customer database nor the retired API credentials.
        env.update(REALYU_PRIVATE_DIR=str(private), REALYU_BACKEND_PORT='18300',
                   REALYU_BRIDGE_PORT='18301', NEWAPI_PORT='18300', BRIDGE_PORT='18301',
                   NO_PROXY='127.0.0.1,localhost')
        args = [python, '-B', str(release / 'lab' / 'image_bridge.py')]
        pid_file = private / 'bridge.pid'
        cwd = release
    elif role == 'api':
        cfg = json.loads((private / 'credentials.json').read_text('utf-8-sig'))
        env.update(REALYU_PRIVATE_DIR=str(private), REALYU_BACKEND_PORT='18300', REALYU_BRIDGE_PORT='18301',
                   PORT='18300', NEWAPI_PORT='18300', BRIDGE_PORT='18301', BIND_ADDRESS='127.0.0.1',
                   SQLITE_PATH=str(private / 'new-api.db') + '?_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate',
                   TRUSTED_PROXIES='127.0.0.1,::1', SESSION_SECRET=cfg['session_secret'], GIN_MODE='release',
                   BATCH_UPDATE_ENABLED='false', HTTP_PROXY='http://127.0.0.1:7890', HTTPS_PROXY='http://127.0.0.1:7890',
                   NO_PROXY='127.0.0.1,localhost', SESSION_COOKIE_SECURE='true', SESSION_COOKIE_TRUSTED_URL='https://api.realyu.fun',
                   RELAY_USER_CONCURRENCY=str(cfg.get('user_concurrency', 2)),
                   RELAY_CONCURRENCY_EXEMPT_IDS=','.join(str(x) for x in cfg.get('concurrency_exempt_ids', [cfg.get('tenant_id', 0)])))
        if cfg.get('auth_sync_enabled', True):
            raise RuntimeError('This six-service manifest requires auth_sync_enabled=false; provision auth sync explicitly otherwise')
        if cfg.get('codex_channel_login_exe'):
            env.update(CODEX_CHANNEL_LOGIN_EXE=cfg['codex_channel_login_exe'],
                       CODEX_CHANNEL_LOGIN_HOME=str(private / 'codex-channel-login' / 'attempts'))
        binary = (private / cfg['binary_name']).resolve()
        if binary.parent != private.resolve() or not binary.is_file():
            raise RuntimeError('Invalid configured backend executable')
        args = [str(binary), '--log-dir', str(private / 'logs')] if role == 'api' else [python, '-B', str(release / 'lab' / 'image_bridge.py')]
        pid_file = private / ('newapi.pid' if role == 'api' else 'bridge.pid')
        cwd = release
    elif role in ('tunnel-primary', 'tunnel-replica'):
        port = 20242 if role == 'tunnel-primary' else 18432
        pid_file = private / ('tunnel.pid' if role == 'tunnel-primary' else 'tunnel-replica.pid')
        executable = config.get('cloudflared_by_role', {}).get(role, config['cloudflared'])
        protocol = config.get('tunnel_protocol_by_role', {}).get(role, 'http2')
        if protocol not in ('http2', 'quic', 'auto'):
            raise ValueError('Unsupported tunnel protocol')
        args = [executable, 'tunnel', '--no-autoupdate', '--config', str(release / 'lab' / 'cloudflared.yml'),
                '--metrics', f'127.0.0.1:{port}', '--protocol', protocol, '--edge-ip-version', '4',
                '--label', 'realyu-production-' + role.removeprefix('tunnel-'), '--loglevel', 'info', 'run',
                '--dns-resolver-addrs', '223.5.5.5:53', '--dns-resolver-addrs', '119.29.29.29:53']
        # Temporary explicit edge bypass: listeners must remain loopback-only.
        edges = config.get('tunnel_edge_addrs_by_role', {}).get(role, [])
        for edge in edges:
            host, port_text = edge.rsplit(':', 1)
            if host != '127.0.0.1' or not 1024 <= int(port_text) <= 65535:
                raise ValueError('Tunnel bypass requires a loopback listener')
            position = args.index('run')
            args[position:position] = ['--edge', edge]
        cwd = release
    elif role == 'edge-proxy':
        proxy = config['edge_proxy']
        args = [proxy['executable']]
        for port, target in zip(range(19454, 19458), proxy['targets']):
            args.extend(['-L', f'tcp://127.0.0.1:{port}/{target}:7844'])
        args.extend(['-F', proxy['upstream']])
        cwd = base
    elif role == 'kuma':
        env.update(NODE_ENV='production', UPTIME_KUMA_HOST='127.0.0.1', UPTIME_KUMA_PORT='3001',
                   UPTIME_KUMA_DB_TYPE='sqlite', DATA_DIR=str(root / 'kuma-data'))
        args = [config['node'], str(root / 'uptime-kuma' / 'server' / 'server.js')]
        cwd = root / 'uptime-kuma'
    elif role == 'evidence':
        args = [python, '-B', str(root / 'collect_evidence.py'), '--root', str(root), '--release', str(release)]
        cwd = root
    elif role == 'fixture':
        args = [python, '-u', str(base / 'fixture_worker.py'), str(base / 'logs' / 'fixture')]
        cwd = base
    else:
        raise ValueError('Unknown service role')
    return args, env, cwd, pid_file


LAUNCHER_REVISION = 'stdio-v3-singlecore'


def start_worker(args, env, cwd):
    # CREATE_NO_WINDOW children must receive the WinSW pipe handles explicitly.
    # Merely inheriting the environment loses worker output on Windows.
    return subprocess.Popen(args, env=env, cwd=cwd, stdin=subprocess.DEVNULL,
                            stdout=sys.stdout, stderr=sys.stderr,
                            creationflags=subprocess.CREATE_NO_WINDOW)


def main():
    base = Path(__file__).resolve().parent
    role = sys.argv[1]
    config = json.loads((base / 'manifest.json').read_text('utf-8'))
    logdir = base / 'logs' / role
    args, env, cwd, pid_file = plan(role, config, base)
    import socket
    ports = {'api': [18300], 'bridge': [18301, 18302], 'kuma': [3001], 'evidence': [19416],
             'tunnel-primary': [20242], 'tunnel-replica': [18432],
             'edge-proxy': [19454, 19455, 19456, 19457]}.get(role, [])
    for port in ports:
        # Fail before worker initialization (especially API DB tasks) if a legacy
        # process or unrelated program still owns the intended port.
        with socket.socket() as probe_socket:
            probe_socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe_socket.bind(('127.0.0.1', port))
    from job_guard import guard, wait
    handles = guard()
    child = start_worker(args, env, cwd)
    from service_identity import process_birth
    receipt = {'role': role, 'launcher_revision': LAUNCHER_REVISION, 'worker_executable': args[0],
               'launcher_pid': os.getpid(), 'pid': child.pid, 'birth': process_birth(child.pid),
               'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(), 'event': 'worker_started'}
    write_json(logdir / 'worker.json', receipt)
    if pid_file:
        pid_file.write_text(str(child.pid), encoding='ascii')
    print(json.dumps(receipt), flush=True)
    started = time.monotonic()
    code = wait(child, handles)
    print(json.dumps({**receipt, 'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(), 'event': 'worker_exited',
                      'exit_code': code, 'uptime_seconds': round(time.monotonic() - started, 3),
                      'recovery_owner': 'Windows SCM'}), flush=True)
    # A daemon exiting successfully without an SCM stop request is still a failure.
    return code if 1 <= code <= 255 else 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({'event': 'launcher_failed', 'error_type': type(exc).__name__}), flush=True)
        sys.exit(1)

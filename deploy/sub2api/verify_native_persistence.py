"""Exercise PostgreSQL and community Redis only in newly created private fixtures.

Does not install services, stop existing processes, read production databases or
configure public listeners. Redis crashes terminate only this script's Popen
handle. Private fixture logs/data are retained; the summary has no credentials.
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
import time

FLAGS = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0


def sha256(path):
    with Path(path).open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def redis_command(port, password, *args):
    def read(stream):
        line = stream.readline()
        if not line:
            raise RuntimeError('Redis response ended')
        tag, value = line[:1], line[1:-2]
        if tag == b'-':
            raise RuntimeError('Redis rejected a fixture command')
        if tag == b'+':
            return value.decode()
        if tag == b':':
            return int(value)
        if tag == b'$':
            size = int(value)
            if size < 0:
                return None
            result = stream.read(size)
            if stream.read(2) != b'\r\n':
                raise RuntimeError('Invalid Redis bulk reply')
            return result.decode()
        if tag == b'*':
            return [read(stream) for _ in range(int(value))]
        raise RuntimeError('Unknown Redis reply')

    def send(stream, values):
        values = [str(value).encode() for value in values]
        payload = b'*' + str(len(values)).encode() + b'\r\n'
        for value in values:
            payload += b'$' + str(len(value)).encode() + b'\r\n' + value + b'\r\n'
        stream.write(payload)
        stream.flush()

    with socket.create_connection(('127.0.0.1', port), timeout=5) as client:
        with client.makefile('rwb') as stream:
            send(stream, ('AUTH', password))
            if read(stream) != 'OK':
                raise RuntimeError('Redis fixture authentication failed')
            send(stream, args)
            return read(stream)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pg-bin', type=Path, required=True)
    parser.add_argument('--redis-server', type=Path, required=True)
    parser.add_argument('--private-root', type=Path, required=True)
    parser.add_argument('--pg-port', type=int, default=28732)
    parser.add_argument('--redis-port', type=int, default=28779)
    args = parser.parse_args()
    if os.name != 'nt':
        raise RuntimeError('This verification targets native Windows')
    pg = args.pg_bin.resolve(strict=True)
    redis = args.redis_server.resolve(strict=True)
    for port in (args.pg_port, args.redis_port):
        if port < 1024 or port > 65535 or port in (18300, 18301, 28082, 28083, 28379, 28432, 28501):
            raise RuntimeError('Unsafe or reserved fixture port')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    if args.pg_port == args.redis_port:
        raise RuntimeError('Fixture ports must differ')
    root = args.private_root.resolve()
    if root.exists():
        raise RuntimeError('Private fixture root must be new')
    if '.private' not in root.parts and '.lab' not in root.parts:
        raise RuntimeError('Use an ignored .private or .lab fixture directory')
    root.mkdir(parents=True)
    pg_password, redis_password = secrets.token_hex(32), secrets.token_hex(32)
    processes = {}
    summary = {'scope': 'new isolated native Windows persistence fixtures',
               'production_changes': False, 'scm_changes': False, 'checks': [],
               'artifacts': {'postgres_sha256': sha256(pg / 'postgres.exe'),
                             'redis_server_sha256': sha256(redis)}}
    (root / 'credentials.json').write_text(json.dumps({'pg_password': pg_password,
        'redis_password': redis_password}), encoding='utf-8')
    pg_env = {k: v for k, v in os.environ.items() if not k.upper().startswith('PG')}
    pg_env['PGPASSWORD'] = pg_password

    def command(name, argv, env=None):
        result = subprocess.run([str(a) for a in argv], cwd=root, env=env,
                                capture_output=True, timeout=90, creationflags=FLAGS)
        (root / (name + '.log')).write_bytes(result.stdout + result.stderr)
        if result.returncode:
            raise RuntimeError('Fixture command failed: ' + name)
        return result.stdout.decode('utf-8', errors='replace').strip()

    def check(label, condition=True):
        summary['checks'].append({'name': label, 'status': 'PASS' if condition else 'FAIL'})
        if not condition:
            raise RuntimeError('Fixture check failed: ' + label)

    def launch(name, argv):
        with (root / (name + '-server.log')).open('ab') as output:
            process = subprocess.Popen([str(a) for a in argv], cwd=root,
                                       stdin=subprocess.DEVNULL, stdout=output,
                                       stderr=subprocess.STDOUT, creationflags=FLAGS)
        processes[name] = process
        return process

    def sql(name, statement, database='postgres'):
        return command(name, [pg / 'psql.exe', '-X', '-h', '127.0.0.1', '-p', args.pg_port,
                              '-U', 'persistence_fixture', '-d', database, '-v', 'ON_ERROR_STOP=1',
                              '-tAc', statement], pg_env)

    def start_pg():
        process = launch('postgres', [pg / 'postgres.exe', '-D', root / 'postgres-data',
                                     '-h', '127.0.0.1', '-p', args.pg_port])
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError('Fixture PostgreSQL exited during startup')
            try:
                if sql('pg-ready', 'SELECT 1') == '1':
                    return
            except RuntimeError:
                pass
            time.sleep(0.25)
        raise RuntimeError('Fixture PostgreSQL startup timed out')

    def stop_pg():
        process = processes.get('postgres')
        if process is not None and process.poll() is None:
            command('pg-stop', [pg / 'pg_ctl.exe', '-D', root / 'postgres-data', '-m', 'fast', '-w', 'stop'])
            process.wait(timeout=30)

    def redis_call(*values):
        return redis_command(args.redis_port, redis_password, *values)

    def start_redis(config):
        process = launch('redis', [redis, config])  # Relative paths for MSYS2.
        for _ in range(60):
            if process.poll() is not None:
                raise RuntimeError('Fixture Redis exited during startup')
            try:
                if redis_call('PING') == 'PONG':
                    return
            except (OSError, RuntimeError):
                pass
            time.sleep(0.25)
        raise RuntimeError('Fixture Redis startup timed out')

    def stop_redis():
        process = processes.get('redis')
        if process is not None and process.poll() is None:
            try:
                redis_call('SHUTDOWN', 'NOSAVE')
            except (OSError, RuntimeError):
                pass  # SHUTDOWN closes the connection without a reply.
            process.wait(timeout=30)

    try:
        password_file = root / 'postgres-password.txt'
        password_file.write_text(pg_password, encoding='utf-8')
        command('pg-init', [pg / 'initdb.exe', '-D', root / 'postgres-data', '-U', 'persistence_fixture',
                           '-A', 'scram-sha-256', '--pwfile=' + str(password_file), '--no-locale', '-E', 'UTF8'])
        start_pg()
        sql('pg-create', 'CREATE DATABASE persistence_candidate')
        sql('pg-insert', "CREATE TABLE durability_check(id integer PRIMARY KEY, note text NOT NULL); "
            "INSERT INTO durability_check VALUES(1,'before-restart'),(2,'restore-sentinel');", 'persistence_candidate')
        stop_pg()
        start_pg()
        check('PostgreSQL committed rows survive a graceful restart',
              sql('pg-read', "SELECT string_agg(note, ',' ORDER BY id) FROM durability_check", 'persistence_candidate')
              == 'before-restart,restore-sentinel')
        dump = root / 'postgres-backup.dump'
        command('pg-dump', [pg / 'pg_dump.exe', '-h', '127.0.0.1', '-p', args.pg_port, '-U', 'persistence_fixture',
                           '-d', 'persistence_candidate', '-Fc', '-f', dump], pg_env)
        sql('pg-create-restore', 'CREATE DATABASE persistence_restored_candidate')
        command('pg-restore', [pg / 'pg_restore.exe', '-h', '127.0.0.1', '-p', args.pg_port, '-U', 'persistence_fixture',
                              '-d', 'persistence_restored_candidate', '--exit-on-error', '--no-owner', dump], pg_env)
        check('PostgreSQL custom dump restores into a fresh database',
              sql('pg-restored-read', 'SELECT count(*) FROM durability_check', 'persistence_restored_candidate') == '2')
        stop_pg()

        for directory in ('redis-aof-data', 'redis-rdb-data'):
            (root / directory).mkdir()
        base = f'bind 127.0.0.1\nport {args.redis_port}\nprotected-mode yes\nrequirepass {redis_password}\n' \
               'save ""\ndbfilename dump.rdb\nappendfsync always\nmaxmemory 128mb\nmaxmemory-policy noeviction\n'
        (root / 'redis-aof.conf').write_text(base + 'appendonly yes\ndir "./redis-aof-data"\n', encoding='utf-8')
        (root / 'redis-rdb.conf').write_text(base + 'appendonly no\ndir "./redis-rdb-data"\n', encoding='utf-8')
        start_redis('redis-aof.conf')
        check('Redis authentication and loopback fixture startup', redis_call('PING') == 'PONG')
        redis_call('SET', 'fixture:string', 'snapshot-value')
        redis_call('HSET', 'fixture:hash', 'member', 'synthetic')
        redis_call('RPUSH', 'fixture:list', 'one', 'two')
        redis_call('ZADD', 'fixture:queue', 10, 'job-one')
        redis_call('SET', 'fixture:lease', 'owner', 'PX', 120000, 'NX')
        check('Redis Lua lease operation is available', redis_call('EVAL',
            "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('PEXPIRE',KEYS[1],ARGV[2]) else return 0 end",
            1, 'fixture:lease', 'owner', 120000) == 1)
        check('Redis SAVE produces an RDB snapshot', redis_call('SAVE') == 'OK' and (root / 'redis-aof-data/dump.rdb').is_file())
        shutil.copy2(root / 'redis-aof-data/dump.rdb', root / 'redis-rdb-data/dump.rdb')
        redis_call('SET', 'fixture:after-rdb', 'aof-only')
        stop_redis()
        start_redis('redis-aof.conf')
        check('Redis AOF survives normal restart without shutdown snapshot', redis_call('GET', 'fixture:after-rdb') == 'aof-only')
        check('Redis persisted hash/list/sorted-set values',
              redis_call('HGET', 'fixture:hash', 'member') == 'synthetic'
              and redis_call('LRANGE', 'fixture:list', 0, -1) == ['one', 'two']
              and redis_call('ZRANGE', 'fixture:queue', 0, -1) == ['job-one'])
        check('Redis lease retains a bounded TTL after restart', 0 < redis_call('PTTL', 'fixture:lease') <= 120000)
        redis_call('BGREWRITEAOF')
        for _ in range(120):
            info = redis_call('INFO', 'persistence')
            if 'aof_rewrite_in_progress:0' in info and 'aof_last_bgrewrite_status:ok' in info:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError('AOF rewrite did not finish')
        check('Redis background AOF rewrite succeeds')
        redis_call('SET', 'fixture:crash-marker', 'durable-before-kill')
        process = processes['redis']
        check('Redis crash targets this script owned active process', process.poll() is None)
        process.kill()
        process.wait(timeout=30)
        start_redis('redis-aof.conf')
        check('Redis AOF restores after forced termination', redis_call('GET', 'fixture:crash-marker') == 'durable-before-kill')
        stop_redis()
        start_redis('redis-rdb.conf')
        check('Redis restores a standalone RDB snapshot', redis_call('GET', 'fixture:string') == 'snapshot-value'
              and redis_call('GET', 'fixture:after-rdb') is None and redis_call('GET', 'fixture:crash-marker') is None)
        stop_redis()
        summary['status'] = 'PASS'
    except Exception as exc:
        summary['status'] = 'FAIL'
        summary['failure_category'] = type(exc).__name__
        raise
    finally:
        for name, process in list(processes.items()):
            if process.poll() is None:
                try:
                    stop_pg() if name == 'postgres' else stop_redis()
                except Exception:
                    process.kill()
                    process.wait(timeout=30)
        summary['all_owned_processes_stopped'] = all(p.poll() is not None for p in processes.values())
        summary['not_run'] = ['SCM installation/account/reboot', 'disk-full fault', 'production data restore', 'long-duration load or availability SLA']
        (root / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
        print(json.dumps({'status': summary.get('status'), 'checks': len(summary['checks']),
                          'all_owned_processes_stopped': summary['all_owned_processes_stopped'],
                          'private_summary': str(root / 'summary.json')}))


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # Details, subprocess output and credentials remain in the private fixture.
        raise SystemExit(1)

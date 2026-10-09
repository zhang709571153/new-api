"""Isolated real-engine release-upgrade and team-management database checks.

No production settings are read. Processes are started directly and only this
run's Popen handles are stopped. Fixtures and credentials stay in ignored .lab.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = ROOT / '.lab' / 'matrix'
GO = Path('C:/srv/tools/go1.25.12-full/go/bin/go.exe')
MYSQL = ROOT / '.lab/db-engines/mysql/mysql-8.4.11-winx64'
PG = ROOT / '.lab/db-engines/postgres/pgsql'
FLAGS = getattr(subprocess, 'CREATE_NO_WINDOW', 0)


def execute(args, *, env=None, cwd=None, input=None, timeout=180):
    return subprocess.run([str(x) for x in args], cwd=cwd, env=env,
                          input=input, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', timeout=timeout,
                          creationflags=FLAGS)


def assert_free(port):
    with socket.socket() as sock:
        if sock.connect_ex(('127.0.0.1', port)) == 0:
            raise RuntimeError(f'Port {port} is already occupied; refusing to reuse it')


class Matrix:
    def __init__(self, report_path=None, test_pattern='^TestTeamMVP$', *, mysql_port=13306, postgres_port=15432):
        self.mysql_port = mysql_port
        self.postgres_port = postgres_port
        PRIVATE.mkdir(parents=True, exist_ok=True)
        self.run_id = 'm' + secrets.token_hex(4)
        self.directory = PRIVATE / self.run_id
        self.directory.mkdir()
        self.password = secrets.token_urlsafe(32)
        self.processes = []
        self.files = []
        self.report_path = report_path or ROOT / 'lab/results/database-matrix.json'
        self.test_pattern = test_pattern
        self.report = {'run_id': self.run_id, 'tested_at_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()),
                       'baseline': 'official v1.0.0-rc.40 release executable',
                       'command': subprocess.list2cmdline([sys.executable, *sys.argv]),
                       'candidate_is_production': False, 'engines': {}, 'cases': [], 'team_tests': {},
                       'limitations': ['Minimum supported MySQL/PostgreSQL versions are not tested by this run.']}
        history = PRIVATE / 'diagnostic-history.json'
        if history.is_file():
            self.report['diagnostic_history'] = json.loads(history.read_text(encoding='utf-8'))
            self.report['unclassified_prior_verifier_anomaly'] = True
        (self.directory / 'credentials.json').write_text(json.dumps({'password': self.password}), encoding='utf-8')

    def start_process(self, args, directory, label, env=None):
        handle = (directory / (label + '.log')).open('wb')
        self.files.append(handle)
        process = subprocess.Popen([str(x) for x in args], cwd=directory,
                                   env=env, stdout=handle, stderr=subprocess.STDOUT,
                                   creationflags=FLAGS)
        self.processes.append(process)
        return process

    def stop(self, process):
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=20)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

    def save(self):
        path = self.report_path
        path.parent.mkdir(parents=True, exist_ok=True)
        body = json.dumps(self.report, ensure_ascii=False, indent=2) + '\n'
        path.write_text(body, encoding='utf-8')
        (self.directory / 'report.json').write_text(body, encoding='utf-8')

    def sql(self, engine, database, statement, *, expect_failure=False):
        if engine == 'sqlite':
            try:
                with sqlite3.connect(database) as connection:
                    result = connection.execute(statement)
                    rows = result.fetchall() if result.description else []
                    connection.commit()
                if expect_failure:
                    raise AssertionError('Duplicate unexpectedly accepted')
                return [[str(v) if v is not None else '<null>' for v in row] for row in rows]
            except sqlite3.IntegrityError as exc:
                if expect_failure:
                    return str(exc)
                raise
        env = os.environ.copy()
        if engine == 'mysql':
            env['MYSQL_PWD'] = self.password
            args = [MYSQL / 'bin/mysql.exe', '--no-defaults', '--protocol=TCP', '--host=127.0.0.1', '--port='+str(self.mysql_port),
                    '--user=root', '--batch', '--raw', '--skip-column-names', '--default-character-set=utf8mb4']
            if database:
                args.append(database)
        else:
            env['PGPASSWORD'] = self.password
            args = [PG / 'bin/psql.exe', '-X', '-h', '127.0.0.1', '-p', str(self.postgres_port), '-U', 'matrix',
                    '-d', database or 'postgres', '-A', '-t', '-F', '\t', '-v', 'ON_ERROR_STOP=1', '-q']
        result = execute(args, env=env, input=statement, timeout=60)
        if expect_failure:
            if result.returncode == 0:
                raise AssertionError('Duplicate unexpectedly accepted')
            return result.stderr
        if result.returncode:
            raise RuntimeError(f'{engine} SQL failed: {result.stderr[-1500:]}')
        return [line.split('\t') for line in result.stdout.splitlines() if line]

    def start_databases(self):
        for port in (self.mysql_port, self.postgres_port, 19400):
            assert_free(port)
        mysql_data = self.directory / 'mysql-data'
        result = execute([MYSQL / 'bin/mysqld.exe', '--no-defaults', '--initialize-insecure',
                          '--basedir=' + str(MYSQL), '--datadir=' + str(mysql_data)], timeout=180)
        (self.directory / 'mysql-init.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('MySQL initialization failed; inspect private mysql-init.log')
        mysql = self.start_process([MYSQL / 'bin/mysqld.exe', '--no-defaults', '--console',
                                   '--basedir=' + str(MYSQL), '--datadir=' + str(mysql_data),
                                   '--bind-address=127.0.0.1', '--port='+str(self.mysql_port), '--mysqlx=OFF'], self.directory, 'mysql')
        args = [MYSQL / 'bin/mysql.exe', '--no-defaults', '--protocol=TCP', '--host=127.0.0.1', '--port='+str(self.mysql_port), '--user=root']
        env = os.environ.copy()
        env.pop('MYSQL_PWD', None)
        for _ in range(90):
            if mysql.poll() is not None:
                raise RuntimeError('MySQL exited during startup')
            result = execute(args, input="ALTER USER 'root'@'localhost' IDENTIFIED BY '" + self.password + "';", env=env, timeout=5)
            if result.returncode == 0:
                break
            time.sleep(1)
        else:
            raise RuntimeError('MySQL startup timed out')
        self.report['engines']['mysql'] = {'version': self.sql('mysql', None, 'SELECT VERSION();')[0][0], 'port': self.mysql_port}

        pg_data = self.directory / 'postgres-data'
        pwfile = self.directory / 'postgres-password.txt'
        pwfile.write_text(self.password, encoding='utf-8')
        result = execute([PG / 'bin/initdb.exe', '-D', pg_data, '-U', 'matrix', '-A', 'scram-sha-256',
                          '--pwfile=' + str(pwfile), '--no-locale', '-E', 'UTF8'], timeout=180)
        (self.directory / 'postgres-init.log').write_text(result.stdout + result.stderr, encoding='utf-8')
        if result.returncode:
            raise RuntimeError('PostgreSQL initialization failed; inspect private postgres-init.log')
        pg = self.start_process([PG / 'bin/postgres.exe', '-D', pg_data, '-h', '127.0.0.1', '-p', str(self.postgres_port)], self.directory, 'postgres')
        for _ in range(60):
            if pg.poll() is not None:
                raise RuntimeError('PostgreSQL exited during startup')
            try:
                version = self.sql('postgres', None, 'SELECT version();')[0][0]
                self.report['engines']['postgres'] = {'version': version, 'port': self.postgres_port}
                break
            except RuntimeError:
                time.sleep(1)
        else:
            raise RuntimeError('PostgreSQL startup timed out')
        sqlite_probe = self.directory / 'sqlite-version.go'
        sqlite_probe.write_text('package main\nimport ("database/sql"; "fmt"; _ "modernc.org/sqlite")\n'
                                'func main(){db,e:=sql.Open("sqlite",":memory:");if e!=nil{panic(e)};'
                                'defer db.Close();var v string;if e=db.QueryRow("SELECT sqlite_version()").Scan(&v);e!=nil{panic(e)};fmt.Print(v)}\n', encoding='utf-8')
        probe_env = os.environ.copy()
        probe_env['GOPROXY'] = 'https://goproxy.cn,direct'
        probe = execute([GO, 'run', sqlite_probe], env=probe_env, cwd=ROOT)
        if probe.returncode:
            raise RuntimeError('Cannot establish the application SQLite engine version')
        self.report['engines']['sqlite'] = {'version': probe.stdout.strip(), 'verification_client_version': sqlite3.sqlite_version,
                                            'application_driver': 'github.com/glebarez/sqlite v1.11.0 (modernc.org/sqlite v1.40.1)'}
        self.save()

    def create_database(self, engine, suffix):
        if engine == 'sqlite':
            return str(self.directory / (suffix + '.db'))
        name = self.run_id + '_' + suffix
        command = 'CREATE DATABASE ' + name
        if engine == 'mysql':
            command += ' CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci'
        self.sql(engine, None, command + ';')
        return name

    def dsn(self, engine, database):
        if engine == 'mysql':
            return f'root:{self.password}@tcp(127.0.0.1:{self.mysql_port})/{database}?charset=utf8mb4&parseTime=true'
        if engine == 'postgres':
            return f'postgres://matrix:{self.password}@127.0.0.1:{self.postgres_port}/{database}?sslmode=disable'
        return ''

    def call(self, path, data=None, token=''):
        headers = {'Content-Type': 'application/json'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        req = urllib.request.Request('http://127.0.0.1:19400' + path, headers=headers,
                                     data=None if data is None else json.dumps(data).encode())
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req, timeout=20) as response:
            result = json.load(response)
        if result.get('success') is False:
            raise RuntimeError(f'Fixture API {path} failed: {result.get("message")}')
        return result.get('data', result)

    def start_gateway(self, binary, engine, main_db, log_engine, log_db, directory, label):
        assert_free(19400)
        env = os.environ.copy()
        for name in ('SQL_DSN', 'LOG_SQL_DSN', 'SQLITE_PATH', 'REDIS_CONN_STRING', 'NODE_TYPE'):
            env.pop(name, None)
        env.update({'PORT': '19400', 'BIND_ADDRESS': '127.0.0.1', 'SESSION_SECRET': self.password,
                    'CRYPTO_SECRET': self.password, 'BATCH_UPDATE_ENABLED': 'false', 'GIN_MODE': 'release',
                    'SQL_MAX_IDLE_CONNS': '4', 'SQL_MAX_OPEN_CONNS': '10', 'NODE_TYPE': 'master',
                    'LOG_SQL_DSN': self.dsn(log_engine, log_db)})
        if engine == 'sqlite':
            env['SQLITE_PATH'] = main_db.replace('\\', '/') + '?_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)&_txlock=immediate'
        else:
            env['SQL_DSN'] = self.dsn(engine, main_db)
        process = self.start_process([binary], directory, label, env)
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError(f'{label} exited during startup; inspect its private log')
            try:
                self.call('/api/setup')
                return process
            except Exception:
                time.sleep(0.5)
        raise RuntimeError(f'{label} startup timed out')

    def seed(self, engine, main_db, log_engine, log_db):
        self.call('/api/setup', {'username': 'matrixadmin', 'password': self.password, 'confirmPassword': self.password,
                                 'SelfUseModeEnabled': False, 'DemoSiteEnabled': False})
        admin = self.call('/api/user/login', {'username': 'matrixadmin', 'password': self.password})['access_token']
        self.call('/api/user/', {'username': 'matrix_member', 'password': self.password, 'display_name': '矩阵成员', 'role': 1}, admin)
        member = self.call('/api/user/login', {'username': 'matrix_member', 'password': self.password})['access_token']
        self.call('/api/token/', {'name': 'matrix-image-key', 'remain_quota': 7654321, 'expired_time': -1,
                                 'group': 'default', 'unlimited_quota': False}, member)
        uid = int(self.sql(engine, main_db, "SELECT id FROM users WHERE username='matrix_member';")[0][0])
        token_id = int(self.sql(engine, main_db, f'SELECT id FROM tokens WHERE user_id={uid};')[0][0])
        self.sql(engine, main_db, f'UPDATE users SET quota=5000000123,used_quota=12345,request_count=1 WHERE id={uid};')
        self.sql(engine, main_db, f'UPDATE tokens SET used_quota=12345 WHERE id={token_id};')
        self.sql(log_engine, log_db,
                 f"INSERT INTO logs(user_id,created_at,type,content,username,token_name,model_name,quota,prompt_tokens,completion_tokens,token_id,other,request_id) "
                 f"VALUES({uid},1800000000,2,'matrix representative image ledger','matrix_member','matrix-image-key','gpt-image-2',12345,100,50,{token_id},'{{\"fixture\":true}}','matrix-image-request');")

    def snapshot(self, engine, main_db, log_engine, log_db):
        q = '`' if engine == 'mysql' else '"'
        return {'users': self.sql(engine, main_db, 'SELECT id,username,quota,used_quota,request_count FROM users ORDER BY id;'),
                'tokens': self.sql(engine, main_db, f'SELECT id,user_id,{q}key{q},remain_quota,used_quota FROM tokens ORDER BY id;'),
                'consume_logs': self.sql(log_engine, log_db, 'SELECT user_id,model_name,quota,prompt_tokens,completion_tokens,token_id,request_id FROM logs WHERE type=2 ORDER BY id;')}

    def schema(self, engine, database):
        if engine == 'sqlite':
            return self.sql(engine, database, "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name;")
        if engine == 'mysql':
            columns = self.sql(engine, database, "SELECT TABLE_NAME,COLUMN_NAME,COLUMN_TYPE,IS_NULLABLE,COALESCE(COLUMN_DEFAULT,'<null>') FROM information_schema.columns WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,ORDINAL_POSITION;")
            indexes = self.sql(engine, database, 'SELECT TABLE_NAME,INDEX_NAME,COLUMN_NAME,NON_UNIQUE,SEQ_IN_INDEX FROM information_schema.statistics WHERE TABLE_SCHEMA=DATABASE() ORDER BY TABLE_NAME,INDEX_NAME,SEQ_IN_INDEX;')
        else:
            columns = self.sql(engine, database, "SELECT table_name,column_name,data_type,is_nullable,COALESCE(column_default,'<null>') FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position;")
            indexes = self.sql(engine, database, "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname;")
        return [columns, indexes]

    def verify_unique(self, engine, main_db):
        user_error = self.sql(engine, main_db, "INSERT INTO users(username,password) SELECT username,password FROM users WHERE username='matrix_member';", expect_failure=True)
        q = '`' if engine == 'mysql' else '"'
        key_error = self.sql(engine, main_db, f'INSERT INTO tokens(user_id,{q}key{q},name) SELECT user_id,{q}key{q},name FROM tokens LIMIT 1;', expect_failure=True)
        if 'username' not in user_error.lower() or 'key' not in key_error.lower():
            raise AssertionError('Duplicate rejected for an unexpected constraint')

    def scenario(self, engine, scenario, baseline, candidate, iteration=1):
        suffix = scenario + ('_' + str(iteration) if iteration > 1 else '')
        directory = self.directory / (engine + '-' + suffix)
        directory.mkdir()
        main_db = self.create_database(engine, engine + '_' + suffix + '_main')
        # The gateway has one SQLITE_PATH global, so SQLite primary uses an
        # independently configured MySQL log DB. Team tests also cover separate
        # SQLite files through the native model fixture.
        log_engine = 'mysql' if engine == 'sqlite' else engine
        log_db = self.create_database(log_engine, engine + '_' + suffix + '_logs')
        first = baseline if scenario == 'release_upgrade' else candidate
        process = self.start_gateway(first, engine, main_db, log_engine, log_db, directory, 'seed')
        self.seed(engine, main_db, log_engine, log_db)
        self.stop(process)
        before = self.snapshot(engine, main_db, log_engine, log_db)
        (directory / 'before.json').write_text(json.dumps(before), encoding='utf-8')
        previous_schema = None
        for run in (1, 2):
            process = self.start_gateway(candidate, engine, main_db, log_engine, log_db, directory, f'candidate-{run}')
            after = self.snapshot(engine, main_db, log_engine, log_db)
            (directory / f'after-{run}.json').write_text(json.dumps(after), encoding='utf-8')
            if after != before:
                (directory / 'after.json').write_text(json.dumps(after), encoding='utf-8')
                changed = [key for key in before if before[key] != after[key]]
                raise AssertionError(f'{engine} {scenario} data changed after startup {run}: {changed}')
            self.verify_unique(engine, main_db)
            schema = [self.schema(engine, main_db), self.schema(log_engine, log_db)]
            if previous_schema is not None and schema != previous_schema:
                raise AssertionError(f'{engine} {scenario} schema changed on repeated startup')
            previous_schema = schema
            self.stop(process)
        if engine == 'sqlite':
            if self.sql(engine, main_db, 'PRAGMA integrity_check;') != [['ok']]:
                raise AssertionError('SQLite integrity_check failed')
        result = {'engine': engine, 'log_engine': log_engine, 'scenario': scenario, 'status': 'PASS',
                  'iteration': iteration,
                  'candidate_startups': 2, 'users': len(before['users']), 'personal_keys': len(before['tokens']),
                  'consume_log_count': len(before['consume_logs']), 'wallet_quota': 5000000123,
                  'consume_quota': 12345, 'ledger_keys_wallet_preserved': True,
                  'username_and_key_uniqueness': True, 'schema_and_indexes_idempotent': True}
        self.report['cases'].append(result)
        self.save()
        print(json.dumps(result), flush=True)

    def team_test(self, engine):
        case_id = engine + '_' + hashlib.sha256(self.test_pattern.encode()).hexdigest()[:8]
        env = os.environ.copy()
        env['GOPROXY'] = 'https://goproxy.cn,direct'
        env['TEAM_TEST_DB_ENGINE'] = engine
        if engine == 'sqlite':
            env.pop('TEAM_TEST_DSN', None)
            env.pop('TEAM_TEST_LOG_DSN', None)
        else:
            env['TEAM_TEST_DSN'] = self.dsn(engine, self.create_database(engine, case_id + '_team_main'))
            env['TEAM_TEST_LOG_DSN'] = self.dsn(engine, self.create_database(engine, case_id + '_team_logs'))
        # Controller fixtures use three established environment conventions.
        # Bind each to this owned engine; otherwise WS/health tests silently
        # fall back to SQLite while the report says MySQL or PostgreSQL.
        env['TEST_TASK_DB_DIALECT'] = engine
        env['TEST_MYSQL_DSN'] = env.get('TEAM_TEST_DSN','') if engine == 'mysql' else ''
        env['TEST_POSTGRES_DSN'] = env.get('TEAM_TEST_DSN','') if engine == 'postgres' else ''
        env['TEST_RESPONSES_SQL_DSN'] = env.get('TEAM_TEST_DSN','')
        env['TEST_RESPONSES_LOG_SQL_DSN'] = env.get('TEAM_TEST_LOG_DSN','')
        result = execute([GO, 'test', './controller', '-run', self.test_pattern, '-count=1', '-v'], env=env, cwd=ROOT, timeout=300)
        (self.directory / (case_id + '-team-test.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        executed = '--- PASS:' in result.stdout and 'no tests to run' not in result.stdout and '--- SKIP:' not in result.stdout
        passed_roots = re.findall(r'^--- PASS: (\w+) \(', result.stdout, re.MULTILINE)
        expected_roots = getattr(self, 'expected_test_roots', [])
        executed = executed and all(name in passed_roots for name in expected_roots)
        self.report['team_tests'][case_id] = {'engine': engine, 'status': 'PASS' if result.returncode == 0 and executed else 'FAIL',
                                            'command': 'go test ./controller -run ' + self.test_pattern + ' -count=1 -v',
                                            'executed_without_skips': executed,
                                            'passed_test_roots': passed_roots,
                                            'separate_primary_and_log_databases': True}
        self.save()
        print('TEAM', engine, self.test_pattern, self.report['team_tests'][case_id]['status'], flush=True)
        if result.returncode or not executed:
            raise RuntimeError(f'Team regression failed for {engine}; inspect private test log')

    def cleanup(self):
        # Ask the two owned servers to flush before terminating our handles.
        if 'postgres' in self.report['engines']:
            execute([PG / 'bin/pg_ctl.exe', '-D', self.directory / 'postgres-data', 'stop', '-m', 'fast', '-w'], timeout=40)
        if 'mysql' in self.report['engines']:
            try:
                self.sql('mysql', None, 'SHUTDOWN;')
            except Exception:
                pass
        for process in reversed(self.processes):
            self.stop(process)
        for handle in self.files:
            handle.close()
        self.report['owned_processes_stopped'] = all(p.poll() is not None for p in self.processes)
        self.save()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, default=PRIVATE / 'downloads/new-api-v1.0.0-rc.40.exe')
    parser.add_argument('--candidate', type=Path, default=ROOT / '.lab/staging/new-api-rc40-candidate.exe')
    parser.add_argument('--baseline-from-source', action='store_true', help='Baseline built from the exact official rc.40 tag archive')
    parser.add_argument('--engines', choices=('sqlite', 'mysql', 'postgres'), nargs='+', default=['sqlite', 'mysql', 'postgres'])
    parser.add_argument('--upgrade-repeats', type=int, default=1, help='Independent release-upgrade fixtures for each engine')
    parser.add_argument('--test-pattern', action='append', help='Repeat for controller tests; each gets a fresh database per engine')
    parser.add_argument('--report-path', type=Path, help='Non-secret verification report output')
    args = parser.parse_args()
    if args.upgrade_repeats < 1:
        parser.error('--upgrade-repeats must be positive')
    patterns = args.test_pattern or ['^TestTeamMVP$']
    matrix = Matrix(args.report_path, patterns[0])
    try:
        for source, label in ((args.baseline, 'baseline'), (args.candidate, 'candidate')):
            if not source.is_file():
                raise RuntimeError(f'{label} executable does not exist')
            destination = matrix.directory / (label + '.exe')
            shutil.copy2(source, destination)
            matrix.report[label + '_sha256'] = hashlib.sha256(destination.read_bytes()).hexdigest()
        if args.baseline_from_source:
            provenance = json.loads((PRIVATE / 'downloads/baseline-source.json').read_text(encoding='utf-8'))
            if provenance.get('commit') != '0aec08fee811ec6136828fda790551b49e410301':
                raise RuntimeError('Baseline source is not the official rc.40 tag')
            matrix.report['baseline'] = provenance
        else:
            checksum = (PRIVATE / 'downloads/checksums-windows.txt').read_text(encoding='utf-8-sig')
            entries = {line.split()[1].lstrip('*'): line.split()[0].lower()
                       for line in checksum.splitlines() if len(line.split()) == 2}
            if matrix.report['baseline_sha256'] != entries.get('new-api-v1.0.0-rc.40.exe'):
                raise RuntimeError('Official baseline checksum mismatch')
        matrix.start_databases()
        for engine in args.engines:
            for pattern in patterns:
                matrix.test_pattern = pattern
                matrix.team_test(engine)
            for scenario in ('fresh', 'release_upgrade'):
                count = args.upgrade_repeats if scenario == 'release_upgrade' else 1
                for iteration in range(1, count + 1):
                    matrix.scenario(engine, scenario, matrix.directory / 'baseline.exe', matrix.directory / 'candidate.exe', iteration)
        matrix.report['status'] = 'PASS'
    except Exception as exc:
        matrix.report['status'] = 'FAIL'
        matrix.report['error'] = str(exc).replace(matrix.password, '[REDACTED]')
        print(matrix.report['error'], flush=True)
    finally:
        matrix.cleanup()
    print('DATABASE_MATRIX', matrix.report['status'], flush=True)
    return 0 if matrix.report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())

import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import singlecore_additive_update as update


class AdditiveTests(unittest.TestCase):
    def test_reviewed_prefix_only(self):
        self.assertEqual(update.migration_map([{'filename': '006b_guard_users_allowed_groups.sql', 'checksum': 'a' * 64}]), {'006b_guard_users_allowed_groups.sql': 'a' * 64})
        baseline = {"305_fixture.sql": "a" * 64}
        first, second = update.REVIEWED
        self.assertEqual(update.validate_prefix(baseline, baseline), [])
        self.assertEqual(update.validate_prefix({**baseline, first: update.REVIEWED[first]}, baseline), [first])
        self.assertEqual(update.validate_prefix({**baseline, **update.REVIEWED}, baseline, True), [first, second])
        for current in ({**baseline, second: update.REVIEWED[second]}, {**baseline, first: "b" * 64},
                        {**baseline, "308_unknown.sql": "b" * 64}, {}):
            with self.assertRaises(update.UpdateError): update.validate_prefix(current, baseline)
        with self.assertRaisesRegex(update.UpdateError, "PENDING"):
            update.validate_prefix(baseline, baseline, True)

    def test_bytes_and_trim_match_go_not_universal_newlines(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'fixture.sql'
            raw = b' \r\nSELECT 1;\r\nSELECT 2;\r\n '
            path.write_bytes(raw)
            content, digest = update.migration_content(path)
            self.assertEqual(content, 'SELECT 1;\r\nSELECT 2;')
            self.assertEqual(digest, hashlib.sha256(raw.strip()).hexdigest())
            self.assertNotEqual(digest, hashlib.sha256(content.replace('\r\n', '\n').encode()).hexdigest())

    def test_no_false_schema_flag_or_unreviewed_files(self):
        for plan in ({'database_migrations': False}, {'database_migrations': True, 'migrations': []},
                     {'database_migrations': True, 'migrations': [{'filename': n, 'checksum': 'a' * 64} for n in update.REVIEWED]}):
            with self.assertRaises(update.UpdateError): update.reviewed_files(plan)

    def test_backup_requires_exact_restored_snapshot_and_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dump, schema, receipt = root / 'backup.dump', root / 'schema.sql', root / 'receipt.json'
            dump.write_bytes(b'synthetic backup')
            schema.write_bytes(b'synthetic schema')
            now = datetime.now(timezone.utc)
            baseline = [{'filename': '305_fixture.sql', 'checksum': 'a' * 64}]
            evidence = {'version': 1, 'kind': 'singlecore-pg-snapshot-restore', 'status': 'PASS',
                'captured_at': (now - timedelta(minutes=3)).isoformat(),
                'source': {**update.DATABASE, 'owner': update.DATABASE['user']},
                'schema_migrations': baseline, 'schema': {'sha256': 'b' * 64, 'table_count': 12},
                'schema_dump': {'path': str(schema), 'sha256': update.sha(schema)},
                'backup': {'path': str(dump), 'sha256': update.sha(dump), 'size_bytes': dump.stat().st_size, 'format': 'custom'},
                'restore': {'status': 'PASS', 'host': '127.0.0.1', 'port': 29490, 'database': 'realyu_backup_verify_synthetic',
                    'dump_sha256': update.sha(dump), 'verified_at': (now - timedelta(minutes=1)).isoformat(), 'table_count': 12,
                    'row_hashes_match': True, 'schema_hash_match': True, 'migrations_match': True}}
            plan = {'candidate_migrations': baseline + [{'filename': n, 'checksum': c} for n, c in update.REVIEWED.items()]}
            def verify(value):
                receipt.write_text(json.dumps(value), encoding='utf-8')
                plan['backup_evidence'] = {'path': str(receipt), 'sha256': update.sha(receipt)}
                return update.backup_evidence(plan, {'DATABASE_USER': update.DATABASE['user']}, now)
            self.assertEqual(verify(evidence)[1], {'305_fixture.sql': 'a' * 64})
            for section, field, value in [('restore', 'port', 28490), ('restore', 'dump_sha256', 'c' * 64),
                    ('restore', 'row_hashes_match', False), ('source', 'database', 'other'), ('backup', 'sha256', 'd' * 64)]:
                changed = copy.deepcopy(evidence)
                changed[section][field] = value
                with self.subTest(section=section, field=field), self.assertRaises(update.UpdateError): verify(changed)
            changed = copy.deepcopy(evidence)
            changed['captured_at'] = (now - timedelta(days=2)).isoformat()
            with self.assertRaises(update.UpdateError): verify(changed)
            plan['candidate_migrations'].append({'filename': '308_unknown.sql', 'checksum': 'e' * 64})
            with self.assertRaises(update.UpdateError): verify(evidence)

    def test_preflight_adjacent_version_authority_pins_and_no_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            binroot = root / 'bin'
            old_dir = binroot / 'previous-version'
            old_dir.mkdir(parents=True)
            old, new = old_dir / 'sub2api.exe', root / 'candidate.exe'
            old.write_bytes(b'old synthetic binary')
            new.write_bytes(b'new synthetic binary')
            env_file, manifest_file = root / 'env.json', root / 'manifest.json'
            authority, backup = root / 'authority.json', root / 'backup-receipt.json'
            authority.write_text('{"phase":"ACTIVE","opened":true}')
            backup.write_text('{}')
            env = {'SERVER_HOST': '127.0.0.1', 'SERVER_PORT': '18300',
                'DATABASE_HOST': update.DATABASE['host'], 'DATABASE_PORT': str(update.DATABASE['port']),
                'DATABASE_DBNAME': update.DATABASE['database'], 'DATABASE_USER': update.DATABASE['user'],
                'REDIS_DB': '1', 'REALYU_FUNDING_ENABLED': 'true'}
            env_file.write_text(json.dumps(env))
            manifest_file.write_text(json.dumps({'singlecore_api': {'exe': str(old), 'sha': update.sha(old), 'env_file': str(env_file)}}))
            plan = {'format': 1, 'operation_id': 'synthetic-additive-update', 'database_migrations': True,
                'drain_seconds': 30, 'authority_receipt': str(authority), 'manifest_path': str(manifest_file),
                'old_binary_sha256': update.sha(old), 'candidate_binary': str(new), 'new_binary_sha256': update.sha(new),
                'old_version': 'old', 'new_version': 'new', 'env_additions': {'REALYU_CUSTOMER_USD_TO_CNY': '7'},
                'installed_binary': str(binroot / 'next-version' / 'sub2api.exe'), 'new_env_file': str(root / 'new-env.json'),
                'maintenance_marker': str(root / 'maintenance.json'), 'runtime_directory': str(root / 'operation'),
                'backup_evidence': {'path': str(backup), 'sha256': update.sha(backup)}}
            pins = [Path(update.__file__).resolve(), Path(update.native.__file__).resolve(), Path(update.release_control.__file__).resolve(),
                    old, new, env_file, manifest_file, authority, backup]
            plan['verified_files'] = {str(p): update.sha(p) for p in pins}
            with patch.object(update, 'BIN_ROOT', binroot), patch.object(update, 'reviewed_files', return_value={}), \
                 patch.object(update, 'backup_evidence', return_value=({'source': {'server_version_num': '180006'}}, {'305_fixture.sql': 'a' * 64})), \
                 patch.object(update, 'Schema') as schema_class:
                schema_class.return_value.check.return_value = {'applied': []}
                before = {p: p.read_bytes() for p in pins if p.is_relative_to(root)}
                update.preflight(plan)
                self.assertFalse(Path(plan['installed_binary']).parent.exists())
                self.assertFalse(Path(plan['new_env_file']).exists())
                self.assertEqual(before, {p: p.read_bytes() for p in before})
                original = plan['installed_binary']
                for destination in (old_dir / 'nested-new' / 'sub2api.exe', root / 'outside-new' / 'sub2api.exe', old):
                    plan['installed_binary'] = str(destination)
                    with self.assertRaisesRegex(update.UpdateError, 'DESTINATION'): update.preflight(plan)
                plan['installed_binary'] = original
                env['DATABASE_PORT'] = '29490'
                env_file.write_text(json.dumps(env))
                plan['verified_files'][str(env_file)] = update.sha(env_file)
                with self.assertRaisesRegex(update.UpdateError, 'AUTHORITY_CONFIGURATION_CHANGED'): update.preflight(plan)
                env['DATABASE_PORT'] = str(update.DATABASE['port'])
                env_file.write_text(json.dumps(env))
                plan['verified_files'][str(env_file)] = update.sha(env_file)
                del plan['verified_files'][str(backup)]
                with self.assertRaisesRegex(update.UpdateError, 'REQUIRED_PIN_MISSING'): update.preflight(plan)

    def run_host(self, *, bad_new=False, orders=False, migration_error=False, busy=False):
        calls, receipts = [], []
        host = object.__new__(update.Host)
        host.plan = {'operation_id': 'synthetic-additive-update', 'drain_seconds': 1,
            'new_version': 'new', 'new_binary_sha256': 'n', 'old_version': 'old', 'old_binary_sha256': 'o'}
        host.stopped, host.schema_state = False, None
        host.schema = type('SchemaFake', (), {})()
        def check(complete=False, recovery=False):
            calls.append('schema-full' if complete else 'schema-prefix')
            if recovery and orders and 'apply-schema' in calls:
                raise update.UpdateError('MANAGED_ORDERS_EXIST_FORWARD_ONLY')
        def apply():
            calls.append('apply-schema')
            if migration_error: raise update.UpdateError('SCHEMA_LOCK_TIMEOUT')
        host.schema.check, host.schema.apply = check, apply
        def ready(version, digest):
            calls.append('ready-' + version)
            if version == 'new' and bad_new: raise update.UpdateError('SYNTHETIC_NEW_FAILED')
        current = [0]
        def sleep(delay): current[0] += delay
        replacements = {'owned': lambda: None, 'close_gate': lambda: calls.append('close'),
            'drain': lambda: {'maintenance': True, 'active_requests': int(busy), 'reserved_funding': 0, 'active_batch_jobs': 0},
            'stop': lambda: calls.append('stop'), 'start': lambda: calls.append('start'),
            'install_manifest': lambda: calls.append('install'), 'restore_native_manifest': lambda: calls.append('restore'),
            'ready': ready, 'open_gate': lambda name: calls.append('open'), 'receipt': lambda value: receipts.append(copy.deepcopy(value))}
        from contextlib import ExitStack
        with ExitStack() as stack:
            for name, callback in replacements.items():
                stack.enter_context(patch.object(update.native.Host, name, side_effect=callback))
            try:
                result = update.native.run_update(host.plan, host, lambda: current[0], sleep)
            except update.UpdateError as error:
                result = str(error)
        return calls, receipts, result

    def test_stop_precedes_migration_and_new_schema_check_precedes_open(self):
        calls, receipts, result = self.run_host()
        self.assertEqual(result['phase'], 'ACTIVE')
        self.assertLess(calls.index('stop'), calls.index('apply-schema'))
        self.assertLess(calls.index('apply-schema'), calls.index('install'))
        self.assertEqual(calls[-3:], ['ready-new', 'schema-full', 'open'])
        self.assertTrue(receipts[-1]['database_migrations'])
        self.assertFalse(receipts[-1]['database_restored'])

    def test_partial_migration_or_start_failure_restores_binary_but_keeps_schema(self):
        for settings in ({'migration_error': True}, {'bad_new': True}):
            calls, receipts, result = self.run_host(**settings)
            self.assertEqual(result, 'RECOVERED_SAME_NATIVE_AUTHORITY')
            self.assertIn('restore', calls)
            self.assertEqual(calls[-1], 'open')
            self.assertTrue(receipts[-1]['additive_schema_retained'])
            self.assertFalse(receipts[-1]['database_restored'])

    def test_new_managed_order_blocks_old_binary_recovery(self):
        calls, receipts, result = self.run_host(orders=True)
        self.assertEqual(result, 'NATIVE_RECOVERY_FAILED_NO_LEGACY_ROLLBACK')
        self.assertNotIn('restore', calls)
        self.assertNotIn('open', calls)
        self.assertEqual(receipts[-1]['phase'], 'FORWARD_RECOVERY_REQUIRED_GATE_RETAINED')

    def test_busy_requests_abort_without_schema_or_stop(self):
        calls, _, result = self.run_host(busy=True)
        self.assertEqual(result, 'ABORTED_BEFORE_STOP')
        self.assertNotIn('apply-schema', calls)
        self.assertNotIn('stop', calls)


@unittest.skipUnless(os.environ.get('REALYU_ADDITIVE_TEST_DSN') and os.environ.get('REALYU_ADDITIVE_TEST_MIGRATIONS'), 'isolated PG opt-in required')
class AdditivePostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        self.dsn = os.environ['REALYU_ADDITIVE_TEST_DSN']
        info = conninfo_to_dict(self.dsn)
        self.assertEqual(info.get('host'), '127.0.0.1')
        self.assertEqual(info.get('port'), '29490')
        self.assertEqual(info.get('dbname'), 'realyu_auth_e2e')
        self.schema_name = 'additive_test_' + uuid.uuid4().hex
        self.db = psycopg.connect(self.dsn, autocommit=True)
        self.db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema_name)))
        self.addCleanup(self.cleanup)
        self.db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema_name)))
        self.db.execute('''CREATE TABLE schema_migrations(filename TEXT PRIMARY KEY,checksum TEXT NOT NULL,applied_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE realyu_migration_state(singleton BOOLEAN PRIMARY KEY,phase TEXT);INSERT INTO realyu_migration_state VALUES(TRUE,'active');
            CREATE TABLE users(id BIGINT PRIMARY KEY);CREATE TABLE subscription_plans(id BIGINT PRIMARY KEY);CREATE TABLE payment_orders(id BIGINT PRIMARY KEY);
            CREATE TABLE realyu_teams(id BIGINT PRIMARY KEY);CREATE TABLE realyu_funding_subscriptions(id BIGINT PRIMARY KEY);
            INSERT INTO realyu_funding_subscriptions VALUES(12);CREATE TABLE realyu_team_members(team_id BIGINT,user_id BIGINT,weekly_cap_quota BIGINT);
            INSERT INTO realyu_team_members VALUES(1,2,3);''')
        self.baseline = {'305_fixture.sql': 'a' * 64}
        self.db.execute('INSERT INTO schema_migrations(filename,checksum) VALUES(%s,%s)', next(iter(self.baseline.items())))
        self.files = {n: str(Path(os.environ['REALYU_ADDITIVE_TEST_MIGRATIONS']) / n) for n in update.REVIEWED}
        self.events = []
        env = {'DATABASE_HOST': '127.0.0.1', 'DATABASE_PORT': '29490', 'DATABASE_DBNAME': 'realyu_auth_e2e', 'DATABASE_USER': info['user']}
        self.runner = update.Schema(env, self.files, self.baseline, self.events.append, self.connection, self.schema_name)

    def cleanup(self):
        from psycopg import sql
        self.db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema_name)))
        self.db.close()

    def connection(self, readonly=True):
        import psycopg
        from psycopg import sql
        conn = psycopg.connect(self.dsn, autocommit=True,
            options='-c statement_timeout=8000 -c lock_timeout=2000 -c default_transaction_read_only=' + ('on' if readonly else 'off'))
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema_name)))
        return conn

    def test_fresh_upgrade_repeat_preserves_rows_and_constraints(self):
        self.assertFalse(self.runner.check(recovery=True)['complete'])
        self.runner.apply()
        self.runner.apply()
        self.assertTrue(self.runner.check(complete=True, recovery=True)['complete'])
        self.assertEqual(self.db.execute('SELECT team_id,user_id,weekly_cap_quota,nickname FROM realyu_team_members').fetchall(), [(1, 2, 3, '')])
        self.assertEqual(self.db.execute('SELECT count(*) FROM schema_migrations').fetchone(), (3,))
        self.assertEqual(self.db.execute("SELECT nextval('realyu_purchase_subscription_id')").fetchone(), (13,))
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):
            self.db.execute('UPDATE realyu_team_members SET nickname=%s', ('x' * 41,))
        with self.connection(True) as conn:
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction): conn.execute('DELETE FROM users')

    def test_307_lock_timeout_retains_306_and_retry_finishes(self):
        with self.connection(False) as blocker:
            with blocker.transaction():
                blocker.execute('LOCK TABLE realyu_team_members IN ACCESS EXCLUSIVE MODE')
                start = time.monotonic()
                import psycopg
                with self.assertRaises(psycopg.errors.LockNotAvailable): self.runner.apply()
                self.assertLess(time.monotonic() - start, 6)
        self.assertEqual(self.runner.check(recovery=True)['applied'], [next(iter(update.REVIEWED))])
        self.assertEqual(self.events[-1]['applied'], [next(iter(update.REVIEWED))])
        self.runner.apply()
        self.assertTrue(self.runner.check(complete=True)['complete'])
        with self.connection(False) as conn:
            self.assertTrue(conn.execute('SELECT pg_try_advisory_lock(%s)', (update.ADVISORY_LOCK_ID,)).fetchone()[0])

    def test_advisory_timeout_no_schema_and_release(self):
        with self.connection(False) as blocker:
            blocker.execute('SELECT pg_advisory_lock(%s)', (update.ADVISORY_LOCK_ID,))
            start = time.monotonic()
            with self.assertRaisesRegex(update.UpdateError, 'SCHEMA_ADVISORY_LOCK_TIMEOUT'): self.runner.apply()
            self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(self.runner.check()['applied'], [])
        self.runner.apply()

    def test_existing_managed_order_forbids_downgrade_and_schema_apply(self):
        self.runner.apply()
        self.db.execute('INSERT INTO users VALUES(1);INSERT INTO payment_orders VALUES(1)')
        self.db.execute("INSERT INTO realyu_purchase_snapshots(order_id,actor_user_id,payer_user_id,plan_id,request_key,request_sha256,payment_source,entitlement) VALUES(1,1,1,1,'synthetic-request-key',%s,'merchant','{}')", ('f' * 64,))
        with self.assertRaisesRegex(update.UpdateError, 'MANAGED_ORDERS_EXIST_FORWARD_ONLY'): self.runner.check(recovery=True)
        with self.assertRaisesRegex(update.UpdateError, 'MANAGED_ORDERS_EXIST_FORWARD_ONLY'): self.runner.apply()
        self.assertEqual(self.db.execute('SELECT count(*) FROM realyu_purchase_snapshots').fetchone(), (1,))

    def test_identity_or_checksum_drift_fails_readonly(self):
        original = self.runner.env['DATABASE_DBNAME']
        self.runner.env['DATABASE_DBNAME'] = 'forbidden-other'
        with self.assertRaisesRegex(update.UpdateError, 'CONNECTED_DATABASE_IDENTITY_MISMATCH'): self.runner.check()
        self.runner.env['DATABASE_DBNAME'] = original
        self.db.execute("UPDATE schema_migrations SET checksum=%s", ('b' * 64,))
        with self.assertRaisesRegex(update.UpdateError, 'HISTORICAL_SCHEMA_CHANGED'): self.runner.apply()
        self.assertIsNone(self.db.execute("SELECT to_regclass('realyu_purchase_snapshots')").fetchone()[0])


if __name__ == '__main__':
    unittest.main()

"""Cutover failure-path tests: synthetic SCM, real temporary SQLite, no host writes."""
from contextlib import ExitStack, closing, redirect_stdout
import gc
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import types
import unittest
from unittest import mock

import host_cutover as cut


FINANCIAL_TABLES = (
    'tokens', 'user_subscriptions', 'subscription_plans', 'subscription_orders',
    'subscription_pre_consume_records', 'billing_orders', 'top_ups', 'redemptions',
    'workspace_team_accounts', 'workspace_members', 'workspace_teams',
    'workspace_personal_keys', 'workspace_member_weekly_usages', 'workspace_funding_migrations',
)


class CutoverFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='realyu-cutover-test-')
        self.addCleanup(self.temp.cleanup)
        self.addCleanup(gc.collect)
        self.root = Path(self.temp.name)
        self.private = self.root / 'private'
        self.run = self.root / 'operation'
        self.state = self.root / 'state'
        self.queue = self.root / 'queue'
        for path in (self.private, self.run, self.state, self.queue):
            path.mkdir()
        self.database = self.private / 'new-api.db'
        with closing(sqlite3.connect(self.database)) as db, db:
            for name in FINANCIAL_TABLES:
                db.execute(f'CREATE TABLE "{name}" (id INTEGER PRIMARY KEY, quota INTEGER NOT NULL)')
                db.execute(f'INSERT INTO "{name}" VALUES (1,1000)')
            db.execute('ALTER TABLE workspace_members ADD COLUMN user_id INTEGER DEFAULT 1')
            db.execute('ALTER TABLE workspace_members ADD COLUMN team_id INTEGER DEFAULT 1')
            db.execute('ALTER TABLE workspace_members ADD COLUMN status INTEGER DEFAULT 0')
            db.execute('CREATE TABLE users(id INTEGER PRIMARY KEY, quota INTEGER, used_quota INTEGER, request_count INTEGER, "group" TEXT, status INTEGER, deleted_at TEXT)')
            db.execute('INSERT INTO users VALUES(1,1000,0,0,"default",1,NULL)')
            db.execute('CREATE TABLE tasks(id INTEGER PRIMARY KEY, status TEXT)')
        self.candidate_db = self.root / 'rehearsed.db'
        self.candidate_db.write_bytes(self.database.read_bytes())
        self.xmls = [self.root / 'api.xml', self.root / 'bridge.xml']
        for path in self.xmls:
            path.write_text('<service><id>synthetic-test</id><env name="KEEP" value="unchanged"/></service>')
        self.old_bridge = self.root / 'image_bridge.py'
        self.old_bridge.write_text('# legacy test bridge\n')
        self.new_bridge = self.root / 'edge_proxy.py'
        self.new_bridge.write_text('# candidate test bridge\n')
        self.old_binary = self.private / 'old.exe'
        self.old_binary.write_bytes(b'not executable: old fixture')
        self.new_binary = self.root / 'new.exe'
        self.new_binary.write_bytes(b'not executable: new fixture')
        self.config = self.private / 'credentials.json'
        cut.save(self.config, {'binary_name': self.old_binary.name, 'session_secret': 'synthetic-only'})
        self.bindings = self.root / 'bindings.json'
        cut.save(self.bindings, {'version': 1, 'namespace': 'synthetic', 'pools': [{'channel_id': 4}]})
        self.generation = cut.digest(self.bindings)
        task = {'realyu_user_id': 1, 'workspace_team_id': 0, 'channel_id': 4}
        (self.queue / self.generation).mkdir()
        cut.save(self.queue / self.generation / '1-0-4.json', {'version': 1, 'config_sha256': self.generation, 'task': task})
        snapshot = self.state / (self.generation + '.queue.json')
        cut.save(snapshot, {'version': 1, 'tasks': [task]})
        cut.save(self.state / (self.generation + '.json'), {'version': 1, 'config_sha256': self.generation,
            'queue_sha256': cut.digest(snapshot), 'tasks': {'1:0:4': {'status': 'DONE', 'attempts': 1}}})
        cut.save(self.state / 'heartbeat.json', {'version': 1, 'config_sha256': self.generation,
            'status': 'running', 'credit_status': 'disabled', 'last_seen': '2099-01-01T00:00:00Z'})
        self.route_receipt = self.run / 'route.json'
        cut.save(self.route_receipt, {'phase': 'disabled_route_ready', 'channel_id': 4})
        self.route_plan = self.run / 'route-plan.json'
        cut.save(self.route_plan, {'receipt': str(self.route_receipt)})
        supply = self.run / 'supply.json'
        cut.save(supply, {'phase': 'access_only_ready'})
        self.plan = {
            'production_private': str(self.private), 'operation_directory': str(self.run),
            'operation_id': 'synthetic-operation', 'old_version': 'old-test-version', 'new_version': 'new-test-version',
            'verified_files': {str(self.new_binary): cut.digest(self.new_binary), str(self.new_bridge): cut.digest(self.new_bridge)},
            'supply_receipt': str(supply), 'bindings_file': str(self.bindings), 'state_directory': str(self.state),
            'queue_directory': str(self.queue), 'minimum_prepared_subjects': 1,
            'route_plan': str(self.route_plan), 'route_receipt': str(self.route_receipt),
            'new_binary_name': 'new-installed.exe', 'new_binary': str(self.new_binary),
            'new_binary_sha256': cut.digest(self.new_binary), 'api_xml': str(self.xmls[0]), 'bridge_xml': str(self.xmls[1]),
            'edge_proxy': str(self.new_bridge), 'production_bridge': str(self.old_bridge),
            'sub2api_base_url': 'http://127.0.0.1:1',
            'backup_files': {'credentials.json': str(self.config), 'api.xml': str(self.xmls[0]),
                             'bridge.xml': str(self.xmls[1]), 'image_bridge.py': str(self.old_bridge)},
            'rehearsal_database': str(self.candidate_db), 'candidate_database': str(self.candidate_db),
            'validated_candidate_database': str(self.candidate_db), 'channel_id': 4,
        }
        self.plan_file = self.run / 'plan.json'
        self.marker = self.private / 'release-maintenance.json'
        self.events = []
        self.scm_times = []
        self.clock = 1000.
        self.public_verdict = 'PASS'
        self.public_charge = 0
        self.verdict_written = False
        self.fail_route_activation = False
        self.fail_new_bridge_start = False
        self.mutate_schema_on_new_start = False
        self.pending_rollback_refund = 0
        self.rollback_started_at = None
        self.refund_at = None
        self.serving_version = self.plan['old_version']

    def fake_service(self, name, action):
        self.events.append((name, action))
        self.scm_times.append((name, action, self.clock))
        self.assertTrue(self.marker.exists(), 'SCM mutation must happen behind admission gate')
        if name == 'RealYuApi' and action == 'Start':
            binary = cut.read(self.config)['binary_name']
            self.serving_version = self.plan['new_version'] if binary == self.plan['new_binary_name'] else self.plan['old_version']
            if self.serving_version == self.plan['new_version'] and self.mutate_schema_on_new_start:
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute('CREATE TABLE unexpected_migration(id INTEGER PRIMARY KEY)')
        if name == 'RealYuBridge' and action == 'Start' and self.fail_new_bridge_start and self.serving_version == self.plan['new_version']:
            self.fail_new_bridge_start = False
            raise RuntimeError('synthetic bridge start failure')

    def fake_fetch(self, url):
        if ':18302/' in url:
            return {'maintenance': self.marker.exists(), 'active_requests': 0, 'pid': 123, 'protocol': 1}
        if '/api/status' in url:
            return {'success': True, 'data': {'version': self.serving_version}}
        raise AssertionError('Unexpected network access: ' + url)

    def fake_route(self, action, path, log):
        self.events.append(('route', action))
        self.assertTrue(self.marker.exists())
        if action == 'activate':
            cut.save(self.route_receipt, {'phase': 'activating'})
            if self.fail_route_activation:
                raise RuntimeError('synthetic partial route activation')
            cut.save(self.route_receipt, {'phase': 'active'})
        elif action == 'restore-routing':
            self.assertTrue(cut.read(path)['refresh_ownership_returned'])
            self.assertEqual(cut.read(self.config)['binary_name'], self.old_binary.name)
            cut.save(self.route_receipt, {'phase': 'restored'})
        else:
            raise AssertionError('Unexpected route operation')

    def advance(self, seconds):
        self.clock += seconds
        receipt = self.run / 'cutover-receipt.json'
        if receipt.exists() and cut.read(receipt)['phase'] == 'rollback_started' and self.pending_rollback_refund:
            if self.rollback_started_at is None:
                self.rollback_started_at = self.clock
            if self.clock >= self.rollback_started_at + 5:
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute('UPDATE users SET quota=quota+?, used_quota=used_quota-? WHERE id=1',
                               (self.pending_rollback_refund, self.pending_rollback_refund))
                self.refund_at = self.clock
                self.pending_rollback_refund = 0
        if receipt.exists() and cut.read(receipt)['phase'] == 'provisional_open' and not self.verdict_written:
            self.verdict_written = True
            if self.public_charge:
                with closing(sqlite3.connect(self.database)) as db, db:
                    db.execute('UPDATE users SET quota=quota-?, used_quota=used_quota+?, request_count=request_count+1 WHERE id=1', (self.public_charge, self.public_charge))
                    db.execute('UPDATE tokens SET quota=quota-? WHERE id=1', (self.public_charge,))
            cut.save(self.run / 'public-acceptance-verdict.json', {'operation_id': self.plan['operation_id'],
                'version': self.plan['new_version'], 'binary_sha256': self.plan['new_binary_sha256'], 'status': self.public_verdict})

    def execute(self):
        cut.save(self.plan_file, self.plan)
        admin = types.SimpleNamespace(windll=types.SimpleNamespace(shell32=types.SimpleNamespace(IsUserAnAdmin=lambda: True)))
        with ExitStack() as stack:
            stack.enter_context(mock.patch.object(cut, 'ctypes', admin))
            stack.enter_context(mock.patch.object(cut, 'service', self.fake_service))
            stack.enter_context(mock.patch.object(cut, 'fetch', self.fake_fetch))
            stack.enter_context(mock.patch.object(cut, 'route', self.fake_route))
            stack.enter_context(mock.patch.object(cut, 'new_admin', return_value=types.SimpleNamespace(
                token='synthetic', call=lambda *args: {'http_stats': {'active_connections': 0}})))
            stack.enter_context(mock.patch.object(cut, 'verify_gateway_process', side_effect=lambda private, exe: self.assertTrue(Path(exe).is_file())))
            stack.enter_context(mock.patch.object(cut, 'ps', side_effect=self.fake_ps))
            stack.enter_context(mock.patch.object(cut.time, 'monotonic', lambda: self.clock))
            stack.enter_context(mock.patch.object(cut.time, 'sleep', self.advance))
            stack.enter_context(mock.patch.object(cut.sys, 'argv', ['host_cutover.py', '--plan', str(self.plan_file)]))
            stack.enter_context(redirect_stdout(io.StringIO()))
            cut.main()

    def fake_ps(self, command):
        if command.startswith('$xmlAcl=Get-Acl -LiteralPath '):
            self.assertIn(str(self.root), command)
            return ''  # ACL only; never launch PowerShell or touch host services.
        raise AssertionError('Unexpected PowerShell operation in lifecycle test')

    def assert_restored(self):
        self.assertEqual(cut.read(self.config)['binary_name'], self.old_binary.name)
        self.assertEqual(self.old_bridge.read_text(), '# legacy test bridge\n')
        self.assertEqual(cut.read(self.route_receipt)['phase'], 'restored')
        self.assertFalse(self.marker.exists())
        self.assertEqual(cut.read(self.run / 'cutover-receipt.json')['phase'], 'rolled_back')
        for path in self.xmls:
            self.assertNotIn('REALYU_UPSTREAM_DRIVER', path.read_text())

    def test_real_sqlite_ledger_fingerprint_observes_mutation_without_writing(self):
        raw_before = self.database.read_bytes()
        first = cut.money_state(self.database)
        self.assertEqual(self.database.read_bytes(), raw_before)
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('UPDATE users SET quota=900, used_quota=100, request_count=1 WHERE id=1')
        after = cut.money_state(self.database)
        self.assertNotEqual(first['users_financial'], after['users_financial'])
        self.assertEqual(first['tokens'], after['tokens'])

    def test_success_requires_current_prepared_map_and_verdict(self):
        before = cut.money_state(self.database)
        self.execute()
        self.assertEqual(cut.read(self.run / 'cutover-receipt.json')['phase'], 'published')
        self.assertEqual(cut.money_state(self.database), before)
        self.assertFalse(self.marker.exists())
        self.assertEqual(cut.read(self.config)['binary_name'], self.plan['new_binary_name'])
        self.assertEqual(self.old_bridge.read_text(), '# candidate test bridge\n')

    def test_public_failure_restores_program_and_routes_preserving_new_charge(self):
        self.public_verdict = 'FAIL'
        self.public_charge = 17
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assert_restored()
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT quota,used_quota,request_count FROM users WHERE id=1').fetchone(), (983,17,1))
        with closing(sqlite3.connect(self.run / 'cutover-backup/customer-ledger.db')) as db:
            self.assertEqual(db.execute('SELECT quota,used_quota FROM users WHERE id=1').fetchone(), (1000,0))

    def test_partial_route_activation_is_restored_before_gate_opens(self):
        self.fail_route_activation = True
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assert_restored()
        self.assertIn(('route', 'restore-routing'), self.events)

    def test_new_bridge_start_failure_restores_both_services_and_original_inputs(self):
        self.fail_new_bridge_start = True
        before = cut.money_state(self.database)
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assert_restored()
        self.assertEqual(cut.money_state(self.database), before)
        self.assertEqual(self.events[-1], ('route', 'restore-routing'))

    def test_wrong_config_generation_fails_before_gate_or_scm(self):
        path = self.state / (self.generation + '.json')
        progress = cut.read(path)
        progress['config_sha256'] = '0' * 64
        cut.save(path, progress)
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.events, [])

    def test_preexisting_public_verdict_must_not_be_reused(self):
        cut.save(self.run / 'public-acceptance-verdict.json', {'operation_id': 'earlier-operation',
            'version': self.plan['new_version'], 'binary_sha256': self.plan['new_binary_sha256'], 'status': 'PASS'})
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.events, [])

    def test_candidate_schema_mismatch_rejected_without_production_mutations(self):
        with closing(sqlite3.connect(self.candidate_db)) as db, db:
            db.execute('CREATE TABLE incompatible_candidate(id INTEGER PRIMARY KEY)')
        with self.assertRaisesRegex(RuntimeError, 'schemas differ'):
            self.execute()
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.events, [])

    def test_unexpected_runtime_schema_change_keeps_gate_and_requires_recovery(self):
        self.mutate_schema_on_new_start = True
        with self.assertRaisesRegex(RuntimeError, 'schema changed'):
            self.execute()
        self.assertTrue(self.marker.exists())
        self.assertEqual(cut.read(self.run / 'cutover-receipt.json')['phase'], 'recovery_required_schema_changed')
        self.assertNotIn(('route', 'restore-routing'), self.events)
        self.assertEqual(cut.read(self.config)['binary_name'], self.plan['new_binary_name'])

    def test_rollback_waits_for_async_refund_and_stable_ledger_before_stop(self):
        self.public_verdict = 'FAIL'
        self.public_charge = 17
        self.pending_rollback_refund = 2
        with self.assertRaises(RuntimeError):
            self.execute()
        self.assert_restored()
        self.assertIsNotNone(self.refund_at)
        api_stops = [at for name, action, at in self.scm_times if name == 'RealYuApi' and action == 'Stop']
        self.assertEqual(len(api_stops), 2)
        self.assertGreaterEqual(api_stops[-1] - self.refund_at, 15)
        with closing(sqlite3.connect(self.database)) as db:
            self.assertEqual(db.execute('SELECT quota,used_quota FROM users WHERE id=1').fetchone(), (985,15))

    def test_new_committed_customer_missing_from_queue_blocks_before_gate(self):
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute('INSERT INTO users VALUES(2,500,0,0,"default",1,NULL)')
        with self.assertRaisesRegex(RuntimeError, 'after prewarming'):
            self.execute()
        self.assertFalse(self.marker.exists())
        self.assertEqual(self.events, [])


if __name__ == '__main__':
    unittest.main()

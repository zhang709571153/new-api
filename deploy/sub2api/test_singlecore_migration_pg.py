"""Opt-in native-schema PostgreSQL rehearsal tests; never discover production.

Requires REALYU_MIGRATION_TEST_TARGET, REALYU_MIGRATION_TEST_SNAPSHOT and
REALYU_MIGRATION_TEST_SHA256. Target must already have native migrations 300-302
and an explicit standard group mapping. All application processes must be stopped.
"""
import copy
import json
import os
from pathlib import Path
import unittest
import uuid

import singlecore_migrate as m


@unittest.skipUnless(os.environ.get("REALYU_MIGRATION_TEST_TARGET"), "explicit isolated target required")
class NativeSchemaMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.pg = psycopg
        cls.config = json.loads(Path(os.environ["REALYU_MIGRATION_TEST_TARGET"]).read_text(encoding="utf-8-sig"))
        m.validate_target(cls.config)
        cls.source_sha = os.environ["REALYU_MIGRATION_TEST_SHA256"]
        cls.tables = m.load_snapshot(Path(os.environ["REALYU_MIGRATION_TEST_SNAPSHOT"]), cls.source_sha)
        cls.prefix = "pg-test-" + uuid.uuid4().hex
        cls.receipt = m.apply_snapshot(cls.tables, cls.source_sha, cls.config, cls.prefix + "-s0")

    def connection(self):
        p=self.config
        return self.pg.connect(host=p['host'], port=p['port'], dbname=p['database'], user=p['user'], password=p['password'])

    def test_same_operation_is_exact_noop_and_conflicting_inputs_fail(self):
        replay=m.apply_snapshot(self.tables,self.source_sha,self.config,self.prefix+'-s0')
        self.assertTrue(replay['idempotent_replay'])
        with self.assertRaisesRegex(m.MigrationError,'OPERATION_ID_INPUT_CONFLICT'):
            m.apply_snapshot(self.tables,'a'*64,self.config,self.prefix+'-s0')

    def test_full_native_schema_wallet_and_entitlement_receipt(self):
        self.assertEqual('exact',self.receipt['user_and_key_reconciliation'])
        self.assertEqual('exact',self.receipt['team_entitlement_period_reconciliation'])
        self.assertEqual(0,self.receipt['native_defaults_granted'])
        self.assertFalse(self.receipt['activated'])
        with self.connection() as c:
            self.assertEqual(len(self.tables['users']),c.execute('SELECT count(*) FROM realyu_legacy_user_metadata').fetchone()[0])
            self.assertEqual(0,c.execute("SELECT count(*) FROM realyu_legacy_user_metadata m JOIN users u ON m.user_id=u.id WHERE m.source_role=10 AND u.role='admin'").fetchone()[0])

    def test_partial_import_failure_rolls_back_every_wallet_and_receipt(self):
        changed=copy.deepcopy(self.tables)
        changed['users'][0]['quota']+=1
        with self.connection() as c:
            before=c.execute('SELECT id,balance FROM users ORDER BY id').fetchall()
            c.execute("CREATE FUNCTION realyu_test_reject_import() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'synthetic migration fault'; END $$")
            c.execute('CREATE TRIGGER realyu_test_reject BEFORE INSERT OR UPDATE ON realyu_funding_subscriptions FOR EACH ROW EXECUTE FUNCTION realyu_test_reject_import()')
        try:
            with self.assertRaises(self.pg.errors.RaiseException):
                m.apply_snapshot(changed,self.source_sha,self.config,self.prefix+'-rollback')
        finally:
            with self.connection() as c:
                c.execute('DROP TRIGGER realyu_test_reject ON realyu_funding_subscriptions')
                c.execute('DROP FUNCTION realyu_test_reject_import()')
        with self.connection() as c:
            self.assertEqual(before,c.execute('SELECT id,balance FROM users ORDER BY id').fetchall())
            self.assertEqual(0,c.execute('SELECT count(*) FROM realyu_migration_runs WHERE operation_id=%s',(self.prefix+'-rollback',)).fetchone()[0])

    def test_active_target_rejects_a_new_snapshot(self):
        with self.connection() as c: c.execute("UPDATE realyu_migration_state SET phase='active'")
        try:
            with self.assertRaisesRegex(m.MigrationError,'TARGET_NOT_STAGING_OR_WRONG_INSTALLATION'):
                m.apply_snapshot(self.tables,self.source_sha,self.config,self.prefix+'-active')
        finally:
            with self.connection() as c: c.execute("UPDATE realyu_migration_state SET phase='staging'")

    def test_business_reservation_blocks_overwriting_snapshot(self):
        marker=self.prefix+'-business'
        with self.connection() as c:
            k=c.execute('SELECT api_key_id,actor_user_id FROM realyu_key_scopes WHERE team_id IS NULL LIMIT 1').fetchone()
            c.execute("INSERT INTO realyu_funding_requests(request_id,api_key_id,actor_user_id,payer_user_id,request_fingerprint,status,target_quota,allow_wallet_overflow,member_cap_exempt) VALUES(%s,%s,%s,%s,'synthetic','reserved',0,TRUE,FALSE)",(marker,k[0],k[1],k[1]))
        try:
            with self.assertRaisesRegex(m.MigrationError,'TARGET_ALREADY_HAS_BUSINESS_WRITES'):
                m.apply_snapshot(self.tables,self.source_sha,self.config,self.prefix+'-blocked')
        finally:
            with self.connection() as c: c.execute('DELETE FROM realyu_funding_requests WHERE request_id=%s',(marker,))

    def test_repeat_snapshot_updates_absolute_not_additive(self):
        receipt=m.apply_snapshot(self.tables,self.source_sha,self.config,self.prefix+'-s1')
        self.assertEqual(self.receipt['counts'],receipt['counts'])
        with self.connection() as c:
            expected=sum(m.usd(u['quota']) for u in self.tables['users'])
            self.assertEqual(expected,c.execute('SELECT SUM(u.balance) FROM users u JOIN realyu_legacy_identities i ON i.user_id=u.id WHERE i.source_user_id IS NOT NULL').fetchone()[0])


if __name__=='__main__': unittest.main()

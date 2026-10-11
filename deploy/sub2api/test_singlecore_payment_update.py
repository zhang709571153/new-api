import os
from pathlib import Path
import unittest
import uuid
from unittest.mock import patch
import singlecore_payment_update as update


class PrecisionContractTests(unittest.TestCase):
    def test_only_exact_pinned_migrations(self):
        for plan in ({'database_migrations': False}, {'database_migrations': True, 'migrations': []},
                     {'database_migrations': True, 'migrations': [{'filename': '310_unknown.sql'}]}):
            with self.assertRaises(update.UpdateError): update.reviewed_files(plan)

    def test_exact_prefix_and_no_missing_changed_or_extra_history(self):
        baseline={'308_fixture.sql':'a'*64}
        with patch.object(update,'BASELINE_COUNT',1), patch.object(update,'BASELINE_SHA256',update.digest(baseline)):
            self.assertEqual(update.validate_prefix(baseline,baseline),[])
            self.assertEqual(update.validate_prefix({**baseline,**update.REVIEWED},baseline,True),list(update.REVIEWED))
            self.assertEqual(update.validate_prefix({**baseline,update.MIGRATION:update.REVIEWED[update.MIGRATION]},baseline),[update.MIGRATION])
            with self.assertRaises(update.UpdateError): update.validate_prefix({**baseline,update.ADMIN_MIGRATION:update.REVIEWED[update.ADMIN_MIGRATION]},baseline)
            for bad in ({}, {'308_fixture.sql':'b'*64}, {**baseline,'310_unknown.sql':'c'*64}, {**baseline,update.MIGRATION:'c'*64}):
                with self.assertRaises(update.UpdateError): update.validate_prefix(bad,baseline)
            with self.assertRaises(update.UpdateError): update.validate_prefix(baseline,baseline,True)


@unittest.skipUnless(os.environ.get('REALYU_PRECISION_TEST_DSN') and os.environ.get('REALYU_PRECISION_TEST_MIGRATIONS'), 'isolated PostgreSQL required')
class PrecisionPostgresTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.conninfo import conninfo_to_dict
        self.dsn=os.environ['REALYU_PRECISION_TEST_DSN']
        self.params=conninfo_to_dict(self.dsn)
        self.assertEqual((self.params['host'],self.params['port'],self.params['dbname']),('127.0.0.1','29490','realyu_funding_e2e'))
        self.namespace='precision_'+uuid.uuid4().hex
        self.base=psycopg.connect(self.dsn,autocommit=True)
        self.base.execute('CREATE SCHEMA '+self.namespace)
        def connect(readonly=True):
            return psycopg.connect(self.dsn,autocommit=True,options='-c search_path='+self.namespace+' -c statement_timeout=3000 -c lock_timeout=1000 -c default_transaction_read_only='+('on' if readonly else 'off'))
        self.connect=connect
        root=Path(os.environ['REALYU_PRECISION_TEST_MIGRATIONS'])
        self.baseline={p.name:update.additive.migration_content(p)[1] for p in root.glob('*.sql') if int(p.name[:3])<=308}
        update.validate_prefix(self.baseline,self.baseline)
        with connect(False) as conn:
            conn.execute('CREATE TABLE schema_migrations(filename TEXT PRIMARY KEY,checksum TEXT NOT NULL); CREATE TABLE realyu_migration_state(singleton BOOLEAN PRIMARY KEY,phase TEXT); INSERT INTO realyu_migration_state VALUES(true,\'active\'); CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT); INSERT INTO settings VALUES(\'BALANCE_PAYMENT_DISABLED\',\'true\'); CREATE TABLE payment_orders(id BIGINT PRIMARY KEY,amount NUMERIC(20,2),bonus_amount NUMERIC(20,2),refund_amount NUMERIC(20,2),pay_amount NUMERIC(20,2),order_type TEXT,status TEXT);')
            for name,value in self.baseline.items():conn.execute('INSERT INTO schema_migrations VALUES(%s,%s)',(name,value))
            conn.execute("INSERT INTO payment_orders VALUES(1,999999999999999999.99,0.01,0.02,0.03,'subscription','COMPLETED')")
            conn.execute("CREATE TABLE users(id BIGINT PRIMARY KEY,balance NUMERIC(20,8)); INSERT INTO users VALUES(1,10.00000001); CREATE TABLE api_keys(id BIGINT PRIMARY KEY,user_id BIGINT); INSERT INTO api_keys VALUES(11,1); CREATE TABLE realyu_teams(id BIGINT PRIMARY KEY,owner_user_id BIGINT,name TEXT); INSERT INTO realyu_teams VALUES(103,1,'Synthetic team'); CREATE TABLE realyu_team_members(team_id BIGINT,user_id BIGINT); INSERT INTO realyu_team_members VALUES(103,1); CREATE TABLE realyu_key_scopes(api_key_id BIGINT,team_id BIGINT); INSERT INTO realyu_key_scopes VALUES(11,103); CREATE TABLE realyu_funding_subscriptions(id BIGINT PRIMARY KEY,weekly_used BIGINT); INSERT INTO realyu_funding_subscriptions VALUES(201,151);")
        env={'DATABASE_HOST':'127.0.0.1','DATABASE_PORT':'29490','DATABASE_DBNAME':'realyu_funding_e2e','DATABASE_USER':self.params['user']}
        self.schema=update.Schema(env,{name:str(root/name) for name in update.REVIEWED},self.baseline,connect=connect,schema_name=self.namespace)

    def tearDown(self):
        self.base.execute('DROP SCHEMA '+self.namespace+' CASCADE')
        self.base.close()

    def test_widen_exact_values_idempotence_and_old_binary_compatible_schema(self):
        with self.connect() as conn:
            before=self.schema.financial_rows(conn)
            entitlements=self.schema.entitlement_rows(conn)
        self.schema.apply()
        self.schema.apply()
        self.assertTrue(self.schema.check(True)['complete'])
        with self.connect() as conn:
            self.assertEqual(before,self.schema.financial_rows(conn))
            self.assertEqual(entitlements,self.schema.entitlement_rows(conn))
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],302)
        with self.connect(False) as conn:
            conn.execute("INSERT INTO payment_orders VALUES(2,0.001428,0,0,0.01,'subscription','COMPLETED')")
            self.assertEqual(str(conn.execute('SELECT amount FROM payment_orders WHERE id=2').fetchone()[0]),'0.00142800')
        self.assertTrue(self.schema.check()['complete'])

    def test_requires_closed_recharge_and_reconciled_orders(self):
        with self.connect(False) as conn:conn.execute("UPDATE settings SET value='false'")
        with self.assertRaisesRegex(update.UpdateError,'RECHARGE_MUST_REMAIN_CLOSED'):self.schema.apply()
        with self.connect(False) as conn:
            conn.execute("UPDATE settings SET value='true'")
            conn.execute("UPDATE payment_orders SET order_type='balance'")
        for status in ('PENDING','PAID','RECHARGING','FAILED','EXPIRED','CANCELLED','COMPLETED'):
            with self.connect(False) as conn:conn.execute('UPDATE payment_orders SET status=%s',(status,))
            with self.assertRaisesRegex(update.UpdateError,'EXISTING_RECHARGE'):self.schema.apply()
        with self.connect() as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM schema_migrations').fetchone()[0],300)

    def test_failed_postcheck_rolls_back_ddl_and_receipt(self):
        inspect=self.schema.inspect
        def fail(conn,complete=False):
            result=inspect(conn,complete)
            if complete:raise update.UpdateError('SYNTHETIC_POSTCHECK_FAILURE')
            return result
        with patch.object(self.schema,'inspect',fail), self.assertRaisesRegex(update.UpdateError,'SYNTHETIC_POSTCHECK'):
            self.schema.apply()
        self.assertFalse(self.schema.check()['complete'])


if __name__=='__main__': unittest.main()

"""Synthetic supply-only tests. PG requires an explicit private e2e config.

No real upstream traffic; every PG test creates/drops only its random schemas.
"""
import copy
from decimal import Decimal
import json
import os
from pathlib import Path
import tempfile
import unittest
import uuid

import singlecore_supply as s


class BoundaryTests(unittest.TestCase):
    def test_rejects_production_and_remote_targets(self):
        for cfg in ({"host": "example.com"}, {"host": "127.0.0.1", "port": 28490, "mode": "rehearsal", "database": "supply_e2e"},
                    {"host": "127.0.0.1", "port": 29490, "mode": "production", "database": "supply_e2e"},
                    {"host": "127.0.0.1", "port": 29490, "mode": "rehearsal", "database": "production"}):
            with self.assertRaises(s.SupplyError): s.connect_config(cfg)

    def test_nested_refresh_removed_without_mutating_export(self):
        original = {"access_token": "private-access", "refresh_token": "private-refresh", "nested": {"refreshToken": "private-refresh"}}
        result = s.strip_refresh(original)
        self.assertEqual({"access_token": "private-access", "nested": {}}, result)
        self.assertIn("refresh_token", original)

    def test_private_export_excludes_git_and_writes_new_file_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "runtime" / "snapshot.json"
            s.private_write(path, {"synthetic": "sentinel"})
            self.assertEqual({"synthetic": "sentinel"}, json.loads(path.read_text()))
            with self.assertRaisesRegex(s.SupplyError, "OUTPUT_ALREADY_EXISTS"): s.private_write(path, {})
            (Path(directory) / ".git").mkdir()
            with self.assertRaisesRegex(s.SupplyError, "OUTPUT_INSIDE_GIT"): s.private_write(path.parent / "second.json", {})


@unittest.skipUnless(os.environ.get("REALYU_SUPPLY_TEST_CONFIG"), "explicit isolated PG required")
class SupplyPGTests(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        cfg = json.loads(Path(os.environ["REALYU_SUPPLY_TEST_CONFIG"]).read_text(encoding="utf-8-sig"))
        self.assertEqual("realyu_funding_e2e", cfg["database"])
        self.assertEqual("127.0.0.1", cfg["host"])
        self.assertNotEqual(28490, int(cfg["port"]))
        self.base = psycopg.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"], dbname=cfg["database"], autocommit=True)
        self.schemas = ["supply_" + uuid.uuid4().hex for _ in range(2)]
        self.connections = []
        for schema in self.schemas:
            self.base.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            c = psycopg.connect(host=cfg["host"], port=cfg["port"], user=cfg["user"], password=cfg["password"], dbname=cfg["database"], autocommit=True, options="-c search_path=" + schema)
            self.connections.append(c)
            c.execute("CREATE TABLE proxies(id BIGSERIAL PRIMARY KEY,host TEXT,password TEXT,backup_proxy_id BIGINT REFERENCES proxies(id))")
            c.execute("CREATE TABLE groups(id BIGSERIAL PRIMARY KEY,platform TEXT,rate_multiplier NUMERIC(20,12),fallback_group_id BIGINT REFERENCES groups(id),fallback_group_id_on_invalid_request BIGINT REFERENCES groups(id),model_pricing JSONB)")
            c.execute("CREATE TABLE accounts(id BIGSERIAL PRIMARY KEY,proxy_id BIGINT REFERENCES proxies(id),credentials JSONB,status TEXT,schedulable BOOLEAN,rate_limit_reset_at TIMESTAMPTZ,parent_account_id BIGINT REFERENCES accounts(id))")
            c.execute("CREATE TABLE account_groups(account_id BIGINT REFERENCES accounts(id),group_id BIGINT REFERENCES groups(id),priority INT,PRIMARY KEY(account_id,group_id))")
            for table in s.TABLES[4:-1]: c.execute(sql.SQL("CREATE TABLE {}(id BIGSERIAL PRIMARY KEY,price NUMERIC(20,12))").format(sql.Identifier(table)))
            c.execute("CREATE TABLE settings(key TEXT PRIMARY KEY,value TEXT)")
            c.execute("CREATE TABLE users(id BIGINT PRIMARY KEY,role TEXT,balance NUMERIC(20,8));CREATE TABLE api_keys(id BIGINT PRIMARY KEY);CREATE TABLE usage_logs(id BIGINT PRIMARY KEY);CREATE TABLE payment_orders(id BIGINT PRIMARY KEY)")
        self.source, self.target = self.connections
        self.source.execute("INSERT INTO proxies(id,host,password) VALUES(10,'synthetic.invalid','private-proxy-sentinel'),(11,'backup.invalid','private-backup');UPDATE proxies SET backup_proxy_id=CASE id WHEN 10 THEN 11 ELSE 10 END")
        self.source.execute("INSERT INTO groups(id,platform,rate_multiplier,model_pricing) VALUES(20,'openai',0.123456789123,'[{\"input_price\":0.00000000123456789123}]'),(21,'openai',1,'[]');UPDATE groups SET fallback_group_id=CASE id WHEN 20 THEN 21 ELSE 20 END")
        self.source.execute("INSERT INTO accounts(id,proxy_id,credentials,status,schedulable,rate_limit_reset_at) VALUES(30,10,'{\"access_token\":\"private-access\",\"refresh_token\":\"private-refresh\"}','active',true,'2026-10-11 00:00:00+00'),(31,11,'{\"access_token\":\"private-inactive\"}','inactive',false,NULL)")
        self.source.execute("INSERT INTO account_groups VALUES(30,20,7),(31,20,11)")
        self.source.execute("INSERT INTO settings VALUES('openai_oauth_scheduling_rate_multiplier','1.25'),('merchant_private_key','must-never-export')")
        self.source.execute("INSERT INTO users VALUES(1,'user',999999)")
        with self.source.transaction():
            self.source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            self.snapshot = s.snapshot_connection(self.source)

    def tearDown(self):
        from psycopg import sql
        for c in self.connections: c.close()
        for schema in self.schemas: self.base.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema)))
        self.base.close()

    def test_exact_supply_relationships_private_boundary_and_replay(self):
        receipt = s.import_connection(self.target, self.snapshot, "synthetic-supply-01")
        self.assertFalse(receipt["activated"])
        self.assertEqual([], receipt["customer_tables_copied"])
        self.assertEqual(0, self.target.execute("SELECT count(*) FROM users").fetchone()[0])
        self.assertEqual([], self.target.execute("SELECT value FROM settings WHERE key='merchant_private_key'").fetchall())
        rows = self.target.execute("SELECT id,status,schedulable,credentials ? 'refresh_token',proxy_id FROM accounts ORDER BY id").fetchall()
        self.assertEqual([(30, "active", False, False, 10), (31, "inactive", False, False, 11)], rows)
        self.assertEqual(Decimal("0.123456789123"), self.target.execute("SELECT rate_multiplier FROM groups WHERE id=20").fetchone()[0])
        self.assertEqual(self.source.execute("SELECT model_pricing::text FROM groups WHERE id=20").fetchone(), self.target.execute("SELECT model_pricing::text FROM groups WHERE id=20").fetchone())
        self.assertEqual([(10, 11), (11, 10)], self.target.execute("SELECT id,backup_proxy_id FROM proxies ORDER BY id").fetchall())
        self.assertEqual([(30,20,7),(31,20,11)],self.target.execute("SELECT * FROM account_groups ORDER BY account_id").fetchall())
        self.assertEqual(self.source.execute("SELECT rate_limit_reset_at FROM accounts WHERE id=30").fetchone(),self.target.execute("SELECT rate_limit_reset_at FROM accounts WHERE id=30").fetchone())
        self.assertIn("private-refresh", s.canonical(self.snapshot))
        self.assertNotIn("must-never-export", s.canonical(self.snapshot))
        self.assertTrue(s.import_connection(self.target,self.snapshot,"synthetic-supply-01")["idempotent_replay"])
        self.target.execute("UPDATE accounts SET schedulable=true WHERE id=30")
        with self.assertRaisesRegex(s.SupplyError,"TARGET_SUPPLY_CHANGED"):s.import_connection(self.target,self.snapshot,"synthetic-supply-01")

    def test_customer_state_and_tampered_allowlist_block_import(self):
        self.target.execute("INSERT INTO users VALUES(1,'user',0)")
        with self.assertRaisesRegex(s.SupplyError,"TARGET_HAS_CUSTOMERS"):s.import_connection(self.target,self.snapshot,"synthetic-supply-02")
        self.assertEqual(0,self.target.execute("SELECT count(*) FROM accounts").fetchone()[0])
        bad=copy.deepcopy(self.snapshot);bad["tables"]["settings"]["rows"].append(["merchant_private_key","sentinel"])
        with self.assertRaisesRegex(s.SupplyError,"SETTING_NOT_ALLOWLISTED"):s.rehearsal_tables(bad)
        bad=copy.deepcopy(self.snapshot);bad["tables"]["users"]={"columns":[],"rows":[]}
        with self.assertRaisesRegex(s.SupplyError,"INVALID_SUPPLY_SNAPSHOT"):s.rehearsal_tables(bad)

    def test_mid_import_failure_rolls_back_supply_and_audit(self):
        self.target.execute("ALTER TABLE account_groups ADD CONSTRAINT synthetic_fail CHECK(priority=999)")
        with self.assertRaises(Exception):s.import_connection(self.target,self.snapshot,"synthetic-supply-03")
        self.assertEqual(0,self.target.execute("SELECT count(*) FROM accounts").fetchone()[0])
        self.assertEqual(0,self.target.execute("SELECT count(*) FROM proxies").fetchone()[0])
        self.assertIsNone(self.target.execute("SELECT to_regclass('realyu_supply_import_runs')").fetchone()[0])

    def test_bootstrap_group_replacement_is_explicit_and_identity_checked(self):
        self.target.execute("INSERT INTO groups(id,platform,rate_multiplier) VALUES(20,'openai',1)")
        with self.assertRaisesRegex(s.SupplyError,"TARGET_SUPPLY_NOT_EMPTY:groups"):s.import_connection(self.target,self.snapshot,"synthetic-supply-04")
        self.target.execute("UPDATE groups SET platform='anthropic'")
        with self.assertRaisesRegex(s.SupplyError,"BOOTSTRAP_GROUP_ID_OR_PLATFORM_MISMATCH"):s.import_connection(self.target,self.snapshot,"synthetic-supply-04",True)
        self.target.execute("UPDATE groups SET platform='openai'")
        self.assertFalse(s.import_connection(self.target,self.snapshot,"synthetic-supply-04",True)["activated"])


if __name__ == "__main__": unittest.main()

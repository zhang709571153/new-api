"""Synthetic migration contract tests; no PostgreSQL socket or customer data.

SQLite loading is exercised against temporary on-disk databases. Import tests
use a deliberately small recording DB-API double to check submitted values,
guards, replay semantics and archive contents. This does not replace the real
PostgreSQL constraint/transaction rehearsal.
"""
from __future__ import annotations

from contextlib import closing, contextmanager, redirect_stderr, redirect_stdout
from copy import deepcopy
from decimal import Decimal
import io
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from types import ModuleType
import unittest
from unittest.mock import patch

import singlecore_migrate as migrate


INSTALLATION = "synthetic-candidate-001"
SOURCE_HASH = "a" * 64
SECRET_MARKERS = (
    "synthetic-private-login", "synthetic-private-email@example.invalid",
    "synthetic-private-password", "synthetic-private-key-1234567890",
)


def source_user(user_id=1, **changes):
    row = dict(id=user_id, username=f"synthetic-user-{user_id}",
               email=f"synthetic-{user_id}@example.invalid", password="$2b$12$synthetic-hash",
               role=1, status=1, quota=500_001, display_name=f"Synthetic {user_id}",
               group="default", created_at=1_700_000_000, deleted_at=None)
    return {**row, **changes}


def source_token(token_id=10, **changes):
    row = dict(id=token_id, user_id=1, workspace_user_id=0, status=1,
               deleted_at=None, key=f"synthetic-key-{token_id:016d}", name="Synthetic key",
               unlimited_quota=0, allow_ips="", used_quota=1, remain_quota=500_000,
               model_limits_enabled=1, model_limits="gpt-synthetic", group="default",
               cross_group_retry=0, auto_groups="[]", expired_time=-1)
    return {**row, **changes}


def source_tables(*, users=None, tokens=None):
    tables = {name: [] for name in migrate.TABLES}
    tables["users"] = [source_user()] if users is None else users
    tables["tokens"] = [source_token()] if tokens is None else tokens
    return tables


def target_config(**changes):
    return {**dict(mode="rehearsal", host="127.0.0.1", port=38490,
                   database="realyu_candidate_unit", installation_id=INSTALLATION,
                   user="synthetic-importer", password="synthetic-db-password",
                   group_mapping={"default": 71}), **changes}


def write_snapshot(path, tables, *, omit=()):
    with closing(sqlite3.connect(path)) as conn:
        for table, rows in tables.items():
            if table in omit:
                continue
            columns = sorted({column for row in rows for column in row} or {"id"})
            # No affinity coercion: preserve integer vs text source values.
            conn.execute('CREATE TABLE "' + table + '" (' +
                         ",".join('"' + column + '"' for column in columns) + ")")
            for row in rows:
                conn.execute('INSERT INTO "' + table + '" VALUES (' +
                             ",".join("?" for _ in columns) + ")",
                             [row.get(column) for column in columns])
        conn.commit()
    return migrate.sha(path.read_bytes())


class JsonbValue:
    def __init__(self, obj):
        self.obj = deepcopy(obj)

    def __eq__(self, other):
        return isinstance(other, JsonbValue) and self.obj == other.obj


class Rows:
    def __init__(self, rows=()):
        self.rows = list(rows)

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def __iter__(self):
        return iter(self.rows)


class RecordingTarget:
    """Capture the importer's SQL contract without opening a network socket."""
    def __init__(self):
        self.state = (INSTALLATION, "staging")
        self.calls = []
        self.connect_calls = []
        self.business_tables = set()
        self.unmapped_users = False
        self.unmapped_keys = False
        self.groups = {71: ("active", "standard"), 72: ("active", "standard")}
        self.users = {}
        self.identities = {}
        self.keys = {}
        self.legacy_keys = {}
        self.scopes = {}
        self.allowed_groups = set()
        self.archives = {}
        self.runs = {}
        self.fail_reconciliation = None
        self.commits = 0
        self.rollbacks = 0

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    @contextmanager
    def transaction(self):
        fields = ("state", "users", "identities", "keys", "legacy_keys", "scopes",
                  "allowed_groups", "archives", "runs")
        before = {field: deepcopy(getattr(self, field)) for field in fields}
        try:
            yield
        except BaseException:
            for field, value in before.items():
                setattr(self, field, value)
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def connect(self, **kwargs):
        self.connect_calls.append(kwargs)
        return self

    def execute(self, sql, params=()):
        p = tuple(params)
        self.calls.append((sql, p))
        if sql.startswith(("SET LOCAL ", "LOCK TABLE ", "SELECT pg_advisory")):
            return Rows()
        if sql.startswith("SELECT installation_id,phase"):
            return Rows([] if self.state is None else [self.state])
        if sql.startswith("SELECT source_sha256,mapping_sha256,receipt"):
            return Rows([] if p[0] not in self.runs else [self.runs[p[0]]])
        if sql.startswith("SELECT EXISTS"):
            if "FROM users u" in sql:
                return Rows([(self.unmapped_users,)])
            if "FROM api_keys k" in sql:
                return Rows([(self.unmapped_keys,)])
            return Rows([(any("FROM " + name + ")" in sql for name in self.business_tables),)])
        if sql.startswith("SELECT status,subscription_type"):
            return Rows([] if p[0] not in self.groups else [self.groups[p[0]]])
        if sql.startswith("SELECT source_user_id,user_id"):
            return Rows((source_id, value[0]) for source_id, value in self.identities.items())
        if sql.startswith("SELECT source_token_id,api_key_id"):
            return Rows((source_id, value[1]) for source_id, value in self.legacy_keys.items())
        if sql.startswith("INSERT INTO realyu_migration_state"):
            self.state = self.state or (p[0], "staging")
        elif sql.startswith("INSERT INTO users("):
            target_id = max(self.users, default=1000) + 1
            self.users[target_id] = list(p)
            return Rows([(target_id,)])
        elif sql.startswith("UPDATE users SET email="):
            self.users[p[-1]] = list(p[:-1])
        elif sql.startswith("UPDATE users SET status='disabled'"):
            self.users[p[0]][4] = "disabled"
            self.users[p[0]][7] = "tombstoned"
        elif sql.startswith("INSERT INTO realyu_legacy_identities"):
            self.identities[p[1]] = (p[0], p[2])
        elif sql.startswith("INSERT INTO api_keys("):
            target_id = max(self.keys, default=2000) + 1
            self.keys[target_id] = list(p)
            return Rows([(target_id,)])
        elif sql.startswith("UPDATE api_keys SET user_id="):
            self.keys[p[-1]] = list(p[:-1])
        elif sql.startswith("UPDATE api_keys SET status='disabled'"):
            for key_id, row in self.keys.items():
                if ("WHERE user_id=" in sql and row[0] == p[0]) or ("WHERE id=" in sql and key_id == p[0]):
                    row[4] = "disabled"
                    if "deleted_at=" in sql:
                        row[9] = row[9] or "tombstoned"
        elif sql.startswith("INSERT INTO user_allowed_groups"):
            self.allowed_groups.add(p)
        elif sql.startswith("DELETE FROM user_allowed_groups"):
            # Accept an explicit per-imported-user replacement, not a global wipe.
            if "WHERE user_id=%s" not in sql:
                raise AssertionError("Unscoped grant deletion")
            self.allowed_groups = {grant for grant in self.allowed_groups if grant[0] != p[0]}
        elif sql.startswith("INSERT INTO realyu_key_scopes"):
            self.scopes[p[0]] = p
        elif sql.startswith("INSERT INTO realyu_legacy_keys"):
            self.legacy_keys[p[0]] = p
        elif sql.startswith("DELETE FROM realyu_legacy_records"):
            name = p[0] if p else "orphan_token_tombstones"
            self.archives = {key: value for key, value in self.archives.items() if key[0] != name}
        elif sql.startswith("INSERT INTO realyu_legacy_records"):
            self.archives[(p[0], p[1])] = (p[2], p[3].obj, p[4])
        elif sql.startswith("SELECT u.balance,u.password_hash,u.username,i.login_name"):
            if self.fail_reconciliation == "user":
                return Rows([(Decimal("-999"), "", "", "")])
            user = self.users[p[0]]
            identity = next(v for v in self.identities.values() if v[0] == p[0])
            return Rows([(user[3], user[1], user[5], identity[1])])
        elif sql.startswith("SELECT k.key,k.user_id,s.actor_user_id,s.team_id,k.quota_used"):
            if self.fail_reconciliation == "key":
                return Rows([("synthetic-wrong-key", 0, 0, None, Decimal(0))])
            key = self.keys[p[0]]
            scope = self.scopes[p[0]]
            return Rows([(key[1], key[0], scope[1], scope[3], key[7])])
        elif sql.startswith("INSERT INTO realyu_migration_runs"):
            self.runs[p[0]] = (p[1], p[2], deepcopy(p[3].obj))
        elif sql.startswith(("UPDATE realyu_migration_state ", "INSERT INTO realyu_teams(",
                             "INSERT INTO realyu_legacy_user_metadata(",
                             "UPDATE realyu_team_members ", "INSERT INTO realyu_team_members(",
                             "UPDATE realyu_funding_subscriptions ", "INSERT INTO realyu_funding_subscriptions(",
                             "DELETE FROM realyu_member_period_usage", "INSERT INTO realyu_member_period_usage(")):
            pass
        else:
            raise AssertionError("Recording adapter needs review for SQL: " + sql)
        return Rows()

    @property
    def mutations(self):
        return [(sql, p) for sql, p in self.calls if sql.startswith(("INSERT", "UPDATE", "DELETE"))]


@contextmanager
def synthetic_psycopg(target=None, *, connection_error=None):
    module = ModuleType("psycopg")
    types_module = ModuleType("psycopg.types")
    json_module = ModuleType("psycopg.types.json")
    json_module.Jsonb = JsonbValue
    if connection_error is not None:
        def connect(**_):
            raise connection_error
        module.connect = connect
    else:
        module.connect = target.connect
    with patch.dict(sys.modules, {"psycopg": module, "psycopg.types": types_module,
                                 "psycopg.types.json": json_module}):
        yield


class SnapshotAndPrepareTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="realyu-migrate-unit-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def assert_blocked(self, code, function, *args, **kwargs):
        with self.assertRaisesRegex(migrate.MigrationError, "^" + code + "$"):
            function(*args, **kwargs)

    def test_quota_preserves_exact_small_and_maximum_units(self):
        for quota in (0, 1, -1, 499_999, 500_001, migrate.MAX_QUOTA, -migrate.MAX_QUOTA):
            with self.subTest(quota=quota):
                self.assertIsInstance(migrate.usd(quota), Decimal)
                self.assertEqual(migrate.usd(quota) * 500_000, quota)
        for invalid in (True, False, 1.0, "1", None, migrate.MAX_QUOTA + 1, -migrate.MAX_QUOTA - 1):
            with self.subTest(invalid=invalid):
                self.assert_blocked("INVALID_QUOTA", migrate.bounded_quota, invalid, signed=True)
        self.assert_blocked("INVALID_QUOTA", migrate.bounded_quota, -1)

    def test_snapshot_read_is_deterministic_and_does_not_change_source(self):
        tables = source_tables(users=[source_user(2), source_user(1)])
        path = self.root / "snapshot.sqlite"
        digest = write_snapshot(path, tables)
        before = path.read_bytes()
        loaded = migrate.load_snapshot(path, digest)
        self.assertEqual([1, 2], [u["id"] for u in loaded["users"]])
        self.assertEqual(before, path.read_bytes())
        self.assertEqual(migrate.inspect(loaded, digest), migrate.inspect(migrate.load_snapshot(path, digest), digest))
        self.assertEqual([path], list(self.root.iterdir()))

    def test_snapshot_rejects_hash_mismatch_and_active_journals(self):
        path = self.root / "snapshot.sqlite"
        digest = write_snapshot(path, source_tables())
        self.assert_blocked("SOURCE_SHA256_MISMATCH", migrate.load_snapshot, path, "f" * 64)
        self.assert_blocked("EXPECTED_SNAPSHOT_SHA256_REQUIRED", migrate.load_snapshot, path, digest.upper())
        for suffix in ("-wal", "-journal"):
            with self.subTest(suffix=suffix):
                sidecar = Path(str(path) + suffix)
                sidecar.write_bytes(b"synthetic")
                self.assert_blocked("USE_A_COMPLETED_BACKUP_NOT_AN_ACTIVE_DATABASE", migrate.load_snapshot, path, digest)
                sidecar.unlink()

    def test_snapshot_requires_all_archive_tables(self):
        path = self.root / "missing.sqlite"
        digest = write_snapshot(path, source_tables(), omit=("billing_orders",))
        self.assert_blocked("SOURCE_SCHEMA_MISSING_REQUIRED_TABLES", migrate.load_snapshot, path, digest)

    def test_snapshot_modified_during_read_is_rejected(self):
        path = self.root / "snapshot.sqlite"
        digest = write_snapshot(path, source_tables())
        real_connect = sqlite3.connect
        def change_after_integrity_read(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            path.write_bytes(path.read_bytes() + b"synthetic-change")
            return conn
        with patch.object(migrate.sqlite3, "connect", side_effect=change_after_integrity_read):
            self.assert_blocked("SOURCE_CHANGED_DURING_READ", migrate.load_snapshot, path, digest)

    def test_personal_key_keeps_credential_and_policy(self):
        row = source_token(key="sk-synthetic-private-key-1234567890", allow_ips="192.0.2.1,\n2001:db8::/48", model_limits="model-a,model-b")
        prepared, blockers = migrate.prepare(source_tables(tokens=[row]))
        self.assertEqual(row["key"], prepared[0]["wire_key"])
        self.assertEqual((1, 0, True), (prepared[0]["actor"], prepared[0]["team_id"], prepared[0]["usable"]))
        self.assertEqual(["192.0.2.1", "2001:db8::/48"], prepared[0]["ips"])
        self.assertEqual({"model-a": True, "model-b": True}, prepared[0]["models"])
        self.assertEqual([{"code": "REHEARSAL_ONLY_NO_ACTIVATION"}], blockers)

    def test_deleted_and_disabled_keys_never_become_usable(self):
        for changes in ({"status": 2}, {"deleted_at": 1_700_000_001}):
            with self.subTest(changes=changes):
                prepared, _ = migrate.prepare(source_tables(tokens=[source_token(**changes)]))
                self.assertFalse(prepared[0]["usable"])

    def test_only_disabled_deleted_orphans_may_be_archived(self):
        for changes in ({"status": 1, "deleted_at": None}, {"status": 2, "deleted_at": None},
                        {"status": 1, "deleted_at": 1_700_000_001}):
            with self.subTest(changes=changes):
                self.assert_blocked("TOKEN_OWNER_MISSING", migrate.prepare,
                                    source_tables(tokens=[source_token(user_id=999, **changes)]))
        plan = migrate.inspect(source_tables(tokens=[source_token(user_id=999, status=2, deleted_at=1_700_000_001)]), SOURCE_HASH)
        self.assertEqual(0, plan["importable_keys"])
        self.assertEqual(1, plan["archived_orphan_tombstones"])

    def test_duplicate_normalized_credentials_rejected(self):
        key = "synthetic-duplicate-key-123456"
        self.assert_blocked("INVALID_OR_DUPLICATE_CLIENT_KEY", migrate.prepare,
                            source_tables(tokens=[source_token(10, key=key), source_token(11, key="sk-" + key)]))

    def test_dynamic_group_and_invalid_ip_fail_closed(self):
        for changes in ({"group": "auto"}, {"cross_group_retry": 1}, {"auto_groups": '["default"]'}):
            with self.subTest(changes=changes):
                self.assert_blocked("DYNAMIC_GROUP_ROUTING_REQUIRES_EXPLICIT_MAPPING", migrate.prepare,
                                    source_tables(tokens=[source_token(**changes)]))
        self.assert_blocked("INVALID_SOURCE_IP_RESTRICTION", migrate.prepare,
                            source_tables(tokens=[source_token(allow_ips="192.0.2.999")]))

    def test_unknown_user_role_status_and_password_rejected(self):
        for changes, code in (({"role": 99}, "UNMAPPED_SOURCE_USER_STATE"),
                              ({"status": 0}, "UNMAPPED_SOURCE_USER_STATE"),
                              ({"password": "plaintext-synthetic"}, "UNSUPPORTED_SOURCE_PASSWORD_FORMAT")):
            with self.subTest(changes=changes):
                self.assert_blocked(code, migrate.prepare, source_tables(users=[source_user(**changes)]))

    def test_team_key_requires_matching_member_and_separate_cap(self):
        tables = source_tables(users=[source_user(1), source_user(2)], tokens=[source_token(workspace_user_id=2)])
        tables["workspace_teams"] = [dict(id=5, owner_user_id=1, funding_version=1, name="Synthetic team")]
        tables["workspace_team_accounts"] = [dict(team_id=5, owner_user_id=1, quota=0, closed_at=None)]
        prepared, _ = migrate.prepare(tables)
        self.assertFalse(prepared[0]["usable"])
        tables["workspace_members"] = [dict(user_id=2, token_id=10, team_id=5, status=1, weekly_quota=500_000)]
        prepared, _ = migrate.prepare(tables)
        self.assertEqual((2, 5, True), (prepared[0]["actor"], prepared[0]["team_id"], prepared[0]["usable"]))
        tables["tokens"][0]["unlimited_quota"] = 1
        self.assertFalse(migrate.prepare(tables)[0][0]["usable"])
        tables["workspace_members"][0]["user_id"] = 1
        self.assert_blocked("TOKEN_MEMBER_SCOPE_MISMATCH", migrate.prepare, tables)

    def test_unsettled_payments_and_reservations_remain_activation_blockers(self):
        tables = source_tables()
        tables["billing_orders"] = [dict(id=1, status="pending"), dict(id=2, status="paid")]
        tables["subscription_pre_consume_records"] = [dict(id=1, status="pending"), dict(id=2, status="settled"), dict(id=3, status="refunded")]
        codes = {x["code"]: x.get("count") for x in migrate.inspect(tables, SOURCE_HASH)["activation_blockers"]}
        self.assertEqual(1, codes["OLD_PAYMENT_CALLBACK_TAKEOVER_REQUIRED"])
        self.assertEqual(1, codes["SOURCE_UNSETTLED_RESERVATIONS"])
        self.assertIn("REHEARSAL_ONLY_NO_ACTIVATION", codes)


class ImportContractTests(unittest.TestCase):
    def setUp(self):
        self.target = RecordingTarget()
        self.driver = synthetic_psycopg(self.target)
        self.driver.__enter__()
        self.addCleanup(self.driver.__exit__, None, None, None)

    def run_import(self, tables=None, *, digest=SOURCE_HASH, config=None, operation="synthetic-run-001"):
        return migrate.apply_snapshot(source_tables() if tables is None else tables, digest,
                                      target_config() if config is None else config, operation)

    def test_target_guards_reject_production_and_ambiguous_settings_before_connect(self):
        cases = (
            ({"mode": "production"}, "ONLY_EXPLICIT_LOOPBACK_REHEARSAL_TARGETS_ALLOWED"),
            ({"host": "localhost"}, "ONLY_EXPLICIT_LOOPBACK_REHEARSAL_TARGETS_ALLOWED"),
            ({"host": "192.0.2.1"}, "ONLY_EXPLICIT_LOOPBACK_REHEARSAL_TARGETS_ALLOWED"),
            ({"port": 5432}, "TARGET_PORT_NOT_ISOLATED"), ({"port": 28490}, "TARGET_PORT_NOT_ISOLATED"),
            ({"port": True}, "TARGET_PORT_NOT_ISOLATED"), ({"port": "38490"}, "TARGET_PORT_NOT_ISOLATED"),
            ({"database": "sub2api"}, "TARGET_DATABASE_NOT_ISOLATED"),
            ({"installation_id": ""}, "INSTALLATION_ID_REQUIRED"),
            ({"group_mapping": {"default": True}}, "EXPLICIT_GROUP_MAPPING_REQUIRED"),
            ({"group_mapping": {"default": 0}}, "EXPLICIT_GROUP_MAPPING_REQUIRED"),
        )
        for changes, code in cases:
            with self.subTest(changes=changes), self.assertRaisesRegex(migrate.MigrationError, code):
                self.run_import(config=target_config(**changes))
        self.assertEqual([], self.target.connect_calls)

    def test_missing_source_group_mapping_rejected_before_connect(self):
        with self.assertRaisesRegex(migrate.MigrationError, "SOURCE_GROUP_NOT_MAPPED"):
            self.run_import(config=target_config(group_mapping={}))
        self.assertEqual([], self.target.connect_calls)

    def test_staging_cluster_exception_is_exact_and_cannot_target_shadow_database(self):
        config=target_config(mode="staging",port=28490,database=migrate.STAGING_DATABASE,
                             installation_id=migrate.STAGING_INSTALLATION,user=migrate.STAGING_ROLE)
        migrate.validate_target(config)
        for changed in ({"database":"sub2api_production"},{"database":"realyu_other_candidate"},
                        {"user":"sub2api_app"},{"installation_id":"other-installation"},
                        {"port":29490},{"host":"localhost"}):
            with self.subTest(changed=changed),self.assertRaises(migrate.MigrationError):
                migrate.validate_target({**config,**changed})
        self.assertEqual([],self.target.connect_calls)

    def test_active_or_different_installation_rejected_before_writes(self):
        for state in ((INSTALLATION, "active"), ("different-installation", "staging")):
            with self.subTest(state=state):
                self.target.state = state
                with self.assertRaisesRegex(migrate.MigrationError, "TARGET_NOT_STAGING_OR_WRONG_INSTALLATION"):
                    self.run_import()
        self.assertEqual([], self.target.mutations)

    def test_existing_business_writes_unmapped_users_and_keys_rejected(self):
        for table in ("usage_logs", "payment_orders", "realyu_funding_requests"):
            with self.subTest(table=table):
                self.target.business_tables = {table}
                with self.assertRaisesRegex(migrate.MigrationError, "TARGET_ALREADY_HAS_BUSINESS_WRITES"):
                    self.run_import()
        self.target.business_tables.clear()
        for field, code in (("unmapped_users", "TARGET_HAS_UNMAPPED_CUSTOMERS_OR_INTERNAL_CREDIT"),
                            ("unmapped_keys", "TARGET_HAS_UNMAPPED_KEYS")):
            setattr(self.target, field, True)
            with self.assertRaisesRegex(migrate.MigrationError, code):
                self.run_import()
            setattr(self.target, field, False)
        self.assertEqual([], self.target.mutations)

    def test_target_group_must_exist_active_standard(self):
        for group in (None, ("disabled", "standard"), ("active", "subscription")):
            with self.subTest(group=group):
                if group is None:
                    self.target.groups.pop(71, None)
                else:
                    self.target.groups[71] = group
                with self.assertRaisesRegex(migrate.MigrationError, "TARGET_GROUP_MUST_BE_ACTIVE_STANDARD"):
                    self.run_import()
        self.assertEqual([], self.target.mutations)

    def test_replay_is_noop_and_does_not_double_balance_or_duplicate_archives(self):
        first = self.run_import()
        before = deepcopy((self.target.users, self.target.keys, self.target.archives))
        self.target.calls.clear()
        second = self.run_import()
        self.assertFalse(first["idempotent_replay"])
        self.assertTrue(second["idempotent_replay"])
        self.assertFalse(second["activated"])
        self.assertEqual([], self.target.mutations)
        self.assertEqual(before, (self.target.users, self.target.keys, self.target.archives))

    def test_replay_changed_hash_or_mapping_is_rejected(self):
        self.run_import()
        for kwargs in ({"digest": "b" * 64}, {"config": target_config(group_mapping={"default": 72})}):
            self.target.calls.clear()
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(migrate.MigrationError, "OPERATION_ID_INPUT_CONFLICT"):
                self.run_import(**kwargs)
            self.assertEqual([], self.target.mutations)

    def test_exact_decimal_values_and_zero_limited_key_not_unlimited(self):
        tables = source_tables(users=[source_user(quota=-1)], tokens=[source_token(used_quota=0, remain_quota=0)])
        self.run_import(tables)
        user = self.target.users[1001]
        key = self.target.keys[2001]
        self.assertEqual(Decimal("-0.000002"), user[3])
        self.assertIsInstance(user[3], Decimal)
        self.assertEqual("quota_exhausted", key[4])
        self.assertEqual((Decimal(0), Decimal(0)), (key[6], key[7]))
        changed = deepcopy(tables)
        changed["users"][0]["quota"] = 3
        self.run_import(changed, digest="b" * 64, operation="synthetic-run-002")
        self.assertEqual(Decimal("0.000006"), self.target.users[1001][3])
        self.assertEqual(1, len(self.target.users))
        self.assertEqual(1, len(self.target.keys))

    def test_reconciliation_failure_rolls_back_every_import_write(self):
        for kind, code in (("user", "USER_RECONCILIATION_FAILED"), ("key", "KEY_RECONCILIATION_FAILED")):
            with self.subTest(kind=kind):
                self.target.fail_reconciliation = kind
                with self.assertRaisesRegex(migrate.MigrationError, code):
                    self.run_import()
                self.assertEqual({}, self.target.users)
                self.assertEqual({}, self.target.keys)
                self.assertEqual({}, self.target.runs)
        self.assertEqual(2, self.target.rollbacks)

    def test_removed_source_key_is_tombstoned(self):
        self.run_import()
        self.run_import(source_tables(tokens=[]), digest="b" * 64, operation="synthetic-run-002")
        self.assertEqual("disabled", self.target.keys[2001][4])
        self.assertIsNotNone(self.target.keys[2001][9])

    def test_removed_source_user_disables_identity_and_credentials(self):
        self.run_import()
        self.run_import(source_tables(users=[], tokens=[]), digest="b" * 64, operation="synthetic-run-002")
        self.assertEqual("disabled", self.target.users[1001][4])
        self.assertIsNotNone(self.target.users[1001][7])
        self.assertEqual("disabled", self.target.keys[2001][4])
        self.assertEqual(set(), self.target.allowed_groups)

    def test_disabled_and_deleted_keys_cannot_grant_group_access(self):
        tables = source_tables(tokens=[source_token(status=2, group="retired-key-group"),
                                       source_token(11, deleted_at=1_700_000_001, group="retired-key-group")])
        self.run_import(tables, config=target_config(group_mapping={"default": 71, "retired-key-group": 72}))
        # The active user's own default group remains independent of key policy.
        self.assertEqual({(1001, 71)}, self.target.allowed_groups)
        self.assertEqual(["disabled", "disabled"], [row[4] for row in self.target.keys.values()])

    def test_user_without_keys_keeps_own_explicit_group_authorization(self):
        tables = source_tables(tokens=[])
        plan = migrate.inspect(tables, SOURCE_HASH)
        self.assertEqual(["default"], plan["source_groups"])
        self.run_import(tables)
        self.assertEqual({(1001, 71)}, self.target.allowed_groups)
        self.assertEqual({}, self.target.keys)

    def test_only_former_root_is_native_admin_and_source_roles_preserved(self):
        tables = source_tables(users=[source_user(1, role=1), source_user(2, role=10), source_user(3, role=100)])
        self.run_import(tables)
        self.assertEqual(["user", "user", "admin"], [row[2] for row in self.target.users.values()])
        writes = [p for sql, p in self.target.calls if sql.startswith("INSERT INTO realyu_legacy_user_metadata(")]
        self.assertEqual([(1001, 1), (1002, 10), (1003, 100)], writes)

    def test_combined_key_quota_overflow_is_rejected_without_partial_import(self):
        tables = source_tables(tokens=[source_token(used_quota=migrate.MAX_QUOTA, remain_quota=1)])
        with self.assertRaisesRegex(migrate.MigrationError, "INVALID_QUOTA"):
            self.run_import(tables)
        self.assertEqual({}, self.target.users)
        self.assertEqual({}, self.target.keys)
        self.assertEqual({}, self.target.runs)

    def test_existing_key_becoming_orphan_tombstone_is_disabled(self):
        # Keep old owner in source; mutate the source key's payer to a missing
        # deleted owner. A now-unimportable credential must not remain active.
        self.run_import()
        second = source_tables(tokens=[source_token(user_id=999, status=2, deleted_at=1_700_000_001)])
        self.run_import(second, digest="b" * 64, operation="synthetic-run-002")
        self.assertEqual("disabled", self.target.keys[2001][4])
        self.assertIsNotNone(self.target.keys[2001][9])

    def test_changed_mapping_removes_old_grants_from_imported_user(self):
        self.run_import()
        self.run_import(digest="b" * 64, operation="synthetic-run-002",
                        config=target_config(group_mapping={"default": 72}))
        self.assertEqual({(1001, 72)}, self.target.allowed_groups)

    def test_archive_keeps_exact_historical_rows_without_inventing_payments(self):
        tables = source_tables()
        history = dict(id=4, status="paid", paid_amount_cents=1200, source_order_id="synthetic-order-004")
        tables["billing_orders"] = [history]
        self.run_import(tables)
        archived = self.target.archives[("billing_orders", "[4]")]
        self.assertEqual(history, archived[1])
        self.assertEqual(migrate.sha(migrate.canonical(history).encode()), archived[0])
        self.assertEqual(SOURCE_HASH, archived[2])
        self.assertFalse(any(sql.startswith("INSERT INTO payment_orders") for sql, _ in self.target.calls))
        self.run_import(source_tables(), digest="b" * 64, operation="synthetic-run-002")
        self.assertNotIn(("billing_orders", "[4]"), self.target.archives)
        self.assertEqual(2, len(self.target.runs))

    def test_orphan_archive_redacts_credential_but_preserves_hash(self):
        token = source_token(user_id=999, status=2, deleted_at=1_700_000_001)
        receipt = self.run_import(source_tables(tokens=[token]))
        archived = self.target.archives[("orphan_token_tombstones", "[10]")][1]
        self.assertNotIn("key", archived)
        self.assertEqual(migrate.sha(token["key"].encode()), archived["key_sha256"])
        self.assertEqual(1, receipt["archived_orphan_tombstones"])
        self.assertNotIn(token["key"], json.dumps(receipt))


class SafeCLIOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(prefix="realyu-migrate-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "private-source.sqlite"
        tables = source_tables(
            users=[source_user(username=SECRET_MARKERS[0], email=SECRET_MARKERS[1],
                               password="$2b$12$" + SECRET_MARKERS[2])],
            tokens=[source_token(key=SECRET_MARKERS[3])])
        self.digest = write_snapshot(self.path, tables)

    def run_cli(self, command="inspect", *args):
        out, err = io.StringIO(), io.StringIO()
        argv = ["singlecore_migrate.py", command, "--snapshot", str(self.path), "--sha256", self.digest, *args]
        with patch.object(sys, "argv", argv), redirect_stdout(out), redirect_stderr(err):
            status = migrate.main()
        for marker in (*SECRET_MARKERS, "synthetic-db-password"):
            self.assertNotIn(marker, out.getvalue() + err.getvalue())
        return status, out.getvalue(), err.getvalue()

    def test_inspection_report_contains_counts_hashes_and_blockers_only(self):
        status, out, err = self.run_cli()
        self.assertEqual(0, status)
        self.assertEqual("", err)
        result = json.loads(out)
        self.assertEqual(1, result["counts"]["users"])
        self.assertFalse(result["production_actions"])
        self.assertIn("REHEARSAL_ONLY_NO_ACTIVATION", [x["code"] for x in result["activation_blockers"]])

    def test_driver_exception_does_not_print_private_sql_values(self):
        private = self.root / "private-target.json"
        private.write_text(json.dumps(target_config()), encoding="utf-8")
        with synthetic_psycopg(connection_error=RuntimeError("SQL leaked " + " ".join(SECRET_MARKERS))):
            status, out, err = self.run_cli("import-rehearsal", "--private-target", str(private),
                                          "--operation-id", "synthetic-run-001")
        self.assertEqual(1, status)
        self.assertEqual("", out)
        self.assertEqual({"status": "FAILED", "code": "PRIVATE_IMPORT_ERROR_NO_CUSTOMER_VALUES_LOGGED"}, json.loads(err))

    def test_existing_evidence_report_is_never_overwritten(self):
        report = self.root / "first-evidence.json"
        report.write_bytes(b"synthetic original evidence")
        status, out, err = self.run_cli("inspect", "--report", str(report))
        self.assertEqual(1, status)
        self.assertEqual("", out)
        self.assertEqual(b"synthetic original evidence", report.read_bytes())
        self.assertEqual("PRIVATE_IMPORT_ERROR_NO_CUSTOMER_VALUES_LOGGED", json.loads(err)["code"])


class ExtraIdentityGuardTests(unittest.TestCase):
    def test_active_mfa_cannot_be_downgraded_to_password_only(self):
        with TemporaryDirectory(prefix="realyu-mfa-guard-") as root:
            path=Path(root)/"source.sqlite"
            tables=source_tables();tables['two_fas']=[{'id':1,'user_id':1,'is_enabled':1,'deleted_at':None}]
            digest=write_snapshot(path,tables)
            with self.assertRaisesRegex(migrate.MigrationError,'ACTIVE_SOURCE_MFA'):
                migrate.load_snapshot(path,digest)

    def test_unported_passkeys_cannot_silently_disappear(self):
        with TemporaryDirectory(prefix="realyu-passkey-guard-") as root:
            path=Path(root)/"source.sqlite"
            tables=source_tables();tables['passkey_credentials']=[{'id':1,'user_id':1}]
            digest=write_snapshot(path,tables)
            with self.assertRaisesRegex(migrate.MigrationError,'SOURCE_PASSKEY_OR_OAUTH'):
                migrate.load_snapshot(path,digest)

    def test_oauth_subjects_require_identity_mapping(self):
        with self.assertRaisesRegex(migrate.MigrationError,'SOURCE_OAUTH_IDENTITY'):
            migrate.prepare(source_tables(users=[source_user(github_id='synthetic-subject')]))


if __name__ == "__main__":
    unittest.main()

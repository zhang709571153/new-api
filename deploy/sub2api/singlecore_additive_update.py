"""Reviewed 306/307-only native update; default is read-only preflight.

Uses the existing admission/drain/SCM updater without changing that pinned file.
Only additive schema is applied after RealYuApi stops. No down migration,
database restore, payment enablement, proxy or tunnel change is performed.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import time

import release_control
import singlecore_native_update as native


UpdateError = native.UpdateError
require, read, sha = native.require, native.read, native.sha
BIN_ROOT = Path(r"C:\ProgramData\RealYu\singlecore-production-20261010\bin")
DATABASE = {"host": "127.0.0.1", "port": 28490,
            "database": "realyu_singlecore_20261010_candidate",
            "user": "realyu_singlecore_owner_20261010"}
REVIEWED = {
    "306_realyu_managed_purchases.sql": "0399b87f731f3511f42ca973f44014cf96ae9a28336a99889ffff3f92ab30bd5",
    "307_realyu_team_member_nicknames.sql": "deb1deaaccae541d7b5cf5dda6cb098eeaf3598fe3227700b3d4c955ebe37c16",
}
ADVISORY_LOCK_ID = 694208311321144027
COMMERCE_TABLES = ("realyu_managed_plans", "realyu_purchase_snapshots", "realyu_purchase_grants")


def migration_content(path):
    # read_text's universal-newline conversion differs from Go's embedded bytes.
    content = Path(path).read_bytes().decode("utf-8").strip()
    return content, hashlib.sha256(content.encode("utf-8")).hexdigest()


def migration_map(rows):
    require(isinstance(rows, list) and rows, "MIGRATION_INVENTORY_REQUIRED")
    result = {}
    for row in rows:
        name, checksum = row.get("filename"), row.get("checksum")
        require(isinstance(name, str) and re.fullmatch(r"[0-9]{3}[a-z]?_[a-z0-9_]+\.sql", name), "MIGRATION_FILENAME")
        require(name not in result and isinstance(checksum, str) and re.fullmatch(r"[a-f0-9]{64}", checksum), "MIGRATION_CHECKSUM_OR_DUPLICATE")
        result[name] = checksum
    return result


def reviewed_files(plan):
    require(plan.get("database_migrations") is True, "ADDITIVE_SCHEMA_PLAN_REQUIRED")
    rows = plan.get("migrations", [])
    require([row.get("filename") for row in rows] == list(REVIEWED), "ONLY_ORDERED_306_307_ALLOWED")
    for row in rows:
        require(row.get("checksum") == REVIEWED[row["filename"]], "UNREVIEWED_MIGRATION_CHECKSUM")
        require(Path(row["path"]).name == row["filename"], "MIGRATION_PATH_NAME_MISMATCH")
        require(migration_content(row["path"])[1] == row["checksum"], "MIGRATION_FILE_CHANGED")
    return {row["filename"]: row["path"] for row in rows}


def backup_evidence(plan, env, now=None):
    ref = plan["backup_evidence"]
    require(sha(ref["path"]) == ref["sha256"], "BACKUP_EVIDENCE_CHANGED")
    evidence = read(ref["path"])
    require(evidence.get("version") == 1 and evidence.get("kind") == "singlecore-pg-snapshot-restore" and evidence.get("status") == "PASS", "BACKUP_RESTORE_PASS_REQUIRED")
    source, restore, backup = evidence["source"], evidence["restore"], evidence["backup"]
    require(all(source.get(k) == v for k, v in DATABASE.items()), "BACKUP_SOURCE_AUTHORITY_MISMATCH")
    require(source.get("owner") == env["DATABASE_USER"], "BACKUP_OWNER_MISMATCH")
    captured = datetime.fromisoformat(evidence["captured_at"].replace("Z", "+00:00"))
    verified = datetime.fromisoformat(restore["verified_at"].replace("Z", "+00:00"))
    now = now or datetime.now(timezone.utc)
    require(captured.tzinfo is not None and verified.tzinfo is not None and 0 <= (now - captured).total_seconds() <= 86400 and captured <= verified <= now, "BACKUP_EVIDENCE_STALE_OR_TIME_INVALID")
    require(backup.get("format") == "custom" and backup.get("size_bytes", 0) > 0, "CUSTOM_BACKUP_REQUIRED")
    require(Path(backup["path"]).is_file() and Path(backup["path"]).stat().st_size == backup["size_bytes"] and sha(backup["path"]) == backup["sha256"], "BACKUP_FILE_CHANGED")
    schema_dump = evidence["schema_dump"]
    require(sha(schema_dump["path"]) == schema_dump["sha256"], "SCHEMA_DUMP_CHANGED")
    require(isinstance(evidence["schema"].get("sha256"), str) and re.fullmatch(r"[a-f0-9]{64}", evidence["schema"]["sha256"]), "SCHEMA_CATALOG_HASH_REQUIRED")
    require(restore.get("status") == "PASS" and restore.get("host") == "127.0.0.1" and restore.get("port") == 29490 and re.fullmatch(r"realyu_backup_verify_[a-z0-9_]+", restore.get("database", "")), "ISOLATED_RESTORE_REQUIRED")
    require(restore.get("dump_sha256") == backup["sha256"] and all(restore.get(k) is True for k in ("row_hashes_match", "schema_hash_match", "migrations_match")), "RESTORE_VERIFICATION_INCOMPLETE")
    require(restore.get("table_count") == evidence["schema"].get("table_count") and restore.get("table_count", 0) > 0, "RESTORE_TABLE_COUNT_MISMATCH")
    baseline = migration_map(evidence["schema_migrations"])
    validate_prefix(baseline, baseline)
    expected = {**baseline, **REVIEWED}
    require(migration_map(plan["candidate_migrations"]) == expected, "CANDIDATE_HAS_UNREVIEWED_OR_MISSING_MIGRATION")
    return evidence, baseline


def validate_prefix(current, baseline, complete=False):
    historical = {k: v for k, v in baseline.items() if k not in REVIEWED}
    require(all(int(k[:3]) <= 305 for k in historical), "UNREVIEWED_SCHEMA_BASELINE")
    require(historical and {k: v for k, v in current.items() if k not in REVIEWED} == historical, "HISTORICAL_SCHEMA_CHANGED")
    applied = []
    absent = False
    for name, checksum in REVIEWED.items():
        if name not in current:
            absent = True
        else:
            require(not absent and current[name] == checksum, "ADDITIVE_SCHEMA_NOT_REVIEWED_PREFIX")
            applied.append(name)
    require(not complete or len(applied) == len(REVIEWED), "REVIEWED_MIGRATIONS_PENDING")
    return applied


class Schema:
    """Finite migration runner using the same lock/checksum/transactions as native."""
    def __init__(self, env, files, baseline, observe=None, connect=None, schema_name="public", server_version_num=None):
        self.env, self.files, self.baseline = env, files, baseline
        self.schema_name, self.server_version_num = schema_name, server_version_num
        self.observe = observe or (lambda state: None)
        self.connect = connect or self.connection

    def connection(self, readonly=True):
        import psycopg
        env = self.env
        return psycopg.connect(host=env["DATABASE_HOST"], port=int(env["DATABASE_PORT"]),
            dbname=env["DATABASE_DBNAME"], user=env["DATABASE_USER"], password=env["DATABASE_PASSWORD"],
            connect_timeout=3, autocommit=True, application_name="realyu-additive-update",
            options="-c statement_timeout=8000 -c lock_timeout=2000 -c default_transaction_read_only=" + ("on" if readonly else "off"))

    def inspect(self, conn, complete=False, recovery=False):
        identity = conn.execute("SELECT current_database(),current_user,host(inet_server_addr()),inet_server_port(),current_schema(),pg_is_in_recovery(),current_setting('server_version_num')").fetchone()
        expected = (self.env["DATABASE_DBNAME"], self.env["DATABASE_USER"], self.env["DATABASE_HOST"], int(self.env["DATABASE_PORT"]), self.schema_name, False)
        require(identity[:6] == expected and (self.server_version_num is None or str(identity[6]) == str(self.server_version_num)), "CONNECTED_DATABASE_IDENTITY_MISMATCH")
        require(conn.execute("SELECT phase FROM realyu_migration_state WHERE singleton").fetchone() == ("active",), "DATABASE_NOT_ACTIVE")
        current = dict(conn.execute("SELECT filename,checksum FROM schema_migrations ORDER BY filename").fetchall())
        applied = validate_prefix(current, self.baseline, complete)
        exists = [conn.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0] for name in COMMERCE_TABLES]
        sequence = conn.execute("SELECT to_regclass('realyu_purchase_subscription_id') IS NOT NULL").fetchone()[0]
        nickname = conn.execute("SELECT data_type,is_nullable,column_default FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='realyu_team_members' AND column_name='nickname'").fetchone()
        require(all(exists) and sequence if applied else not any(exists) and not sequence, "ADDITIVE_SCHEMA_ARTIFACT_MISMATCH")
        require(bool(nickname) == (list(REVIEWED)[1] in applied), "NICKNAME_SCHEMA_MISMATCH")
        if nickname:
            require(nickname == ("text", "NO", "''::text"), "NICKNAME_SCHEMA_MISMATCH")
            checks = conn.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid='realyu_team_members'::regclass AND contype='c'").fetchall()
            require(any("char_length(nickname) <= 40" in item[0] for item in checks), "NICKNAME_BOUND_MISSING")
        counts = {"snapshots": 0, "grants": 0}
        if applied:
            counts["snapshots"] = conn.execute("SELECT count(*) FROM realyu_purchase_snapshots").fetchone()[0]
            counts["grants"] = conn.execute("SELECT count(*) FROM realyu_purchase_grants").fetchone()[0]
            value, called = conn.execute("SELECT last_value,is_called FROM realyu_purchase_subscription_id").fetchone()
            maximum = conn.execute("SELECT COALESCE(MAX(id),0) FROM realyu_funding_subscriptions").fetchone()[0]
            require(value + int(called) > maximum, "PURCHASE_SEQUENCE_NOT_AHEAD")
        if recovery:
            require(not any(counts.values()), "MANAGED_ORDERS_EXIST_FORWARD_ONLY")
        return {"applied": applied, **counts, "complete": len(applied) == 2}

    def check(self, complete=False, recovery=False):
        with self.connect(True) as conn:
            state = self.inspect(conn, complete, recovery)
        self.observe(state)
        return state

    def apply(self):
        with self.connect(False) as conn:
            acquired = False
            failed = False
            try:
                deadline = time.monotonic() + 2
                while not acquired:
                    acquired = conn.execute("SELECT pg_try_advisory_lock(%s)", (ADVISORY_LOCK_ID,)).fetchone()[0]
                    require(acquired or time.monotonic() < deadline, "SCHEMA_ADVISORY_LOCK_TIMEOUT")
                    if not acquired:
                        time.sleep(.05)
                self.inspect(conn, recovery=True)
                for name, checksum in REVIEWED.items():
                    state = self.inspect(conn, recovery=True)
                    if name in state["applied"]:
                        continue
                    content, digest = migration_content(self.files[name])
                    require(digest == checksum, "MIGRATION_FILE_CHANGED")
                    with conn.transaction():
                        conn.execute(content, prepare=False)
                        conn.execute("INSERT INTO schema_migrations(filename,checksum) VALUES(%s,%s)", (name, checksum))
                    # A later migration failure never undoes this committed prefix.
                    self.observe(self.inspect(conn, recovery=True))
                self.observe(self.inspect(conn, complete=True, recovery=True))
            except BaseException as failure:
                failed = True
                try:
                    self.observe({"first_failure": {"type": type(failure).__name__,
                        "code": str(failure) if isinstance(failure, UpdateError) else "PRIVATE_SCHEMA_FAILURE",
                        "sqlstate": getattr(failure, "sqlstate", None)}})
                except BaseException:
                    pass
                raise
            finally:
                if acquired:
                    try:
                        conn.execute("SELECT pg_advisory_unlock(%s)", (ADVISORY_LOCK_ID,))
                    except BaseException:
                        if not failed:
                            raise


def preflight(plan):
    require(plan.get("format") == 1 and re.fullmatch(r"[a-z0-9-]{8,100}", plan.get("operation_id", "")), "PLAN_FORMAT_OR_OPERATION_ID")
    files = reviewed_files(plan)
    require(30 <= plan.get("drain_seconds", 0) <= 60, "DRAIN_BUDGET")
    authority = read(plan["authority_receipt"])
    require(authority.get("phase") == "ACTIVE" and authority.get("opened") is True, "ACTIVE_AUTHORITY_REQUIRED")
    pinned = plan["verified_files"]
    for path, digest in pinned.items():
        require(sha(path) == digest, "PINNED_FILE_CHANGED")
    manifest = read(plan["manifest_path"])
    active = manifest.get("singlecore_api", {})
    expected_pins = {str(Path(__file__).resolve()), str(Path(native.__file__).resolve()), str(Path(release_control.__file__).resolve()), plan["manifest_path"], plan["authority_receipt"], active["env_file"], active["exe"], plan["candidate_binary"], plan["backup_evidence"]["path"], *files.values()}
    require({str(Path(p).resolve()) for p in expected_pins}.issubset({str(Path(p).resolve()) for p in pinned}), "REQUIRED_PIN_MISSING")
    require(active.get("sha") == plan["old_binary_sha256"] and sha(active["exe"]) == active["sha"], "OLD_BINARY_CHANGED")
    require(sha(plan["candidate_binary"]) == plan["new_binary_sha256"], "CANDIDATE_BINARY_CHANGED")
    require(plan["new_binary_sha256"] != plan["old_binary_sha256"] and plan["new_version"] != plan["old_version"], "DISTINCT_CANDIDATE_REQUIRED")
    env = read(active["env_file"])
    required = {"SERVER_HOST": "127.0.0.1", "SERVER_PORT": "18300", "DATABASE_HOST": DATABASE["host"],
                "DATABASE_PORT": str(DATABASE["port"]), "DATABASE_DBNAME": DATABASE["database"], "DATABASE_USER": DATABASE["user"],
                "REDIS_DB": "1", "REALYU_FUNDING_ENABLED": "true"}
    require(all(env.get(k) == v for k, v in required.items()), "AUTHORITY_CONFIGURATION_CHANGED")
    native.updated_environment(env, plan["env_additions"])
    destination, old = Path(plan["installed_binary"]).resolve(), Path(active["exe"]).resolve()
    root = BIN_ROOT.resolve()
    require(old.parent == root or old.parent.parent == root, "OLD_BINARY_OUTSIDE_FIXED_ROOT")
    require(destination.parent.parent == root and destination.name == "sub2api.exe" and destination.parent != old.parent and not destination.parent.exists() and re.fullmatch(r"[a-z0-9][a-z0-9-]{5,99}", destination.parent.name), "DESTINATION_NOT_ADJACENT_UNIQUE_VERSION")
    env_destination = Path(plan["new_env_file"]).resolve()
    require(env_destination.parent == Path(active["env_file"]).resolve().parent and not env_destination.exists(), "NEW_ENV_MUST_BE_ADJACENT_UNIQUE_FILE")
    require(not Path(plan["maintenance_marker"]).exists(), "MAINTENANCE_ALREADY_OWNED")
    require(not any((Path(plan["runtime_directory"]) / name).exists() for name in ("update-receipt.json", "previous-native-manifest.private.json")), "EXISTING_OPERATION")
    evidence, baseline = backup_evidence(plan, env)
    schema = Schema(env, files, baseline, server_version_num=evidence["source"]["server_version_num"])
    state = schema.check(recovery=True)
    return manifest, env, schema, state, evidence


class Host(native.Host):
    def __init__(self, plan, manifest, env, schema):
        self.schema, self.stopped, self.schema_state = schema, False, None
        super().__init__(plan, manifest, env)
        self.schema.observe = self.observe_schema

    def observe_schema(self, state):
        self.schema_state = state
        native.save(self.run / ("schema-" + str(time.time_ns()) + ".json"), state, exclusive=True)

    def receipt(self, journal):
        value = copy.deepcopy(journal)
        value.update(database_migrations=True, reviewed_migrations=REVIEWED, schema=self.schema_state,
                     additive_schema_retained=True, payment_enabled_by_update=False)
        super().receipt(value)

    def stop(self):
        self.stopped = False
        super().stop()
        self.stopped = True

    def install_manifest(self):
        self.owned()
        require(self.stopped, "NATIVE_STOP_REQUIRED_BEFORE_SCHEMA")
        self.schema.apply()
        super().install_manifest()

    def restore_native_manifest(self):
        self.owned()
        require(self.stopped, "NATIVE_STOP_REQUIRED_BEFORE_RECOVERY")
        self.schema.check(recovery=True)
        super().restore_native_manifest()

    def start(self):
        super().start()
        self.stopped = False

    def ready(self, version, digest):
        new = digest == self.plan["new_binary_sha256"]
        self.schema.check(complete=new, recovery=True)
        super().ready(version, digest)
        self.schema.check(complete=new, recovery=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    require(sha(args.plan) == args.expected_plan_sha256, "PLAN_CHANGED")
    plan = read(args.plan)
    manifest, env, schema, state, _ = preflight(plan)
    if not args.execute:
        print(json.dumps({"status": "PREFLIGHT_PASS", "production_changed": False,
                          "database_migrations": True, "schema": state}))
        return
    require(os.name == "nt" and ctypes.windll.shell32.IsUserAnAdmin(), "NORMAL_WINDOWS_ELEVATION_REQUIRED")
    with release_control.release_lock(Path(plan["maintenance_marker"]).parent):
        manifest, env, schema, _, _ = preflight(plan)
        host = Host(plan, manifest, env, schema)
        host.ready(plan["old_version"], plan["old_binary_sha256"])
        result = native.run_update(plan, host)
    print(json.dumps({"status": result["phase"], "database_migrations": True,
                      "database_restored": False, "additive_schema_retained": True}))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(json.dumps({"status": "FAILED", "code": str(exc) if isinstance(exc, UpdateError) else "PRIVATE_UPDATE_FAILURE", "type": type(exc).__name__}))
        raise SystemExit(1)

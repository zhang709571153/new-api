"""Private native supply snapshot and isolated, disabled-scheduler rehearsal import.

This is NOT a customer migration or a production activation tool. Source PG is
read-only port 28490. Rehearsal strips refresh tokens and disables scheduling;
the private export retains original credentials/state for later reviewed cutover.
Never upload its output to Git. Errors print codes, never SQL values or secrets.
"""
from __future__ import annotations

import argparse
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

TABLES = (
    "proxies", "groups", "accounts", "account_groups", "channels", "channel_groups",
    "channel_model_pricing", "channel_pricing_intervals", "channel_account_stats_pricing_rules",
    "channel_account_stats_model_pricing", "channel_account_stats_pricing_intervals", "composite_model_routes", "settings",
)
SETTING_KEYS = (
    "openai_oauth_scheduling_rate_multiplier", "openai_low_upstream_rate_priority_enabled",
    "openai_advanced_scheduler_weight_load", "openai_advanced_scheduler_weight_queue",
    "openai_advanced_scheduler_weight_error_rate", "openai_advanced_scheduler_weight_ttft",
    "openai_advanced_scheduler_weight_usage", "openai_advanced_scheduler_enabled",
)
FORBIDDEN = ("users", "api_keys", "usage_logs", "payment_orders", "user_subscriptions", "user_allowed_groups")
DEFERRED = {"proxies": ("backup_proxy_id",), "groups": ("fallback_group_id", "fallback_group_id_on_invalid_request"),
            "accounts": ("parent_account_id",)}


class SupplyError(ValueError):
    pass


def encode(value):
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    raise TypeError("UNSUPPORTED_COLUMN_VALUE")


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=encode)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def load_config(path):
    cfg = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    if "sub_env" in cfg:
        env = cfg["sub_env"]
        cfg = {"host": env["DATABASE_HOST"], "port": env["DATABASE_PORT"], "user": env["DATABASE_USER"],
               "password": env["DATABASE_PASSWORD"], "database": env["DATABASE_DBNAME"]}
    return cfg


def connect_config(cfg, source=False):
    import psycopg
    if cfg.get("host") not in ("127.0.0.1", "::1", "localhost"):
        raise SupplyError("LOOPBACK_REQUIRED")
    port = int(cfg.get("port", 0))
    if source:
        if port != 28490:
            raise SupplyError("SOURCE_PORT_MUST_BE_28490")
    elif port == 28490 or cfg.get("mode") != "rehearsal" or not re.fullmatch(r"[a-z][a-z0-9_]*(?:e2e|rehearsal|candidate)[a-z0-9_]*", cfg.get("database", "")):
        raise SupplyError("ISOLATED_REHEARSAL_TARGET_REQUIRED")
    options = "-c statement_timeout=30000 -c lock_timeout=5000"
    if source:
        options += " -c default_transaction_read_only=on"
    return psycopg.connect(host=cfg["host"], port=port, user=cfg["user"], password=cfg["password"],
                          dbname=cfg["database"], connect_timeout=5, options=options)


def columns(conn, table):
    rows = conn.execute("SELECT column_name,udt_name FROM information_schema.columns WHERE table_schema=current_schema() AND table_name=%s ORDER BY ordinal_position", (table,)).fetchall()
    if not rows:
        raise SupplyError("REQUIRED_SUPPLY_TABLE_MISSING:" + table)
    return [(name, typ) for name, typ in rows]


def table_snapshot(conn, table):
    from psycopg import sql
    cols = columns(conn, table)
    # Preserve JSON numeric lexemes (pricing) and credentials exactly as JSONB
    # text, without converting their numbers through Python float.
    expr = [sql.SQL("{}::text").format(sql.Identifier(n)) if t in ("json", "jsonb") else sql.Identifier(n) for n, t in cols]
    query = sql.SQL("SELECT {} FROM {}").format(sql.SQL(",").join(expr), sql.Identifier(table))
    params = ()
    if table == "settings":
        query += sql.SQL(" WHERE key=ANY(%s)")
        params = (list(SETTING_KEYS),)
    rows = [list(row) for row in conn.execute(query, params)]
    rows.sort(key=canonical)
    return {"columns": cols, "rows": rows}


def snapshot_connection(conn):
    tables = {table: table_snapshot(conn, table) for table in TABLES}
    payload = {"schema": 1, "kind": "realyu-native-supply-only", "tables": tables,
               "excluded_tables": list(FORBIDDEN), "pricing_files_included": False}
    # Convert typed decimal/timestamp scalars while JSON columns remain text.
    return json.loads(canonical(payload))


def export_snapshot(cfg):
    with connect_config(cfg, source=True) as conn:
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        return snapshot_connection(conn)


def private_write(path, payload):
    path = Path(path).resolve()
    if not ({"runtime", ".private"} & {p.lower() for p in path.parts}):
        raise SupplyError("PRIVATE_RUNTIME_OUTPUT_REQUIRED")
    for parent in path.parents:
        if (parent / ".git").exists():
            raise SupplyError("OUTPUT_INSIDE_GIT_FORBIDDEN")
    if path.exists():
        raise SupplyError("OUTPUT_ALREADY_EXISTS")
    path.parent.mkdir(parents=True, exist_ok=True)
    # Restrict a NEW empty file before putting any credentials into it. No ACL
    # changes are made to an existing directory or unrelated files.
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    try:
        if os.name == "nt":
            sid = subprocess.check_output(["powershell", "-NoProfile", "-Command", "[System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value"], text=True).strip()
            if not re.fullmatch(r"S-1-5-[0-9-]+", sid):
                raise SupplyError("PRIVATE_ACL_IDENTITY_FAILED")
            result = subprocess.run(["icacls", str(path), "/inheritance:r", "/grant:r", "*" + sid + ":F", "*S-1-5-18:F", "*S-1-5-32-544:F"], capture_output=True)
            if result.returncode:
                raise SupplyError("PRIVATE_ACL_FAILED")
        with path.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(canonical(payload) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def strip_refresh(value):
    if isinstance(value, dict):
        return {k: strip_refresh(v) for k, v in value.items() if k.lower().replace("_", "") != "refreshtoken"}
    if isinstance(value, list):
        return [strip_refresh(v) for v in value]
    return value


def rehearsal_tables(snapshot):
    if snapshot.get("schema") != 1 or snapshot.get("kind") != "realyu-native-supply-only" or set(snapshot.get("tables", {})) != set(TABLES):
        raise SupplyError("INVALID_SUPPLY_SNAPSHOT")
    result = json.loads(canonical(snapshot["tables"]))
    for table, data in result.items():
        names = [name for name, _ in data["columns"]]
        if len(names) != len(set(names)) or any(not re.fullmatch(r"[a-z][a-z0-9_]*", n) for n in names):
            raise SupplyError("INVALID_COLUMN_LIST")
        for row in data["rows"]:
            if len(row) != len(names):
                raise SupplyError("INVALID_ROW")
            if table == "settings" and row[names.index("key")] not in SETTING_KEYS:
                raise SupplyError("SETTING_NOT_ALLOWLISTED")
            if table == "accounts":
                row[names.index("schedulable")] = False
                idx = names.index("credentials")
                row[idx] = canonical(strip_refresh(json.loads(row[idx])))
    return result


def validate_no_customer_activity(conn):
    from psycopg import sql
    present = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables WHERE table_schema=current_schema()")}
    for table in ("api_keys", "usage_logs", "payment_orders", "user_subscriptions", "user_allowed_groups", "realyu_funding_requests"):
        if table in present and conn.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {})").format(sql.Identifier(table))).fetchone()[0]:
            raise SupplyError("TARGET_CUSTOMER_ACTIVITY:" + table)
    if "users" in present and conn.execute("SELECT EXISTS(SELECT 1 FROM users WHERE role<>'admin' OR balance<>0)").fetchone()[0]:
        raise SupplyError("TARGET_HAS_CUSTOMERS")


def import_connection(conn, snapshot, operation_id, allow_empty_bootstrap_groups=False):
    from psycopg import sql
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", operation_id):
        raise SupplyError("INVALID_OPERATION_ID")
    transformed = rehearsal_tables(snapshot)
    source_sha = digest(snapshot)
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(hashtextextended('realyu-supply-import',0))")
        conn.execute("CREATE TABLE IF NOT EXISTS realyu_supply_import_runs(operation_id TEXT PRIMARY KEY,source_sha256 CHAR(64) NOT NULL,target_sha256 CHAR(64) NOT NULL,receipt JSONB NOT NULL,created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())")
        conn.execute(sql.SQL("LOCK TABLE {} IN ACCESS EXCLUSIVE MODE").format(sql.SQL(",").join(sql.Identifier(t) for t in TABLES)))
        old = conn.execute("SELECT source_sha256,target_sha256,receipt FROM realyu_supply_import_runs WHERE operation_id=%s FOR UPDATE", (operation_id,)).fetchone()
        if old:
            if old[0] != source_sha:
                raise SupplyError("OPERATION_INPUT_CHANGED")
            if old[1] != digest(snapshot_connection(conn)):
                raise SupplyError("TARGET_SUPPLY_CHANGED_AFTER_IMPORT")
            return dict(old[2], idempotent_replay=True)
        validate_no_customer_activity(conn)
        for table in TABLES:
            count = conn.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            if count and table != "settings" and not (table == "groups" and allow_empty_bootstrap_groups):
                raise SupplyError("TARGET_SUPPLY_NOT_EMPTY:" + table)
            actual = dict(columns(conn, table))
            for name, typ in transformed[table]["columns"]:
                if actual.get(name) != typ:
                    raise SupplyError("COLUMN_TYPE_MISMATCH:" + table + "." + name)
        if allow_empty_bootstrap_groups:
            names = [n for n, _ in transformed["groups"]["columns"]]
            intended = {row[names.index("id")]: row[names.index("platform")] for row in transformed["groups"]["rows"]}
            for gid, platform in conn.execute("SELECT id,platform FROM groups"):
                if intended.get(gid) != platform:
                    raise SupplyError("BOOTSTRAP_GROUP_ID_OR_PLATFORM_MISMATCH")
        deferred = []
        for table in TABLES:
            data = transformed[table]
            names = [n for n, _ in data["columns"]]
            placeholders = [sql.SQL("%s::jsonb") if typ == "jsonb" else sql.Placeholder() for _, typ in data["columns"]]
            query = sql.SQL("INSERT INTO {}({}) VALUES({})").format(sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, names)), sql.SQL(",").join(placeholders))
            if table == "settings" or (table == "groups" and allow_empty_bootstrap_groups):
                key = "key" if table == "settings" else "id"
                updates = sql.SQL(",").join(sql.SQL("{}=EXCLUDED.{}").format(sql.Identifier(n), sql.Identifier(n)) for n in names if n != key)
                query += sql.SQL(" ON CONFLICT({}) DO UPDATE SET {}").format(sql.Identifier(key), updates)
            for original in data["rows"]:
                row = list(original)
                for name in DEFERRED.get(table, ()):
                    if name in names and row[names.index(name)] is not None:
                        deferred.append((table, name, row[names.index(name)], row[names.index("id")]))
                        row[names.index(name)] = None
                conn.execute(query, row)
        for table, name, value, rowid in deferred:
            conn.execute(sql.SQL("UPDATE {} SET {}=%s WHERE id=%s").format(sql.Identifier(table), sql.Identifier(name)), (value, rowid))
        for table in TABLES:
            if any(n == "id" for n, _ in transformed[table]["columns"]):
                sequence = conn.execute("SELECT pg_get_serial_sequence(%s,'id')", (table,)).fetchone()[0]
                if sequence:
                    conn.execute(sql.SQL("SELECT setval(%s,GREATEST(COALESCE((SELECT max(id) FROM {}),0),1),EXISTS(SELECT 1 FROM {}))").format(sql.Identifier(table), sql.Identifier(table)), (sequence,))
        target_sha = digest(snapshot_connection(conn))
        receipt = {"status": "REHEARSAL_SUPPLY_STAGED", "source_sha256": source_sha, "target_sha256": target_sha,
                   "counts": {t: len(v["rows"]) for t, v in transformed.items()}, "customer_tables_copied": [],
                   "refresh_tokens_removed": True, "all_account_scheduling_disabled": True,
                   "source_status_preserved": True, "idempotent_replay": False, "activated": False,
                   "pricing_files_included": False}
        conn.execute("INSERT INTO realyu_supply_import_runs(operation_id,source_sha256,target_sha256,receipt) VALUES(%s,%s,%s,%s::jsonb)", (operation_id, source_sha, target_sha, canonical(receipt)))
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect-source", "export", "import-rehearsal"))
    parser.add_argument("--source-config")
    parser.add_argument("--target-config")
    parser.add_argument("--snapshot")
    parser.add_argument("--output")
    parser.add_argument("--operation-id")
    parser.add_argument("--expected-sha256")
    parser.add_argument("--allow-empty-bootstrap-groups", action="store_true")
    args = parser.parse_args()
    try:
        if args.action in ("inspect-source", "export"):
            snap = export_snapshot(load_config(args.source_config))
            if args.action == "export":
                private_write(args.output, snap)
            result = {"status": "EXPORTED_PRIVATE" if args.action == "export" else "READ_ONLY_INSPECTED", "sha256": digest(snap), "counts": {t: len(v["rows"]) for t, v in snap["tables"].items()}, "customer_tables_copied": [], "pricing_files_included": False}
        else:
            snap = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
            if not args.expected_sha256 or digest(snap) != args.expected_sha256:
                raise SupplyError("SNAPSHOT_HASH_MISMATCH")
            with connect_config(load_config(args.target_config)) as conn:
                result = import_connection(conn, snap, args.operation_id, args.allow_empty_bootstrap_groups)
        print(canonical(result))
        return 0
    except SupplyError as exc:
        print(canonical({"status": "REJECTED", "code": str(exc)}))
    except Exception:
        print(canonical({"status": "ERROR", "code": "SUPPLY_OPERATION_FAILED_PRIVATE_DIAGNOSTICS_REQUIRED"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())

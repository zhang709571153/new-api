"""Read-only authority snapshot and isolated restore proof; never a ledger rollback tool.

Production connection is discovered from the active manifest, not caller DSNs.
Only a brand-new database on the independent loopback PostgreSQL is writable.
Raw dumps and full evidence are private. stdout contains only status and paths.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import uuid
from datetime import datetime, timezone

import psycopg
from psycopg import sql

SOURCE = {"host": "127.0.0.1", "port": 28490,
          "database": "realyu_singlecore_20261010_candidate",
          "user": "realyu_singlecore_owner_20261010"}
RESTORE_PREFIX = "realyu_backup_verify_"
MIN_FREE_BYTES = 2 * 1024 ** 3


class Refused(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    # Only writes inside this run's newly-created, private directory.
    with Path(path).open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2, default=str)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


def source_from_manifest(manifest_path):
    manifest = load(manifest_path)
    entry = manifest.get("singlecore_api", {})
    env_path = Path(entry["env_file"])
    env = load(env_path)
    result = {"host": env.get("DATABASE_HOST"), "port": int(env.get("DATABASE_PORT", 0)),
              "database": env.get("DATABASE_DBNAME"), "user": env.get("DATABASE_USER")}
    if result != SOURCE or env.get("SERVER_HOST") != "127.0.0.1" or str(env.get("SERVER_PORT")) != "18300":
        raise Refused("SOURCE_IDENTITY_MISMATCH")
    if not env.get("DATABASE_PASSWORD") or env.get("REALYU_FUNDING_ENABLED") != "true":
        raise Refused("SOURCE_CONFIGURATION_INVALID")
    if sha256(entry["exe"]) != entry.get("sha"):
        raise Refused("ACTIVE_BINARY_HASH_MISMATCH")
    result["password"] = env["DATABASE_PASSWORD"]
    return result, {"manifest_sha256": sha256(manifest_path), "env_sha256": sha256(env_path),
                    "env_path": str(env_path), "binary_sha256": entry["sha"]}


def isolated_config(path, name):
    cfg = load(path)
    if cfg.get("host") != "127.0.0.1" or int(cfg.get("port", 0)) != 29490:
        raise Refused("RESTORE_ENDPOINT_NOT_ISOLATED")
    if not re.fullmatch(RESTORE_PREFIX + r"[a-z0-9_]{8,40}", name):
        raise Refused("RESTORE_DATABASE_NAME_INVALID")
    if not cfg.get("user") or not cfg.get("password"):
        raise Refused("RESTORE_CREDENTIALS_MISSING")
    return {"host": "127.0.0.1", "port": 29490, "database": name,
            "user": cfg["user"], "password": cfg["password"]}


def connect(cfg, *, readonly=True, admin=False):
    return psycopg.connect(host=cfg["host"], port=cfg["port"],
                           dbname="postgres" if admin else cfg["database"],
                           user=cfg["user"], password=cfg["password"], connect_timeout=10,
                           application_name="realyu_snapshot_backup_readonly" if readonly else "realyu_isolated_restore",
                           options="-c timezone=UTC -c lock_timeout=10000 -c statement_timeout=180000 "
                                   + ("-c default_transaction_read_only=on" if readonly else ""),
                           autocommit=admin)


def private_directory(root):
    root = Path(root)
    root.mkdir(parents=True, exist_ok=False)
    if os.name == "nt":
        who = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True, check=True)
        sid = list(csv.reader(who.stdout.decode("utf-8", errors="strict").splitlines()))[0][1]
        if not re.fullmatch(r"S-1-5-[0-9-]+", sid):
            raise Refused("PRIVATE_ACL_IDENTITY_INVALID")
        subprocess.run(["icacls", str(root), "/inheritance:r", "/grant:r",
                        f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
    else:
        root.chmod(0o700)
    return root


def check_disk(path, database_bytes):
    free = shutil.disk_usage(path).free
    # Source disk already holds production. Reserve space for dump, restored database,
    # indexes/WAL and temporary sorting; this is a conservative gate, not size prediction.
    required = max(MIN_FREE_BYTES, database_bytes * 6)
    if free < required:
        raise Refused("INSUFFICIENT_FREE_DISK")
    return {"free_bytes": free, "required_free_bytes": required}


def tool_version(exe):
    proc = subprocess.run([str(exe), "--version"], capture_output=True, timeout=15, check=True)
    text = proc.stdout.decode("utf-8", errors="strict").strip()
    match = re.search(r"\(PostgreSQL\) (\d+)(?:\.(\d+))?", text)
    if not match:
        raise Refused("POSTGRES_TOOL_VERSION_INVALID")
    return {"version": text, "major": int(match.group(1)), "sha256": sha256(exe)}


def database_identity(conn):
    row = conn.execute("""SELECT current_database(),current_user,pg_get_userbyid(datdba),
        current_setting('server_version_num')::int,current_setting('server_version'),
        host(inet_server_addr()),inet_server_port(),pg_database_size(current_database()),
        pg_current_wal_lsn()::text FROM pg_database WHERE datname=current_database()""").fetchone()
    return dict(zip(("database", "user", "owner", "server_version_num", "server_version",
                     "host", "port", "database_bytes", "wal_lsn"), row))


def verify_source(identity):
    if any(identity.get(k) != v for k, v in SOURCE.items()) or identity.get("owner") != SOURCE["user"]:
        raise Refused("CONNECTED_SOURCE_IDENTITY_MISMATCH")


def canonical_constraint(definition):
    # PostgreSQL reparses varchar-literal-array -> text[] coercion on restore as
    # per-element text coercions. Normalize only this exact, equivalent constant
    # expression; retain every literal, constraint operator and other cast.
    literal = r"'(?:[^']|'')*'"
    source = rf"ARRAY\[((?:{literal}::character varying)(?:, {literal}::character varying)*)\]::text\[\]"
    target = rf"ARRAY\[((?:{literal}::character varying::text)(?:, {literal}::character varying::text)*)\]"
    definition = re.sub(source, lambda m: "ARRAY[" + m.group(1).replace("::character varying", "::text") + "]", definition)
    return re.sub(target, lambda m: "ARRAY[" + m.group(1).replace("::character varying::text", "::text") + "]", definition)


def schema_evidence(conn):
    # Normalize catalog semantics rather than pg_dump's per-run restriction token,
    # ownership or grants. Object definitions, not customer rows, form this digest.
    where = "n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'"
    queries = {
        "columns": f"""SELECT n.nspname,c.relname,c.relkind,
            row_number() OVER (PARTITION BY n.nspname,c.relname ORDER BY a.attnum),a.attname,
            pg_catalog.format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,
            pg_get_expr(d.adbin,d.adrelid) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            JOIN pg_attribute a ON a.attrelid=c.oid AND a.attnum>0 AND NOT a.attisdropped
            LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
            WHERE {where} AND c.relkind IN ('r','p','v','m','S') ORDER BY 1,2,4""",
        "constraints": f"""SELECT n.nspname,c.relname,x.conname,x.contype,pg_get_constraintdef(x.oid,true),x.convalidated
            FROM pg_constraint x JOIN pg_class c ON c.oid=x.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE {where} ORDER BY 1,2,3""",
        "indexes": """SELECT schemaname,tablename,indexname,indexdef FROM pg_indexes
            WHERE schemaname NOT LIKE 'pg_%' AND schemaname<>'information_schema' ORDER BY 1,2,3""",
        "views": f"""SELECT n.nspname,c.relname,pg_get_viewdef(c.oid,true) FROM pg_class c
            JOIN pg_namespace n ON n.oid=c.relnamespace WHERE {where} AND c.relkind IN ('v','m') ORDER BY 1,2""",
        "sequences": """SELECT schemaname,sequencename,data_type::text,start_value,min_value,max_value,increment_by,cycle,cache_size
            FROM pg_sequences WHERE schemaname NOT LIKE 'pg_%' AND schemaname<>'information_schema' ORDER BY 1,2""",
        "functions": f"""SELECT n.nspname,p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid)
            FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE {where} AND p.prokind IN ('f','p') ORDER BY 1,2,3""",
        "triggers": f"""SELECT n.nspname,c.relname,t.tgname,pg_get_triggerdef(t.oid,true),t.tgenabled
            FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE {where} AND NOT t.tgisinternal ORDER BY 1,2,3""",
        "extensions": "SELECT extname,extversion FROM pg_extension ORDER BY extname",
    }
    data = {name: conn.execute(query).fetchall() for name, query in queries.items()}
    data["constraints"] = [(*row[:4], canonical_constraint(row[4]), *row[5:]) for row in data["constraints"]]
    tables = conn.execute(f"SELECT n.nspname,c.relname FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE {where} AND c.relkind IN ('r','p') ORDER BY 1,2").fetchall()
    data["tables"] = tables
    return {"sha256": digest(data), "table_count": len(tables),
            "hash_method": "canonical-catalog-v2-live-column-order-literal-array-coercion-excludes-ownership-acl-sequence-current-value"}, tables, data


def table_hashes(conn, tables):
    result = {}
    for index, (schema, table) in enumerate(tables):
        h = hashlib.sha256()
        count = 0
        query = sql.SQL("SELECT row_to_json(t)::text FROM ONLY {}.{} t ORDER BY row_to_json(t)::text COLLATE \"C\"").format(sql.Identifier(schema), sql.Identifier(table))
        with conn.cursor(name=f"backup_verify_{index}") as cur:
            cur.itersize = 1000
            cur.execute(query)
            for (value,) in cur:
                raw = value.encode("utf-8")
                h.update(len(raw).to_bytes(8, "big"))
                h.update(raw)
                count += 1
        result[f"{schema}.{table}"] = {"rows": count, "sha256": h.hexdigest()}
    return result


def migrations(conn):
    return [dict(zip(("filename", "checksum", "applied_at"), row)) for row in
            conn.execute("SELECT filename,checksum,applied_at::text FROM public.schema_migrations ORDER BY filename").fetchall()]


def verify_migrations(rows, source_migrations):
    by_name = {x["filename"]: x for x in rows}
    # The exact current filename is discovered from source; do not guess it.
    expected = list(Path(source_migrations).glob("305_*.sql"))
    if len(expected) != 1 or expected[0].name not in by_name:
        raise Refused("EXPECTED_305_MIGRATION_MISSING")
    if any(not re.match(r"^\d+", x["filename"]) for x in rows):
        raise Refused("SOURCE_MIGRATION_FILENAME_INVALID")
    if any(int(re.match(r"^\d+", x["filename"]).group()) >= 306 for x in rows):
        raise Refused("SOURCE_ALREADY_HAS_POST_305_MIGRATIONS")
    for row in rows:
        path = Path(source_migrations) / row["filename"]
        # Native uses strings.TrimSpace on UTF-8, preserving internal CR/LF bytes.
        if not path.is_file() or hashlib.sha256(path.read_bytes().decode("utf-8").strip().encode("utf-8")).hexdigest() != row["checksum"]:
            raise Refused("SOURCE_MIGRATION_CHECKSUM_MISMATCH")


def child(exe, args, cfg, output_dir, label, readonly):
    env = {k: v for k, v in os.environ.items() if not k.upper().startswith("PG")}
    env.update(PGPASSWORD=cfg["password"], PGCONNECT_TIMEOUT="10", PGAPPNAME="realyu_snapshot_dump" if readonly else "realyu_isolated_restore",
               PGOPTIONS="-c timezone=UTC -c lock_timeout=10000" + (" -c default_transaction_read_only=on" if readonly else ""))
    with (output_dir / f"{label}.stdout.private.log").open("xb") as out, (output_dir / f"{label}.stderr.private.log").open("xb") as err:
        proc = subprocess.run([str(exe), *args], env=env, stdout=out, stderr=err, timeout=900)
    if proc.returncode:
        raise Refused(f"{label.upper()}_FAILED")


def connection_args(cfg):
    return ["--host", cfg["host"], "--port", str(cfg["port"]), "--username", cfg["user"], "--dbname", cfg["database"], "--no-password"]


def create_restore_database(cfg):
    if cfg["host"] != "127.0.0.1" or cfg["port"] != 29490 or not cfg["database"].startswith(RESTORE_PREFIX):
        raise Refused("RESTORE_WRITE_GUARD")
    with connect(cfg, readonly=False, admin=True) as admin:
        if admin.execute("SELECT 1 FROM pg_database WHERE datname=%s", (cfg["database"],)).fetchone():
            raise Refused("RESTORE_DATABASE_ALREADY_EXISTS")
        admin.execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(cfg["database"])))


def verify_restored(source, restored):
    if source["schema"]["sha256"] != restored["schema"]["sha256"]:
        raise Refused("RESTORE_SCHEMA_HASH_MISMATCH")
    if source["schema_migrations"] != restored["schema_migrations"]:
        raise Refused("RESTORE_MIGRATIONS_MISMATCH")
    if source["table_hashes"] != restored["table_hashes"]:
        raise Refused("RESTORE_ROW_HASH_MISMATCH")


def execute(args):
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + uuid.uuid4().hex[:10]
    output = private_directory(Path(args.output_root) / run_id)
    phase = "PREFLIGHT"
    try:
        src, pins = source_from_manifest(args.manifest)
        dst = isolated_config(args.isolated_config, RESTORE_PREFIX + run_id.lower())
        dump_exe, restore_exe = Path(args.pg_bin) / "pg_dump.exe", Path(args.pg_bin) / "pg_restore.exe"
        versions = {"pg_dump": tool_version(dump_exe), "pg_restore": tool_version(restore_exe)}
        with connect(dst, readonly=True, admin=True) as c:
            target_identity = database_identity(c)
            if target_identity["host"] != "127.0.0.1" or target_identity["port"] != 29490:
                raise Refused("CONNECTED_RESTORE_ENDPOINT_MISMATCH")
        phase = "SOURCE_SNAPSHOT"
        with connect(src) as source:
            source.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            ident = database_identity(source)
            verify_source(ident)
            if any(v["major"] != ident["server_version_num"] // 10000 for v in versions.values()) or target_identity["server_version_num"] // 10000 != ident["server_version_num"] // 10000:
                raise Refused("POSTGRES_MAJOR_VERSION_MISMATCH")
            disk = check_disk(output, ident["database_bytes"])
            snapshot = source.execute("SELECT pg_export_snapshot()").fetchone()[0]
            captured = now()
            schema, tables, catalog = schema_evidence(source)
            recorded = migrations(source)
            verify_migrations(recorded, args.migrations)
            source_proof = {"schema": schema, "schema_migrations": recorded, "table_hashes": table_hashes(source, tables)}
            save(output / "source-snapshot.private.json", {**source_proof, "catalog": catalog, "source": ident, "snapshot": snapshot, "captured_at": captured})
            phase = "PG_DUMP"
            backup = output / "authority.snapshot.dump"
            schema_dump = output / "authority.schema.sql"
            base = connection_args(src) + ["--snapshot", snapshot, "--no-owner", "--no-acl", "--lock-wait-timeout=10s"]
            child(dump_exe, base + ["--format=custom", "--file", str(backup)], src, output, "pg_dump", True)
            child(dump_exe, base + ["--schema-only", "--file", str(schema_dump)], src, output, "pg_dump_schema", True)
        # The dump is immutable input for restore; source traffic continues normally.
        phase = "ISOLATED_RESTORE"
        create_restore_database(dst)
        child(restore_exe, connection_args(dst) + ["--exit-on-error", "--single-transaction", "--no-owner", "--no-acl", str(backup)], dst, output, "pg_restore", False)
        phase = "RESTORE_VERIFY"
        with connect(dst) as restored:
            restored.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            rschema, rtables, _ = schema_evidence(restored)
            restore_proof = {"schema": rschema, "schema_migrations": migrations(restored), "table_hashes": table_hashes(restored, rtables)}
        verify_restored(source_proof, restore_proof)
        save(output / "restore-verification.private.json", restore_proof)
        if sha256(args.manifest) != pins["manifest_sha256"] or sha256(pins["env_path"]) != pins["env_sha256"]:
            raise Refused("ACTIVE_CONFIGURATION_CHANGED_DURING_BACKUP")
        dump_sha = sha256(backup)
        receipt = {"version": 1, "kind": "singlecore-pg-snapshot-restore", "status": "PASS", "captured_at": captured,
                   "source": ident, "pins": pins, "tools": versions, "disk": disk, "schema": schema,
                   "schema_migrations": recorded,
                   "backup": {"path": str(backup), "sha256": dump_sha, "size_bytes": backup.stat().st_size, "format": "custom"},
                   "schema_dump": {"path": str(schema_dump), "sha256": sha256(schema_dump)},
                   "restore": {"status": "PASS", "host": dst["host"], "port": dst["port"], "database": dst["database"],
                               "server_version_num": target_identity["server_version_num"], "dump_sha256": dump_sha,
                               "verified_at": now(), "table_count": len(tables), "row_hashes_match": True,
                               "schema_hash_match": True, "migrations_match": True},
                   "limitation": "Consistent logical snapshot only. LSN is observation, not PITR. Never restore over authority after subsequent financial writes. Ownership/ACL require separate reviewed provisioning."}
        save(output / "receipt.private.json", receipt)
        summary = {k: receipt[k] for k in ("version", "kind", "status", "captured_at", "schema", "limitation")}
        summary.update({"backup_sha256": dump_sha, "backup_size_bytes": backup.stat().st_size,
                        "schema_dump_sha256": sha256(schema_dump), "source_server_version": ident["server_version"],
                        "restored_server_version_num": target_identity["server_version_num"],
                        "migration_count": len(recorded), "latest_migration": recorded[-1]["filename"],
                        "total_rows_verified": sum(x["rows"] for x in source_proof["table_hashes"].values()),
                        "all_table_hashes_match": True, "restore_verified_at": receipt["restore"]["verified_at"]})
        save(output / "summary.redacted.json", summary)
        print(json.dumps({"status": "PASS", "receipt": str(output / "receipt.private.json"), "summary": str(output / "summary.redacted.json")}))
        return 0
    except Exception as error:
        # SQL/subprocess errors may contain row values or connection material. Never
        # stringify them into public output or generic diagnostic receipts.
        failure = {"status": "FAILED", "phase": phase, "at": now(), "exception_type": type(error).__name__,
                   "code": str(error) if isinstance(error, Refused) else "PRIVATE_OPERATION_FAILED"}
        save(output / "first-failure.json", failure)
        print(json.dumps({**failure, "evidence_directory": str(output)}))
        return 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--isolated-config", required=True)
    parser.add_argument("--pg-bin", required=True)
    parser.add_argument("--migrations", required=True)
    parser.add_argument("--output-root", required=True)
    return execute(parser.parse_args())


if __name__ == "__main__":
    sys.exit(main())

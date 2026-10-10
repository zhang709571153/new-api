"""Pinned 308-only history schema/program update; default is read-only preflight.

Reuses native admission, drain, Session-0/SCM ownership and same-PostgreSQL
recovery. It never imports history, restores a database, changes payments or
networking, or starts a legacy writer. Existing managed orders remain valid.
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
import singlecore_additive_update as additive


UpdateError = native.UpdateError
require, read, sha = native.require, native.read, native.sha
DATABASE, BIN_ROOT = additive.DATABASE, additive.BIN_ROOT
ADVISORY_LOCK_ID = additive.ADVISORY_LOCK_ID
BASELINE_COUNT = 299
BASELINE_SHA256 = "9e617c32ee712fd94b12f23632cf0ac68005e85c0985216eb3979590f393380f"
OLD_VERSION = "realyu-singlecore-v0.2.15-20261010-ux"
OLD_BINARY_SHA256 = "88d749b7a8cb354959f7343280dea51843d68a34f9cc3619919294abfacad081"
MIGRATION = "308_realyu_usage_history.sql"
REVIEWED = {MIGRATION: "3ee70f5ffb0910c1693641c686a24e567ea2c0b87d32a7cb2084cffdccb3048c"}
TABLES = ("realyu_legacy_self_usage", "realyu_legacy_deleted_actor_usage",
          "realyu_legacy_usage_details", "realyu_usage_history_imports")
COUNT_FIELDS = ("self_rows", "deleted_actor_rows", "detail_rows", "existing_team_rows", "quarantined_rows", "source_consume_rows")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


def validate_prefix(current, baseline, complete=False):
    require(len(baseline) == BASELINE_COUNT and digest(baseline) == BASELINE_SHA256,
            "ONLY_REVIEWED_307_BASELINE_ALLOWED")
    require({k: v for k, v in current.items() if k != MIGRATION} == baseline, "HISTORICAL_SCHEMA_CHANGED")
    applied = MIGRATION in current
    require(not applied or current[MIGRATION] == REVIEWED[MIGRATION], "HISTORY_SCHEMA_CHECKSUM_MISMATCH")
    require(not complete or applied, "REVIEWED_MIGRATIONS_PENDING")
    return [MIGRATION] if applied else []


def reviewed_files(plan):
    require(plan.get("database_migrations") is True, "HISTORY_SCHEMA_PLAN_REQUIRED")
    rows = plan.get("migrations", [])
    require(isinstance(rows, list) and len(rows) == 1 and rows[0].get("filename") == MIGRATION,
            "ONLY_REVIEWED_308_ALLOWED")
    row = rows[0]
    require(row.get("checksum") == REVIEWED[MIGRATION], "UNREVIEWED_MIGRATION_CHECKSUM")
    require(Path(row["path"]).name == MIGRATION, "MIGRATION_PATH_NAME_MISMATCH")
    require(additive.migration_content(row["path"])[1] == REVIEWED[MIGRATION], "MIGRATION_FILE_CHANGED")
    return {MIGRATION: row["path"]}


def archive_evidence(plan):
    reference = plan["archive_manifest"]
    require(sha(reference["path"]) == reference["sha256"], "ARCHIVE_MANIFEST_FILE_CHANGED")
    value = read(reference["path"])
    require(value.get("version") == 1 and re.fullmatch(r"[A-Za-z0-9_-]{8,80}", value.get("installation_id", "")), "ARCHIVE_MANIFEST_IDENTITY")
    for field in ("source_sha256", "manifest_sha256", "facts_sha256", "deleted_actor_sha256", "details_sha256"):
        require(isinstance(value.get(field), str) and re.fullmatch(r"[0-9a-f]{64}", value[field]), "ARCHIVE_MANIFEST_CHECKSUM")
    unsigned = {k: v for k, v in value.items() if k != "manifest_sha256"}
    require(digest(unsigned) == value["manifest_sha256"], "ARCHIVE_MANIFEST_DIGEST_MISMATCH")
    counts = value.get("counts", {})
    require(all(type(counts.get(k)) is int and counts[k] >= 0 for k in COUNT_FIELDS), "ARCHIVE_MANIFEST_COUNTS")
    require(counts["source_consume_rows"] > 0 and counts["self_rows"] + counts["deleted_actor_rows"] + counts["existing_team_rows"] + counts["quarantined_rows"] == counts["source_consume_rows"], "ARCHIVE_MANIFEST_PARTITION")
    require(counts["detail_rows"] == counts["source_consume_rows"] - counts["quarantined_rows"], "ARCHIVE_MANIFEST_DETAILS")
    require(type(value.get("source_started_at")) is int and type(value.get("source_ended_at")) is int and 0 < value["source_started_at"] <= value["source_ended_at"], "ARCHIVE_MANIFEST_TIME_RANGE")
    return value


def backup_evidence(plan, env, now=None):
    # The reviewed old helper still validates the restored backup and all files.
    # Remove only 308 from its candidate inventory; its 306/307 rules are not run
    # against the live database or used to decide recovery in this updater.
    candidate = additive.migration_map(plan["candidate_migrations"])
    proxy = copy.deepcopy(plan)
    proxy["candidate_migrations"] = [r for r in plan["candidate_migrations"] if r["filename"] != MIGRATION]
    evidence, baseline = additive.backup_evidence(proxy, env, now)
    validate_prefix(baseline, baseline)
    require(candidate == {**baseline, **REVIEWED}, "CANDIDATE_HAS_UNREVIEWED_OR_MISSING_MIGRATION")
    return evidence, baseline


def normalized_check(value):
    value = re.sub(r"::(?:text|bigint|integer)", "", value.lower())
    value = re.sub(r"'([0-9]+)'", r"\1", value)
    return re.sub(r"[\s()]", "", value)


class Schema:
    def __init__(self, env, files, baseline, archive, observe=None, connect=None, schema_name="public", server_version_num=None):
        self.env, self.files, self.baseline, self.archive = env, files, baseline, archive
        self.schema_name, self.server_version_num = schema_name, server_version_num
        self.observe = observe or (lambda state: None)
        self.connect = connect or self.connection
        self.archive_fingerprint = None

    def connection(self, readonly=True):
        import psycopg
        env = self.env
        return psycopg.connect(host=env["DATABASE_HOST"], port=int(env["DATABASE_PORT"]),
            dbname=env["DATABASE_DBNAME"], user=env["DATABASE_USER"], password=env["DATABASE_PASSWORD"],
            connect_timeout=3, autocommit=True, application_name="realyu-history-update",
            options="-c timezone=UTC -c statement_timeout=8000 -c lock_timeout=2000 -c default_transaction_read_only=" + ("on" if readonly else "off"))

    def artifacts(self, conn, applied):
        rows = conn.execute("""SELECT c.relname,c.relkind,c.relrowsecurity,c.relowner::regrole::text
            FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname=current_schema() AND c.relname=ANY(%s) ORDER BY c.relname""", (list(TABLES),)).fetchall()
        if not applied:
            require(not rows, "UNRECEIPTED_HISTORY_SCHEMA_EXISTS")
            return
        require({r[0] for r in rows} == set(TABLES) and all(r[1:] == ('r', False, self.env['DATABASE_USER']) for r in rows), "HISTORY_TABLE_SHAPE_MISMATCH")
        fact = [("model", "text", True, ""), ("input_tokens", "bigint", True, ""), ("output_tokens", "bigint", True, ""),
                ("cost_quota", "bigint", True, ""), ("duration_ms", "bigint", True, ""), ("created_at", "timestamp with time zone", True, ""),
                ("fact_sha256", "character(64)", True, ""), ("snapshot_sha256", "character(64)", True, "")]
        shapes = {
            TABLES[0]: [("source_log_id", "bigint", True, ""), ("actor_user_id", "bigint", True, ""), ("scope", "text", True, ""), *fact],
            TABLES[1]: [("source_log_id", "bigint", True, ""), ("source_actor_id", "bigint", True, ""), *fact],
            TABLES[2]: [("source_log_id", "bigint", True, ""), *[(k, "bigint", False, "") for k in ("cache_read_tokens", "cache_creation_tokens", "input_tokens_total")],
                       ("usage_semantic", "text", True, ""), ("fact_sha256", "character(64)", True, ""), ("snapshot_sha256", "character(64)", True, "")],
            TABLES[3]: [("source_sha256", "character(64)", True, ""), ("installation_id", "text", True, ""), ("manifest_sha256", "character(64)", True, ""),
                       *[(k, "bigint", True, "") for k in ("imported_self_rows", "imported_deleted_actor_rows", "imported_detail_rows", "existing_team_rows", "quarantined_rows", "source_consume_rows")],
                       ("source_started_at", "timestamp with time zone", True, ""), ("source_ended_at", "timestamp with time zone", True, ""),
                       ("created_at", "timestamp with time zone", True, "now()")],
        }
        base_checks = ["input_tokens>=0", "output_tokens>=0", "cost_quota>=0 AND cost_quota<=9007199254740991", "duration_ms>=0"]
        checks = {TABLES[0]: [*base_checks, "scope=ANY(ARRAY['personal','legacy_unknown'])"], TABLES[1]: base_checks,
                  TABLES[2]: ["cache_read_tokens>=0", "cache_creation_tokens>=0", "input_tokens_total>=0", "usage_semantic=ANY(ARRAY['reported','anthropic'])"], TABLES[3]: []}
        for table in TABLES:
            columns = conn.execute("""SELECT a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,COALESCE(pg_get_expr(d.adbin,d.adrelid),'')
                FROM pg_attribute a LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum
                WHERE a.attrelid=%s::regclass AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum""", (table,)).fetchall()
            require(columns == shapes[table], "HISTORY_COLUMN_SHAPE_MISMATCH")
            constraints = conn.execute("""SELECT contype,pg_get_constraintdef(oid),convalidated,condeferrable,condeferred
                FROM pg_constraint WHERE conrelid=%s::regclass ORDER BY contype,conname""", (table,)).fetchall()
            require(all(r[2:] == (True, False, False) for r in constraints), "HISTORY_UNVALIDATED_CONSTRAINT")
            actual_checks = sorted(normalized_check(r[1][6:]) for r in constraints if r[0] == 'c')
            require(actual_checks == sorted(normalized_check(s) for s in checks[table]), "HISTORY_CHECK_CONSTRAINT_MISMATCH")
            pk = "source_sha256" if table == TABLES[3] else "source_log_id"
            expected = [('p', f'PRIMARY KEY ({pk})')]
            if table == TABLES[0]: expected.append(('f', 'FOREIGN KEY (actor_user_id) REFERENCES users(id)'))
            # PostgreSQL 18 also stores NOT NULL in pg_constraint. Column
            # attnotnull is checked above; when catalog entries exist, verify
            # their exact columns as well rather than treating them as FKs.
            not_null = sorted(r[1] for r in constraints if r[0] == 'n')
            require(not not_null or not_null == sorted('NOT NULL ' + r[0] for r in shapes[table] if r[2]), "HISTORY_NOT_NULL_CONSTRAINT_MISMATCH")
            require(sorted((r[0], r[1]) for r in constraints if r[0] not in ('c','n')) == sorted(expected), "HISTORY_PRIMARY_OR_FOREIGN_KEY_MISMATCH")
            indexes = conn.execute("""SELECT x.relname,i.indisunique,i.indisprimary,i.indisvalid,i.indisready,am.amname,
                ARRAY(SELECT a.attname FROM unnest(i.indkey) WITH ORDINALITY AS k(num,ord)
                    JOIN pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.num ORDER BY k.ord),
                i.indoption::smallint[],i.indnkeyatts,i.indpred IS NULL,i.indexprs IS NULL
                FROM pg_index i JOIN pg_class x ON x.oid=i.indexrelid JOIN pg_am am ON am.oid=x.relam
                WHERE i.indrelid=%s::regclass ORDER BY x.relname""", (table,)).fetchall()
            expected_indexes = [(table + '_pkey', True, True, True, True, 'btree', [pk], [0], 1, True, True)]
            if table == TABLES[0]: expected_indexes.append(('realyu_legacy_self_usage_actor', False, False, True, True, 'btree', ['actor_user_id','created_at'], [0,3], 2, True, True))
            require(indexes == sorted(expected_indexes), "HISTORY_INDEX_MISMATCH")
            require(conn.execute("SELECT count(*) FROM pg_trigger WHERE tgrelid=%s::regclass AND NOT tgisinternal", (table,)).fetchone() == (0,), "HISTORY_UNREVIEWED_TRIGGER")

    def archived(self, conn, applied):
        state = {"status": "empty", "counts": [0, 0, 0, 0]}
        if applied:
            # Table names are fixed constants, never supplied by the plan.
            counts = [conn.execute('SELECT count(*) FROM ' + table).fetchone()[0] for table in TABLES]
            if any(counts):
                a, c = self.archive, self.archive['counts']
                require(counts == [c['self_rows'], c['deleted_actor_rows'], c['detail_rows'], 1], "HISTORY_PARTIAL_OR_FOREIGN_ARCHIVE")
                receipt = conn.execute("""SELECT source_sha256,installation_id,manifest_sha256,imported_self_rows,imported_deleted_actor_rows,
                    imported_detail_rows,existing_team_rows,quarantined_rows,source_consume_rows,source_started_at,source_ended_at
                    FROM realyu_usage_history_imports""").fetchall()
                expected = (a['source_sha256'], a['installation_id'], a['manifest_sha256'], *[c[k] for k in COUNT_FIELDS],
                    datetime.fromtimestamp(a['source_started_at'], timezone.utc), datetime.fromtimestamp(a['source_ended_at'], timezone.utc))
                require(receipt == [expected], "HISTORY_ARCHIVE_RECEIPT_MISMATCH")
                queries = (
                    ("SELECT source_log_id,actor_user_id,scope,model,input_tokens,output_tokens,cost_quota,duration_ms,created_at,fact_sha256,snapshot_sha256 FROM realyu_legacy_self_usage ORDER BY source_log_id", 'facts_sha256'),
                    ("SELECT source_log_id,source_actor_id,model,input_tokens,output_tokens,cost_quota,duration_ms,created_at,fact_sha256,snapshot_sha256 FROM realyu_legacy_deleted_actor_usage ORDER BY source_log_id", 'deleted_actor_sha256'),
                    ("SELECT source_log_id,cache_read_tokens,cache_creation_tokens,input_tokens_total,usage_semantic,fact_sha256,snapshot_sha256 FROM realyu_legacy_usage_details ORDER BY source_log_id", 'details_sha256'),
                )
                for query, key in queries:
                    values = conn.execute(query).fetchall()
                    require(all(row[-1] == a['source_sha256'] for row in values), "HISTORY_SNAPSHOT_BINDING_CHANGED")
                    # The importer computes details_sha256 before appending the
                    # already-bound snapshot SHA; facts/deleted hashes include it.
                    hashed = [row[:-1] for row in values] if key == 'details_sha256' else values
                    require(digest(hashed) == a[key], "IMMUTABLE_HISTORY_FACTS_CHANGED")
                state = {'status': 'sealed', 'counts': counts, 'manifest_sha256': a['manifest_sha256']}
        fingerprint = digest(state)
        require(self.archive_fingerprint is None or fingerprint == self.archive_fingerprint, "ARCHIVE_CHANGED_DURING_RELEASE")
        self.archive_fingerprint = fingerprint
        return state

    def inspect(self, conn, complete=False):
        identity = conn.execute("SELECT current_database(),current_user,host(inet_server_addr()),inet_server_port(),current_schema(),pg_is_in_recovery(),current_setting('server_version_num')").fetchone()
        expected = (self.env['DATABASE_DBNAME'],self.env['DATABASE_USER'],self.env['DATABASE_HOST'],int(self.env['DATABASE_PORT']),self.schema_name,False)
        require(identity[:6] == expected and (self.server_version_num is None or str(identity[6]) == str(self.server_version_num)), "CONNECTED_DATABASE_IDENTITY_MISMATCH")
        require(conn.execute("SELECT phase,installation_id,source_sha256 FROM realyu_migration_state WHERE singleton").fetchone() == ('active',self.archive['installation_id'],self.archive['source_sha256']), "ACTIVE_HISTORY_AUTHORITY_MISMATCH")
        current = dict(conn.execute('SELECT filename,checksum FROM schema_migrations ORDER BY filename').fetchall())
        applied = validate_prefix(current, self.baseline, complete)
        self.artifacts(conn, bool(applied))
        archive = self.archived(conn, bool(applied))
        return {'applied': applied, 'complete': bool(applied), 'migration_count': len(current), 'archive': archive}

    def check(self, complete=False):
        with self.connect(True) as conn:
            with conn.transaction():
                conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                state = self.inspect(conn, complete)
        self.observe(state)
        return state

    def apply(self):
        with self.connect(False) as conn:
            acquired, failed = False, False
            try:
                deadline = time.monotonic() + 2
                while not acquired:
                    acquired = conn.execute('SELECT pg_try_advisory_lock(%s)', (ADVISORY_LOCK_ID,)).fetchone()[0]
                    require(acquired or time.monotonic() < deadline, 'SCHEMA_ADVISORY_LOCK_TIMEOUT')
                    if not acquired: time.sleep(.05)
                state = self.inspect(conn)
                if not state['complete']:
                    content, checksum = additive.migration_content(self.files[MIGRATION])
                    require(checksum == REVIEWED[MIGRATION], 'MIGRATION_FILE_CHANGED')
                    with conn.transaction():
                        conn.execute(content, prepare=False)
                        conn.execute('INSERT INTO schema_migrations(filename,checksum) VALUES(%s,%s)', (MIGRATION, checksum))
                        # Shape failures roll back both schema and its receipt.
                        self.inspect(conn, complete=True)
                self.observe(self.inspect(conn, complete=True))
            except BaseException as error:
                failed = True
                try: self.observe({'first_failure': {'type': type(error).__name__, 'code': str(error) if isinstance(error, UpdateError) else 'PRIVATE_SCHEMA_FAILURE', 'sqlstate': getattr(error, 'sqlstate', None)}})
                except BaseException: pass
                raise
            finally:
                if acquired:
                    try: conn.execute('SELECT pg_advisory_unlock(%s)', (ADVISORY_LOCK_ID,))
                    except BaseException:
                        if not failed: raise


def preflight(plan):
    require(plan.get('format') == 1 and re.fullmatch(r'[a-z0-9-]{8,100}', plan.get('operation_id','')), 'PLAN_FORMAT_OR_OPERATION_ID')
    require(plan.get('old_version') == OLD_VERSION and plan.get('old_binary_sha256') == OLD_BINARY_SHA256, 'ONLY_CURRENT_UX_RECOVERY_BASELINE_ALLOWED')
    files = reviewed_files(plan)
    require(30 <= plan.get('drain_seconds',0) <= 60, 'DRAIN_BUDGET')
    authority = read(plan['authority_receipt'])
    require(authority.get('phase') == 'ACTIVE' and authority.get('opened') is True, 'ACTIVE_AUTHORITY_REQUIRED')
    for path, value in plan['verified_files'].items(): require(sha(path) == value, 'PINNED_FILE_CHANGED')
    manifest = read(plan['manifest_path']); active = manifest.get('singlecore_api',{})
    expected_pins = {str(Path(__file__).resolve()), str(Path(native.__file__).resolve()), str(Path(additive.__file__).resolve()),
        str(Path(release_control.__file__).resolve()), plan['manifest_path'], plan['authority_receipt'], active['env_file'], active['exe'],
        plan['candidate_binary'], plan['backup_evidence']['path'], plan['archive_manifest']['path'], *files.values()}
    require({str(Path(p).resolve()) for p in expected_pins}.issubset({str(Path(p).resolve()) for p in plan['verified_files']}), 'REQUIRED_PIN_MISSING')
    require(active.get('sha') == plan['old_binary_sha256'] and sha(active['exe']) == active['sha'], 'OLD_BINARY_CHANGED')
    require(sha(plan['candidate_binary']) == plan['new_binary_sha256'], 'CANDIDATE_BINARY_CHANGED')
    require(plan['new_binary_sha256'] != plan['old_binary_sha256'] and plan['new_version'] != plan['old_version'], 'DISTINCT_CANDIDATE_REQUIRED')
    env = read(active['env_file'])
    expected_env = {'SERVER_HOST':'127.0.0.1','SERVER_PORT':'18300','DATABASE_HOST':DATABASE['host'],'DATABASE_PORT':str(DATABASE['port']),
        'DATABASE_DBNAME':DATABASE['database'],'DATABASE_USER':DATABASE['user'],'REDIS_DB':'1','REALYU_FUNDING_ENABLED':'true'}
    require(all(env.get(k) == v for k,v in expected_env.items()), 'AUTHORITY_CONFIGURATION_CHANGED')
    require(plan['env_additions'] == {'REALYU_CUSTOMER_USD_TO_CNY':env.get('REALYU_CUSTOMER_USD_TO_CNY')} and env.get('REALYU_CUSTOMER_USD_TO_CNY') == '7', 'HISTORY_RELEASE_CANNOT_CHANGE_SETTINGS')
    destination, old, root = Path(plan['installed_binary']).resolve(), Path(active['exe']).resolve(), BIN_ROOT.resolve()
    require(old.parent == root or old.parent.parent == root, 'OLD_BINARY_OUTSIDE_FIXED_ROOT')
    require(destination.parent.parent == root and destination.name == 'sub2api.exe' and destination.parent != old.parent and not destination.parent.exists()
        and re.fullmatch(r'[a-z0-9][a-z0-9-]{5,99}', destination.parent.name), 'DESTINATION_NOT_ADJACENT_UNIQUE_VERSION')
    env_destination = Path(plan['new_env_file']).resolve()
    require(env_destination.parent == Path(active['env_file']).resolve().parent and not env_destination.exists(), 'NEW_ENV_MUST_BE_ADJACENT_UNIQUE_FILE')
    require(not Path(plan['maintenance_marker']).exists(), 'MAINTENANCE_ALREADY_OWNED')
    require(not any((Path(plan['runtime_directory'])/name).exists() for name in ('update-receipt.json','previous-native-manifest.private.json')), 'EXISTING_OPERATION')
    evidence, baseline = backup_evidence(plan, env)
    archive = archive_evidence(plan)
    schema = Schema(env, files, baseline, archive, server_version_num=evidence['source']['server_version_num'])
    return manifest, env, schema, schema.check(), evidence


class Host(native.Host):
    def __init__(self, plan, manifest, env, schema):
        self.schema, self.stopped, self.schema_state = schema, False, None
        super().__init__(plan, manifest, env)
        self.schema.observe = self.observe_schema

    def observe_schema(self, state):
        self.schema_state = state
        native.save(self.run/('schema-'+str(time.time_ns())+'.json'), state, exclusive=True)

    def receipt(self, journal):
        value = copy.deepcopy(journal)
        value.update(database_migrations=True, reviewed_migrations=REVIEWED, schema=self.schema_state,
            additive_schema_retained=True, history_imported_by_update=False, payment_enabled_by_update=False)
        super().receipt(value)

    def stop(self):
        self.stopped = False
        super().stop()
        self.stopped = True

    def install_manifest(self):
        self.owned()
        require(self.stopped, 'NATIVE_STOP_REQUIRED_BEFORE_SCHEMA')
        self.schema.apply()
        super().install_manifest()

    def restore_native_manifest(self):
        self.owned()
        require(self.stopped, 'NATIVE_STOP_REQUIRED_BEFORE_RECOVERY')
        self.schema.check()
        super().restore_native_manifest()

    def start(self):
        super().start()
        self.stopped = False

    def ready(self, version, value):
        new = value == self.plan['new_binary_sha256']
        self.schema.check(complete=new)
        super().ready(version, value)
        self.schema.check(complete=new)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--expected-plan-sha256', required=True)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    require(sha(args.plan) == args.expected_plan_sha256, 'PLAN_CHANGED')
    plan = read(args.plan)
    manifest, env, schema, state, _ = preflight(plan)
    if not args.execute:
        print(json.dumps({'status':'PREFLIGHT_PASS','production_changed':False,'database_migrations':True,'schema':state}))
        return
    require(os.name == 'nt' and ctypes.windll.shell32.IsUserAnAdmin(), 'NORMAL_WINDOWS_ELEVATION_REQUIRED')
    with release_control.release_lock(Path(plan['maintenance_marker']).parent):
        manifest, env, schema, _, _ = preflight(plan)
        host = Host(plan, manifest, env, schema)
        host.ready(plan['old_version'], plan['old_binary_sha256'])
        result = native.run_update(plan, host)
    print(json.dumps({'status':result['phase'],'database_migrations':True,'database_restored':False,'additive_schema_retained':True,'history_imported_by_update':False}))


if __name__ == '__main__':
    try: main()
    except BaseException as error:
        print(json.dumps({'status':'FAILED','code':str(error) if isinstance(error, UpdateError) else 'PRIVATE_UPDATE_FAILURE','type':type(error).__name__}))
        raise SystemExit(1)

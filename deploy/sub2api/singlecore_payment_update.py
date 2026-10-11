"""Pinned 309/310 precision and administrative entitlement update.

Runs through the existing short admission/drain/SCM workflow. Never changes
wallet values, payment configuration, networking, or restores a live database.
On startup failure, recover UX5 against the retained widened decimal columns.
"""
from __future__ import annotations
import argparse, copy, ctypes, hashlib, json, os, re, time
from datetime import datetime, timezone
from pathlib import Path
from decimal import Decimal
import release_control
import singlecore_native_update as native
import singlecore_additive_update as additive

UpdateError = native.UpdateError
require, read, sha = native.require, native.read, native.sha
DATABASE, BIN_ROOT = additive.DATABASE, additive.BIN_ROOT
ADVISORY_LOCK_ID = additive.ADVISORY_LOCK_ID
OLD_VERSION = 'realyu-singlecore-v0.2.15-20261011-ux5'
OLD_BINARY_SHA256 = 'ed4a0f9524693c8e3047a5f637dfae6c9176099a116207a138e896f43b174ab3'
BASELINE_COUNT = 300
BASELINE_SHA256 = '900b7497db886b1a096b9792533092fe332d815d0959f7661956b73cbbc68553'
MIGRATION = '309_realyu_recharge_precision.sql'
ADMIN_MIGRATION = '310_realyu_admin_entitlements.sql'
REVIEWED = {MIGRATION: '72e75892e3ee0f65552e75902ce7cb2b8a763790a557f0da458eb06587999c23',
            ADMIN_MIGRATION: 'e8a6da52c33068dc15d4a868d78e56656ba35664bad0b720614e463db74147a7'}

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()).hexdigest()


def validate_prefix(current, baseline, complete=False):
    require(len(baseline) == BASELINE_COUNT and digest(baseline) == BASELINE_SHA256,
            "ONLY_REVIEWED_308_BASELINE_ALLOWED")
    require({k: v for k, v in current.items() if k not in REVIEWED} == baseline, "HISTORICAL_SCHEMA_CHANGED")
    applied = [name for name in REVIEWED if name in current]
    require(applied == list(REVIEWED)[:len(applied)], "MIGRATIONS_NOT_A_REVIEWED_PREFIX")
    require(all(current[name] == REVIEWED[name] for name in applied), "PAYMENT_PRECISION_SCHEMA_CHECKSUM_MISMATCH")
    require(not complete or len(applied) == len(REVIEWED), "REVIEWED_MIGRATIONS_PENDING")
    return applied


def reviewed_files(plan):
    require(plan.get("database_migrations") is True, "PAYMENT_PRECISION_SCHEMA_PLAN_REQUIRED")
    rows = plan.get("migrations", [])
    require(isinstance(rows, list) and [row.get('filename') for row in rows] == list(REVIEWED),
            "ONLY_REVIEWED_309_310_ALLOWED")
    for row in rows:
        name = row['filename']
        require(row.get("checksum") == REVIEWED[name], "UNREVIEWED_MIGRATION_CHECKSUM")
        require(Path(row["path"]).name == name, "MIGRATION_PATH_NAME_MISMATCH")
        require(additive.migration_content(row["path"])[1] == REVIEWED[name], "MIGRATION_FILE_CHANGED")
    return {row['filename']: row['path'] for row in rows}



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
    baseline = additive.migration_map(evidence["schema_migrations"])
    validate_prefix(baseline, baseline)
    expected = {**baseline, **REVIEWED}
    require(additive.migration_map(plan["candidate_migrations"]) == expected, "CANDIDATE_HAS_UNREVIEWED_OR_MISSING_MIGRATION")
    return evidence, baseline



class Schema:
    def __init__(self, env, files, baseline, observe=None, connect=None, schema_name='public', server_version_num=None):
        self.env, self.files, self.baseline = env, files, baseline
        self.observe = observe or (lambda state: None)
        self.connect = connect or self.connection
        self.schema_name, self.server_version_num = schema_name, server_version_num

    def connection(self, readonly=True):
        import psycopg
        e=self.env
        return psycopg.connect(host=e['DATABASE_HOST'],port=int(e['DATABASE_PORT']),dbname=e['DATABASE_DBNAME'],user=e['DATABASE_USER'],password=e['DATABASE_PASSWORD'],connect_timeout=3,autocommit=True,application_name='realyu-payment-precision-update',options='-c statement_timeout=8000 -c lock_timeout=2000 -c default_transaction_read_only='+('on' if readonly else 'off'))

    def inspect(self, conn, complete=False):
        identity=conn.execute("SELECT current_database(),current_user,host(inet_server_addr()),inet_server_port(),current_schema(),pg_is_in_recovery(),current_setting('server_version_num')").fetchone()
        expected=(self.env['DATABASE_DBNAME'],self.env['DATABASE_USER'],self.env['DATABASE_HOST'],int(self.env['DATABASE_PORT']),self.schema_name,False)
        require(identity[:6]==expected and (self.server_version_num is None or str(identity[6])==str(self.server_version_num)), 'CONNECTED_DATABASE_IDENTITY_MISMATCH')
        require(conn.execute('SELECT phase FROM realyu_migration_state WHERE singleton').fetchone()==('active',), 'DATABASE_NOT_ACTIVE')
        current=dict(conn.execute('SELECT filename,checksum FROM schema_migrations ORDER BY filename').fetchall())
        applied=validate_prefix(current,self.baseline,complete)
        columns=conn.execute("SELECT column_name,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='payment_orders' AND column_name IN ('amount','bonus_amount','refund_amount','pay_amount') ORDER BY column_name").fetchall()
        shape=(26,8) if MIGRATION in applied else (20,2)
        require(columns==[(name,*( (20,2) if name=='pay_amount' else shape)) for name in ['amount','bonus_amount','pay_amount','refund_amount']], 'PAYMENT_PRECISION_SHAPE_MISMATCH')
        require(conn.execute("SELECT value FROM settings WHERE key='BALANCE_PAYMENT_DISABLED'").fetchone()==('true',), 'RECHARGE_MUST_REMAIN_CLOSED_DURING_RELEASE')
        require(conn.execute("SELECT count(*) FROM payment_orders WHERE order_type='balance'").fetchone()==(0,), 'EXISTING_RECHARGE_REQUIRES_RECONCILIATION')
        if ADMIN_MIGRATION in applied:
            fields=conn.execute("SELECT column_name,data_type FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='realyu_admin_entitlement_ops' ORDER BY ordinal_position").fetchall()
            require(fields==[('actor_user_id','bigint'),('request_key','text'),('request_sha256','character'),('user_id','bigint'),('team_id','bigint'),('subscription_id','bigint'),('action','text'),('reason','text'),('before_value','jsonb'),('after_value','jsonb'),('created_at','timestamp with time zone')], 'ADMIN_ENTITLEMENT_SHAPE_MISMATCH')
            default=conn.execute("SELECT column_default FROM information_schema.columns WHERE table_schema=current_schema() AND table_name='realyu_teams' AND column_name='id'").fetchone()
            require(default and 'realyu_team_id' in default[0] and default[0].startswith('nextval('), 'TEAM_SEQUENCE_DEFAULT_MISMATCH')
            value, called=conn.execute('SELECT last_value,is_called FROM realyu_team_id').fetchone()
            maximum=conn.execute('SELECT COALESCE(max(id),0) FROM realyu_teams').fetchone()[0]
            require(value+(1 if called else 0)>maximum, 'TEAM_SEQUENCE_BEHIND_EXISTING_IDS')
        return {'applied':applied,'complete':len(applied)==len(REVIEWED),'migration_count':len(current),'recharge_closed':True}

    def financial_rows(self,conn):
        # Only these numeric columns are altered; preserve their exact values.
        return conn.execute('SELECT id,amount,bonus_amount,refund_amount,pay_amount FROM payment_orders ORDER BY id').fetchall()

    def entitlement_rows(self,conn):
        # In-memory fingerprints only; never write keys or customer rows to receipts.
        tables=('realyu_teams','realyu_team_members','realyu_funding_subscriptions','realyu_key_scopes','users','api_keys')
        return {name:conn.execute('SELECT md5(row_to_json(t)::text) FROM '+name+' t ORDER BY 1').fetchall() for name in tables}

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
                    with conn.transaction():
                        before = self.financial_rows(conn)
                        entitlements = self.entitlement_rows(conn)
                        for name, checksum in REVIEWED.items():
                            if name in state['applied']: continue
                            content, actual = additive.migration_content(self.files[name])
                            require(actual == checksum, 'MIGRATION_FILE_CHANGED')
                            conn.execute(content, prepare=False)
                            conn.execute('INSERT INTO schema_migrations(filename,checksum) VALUES(%s,%s)', (name, checksum))
                        # Shape failures roll back both schema and its receipt.
                        self.inspect(conn, complete=True)
                        require(before == self.financial_rows(conn), 'ORDER_AMOUNTS_CHANGED')
                        require(entitlements == self.entitlement_rows(conn), 'EXISTING_ENTITLEMENTS_CHANGED')
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
        plan['candidate_binary'], plan['backup_evidence']['path'], *files.values()}
    require({str(Path(p).resolve()) for p in expected_pins}.issubset({str(Path(p).resolve()) for p in plan['verified_files']}), 'REQUIRED_PIN_MISSING')
    require(active.get('sha') == plan['old_binary_sha256'] and sha(active['exe']) == active['sha'], 'OLD_BINARY_CHANGED')
    require(sha(plan['candidate_binary']) == plan['new_binary_sha256'], 'CANDIDATE_BINARY_CHANGED')
    require(plan['new_binary_sha256'] != plan['old_binary_sha256'] and plan['new_version'] != plan['old_version'], 'DISTINCT_CANDIDATE_REQUIRED')
    env = read(active['env_file'])
    expected_env = {'SERVER_HOST':'127.0.0.1','SERVER_PORT':'18300','DATABASE_HOST':DATABASE['host'],'DATABASE_PORT':str(DATABASE['port']),
        'DATABASE_DBNAME':DATABASE['database'],'DATABASE_USER':DATABASE['user'],'REDIS_DB':'1','REALYU_FUNDING_ENABLED':'true'}
    require(all(env.get(k) == v for k,v in expected_env.items()), 'AUTHORITY_CONFIGURATION_CHANGED')
    require(plan['env_additions'] == {'REALYU_CUSTOMER_USD_TO_CNY':env.get('REALYU_CUSTOMER_USD_TO_CNY')} and env.get('REALYU_CUSTOMER_USD_TO_CNY') == '7', 'PRECISION_RELEASE_CANNOT_CHANGE_SETTINGS')
    destination, old, root = Path(plan['installed_binary']).resolve(), Path(active['exe']).resolve(), BIN_ROOT.resolve()
    require(old.is_relative_to(root) and old.name == 'sub2api.exe', 'OLD_BINARY_OUTSIDE_FIXED_ROOT')
    require(destination.parent.parent == root and destination.name == 'sub2api.exe' and destination.parent != old.parent and not destination.parent.exists()
        and re.fullmatch(r'[a-z0-9][a-z0-9-]{5,99}', destination.parent.name), 'DESTINATION_NOT_ADJACENT_UNIQUE_VERSION')
    env_destination = Path(plan['new_env_file']).resolve()
    require(env_destination.parent == Path(active['env_file']).resolve().parent and not env_destination.exists(), 'NEW_ENV_MUST_BE_ADJACENT_UNIQUE_FILE')
    require(not Path(plan['maintenance_marker']).exists(), 'MAINTENANCE_ALREADY_OWNED')
    require(not any((Path(plan['runtime_directory'])/name).exists() for name in ('update-receipt.json','previous-native-manifest.private.json')), 'EXISTING_OPERATION')
    evidence, baseline = backup_evidence(plan, env)
    schema = Schema(env, files, baseline, server_version_num=evidence['source']['server_version_num'])
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
            additive_schema_retained=True, wallet_values_changed_by_update=False, payment_enabled_by_update=False)
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
    print(json.dumps({'status':result['phase'],'database_migrations':True,'database_restored':False,'additive_schema_retained':True,'wallet_values_changed_by_update':False}))


if __name__ == '__main__':
    try: main()
    except BaseException as error:
        print(json.dumps({'status':'FAILED','code':str(error) if isinstance(error, UpdateError) else 'PRIVATE_UPDATE_FAILURE','type':type(error).__name__}))
        raise SystemExit(1)

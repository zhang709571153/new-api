"""Receipt-pinned 308 rehearsal on an already verified, isolated snapshot.

No production connection, service, gateway, refresh, billing replay, or generic
target DSN is accepted. The original importer production guard is unchanged.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import uuid

import singlecore_pg_backup as backup
import singlecore_pg_backup307 as baseline

NEW_TABLES={"public.realyu_legacy_self_usage","public.realyu_legacy_deleted_actor_usage",
            "public.realyu_legacy_usage_details","public.realyu_usage_history_imports"}

def check(condition,code):
    if not condition:raise backup.Refused(code)

def receipt_target(path,expected_sha,isolated_path,migrations):
    check(backup.sha256(path)==expected_sha,"RESTORE_RECEIPT_PIN_MISMATCH")
    receipt=backup.load(path);restore=receipt["restore"]
    check(receipt.get("version")==1 and receipt.get("kind")=="singlecore-pg-snapshot-restore" and receipt.get("status")=="PASS","PASSED_SNAPSHOT_REQUIRED")
    backup.verify_source(receipt["source"])
    check(restore.get("status")=="PASS" and all(restore.get(k) is True for k in ("row_hashes_match","schema_hash_match","migrations_match")),"RESTORE_PROOF_REQUIRED")
    check(restore.get("host")=="127.0.0.1" and restore.get("port")==29490,"RESTORE_ENDPOINT_NOT_ISOLATED")
    cfg=backup.isolated_config(isolated_path,restore["database"])
    check(cfg["database"]==restore["database"],"RESTORE_DATABASE_MISMATCH")
    captured=datetime.fromisoformat(receipt["captured_at"].replace("Z","+00:00"))
    verified=datetime.fromisoformat(restore["verified_at"].replace("Z","+00:00"))
    check(0<=(datetime.now(timezone.utc)-captured).total_seconds()<=86400 and verified>=captured,"RESTORE_RECEIPT_EXPIRED_OR_UNORDERED")
    dump=receipt["backup"]
    check(dump.get("format")=="custom" and Path(dump["path"]).stat().st_size==dump["size_bytes"] and backup.sha256(dump["path"])==dump["sha256"]==restore.get("dump_sha256"),"VERIFIED_DUMP_PIN_MISMATCH")
    check(backup.sha256(receipt["schema_dump"]["path"])==receipt["schema_dump"]["sha256"],"SCHEMA_DUMP_PIN_MISMATCH")
    baseline.verify_migrations(receipt["schema_migrations"],migrations)
    return receipt,cfg

def snapshot(conn):
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        schema,tables,_=backup.schema_evidence(conn)
        return {"schema":schema,"schema_migrations":backup.migrations(conn),"table_hashes":backup.table_hashes(conn,tables)}

def execute(args):
    out=backup.private_directory(Path(args.output_root)/(datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")+"_"+uuid.uuid4().hex[:10]))
    phase="RECEIPT_GUARD"
    try:
        check(backup.sha256(backup.__file__)==baseline.BASE_RUNNER_SHA256,"REVIEWED_BACKUP_RUNNER_CHANGED")
        receipt,cfg=receipt_target(args.restore_receipt,args.receipt_sha256,args.isolated_config,args.migrations)
        check(backup.sha256(args.importer)==args.importer_sha256,"IMPORTER_PIN_MISMATCH")
        migration=Path(args.migrations)/"308_realyu_usage_history.sql"
        check(backup.sha256(migration)==args.migration_sha256,"MIGRATION_308_PIN_MISMATCH")
        check(re.fullmatch(r"[A-Za-z0-9_-]{8,80}",args.installation_id),"INSTALLATION_REQUIRED")
        reviewed=backup.load(args.reviewed_manifest)
        check(reviewed.get("source_sha256")==args.source_sha256 and reviewed.get("installation_id")==args.installation_id and reviewed.get("counts",{}).get("source_consume_rows")==31295,"REVIEWED_SEALED_HISTORY_REQUIRED")
        spec=importlib.util.spec_from_file_location("reviewed_usage_importer",args.importer)
        importer=importlib.util.module_from_spec(spec);spec.loader.exec_module(importer)
        source=importer.read_snapshot(Path(args.source),args.source_sha256)
        source_proof=backup.load(Path(args.restore_receipt).parent/"source-snapshot.private.json")
        restore_proof=backup.load(Path(args.restore_receipt).parent/"restore-verification.private.json")
        backup.verify_restored(source_proof,restore_proof)
        check(source_proof["schema"]==receipt["schema"] and source_proof["schema_migrations"]==receipt["schema_migrations"],"RESTORE_SIDECAR_DRIFT")
        phase="EXACT_RESTORED_DATABASE_VERIFY"
        with backup.connect(cfg,readonly=False) as conn:
            conn.autocommit=True
            identity=backup.database_identity(conn)
            check(identity["host"]=="127.0.0.1" and identity["port"]==29490 and identity["database"]==cfg["database"] and identity["owner"]==cfg["user"],"CONNECTED_TARGET_MISMATCH")
            check(conn.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()").fetchone()[0]==0,"RESTORED_DATABASE_IN_USE")
            before=snapshot(conn);backup.verify_restored(source_proof,before)
            backup.save(out/"before.private.json",before)
            check(conn.execute("SELECT installation_id,phase,source_sha256 FROM realyu_migration_state WHERE singleton").fetchone()==(args.installation_id,"active",args.source_sha256),"RESTORED_AUTHORITY_MARKER_MISMATCH")
            phase="ISOLATED_MIGRATION_308"
            raw=migration.read_bytes().decode("utf-8");checksum=hashlib.sha256(raw.strip().encode()).hexdigest()
            with conn.transaction():
                conn.execute("SET LOCAL lock_timeout='5s'")
                conn.execute("SET LOCAL statement_timeout='30s'")
                check(conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0]==299,"RESTORED_SCHEMA_ALREADY_CHANGED")
                conn.execute(raw)
                conn.execute("INSERT INTO schema_migrations(filename,checksum) VALUES(%s,%s)",(migration.name,checksum))
            phase="FULL_HISTORY_INSPECTION"
            manifest=importer.inspect(conn,source,args.source_sha256,args.installation_id)
            check(manifest==reviewed,"SCALE_REVIEWED_MANIFEST_MISMATCH")
            backup.save(out/"manifest.private.json",manifest)
            phase="FULL_HISTORY_APPLY"
            first=importer.apply(conn,source,args.source_sha256,args.installation_id,manifest)
            check(first["idempotent_replay"] is False,"EXPECTED_FIRST_ARCHIVE")
            backup.save(out/"first-apply.private.json",first)
            phase="FULL_HISTORY_IDEMPOTENT_REPLAY"
            replay=importer.apply(conn,source,args.source_sha256,args.installation_id,manifest)
            check(replay["idempotent_replay"] is True,"FULL_REPLAY_NOT_IDEMPOTENT")
            backup.save(out/"replay.private.json",replay)
            phase="SOURCE_AND_FINANCIAL_RECONCILIATION"
            # Every source ID, including four existing operator-only orphans, is
            # represented exactly once; details are additional columns, not usage.
            rows=conn.execute("""SELECT source_log_id,input_tokens,output_tokens,cost_quota FROM realyu_legacy_self_usage
                UNION ALL SELECT source_log_id,input_tokens,output_tokens,cost_quota FROM realyu_legacy_team_usage
                UNION ALL SELECT source_log_id,input_tokens,output_tokens,cost_quota FROM realyu_legacy_deleted_actor_usage
                UNION ALL SELECT source_log_id,input_tokens,output_tokens,cost_quota FROM realyu_legacy_team_usage_orphans
                ORDER BY 1""").fetchall()
            check(len(rows)==31295 and len({r[0] for r in rows})==31295,"SOURCE_ID_COVERAGE_OR_DUPLICATION")
            check(importer.digest([r[0] for r in rows])==manifest["source_ids_sha256"],"SOURCE_ID_DIGEST_MISMATCH")
            totals=dict(zip(("input_tokens","output_tokens","cost_quota"),(sum(r[i] for r in rows) for i in (1,2,3))))
            check(totals==manifest["source_totals"],"SOURCE_EXACT_TOTALS_MISMATCH")
            after=snapshot(conn);backup.save(out/"after.private.json",after)
            old=before["table_hashes"];new=after["table_hashes"]
            unchanged={name:value for name,value in old.items() if name!="public.schema_migrations"}
            check(set(new)-set(old)==NEW_TABLES,"UNEXPECTED_TABLE_ADDED")
            check(all(new.get(name)==value for name,value in unchanged.items()),"PREEXISTING_ROWS_OR_FINANCES_CHANGED")
            check(after["schema_migrations"][:-1]==before["schema_migrations"] and after["schema_migrations"][-1]["filename"]==migration.name and after["schema_migrations"][-1]["checksum"]==checksum,"MIGRATION_HISTORY_DRIFT")
        result={"version":1,"status":"PASS","kind":"isolated-full-usage-history-rehearsal","finished_at":backup.now(),
                "restore_receipt_sha256":args.receipt_sha256,"dump_sha256":receipt["backup"]["sha256"],"database":cfg["database"],"host":"127.0.0.1","port":29490,
                "source_sha256":args.source_sha256,"importer_sha256":args.importer_sha256,"migration_sha256":args.migration_sha256,"migration_checksum":checksum,
                "counts":manifest["counts"],"source_totals":totals,"source_ids_sha256":manifest["source_ids_sha256"],"manifest_sha256":manifest["manifest_sha256"],
                "visible_totals":manifest["visible_totals"],"deleted_actor_totals":manifest["deleted_actor_totals"],"quarantined_totals":manifest["quarantined_totals"],
                "idempotent_replay":True,"preexisting_tables_unchanged":len(unchanged),"preexisting_rows_sha256_before":backup.digest(unchanged),
                "preexisting_rows_sha256_after":backup.digest({name:new[name] for name in unchanged}),"production_modified":False,"gateway_started":False,"refresh_started":False,
                "limitation":"Isolated snapshot and immutable facts only; no permission to restore or rewind the production ledger."}
        backup.save(out/"result.redacted.json",result)
        print(json.dumps({"status":"PASS","report":str(out/"result.redacted.json"),"counts":manifest["counts"]}))
        return 0
    except Exception as error:
        failure={"status":"FAILED","phase":phase,"exception_type":type(error).__name__,"code":str(error) if isinstance(error,backup.Refused) else "PRIVATE_REHEARSAL_FAILED","production_modified":False}
        backup.save(out/"first-failure.json",failure)
        print(json.dumps({**failure,"evidence_directory":str(out)}));return 1

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--execute",action="store_true")
    for field in ("restore-receipt","receipt-sha256","isolated-config","migrations","importer","importer-sha256","migration-sha256","source","source-sha256","installation-id","reviewed-manifest","output-root"):
        p.add_argument("--"+field)
    args=p.parse_args()
    if not args.execute:print('{"status":"PREPARED_NOT_RUN","database_accessed":false}');return 0
    if any(v is None for k,v in vars(args).items() if k!="execute"):
        print('{"status":"BLOCKED","code":"ALL_PINNED_INPUTS_REQUIRED"}');return 2
    return execute(args)

if __name__=="__main__":raise SystemExit(main())

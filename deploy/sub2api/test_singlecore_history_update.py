import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid

import singlecore_history_update as update
from test_singlecore_native_update import Clock


def manifest_fixture():
    at = datetime(2026, 9, 1, tzinfo=timezone.utc)
    source = 'a' * 64
    facts = [(42, 1, 'personal', 'synthetic-model', 100, 10, 1, 1000, at, 'b' * 64, source)]
    details = [(42, 80, None, None, 'reported', 'c' * 64)]
    value = {'version': 1, 'source_sha256': source, 'installation_id': 'synthetic-history-installation',
        'counts': {'self_rows': 1, 'deleted_actor_rows': 0, 'detail_rows': 1, 'existing_team_rows': 0, 'quarantined_rows': 0, 'source_consume_rows': 1},
        'facts_sha256': update.digest(facts), 'deleted_actor_sha256': update.digest([]), 'details_sha256': update.digest(details),
        'source_started_at': int(at.timestamp()), 'source_ended_at': int(at.timestamp())}
    value['manifest_sha256'] = update.digest(value)
    return value, facts, [(*row, source) for row in details]


class HistoryUpdateTests(unittest.TestCase):
    def test_308_requires_declared_schema_and_reviewed_file(self):
        for plan in ({'database_migrations': False}, {'database_migrations': True, 'migrations': []},
            {'database_migrations': True, 'migrations': [{'filename': '309_other.sql'}]},
            {'database_migrations': True, 'migrations': [{'filename': update.MIGRATION, 'checksum': 'f' * 64}]}):
            with self.assertRaises(update.UpdateError): update.reviewed_files(plan)

    def test_exact_baseline_and_only_one_append(self):
        baseline = {'307_fixture.sql': 'a' * 64}
        with patch.object(update, 'BASELINE_COUNT', 1), patch.object(update, 'BASELINE_SHA256', update.digest(baseline)):
            self.assertEqual(update.validate_prefix(baseline, baseline), [])
            self.assertEqual(update.validate_prefix({**baseline, **update.REVIEWED}, baseline, True), [update.MIGRATION])
            for current in ({}, {**baseline, update.MIGRATION: 'b' * 64}, {**baseline, '309_bad.sql': 'b' * 64}):
                with self.assertRaises(update.UpdateError): update.validate_prefix(current, baseline)
            with self.assertRaisesRegex(update.UpdateError, 'PENDING'): update.validate_prefix(baseline, baseline, True)
        with self.assertRaisesRegex(update.UpdateError, '307_BASELINE'): update.validate_prefix(baseline, baseline)

    def test_archive_manifest_content_not_only_file_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'archive.json'
            value = manifest_fixture()[0]
            def validate(v):
                path.write_text(json.dumps(v), encoding='utf-8')
                return update.archive_evidence({'archive_manifest': {'path': str(path), 'sha256': update.sha(path)}})
            self.assertEqual(validate(value), value)
            bad = copy.deepcopy(value); bad['counts']['self_rows'] = 2
            with self.assertRaisesRegex(update.UpdateError, 'DIGEST'): validate(bad)
            bad['manifest_sha256'] = update.digest({k:v for k,v in bad.items() if k != 'manifest_sha256'})
            with self.assertRaisesRegex(update.UpdateError, 'PARTITION'): validate(bad)
            bad = copy.deepcopy(value); bad['counts']['detail_rows'] = 0
            bad['manifest_sha256'] = update.digest({k:v for k,v in bad.items() if k != 'manifest_sha256'})
            with self.assertRaisesRegex(update.UpdateError, 'DETAILS'): validate(bad)

    def test_backup_must_be_fresh_exact_307_restore(self):
        baseline = {'305_fixture.sql':'a'*64, **update.additive.REVIEWED}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); dump=root/'backup.dump'; schema=root/'schema.sql'; receipt=root/'receipt.json'
            dump.write_bytes(b'synthetic backup'); schema.write_bytes(b'synthetic schema')
            now=datetime.now(timezone.utc)
            evidence={'version':1,'kind':'singlecore-pg-snapshot-restore','status':'PASS','captured_at':(now-timedelta(minutes=3)).isoformat(),
                'source':{**update.DATABASE,'owner':update.DATABASE['user'],'server_version_num':'180006'},
                'schema_migrations':[{'filename':k,'checksum':v} for k,v in baseline.items()], 'schema':{'sha256':'b'*64,'table_count':12},
                'schema_dump':{'path':str(schema),'sha256':update.sha(schema)},
                'backup':{'path':str(dump),'sha256':update.sha(dump),'size_bytes':dump.stat().st_size,'format':'custom'},
                'restore':{'status':'PASS','host':'127.0.0.1','port':29490,'database':'realyu_backup_verify_synthetic',
                    'dump_sha256':update.sha(dump),'verified_at':(now-timedelta(minutes=1)).isoformat(),'table_count':12,
                    'row_hashes_match':True,'schema_hash_match':True,'migrations_match':True}}
            plan={'candidate_migrations':[{'filename':k,'checksum':v} for k,v in {**baseline,**update.REVIEWED}.items()]}
            def validate(v):
                receipt.write_text(json.dumps(v),encoding='utf-8')
                plan['backup_evidence']={'path':str(receipt),'sha256':update.sha(receipt)}
                return update.backup_evidence(plan,{'DATABASE_USER':update.DATABASE['user']},now)
            with patch.object(update,'BASELINE_COUNT',3),patch.object(update,'BASELINE_SHA256',update.digest(baseline)):
                self.assertEqual(validate(evidence)[1],baseline)
                for section,field,value in [('restore','port',28490),('restore','row_hashes_match',False),('backup','sha256','c'*64),('source','database','wrong')]:
                    bad=copy.deepcopy(evidence);bad[section][field]=value
                    with self.subTest(field=field),self.assertRaises(update.UpdateError):validate(bad)
                bad=copy.deepcopy(evidence);bad['schema_migrations']=bad['schema_migrations'][:1]
                with self.assertRaises(update.UpdateError):validate(bad)
                plan['candidate_migrations'].append({'filename':'309_other.sql','checksum':'f'*64})
                with self.assertRaises(update.UpdateError):validate(evidence)

    def test_preflight_owns_exact_authority_adjacent_paths_and_pins_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);binroot=root/'bin';old_dir=binroot/'current-ux-version'
            old_dir.mkdir(parents=True)
            old,new=old_dir/'sub2api.exe',root/'candidate.exe'
            old.write_bytes(b'synthetic old UX');new.write_bytes(b'synthetic candidate')
            env_file,manifest_file=root/'env.json',root/'manifest.json'
            authority,backup,archive,migration=(root/n for n in ('authority.json','backup.json','archive.json',update.MIGRATION))
            authority.write_text('{"phase":"ACTIVE","opened":true}')
            backup.write_text('{}');archive.write_text('{}');migration.write_text('synthetic migration')
            env={'SERVER_HOST':'127.0.0.1','SERVER_PORT':'18300','DATABASE_HOST':update.DATABASE['host'],
                'DATABASE_PORT':str(update.DATABASE['port']),'DATABASE_DBNAME':update.DATABASE['database'],
                'DATABASE_USER':update.DATABASE['user'],'REDIS_DB':'1','REALYU_FUNDING_ENABLED':'true','REALYU_CUSTOMER_USD_TO_CNY':'7'}
            env_file.write_text(json.dumps(env))
            manifest_file.write_text(json.dumps({'singlecore_api':{'exe':str(old),'sha':update.sha(old),'env_file':str(env_file)}}))
            plan={'format':1,'operation_id':'synthetic-history-update','database_migrations':True,'drain_seconds':30,
                'authority_receipt':str(authority),'manifest_path':str(manifest_file),'old_binary_sha256':update.sha(old),
                'candidate_binary':str(new),'new_binary_sha256':update.sha(new),'old_version':update.OLD_VERSION,
                'new_version':'synthetic-new-version','env_additions':{'REALYU_CUSTOMER_USD_TO_CNY':'7'},
                'installed_binary':str(binroot/'history-version'/'sub2api.exe'),'new_env_file':str(root/'next-env.json'),
                'maintenance_marker':str(root/'maintenance.json'),'runtime_directory':str(root/'operation'),
                'backup_evidence':{'path':str(backup),'sha256':update.sha(backup)},
                'archive_manifest':{'path':str(archive),'sha256':update.sha(archive)}}
            pins=[Path(update.__file__).resolve(),Path(update.native.__file__).resolve(),Path(update.additive.__file__).resolve(),
                Path(update.release_control.__file__).resolve(),old,new,env_file,manifest_file,authority,backup,archive,migration]
            plan['verified_files']={str(p):update.sha(p) for p in pins}
            with patch.object(update,'BIN_ROOT',binroot),patch.object(update,'OLD_BINARY_SHA256',update.sha(old)), \
                 patch.object(update,'reviewed_files',return_value={update.MIGRATION:str(migration)}), \
                 patch.object(update,'backup_evidence',return_value=({'source':{'server_version_num':'180006'}},{})), \
                 patch.object(update,'archive_evidence',return_value=manifest_fixture()[0]),patch.object(update,'Schema') as schema:
                schema.return_value.check.return_value={'complete':False}
                before={p:p.read_bytes() for p in pins if p.is_relative_to(root)}
                update.preflight(plan)
                self.assertEqual(before,{p:p.read_bytes() for p in before})
                self.assertFalse(Path(plan['installed_binary']).parent.exists())
                self.assertFalse(Path(plan['new_env_file']).exists())
                original=plan['installed_binary']
                for target in (old,old_dir/'nested-version'/'sub2api.exe',root/'outside-version'/'sub2api.exe'):
                    plan['installed_binary']=str(target)
                    with self.subTest(target=target),self.assertRaisesRegex(update.UpdateError,'DESTINATION'):update.preflight(plan)
                plan['installed_binary']=original
                for name,value in (('DATABASE_PORT','29490'),('DATABASE_DBNAME','other'),('REDIS_DB','0'),('REALYU_FUNDING_ENABLED','false')):
                    previous=env[name];env[name]=value;env_file.write_text(json.dumps(env))
                    plan['verified_files'][str(env_file)]=update.sha(env_file)
                    with self.subTest(setting=name),self.assertRaisesRegex(update.UpdateError,'AUTHORITY_CONFIGURATION_CHANGED'):update.preflight(plan)
                    env[name]=previous
                env_file.write_text(json.dumps(env));plan['verified_files'][str(env_file)]=update.sha(env_file)
                plan['env_additions']['REALYU_CUSTOMER_USD_TO_CNY']='8'
                with self.assertRaisesRegex(update.UpdateError,'CANNOT_CHANGE_SETTINGS'):update.preflight(plan)
                plan['env_additions']['REALYU_CUSTOMER_USD_TO_CNY']='7'
                plan['old_version']='older-native'
                with self.assertRaisesRegex(update.UpdateError,'RECOVERY_BASELINE'):update.preflight(plan)
                plan['old_version']=update.OLD_VERSION
                for path in (str(archive),str(backup),str(Path(update.additive.__file__).resolve()),str(migration)):
                    value=plan['verified_files'].pop(path)
                    with self.subTest(pin=path),self.assertRaisesRegex(update.UpdateError,'REQUIRED_PIN_MISSING'):update.preflight(plan)
                    plan['verified_files'][path]=value
                Path(plan['maintenance_marker']).write_text('owned by another release')
                with self.assertRaisesRegex(update.UpdateError,'MAINTENANCE_ALREADY_OWNED'):update.preflight(plan)

    def run_host(self, *, busy=False, fail_schema=False, fail_new=False, drift_on_recovery=False):
        calls,receipts=[],[]
        host=object.__new__(update.Host)
        host.plan={'operation_id':'synthetic-history-update','drain_seconds':1,'old_version':'old','new_version':'new','old_binary_sha256':'o','new_binary_sha256':'n'}
        host.stopped=False;host.schema_state=None
        host.schema=type('SchemaFake',(),{})()
        def schema_check(complete=False):
            calls.append('check-full' if complete else 'check-prefix')
            if drift_on_recovery and calls.count('stop')>1:raise update.UpdateError('IMMUTABLE_HISTORY_FACTS_CHANGED')
        def schema_apply():
            calls.append('apply-schema')
            if fail_schema:raise update.UpdateError('SYNTHETIC_SCHEMA_FAILURE')
        def ready(version,value):
            calls.append('ready-'+version)
            if version=='new' and fail_new:raise update.UpdateError('NEW_READINESS_FAILED')
        host.schema.check=schema_check;host.schema.apply=schema_apply
        host.observe_schema=lambda state:None
        with patch.object(update.native.Host,'owned',lambda s:None),patch.object(update.native.Host,'receipt',lambda s,j:receipts.append(copy.deepcopy(j))), \
             patch.object(update.native.Host,'close_gate',lambda s:calls.append('gate-close')),patch.object(update.native.Host,'open_gate',lambda s,p:calls.append('gate-open')), \
             patch.object(update.native.Host,'drain',lambda s:{'maintenance':True,'active_requests':int(busy),'reserved_funding':0,'active_batch_jobs':0}), \
             patch.object(update.native.Host,'stop',lambda s:calls.append('stop')),patch.object(update.native.Host,'start',lambda s:calls.append('start')), \
             patch.object(update.native.Host,'install_manifest',lambda s:calls.append('install')),patch.object(update.native.Host,'restore_native_manifest',lambda s:calls.append('restore-same-db')), \
             patch.object(update.native.Host,'ready',lambda s,v,d:ready(v,d)):
            clock=Clock()
            try:result=update.native.run_update(host.plan,host,clock.now,clock.sleep)
            except update.UpdateError as error:result=str(error)
        return result,calls,receipts

    def test_host_applies_only_after_drain_and_stop(self):
        result,calls,receipts=self.run_host()
        self.assertEqual(result['phase'],'ACTIVE')
        self.assertLess(calls.index('stop'),calls.index('apply-schema'))
        self.assertLess(calls.index('apply-schema'),calls.index('install'))
        self.assertTrue(receipts[-1]['database_migrations'])
        self.assertFalse(receipts[-1]['history_imported_by_update'])
        self.assertFalse(receipts[-1]['payment_enabled_by_update'])

    def test_busy_stream_does_not_stop_or_migrate(self):
        result,calls,_=self.run_host(busy=True)
        self.assertEqual(result,'ABORTED_BEFORE_STOP');self.assertNotIn('stop',calls);self.assertNotIn('apply-schema',calls)

    def test_migration_failure_recovers_same_db_and_retains_first_error(self):
        result,calls,receipts=self.run_host(fail_schema=True)
        self.assertEqual(result,'RECOVERED_SAME_NATIVE_AUTHORITY');self.assertIn('restore-same-db',calls)
        self.assertEqual(receipts[-1]['first_failure']['code'],'SYNTHETIC_SCHEMA_FAILURE')
        self.assertFalse(receipts[-1]['database_restored'])

    def test_new_readiness_failure_keeps_308_and_same_db_recovery(self):
        result,calls,receipts=self.run_host(fail_new=True)
        self.assertEqual(result,'RECOVERED_SAME_NATIVE_AUTHORITY');self.assertIn('restore-same-db',calls)
        self.assertEqual(calls.count('apply-schema'),1);self.assertTrue(receipts[-1]['additive_schema_retained'])

    def test_archive_drift_does_not_reopen_gate(self):
        result,calls,receipts=self.run_host(fail_new=True,drift_on_recovery=True)
        self.assertEqual(result,'NATIVE_RECOVERY_FAILED_NO_LEGACY_ROLLBACK');self.assertNotIn('gate-open',calls)
        self.assertEqual(receipts[-1]['phase'],'FORWARD_RECOVERY_REQUIRED_GATE_RETAINED')


@unittest.skipUnless(os.getenv('REALYU_HISTORY_UPDATE_TEST_DSN') and os.getenv('REALYU_HISTORY_UPDATE_TEST_MIGRATIONS'),'explicit isolated PG required')
class HistoryUpdatePG(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg import sql
        from psycopg.conninfo import conninfo_to_dict
        self.dsn=os.environ['REALYU_HISTORY_UPDATE_TEST_DSN'];info=conninfo_to_dict(self.dsn)
        self.assertEqual((info.get('host'),info.get('port'),info.get('dbname')),('127.0.0.1','29490','realyu_auth_e2e'))
        self.schema_name='history_update_'+uuid.uuid4().hex
        self.db=psycopg.connect(self.dsn,autocommit=True,options='-c timezone=UTC')
        self.db.execute(sql.SQL('CREATE SCHEMA {}').format(sql.Identifier(self.schema_name)));self.addCleanup(self.cleanup)
        self.db.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema_name)))
        self.db.execute('''CREATE TABLE schema_migrations(filename TEXT PRIMARY KEY,checksum TEXT NOT NULL,applied_at TIMESTAMPTZ DEFAULT NOW());
            CREATE TABLE realyu_migration_state(singleton BOOLEAN PRIMARY KEY,phase TEXT,installation_id TEXT,source_sha256 TEXT);
            CREATE TABLE users(id BIGINT PRIMARY KEY,balance NUMERIC);INSERT INTO users VALUES(1,123.456);
            CREATE TABLE realyu_purchase_snapshots(id BIGINT);INSERT INTO realyu_purchase_snapshots VALUES(77);
            CREATE TABLE realyu_purchase_grants(id BIGINT);INSERT INTO realyu_purchase_grants VALUES(77);''')
        root=Path(os.environ['REALYU_HISTORY_UPDATE_TEST_MIGRATIONS'])
        self.baseline={p.name:update.additive.migration_content(p)[1] for p in root.glob('*.sql') if int(p.name[:3])<=307}
        self.assertEqual((len(self.baseline),update.digest(self.baseline)),(update.BASELINE_COUNT,update.BASELINE_SHA256))
        with self.db.cursor() as cur:cur.executemany('INSERT INTO schema_migrations(filename,checksum) VALUES(%s,%s)',self.baseline.items())
        self.archive,self.facts,self.details=manifest_fixture()
        self.db.execute("INSERT INTO realyu_migration_state VALUES(TRUE,'active',%s,%s)",(self.archive['installation_id'],self.archive['source_sha256']))
        self.events=[]
        self.env={'DATABASE_HOST':'127.0.0.1','DATABASE_PORT':'29490','DATABASE_DBNAME':'realyu_auth_e2e','DATABASE_USER':info['user']}
        self.files={update.MIGRATION:str(root/update.MIGRATION)}
        self.runner=self.new_runner()

    def new_runner(self):
        return update.Schema(self.env,self.files,self.baseline,self.archive,self.events.append,self.connection,self.schema_name)

    def cleanup(self):
        from psycopg import sql
        self.assertTrue(self.schema_name.startswith('history_update_') and len(self.schema_name)==47)
        self.db.execute(sql.SQL('DROP SCHEMA {} CASCADE').format(sql.Identifier(self.schema_name)));self.db.close()

    def connection(self,readonly=True):
        import psycopg
        from psycopg import sql
        conn=psycopg.connect(self.dsn,autocommit=True,options='-c timezone=UTC -c statement_timeout=8000 -c lock_timeout=2000 -c default_transaction_read_only='+('on' if readonly else 'off'))
        conn.execute(sql.SQL('SET search_path TO {}').format(sql.Identifier(self.schema_name)))
        return conn

    def seal(self):
        with self.db.cursor() as cur:
            cur.executemany('INSERT INTO realyu_legacy_self_usage VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)',self.facts)
            cur.executemany('INSERT INTO realyu_legacy_usage_details VALUES(%s,%s,%s,%s,%s,%s,%s)',self.details)
        a,c=self.archive,self.archive['counts']
        self.db.execute('''INSERT INTO realyu_usage_history_imports(source_sha256,installation_id,manifest_sha256,imported_self_rows,imported_deleted_actor_rows,
            imported_detail_rows,existing_team_rows,quarantined_rows,source_consume_rows,source_started_at,source_ended_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,to_timestamp(%s),to_timestamp(%s))''',
            (a['source_sha256'],a['installation_id'],a['manifest_sha256'],*[c[k] for k in update.COUNT_FIELDS],a['source_started_at'],a['source_ended_at']))

    def test_upgrade_repeat_preserves_orders_wallet_and_fresh_shape(self):
        self.assertFalse(self.runner.check()['complete'])
        self.runner.apply();self.runner.apply()
        self.assertTrue(self.runner.check(complete=True)['complete'])
        self.assertEqual(self.db.execute('SELECT count(*) FROM schema_migrations').fetchone(),(300,))
        self.assertEqual(str(self.db.execute('SELECT balance FROM users').fetchone()[0]),'123.456')
        for table in ('realyu_purchase_snapshots','realyu_purchase_grants'):
            self.assertEqual(self.db.execute('SELECT * FROM '+table).fetchall(),[(77,)])
        self.assertEqual(self.runner.check()['archive']['counts'],[0,0,0,0])

    def test_receipt_insert_failure_rolls_back_all_ddl_and_releases_lock(self):
        self.db.execute("ALTER TABLE schema_migrations ADD CONSTRAINT reject_new CHECK(filename <> '308_realyu_usage_history.sql')")
        import psycopg
        with self.assertRaises(psycopg.errors.CheckViolation):self.runner.apply()
        self.assertFalse(self.runner.check()['complete'])
        for name in update.TABLES:self.assertIsNone(self.db.execute('SELECT to_regclass(%s)',(name,)).fetchone()[0])
        with self.connection(False) as conn:self.assertTrue(conn.execute('SELECT pg_try_advisory_lock(%s)',(update.ADVISORY_LOCK_ID,)).fetchone()[0])

    def test_advisory_lock_timeout_is_bounded_and_no_schema(self):
        with self.connection(False) as blocker:
            blocker.execute('SELECT pg_advisory_lock(%s)',(update.ADVISORY_LOCK_ID,));start=time.monotonic()
            with self.assertRaisesRegex(update.UpdateError,'ADVISORY_LOCK_TIMEOUT'):self.runner.apply()
            self.assertLess(time.monotonic()-start,5)
        self.assertFalse(self.runner.check()['complete']);self.runner.apply()

    def test_unreceipted_table_is_rejected_without_overwrite(self):
        self.db.execute('CREATE TABLE realyu_legacy_self_usage(id BIGINT)')
        with self.assertRaisesRegex(update.UpdateError,'UNRECEIPTED'):self.runner.apply()
        self.assertEqual(self.db.execute('SELECT count(*) FROM schema_migrations').fetchone(),(299,))

    def test_foreign_key_index_and_check_tamper_are_detected(self):
        self.runner.apply()
        cases=(('ALTER TABLE realyu_legacy_self_usage DROP CONSTRAINT realyu_legacy_self_usage_actor_user_id_fkey','FOREIGN_KEY'),
            ('DROP INDEX realyu_legacy_self_usage_actor','INDEX'),
            ('ALTER TABLE realyu_legacy_usage_details DROP CONSTRAINT realyu_legacy_usage_details_cache_read_tokens_check','CHECK_CONSTRAINT'))
        for command,reason in cases:
            with self.subTest(reason=reason),self.db.transaction(force_rollback=True):
                self.db.execute(command)
                with self.assertRaisesRegex(update.UpdateError,reason):self.runner.inspect(self.db)

    def test_sealed_archive_rechecked_without_data_or_order_changes(self):
        self.runner.apply();self.seal();self.runner=self.new_runner()
        self.assertEqual(self.runner.check()['archive']['status'],'sealed')
        self.runner.apply();self.assertTrue(self.runner.check(complete=True)['complete'])
        self.db.execute("UPDATE realyu_legacy_self_usage SET model='tampered'")
        with self.assertRaisesRegex(update.UpdateError,'IMMUTABLE_HISTORY_FACTS_CHANGED'):self.runner.check()

    def test_partial_archive_or_wrong_receipt_rejected(self):
        self.runner.apply();self.seal();self.runner=self.new_runner()
        self.db.execute("UPDATE realyu_usage_history_imports SET manifest_sha256=%s",('f'*64,))
        with self.assertRaisesRegex(update.UpdateError,'RECEIPT'):self.runner.check()
        self.db.execute('DELETE FROM realyu_usage_history_imports')
        with self.assertRaisesRegex(update.UpdateError,'PARTIAL'):self.runner.check()

    def test_archive_cannot_change_during_release_and_readonly_is_real(self):
        self.runner.apply();self.seal()
        with self.assertRaisesRegex(update.UpdateError,'ARCHIVE_CHANGED_DURING_RELEASE'):self.runner.check()
        import psycopg
        with self.connection(True) as conn:
            with self.assertRaises(psycopg.errors.ReadOnlySqlTransaction):conn.execute('DELETE FROM users')

    def test_wrong_source_checksum_or_db_identity_fails_closed(self):
        self.db.execute("UPDATE realyu_migration_state SET source_sha256=%s",('f'*64,))
        with self.assertRaisesRegex(update.UpdateError,'AUTHORITY'):self.runner.check()
        self.db.execute("UPDATE realyu_migration_state SET source_sha256=%s",(self.archive['source_sha256'],))
        self.db.execute("UPDATE schema_migrations SET checksum=%s WHERE filename='307_realyu_team_member_nicknames.sql'",('f'*64,))
        with self.assertRaisesRegex(update.UpdateError,'HISTORICAL_SCHEMA'):self.runner.apply()


if __name__=='__main__':unittest.main()

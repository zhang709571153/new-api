"""No services, production sockets or credentials: failure-lifecycle contracts."""
from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import sys
import time
import sqlite3
import unittest
from unittest.mock import patch

import singlecore_host_cutover as h


class PlanValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        root=Path(self.temp.name)
        self.cfg={"mode":"staging","host":"127.0.0.1","port":28490,"database":h.customers.STAGING_DATABASE,
                  "user":h.customers.STAGING_ROLE,"password":"synthetic-password-not-an-actual-credential","installation_id":h.customers.STAGING_INSTALLATION,"group_mapping":{"default":2}}
        def file(name,value):
            path=root/name;path.write_text(json.dumps(value));return str(path)
        target=file("target.json",self.cfg)
        env={"DATABASE_HOST":"127.0.0.1","DATABASE_PORT":"28490","DATABASE_DBNAME":self.cfg["database"],"DATABASE_USER":self.cfg["user"],"DATABASE_PASSWORD":self.cfg["password"],"TOKEN_REFRESH_ENABLED":"false","REALYU_FUNDING_ENABLED":"true","REDIS_HOST":"127.0.0.1","REDIS_PORT":"28391","REDIS_DB":"1"}
        env_path=file("env.json",env);exe=file("synthetic.exe",{});history=file("history.py",{});price=file("price.json",{})
        paths=[target,env_path,exe,history,price,h.__file__,h.customers.__file__,h.supply.__file__,h.pricing.__file__,h.snapshot_sqlite.__file__]
        self.plan={"version":1,"operation_id":"synthetic-reviewed-plan","installation_id":self.cfg["installation_id"],"target_config":target,
            "source_sqlite":str(root/"source.sqlite"),"runtime_directory":str(root),"maintenance_marker":str(root/"marker"),"manifest_path":str(root/"manifest.json"),
            "history_importer_path":history,"pricing_mapping":price,"gateway_base_url":"http://127.0.0.1:18300","bridge_status_url":"http://127.0.0.1:18302/",
            "native_manifest_entry":{"exe":exe,"env_file":env_path,"working_dir":str(root),"sha":h.file_sha(exe)},
            "proxy_overrides":{"1":{"protocol":"http","host":"127.0.0.1","port":17897,"username":"","password":""}},
            "verified_files":{str(Path(path).resolve()):h.file_sha(path) for path in paths}}

    def test_exact_stage_plan_validates_without_connections(self):
        with patch.object(h,"target_connection",side_effect=AssertionError("validation must not connect")):
            self.assertEqual(self.cfg,h.validate_plan(self.plan))

    def test_source_shadow_database_or_old_proxy_route_cannot_slip_into_plan(self):
        self.plan["proxy_overrides"]["1"]["port"]=7890
        with self.assertRaisesRegex(h.CutoverError,"SG_PROXY_OVERRIDE_MISMATCH"):h.validate_plan(self.plan)

    def test_changed_script_or_private_input_is_rejected(self):
        self.plan["verified_files"][str(Path(h.customers.__file__).resolve())]="0"*64
        with self.assertRaisesRegex(h.CutoverError,"REVIEWED_INPUT_CHANGED"):h.validate_plan(self.plan)

    def test_unconfirmed_worker_prevents_financial_check_and_rollback(self):
        host=object.__new__(h.WindowsHost);host.worker_unconfirmed=True
        host.financial_state=lambda:self.fail("must not proceed with another unconfirmed worker")
        with self.assertRaisesRegex(h.CutoverError,"EXIT_UNCONFIRMED"):host.verify_target_unchanged()


class FakeOps:
    def __init__(self):
        self.calls=[];self.busy=0;self.fail="";self.changed=False
    def step(self,name):
        self.calls.append(name)
        if self.fail==name:raise RuntimeError("synthetic-private-secret-never-public")
    def drain_status(self):
        self.step("drain")
        value=self.busy;self.busy=max(0,self.busy-1)
        return dict(maintenance=True,bridge_active=value,gateway_active=value,async_pending=0,unsettled_reservations=0)
    def stop_legacy_writers(self):self.step("stop_old")
    def legacy_writers_stopped(self):self.step("old_stopped");return True
    def import_s1(self):self.step("s1");return {"exact":True}
    def final_supply(self):self.step("supply");return {"private_values_returned":False}
    def copy_affinity(self):self.step("affinity");return {"copied":2}
    def seal_target(self):self.step("seal")
    def switch_to_native(self):self.step("switch")
    def activate_target(self):self.step("active")
    def start_native(self):self.step("start_native")
    def native_ready(self):self.step("ready");return True
    def verify_target_unchanged(self):
        self.step("verify")
        if self.changed:raise h.CutoverError("NEW_BUSINESS_WRITES")
    def stop_native(self):self.step("stop_native")
    def restore_target_staging(self):self.step("staging")
    def restore_legacy(self):self.step("restore_old")
    def legacy_ready(self):self.step("legacy_ready");return True


class LegacyExceptionTests(unittest.TestCase):
    def row(self,**extra):
        return {"id":7,"request_id":"synthetic-old-empty-request","status":"consumed","funding_mode":"subscription_first","pre_consumed":0,"wallet_pre_consumed":0,"created_at":1,"updated_at":1,"user_id":3,**extra}
    def policy(self,row):
        return {"record_id":row["id"],"row_sha256":h.customers.sha(h.customers.canonical(row).encode()),"treatment":"archive_unchanged_zero_amount_v1"}
    def test_one_exact_old_zero_row_is_preserved_and_new_pending_still_blocks(self):
        row=self.row();before=deepcopy(row)
        pending,reviewed=h.reviewed_reservations([row],self.policy(row),now=1_000_000)
        self.assertEqual([],pending);self.assertEqual(before,reviewed);self.assertEqual(before,row)
        pending,_=h.reviewed_reservations([row,self.row(id=8,pre_consumed=5)],self.policy(row),now=1_000_000)
        self.assertEqual([8],[x["id"] for x in pending])
        self.assertEqual([row],h.reviewed_reservations([row],None)[0])
    def test_fingerprint_change_missing_row_nonzero_recent_and_state_change_rejected(self):
        row=self.row();policy=self.policy(row)
        with self.assertRaisesRegex(h.CutoverError,"FINGERPRINT_CHANGED"):
            h.reviewed_reservations([self.row(user_id=4)],policy,now=1_000_000)
        with self.assertRaisesRegex(h.CutoverError,"MISSING"):
            h.reviewed_reservations([],policy,now=1_000_000)
        for extra in ({"pre_consumed":1},{"wallet_pre_consumed":1},{"created_at":999999,"updated_at":999999},{"updated_at":2},{"status":"settled"}):
            changed=self.row(**extra)
            with self.subTest(extra=extra),self.assertRaisesRegex(h.CutoverError,"NOT_OLD_ZERO_UNCHANGED"):
                h.reviewed_reservations([changed],self.policy(changed),now=1_000_000)
    def test_new_log_evidence_refuses_same_zero_row_instead_of_asserting_settlement(self):
        row=self.row()
        with sqlite3.connect(":memory:") as db:
            db.execute("CREATE TABLE subscription_pre_consume_records(id INTEGER,request_id TEXT,status TEXT,funding_mode TEXT,pre_consumed INTEGER,wallet_pre_consumed INTEGER,created_at INTEGER,updated_at INTEGER,user_id INTEGER)")
            db.execute("CREATE TABLE logs(request_id TEXT)")
            db.execute("INSERT INTO subscription_pre_consume_records VALUES(?,?,?,?,?,?,?,?,?)",tuple(row.values()))
            self.assertEqual([],h.inspect_reservations(db,self.policy(row))[0])
            db.execute("INSERT INTO logs VALUES(?)",(row["request_id"],))
            with self.assertRaisesRegex(h.CutoverError,"LOG_EVIDENCE_CHANGED"):h.inspect_reservations(db,self.policy(row))


class CutoverLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.run=Path(self.temp.name);self.marker=self.run/"maintenance.json"
        self.plan=dict(operation_id="synthetic-cutover-001",runtime_directory=str(self.run),maintenance_marker=str(self.marker),maintenance_budget_seconds=10)
        self.ops=FakeOps();self.now=0
    def clock(self):return self.now
    def sleep(self,n):self.now+=n
    def run_cutover(self):return h.run_cutover(self.plan,self.ops,self.clock,self.sleep)
    def receipt(self):return h.read(self.run/"authority-receipt.json")

    def test_busy_stream_drains_then_s1_precedes_authority_and_open(self):
        self.ops.busy=2
        result=self.run_cutover()
        self.assertEqual("ACTIVE",result["phase"]);self.assertTrue(result["opened"])
        self.assertFalse(self.marker.exists());self.assertEqual(.5,self.now)
        calls=self.ops.calls
        self.assertLess(calls.index("stop_old"),calls.index("s1"));self.assertLess(calls.index("s1"),calls.index("active"))
        self.assertLess(calls.index("supply"),calls.index("start_native"))
        self.assertNotIn("restore_old",calls);self.assertFalse(result["rollback_database_restored"])

    def test_busy_deadline_reopens_old_authority_without_stopping_services(self):
        self.ops.busy=999
        with self.assertRaisesRegex(h.CutoverError,"CUTOVER_ABORTED"):self.run_cutover()
        self.assertEqual(7.5,self.now);self.assertNotIn("stop_old",self.ops.calls)
        self.assertFalse(self.marker.exists());self.assertEqual("ABORTED_OLD_AUTHORITY_RESTORED",self.receipt()["phase"])

    def test_s1_failure_or_partial_stop_recovers_old_services_and_never_restores_database(self):
        for failure in ("stop_old","s1","supply","affinity","switch","active","start_native"):
            with self.subTest(failure=failure),tempfile.TemporaryDirectory() as directory:
                self.plan["runtime_directory"]=directory;self.plan["maintenance_marker"]=str(Path(directory)/"gate")
                self.ops=FakeOps();self.ops.fail=failure
                with self.assertRaisesRegex(h.CutoverError,"CUTOVER_ABORTED"):self.run_cutover()
                self.assertIn("restore_old",self.ops.calls)
                self.assertFalse(Path(self.plan["maintenance_marker"]).exists())
                self.assertNotIn("database_restore",self.ops.calls)

    def test_any_new_target_write_blocks_old_sqlite_rollback_and_retains_gate(self):
        self.ops.changed=True
        with self.assertRaisesRegex(h.CutoverError,"RECOVERY_BLOCKED"):self.run_cutover()
        self.assertTrue(self.marker.exists());self.assertNotIn("restore_old",self.ops.calls)
        self.assertEqual("BLOCKED_GATE_RETAINED",self.receipt()["phase"])

    def test_opening_unknown_result_is_forward_only_even_if_gate_still_exists(self):
        with patch.object(h,"open_owned_gate",side_effect=OSError("synthetic-private-secret")):
            with self.assertRaisesRegex(h.CutoverError,"FORWARD_RECOVERY_REQUIRED_NO_SQLITE_ROLLBACK"):self.run_cutover()
        self.assertTrue(self.marker.exists());self.assertNotIn("restore_old",self.ops.calls)
        self.assertEqual("FORWARD_RECOVERY_REQUIRED",self.receipt()["phase"])

    def test_foreign_maintenance_gate_never_replaced_or_opened(self):
        h.create_owned_gate(self.marker,"someone-else")
        before=self.marker.read_bytes()
        with self.assertRaisesRegex(h.CutoverError,"MAINTENANCE_ALREADY_OWNED"):self.run_cutover()
        self.assertEqual(before,self.marker.read_bytes());self.assertEqual([],self.ops.calls)

    def test_existing_receipt_never_automatically_replayed(self):
        h.atomic_save(self.run/"authority-receipt.json",dict(phase="OPENING"))
        with self.assertRaisesRegex(h.CutoverError,"EXISTING_AUTHORITY_RECEIPT"):self.run_cutover()
        self.assertEqual([],self.ops.calls)

    def test_blocked_s1_child_ends_before_old_authority_is_restored(self):
        with tempfile.TemporaryDirectory() as directory:
            finished=Path(directory)/"child-finished"
            def blocked_s1():
                self.ops.step("s1")
                try:h.bounded_process([sys.executable,"-c","import time;time.sleep(30)"],time.monotonic()+.25)
                finally:finished.write_text("confirmed-process-ended")
            def restored():
                self.assertTrue(finished.exists());self.ops.step("restore_old")
            self.ops.import_s1=blocked_s1;self.ops.restore_legacy=restored
            started=time.monotonic()
            with self.assertRaisesRegex(h.CutoverError,"CUTOVER_ABORTED"):self.run_cutover()
            self.assertLess(time.monotonic()-started,5)
            self.assertEqual("ABORTED_OLD_AUTHORITY_RESTORED",self.receipt()["phase"])

    def test_database_worker_exit_unconfirmed_never_restores_old_authority(self):
        def blocked_s1():
            self.ops.changed=True
            raise h.CutoverError("MIGRATION_DATABASE_SESSION_STILL_RUNNING")
        self.ops.import_s1=blocked_s1
        with self.assertRaisesRegex(h.CutoverError,"RECOVERY_BLOCKED"):self.run_cutover()
        self.assertTrue(self.marker.exists());self.assertNotIn("restore_old",self.ops.calls)


class FakeRedis:
    def __init__(self,values=None,ttls=None):self.values=values or {};self.ttls=ttls or {};self.writes=[]
    def command(self,command,*args):
        if command=="SCAN":return [b"0",list(self.values)]
        if command=="GET":return self.values.get(args[0])
        if command=="PTTL":return self.ttls.get(args[0],1000)
        if command=="SET":
            key,value,px,ttl=args;self.values[key]=value;self.ttls[key]=ttl;self.writes.append(args);return b"OK"
        raise AssertionError("unexpected Redis command")


class AffinityTests(unittest.TestCase):
    def test_target_must_start_empty_and_existing_auth_owner_keys_block(self):
        self.assertEqual(0,h.validate_target_redis(FakeRedis(),require_empty=True)["reviewed_keys"])
        affinity=b"sticky_session:2:openai:response:"+b"a"*64
        with self.assertRaisesRegex(h.CutoverError,"MUST_START_EMPTY"):
            h.validate_target_redis(FakeRedis({affinity:b"7"}),require_empty=True)
        for key in (b"auth:token:secret",b"billing:1",b"sticky_session:2:openai:http-response-owner:user:"+b"a"*64):
            with self.subTest(key=key),self.assertRaisesRegex(h.CutoverError,"UNREVIEWED_KEY"):
                h.validate_target_redis(FakeRedis({key:b"7"}),{2},{7})
        self.assertEqual(1,h.validate_target_redis(FakeRedis({affinity:b"7"}),{2},{7})["reviewed_keys"])
        with self.assertRaisesRegex(h.CutoverError,"UNREVIEWED_AFFINITY"):
            h.validate_target_redis(FakeRedis({affinity:b"7"},{affinity:-1}),{2},{7})
    def test_allowlist_integer_identity_and_remaining_ttl_only(self):
        key=b"sticky_session:2:openai:response:"+b"a"*64
        foreign=b"sticky_session:3:openai:response:"+b"b"*64
        owner=b"sticky_session:2:openai:http-response-owner:user:"+b"a"*64
        invalid=b"sticky_session:2:openai:response:"+b"c"*64
        source=FakeRedis({key:b"7",foreign:b"7",owner:b"100",invalid:b"private-secret"});target=FakeRedis()
        clock=iter([0,.1,.1,.1,.1,.1])
        result=h.copy_affinity(source,target,{2},{7},lambda:next(clock))
        self.assertEqual({key:b"7"},target.values);self.assertEqual(899,target.ttls[key])
        self.assertEqual(1,result["copied"]);self.assertFalse(result["owner_auth_billing_copied"])
    def test_expired_keys_skip_and_existing_shorter_ttl_never_extended(self):
        a=b"sticky_session:2:openai:response:"+b"a"*64;b=b"sticky_session:2:openai:response:"+b"b"*64
        source=FakeRedis({a:b"7",b:b"7"},{a:0,b:1000});target=FakeRedis({b:b"7"},{b:20})
        result=h.copy_affinity(source,target,{2},{7},lambda:0)
        self.assertEqual(1,result["expired_skipped"]);self.assertEqual(19,target.ttls[b]);self.assertNotIn(a,target.values)
    def test_conflicting_target_is_never_overwritten(self):
        key=b"sticky_session:2:openai:response:"+b"a"*64
        target=FakeRedis({key:b"8"})
        with self.assertRaisesRegex(h.CutoverError,"TARGET_AFFINITY_CONFLICT"):
            h.copy_affinity(FakeRedis({key:b"7"}),target,{2},{7},lambda:0)
        self.assertEqual([],target.writes)


@unittest.skipUnless(os.environ.get("REALYU_SUPPLY_TEST_CONFIG"),"explicit isolated PG required")
class FinalSupplyPGTests(unittest.TestCase):
    # Reuse only the dedicated random-schema fixture, not another live DB.
    from test_singlecore_supply import SupplyPGTests as Fixture
    setUp=Fixture.setUp
    tearDown=Fixture.tearDown

    def test_scoped_role_policy_verifies_owner_member_and_lower_role_scope_without_global_admin(self):
        self.target.execute("ALTER TABLE users ADD status TEXT DEFAULT 'active'")
        self.target.execute("CREATE TABLE realyu_legacy_identities(source_user_id BIGINT,user_id BIGINT);CREATE TABLE realyu_legacy_user_metadata(user_id BIGINT,source_role INT);CREATE TABLE realyu_teams(id BIGINT,owner_user_id BIGINT);CREATE TABLE realyu_team_members(team_id BIGINT,user_id BIGINT,status TEXT)")
        self.target.execute("INSERT INTO users(id,role,balance) VALUES(101,'user',0),(102,'user',0),(103,'admin',0),(104,'user',0);INSERT INTO realyu_legacy_identities VALUES(1,101),(2,102),(3,103),(4,104);INSERT INTO realyu_legacy_user_metadata VALUES(101,1),(102,10),(103,100),(104,10);INSERT INTO realyu_teams VALUES(21,101),(22,104),(23,103);INSERT INTO realyu_team_members VALUES(23,102,'active'),(22,104,'active')")
        tables={"users":[{"id":i,"role":role,"status":1} for i,role in ((1,1),(2,10),(3,100),(4,10))],"workspace_teams":[{"id":i,"owner_user_id":owner} for i,owner in ((21,1),(22,4),(23,3))],"workspace_members":[{"team_id":23,"user_id":2,"status":1},{"team_id":22,"user_id":4,"status":1}]}
        policy={"policy":"native_user_preserve_scoped_team_access_v1","expected_count":2}
        result=h.verify_scoped_admin_policy(self.target,tables,policy)
        self.assertEqual(6,result["team_scope_pairs_verified"]);self.assertFalse(result["global_admin_granted"])
        self.target.execute("UPDATE users SET role='admin' WHERE id=102")
        with self.assertRaisesRegex(h.CutoverError,"ROLE_MAPPING_MISMATCH"):h.verify_scoped_admin_policy(self.target,tables,policy)
        self.target.execute("UPDATE users SET role='user' WHERE id=102;DELETE FROM realyu_team_members WHERE user_id=102")
        with self.assertRaisesRegex(h.CutoverError,"MEMBERSHIP_MISMATCH"):h.verify_scoped_admin_policy(self.target,tables,policy)

    def test_s0_and_s1_keep_reviewed_sg_proxy_and_explicit_account_binding(self):
        import singlecore_supply as s
        for conn in (self.source,self.target):
            conn.execute("ALTER TABLE proxies ADD protocol TEXT DEFAULT 'http',ADD port INT DEFAULT 7890,ADD username TEXT DEFAULT ''")
        original=s.snapshot_connection(self.source)
        overrides={"10":{"protocol":"http","host":"127.0.0.1","port":17897,"username":"","password":""}}
        effective=h.overridden_supply(original,overrides)
        self.assertEqual(original,s.snapshot_connection(self.source))
        s.import_connection(self.target,effective,"synthetic-sg-001")
        self.assertTrue(h.verify_proxy_routes(self.target,effective,overrides)["account_proxy_bindings_exact"])
        self.source.execute("UPDATE proxies SET port=7891,password='synthetic-old-source-secret' WHERE id=10")
        final=s.snapshot_connection(self.source)
        receipt=h.final_supply_connection(self.target,final,original,overrides)
        self.assertEqual((10,"http","127.0.0.1",17897,"",""),self.target.execute("SELECT a.proxy_id,p.protocol,p.host,p.port,p.username,p.password FROM accounts a JOIN proxies p ON p.id=a.proxy_id WHERE a.id=30").fetchone())
        self.assertEqual({"10":17897},receipt["routes"]["proxy_ports"])
        self.assertNotIn("synthetic-old-source-secret",json.dumps(receipt))
        self.assertEqual(7891,self.source.execute("SELECT port FROM proxies WHERE id=10").fetchone()[0])

    def test_timed_out_pg_child_is_gone_and_uncommitted_write_rolled_back(self):
        cfg=json.loads(Path(os.environ["REALYU_SUPPLY_TEST_CONFIG"]).read_text(encoding="utf-8-sig"))
        cfg.update(mode="rehearsal",installation_id="synthetic-cutover-e2e",group_mapping={"default":20})
        self.target.execute("CREATE TABLE cancel_probe(id INT PRIMARY KEY)")
        application=h.worker_application_name("synthetic-cancel-"+self.schemas[1])
        with tempfile.TemporaryDirectory() as directory:
            signal_path=Path(directory)/"started"
            code="""import json,os,pathlib,psycopg,time
cfg=json.loads(pathlib.Path(os.environ['REALYU_SUPPLY_TEST_CONFIG']).read_text(encoding='utf-8-sig'))
with psycopg.connect(host=cfg['host'],port=cfg['port'],dbname=cfg['database'],user=cfg['user'],password=cfg['password'],options='-c search_path='+os.environ['SYNTHETIC_TEST_SCHEMA']) as conn:
 conn.execute('INSERT INTO cancel_probe VALUES(1)')
 pathlib.Path(os.environ['SYNTHETIC_STARTED_FILE']).write_text('started')
 conn.execute('SELECT pg_sleep(60)')
"""
            env=dict(os.environ,PGAPPNAME=application,SYNTHETIC_TEST_SCHEMA=self.schemas[1],SYNTHETIC_STARTED_FILE=str(signal_path))
            started=time.monotonic()
            with self.assertRaisesRegex(h.CutoverError,"BOUNDED_WORKER_DEADLINE"):
                h.bounded_process([sys.executable,"-c",code],time.monotonic()+1.5,env,lambda:h.clear_worker_sessions(cfg,application))
            self.assertTrue(signal_path.exists(),"fixture must reach actual uncommitted PG write")
            self.assertLess(time.monotonic()-started,8)
        self.assertEqual(0,self.target.execute("SELECT count(*) FROM cancel_probe").fetchone()[0])
        self.assertEqual(0,self.base.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name=%s",(application,)).fetchone()[0])

    def test_final_source_credentials_and_status_transfer_preserve_customer_price(self):
        import singlecore_supply as s
        s.import_connection(self.target,self.snapshot,"synthetic-final-01")
        self.target.execute("UPDATE groups SET rate_multiplier=0.125")
        self.source.execute("UPDATE accounts SET credentials=jsonb_set(credentials,'{refresh_token}','\"synthetic-final-refresh\"'),status='inactive' WHERE id=30")
        current=s.snapshot_connection(self.source)
        result=h.final_supply_connection(self.target,current,self.snapshot)
        row=self.target.execute("SELECT credentials->>'refresh_token',status,schedulable FROM accounts WHERE id=30").fetchone()
        self.assertEqual(("synthetic-final-refresh","inactive",True),row)
        self.assertFalse(result["customer_tables_changed"])
        self.assertEqual("0.125000000000",str(self.target.execute("SELECT rate_multiplier FROM groups WHERE id=20").fetchone()[0]))
        self.assertEqual(0,self.target.execute("SELECT count(*) FROM users").fetchone()[0])

    def test_policy_or_inventory_drift_refuses_before_overwriting_staged_credentials(self):
        import singlecore_supply as s
        s.import_connection(self.target,self.snapshot,"synthetic-final-02")
        self.source.execute("UPDATE groups SET rate_multiplier=9")
        with self.assertRaisesRegex(h.CutoverError,"FINAL_SUPPLY_POLICY_CHANGED"):
            h.final_supply_connection(self.target,s.snapshot_connection(self.source),self.snapshot)
        self.assertFalse(self.target.execute("SELECT credentials ? 'refresh_token' FROM accounts WHERE id=30").fetchone()[0])
        self.target.execute("INSERT INTO accounts(id,credentials,schedulable,status) VALUES(99,'{}',false,'inactive')")
        with self.assertRaisesRegex(h.CutoverError,"FINAL_SUPPLY_TARGET_INVENTORY_CHANGED"):
            h.final_supply_connection(self.target,self.snapshot,self.snapshot)

    def test_mid_sync_error_rolls_back_proxy_and_credentials(self):
        import singlecore_supply as s
        s.import_connection(self.target,self.snapshot,"synthetic-final-03")
        before=self.target.execute("SELECT password FROM proxies WHERE id=10").fetchone()
        self.source.execute("UPDATE proxies SET password='synthetic-new-secret' WHERE id=10")
        self.target.execute("ALTER TABLE accounts ADD CONSTRAINT stop_refresh CHECK(NOT(credentials ? 'refresh_token'))")
        with self.assertRaises(Exception):h.final_supply_connection(self.target,s.snapshot_connection(self.source),self.snapshot)
        self.assertEqual(before,self.target.execute("SELECT password FROM proxies WHERE id=10").fetchone())
        self.assertFalse(self.target.execute("SELECT credentials ? 'refresh_token' FROM accounts WHERE id=30").fetchone()[0])


if __name__=="__main__":unittest.main()

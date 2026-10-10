"""Separate customer-database cutover. Default validation is read-only.

No old SQLite restoration, no shadow-user import, no full Redis copy. Every
mutation needs an explicitly SHA-pinned plan. Public errors contain codes only.
This does not call the old same-ledger host_cutover activation implementation.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from contextlib import closing
import ctypes
import hashlib
import http.cookiejar
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.parse
import urllib.request

import singlecore_migrate as customers
import singlecore_pricing as pricing
import singlecore_supply as supply
import snapshot_sqlite

OLD_SERVICES = ("RealYuApi", "RealYuSub2APIPrewarm20261009", "RealYuSub2API20261009")
AFFINITY = re.compile(r"sticky_session:([1-9][0-9]*):openai:response:([0-9a-f]{64})")
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))
PROXY_FIELDS = {"protocol","host","port","username","password"}


class CutoverError(ValueError):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def file_sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream,"sha256").hexdigest()


def atomic_save(path,value):
    path=Path(path);temp=path.with_name(path.name+".new")
    with temp.open("x",encoding="utf-8") as stream:
        stream.write(supply.canonical(value)+"\n");stream.flush();os.fsync(stream.fileno())
    os.replace(temp,path)


def verify_files(plan):
    for path,expected in plan.get("verified_files",{}).items():
        if not re.fullmatch(r"[0-9a-f]{64}",expected) or file_sha(path)!=expected:
            raise CutoverError("REVIEWED_INPUT_CHANGED")


def validate_legacy_policies(plan):
    reservation=plan.get("legacy_zero_reservation_archive")
    if reservation is not None:
        if (not isinstance(reservation,dict) or set(reservation)!={"record_id","row_sha256","treatment"}
            or type(reservation["record_id"]) is not int or reservation["record_id"]<=0
            or not re.fullmatch(r"[0-9a-f]{64}",reservation["row_sha256"])
            or reservation["treatment"]!="archive_unchanged_zero_amount_v1"):
            raise CutoverError("INVALID_EXACT_ZERO_RESERVATION_POLICY")
    role=plan.get("legacy_scoped_admin_policy")
    if role is not None:
        if (not isinstance(role,dict) or set(role)!={"policy","expected_count"}
            or role["policy"]!="native_user_preserve_scoped_team_access_v1"
            or type(role["expected_count"]) is not int or not 1<=role["expected_count"]<=1000):
            raise CutoverError("INVALID_SCOPED_ADMIN_POLICY")


def reviewed_reservations(rows,policy,now=None):
    """One exact, old, zero-amount row may remain evidence, never settlement."""
    pending=[row for row in rows if row["status"] not in ("settled","refunded")]
    if policy is None:return pending,None
    candidates=[row for row in rows if row["id"]==policy["record_id"]]
    if len(candidates)!=1:raise CutoverError("REVIEWED_RESERVATION_MISSING")
    row=candidates[0];now=time.time() if now is None else now
    if customers.sha(customers.canonical(row).encode())!=policy["row_sha256"]:
        raise CutoverError("REVIEWED_RESERVATION_FINGERPRINT_CHANGED")
    if (row["status"]!="consumed" or row.get("funding_mode")!="subscription_first"
        or row.get("pre_consumed")!=0 or row.get("wallet_pre_consumed")!=0
        or row["created_at"]!=row["updated_at"] or row["updated_at"]>now-7*86400):
        raise CutoverError("REVIEWED_RESERVATION_NOT_OLD_ZERO_UNCHANGED")
    return [item for item in pending if item["id"]!=row["id"]],row


def inspect_reservations(db,policy):
    cursor=db.execute("SELECT * FROM subscription_pre_consume_records WHERE status NOT IN ('settled','refunded') OR id=?",(policy["record_id"] if policy else -1,))
    names=[x[0] for x in cursor.description]
    pending,reviewed=reviewed_reservations([dict(zip(names,row)) for row in cursor],policy)
    if reviewed and db.execute("SELECT count(*) FROM logs WHERE request_id=?",(reviewed["request_id"],)).fetchone()[0]!=0:
        raise CutoverError("REVIEWED_RESERVATION_LOG_EVIDENCE_CHANGED")
    return pending,reviewed


def verify_scoped_admin_policy(conn,tables,policy):
    users={row["id"]:row for row in tables["users"]}
    scoped=[row for row in users.values() if row["role"]==10]
    if len(scoped)!=policy["expected_count"]:raise CutoverError("SCOPED_ADMIN_INVENTORY_CHANGED")
    mapping=dict(conn.execute("SELECT source_user_id,user_id FROM realyu_legacy_identities WHERE source_user_id IS NOT NULL"))
    teams={row["id"]:row for row in tables["workspace_teams"]}
    target_teams=dict(conn.execute("SELECT id,owner_user_id FROM realyu_teams"))
    if target_teams!={key:mapping[row["owner_user_id"]] for key,row in teams.items()}:
        raise CutoverError("SCOPED_ADMIN_TEAM_OWNERSHIP_CHANGED")
    checks=0
    for user in scoped:
        target=mapping[user["id"]]
        actual=conn.execute("SELECT u.role,m.source_role,u.status FROM users u JOIN realyu_legacy_user_metadata m ON m.user_id=u.id WHERE u.id=%s",(target,)).fetchone()
        if actual!=("user",10,"active" if user["status"]==1 else "disabled"):
            raise CutoverError("SCOPED_ADMIN_ROLE_MAPPING_MISMATCH")
        expected={team["id"] for team in teams.values() if team["owner_user_id"]==user["id"] or users[team["owner_user_id"]]["role"]<10}
        actual_manage={row[0] for row in conn.execute("SELECT t.id FROM realyu_teams t JOIN users u ON u.id=t.owner_user_id LEFT JOIN realyu_legacy_user_metadata m ON m.user_id=u.id WHERE t.owner_user_id=%s OR (u.role<>'admin' AND COALESCE(m.source_role,1)<10)",(target,))}
        if actual_manage!=expected:raise CutoverError("SCOPED_ADMIN_MANAGEMENT_SCOPE_MISMATCH")
        source_members={row["team_id"]:("active" if row["status"]==1 else "disabled") for row in tables["workspace_members"] if row["user_id"]==user["id"]}
        target_members=dict(conn.execute("SELECT team_id,status FROM realyu_team_members WHERE user_id=%s AND status<>'removed'",(target,)))
        if target_members!=source_members:raise CutoverError("SCOPED_ADMIN_MEMBERSHIP_MISMATCH")
        checks+=len(teams)
    return {"mapped_users":len(scoped),"team_scope_pairs_verified":checks,"global_admin_granted":False}


def validate_proxy_overrides(overrides):
    if not isinstance(overrides,dict):raise CutoverError("EXPLICIT_PROXY_OVERRIDES_REQUIRED")
    for identity,value in overrides.items():
        if not re.fullmatch(r"[1-9][0-9]*",identity) or not isinstance(value,dict) or set(value)!=PROXY_FIELDS:
            raise CutoverError("INVALID_PROXY_OVERRIDE")
        if value["protocol"] not in ("http","https","socks5") or value["host"] not in ("127.0.0.1","::1"):
            raise CutoverError("REVIEWED_PROXY_MUST_BE_LOOPBACK")
        if type(value["port"]) is not int or not 1<=value["port"]<=65535:
            raise CutoverError("INVALID_PROXY_PORT")
        if any(not isinstance(value[k],str) for k in ("username","password")):
            raise CutoverError("INVALID_PROXY_CREDENTIAL_FORMAT")


def overridden_supply(snapshot,overrides):
    """Retain source evidence and account/proxy IDs; override only reviewed route."""
    validate_proxy_overrides(overrides)
    result=deepcopy(snapshot);data=result["tables"]["proxies"]
    names=[x[0] for x in data["columns"]]
    found=set()
    for row in data["rows"]:
        identity=str(row[names.index("id")])
        if identity not in overrides:continue
        found.add(identity)
        if not PROXY_FIELDS.issubset(names):raise CutoverError("PROXY_OVERRIDE_SCHEMA_MISSING")
        for key,value in overrides[identity].items():row[names.index(key)]=value
    if found!=set(overrides):raise CutoverError("PROXY_OVERRIDE_ID_MISSING")
    return result


def verify_proxy_routes(conn,expected,overrides):
    """Return only nonsecret IDs/ports and prove per-account explicit routing."""
    data=expected["tables"]["accounts"];names=[x[0] for x in data["columns"]]
    bindings={row[names.index("id")]:row[names.index("proxy_id")] for row in data["rows"]}
    if dict(conn.execute("SELECT id,proxy_id FROM accounts").fetchall())!=bindings:
        raise CutoverError("FINAL_ACCOUNT_PROXY_BINDING_MISMATCH")
    for identity,route in overrides.items():
        actual=conn.execute("SELECT protocol,host,port,username,password FROM proxies WHERE id=%s",(int(identity),)).fetchone()
        if actual!=tuple(route[x] for x in ("protocol","host","port","username","password")):
            raise CutoverError("REVIEWED_PROXY_ROUTE_NOT_APPLIED")
    return {"proxy_ports":{k:v["port"] for k,v in overrides.items()},"account_proxy_bindings_exact":True}


def validate_plan(plan):
    if plan.get("version")!=1 or not re.fullmatch(r"[A-Za-z0-9_-]{8,90}",plan.get("operation_id","")):
        raise CutoverError("INVALID_PLAN_ID")
    cfg=read(plan["target_config"]);customers.validate_target(cfg)
    validate_legacy_policies(plan)
    validate_proxy_overrides(plan.get("proxy_overrides"))
    if plan.get("installation_id")!=cfg["installation_id"]:
        raise CutoverError("PLAN_INSTALLATION_MISMATCH")
    budget=plan.get("maintenance_budget_seconds",120)
    if type(budget) is not int or not 10<=budget<=120:
        raise CutoverError("MAINTENANCE_BUDGET_MUST_BE_10_TO_120_SECONDS")
    for name in ("source_sqlite","runtime_directory","maintenance_marker","manifest_path","history_importer_path"):
        if not Path(plan[name]).is_absolute():raise CutoverError("ABSOLUTE_PATH_REQUIRED")
    if Path(plan["source_sqlite"]).resolve()==Path(plan.get("s1_snapshot",Path(plan["runtime_directory"])/"s1.sqlite")).resolve():
        raise CutoverError("SOURCE_CANNOT_BE_DESTINATION")
    for field in ("bridge_status_url","gateway_base_url"):
        url=urllib.parse.urlparse(plan[field])
        if url.scheme!="http" or url.hostname not in ("127.0.0.1","::1") or url.username or url.password:
            raise CutoverError("LOOPBACK_STATUS_URL_REQUIRED")
    native=plan["native_manifest_entry"]
    if set(native)!={"exe","env_file","working_dir","sha"} or not all(Path(native[k]).is_absolute() for k in ("exe","env_file","working_dir")):
        raise CutoverError("INVALID_NATIVE_MANIFEST_ENTRY")
    if file_sha(native["exe"])!=native["sha"]:raise CutoverError("NATIVE_BINARY_SHA_MISMATCH")
    env=read(native["env_file"])
    if not isinstance(env,dict) or any(not isinstance(k,str) or not isinstance(v,str) for k,v in env.items()):
        raise CutoverError("NATIVE_ENV_MUST_BE_FLAT_STRING_MAP")
    expected={"DATABASE_HOST":cfg["host"],"DATABASE_PORT":str(cfg["port"]),"DATABASE_DBNAME":cfg["database"],
              "DATABASE_USER":cfg["user"],"TOKEN_REFRESH_ENABLED":"false","REALYU_FUNDING_ENABLED":"true"}
    if any(env.get(k)!=v for k,v in expected.items()):raise CutoverError("NATIVE_ENV_DATABASE_OR_REFRESH_MISMATCH")
    if env.get("DATABASE_PASSWORD")!=cfg.get("password") or env.get("REDIS_DB")!="1":
        raise CutoverError("NATIVE_PRIVATE_ENV_MISMATCH")
    if cfg["mode"]=="staging":
        if plan["proxy_overrides"]!={"1":{"protocol":"http","host":"127.0.0.1","port":17897,"username":"","password":""}}:
            raise CutoverError("STAGING_SG_PROXY_OVERRIDE_MISMATCH")
        if env.get("REDIS_HOST")!="127.0.0.1" or env.get("REDIS_PORT")!="28391":raise CutoverError("STAGING_REDIS_ENDPOINT_MISMATCH")
        if plan["gateway_base_url"].rstrip("/")!="http://127.0.0.1:18300" or plan["bridge_status_url"]!="http://127.0.0.1:18302/":
            raise CutoverError("STAGING_LISTENER_MISMATCH")
    verify_files(plan)
    pinned=plan.get("verified_files",{})
    for path in (plan["history_importer_path"],native["exe"],native["env_file"],plan["target_config"],plan["pricing_mapping"],
                 __file__,customers.__file__,supply.__file__,pricing.__file__,snapshot_sqlite.__file__):
        expected=next((value for name,value in pinned.items() if Path(name).resolve()==Path(path).resolve()),None)
        if expected!=file_sha(path):raise CutoverError("REQUIRED_INPUT_NOT_PINNED")
    return cfg


def target_connection(cfg):
    import psycopg
    customers.validate_target(cfg)
    return psycopg.connect(host=cfg["host"],port=cfg["port"],dbname=cfg["database"],user=cfg["user"],
                          password=cfg["password"],connect_timeout=5,autocommit=True,
                          options="-c lock_timeout=5000 -c statement_timeout=60000")


def worker_application_name(operation):
    return "realyu-cutover-"+hashlib.sha256(operation.encode()).hexdigest()[:24]


def clear_worker_sessions(cfg,application_name):
    """Cancel only this operation's sessions under the dedicated target role."""
    import psycopg
    customers.validate_target(cfg)
    with psycopg.connect(host=cfg["host"],port=cfg["port"],dbname=cfg["database"],user=cfg["user"],
                          password=cfg["password"],connect_timeout=2,autocommit=True,
                          application_name="realyu-cutover-cleanup",
                          options="-c statement_timeout=2000 -c lock_timeout=1000") as conn:
        for _ in range(20):
            rows=conn.execute("SELECT pid FROM pg_stat_activity WHERE datname=current_database() AND usename=current_user AND application_name=%s AND pid<>pg_backend_pid()",(application_name,)).fetchall()
            if not rows:return
            for (pid,) in rows:conn.execute("SELECT pg_terminate_backend(%s)",(pid,))
            time.sleep(.05)
    raise CutoverError("MIGRATION_DATABASE_SESSION_STILL_RUNNING")


def bounded_process(command,deadline,env=None,on_finish=None):
    """Terminate the owned child tree, wait, then confirm PG work has ended.

    The child never runs SCM or authority transitions. A cancelled DB transaction
    must be absent before the parent can restore the old authority.
    """
    remaining=deadline-time.monotonic()
    if remaining<=0:raise CutoverError("OPERATION_BUDGET_EXCEEDED")
    flags=subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP if os.name=="nt" else 0
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.PIPE,env=env,
                             creationflags=flags,start_new_session=os.name!="nt")
    failed=False
    try:
        output,_=process.communicate(timeout=remaining)
    except BaseException:
        failed=True
        if process.poll() is None:
            if os.name=="nt":
                killed=subprocess.run(["taskkill.exe","/PID",str(process.pid),"/T","/F"],capture_output=True,timeout=3,creationflags=subprocess.CREATE_NO_WINDOW)
                if killed.returncode and process.poll() is None:
                    raise CutoverError("OWNED_WORKER_TERMINATION_UNCONFIRMED") from None
            else:os.killpg(process.pid,signal.SIGKILL)
        process.communicate(timeout=2)
    finally:
        if process.poll() is None:raise CutoverError("OWNED_WORKER_STILL_RUNNING")
        if on_finish:on_finish()
    if failed:raise CutoverError("BOUNDED_WORKER_DEADLINE_OR_INTERRUPTION")
    if process.returncode:raise CutoverError("BOUNDED_WORKER_FAILED_PRIVATE_RECEIPTS_RETAINED")
    try:result=json.loads(output)
    except Exception:raise CutoverError("BOUNDED_WORKER_INVALID_RESULT") from None
    if not isinstance(result,dict):raise CutoverError("BOUNDED_WORKER_INVALID_RESULT")
    return result


def prepare_database(plan):
    """Only a new pinned DB/role. Never alter existing shadow DB/role/schema."""
    import psycopg
    from psycopg import sql
    cfg=validate_plan(plan)
    if cfg["mode"]!="staging":raise CutoverError("PREPARE_DATABASE_REQUIRES_PINNED_STAGING")
    if not isinstance(cfg.get("password"),str) or len(cfg["password"])<32:raise CutoverError("OWNER_PASSWORD_MUST_BE_RANDOM_PRIVATE_32_PLUS")
    secret=read(plan["source_supply_config"])
    source=supply.load_config(plan["source_supply_config"])
    if source["host"]!="127.0.0.1" or int(source["port"])!=28490 or source["database"]==cfg["database"]:
        raise CutoverError("INVALID_SOURCE_CLUSTER")
    marker="realyu-singlecore:"+cfg["installation_id"]
    with psycopg.connect(host="127.0.0.1",port=28490,dbname=source["database"],user="sub2api_cluster_admin",
                          password=secret["pg_admin_password"],autocommit=True,connect_timeout=5) as conn:
        role=conn.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolinherit,rolreplication,rolbypassrls,rolcanlogin,shobj_description(oid,'pg_authid') FROM pg_roles WHERE rolname=%s",(cfg["user"],)).fetchone()
        if role:
            if role!=(False,False,False,False,False,False,True,marker):raise CutoverError("EXISTING_ROLE_NOT_OWNED_BY_INSTALLATION")
        else:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS").format(sql.Identifier(cfg["user"]),sql.Literal(cfg["password"])))
            conn.execute(sql.SQL("COMMENT ON ROLE {} IS {}").format(sql.Identifier(cfg["user"]),sql.Literal(marker)))
        if conn.execute("SELECT EXISTS(SELECT 1 FROM pg_auth_members m JOIN pg_roles r ON r.oid=m.member WHERE r.rolname=%s)",(cfg["user"],)).fetchone()[0]:
            raise CutoverError("STAGING_ROLE_HAS_UNEXPECTED_MEMBERSHIPS")
        database=conn.execute("SELECT pg_get_userbyid(datdba),shobj_description(oid,'pg_database') FROM pg_database WHERE datname=%s",(cfg["database"],)).fetchone()
        if database:
            if database!=(cfg["user"],marker):raise CutoverError("EXISTING_DATABASE_NOT_OWNED_BY_INSTALLATION")
        else:
            conn.execute(sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING 'UTF8'").format(sql.Identifier(cfg["database"]),sql.Identifier(cfg["user"])))
            conn.execute(sql.SQL("COMMENT ON DATABASE {} IS {}").format(sql.Identifier(cfg["database"]),sql.Literal(marker)))
        conn.execute(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(cfg["database"])))
    # Password is deliberately not reset on rerun; wrong secrets fail here.
    with target_connection(cfg) as conn:
        if conn.execute("SELECT current_database(),current_user").fetchone()!=(cfg["database"],cfg["user"]):raise CutoverError("TARGET_ROLE_VERIFICATION_FAILED")
    return {"status":"PRIVATE_DATABASE_PREPARED","schema_initialized":False,"production_traffic_changed":False}


def history_module(plan):
    path=plan["history_importer_path"]
    if plan["verified_files"].get(path)!=file_sha(path):raise CutoverError("HISTORY_IMPORTER_CHANGED")
    spec=importlib.util.spec_from_file_location("realyu_pinned_history",path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def import_customer_snapshot(plan,label):
    cfg=validate_plan(plan);run=Path(plan["runtime_directory"])
    destination=run/(label+".sqlite")
    if destination.exists():raise CutoverError("SNAPSHOT_ALREADY_EXISTS_REVIEW_PREVIOUS_RUN")
    result=snapshot_sqlite.snapshot(Path(plan["source_sqlite"]),destination)
    supply.private_write(run/(label+"-snapshot.json"),result)
    tables=customers.load_snapshot(destination,result["sha256"])
    customer=customers.apply_snapshot(tables,result["sha256"],cfg,plan["operation_id"]+"-"+label)
    history=history_module(plan)
    with target_connection(cfg) as conn:
        history_result=history.apply_history(conn,history.read_snapshot(destination,result["sha256"]),result["sha256"],cfg["installation_id"])
    receipt={"snapshot_sha256":result["sha256"],"customers":customer,"history":history_result}
    supply.private_write(run/(label+"-import.json"),receipt)
    return receipt


def stage(plan):
    cfg=validate_plan(plan);run=Path(plan["runtime_directory"])
    if (run/"s0-import.json").exists():raise CutoverError("S0_EXISTS_USE_FINAL_S1_NOT_REIMPORT")
    env=read(plan["native_manifest_entry"]["env_file"])
    with closing(RedisConnection(env["REDIS_HOST"],env["REDIS_PORT"],env["REDIS_PASSWORD"],env["REDIS_DB"])) as redis:
        validate_target_redis(redis,require_empty=True)
    snapshot=supply.export_snapshot(supply.load_config(plan["source_supply_config"]))
    supply.private_write(run/"s0-supply.json",snapshot)
    effective=overridden_supply(snapshot,plan["proxy_overrides"])
    with target_connection(cfg) as conn:
        with conn.transaction():
            receipt=supply.import_connection(conn,effective,plan["operation_id"]+"-supply",True)
            receipt["routes"]=verify_proxy_routes(conn,effective,plan["proxy_overrides"])
    supply.private_write(run/"s0-supply-receipt.json",receipt)
    price=read(plan["pricing_mapping"])
    if price.get("mapping_sha256")!=plan["pricing_mapping_sha256"]:raise CutoverError("PRICING_MAPPING_CHANGED")
    with target_connection(cfg) as conn:price_receipt=pricing.apply_rehearsal(conn,price,plan["target_group_id"])
    supply.private_write(run/"s0-pricing-receipt.json",price_receipt)
    receipt=import_customer_snapshot(plan,"s0")
    return {"status":"S0_STAGED","snapshot_sha256":receipt["snapshot_sha256"],"production_traffic_changed":False,
            "activation_blockers":receipt["customers"]["activation_blockers"]}


def final_supply_connection(conn,current,initial,overrides=None):
    """Restore full final credentials only after all old supply writers stop.

    Customer/group price tables stay untouched. Inventory changes require a new
    rehearsal, rather than guessing at group/proxy/account identity remapping.
    """
    from psycopg import sql
    for table in supply.TABLES:
        if table not in ("accounts","proxies","account_groups") and current["tables"][table]!=initial["tables"][table]:
            raise CutoverError("FINAL_SUPPLY_POLICY_CHANGED")
    for table in ("accounts","proxies","account_groups"):
        now=current["tables"][table];old=initial["tables"][table]
        if now["columns"]!=old["columns"]:raise CutoverError("FINAL_SUPPLY_SCHEMA_CHANGED")
        names=[x[0] for x in now["columns"]]
        keys=("account_id","group_id") if table=="account_groups" else ("id",)
        identities=lambda rows:{tuple(row[names.index(k)] for k in keys) for row in rows}
        if identities(now["rows"])!=identities(old["rows"]):raise CutoverError("FINAL_SUPPLY_INVENTORY_CHANGED")
    effective=overridden_supply(current,overrides or {})
    with conn.transaction():
        conn.execute("LOCK TABLE proxies,accounts,account_groups IN ACCESS EXCLUSIVE MODE")
        for table in ("proxies","accounts","account_groups"):
            data=effective["tables"][table];names=[x[0] for x in data["columns"]]
            keys=("account_id","group_id") if table=="account_groups" else ("id",)
            actual=set(conn.execute(sql.SQL("SELECT {} FROM {}").format(sql.SQL(",").join(map(sql.Identifier,keys)),sql.Identifier(table))).fetchall())
            expected={tuple(row[names.index(k)] for k in keys) for row in data["rows"]}
            if actual!=expected:raise CutoverError("FINAL_SUPPLY_TARGET_INVENTORY_CHANGED")
            updates=[(name,typ) for name,typ in data["columns"] if name not in keys]
            for row in data["rows"]:
                values=dict(zip(names,row))
                assignments=sql.SQL(",").join(sql.SQL("{}={}").format(sql.Identifier(name),sql.SQL("%s::jsonb") if typ=="jsonb" else sql.Placeholder()) for name,typ in updates)
                where=sql.SQL(" AND ").join(sql.SQL("{}=%s").format(sql.Identifier(key)) for key in keys)
                count=conn.execute(sql.SQL("UPDATE {} SET {} WHERE {}").format(sql.Identifier(table),assignments,where),[values[name] for name,_ in updates]+[values[k] for k in keys]).rowcount
                if count!=1:raise CutoverError("FINAL_SUPPLY_TARGET_ID_MISSING")
        routes=verify_proxy_routes(conn,effective,overrides or {})
    return {"source_sha256":supply.digest(current),"credentials":"final_source","customer_tables_changed":False,"routes":routes}


class RedisConnection:
    """Tiny bounded RESP client. Only explicit commands below are used."""
    def __init__(self,host,port,password,database,deadline=None):
        if host not in ("127.0.0.1","::1"):raise CutoverError("REDIS_LOOPBACK_REQUIRED")
        self.deadline=deadline
        self.socket=socket.create_connection((host,int(port)),timeout=self.timeout());self.stream=self.socket.makefile("rb")
        self.command("AUTH",password);self.command("SELECT",database)
    def timeout(self):
        if self.deadline is None:return 5
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise CutoverError("REDIS_OPERATION_BUDGET_EXCEEDED")
        return min(5,remaining)
    def close(self):self.stream.close();self.socket.close()
    def _reply(self):
        kind=self.stream.read(1);line=self.stream.readline(16384).rstrip(b"\r\n")
        if kind==b"+":return line
        if kind==b"-":raise CutoverError("REDIS_COMMAND_FAILED")
        if kind==b":":return int(line)
        if kind==b"$":
            size=int(line)
            if size<0:return None
            if size>1048576:raise CutoverError("REDIS_VALUE_TOO_LARGE")
            value=self.stream.read(size)
            if self.stream.read(2)!=b"\r\n":raise CutoverError("INVALID_REDIS_REPLY")
            return value
        if kind==b"*":
            size=int(line)
            if not 0<=size<=10000:raise CutoverError("REDIS_ARRAY_TOO_LARGE")
            return [self._reply() for _ in range(size)]
        raise CutoverError("INVALID_REDIS_REPLY")
    def command(self,*arguments):
        self.socket.settimeout(self.timeout())
        values=[x if isinstance(x,bytes) else str(x).encode() for x in arguments]
        self.socket.sendall(b"*"+str(len(values)).encode()+b"\r\n"+b"".join(b"$"+str(len(x)).encode()+b"\r\n"+x+b"\r\n" for x in values))
        return self._reply()


def validate_target_redis(target,group_ids=None,account_ids=None,require_empty=False,deadline=None):
    """Never FLUSH. Before native startup permit only reviewed account affinity."""
    cursor=b"0";seen=set()
    while True:
        if deadline is not None and time.monotonic()>=deadline:raise CutoverError("REDIS_VALIDATION_BUDGET_EXCEEDED")
        cursor,keys=target.command("SCAN",cursor,"COUNT",200)
        for raw in keys:
            if raw in seen:continue
            seen.add(raw)
            if len(seen)>50000:raise CutoverError("TARGET_REDIS_SCAN_LIMIT")
            if require_empty:raise CutoverError("TARGET_REDIS_MUST_START_EMPTY")
            match=AFFINITY.fullmatch(raw.decode("ascii",errors="replace"))
            if not match or int(match[1]) not in group_ids:raise CutoverError("TARGET_REDIS_UNREVIEWED_KEY")
            value=target.command("GET",raw);ttl=target.command("PTTL",raw)
            if value is None:continue  # expired during SCAN
            if not re.fullmatch(rb"[1-9][0-9]*",value) or int(value) not in account_ids or ttl<=0:
                raise CutoverError("TARGET_REDIS_UNREVIEWED_AFFINITY")
        if cursor==b"0":break
    return {"reviewed_keys":len(seen),"other_keys_allowed":False}


def copy_affinity(source,target,group_ids,account_ids,clock=time.monotonic,deadline=None):
    """Read only whitelisted integer affinity; preserve elapsed-time TTL loss."""
    cursor=b"0";seen=set();copied=expired=0
    while True:
        cursor,keys=source.command("SCAN",cursor,"MATCH","sticky_session:*:openai:response:*","COUNT",200)
        for raw in keys:
            if deadline is not None and clock()>=deadline:raise CutoverError("AFFINITY_COPY_BUDGET_EXCEEDED")
            if raw in seen:continue
            seen.add(raw)
            if len(seen)>50000:raise CutoverError("AFFINITY_SCAN_LIMIT")
            match=AFFINITY.fullmatch(raw.decode("ascii"))
            if not match or int(match[1]) not in group_ids:continue
            started=clock();ttl=source.command("PTTL",raw);value=source.command("GET",raw)
            if not value or not re.fullmatch(rb"[1-9][0-9]*",value):continue
            if int(value) not in account_ids:continue
            remaining=ttl-int((clock()-started)*1000)-1
            if ttl<=0 or remaining<=0:expired+=1;continue
            old=target.command("GET",raw)
            if old is not None and old!=value:raise CutoverError("TARGET_AFFINITY_CONFLICT")
            if old is not None:
                before=clock()
                old_ttl=target.command("PTTL",raw)
                if old_ttl>0:remaining=min(remaining,old_ttl-int((clock()-before)*1000)-1)
            remaining=min(remaining,ttl-int((clock()-started)*1000)-1)
            if remaining<=0:expired+=1;continue
            target.command("SET",raw,value,"PX",remaining);copied+=1
        if cursor==b"0":break
    return {"copied":copied,"expired_skipped":expired,"owner_auth_billing_copied":False}


def create_owned_gate(path,operation):
    path=Path(path)
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,"w",encoding="utf-8") as stream:
        stream.write(supply.canonical({"operation_id":operation,"mode":"singlecore-cutover","owner_pid":os.getpid()}));stream.flush();os.fsync(stream.fileno())


def check_owned_gate(path,operation):
    if read(path).get("operation_id")!=operation:raise CutoverError("MAINTENANCE_GATE_OWNERSHIP_LOST")


def open_owned_gate(path,operation,destination):
    check_owned_gate(path,operation)
    if Path(destination).exists():raise CutoverError("GATE_RECEIPT_ALREADY_EXISTS")
    Path(path).rename(destination)


def run_cutover(plan,ops,clock=time.monotonic,sleep=time.sleep):
    """Testable authority transition; after OPENING, recovery is forward only."""
    run=Path(plan["runtime_directory"]);journal_path=run/"authority-receipt.json"
    if journal_path.exists():raise CutoverError("EXISTING_AUTHORITY_RECEIPT_REQUIRES_RECONCILIATION")
    operation=plan["operation_id"];marker=Path(plan["maintenance_marker"])
    if marker.exists():raise CutoverError("MAINTENANCE_ALREADY_OWNED")
    journal={"version":1,"operation_id":operation,"phase":"PREPARED","opened":False,"rollback_database_restored":False}
    atomic_save(journal_path,journal)
    total_budget=plan.get("maintenance_budget_seconds",120)
    recovery_deadline=clock()+total_budget
    deadline=recovery_deadline-min(30,total_budget/4)
    journal["attempt_budget_seconds"]=total_budget-min(30,total_budget/4)
    journal["recovery_reserved_seconds"]=min(30,total_budget/4)
    stopped=False;opening=False
    if hasattr(ops,"set_deadline"):ops.set_deadline(deadline)
    def phase(name):
        journal["phase"]=name;atomic_save(journal_path,journal)
    def budget():
        check_owned_gate(marker,operation)
        if clock()>=deadline:raise CutoverError("MAINTENANCE_BUDGET_EXCEEDED")
    try:
        create_owned_gate(marker,operation);phase("DRAINING")
        while True:
            budget();status=ops.drain_status()
            if status.get("maintenance") is not True:raise CutoverError("BRIDGE_DID_NOT_OBSERVE_OWNED_GATE")
            if all(status.get(k)==0 and type(status.get(k)) is int for k in ("bridge_active","gateway_active","async_pending","unsettled_reservations")):break
            sleep(min(.25,max(0,deadline-clock())))
        # Record intent before a partial stop so recovery always restores any
        # stopped old service. No requirement to wait for an idle time window.
        stopped=True;phase("STOPPING_OLD_WRITERS");ops.stop_legacy_writers()
        if not ops.legacy_writers_stopped():raise CutoverError("OLD_WRITER_STILL_RUNNING")
        budget();phase("FINAL_SNAPSHOT")
        journal["s1"]=ops.import_s1();phase("S1_RECONCILED")
        budget();journal["supply"]=ops.final_supply();journal["affinity"]=ops.copy_affinity()
        budget();ops.seal_target();phase("TARGET_SEALED")
        ops.switch_to_native();ops.activate_target();ops.start_native();phase("NATIVE_PRIVATE")
        while not ops.native_ready():budget();sleep(.25)
        budget();ops.verify_target_unchanged()
        # Conservative durable boundary: a crash from here may have admitted a
        # transaction. Never silently restore SQLite even if marker still exists.
        opening=True;phase("OPENING")
        open_owned_gate(marker,operation,run/"opened-maintenance.json")
        journal["opened"]=True;phase("ACTIVE")
        return journal
    except BaseException:
        if opening:
            journal["phase"]="FORWARD_RECOVERY_REQUIRED";atomic_save(journal_path,journal)
            raise CutoverError("FORWARD_RECOVERY_REQUIRED_NO_SQLITE_ROLLBACK") from None
        try:
            check_owned_gate(marker,operation)
            if hasattr(ops,"set_deadline"):ops.set_deadline(recovery_deadline)
            if stopped:
                ops.stop_native()
                ops.verify_target_unchanged()
                ops.restore_target_staging()
                ops.restore_legacy()
                if not ops.legacy_ready():raise CutoverError("LEGACY_RESTART_NOT_VERIFIED")
            open_owned_gate(marker,operation,run/"aborted-maintenance.json")
            journal["phase"]="ABORTED_OLD_AUTHORITY_RESTORED";atomic_save(journal_path,journal)
        except BaseException:
            journal["phase"]="BLOCKED_GATE_RETAINED";atomic_save(journal_path,journal)
            raise CutoverError("RECOVERY_BLOCKED_MAINTENANCE_RETAINED") from None
        raise CutoverError("CUTOVER_ABORTED_OLD_AUTHORITY_RESTORED") from None


class WindowsHost:
    """Concrete reviewed host operations; bridge, PostgreSQL and Redis stay up."""
    def __init__(self,plan):
        self.plan=plan;self.cfg=validate_plan(plan);self.run=Path(plan["runtime_directory"])
        self.seal=None;self.deadline=time.monotonic()+120;self.changed=False;self.activated=False;self.worker_unconfirmed=False
        if os.name!="nt" or not ctypes.windll.shell32.IsUserAnAdmin():raise CutoverError("NORMAL_WINDOWS_ELEVATION_REQUIRED")
        if self.cfg["mode"]!="staging":raise CutoverError("SCM_ADAPTER_ONLY_SUPPORTS_PINNED_STAGING_HOST")
        if not (self.run/"s0-import.json").exists():raise CutoverError("S0_STAGE_REQUIRED")
        if "singlecore_api" in read(plan["manifest_path"]):raise CutoverError("NATIVE_MANIFEST_ALREADY_ACTIVE")
        if (self.run/"legacy-manifest.json").exists():raise CutoverError("EXISTING_MANIFEST_BACKUP_REQUIRES_REVIEW")
        supply.private_write(self.run/"legacy-manifest.json",read(plan["manifest_path"]))
        self.worker_plan=self.run/"worker-plan.json"
        supply.private_write(self.worker_plan,plan)

    def set_deadline(self,deadline):self.deadline=deadline
    def remaining(self):
        remaining=self.deadline-time.monotonic()
        if remaining<=0:raise CutoverError("OPERATION_BUDGET_EXCEEDED")
        return min(remaining,30)
    def worker(self,operation):
        if self.worker_unconfirmed:raise CutoverError("PREVIOUS_MIGRATION_WORKER_EXIT_UNCONFIRMED")
        application=worker_application_name(self.plan["operation_id"])
        env=dict(os.environ,PGAPPNAME=application)
        command=[sys.executable,str(Path(__file__).resolve()),"internal-worker","--plan",str(self.worker_plan),
                 "--expected-plan-sha256",file_sha(self.worker_plan),"--worker-operation",operation,
                 "--worker-deadline",str(self.deadline)]
        self.worker_unconfirmed=True
        def finished():
            clear_worker_sessions(self.cfg,application);self.worker_unconfirmed=False
        return bounded_process(command,self.deadline,env,finished)
    def ps(self,code):
        result=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command","$ErrorActionPreference='Stop'; "+code],
                              capture_output=True,timeout=self.remaining(),creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode:raise CutoverError("SCM_OPERATION_FAILED")
        return result.stdout.decode("utf-8-sig").strip()
    def service(self,name,action):
        if name not in OLD_SERVICES or action not in ("Start","Stop"):raise CutoverError("SERVICE_NOT_ALLOWED")
        # WaitForStatus timeout is bounded by the overall operation deadline.
        seconds=max(1,int(self.remaining()))
        status="Running" if action=="Start" else "Stopped"
        self.ps(f"{action}-Service -Name {name}; (Get-Service {name}).WaitForStatus('{status}',[TimeSpan]::FromSeconds({seconds}))")
    def state(self,name):
        if name not in OLD_SERVICES:raise CutoverError("SERVICE_NOT_ALLOWED")
        return self.ps(f"(Get-Service {name}).Status")
    def fetch(self,path,base=None):
        with HTTP.open((base or self.plan["gateway_base_url"]).rstrip("/")+path,timeout=min(5,self.remaining())) as response:
            return json.load(response)
    def drain_status(self):return self.worker("drain")
    def _drain_status(self):
        with HTTP.open(self.plan["bridge_status_url"],timeout=min(5,self.remaining())) as response:gate=json.load(response)
        # Bound each authentication/read/logout to the operation deadline. Do
        # not reuse the legacy acceptance client's fixed 40-second timeout.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):raise CutoverError("LOCAL_ADMIN_REDIRECT_REFUSED")
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect(),urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        credentials=read(self.plan["legacy_credentials"]);token=""
        def call(path,data=None):
            headers={"Origin":"https://api.realyu.fun","Content-Type":"application/json"}
            if token:headers["Authorization"]="Bearer "+token
            request=urllib.request.Request(self.plan["gateway_base_url"].rstrip("/")+path,headers=headers,
                                           data=None if data is None else supply.canonical(data).encode())
            with opener.open(request,timeout=min(5,self.remaining())) as response:value=json.load(response)
            if not isinstance(value,dict) or value.get("success") is False:raise CutoverError("LOCAL_ADMIN_CHECK_FAILED")
            return value.get("data",value)
        token=call("/api/user/login",{"username":credentials["admin_username"],"password":credentials["admin_password"]})["access_token"]
        try:active=call("/api/status/test")["http_stats"]["active_connections"]
        finally:
            call("/api/user/auth/logout",{});token=""
        with closing(sqlite3.connect(Path(self.plan["source_sqlite"]).as_uri()+"?mode=ro",uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            pending=db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('SUCCESS','FAILURE')").fetchone()[0]
            pending_reservations,_=inspect_reservations(db,self.plan.get("legacy_zero_reservation_archive"))
            reservations=len(pending_reservations)
        return {"maintenance":gate.get("maintenance"),"bridge_active":gate.get("active_requests"),
                "gateway_active":active,"async_pending":pending,"unsettled_reservations":reservations}
    def stop_legacy_writers(self):
        for name in OLD_SERVICES:self.service(name,"Stop")
    def legacy_writers_stopped(self):
        if not all(self.state(name)=="Stopped" for name in OLD_SERVICES):return False
        # Stopped wrappers alone are not evidence that an orphan Go child left.
        listeners=self.ps("@(Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue | Where-Object {$_.LocalPort -in @(18300,28090)}).Count")
        return listeners=="0"
    def import_s1(self):return self.worker("import-s1")
    def _import_s1(self):
        current_pricing=pricing.read_legacy(self.plan["source_sqlite"],0)
        expected_pricing=read(self.plan["pricing_mapping"])
        for key in ("legacy_default_group_ratio","required_group","required_channel","rules","unmapped"):
            if current_pricing[key]!=expected_pricing[key]:raise CutoverError("SOURCE_PRICING_CHANGED_SINCE_REHEARSAL")
        result=import_customer_snapshot(self.plan,"s1")
        blockers=result["customers"]["activation_blockers"]
        acceptable={"REHEARSAL_ONLY_NO_ACTIVATION"}
        with closing(sqlite3.connect((self.run/"s1.sqlite").as_uri()+"?mode=ro",uri=True)) as db:
            db.execute("PRAGMA query_only=ON")
            pending,reviewed=inspect_reservations(db,self.plan.get("legacy_zero_reservation_archive"))
        if pending:raise CutoverError("S1_HAS_NEW_OR_UNREVIEWED_PENDING_RESERVATIONS")
        extra={}
        with target_connection(self.cfg) as conn:
            if reviewed:
                archived=conn.execute("SELECT row_sha256,source_row FROM realyu_legacy_records WHERE source_table='subscription_pre_consume_records' AND source_key=%s",(customers.source_key("subscription_pre_consume_records",reviewed),)).fetchone()
                expected_sha=customers.sha(customers.canonical(reviewed).encode())
                if archived is None or archived[0]!=expected_sha or archived[1]!=reviewed:
                    raise CutoverError("REVIEWED_RESERVATION_ARCHIVE_MISMATCH")
                acceptable.add("SOURCE_UNSETTLED_RESERVATIONS")
                extra["legacy_reservation"]={"archived_unchanged":True,"financial_adjustment":False,"marked_settled":False,"source_row_sha256":expected_sha}
            if self.plan.get("legacy_scoped_admin_policy"):
                tables=customers.load_snapshot(self.run/"s1.sqlite",result["snapshot_sha256"])
                extra["legacy_roles"]=verify_scoped_admin_policy(conn,tables,self.plan["legacy_scoped_admin_policy"])
                acceptable.add("LEGACY_SCOPED_ADMINS_REQUIRE_ROLE_MAPPING")
        if read(self.plan["native_manifest_entry"]["env_file"]).get("REALYU_LEGACY_PAYMENT_ENABLED")=="true":
            acceptable.add("OLD_PAYMENT_CALLBACK_TAKEOVER_REQUIRED")
        if any(x["code"] not in acceptable for x in blockers):raise CutoverError("S1_HAS_UNACCEPTED_ACTIVATION_BLOCKERS")
        return {"snapshot_sha256":result["snapshot_sha256"],"user_key_reconciliation":"exact","team_entitlement_period_reconciliation":"exact",**extra}
    def final_supply(self):
        if not self.legacy_writers_stopped():raise CutoverError("OLD_SUPPLY_WRITER_RUNNING")
        return self.worker("final-supply")
    def _final_supply(self):
        current=supply.export_snapshot(supply.load_config(self.plan["source_supply_config"]))
        supply.private_write(self.run/"s1-supply.json",current)
        with target_connection(self.cfg) as conn:
            return final_supply_connection(conn,current,read(self.run/"s0-supply.json"),self.plan["proxy_overrides"])
    def copy_affinity(self):return self.worker("affinity")
    def _copy_affinity(self):
        source_env=read(self.plan["source_supply_config"])["sub_env"]
        target_env=read(self.plan["native_manifest_entry"]["env_file"])
        if source_env.get("REDIS_DB","0")==target_env["REDIS_DB"]:raise CutoverError("REDIS_SOURCE_TARGET_DATABASE_EQUAL")
        with target_connection(self.cfg) as conn:
            groups={r[0] for r in conn.execute("SELECT id FROM groups")};accounts={r[0] for r in conn.execute("SELECT id FROM accounts")}
        source=RedisConnection(source_env["REDIS_HOST"],source_env["REDIS_PORT"],source_env["REDIS_PASSWORD"],source_env.get("REDIS_DB","0"),self.deadline)
        try:
            target=RedisConnection(target_env["REDIS_HOST"],target_env["REDIS_PORT"],target_env["REDIS_PASSWORD"],target_env["REDIS_DB"],self.deadline)
            try:
                validation=validate_target_redis(target,groups,accounts,deadline=self.deadline)
                return {**copy_affinity(source,target,groups,accounts,deadline=self.deadline),"target_validation":validation}
            finally:target.close()
        finally:source.close()
    def financial_state(self):return self.worker("financial-state")
    def _financial_state(self):
        from psycopg import sql
        with target_connection(self.cfg) as conn:
            for name in ("usage_logs","payment_orders","realyu_funding_requests","realyu_team_audit"):
                if conn.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {})").format(sql.Identifier(name))).fetchone()[0]:
                    raise CutoverError("NEW_AUTHORITY_HAS_BUSINESS_WRITES")
            result={}
            for table in ("users","api_keys","realyu_key_scopes","realyu_teams","realyu_team_members","realyu_funding_subscriptions","realyu_member_period_usage"):
                rows=[r[0] for r in conn.execute(sql.SQL("SELECT row_to_json(t)::text FROM {} t").format(sql.Identifier(table)))]
                result[table]=hashlib.sha256("\n".join(sorted(rows)).encode()).hexdigest()
            return result
    def seal_target(self):
        self.seal=self.financial_state();supply.private_write(self.run/"s1-financial-seal.json",self.seal)
    def verify_target_unchanged(self):
        if self.worker_unconfirmed:raise CutoverError("PREVIOUS_MIGRATION_WORKER_EXIT_UNCONFIRMED")
        actual=self.financial_state()
        if self.seal is not None and self.seal!=actual:raise CutoverError("NEW_AUTHORITY_FINANCIAL_STATE_CHANGED")
    def switch_to_native(self):
        manifest=read(self.plan["manifest_path"])
        if manifest!=read(self.run/"legacy-manifest.json"):raise CutoverError("LEGACY_MANIFEST_CHANGED")
        manifest["singlecore_api"]=self.plan["native_manifest_entry"]
        atomic_save(self.plan["manifest_path"],manifest);self.changed=True
    def activate_target(self):
        self.verify_target_unchanged()
        self.worker("activate-target");self.activated=True
    def _activate_target(self):
        with target_connection(self.cfg) as conn:
            result=conn.execute("UPDATE realyu_migration_state SET phase='active',updated_at=NOW() WHERE singleton AND installation_id=%s AND phase='staging'",(self.cfg["installation_id"],))
            if result.rowcount!=1:raise CutoverError("TARGET_AUTHORITY_ACTIVATION_FAILED")
        return {"phase":"active"}
    def start_native(self):self.service("RealYuApi","Start")
    def native_ready(self):
        try:
            health=self.fetch("/health");status=self.fetch("/api/status")
            identity=json.loads(self.ps("@(Get-NetTCPConnection -LocalPort 18300 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique | ForEach-Object { (Get-CimInstance Win32_Process -Filter ('ProcessId = '+$_)).ExecutablePath }) | ConvertTo-Json -Compress"))
            if isinstance(identity,str):identity=[identity]
            expected=Path(self.plan["native_manifest_entry"]["exe"]).resolve()
            return (len(identity)==1 and Path(identity[0]).resolve()==expected and health.get("status")=="ok"
                    and status.get("success") is True and status.get("data",{}).get("engine")=="sub2api"
                    and status["data"].get("version")==self.plan["native_version"])
        except Exception:return False
    def stop_native(self):
        if self.state("RealYuApi")!="Stopped":self.service("RealYuApi","Stop")
        if self.ps("@(Get-NetTCPConnection -LocalPort 18300 -State Listen -ErrorAction SilentlyContinue).Count")!="0":
            raise CutoverError("NATIVE_CHILD_LISTENER_STILL_RUNNING")
    def restore_target_staging(self):
        # The activation child may have committed before its process timed out.
        # Read back, rather than relying on the parent's in-memory success flag.
        self.verify_target_unchanged();self.worker("restore-staging");self.activated=False
    def _restore_staging(self):
        with target_connection(self.cfg) as conn:
            result=conn.execute("UPDATE realyu_migration_state SET phase='staging',updated_at=NOW() WHERE singleton AND installation_id=%s AND phase IN ('active','staging')",(self.cfg["installation_id"],))
            if result.rowcount!=1:raise CutoverError("TARGET_STAGING_RESTORATION_FAILED")
        return {"phase":"staging"}
    def restore_legacy(self):
        if self.changed:atomic_save(self.plan["manifest_path"],read(self.run/"legacy-manifest.json"))
        for name in reversed(OLD_SERVICES):
            if self.state(name)!="Running":self.service(name,"Start")
    def legacy_ready(self):
        try:
            status=self.fetch("/api/status")
            return status.get("success") is True and status.get("data",{}).get("version")==self.plan["legacy_version"] and all(self.state(x)=="Running" for x in OLD_SERVICES)
        except Exception:return False


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("action",choices=("validate","prepare-database","stage","activate","internal-worker"))
    p.add_argument("--plan",required=True);p.add_argument("--expected-plan-sha256")
    p.add_argument("--worker-operation",choices=("drain","import-s1","final-supply","affinity","financial-state","activate-target","restore-staging"))
    p.add_argument("--worker-deadline",type=float)
    args=p.parse_args()
    try:
        if args.action!="validate" and file_sha(args.plan)!=args.expected_plan_sha256:raise CutoverError("PLAN_SHA256_REQUIRED_OR_CHANGED")
        plan=read(args.plan);validate_plan(plan)
        if args.action=="validate":result={"status":"PLAN_VALIDATED","source_writes":False,"production_traffic_changed":False}
        elif args.action=="prepare-database":result=prepare_database(plan)
        elif args.action=="stage":result=stage(plan)
        elif args.action=="internal-worker":
            if not args.worker_operation or args.worker_deadline is None:raise CutoverError("INTERNAL_WORKER_ARGUMENTS_REQUIRED")
            check_owned_gate(plan["maintenance_marker"],plan["operation_id"])
            if os.environ.get("PGAPPNAME")!=worker_application_name(plan["operation_id"]):raise CutoverError("INTERNAL_WORKER_IDENTITY_REQUIRED")
            host=object.__new__(WindowsHost);host.plan=plan;host.cfg=validate_plan(plan);host.run=Path(plan["runtime_directory"]);host.deadline=args.worker_deadline
            host.remaining()
            method={"drain":"_drain_status","import-s1":"_import_s1","final-supply":"_final_supply","affinity":"_copy_affinity","financial-state":"_financial_state","activate-target":"_activate_target","restore-staging":"_restore_staging"}[args.worker_operation]
            result=getattr(host,method)()
        else:
            import release_control
            # Same local-writer lock as the installed release machinery.
            with release_control.release_lock(Path(plan["maintenance_marker"]).parent):
                result=run_cutover(plan,WindowsHost(plan))
        print(supply.canonical(result));return 0
    except (CutoverError,customers.MigrationError,supply.SupplyError) as error:
        print(supply.canonical({"status":"BLOCKED","code":str(error)}));return 2
    except Exception:
        print('{"status":"BLOCKED","code":"PRIVATE_OPERATION_FAILED_NO_VALUES_LOGGED"}');return 2


if __name__=="__main__":sys.exit(main())

"""Bounded Windows host cutover with admission drain and ledger-preserving rollback.

Run only from the reviewed elevated installation entry. All configuration and
receipts are private. This does not restore an old customer database on rollback.
Refresh-token ownership is transferred separately after publication acceptance.
"""
import argparse
from contextlib import closing
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET


HERE = Path(__file__).resolve().parent
FLAGS = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8") as out:
        json.dump(value, out, indent=2)
        out.flush()
        os.fsync(out.fileno())
    os.replace(temp, path)


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def fetch(url):
    with HTTP.open(url, timeout=15) as response:
        return json.load(response)


def ps(command):
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command",
                             "$ErrorActionPreference='Stop'; " + command],
                            capture_output=True, creationflags=FLAGS, timeout=120)
    if result.returncode:
        raise RuntimeError("SCM operation failed; inspect private deployment logs")
    return result.stdout.decode("utf-8", errors="replace").strip()


def service(name, action):
    if name not in ("RealYuApi", "RealYuBridge") or action not in ("Start", "Stop"):
        raise ValueError("Unexpected service operation")
    ps(action + "-Service -Name " + name + " -ErrorAction Stop; (Get-Service " + name +
       ").WaitForStatus('" + ("Running" if action == "Start" else "Stopped") + "',[TimeSpan]::FromSeconds(90))")


def wait_version(version):
    until = time.monotonic() + 75
    while time.monotonic() < until:
        try:
            if fetch("http://127.0.0.1:18300/api/status")["data"]["version"] == version:
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("Expected gateway version did not start")


def verify_gateway_process(private, executable):
    pid = int((private / "newapi.pid").read_text().strip())
    value = json.loads(ps("$p=Get-CimInstance Win32_Process -Filter 'ProcessId = " + str(pid) +
                         "'; [pscustomobject]@{path=$p.ExecutablePath;owners=@(Get-NetTCPConnection -LocalPort 18300 -State Listen | Select-Object -ExpandProperty OwningProcess)} | ConvertTo-Json -Compress"))
    if Path(value["path"]).resolve() != Path(executable).resolve() or set(value["owners"]) != {pid}:
        raise RuntimeError("Gateway executable or listener identity differs")


def new_admin(credentials):
    spec = importlib.util.spec_from_file_location("host_admin", HERE.parents[1] / "lab/verify_provider_release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BASE = "http://127.0.0.1:18300"
    admin = module.API()
    original = admin.request
    admin.request = lambda path, data=None, method=None, headers=None: original(
        path, data, method, {"Origin": "https://api.realyu.fun", **(headers or {})})
    admin.login(credentials["admin_username"], credentials["admin_password"])
    return admin


def drain(private, timeout=300):
    admin = new_admin(read(private / "credentials.json"))
    try:
        until, stable, previous = time.monotonic() + timeout, None, None
        while time.monotonic() < until:
            try:
                gate = fetch("http://127.0.0.1:18302/")
            except Exception:
                if ps("(Get-Service RealYuBridge).Status") == "Stopped":
                    gate = {"maintenance": (private / "release-maintenance.json").exists(), "active_requests": 0}
                else:
                    stable = None
                    time.sleep(1)
                    continue
            current = money_state(private / "new-api.db")
            active = admin.call("/api/status/test")["http_stats"]["active_connections"]
            with closing(sqlite3.connect((private / "new-api.db").resolve().as_uri() + "?mode=ro", uri=True)) as db:
                pending = db.execute("SELECT count(*) FROM tasks WHERE status NOT IN ('SUCCESS','FAILURE')").fetchone()[0]
            if gate["maintenance"] and gate["active_requests"] == 0 and active == 0 and pending == 0 and current == previous:
                stable = stable or time.monotonic()
                if time.monotonic() - stable >= 15:
                    return
            else:
                stable = None
            previous = current
            time.sleep(1)
        raise RuntimeError("Active traffic or asynchronous settlement did not drain")
    finally:
        admin.call("/api/user/auth/logout", {})
        admin.token = ""


def schema_state(database):
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        return db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name").fetchall()


def validate_prepared(plan):
    binding_sha = digest(plan["bindings_file"])
    state_root = Path(plan["state_directory"])
    progress = read(state_root / (binding_sha + ".json"))
    queue_path = state_root / (binding_sha + ".queue.json")
    queue = read(queue_path)
    if (progress.get("version") != 1 or progress.get("config_sha256") != binding_sha
            or progress.get("queue_sha256") != digest(queue_path) or queue.get("version") != 1
            or not isinstance(progress.get("tasks"), dict)):
        raise RuntimeError("Prewarming state does not match the current configuration and queue")
    expected = {str(t["realyu_user_id"]) + ":" + str(t.get("workspace_team_id", 0)) + ":" + str(t["channel_id"])
                for t in queue["tasks"]}
    if len(expected) < plan["minimum_prepared_subjects"] or any(progress["tasks"].get(key, {}).get("status") != "DONE" for key in expected):
        raise RuntimeError("Existing customer identities are not all prepared")
    with closing(sqlite3.connect((Path(plan["production_private"]) / "new-api.db").resolve().as_uri() + "?mode=ro", uri=True)) as db:
        users = db.execute('SELECT id,"group" FROM users WHERE status=1 AND deleted_at IS NULL').fetchall()
        if any(group != "default" for _, group in users):
            raise RuntimeError("Customer groups changed; refresh the reviewed enrollment inventory")
        subjects = {(uid, 0) for uid, _ in users}
        subjects.update(db.execute("SELECT m.user_id,m.team_id FROM workspace_members m JOIN users u ON u.id=m.user_id WHERE m.status=1 AND u.status=1 AND u.deleted_at IS NULL").fetchall())
        if any(f"{uid}:{team}:{plan['channel_id']}" not in expected for uid, team in subjects):
            raise RuntimeError("New customer memberships were added after prewarming")
    return binding_sha


def money_state(database):
    tables = ("tokens", "user_subscriptions", "subscription_plans", "subscription_orders",
              "subscription_pre_consume_records", "billing_orders", "top_ups", "redemptions",
              "workspace_team_accounts", "workspace_members", "workspace_teams",
              "workspace_personal_keys", "workspace_member_weekly_usages", "workspace_funding_migrations")
    result = {}
    with closing(sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True)) as db:
        for table in tables:
            rows = db.execute('SELECT * FROM "' + table + '" ORDER BY rowid').fetchall()
            payload = json.dumps(rows, ensure_ascii=True, default=lambda v: v.hex()).encode()
            result[table] = {"rows": len(rows), "sha256": hashlib.sha256(payload).hexdigest()}
        rows = db.execute("SELECT id,quota,used_quota,request_count FROM users ORDER BY id").fetchall()
        result["users_financial"] = {"rows": len(rows), "sha256": hashlib.sha256(json.dumps(rows).encode()).hexdigest()}
    return result


def change_xml(path, environment):
    tree = ET.parse(path)
    root = tree.getroot()
    for name, value in environment.items():
        nodes = [node for node in root.findall("env") if node.get("name") == name]
        if len(nodes) > 1:
            raise RuntimeError("Duplicate service environment entry")
        node = nodes[0] if nodes else ET.SubElement(root, "env")
        node.set("name", name)
        node.set("value", value)
    temp = Path(str(path) + ".sub2api-tmp")
    tree.write(temp, encoding="utf-8", xml_declaration=True)
    # Preserve the original protected service XML ACL across atomic replacement.
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    ps("$xmlAcl=Get-Acl -LiteralPath " + quote(path) + "; Set-Acl -LiteralPath " + quote(temp) + " -AclObject $xmlAcl")
    os.replace(temp, path)


def route(action, plan, log):
    result = subprocess.run([sys.executable, "-X", "utf8", str(HERE / "production_route.py"),
                             action, "--plan", str(plan)], stdout=log, stderr=log, creationflags=FLAGS, timeout=90)
    if result.returncode:
        raise RuntimeError("Route change failed; inspect private receipt")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise RuntimeError("This host installation requires the normal Windows administrator elevation")
    private = Path(plan["production_private"])
    run = Path(plan["operation_directory"])
    backup = run / "cutover-backup"
    marker = private / "release-maintenance.json"
    journal_file = run / "cutover-receipt.json"
    if journal_file.exists() or backup.exists() or marker.exists() or (run / "public-acceptance-verdict.json").exists():
        raise RuntimeError("An existing operation requires reconciliation")
    for path, expected in plan["verified_files"].items():
        if digest(path) != expected:
            raise RuntimeError("A reviewed deployment input changed")
    if fetch("http://127.0.0.1:18300/api/status")["data"]["version"] != plan["old_version"]:
        raise RuntimeError("Live version advanced beyond the reviewed baseline")
    verify_gateway_process(private, private / read(private / "credentials.json")["binary_name"])
    if read(plan["supply_receipt"])["phase"] != "access_only_ready":
        raise RuntimeError("Initial publication requires access-only supply and reversible refresh ownership")
    validate_prepared(plan)
    baseline_schema = schema_state(private / "new-api.db")
    if schema_state(plan["validated_candidate_database"]) != baseline_schema:
        raise RuntimeError("Candidate and live schemas differ; automatic old-program rollback is not validated")
    # Existing production controller supplies the same OS lock as earlier releases.
    spec = importlib.util.spec_from_file_location("deployment_control", HERE / "release_control.py")
    control = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(control)
    journal = {"phase": "prepared", "operation_id": plan["operation_id"], "old_version": plan["old_version"],
               "new_version": plan["new_version"], "database_rollback": False}
    save(journal_file, journal)
    with control.release_lock(private), (run / "cutover-private.log").open("ab", buffering=0) as log:
        backup.mkdir()
        for label, source in plan["backup_files"].items():
            shutil.copy2(source, backup / label)
        before = read(private / "credentials.json")
        opened = False
        changed = False
        try:
            save(marker, {"operation_id": plan["operation_id"], "mode": "sub2api-cutover", "owner_pid": os.getpid()})
            journal["phase"] = "draining"
            save(journal_file, journal)
            drain(private)
            validate_prepared(plan)
            route("activate", plan["route_plan"], log)
            changed = True
            service("RealYuApi", "Stop")
            service("RealYuBridge", "Stop")
            from snapshot_sqlite import snapshot
            journal["backup"] = snapshot(private / "new-api.db", backup / "customer-ledger.db")
            expected_money = money_state(private / "new-api.db")
            journal["phase"] = "stopped_and_backed_up"
            save(journal_file, journal)
            target = private / plan["new_binary_name"]
            if target.exists() and digest(target) != plan["new_binary_sha256"]:
                raise RuntimeError("Existing release filename has a different binary")
            shutil.copy2(plan["new_binary"], target)
            if digest(target) != plan["new_binary_sha256"]:
                raise RuntimeError("Deployed binary digest differs")
            config = read(private / "credentials.json")
            config["binary_name"] = target.name
            save(private / "credentials.json", config)
            change_xml(plan["api_xml"], {"REALYU_UPSTREAM_DRIVER": "sub2api",
                "REALYU_SUB2API_BINDINGS_FILE": plan["bindings_file"],
                "REALYU_SUB2API_QUEUE_DIR": plan["queue_directory"],
                "REALYU_SUB2API_STATE_DIR": plan["state_directory"],
                "REALYU_SUB2API_ADMIN_URL": plan["sub2api_base_url"]})
            change_xml(plan["bridge_xml"], {"REALYU_UPSTREAM_DRIVER": "sub2api"})
            shutil.copy2(plan["edge_proxy"], plan["production_bridge"])
            service("RealYuApi", "Start")
            wait_version(plan["new_version"])
            verify_gateway_process(private, target)
            service("RealYuBridge", "Start")
            gate = fetch("http://127.0.0.1:18302/")
            if not gate["maintenance"] or gate["active_requests"]:
                raise RuntimeError("New edge did not preserve maintenance admission")
            if money_state(private / "new-api.db") != expected_money:
                raise RuntimeError("Customer ledger changed during the closed deployment")
            if schema_state(private / "new-api.db") != baseline_schema:
                raise RuntimeError("Unexpected schema change; preserve maintenance for explicit recovery")
            journal["ledger_preserved"] = True
            journal["phase"] = "provisional_open"
            marker.rename(private / ("release-maintenance." + plan["operation_id"] + ".opened.json"))
            opened = True
            save(journal_file, journal)
            # Keep this single reviewed elevated operation available for rollback
            # while the caller performs actual public acceptance. No arbitrary
            # commands are accepted through this result file.
            deadline = time.monotonic() + 1200
            verdict_path = run / "public-acceptance-verdict.json"
            while time.monotonic() < deadline:
                if verdict_path.exists():
                    verdict = read(verdict_path)
                    if (verdict.get("operation_id") != plan["operation_id"] or verdict.get("version") != plan["new_version"]
                            or verdict.get("binary_sha256") != plan["new_binary_sha256"]):
                        raise RuntimeError("Acceptance result targets another release")
                    if verdict.get("status") != "PASS":
                        raise RuntimeError("Public acceptance rejected the candidate")
                    journal["phase"] = "published"
                    journal["public_acceptance"] = verdict
                    save(journal_file, journal)
                    print(json.dumps({"phase": "published", "version": plan["new_version"]}), flush=True)
                    return
                time.sleep(1)
            raise RuntimeError("Public acceptance deadline expired")
        except Exception as error:
            journal["failure_type"] = type(error).__name__
            journal["phase"] = "rollback_started"
            save(journal_file, journal)
            if opened:
                save(marker, {"operation_id": plan["operation_id"], "mode": "rollback"})
                try:
                    drain(private)
                except Exception:
                    journal["phase"] = "recovery_required_drain"
                    save(journal_file, journal)
                    raise RuntimeError("Could not verify safe rollback drain; maintenance retained for recovery") from None
            if changed or read(plan["route_receipt"])["phase"] in ("activating", "active"):
                if schema_state(private / "new-api.db") != baseline_schema:
                    journal["phase"] = "recovery_required_schema_changed"
                    save(journal_file, journal)
                    raise RuntimeError("Automatic program rollback withheld because schema changed; maintenance retained") from None
                service("RealYuApi", "Stop")
                service("RealYuBridge", "Stop")
                for label, destination in plan["backup_files"].items():
                    if label != "credentials.json":
                        shutil.copy2(backup / label, destination)
                current = read(private / "credentials.json")
                current["binary_name"] = before["binary_name"]
                save(private / "credentials.json", current)
                service("RealYuApi", "Start")
                wait_version(plan["old_version"])
                verify_gateway_process(private, private / before["binary_name"])
                service("RealYuBridge", "Start")
                route_plan = read(plan["route_plan"])
                route_plan["refresh_ownership_returned"] = True  # transfer has not run in this operation
                restore_plan = run / "route-restore-plan.json"
                save(restore_plan, route_plan)
                route("restore-routing", restore_plan, log)
            if marker.exists() and read(marker).get("operation_id") == plan["operation_id"]:
                marker.rename(run / "rollback-maintenance-marker.json")
            journal["phase"] = "rolled_back"
            save(journal_file, journal)
            raise RuntimeError("Deployment failed; original routing and program restored without replacing customer ledger") from None


if __name__ == "__main__":
    main()

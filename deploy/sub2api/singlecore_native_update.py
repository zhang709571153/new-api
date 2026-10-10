"""Pinned, drained program/display update of an ACTIVE native customer authority.

No database migration, legacy writer, tunnel or proxy is touched. The only
permitted configuration addition is the customer display/input exchange rate.
Default is preflight. Execution requires normal Windows administrator rights.
The old native binary may recover the same PostgreSQL authority if startup fails;
the retired SQLite authority is never restored. Receipts are append-only per run.
"""
from __future__ import annotations

import argparse
import copy
import ctypes
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
import urllib.request

import release_control


HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class UpdateError(ValueError):
    pass


def require(ok, code):
    if not ok:
        raise UpdateError(code)


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def save(path, value, exclusive=False):
    path = Path(path)
    data = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()
    if exclusive:
        with path.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    else:
        temp = path.with_name(path.name + ".update-tmp")
        with temp.open("xb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)


def replacement(manifest, installed_binary, digest, env_file=None):
    require("singlecore_api" in manifest, "NATIVE_MANIFEST_REQUIRED")
    value = copy.deepcopy(manifest)
    value["singlecore_api"]["exe"] = str(installed_binary)
    value["singlecore_api"]["sha"] = digest
    if env_file is not None:
        value["singlecore_api"]["env_file"] = str(env_file)
    return value


def updated_environment(env, additions):
    require(set(additions) == {"REALYU_CUSTOMER_USD_TO_CNY"}, "ONLY_CUSTOMER_DISPLAY_RATE_ALLOWED")
    raw = additions["REALYU_CUSTOMER_USD_TO_CNY"]
    require(isinstance(raw, str), "DISPLAY_RATE_MUST_BE_STRING")
    try:
        rate = Decimal(raw)
        require(rate.is_finite() and Decimal('.01') <= rate <= Decimal('1000'), "DISPLAY_RATE_INVALID")
    except InvalidOperation:
        raise UpdateError("DISPLAY_RATE_INVALID") from None
    return {**env, **additions}


def preflight(plan):
    require(plan.get("format") == 1, "PLAN_FORMAT")
    require(re.fullmatch(r"[a-z0-9-]{8,100}", plan.get("operation_id", "")), "OPERATION_ID")
    require(plan.get("database_migrations") is False, "SCHEMA_CHANGE_NOT_ALLOWED")
    require(30 <= plan.get("drain_seconds", 0) <= 60, "DRAIN_BUDGET")
    authority = read(plan["authority_receipt"])
    require(authority.get("phase") == "ACTIVE" and authority.get("opened") is True, "ACTIVE_AUTHORITY_REQUIRED")
    for path, digest in plan["verified_files"].items():
        require(sha(path) == digest, "PINNED_FILE_CHANGED")
    manifest = read(plan["manifest_path"])
    native = manifest.get("singlecore_api", {})
    require(native.get("sha") == plan["old_binary_sha256"], "OLD_BINARY_PIN_MISMATCH")
    require(sha(native["exe"]) == native["sha"], "OLD_BINARY_CHANGED")
    require(sha(plan["candidate_binary"]) == plan["new_binary_sha256"], "CANDIDATE_BINARY_CHANGED")
    env = read(native["env_file"])
    required = {"SERVER_HOST": "127.0.0.1", "SERVER_PORT": "18300",
                "DATABASE_HOST": "127.0.0.1", "DATABASE_PORT": "28490",
                "DATABASE_DBNAME": "realyu_singlecore_20261010_candidate",
                "DATABASE_USER": "realyu_singlecore_owner_20261010",
                "REDIS_DB": "1", "REALYU_FUNDING_ENABLED": "true"}
    require(all(env.get(k) == v for k, v in required.items()), "AUTHORITY_CONFIGURATION_CHANGED")
    updated_environment(env, plan["env_additions"])
    env_destination = Path(plan["new_env_file"]).resolve()
    require(env_destination.parent == Path(native["env_file"]).resolve().parent and not env_destination.exists(), "NEW_ENV_MUST_BE_ADJACENT_UNIQUE_FILE")
    destination = Path(plan["installed_binary"]).resolve()
    parent = Path(native["exe"]).resolve().parent
    require(destination.is_relative_to(parent) and destination != Path(native["exe"]).resolve(), "DESTINATION_NOT_VERSIONED_NATIVE_BINARY")
    require(not Path(plan["maintenance_marker"]).exists(), "MAINTENANCE_ALREADY_OWNED")
    require(not (Path(plan["runtime_directory"]) / "update-receipt.json").exists(), "EXISTING_OPERATION")
    return manifest, env


def run_update(plan, ops, clock=time.monotonic, sleep=time.sleep):
    """Draining is bounded; startup failure recovers the previous native binary."""
    journal = {"operation_id": plan["operation_id"], "phase": "PREPARED", "phase_times": {},
               "database_restored": False, "old_legacy_services_started": False}
    def record(strict=True):
        try:
            ops.receipt(journal)
        except BaseException:
            journal["receipt_write_failed"] = True
            # Disk/ACL trouble must not prevent admission recovery. Console
            # capture is an independent, sanitized fallback for first failure.
            print(json.dumps({"receipt_write_failed": True, "phase": journal["phase"],
                              "first_failure": journal.get("first_failure")}))
            if strict:
                raise
    def phase(name, strict=True):
        journal["phase"] = name
        journal["phase_times"][name] = time.time()
        record(strict)
    stopped = False
    gate = False
    try:
        ops.close_gate()
        gate = True
        phase("DRAINING")
        deadline = clock() + plan["drain_seconds"]
        while True:
            state = ops.drain()
            require(state.get("maintenance") is True, "BRIDGE_GATE_NOT_OBSERVED")
            if all(type(state.get(k)) is int and state[k] == 0 for k in ("active_requests", "reserved_funding", "active_batch_jobs")):
                break
            require(clock() < deadline, "DRAIN_TIMEOUT_NO_STREAM_KILLED")
            sleep(.25)
        stopped = True
        phase("STOPPING_NATIVE")
        ops.stop()
        ops.install_manifest()
        phase("STARTING_NATIVE")
        ops.start()
        ops.ready(plan["new_version"], plan["new_binary_sha256"])
        phase("VERIFIED_PRIVATE")
        ops.open_gate("opened-maintenance.json")
        gate = False
        phase("ACTIVE", strict=False)
        return journal
    except BaseException as failure:
        journal["first_failure"] = {"phase": journal["phase"], "type": type(failure).__name__,
            "code": str(failure) if isinstance(failure, UpdateError) else "PRIVATE_UPDATE_FAILURE"}
        record(strict=False)
        if not gate:
            raise
        try:
            if stopped:
                ops.stop()
                ops.restore_native_manifest()
                ops.start()
                ops.ready(plan["old_version"], plan["old_binary_sha256"])
            ops.open_gate("aborted-maintenance.json")
            phase("RECOVERED_SAME_NATIVE_AUTHORITY" if stopped else "ABORTED_BEFORE_STOP", strict=False)
        except BaseException:
            phase("FORWARD_RECOVERY_REQUIRED_GATE_RETAINED", strict=False)
            raise UpdateError("NATIVE_RECOVERY_FAILED_NO_LEGACY_ROLLBACK") from None
        raise UpdateError(journal["phase"]) from None


class Host:
    def __init__(self, plan, manifest, env):
        self.plan, self.manifest, self.env = plan, manifest, env
        self.run = Path(plan["runtime_directory"])
        self.marker = Path(plan["maintenance_marker"])
        self.run.mkdir(parents=True, exist_ok=True)
        save(self.run / "previous-native-manifest.private.json", manifest, exclusive=True)
        destination = Path(plan["installed_binary"])
        destination.parent.mkdir(parents=True, exist_ok=False)
        shutil.copyfile(plan["candidate_binary"], destination)
        require(sha(destination) == plan["new_binary_sha256"], "INSTALLED_BINARY_MISMATCH")
        save(plan["new_env_file"], updated_environment(env, plan["env_additions"]), exclusive=True)

    def receipt(self, journal):
        save(self.run / "update-receipt.json", journal)

    def owned(self):
        require(read(self.marker).get("operation_id") == self.plan["operation_id"], "GATE_OWNERSHIP_LOST")

    def close_gate(self):
        # On Windows rename is atomic and refuses an existing destination.
        # Complete and flush the document before it can close admission.
        temp = self.marker.with_name(self.marker.name + "." + self.plan["operation_id"] + ".prepared")
        save(temp, {"operation_id": self.plan["operation_id"], "mode": "native-binary-update", "owner_pid": os.getpid()}, exclusive=True)
        temp.rename(self.marker)

    def open_gate(self, filename):
        self.owned()
        target = self.run / filename
        require(not target.exists(), "GATE_RECEIPT_EXISTS")
        self.marker.rename(target)

    def fetch(self, url):
        with HTTP.open(url, timeout=5) as response:
            return json.load(response)

    def drain(self):
        import psycopg
        self.owned()
        state = self.fetch("http://127.0.0.1:18302/")
        env = self.env
        with psycopg.connect(host=env["DATABASE_HOST"], port=int(env["DATABASE_PORT"]),
                dbname=env["DATABASE_DBNAME"], user=env["DATABASE_USER"], password=env["DATABASE_PASSWORD"],
                connect_timeout=3, application_name="realyu-native-update-readonly",
                options="-c default_transaction_read_only=on -c statement_timeout=3000") as conn:
            require(conn.execute("SELECT phase FROM realyu_migration_state WHERE singleton").fetchone() == ("active",), "DATABASE_NOT_ACTIVE")
            state["reserved_funding"] = conn.execute("SELECT count(*) FROM realyu_funding_requests WHERE status='reserved'").fetchone()[0]
            state["active_batch_jobs"] = conn.execute("SELECT count(*) FROM batch_image_jobs WHERE status NOT IN ('completed','failed','cancelled','output_deleted')").fetchone()[0]
        return state

    def ps(self, code, timeout=28):
        prefix = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); $OutputEncoding=[Console]::OutputEncoding; "
        # A PowerShell 7 parent can pass its incompatible module search path to
        # Windows PowerShell 5.1; let that child initialize its own native path.
        child_env = {key: value for key, value in os.environ.items() if key.upper() != "PSMODULEPATH"}
        # powershell.exe -Command otherwise derives its exit status from the
        # last $? value. An expected Get-Process miss when proving that the old
        # worker has exited sets $?=False even inside a successful final loop.
        # Terminating errors still abort before this explicit success status.
        result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", prefix + code + "\nexit 0"],
                    capture_output=True, timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW, env=child_env)
        with (self.run / ("powershell-" + str(time.time_ns()) + ".private.log")).open("xb") as stream:
            stream.write(json.dumps({"returncode": result.returncode}).encode() + b"\n" + result.stdout + result.stderr)
        require(result.returncode == 0, "SCM_OR_IDENTITY_CHECK_FAILED")
        return result.stdout.decode("utf-8-sig").strip()

    def stop(self):
        self.owned()
        self.ps("$before=@(Get-NetTCPConnection -State Listen -LocalPort 18300 -ErrorAction SilentlyContinue | ForEach-Object {$p=Get-Process -Id $_.OwningProcess -ErrorAction Stop; [pscustomobject]@{Id=$p.Id;StartTime=$p.StartTime}}); Stop-Service -Name RealYuApi; (Get-Service RealYuApi).WaitForStatus('Stopped',[TimeSpan]::FromSeconds(25)); if (@(Get-NetTCPConnection -State Listen -LocalPort 18300 -ErrorAction SilentlyContinue).Count -ne 0) {throw 'Native listener remains'}; foreach($oldWorker in $before){$still=Get-Process -Id $oldWorker.Id -ErrorAction SilentlyContinue; if($still -and $still.StartTime -eq $oldWorker.StartTime){throw 'Native worker remains'}}")

    def install_manifest(self):
        self.owned()
        require(sha(self.plan["manifest_path"]) == self.plan["verified_files"][self.plan["manifest_path"]], "MANIFEST_CHANGED_DURING_DRAIN")
        save(self.plan["manifest_path"], replacement(self.manifest, self.plan["installed_binary"], self.plan["new_binary_sha256"], self.plan["new_env_file"]))

    def restore_native_manifest(self):
        self.owned()
        current = read(self.plan["manifest_path"])
        expected = replacement(self.manifest, self.plan["installed_binary"], self.plan["new_binary_sha256"], self.plan["new_env_file"])
        require(current in (self.manifest, expected), "UNREVIEWED_MANIFEST_CHANGE")
        save(self.plan["manifest_path"], self.manifest)

    def start(self):
        self.owned()
        self.ps("Start-Service -Name RealYuApi; (Get-Service RealYuApi).WaitForStatus('Running',[TimeSpan]::FromSeconds(10))", timeout=15)

    def ready(self, version, digest):
        deadline = time.monotonic() + 20
        while True:
            try:
                status = self.fetch("http://127.0.0.1:18300/api/status")
                if status.get("data", {}).get("version") == version:
                    require(status["data"].get("single_core") is True and status["data"].get("engine") == "sub2api", "NATIVE_IDENTITY_MISMATCH")
                    break
            except (OSError, json.JSONDecodeError):
                pass
            require(time.monotonic() < deadline, "NATIVE_READINESS_TIMEOUT")
            time.sleep(.25)
        actual = json.loads(self.ps("$l=@(Get-NetTCPConnection -State Listen -LocalPort 18300); if($l.Count -ne 1){throw 'Listener count'}; $p=Get-Process -Id $l[0].OwningProcess; if($p.SessionId -ne 0){throw 'Worker not Session0'}; $w=Get-Content -LiteralPath 'C:\\ProgramData\\RealYuServices\\logs\\api\\worker.json' -Raw | ConvertFrom-Json; $svc=Get-CimInstance Win32_Service -Filter \"Name='RealYuApi'\"; $launcher=Get-CimInstance Win32_Process -Filter ('ProcessId = '+[int]$w.launcher_pid); $worker=Get-CimInstance Win32_Process -Filter ('ProcessId = '+[int]$p.Id); if($svc.State -ne 'Running' -or $w.pid -ne $p.Id -or $worker.ParentProcessId -ne $w.launcher_pid -or $launcher.ParentProcessId -ne $svc.ProcessId){throw 'SCM worker ownership mismatch'}; [pscustomobject]@{sha=(Get-FileHash -LiteralPath $p.Path -Algorithm SHA256).Hash.ToLowerInvariant();pid=$p.Id;session=$p.SessionId;launcher_pid=$w.launcher_pid;service_pid=$svc.ProcessId;binary=$p.Path;start=$p.StartTime.ToUniversalTime().ToString('o')} | ConvertTo-Json -Compress"))
        require(actual["sha"] == digest, "ACTUAL_WORKER_BINARY_MISMATCH")
        save(self.run / ("worker-observation-" + str(time.time_ns()) + ".json"), actual, exclusive=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", required=True)
    parser.add_argument("--expected-plan-sha256", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    require(sha(args.plan) == args.expected_plan_sha256, "PLAN_CHANGED")
    plan = read(args.plan)
    manifest, env = preflight(plan)
    if not args.execute:
        print(json.dumps({"status": "PREFLIGHT_PASS", "production_changed": False}))
        return
    require(os.name == "nt" and ctypes.windll.shell32.IsUserAnAdmin(), "NORMAL_WINDOWS_ELEVATION_REQUIRED")
    with release_control.release_lock(Path(plan["maintenance_marker"]).parent):
        manifest, env = preflight(plan)
        host = Host(plan, manifest, env)
        host.ready(plan["old_version"], plan["old_binary_sha256"])
        result = run_update(plan, host)
    print(json.dumps({"status": result["phase"], "database_restored": False}))


if __name__ == "__main__":
    try:
        main()
    except BaseException as exc:
        print(json.dumps({"status": "FAILED", "code": str(exc) if isinstance(exc, UpdateError) else "PRIVATE_UPDATE_FAILURE", "type": type(exc).__name__}))
        raise SystemExit(1)

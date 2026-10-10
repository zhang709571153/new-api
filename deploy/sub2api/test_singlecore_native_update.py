import copy
import os
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch

from singlecore_native_update import Host, UpdateError, replacement, run_update, updated_environment


class Clock:
    def __init__(self): self.value = 0
    def now(self): return self.value
    def sleep(self, seconds): self.value += seconds


class Operations:
    def __init__(self, busy=False, bad_new=False, bad_old=False):
        self.busy, self.bad_new, self.bad_old = busy, bad_new, bad_old
        self.calls = []; self.journals = []; self.gate = False
    def receipt(self, journal): self.journals.append(copy.deepcopy(journal))
    def close_gate(self): self.gate = True
    def drain(self): return {"maintenance": self.gate, "active_requests": int(self.busy), "reserved_funding": 0, "active_batch_jobs": 0}
    def stop(self): self.calls.append("stop")
    def install_manifest(self): self.calls.append("install")
    def start(self): self.calls.append("start")
    def ready(self, version, digest):
        self.calls.append("ready-" + version)
        if (version == "new" and self.bad_new) or (version == "old" and self.bad_old): raise UpdateError("READINESS_FAILED")
    def restore_native_manifest(self): self.calls.append("restore-native-manifest")
    def open_gate(self, path): self.gate = False; self.calls.append("open-" + path)


class UpdateTests(unittest.TestCase):
    plan = {"operation_id": "test-native-update", "drain_seconds": 1,
            "new_version": "new", "new_binary_sha256": "a" * 64,
            "old_version": "old", "old_binary_sha256": "b" * 64}

    def execute(self, operations):
        clock = Clock()
        return run_update(self.plan, operations, clock.now, clock.sleep)

    def test_success_orders_admission_stop_and_start(self):
        ops = Operations()
        result = self.execute(ops)
        self.assertEqual(result["phase"], "ACTIVE")
        self.assertEqual(ops.calls, ["stop", "install", "start", "ready-new", "open-opened-maintenance.json"])
        self.assertFalse(ops.gate)
        self.assertFalse(result["database_restored"])

    def test_long_stream_aborts_without_stopping_worker(self):
        ops = Operations(busy=True)
        with self.assertRaisesRegex(UpdateError, "ABORTED_BEFORE_STOP"): self.execute(ops)
        self.assertEqual(ops.calls, ["open-aborted-maintenance.json"])
        self.assertEqual(ops.journals[-1]["first_failure"]["code"], "DRAIN_TIMEOUT_NO_STREAM_KILLED")

    def test_pending_settlement_never_allows_stop(self):
        ops = Operations()
        ops.drain = lambda: {"maintenance": True, "active_requests": 0, "reserved_funding": 1, "active_batch_jobs": 0}
        with self.assertRaises(UpdateError): self.execute(ops)
        self.assertNotIn("stop", ops.calls)

    def test_boolean_zero_is_not_a_valid_drain_count(self):
        ops = Operations()
        ops.drain = lambda: {"maintenance": True, "active_requests": False, "reserved_funding": 0, "active_batch_jobs": 0}
        with self.assertRaises(UpdateError): self.execute(ops)
        self.assertNotIn("stop", ops.calls)

    def test_new_start_failure_recovers_same_native_authority(self):
        ops = Operations(bad_new=True)
        with self.assertRaisesRegex(UpdateError, "RECOVERED_SAME_NATIVE_AUTHORITY"): self.execute(ops)
        self.assertEqual(ops.calls, ["stop", "install", "start", "ready-new", "stop", "restore-native-manifest", "start", "ready-old", "open-aborted-maintenance.json"])
        self.assertFalse(ops.journals[-1]["old_legacy_services_started"])
        self.assertFalse(ops.journals[-1]["database_restored"])
        self.assertEqual(ops.journals[-1]["first_failure"]["phase"], "STARTING_NATIVE")

    def test_failed_native_recovery_retains_gate(self):
        ops = Operations(bad_new=True, bad_old=True)
        with self.assertRaisesRegex(UpdateError, "NO_LEGACY_ROLLBACK"): self.execute(ops)
        self.assertTrue(ops.gate)
        self.assertFalse(any(c.startswith("open-") for c in ops.calls))

    def test_receipt_failure_never_blocks_gate_recovery(self):
        ops = Operations()
        ops.receipt = lambda journal: (_ for _ in ()).throw(OSError("private disk detail"))
        with patch('builtins.print'), self.assertRaisesRegex(UpdateError, "ABORTED_BEFORE_STOP"):
            self.execute(ops)
        self.assertFalse(ops.gate)
        self.assertNotIn("stop", ops.calls)

    def test_partial_gate_write_never_closes_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            host = object.__new__(Host)
            host.marker = Path(directory) / 'maintenance.json'
            host.plan = self.plan
            with patch('singlecore_native_update.save', side_effect=OSError('private write failure')):
                with self.assertRaises(OSError): host.close_gate()
            self.assertFalse(host.marker.exists())

    def test_atomic_gate_cannot_replace_another_operation(self):
        with tempfile.TemporaryDirectory() as directory:
            host = object.__new__(Host)
            host.marker = Path(directory) / 'maintenance.json'
            host.plan = self.plan
            host.marker.write_text('{"operation_id":"other"}')
            with self.assertRaises(FileExistsError): host.close_gate()
            self.assertEqual(host.marker.read_text(), '{"operation_id":"other"}')

    def test_only_native_binary_and_display_env_reference_change(self):
        old = {"singlecore_api": {"exe": "old", "sha": "a", "env_file": "env-old", "working_dir": "same"}, "tunnel_edge_addrs_by_role": {"primary": ["route"]}}
        before = copy.deepcopy(old)
        new = replacement(old, "new", "b", "env-new")
        self.assertEqual(old, before)
        self.assertEqual(new["tunnel_edge_addrs_by_role"], old["tunnel_edge_addrs_by_role"])
        self.assertEqual(new["singlecore_api"]["working_dir"], "same")
        self.assertEqual(new["singlecore_api"]["env_file"], "env-new")

    def test_currency_setting_cannot_change_credentials_or_funding(self):
        original = {"DATABASE_PASSWORD": "private", "REALYU_FUNDING_ENABLED": "true"}
        new = updated_environment(original, {"REALYU_CUSTOMER_USD_TO_CNY": "7"})
        self.assertEqual(new, {**original, "REALYU_CUSTOMER_USD_TO_CNY": "7"})
        for additions in ({"DATABASE_PASSWORD": "changed"}, {"REALYU_CUSTOMER_USD_TO_CNY": "7", "REALYU_FUNDING_ENABLED": "false"}):
            with self.assertRaises(UpdateError): updated_environment(original, additions)
        for rate in ("NaN", "Infinity", "0", "-1", "1001", "invalid", 7):
            with self.assertRaises(UpdateError): updated_environment(original, {"REALYU_CUSTOMER_USD_TO_CNY": rate})

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell exit status regression')
    def test_expected_process_absence_does_not_fail_successful_stop_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            host = object.__new__(Host)
            host.run = Path(directory)
            # Int32 maximum cannot be a Windows process ID. This is read-only.
            result = host.ps("foreach($idToCheck in @(2147483647)){$still=Get-Process -Id $idToCheck -ErrorAction SilentlyContinue; if($still){throw 'Unexpected process'}}")
            self.assertEqual(result, '')
            self.assertIn('"returncode": 0', next(host.run.glob('*.log')).read_text(encoding='utf-8'))

    @unittest.skipUnless(os.name == 'nt', 'Windows PowerShell exit status regression')
    def test_explicit_success_does_not_mask_terminating_identity_error(self):
        with tempfile.TemporaryDirectory() as directory:
            host = object.__new__(Host)
            host.run = Path(directory)
            with self.assertRaisesRegex(UpdateError, 'SCM_OR_IDENTITY_CHECK_FAILED'):
                host.ps("throw 'Synthetic ownership mismatch'")
            self.assertIn('"returncode": 1', next(host.run.glob('*.log')).read_text(encoding='utf-8'))


if __name__ == "__main__": unittest.main()

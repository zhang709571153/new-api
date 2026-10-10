import copy
import contextlib
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import singlecore_pg_backup as backup


class BackupTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def manifest(self, changes=None):
        exe = self.root / "native.exe"
        exe.write_bytes(b"test-native")
        env = {"DATABASE_HOST": "127.0.0.1", "DATABASE_PORT": "28490", "DATABASE_DBNAME": backup.SOURCE["database"],
               "DATABASE_USER": backup.SOURCE["user"], "DATABASE_PASSWORD": "private-sentinel",
               "SERVER_HOST": "127.0.0.1", "SERVER_PORT": "18300", "REALYU_FUNDING_ENABLED": "true"}
        env.update(changes or {})
        ep = self.write("env.json", env)
        return self.write("manifest.json", {"singlecore_api": {"env_file": str(ep), "exe": str(exe), "sha": backup.sha256(exe)}})

    def test_authoritative_manifest_and_binary_pin(self):
        path = self.manifest()
        cfg, pins = backup.source_from_manifest(path)
        self.assertEqual(cfg["database"], backup.SOURCE["database"])
        self.assertNotIn("private-sentinel", json.dumps(pins))
        (self.root / "native.exe").write_bytes(b"changed")
        with self.assertRaisesRegex(backup.Refused, "ACTIVE_BINARY_HASH_MISMATCH"):
            backup.source_from_manifest(path)

    def test_refuse_other_source_host_database_user_or_port(self):
        for field, value in (("DATABASE_HOST", "remote"), ("DATABASE_PORT", "29490"),
                             ("DATABASE_DBNAME", "sub2api_production"), ("DATABASE_USER", "postgres")):
            with self.subTest(field=field), self.assertRaisesRegex(backup.Refused, "SOURCE_IDENTITY"):
                backup.source_from_manifest(self.manifest({field: value}))

    def test_refuse_production_restore_and_existing_names(self):
        path = self.write("isolated.json", {"host": "127.0.0.1", "port": 28490, "user": "u", "password": "p"})
        with self.assertRaisesRegex(backup.Refused, "ENDPOINT"):
            backup.isolated_config(path, "realyu_backup_verify_12345678")
        path = self.write("isolated.json", {"host": "127.0.0.1", "port": 29490, "user": "u", "password": "p"})
        with self.assertRaisesRegex(backup.Refused, "NAME"):
            backup.isolated_config(path, "realyu_funding_e2e")
        cfg = backup.isolated_config(path, "realyu_backup_verify_12345678")
        admin = Mock()
        admin.__enter__ = Mock(return_value=admin)
        admin.__exit__ = Mock(return_value=False)
        admin.execute.return_value.fetchone.return_value = (1,)
        with patch.object(backup, "connect", return_value=admin), self.assertRaisesRegex(backup.Refused, "ALREADY_EXISTS"):
            backup.create_restore_database(cfg)
        self.assertEqual(admin.execute.call_count, 1)

    def test_connected_owner_and_endpoint_are_checked(self):
        ident = {**backup.SOURCE, "owner": backup.SOURCE["user"]}
        backup.verify_source(ident)
        for key, value in (("owner", "postgres"), ("host", "::1"), ("port", 29490)):
            with self.subTest(key=key), self.assertRaises(backup.Refused):
                backup.verify_source({**ident, key: value})

    def test_identity_uses_host_not_inet_cidr_text(self):
        conn = Mock()
        conn.execute.return_value.fetchone.return_value = ("db", "u", "u", 180006, "18.6", "127.0.0.1", 28490, 100, "0/1")
        self.assertEqual(backup.database_identity(conn)["host"], "127.0.0.1")
        self.assertIn("host(inet_server_addr())", conn.execute.call_args.args[0])

    def test_native_migration_trim_and_post305_rejection(self):
        path = self.root / "305_realyu_teams.sql"
        path.write_bytes(b"\n  SELECT 1;\r\n SELECT 2; \r\n")
        row = {"filename": path.name, "checksum": hashlib.sha256(path.read_bytes().decode().strip().encode()).hexdigest()}
        backup.verify_migrations([row], self.root)
        lettered = self.root / "120a_existing.sql"
        lettered.write_bytes(b"SELECT 1;")
        backup.verify_migrations([row, {"filename": lettered.name, "checksum": backup.sha256(lettered)}], self.root)
        with self.assertRaisesRegex(backup.Refused, "POST_305"):
            backup.verify_migrations([row, {"filename": "306_next.sql", "checksum": "x"}], self.root)
        with self.assertRaisesRegex(backup.Refused, "CHECKSUM"):
            backup.verify_migrations([{**row, "checksum": "changed"}], self.root)

    def test_mismatch_in_any_table_migration_or_schema_fails(self):
        original = {"schema": {"sha256": "a"}, "schema_migrations": [{"filename": "305", "checksum": "b"}],
                    "table_hashes": {"public.users": {"rows": 2, "sha256": "c"}}}
        backup.verify_restored(original, copy.deepcopy(original))
        for key, value in (("schema", {"sha256": "z"}), ("schema_migrations", []), ("table_hashes", {})):
            with self.subTest(key=key), self.assertRaises(backup.Refused):
                backup.verify_restored(original, {**original, key: value})

    def test_only_equivalent_constant_array_cast_is_normalized(self):
        old = "CHECK (x::text = ANY (ARRAY['a'::character varying, 'b'::character varying]::text[]))"
        restored = "CHECK (x::text = ANY (ARRAY['a'::character varying::text, 'b'::character varying::text]))"
        self.assertEqual(backup.canonical_constraint(old), backup.canonical_constraint(restored))
        self.assertNotEqual(backup.canonical_constraint(old), backup.canonical_constraint(restored.replace("'b'", "'c'")))
        expression = "CHECK (x = ANY (ARRAY[column::character varying]::text[]))"
        self.assertEqual(backup.canonical_constraint(expression), expression)

    def test_disk_insufficient_rejected_before_dump(self):
        with patch.object(backup.shutil, "disk_usage", return_value=Mock(free=1024)), self.assertRaisesRegex(backup.Refused, "DISK"):
            backup.check_disk(self.root, 100)

    def test_password_not_in_child_arguments_or_logs(self):
        cfg = {**backup.SOURCE, "password": "secret-sentinel"}
        args = backup.connection_args(cfg)
        self.assertNotIn("secret-sentinel", repr(args))
        with patch.object(backup.subprocess, "run", return_value=Mock(returncode=0)) as run:
            backup.child("pg_dump.exe", args, cfg, self.root, "dump", True)
        call = run.call_args
        self.assertNotIn("secret-sentinel", repr(call.args))
        self.assertEqual(call.kwargs["env"]["PGPASSWORD"], "secret-sentinel")
        self.assertIn("default_transaction_read_only=on", call.kwargs["env"]["PGOPTIONS"])

    def test_unexpected_private_exception_never_echoes_secret(self):
        output = io.StringIO()
        args = SimpleNamespace(output_root=self.root, manifest="private-path")
        with patch.object(backup, "private_directory", return_value=self.root), \
             patch.object(backup, "source_from_manifest", side_effect=ValueError("private-secret-sentinel")), \
             contextlib.redirect_stdout(output):
            self.assertEqual(backup.execute(args), 1)
        self.assertNotIn("private-secret-sentinel", output.getvalue())
        self.assertNotIn("private-secret-sentinel", (self.root / "first-failure.json").read_text())


if __name__ == "__main__":
    unittest.main()

import contextlib
import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import singlecore_pg_backup308 as module


class Strict308(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.rows = []
        for index in range(1, 298):
            self.add("%03d_old.sql" % index, b"SELECT 1;")
        self.special = {}
        for filename in module.REQUIRED:
            self.add(filename, b"\n  SELECT 2;\r\n")
            self.special[filename] = self.rows[-1]["checksum"]

    def add(self, filename, sql):
        (self.root / filename).write_bytes(sql)
        self.rows.append({"filename": filename, "checksum": hashlib.sha256(sql.decode("utf-8").strip().encode()).hexdigest()})

    def verify(self, rows=None):
        with patch.object(module, "REQUIRED", self.special):
            module.verify_migrations(self.rows if rows is None else rows, self.root)

    def test_exact_300_map_with_native_trim_is_accepted(self):
        self.verify()

    def test_missing_extra_and_duplicate_rows_are_rejected(self):
        for rows in (self.rows[:-1], self.rows + [self.rows[0]], self.rows[:-1] + [self.rows[0]]):
            with self.subTest(count=len(rows)), self.assertRaisesRegex(module.backup.Refused, "COUNT"):
                self.verify(rows)

    def test_changed_record_or_source_is_rejected(self):
        rows = [dict(row) for row in self.rows]
        rows[0]["checksum"] = "0" * 64
        with self.assertRaisesRegex(module.backup.Refused, "MAP"):
            self.verify(rows)
        (self.root / self.rows[0]["filename"]).write_text("SELECT 99;", encoding="utf-8")
        with self.assertRaisesRegex(module.backup.Refused, "MAP"):
            self.verify()

    def test_missing_source_or_later_migration_is_rejected(self):
        old = self.root / self.rows[0]["filename"]
        old.rename(self.root / "309_not_reviewed.sql")
        with self.assertRaisesRegex(module.backup.Refused, "SOURCE_MIGRATION_SET"):
            self.verify()
        (self.root / "309_not_reviewed.sql").unlink()
        with self.assertRaisesRegex(module.backup.Refused, "MAP"):
            self.verify()

    def test_reviewed_306_307_308_checksums_are_not_optional(self):
        with self.assertRaisesRegex(module.backup.Refused, "MAP"):
            module.verify_migrations(self.rows, self.root)

    def test_base_runner_pin_and_old_gate_remain_exact(self):
        self.assertEqual(module.BASE_RUNNER_SHA256, module.backup.sha256(module.backup.__file__))
        old_baseline = {"filename": "305_realyu_teams.sql", "checksum": "synthetic"}
        (self.root / old_baseline["filename"]).write_text("SELECT 1;", encoding="utf-8")
        with self.assertRaisesRegex(module.backup.Refused, "POST_305"):
            module.backup.verify_migrations([old_baseline] + self.rows, self.root)

    def test_changed_base_runner_cannot_execute(self):
        argv = sum((["--" + name, "test-only"] for name in ("manifest", "isolated-config", "pg-bin", "migrations", "output-root")), [])
        with patch.object(module.backup, "sha256", return_value="0" * 64), patch.object(module.backup, "execute") as execute, contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(module.main(argv), 2)
        execute.assert_not_called()

    def test_override_is_scoped_even_when_runner_fails(self):
        argv = sum((["--" + name, "test-only"] for name in ("manifest", "isolated-config", "pg-bin", "migrations", "output-root")), [])
        original = module.backup.verify_migrations
        def fail(_):
            self.assertIs(module.backup.verify_migrations, module.verify_migrations)
            raise RuntimeError("synthetic-failure")
        with patch.object(module.backup, "execute", side_effect=fail), self.assertRaisesRegex(RuntimeError, "synthetic-failure"):
            module.main(argv)
        self.assertIs(module.backup.verify_migrations, original)


if __name__ == "__main__":
    unittest.main()

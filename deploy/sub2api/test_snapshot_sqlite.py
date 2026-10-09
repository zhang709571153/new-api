from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import unittest

from snapshot_sqlite import snapshot


class SnapshotTests(unittest.TestCase):
    def test_wal_snapshot_and_no_source_mutation(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "live.db"
            destination = Path(tmp) / "backup.db"
            with closing(sqlite3.connect(source)) as writer:
                writer.execute("PRAGMA journal_mode=WAL")
                writer.execute('CREATE TABLE ledger (id INTEGER PRIMARY KEY, quota INTEGER)')
                writer.execute("INSERT INTO ledger VALUES (1, 900)")
                writer.commit()
                result = snapshot(source, destination)
                self.assertEqual(result["table_rows"]["ledger"], 1)
                self.assertEqual(writer.execute("SELECT quota FROM ledger").fetchone(), (900,))
                writer.execute("UPDATE ledger SET quota=800 WHERE id=1")
                writer.commit()
            with closing(sqlite3.connect(destination)) as restored:
                self.assertEqual(restored.execute("SELECT quota FROM ledger").fetchone(), (900,))

    def test_refuses_existing_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            destination = Path(tmp) / "existing.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE ledger (id INTEGER)")
            destination.write_bytes(b"preserve")
            with self.assertRaises(FileExistsError):
                snapshot(source, destination)
            self.assertEqual(destination.read_bytes(), b"preserve")

    def test_refuses_self_and_missing_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            with closing(sqlite3.connect(source)) as db:
                db.execute("CREATE TABLE ledger (id INTEGER)")
            with self.assertRaises(ValueError):
                snapshot(source, source)
            with self.assertRaises(FileNotFoundError):
                snapshot(Path(tmp) / "missing.db", Path(tmp) / "output.db")
            self.assertFalse((Path(tmp) / "output.db").exists())


if __name__ == "__main__":
    unittest.main()

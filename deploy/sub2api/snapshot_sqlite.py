"""Create a consistent private SQLite snapshot without writing the source DB.

The result contains the complete customer ledger and MUST NOT be uploaded to Git.
This is a rehearsal snapshot, not a cross-machine cutover or a stop/drain tool.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3


def snapshot(source: Path, destination: Path) -> dict:
    source = source.resolve(strict=True)
    destination = destination.resolve()
    if destination == source:
        raise ValueError("Source and destination must differ")
    if not destination.parent.is_dir():
        raise ValueError("Create a private destination directory first")
    # Exclusive creation prevents overwriting another backup, including when two
    # operators choose the same name. Preserve failures for diagnosis.
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(fd)
    with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
        with closing(sqlite3.connect(destination)) as backup:
            original.backup(backup)
            check = backup.execute("PRAGMA integrity_check").fetchall()
            if check != [("ok",)]:
                raise RuntimeError("Snapshot integrity_check failed; retain snapshot privately")
            tables = [row[0] for row in backup.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )]
            counts = {}
            for table in tables:
                quoted = '"' + table.replace('"', '""') + '"'
                counts[table] = backup.execute("SELECT COUNT(*) FROM " + quoted).fetchone()[0]
    with destination.open("rb") as snapshot_file:
        digest = hashlib.file_digest(snapshot_file, "sha256").hexdigest()
    return {"status": "PASS", "sha256": digest, "bytes": destination.stat().st_size,
            "integrity_check": "ok", "table_rows": counts,
            "scope": "one consistent SQLite snapshot; live writes after this snapshot are not included"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    args = parser.parse_args()
    report = snapshot(args.source, args.destination)
    manifest = args.destination.with_suffix(args.destination.suffix + ".manifest.json")
    with manifest.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2)
    print(json.dumps({"status": report["status"], "sha256": report["sha256"],
                      "tables": len(report["table_rows"])}))


if __name__ == "__main__":
    main()

"""Strict 307 snapshot/isolated-restore entry; preserves the original 305 runner.

The reused runner keeps source read-only and restores only a new loopback 29490
database. A snapshot is evidence, never permission to rewind an active ledger.
"""
import argparse
import hashlib
from pathlib import Path
import re
import singlecore_pg_backup as backup

BASE_RUNNER_SHA256 = "cbbe20c414edf90445db9c4101ad323cfdfa9a4a2a8271b5366ce9a6977b8e74"
REQUIRED = {
    "306_realyu_managed_purchases.sql": "0399b87f731f3511f42ca973f44014cf96ae9a28336a99889ffff3f92ab30bd5",
    "307_realyu_team_member_nicknames.sql": "deb1deaaccae541d7b5cf5dda6cb098eeaf3598fe3227700b3d4c955ebe37c16",
}

def verify_migrations(rows, source_migrations):
    by_name = {x["filename"]: x["checksum"] for x in rows}
    if len(rows) != 299 or len(by_name) != 299:
        raise backup.Refused("EXACT_307_MIGRATION_COUNT_REQUIRED")
    expected = {}
    for path in Path(source_migrations).glob("*.sql"):
        match = re.match(r"^(\d+)", path.name)
        if match and int(match.group(1)) <= 307:
            expected[path.name] = hashlib.sha256(path.read_bytes().decode("utf-8").strip().encode()).hexdigest()
    if by_name != expected or any(by_name.get(name) != checksum for name, checksum in REQUIRED.items()):
        raise backup.Refused("EXACT_307_MIGRATION_MAP_REQUIRED")

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ("manifest","isolated-config","pg-bin","migrations","output-root"):
        p.add_argument("--"+name,required=True)
    args=p.parse_args()
    if backup.sha256(backup.__file__) != BASE_RUNNER_SHA256:
        print('{"status":"BLOCKED","code":"REVIEWED_BACKUP_RUNNER_CHANGED"}')
        return 2
    backup.verify_migrations=verify_migrations
    return backup.execute(args)

if __name__=="__main__":raise SystemExit(main())

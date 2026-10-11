"""Strict 308 snapshot/isolated-restore entry; preserves the 305 and 307 gates.

Production remains read-only. The reviewed runner may restore only a fresh
loopback 29490 database. Its receipt is backup evidence, never rollback approval.
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
    "308_realyu_usage_history.sql": "3ee70f5ffb0910c1693641c686a24e567ea2c0b87d32a7cb2084cffdccb3048c",
}


def verify_migrations(rows, source_migrations):
    by_name = {row["filename"]: row["checksum"] for row in rows}
    if len(rows) != 300 or len(by_name) != 300:
        raise backup.Refused("EXACT_308_MIGRATION_COUNT_REQUIRED")

    expected = {}
    for path in Path(source_migrations).glob("*.sql"):
        match = re.match(r"^(\d+)", path.name)
        if not match or int(match.group(1)) > 308:
            raise backup.Refused("EXACT_308_SOURCE_MIGRATION_SET_REQUIRED")
        # Native migration checksum is SHA256 of UTF-8 strings.TrimSpace(SQL).
        expected[path.name] = hashlib.sha256(
            path.read_bytes().decode("utf-8").strip().encode("utf-8")
        ).hexdigest()
    if len(expected) != 300 or by_name != expected or any(
        by_name.get(name) != checksum for name, checksum in REQUIRED.items()
    ):
        raise backup.Refused("EXACT_308_MIGRATION_MAP_REQUIRED")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("manifest", "isolated-config", "pg-bin", "migrations", "output-root"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args(argv)
    if backup.sha256(backup.__file__) != BASE_RUNNER_SHA256:
        print('{"status":"BLOCKED","code":"REVIEWED_BACKUP_RUNNER_CHANGED"}')
        return 2

    original_verifier = backup.verify_migrations
    backup.verify_migrations = verify_migrations
    try:
        return backup.execute(args)
    finally:
        backup.verify_migrations = original_verifier


if __name__ == "__main__":
    raise SystemExit(main())

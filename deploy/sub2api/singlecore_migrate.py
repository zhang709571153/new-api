"""Inspect/import a consistent RealYu SQLite snapshot into an isolated Sub2API.

Rehearsal by default; an explicitly pinned, separate staging database may share
the production PG cluster. This command never activates traffic or source data.
Private input includes passwords and keys;
only anonymous counts, hashes and blocker codes are printed or returned.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import ipaddress
import json
from pathlib import Path
import re
import sqlite3
import sys

QUOTA_PER_USD = 500_000
MAX_QUOTA = 9_007_199_254_740_991
STAGING_DATABASE = "realyu_singlecore_20261010_candidate"
STAGING_INSTALLATION = "realyu-singlecore-production-20261010"
STAGING_ROLE = "realyu_singlecore_owner_20261010"
TABLES = (
    "users", "tokens", "workspace_teams", "workspace_team_accounts",
    "workspace_members", "workspace_personal_keys", "workspace_invites",
    "workspace_member_weekly_usages", "user_subscriptions", "subscription_plans",
    "billing_orders", "subscription_orders", "subscription_pre_consume_records",
    "workspace_funding_migrations", "workspace_member_opening_receipts",
)
ARCHIVE_TABLES = tuple(t for t in TABLES if t not in ("users", "tokens"))
SOURCE_KEYS = {
    "workspace_team_accounts": ("team_id",), "workspace_members": ("user_id",),
    "workspace_personal_keys": ("user_id",), "workspace_invites": ("hash",),
    "workspace_member_weekly_usages": ("team_id", "user_id", "subscription_id", "weekly_reset_at"),
    "workspace_funding_migrations": ("team_id",),
    "workspace_member_opening_receipts": ("operation_id",),
}


class MigrationError(ValueError):
    """Messages contain codes/column names, never customer values or SQL errors."""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha(data):
    return hashlib.sha256(data).hexdigest()


def bounded_quota(value, *, signed=False):
    if type(value) is not int or abs(value) > MAX_QUOTA or (not signed and value < 0):
        raise MigrationError("INVALID_QUOTA")
    return value


def usd(quota):
    return Decimal(bounded_quota(quota, signed=True)) / Decimal(QUOTA_PER_USD)


def source_time(value):
    if value in (None, "", 0, "0001-01-01 00:00:00+00:00"):
        return None
    if isinstance(value, int):
        return datetime.fromtimestamp(value, timezone.utc)
    try:
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp
    except (ValueError, TypeError, OverflowError):
        raise MigrationError("INVALID_SOURCE_TIMESTAMP") from None


def source_key(table, row):
    return canonical([row[column] for column in SOURCE_KEYS.get(table, ("id",))])


def load_snapshot(path: Path, expected_sha: str):
    path = path.resolve(strict=True)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
        raise MigrationError("EXPECTED_SNAPSHOT_SHA256_REQUIRED")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-journal")):
        raise MigrationError("USE_A_COMPLETED_BACKUP_NOT_AN_ACTIVE_DATABASE")
    with path.open("rb") as file:
        data_hash = hashlib.file_digest(file, "sha256").hexdigest()
    if data_hash != expected_sha:
        raise MigrationError("SOURCE_SHA256_MISMATCH")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as conn:
        conn.row_factory = sqlite3.Row
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise MigrationError("SOURCE_INTEGRITY_FAILED")
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if set(TABLES) - existing:
            raise MigrationError("SOURCE_SCHEMA_MISSING_REQUIRED_TABLES")
        # Do not silently turn an MFA/passkey/OAuth account into password-only
        # authentication. These identity adapters require a separate migration.
        if "two_fas" in existing and conn.execute("SELECT EXISTS(SELECT 1 FROM two_fas WHERE is_enabled=1 AND deleted_at IS NULL)").fetchone()[0]:
            raise MigrationError("ACTIVE_SOURCE_MFA_REQUIRES_EXPLICIT_MIGRATION")
        for name in ("passkey_credentials", "user_oauth_bindings"):
            if name in existing and conn.execute('SELECT EXISTS(SELECT 1 FROM "'+name+'")').fetchone()[0]:
                raise MigrationError("SOURCE_PASSKEY_OR_OAUTH_REQUIRES_EXPLICIT_MIGRATION")
        tables = {name: [dict(row) for row in conn.execute('SELECT * FROM "' + name + '"')] for name in TABLES}
    # Detect concurrent replacement/modification even when the caller supplied a
    # writable snapshot path. SQLite immutable mode is safe only for a real backup.
    with path.open("rb") as file:
        if hashlib.file_digest(file, "sha256").hexdigest() != expected_sha:
            raise MigrationError("SOURCE_CHANGED_DURING_READ")
    for name, rows in tables.items():
        rows.sort(key=lambda row: source_key(name, row))
    return tables


def prepare(tables):
    users = {r["id"]: r for r in tables["users"]}
    teams = {r["id"]: r for r in tables["workspace_teams"]}
    accounts = {r["team_id"]: r for r in tables["workspace_team_accounts"]}
    members = {r["token_id"]: r for r in tables["workspace_members"]}
    keys = {r["id"]: r for r in tables["tokens"]}
    blockers = []
    names = set()
    for row in users.values():
        if any(row.get(col) not in (None,"",0,"0") for col in ("github_id","discord_id","oidc_id","wechat_id","telegram_id","linux_do_id")):
            raise MigrationError("SOURCE_OAUTH_IDENTITY_REQUIRES_EXPLICIT_MIGRATION")
        if row["id"] <= 0 or not row["username"] or row["username"] in names or len(row["username"]) > 191:
            raise MigrationError("INVALID_OR_DUPLICATE_LOGIN_NAME")
        names.add(row["username"])
        bounded_quota(row["quota"], signed=True)
        if row["role"] not in (1, 10, 100) or row["status"] not in (1, 2):
            raise MigrationError("UNMAPPED_SOURCE_USER_STATE")
        if len(row["display_name"] or "") > 100:
            raise MigrationError("NICKNAME_EXCEEDS_NATIVE_LIMIT")
        password = row["password"] or ""
        if password and not (password.startswith("$argon2id$") or re.match(r"^\$2[aby]\$", password)):
            raise MigrationError("UNSUPPORTED_SOURCE_PASSWORD_FORMAT")
    for team in teams.values():
        account = accounts.get(team["id"])
        if team["funding_version"] != 1 or account is None or account["owner_user_id"] != team["owner_user_id"]:
            raise MigrationError("TEAM_FUNDING_REQUIRES_EXPLICIT_RECONCILIATION")
        if team["owner_user_id"] not in users:
            raise MigrationError("TEAM_OWNER_MISSING")
    prepared_keys = []
    seen_keys = set()
    for row in keys.values():
        actor = row["workspace_user_id"] or row["user_id"]
        if actor not in users or row["user_id"] not in users:
            if row["status"] != 1 and source_time(row["deleted_at"]) is not None:
                # Historical disabled tombstones need no invented login owner.
                # A sanitized archive below preserves their identity and key hash.
                continue
            raise MigrationError("TOKEN_OWNER_MISSING")
        team_id = 0
        usable = row["status"] == 1 and source_time(row["deleted_at"]) is None
        if row["workspace_user_id"]:
            member = members.get(row["id"])
            if member is None:
                # A historical key without a current membership stays disabled.
                usable = False
            else:
                team_id = member["team_id"]
                if team_id not in teams or teams[team_id]["owner_user_id"] != row["user_id"] or member["user_id"] != actor:
                    raise MigrationError("TOKEN_MEMBER_SCOPE_MISMATCH")
                usable = usable and member["status"] == 1
                if actor != row["user_id"] and row["unlimited_quota"]:
                    usable = False
        wire_key = "sk-" + row["key"].removeprefix("sk-")
        if wire_key in seen_keys or not re.fullmatch(r"sk-[A-Za-z0-9_-]{16,125}", wire_key) or len(wire_key) > 128:
            raise MigrationError("INVALID_OR_DUPLICATE_CLIENT_KEY")
        seen_keys.add(wire_key)
        ips = []
        for token in (row["allow_ips"] or "").replace(" ", "").splitlines():
            token = token.strip().replace(",", "")
            if token:
                try:
                    ipaddress.ip_network(token, strict=False)
                except ValueError:
                    raise MigrationError("INVALID_SOURCE_IP_RESTRICTION") from None
                ips.append(token)
        bounded_quota(row["used_quota"])
        bounded_quota(row["remain_quota"], signed=True)
        models = {x: True for x in (row["model_limits"] or "").split(",") if x}
        group = row["group"] or users[row["user_id"]]["group"]
        if group == "auto" or row.get("cross_group_retry") or row.get("auto_groups") not in (None, "", "[]"):
            raise MigrationError("DYNAMIC_GROUP_ROUTING_REQUIRES_EXPLICIT_MAPPING")
        prepared_keys.append({"row": row, "actor": actor, "team_id": team_id, "usable": usable,
                              "wire_key": wire_key, "ips": ips, "models": models, "source_group": group})
    for row in tables["user_subscriptions"]:
        for col in ("amount_total", "amount_used", "weekly_amount", "weekly_used"):
            bounded_quota(row[col])
        if row["status"] not in ("active", "expired", "cancelled") or row["end_time"] <= row["start_time"]:
            raise MigrationError("UNMAPPED_ENTITLEMENT_STATE")
        if row["workspace_team_id"] and row["allow_wallet_overflow"]:
            raise MigrationError("TEAM_WALLET_OVERFLOW_CONFLICT")
    unresolved = sum(r["status"] not in ("settled", "refunded") for r in tables["subscription_pre_consume_records"])
    if unresolved:
        blockers.append({"code": "SOURCE_UNSETTLED_RESERVATIONS", "count": unresolved})
    pending = sum(r["status"] == "pending" for r in tables["billing_orders"])
    if pending:
        blockers.append({"code": "OLD_PAYMENT_CALLBACK_TAKEOVER_REQUIRED", "count": pending})
    scoped_admins = sum(r["role"] == 10 for r in users.values())
    if scoped_admins:
        blockers.append({"code": "LEGACY_SCOPED_ADMINS_REQUIRE_ROLE_MAPPING", "count": scoped_admins})
    blockers.append({"code": "REHEARSAL_ONLY_NO_ACTIVATION"})
    return prepared_keys, blockers


def inspect(tables, source_sha):
    keys, blockers = prepare(tables)
    return {"schema": 1, "mode": "rehearsal", "source_sha256": source_sha,
            "counts": {t: len(rows) for t, rows in tables.items()},
            "importable_keys": len(keys), "archived_orphan_tombstones": len(tables["tokens"]) - len(keys),
            "source_groups": sorted({k["source_group"] for k in keys} | {u["group"] for u in tables["users"]}),
            "table_sha256": {t: sha(canonical(rows).encode()) for t, rows in tables.items()},
            "activation_blockers": blockers, "production_actions": False}


def validate_target(config):
    if config.get("mode") not in ("rehearsal", "staging") or config.get("host") not in ("127.0.0.1", "::1"):
        raise MigrationError("ONLY_EXPLICIT_LOOPBACK_REHEARSAL_TARGETS_ALLOWED")
    if config["mode"] == "staging":
        if (type(config.get("port")) is not int or config["port"] != 28490
                or config.get("database") != STAGING_DATABASE
                or config.get("installation_id") != STAGING_INSTALLATION
                or config.get("user") != STAGING_ROLE):
            raise MigrationError("ONLY_PINNED_SEPARATE_STAGING_DATABASE_ALLOWED")
    elif type(config.get("port")) is not int or config["port"] in (5432, 28490) or not (1024 < config["port"] < 65536):
        raise MigrationError("TARGET_PORT_NOT_ISOLATED")
    if not re.fullmatch(r"realyu_[a-z0-9_]*(?:e2e|rehearsal|candidate)[a-z0-9_]*", config.get("database", "")):
        raise MigrationError("TARGET_DATABASE_NOT_ISOLATED")
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,80}", config.get("installation_id", "")):
        raise MigrationError("INSTALLATION_ID_REQUIRED")
    mapping = config.get("group_mapping")
    if not isinstance(mapping, dict) or any(type(x) is not int or x <= 0 for x in mapping.values()):
        raise MigrationError("EXPLICIT_GROUP_MAPPING_REQUIRED")


def apply_snapshot(tables, source_sha, config, operation_id):
    """Single transaction; same operation is a no-op, changed inputs are rejected."""
    validate_target(config)
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,100}", operation_id):
        raise MigrationError("INVALID_OPERATION_ID")
    import psycopg
    from psycopg.types.json import Jsonb

    plan = inspect(tables, source_sha)
    prepared_keys, _ = prepare(tables)
    mapping = config["group_mapping"]
    if set(plan["source_groups"]) - set(mapping):
        raise MigrationError("SOURCE_GROUP_NOT_MAPPED")
    mapping_hash = sha(canonical({"schema": 1, "group_mapping": mapping, "installation_id": config["installation_id"]}).encode())
    with psycopg.connect(host=config["host"], port=config["port"], dbname=config["database"],
                         user=config["user"], password=config["password"], connect_timeout=5) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL lock_timeout='5s'")
            conn.execute("SET LOCAL statement_timeout='60s'")
            conn.execute("SELECT pg_advisory_xact_lock(784930201026)")
            conn.execute("LOCK TABLE users,api_keys,realyu_legacy_identities,realyu_legacy_keys,realyu_key_scopes,"
                         "realyu_teams,realyu_team_members,realyu_funding_subscriptions,realyu_member_period_usage,"
                         "realyu_funding_requests,usage_logs,payment_orders IN ACCESS EXCLUSIVE MODE")
            state = conn.execute("SELECT installation_id,phase FROM realyu_migration_state WHERE singleton FOR UPDATE").fetchone()
            if state is not None and state != (config["installation_id"], "staging"):
                raise MigrationError("TARGET_NOT_STAGING_OR_WRONG_INSTALLATION")
            old = conn.execute("SELECT source_sha256,mapping_sha256,receipt FROM realyu_migration_runs WHERE operation_id=%s", (operation_id,)).fetchone()
            if old:
                if old[0] != source_sha or old[1] != mapping_hash:
                    raise MigrationError("OPERATION_ID_INPUT_CONFLICT")
                return {**old[2], "idempotent_replay": True}
            for name in ("usage_logs", "payment_orders", "realyu_funding_requests"):
                if conn.execute("SELECT EXISTS(SELECT 1 FROM " + name + ")").fetchone()[0]:
                    raise MigrationError("TARGET_ALREADY_HAS_BUSINESS_WRITES")
            if conn.execute("SELECT EXISTS(SELECT 1 FROM users u WHERE NOT EXISTS(SELECT 1 FROM realyu_legacy_identities i WHERE i.user_id=u.id AND i.source_user_id IS NOT NULL) AND (u.role<>'admin' OR u.balance<>0))").fetchone()[0]:
                raise MigrationError("TARGET_HAS_UNMAPPED_CUSTOMERS_OR_INTERNAL_CREDIT")
            if conn.execute("SELECT EXISTS(SELECT 1 FROM api_keys k WHERE NOT EXISTS(SELECT 1 FROM realyu_legacy_keys l WHERE l.api_key_id=k.id))").fetchone()[0]:
                raise MigrationError("TARGET_HAS_UNMAPPED_KEYS")
            for group_id in set(mapping.values()):
                group = conn.execute("SELECT status,subscription_type FROM groups WHERE id=%s AND deleted_at IS NULL", (group_id,)).fetchone()
                if group is None or group != ("active", "standard"):
                    raise MigrationError("TARGET_GROUP_MUST_BE_ACTIVE_STANDARD")
            conn.execute("INSERT INTO realyu_migration_state(singleton,installation_id) VALUES(TRUE,%s) ON CONFLICT(singleton) DO NOTHING", (config["installation_id"],))
            user_map = dict(conn.execute("SELECT source_user_id,user_id FROM realyu_legacy_identities WHERE source_user_id IS NOT NULL"))
            source_ids = {r["id"] for r in tables["users"]}
            # Hard-deleted source users cannot silently remain usable at S1.
            for removed in set(user_map) - source_ids:
                conn.execute("UPDATE users SET status='disabled',deleted_at=COALESCE(deleted_at,NOW()) WHERE id=%s", (user_map[removed],))
                conn.execute("UPDATE api_keys SET status='disabled' WHERE user_id=%s", (user_map[removed],))
            for row in tables["users"]:
                email = row["email"] or f"{row['id']}@realyu-legacy.invalid"
                # Only the former root receives unrestricted native admin.
                # Role 10's narrower permissions need an explicit future mapping.
                values = (email, row["password"] or "", "admin" if row["role"] == 100 else "user", usd(row["quota"]),
                          "active" if row["status"] == 1 else "disabled", row["display_name"] or row["username"],
                          source_time(row["created_at"]) or datetime(1970, 1, 1, tzinfo=timezone.utc), source_time(row["deleted_at"]))
                target = user_map.get(row["id"])
                if target:
                    conn.execute("UPDATE users SET email=%s,password_hash=%s,role=%s,balance=%s,status=%s,username=%s,created_at=%s,deleted_at=%s,updated_at=NOW() WHERE id=%s", (*values, target))
                else:
                    target = conn.execute("INSERT INTO users(email,password_hash,role,balance,status,username,created_at,deleted_at,signup_source,restrict_public_groups,balance_notify_enabled) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'realyu_legacy',TRUE,FALSE) RETURNING id", values).fetchone()[0]
                    user_map[row["id"]] = target
                conn.execute("INSERT INTO realyu_legacy_identities(user_id,source_user_id,login_name) VALUES(%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET login_name=EXCLUDED.login_name,email_verified_at=NULL", (target, row["id"], row["username"]))
                conn.execute("INSERT INTO realyu_legacy_user_metadata(user_id,source_role) VALUES(%s,%s) ON CONFLICT(user_id) DO UPDATE SET source_role=EXCLUDED.source_role", (target, row["role"]))
            accounts = {r["team_id"]: r for r in tables["workspace_team_accounts"]}
            for row in tables["workspace_teams"]:
                a = accounts[row["id"]]
                conn.execute("INSERT INTO realyu_teams(id,owner_user_id,name,historical_quota,closed_at) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET owner_user_id=EXCLUDED.owner_user_id,name=EXCLUDED.name,historical_quota=EXCLUDED.historical_quota,closed_at=EXCLUDED.closed_at", (row["id"], user_map[row["owner_user_id"]], row["name"], a["quota"], a["closed_at"]))
            # Absolute period closing state on an unopened target, never additive.
            conn.execute("UPDATE realyu_team_members SET status='removed'")
            source_keys = {r["id"]: r for r in tables["tokens"]}
            for row in tables["workspace_members"]:
                cap = row["weekly_quota"]
                if cap is None:
                    cap = max(0, source_keys[row["token_id"]]["remain_quota"])
                bounded_quota(cap)
                conn.execute("INSERT INTO realyu_team_members(team_id,user_id,status,weekly_cap_quota) VALUES(%s,%s,%s,%s) ON CONFLICT(team_id,user_id) DO UPDATE SET status=EXCLUDED.status,weekly_cap_quota=EXCLUDED.weekly_cap_quota", (row["team_id"], user_map[row["user_id"]], "active" if row["status"] == 1 else "disabled", cap))
            key_map = dict(conn.execute("SELECT source_token_id,api_key_id FROM realyu_legacy_keys"))
            importable_ids = {item["row"]["id"] for item in prepared_keys}
            for source_id, target in key_map.items():
                if source_id not in importable_ids:
                    conn.execute("UPDATE api_keys SET status='disabled',deleted_at=COALESCE(deleted_at,NOW()) WHERE id=%s", (target,))
            # The unopened candidate's imported customer permissions are an
            # exact snapshot. Removed/disabled keys cannot retain group grants.
            for target in user_map.values():
                conn.execute("DELETE FROM user_allowed_groups WHERE user_id=%s", (target,))
            # An active source user's own group is an independent permission:
            # new users without old keys must still be able to create a key.
            for user in tables["users"]:
                if user["status"] == 1 and source_time(user["deleted_at"]) is None:
                    conn.execute("INSERT INTO user_allowed_groups(user_id,group_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (user_map[user["id"]], mapping[user["group"]]))
            for item in prepared_keys:
                row = item["row"]; group = mapping[item["source_group"]]
                unlimited = bool(row["unlimited_quota"]) or bool(item["team_id"])
                total_quota = 0 if unlimited else bounded_quota(max(0, row["used_quota"] + row["remain_quota"]))
                # Native 0 means unlimited; a zero-limited key must be exhausted.
                status = "active" if item["usable"] else "disabled"
                if status == "active" and not unlimited and total_quota <= row["used_quota"]:
                    status = "quota_exhausted"
                expires = source_time(row["expired_time"]) if row["expired_time"] > 0 else None
                values = (user_map[item["actor"]], item["wire_key"], row["name"] or "Imported key", group, status,
                          Jsonb(item["ips"]), usd(total_quota), usd(row["used_quota"]), expires, source_time(row["deleted_at"]))
                target = key_map.get(row["id"])
                if target:
                    conn.execute("UPDATE api_keys SET user_id=%s,key=%s,name=%s,group_id=%s,status=%s,ip_whitelist=%s,quota=%s,quota_used=%s,expires_at=%s,deleted_at=%s,updated_at=NOW() WHERE id=%s", (*values, target))
                else:
                    target = conn.execute("INSERT INTO api_keys(user_id,key,name,group_id,status,ip_whitelist,quota,quota_used,expires_at,deleted_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id", values).fetchone()[0]
                    key_map[row["id"]] = target
                if item["usable"]:
                    conn.execute("INSERT INTO user_allowed_groups(user_id,group_id) VALUES(%s,%s) ON CONFLICT DO NOTHING", (user_map[item["actor"]], group))
                # No current membership -> disabled personal-shaped tombstone;
                # this key is never made usable by an import.
                payer = row["user_id"] if item["team_id"] else item["actor"]
                conn.execute("INSERT INTO realyu_key_scopes(api_key_id,actor_user_id,payer_user_id,team_id) VALUES(%s,%s,%s,%s) ON CONFLICT(api_key_id) DO UPDATE SET actor_user_id=EXCLUDED.actor_user_id,payer_user_id=EXCLUDED.payer_user_id,team_id=EXCLUDED.team_id", (target, user_map[item["actor"]], user_map[payer], item["team_id"] or None))
                conn.execute("INSERT INTO realyu_legacy_keys(source_token_id,api_key_id,source_actor_id,source_payer_id,legacy_team_id,model_limits_enabled,model_limits,unlimited_quota,opening_used_quota,opening_remaining_quota) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(source_token_id) DO UPDATE SET source_actor_id=EXCLUDED.source_actor_id,source_payer_id=EXCLUDED.source_payer_id,legacy_team_id=EXCLUDED.legacy_team_id,model_limits_enabled=EXCLUDED.model_limits_enabled,model_limits=EXCLUDED.model_limits,unlimited_quota=EXCLUDED.unlimited_quota,opening_used_quota=EXCLUDED.opening_used_quota,opening_remaining_quota=EXCLUDED.opening_remaining_quota", (row["id"], target, item["actor"], row["user_id"], item["team_id"], bool(row["model_limits_enabled"]), Jsonb(item["models"]), bool(row["unlimited_quota"]), row["used_quota"], row["remain_quota"]))
            conn.execute("UPDATE realyu_funding_subscriptions SET status='cancelled'")
            for row in tables["user_subscriptions"]:
                columns = ["id", "user_id", "team_id", "amount_total", "amount_used", "weekly_amount", "weekly_used", "weekly_reset_at", "start_time", "end_time", "status", "source", "purchase_price_cents", "purchase_title", "allow_wallet_overflow"]
                values = [row.get(c) for c in columns]
                values[1] = user_map[row["user_id"]]; values[2] = row["workspace_team_id"] or None
                values[-1] = bool(row["allow_wallet_overflow"])
                updates = ",".join(c + "=EXCLUDED." + c for c in columns[1:])
                conn.execute("INSERT INTO realyu_funding_subscriptions(" + ",".join(columns) + ") VALUES(" + ",".join(["%s"] * len(columns)) + ") ON CONFLICT(id) DO UPDATE SET " + updates, values)
            conn.execute("DELETE FROM realyu_member_period_usage")
            for row in tables["workspace_member_weekly_usages"]:
                conn.execute("INSERT INTO realyu_member_period_usage(team_id,user_id,subscription_id,weekly_reset_at,used_quota,opening_used_quota) VALUES(%s,%s,%s,%s,%s,%s)", (row["team_id"], user_map[row["user_id"]], row["subscription_id"], row["weekly_reset_at"], row["used_quota"], row["opening_used_quota"]))
            for table in ARCHIVE_TABLES:
                # Archives represent the exact latest source snapshot; run hashes
                # retain evidence of previous rehearsals, not invented purchases.
                conn.execute("DELETE FROM realyu_legacy_records WHERE source_table=%s", (table,))
                for row in tables[table]:
                    conn.execute("INSERT INTO realyu_legacy_records(source_table,source_key,row_sha256,source_row,snapshot_sha256) VALUES(%s,%s,%s,%s,%s)", (table, source_key(table, row), sha(canonical(row).encode()), Jsonb(row), source_sha))
            imported_key_ids = {item["row"]["id"] for item in prepared_keys}
            conn.execute("DELETE FROM realyu_legacy_records WHERE source_table='orphan_token_tombstones'")
            for row in tables["tokens"]:
                if row["id"] not in imported_key_ids:
                    archived = {k: v for k, v in row.items() if k != "key"}
                    archived["key_sha256"] = sha(row["key"].encode())
                    conn.execute("INSERT INTO realyu_legacy_records(source_table,source_key,row_sha256,source_row,snapshot_sha256) VALUES(%s,%s,%s,%s,%s)", ("orphan_token_tombstones", source_key("tokens", row), sha(canonical(archived).encode()), Jsonb(archived), source_sha))
            # Per-identity exact assertions run before COMMIT. No total-only or
            # float epsilon reconciliation can hide a misattributed account.
            for row in tables["users"]:
                actual = conn.execute("SELECT u.balance,u.password_hash,u.username,i.login_name FROM users u JOIN realyu_legacy_identities i ON i.user_id=u.id WHERE u.id=%s", (user_map[row["id"]],)).fetchone()
                if actual != (usd(row["quota"]), row["password"] or "", row["display_name"] or row["username"], row["username"]):
                    raise MigrationError("USER_RECONCILIATION_FAILED")
            for item in prepared_keys:
                row = item["row"]
                actual = conn.execute("SELECT k.key,k.user_id,s.actor_user_id,s.team_id,k.quota_used FROM api_keys k JOIN realyu_key_scopes s ON s.api_key_id=k.id WHERE k.id=%s", (key_map[row["id"]],)).fetchone()
                if actual != (item["wire_key"], user_map[item["actor"]], user_map[item["actor"]], item["team_id"] or None, usd(row["used_quota"])):
                    raise MigrationError("KEY_RECONCILIATION_FAILED")
            for row in tables["workspace_teams"]:
                a = accounts[row["id"]]
                actual = conn.execute("SELECT owner_user_id,historical_quota,closed_at FROM realyu_teams WHERE id=%s", (row["id"],)).fetchone()
                if actual != (user_map[row["owner_user_id"]], a["quota"], a["closed_at"]):
                    raise MigrationError("TEAM_RECONCILIATION_FAILED")
            for row in tables["user_subscriptions"]:
                columns = ["amount_total", "amount_used", "weekly_amount", "weekly_used", "weekly_reset_at", "start_time", "end_time", "status", "source", "purchase_price_cents", "purchase_title"]
                actual = conn.execute("SELECT user_id,team_id," + ",".join(columns) + ",allow_wallet_overflow FROM realyu_funding_subscriptions WHERE id=%s", (row["id"],)).fetchone()
                expected = (user_map[row["user_id"]], row["workspace_team_id"] or None, *(row[c] for c in columns), bool(row["allow_wallet_overflow"]))
                if actual != expected:
                    raise MigrationError("ENTITLEMENT_RECONCILIATION_FAILED")
            for row in tables["workspace_member_weekly_usages"]:
                actual = conn.execute("SELECT used_quota,opening_used_quota FROM realyu_member_period_usage WHERE team_id=%s AND user_id=%s AND subscription_id=%s AND weekly_reset_at=%s", (row["team_id"], user_map[row["user_id"]], row["subscription_id"], row["weekly_reset_at"])).fetchone()
                if actual != (row["used_quota"], row["opening_used_quota"]):
                    raise MigrationError("MEMBER_PERIOD_RECONCILIATION_FAILED")
            receipt = {**plan, "operation_id": operation_id, "mapping_sha256": mapping_hash,
                       "mode": config["mode"],
                       "status": "STAGED", "idempotent_replay": False, "activated": False,
                       "native_defaults_granted": 0, "user_and_key_reconciliation": "exact", "team_entitlement_period_reconciliation": "exact"}
            conn.execute("INSERT INTO realyu_migration_runs(operation_id,source_sha256,mapping_sha256,receipt) VALUES(%s,%s,%s,%s)", (operation_id, source_sha, mapping_hash, Jsonb(receipt)))
            conn.execute("UPDATE realyu_migration_state SET source_sha256=%s,updated_at=NOW() WHERE singleton", (source_sha,))
        return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["inspect", "import-rehearsal"])
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--private-target", type=Path)
    parser.add_argument("--operation-id")
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    try:
        tables = load_snapshot(args.snapshot, args.sha256)
        if args.command == "inspect":
            result = inspect(tables, args.sha256)
        else:
            if args.private_target is None or args.operation_id is None:
                raise MigrationError("PRIVATE_TARGET_AND_OPERATION_ID_REQUIRED")
            result = apply_snapshot(tables, args.sha256, json.loads(args.private_target.read_text(encoding="utf-8-sig")), args.operation_id)
        if args.report:
            with args.report.open("x", encoding="utf-8") as file:
                json.dump(result, file, ensure_ascii=False, indent=2)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except MigrationError as exc:
        print(json.dumps({"status": "BLOCKED", "code": str(exc)}), file=sys.stderr)
        return 2
    except Exception:
        # Driver/constraint exceptions may include source emails/keys/SQL values.
        print('{"status":"FAILED","code":"PRIVATE_IMPORT_ERROR_NO_CUSTOMER_VALUES_LOGGED"}', file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

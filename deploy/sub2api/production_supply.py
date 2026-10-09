"""Operator-driven supply migration through Sub2API's authenticated API.

The private plan and receipt must stay outside Git. Stage copies only current
access tokens. Transfer is a separate operation after the old owner is stopped.
No customer balances or RealYu database rows are written by this tool.
"""
import argparse
import base64
import datetime as dt
import json
import os
from pathlib import Path
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(tmp, path)


class Client:
    def __init__(self, base, token, api_key=False):
        self.base = base.rstrip("/")
        target = urllib.parse.urlsplit(self.base)
        if (target.scheme != "http" or target.hostname != "127.0.0.1" or not target.port
                or target.username or target.password or target.path or target.query or target.fragment):
            raise RuntimeError("Migration requires the explicit local management endpoint")
        self.token = token
        self.api_key = api_key
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(self, path, payload=None, method=None, operation=None):
        headers = {"Content-Type": "application/json"}
        headers["x-api-key" if self.api_key else "Authorization"] = self.token if self.api_key else "Bearer " + self.token
        if payload is not None:
            headers["Idempotency-Key"] = operation or "realyu-migration-" + secrets.token_hex(16)
        request = urllib.request.Request(self.base + path, headers=headers, method=method,
            data=None if payload is None else json.dumps(payload).encode())
        try:
            with self.http.open(request, timeout=90) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise RuntimeError("Sub2API administration HTTP " + str(exc.code)) from None
        except (OSError, ValueError):
            raise RuntimeError("Sub2API administration outcome unknown; reconcile before retry") from None
        if result.get("code", 0) != 0:
            raise RuntimeError("Sub2API rejected the administrative operation")
        return result["data"]


def materials(database, channel_ids):
    with sqlite3.connect(Path(database).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        rows = list(db.execute("SELECT id,type,status,models,key FROM channels WHERE id IN (" +
                              ",".join("?" for _ in channel_ids) + ") ORDER BY id", channel_ids))
    if len(rows) != len(channel_ids) or any(row["type"] != 57 for row in rows):
        raise RuntimeError("Source channel inventory differs")
    result = []
    for row in rows:
        key = json.loads(row["key"])
        part = key["access_token"].split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        if claims["exp"] < dt.datetime.now().timestamp() + 1800:
            raise RuntimeError("Source access token expires too soon for migration")
        auth = claims.get("https://api.openai.com/auth", {})
        credentials = {"access_token": key["access_token"], "chatgpt_account_id": key["account_id"],
                       "expires_at": dt.datetime.fromtimestamp(claims["exp"], dt.timezone.utc).isoformat(),
                       "model_mapping": {name: name for name in row["models"].split(",")}}
        if auth.get("chatgpt_user_id"):
            credentials["chatgpt_user_id"] = auth["chatgpt_user_id"]
        result.append((row, key, credentials, claims["exp"]))
    return result


def verify_refresh_transfer(client, account_id, expected_credentials):
    current = client.call("/api/v1/admin/accounts/" + str(account_id))
    if current.get("id") != account_id or current.get("expires_at"):
        raise RuntimeError("Transferred renewable account metadata differs")
    # Ordinary account GET responses deliberately omit OAuth secrets. The
    # authenticated official export can read back exactly one selected account;
    # compare only in memory and never save/print the returned credentials.
    exported = client.call("/api/v1/admin/accounts/data?ids=" + str(account_id) + "&include_proxies=false")
    accounts = exported.get("accounts", [])
    if len(accounts) != 1:
        raise RuntimeError("Credential readback scope differs")
    account = accounts[0]
    credentials = account.get("credentials") or {}
    refresh = credentials.get("refresh_token")
    expected = expected_credentials.get("refresh_token")
    if (account.get("name") != current.get("name") or account.get("platform") != "openai"
            or account.get("type") != "oauth" or not credentials.get("access_token")
            or credentials.get("chatgpt_account_id") != expected_credentials.get("chatgpt_account_id")
            or not isinstance(refresh, str) or not isinstance(expected, str) or not expected
            or not secrets.compare_digest(refresh, expected)):
        raise RuntimeError("Transferred credential readback differs; reconcile before retry")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "transfer-refresh", "reconcile-transfer"])
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    admin = read(plan["admin_credentials"])
    token_field = plan.get("admin_token_field", "admin_access_token")
    client = Client(plan["base_url"], admin[token_field], api_key=token_field == "admin_api_key")
    if client.call("/api/v1/admin/compliance")["required"]:
        raise RuntimeError("Normal administrative declaration must be completed first")
    receipt_path = Path(plan["receipt"])
    receipt = read(receipt_path) if receipt_path.exists() else {"version": 1, "accounts": [], "phase": "new"}
    source = materials(plan["source_db"], plan["channel_ids"])
    if args.action == "stage":
        if receipt["accounts"] or receipt.get("pending") or receipt["phase"] != "new":
            raise RuntimeError("Existing supply receipt requires reconciliation, not another stage")
        receipt["phase"] = "staging"
        save(receipt_path, receipt)
        for row, key, credentials, expiry in source:
            operation = "realyu-supply-" + secrets.token_hex(16)
            receipt["pending"] = {"source_channel_id": row["id"], "operation": operation}
            save(receipt_path, receipt)
            account = client.call("/api/v1/admin/accounts", {
                "name": "realyu-channel-" + str(row["id"]), "platform": "openai", "type": "oauth",
                "credentials": credentials, "extra": {"openai_oauth_responses_websockets_v2_enabled": True,
                    "openai_oauth_responses_websockets_v2_mode": "ctx_pool"},
                "concurrency": plan.get("account_concurrency", 10), "priority": 1,
                "group_ids": [plan["group_id"]], "proxy_id": plan["proxy_id"],
                "expires_at": expiry, "auto_pause_on_expired": True,
                "upstream_billing_probe_enabled": False}, operation=operation)
            receipt["accounts"].append({"source_channel_id": row["id"], "sub2api_account_id": account["id"],
                "source_has_refresh": bool(key.get("refresh_token")), "refresh_transferred": False,
                "access_expiry": credentials["expires_at"]})
            receipt.pop("pending", None)
            save(receipt_path, receipt)
        receipt["phase"] = "access_only_ready"
    else:
        ownership = read(plan["ownership_receipt"])
        if ownership.get("old_refresh_owner_stopped") is not True or ownership.get("driver") != "sub2api":
            raise RuntimeError("Verified old-owner shutdown receipt is required")
        if receipt["phase"] not in ("access_only_ready", "refresh_transferred"):
            raise RuntimeError("Supply stage is incomplete")
        mappings = {item["source_channel_id"]: item for item in receipt["accounts"]}
        if args.action == "reconcile-transfer":
            pending = receipt.get("pending") or {}
            if pending.get("action") != "transfer-refresh":
                raise RuntimeError("No matching transfer intent to reconcile")
            selected = [(row, key, credentials) for row, key, credentials, _ in source
                        if row["id"] == pending.get("source_channel_id")]
            if len(selected) != 1 or not selected[0][1].get("refresh_token"):
                raise RuntimeError("Pending transfer source differs")
            row, key, credentials = selected[0]
            credentials["refresh_token"] = key["refresh_token"]
            item = mappings[row["id"]]
            verify_refresh_transfer(client, item["sub2api_account_id"], credentials)
            item["refresh_transferred"] = True
            receipt.setdefault("reconciled_intents", []).append({**pending,
                "verified_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                "verification": "official_single_account_export; no upstream mutation replayed"})
            receipt.pop("pending")
            receipt["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
            save(receipt_path, receipt)
            print(json.dumps({"phase": receipt["phase"], "reconciled_source_channel_id": row["id"],
                              "upstream_mutations_replayed": 0}))
            return
        if receipt.get("pending"):
            raise RuntimeError("Pending transfer must be reconciled before another write")
        for row, key, credentials, expiry in source:
            item = mappings[row["id"]]
            if item["refresh_transferred"] or not key.get("refresh_token"):
                continue
            credentials["refresh_token"] = key["refresh_token"]
            if key.get("id_token"):
                credentials["id_token"] = key["id_token"]
            operation = "realyu-transfer-" + secrets.token_hex(16)
            receipt["pending"] = {"source_channel_id": row["id"], "operation": operation,
                                  "action": "transfer-refresh"}
            save(receipt_path, receipt)
            client.call("/api/v1/admin/accounts/" + str(item["sub2api_account_id"]),
                        {"credentials": credentials, "expires_at": 0}, method="PUT", operation=operation)
            verify_refresh_transfer(client, item["sub2api_account_id"], credentials)
            item["refresh_transferred"] = True
            receipt.pop("pending", None)
            save(receipt_path, receipt)
        receipt["phase"] = "refresh_transferred"
    receipt["updated_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    save(receipt_path, receipt)
    print(json.dumps({"phase": receipt["phase"], "accounts": len(receipt["accounts"]),
                      "refresh_transferred": sum(x["refresh_transferred"] for x in receipt["accounts"])}))


if __name__ == "__main__":
    main()

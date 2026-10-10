"""Stage the reviewed former RealYu catalogue through native admin APIs.

Default is fully offline. This never enables payment, providers or registration,
never grants subscriptions, and never changes customer balances. Each plan is
created off sale and remains off sale after its managed metadata is configured.
An interrupted operation can resume from its exclusive private journal.
"""
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request


def require(condition, code):
    if not condition:
        raise ValueError(code)


def build_catalog(preview, group_id):
    require(type(group_id) is int and group_id > 0, "EXPLICIT_GROUP_REQUIRED")
    plans = []
    for row in sorted(preview["plans"], key=lambda r: (-int(r.get("sort_order", 0)), int(r["id"]))):
        require(type(row.get("enabled")) in (bool, int) and row["enabled"] in (0, 1), "INVALID_ENABLED_FLAG")
        if not row.get("enabled"):
            continue
        require(row.get("funding_scope") == "personal" and row.get("currency") == "CNY", "UNSUPPORTED_LEGACY_SCOPE_OR_CURRENCY")
        require(row.get("duration_unit") == "day" and row.get("duration_value") == 28, "UNSUPPORTED_LEGACY_TERM")
        require(not row.get("upgrade_group"), "EXPLICIT_GROUP_CHANGE_REVIEW_REQUIRED")
        require(row.get("quota_reset_period") == "never", "LEGACY_RESET_REQUIRES_REVIEW")
        for flag in ("allow_wallet_overflow", "allow_balance_pay"):
            require(type(row.get(flag)) in (bool, int) and row[flag] in (0, 1), "INVALID_PAYMENT_FLAG")
        total, weekly = row["total_amount"], row["weekly_amount"]
        require(type(total) is int and type(weekly) is int and 0 < weekly <= 2251799813685247 and total == weekly * 4, "ALLOWANCE_REQUIRES_REVIEW")
        cents = Decimal(str(row["price_amount"])) * 100
        require(cents.is_finite() and cents == cents.to_integral_value() and 0 < cents <= 1000000, "PRICE_REQUIRES_REVIEW")
        require(type(row["id"]) is int and row["id"] > 0 and isinstance(row["title"], str) and row["title"].strip(), "INVALID_PLAN_IDENTITY")
        native = {"group_id": group_id, "name": row["title"], "price": float(cents / 100), "currency": "CNY",
                  "validity_days": 28, "validity_unit": "day", "for_sale": False, "sort_order": len(plans),
                  "description": "", "features": "", "product_name": row["title"]}
        managed = {"source_plan_id": row["id"], "funding_scope": "personal", "total_amount_quota": total,
                   "weekly_amount_quota": weekly, "duration_seconds": 2419200, "price_cents": int(cents),
                   "allow_wallet_overflow": bool(row["allow_wallet_overflow"]), "allow_balance_pay": bool(row["allow_balance_pay"])}
        plans.append({"native": native, "managed": managed})
    require(plans and len({p["managed"]["source_plan_id"] for p in plans}) == len(plans), "EMPTY_OR_DUPLICATE_CATALOGUE")
    return plans


def matches_native(actual, expected):
    return all(actual.get(k) == v for k, v in expected.items())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", required=True, help="Reviewed non-secret legacy catalogue preview JSON")
    parser.add_argument("--catalog-sha256", required=True)
    parser.add_argument("--group-id", type=int, required=True)
    parser.add_argument("--base-url", default="https://api.realyu.fun")
    parser.add_argument("--expected-version")
    parser.add_argument("--credentials")
    parser.add_argument("--journal")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    content = Path(args.catalog).read_bytes()
    require(hashlib.sha256(content).hexdigest() == args.catalog_sha256, "CATALOGUE_CHANGED")
    preview = json.loads(content)
    plans = build_catalog(preview, args.group_id)
    if not args.execute:
        print(json.dumps({"status": "PREPARED_OFFLINE", "production_changed": False, "plans": plans}, ensure_ascii=False))
        return
    require(args.base_url in ("https://api.realyu.fun", "http://127.0.0.1:29481"), "TARGET_NOT_REVIEWED")
    require(args.expected_version and args.credentials and args.journal, "EXECUTION_ARGUMENTS_REQUIRED")
    if args.base_url == "https://api.realyu.fun":
        require(preview.get("reviewed_group_mapping", {}).get("default") == args.group_id, "SOURCE_GROUP_MAPPING_MISMATCH")
    journal_file = Path(args.journal)
    identity = {"target": args.base_url, "version": args.expected_version, "catalogue_sha256": args.catalog_sha256, "group_id": args.group_id}
    if journal_file.exists():
        journal = json.loads(journal_file.read_text(encoding="utf-8"))
        require(journal["identity"] == identity, "OTHER_OPERATION_JOURNAL")
    else:
        journal = {"identity": identity, "plans": {}, "status": "PREPARED", "first_failure": None}
        journal_file.parent.mkdir(parents=True, exist_ok=True)
        with journal_file.open("x", encoding="utf-8") as out:
            json.dump(journal, out, ensure_ascii=False, indent=2)
    def save():
        temp = journal_file.with_suffix(journal_file.suffix + ".tmp")
        temp.write_text(json.dumps(journal, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(journal_file)
    http = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    token = None
    def call(method, route, value=None):
        headers = {"Accept": "application/json", "Cache-Control": "no-store",
                   "User-Agent": "RealYu-Maintenance/1.0"}
        if token:
            headers["Authorization"] = "Bearer " + token
        body = None
        if value is not None:
            body = json.dumps(value).encode()
            headers["Content-Type"] = "application/json"
        try:
            with http.open(urllib.request.Request(args.base_url + route, data=body, headers=headers, method=method), timeout=25) as response:
                result = json.load(response)
        except urllib.error.HTTPError as exc:
            raise ValueError("HTTP_" + str(exc.code)) from None
        if route == "/api/status":
            require(result.get("success") is True, "STATUS_ENVELOPE")
        else:
            require(result.get("code") == 0 and "data" in result, "API_ENVELOPE")
        return result["data"]
    try:
        status = call("GET", "/api/status")
        require(status.get("single_core") is True and status.get("engine") == "sub2api" and status.get("version") == args.expected_version, "WRONG_LIVE_VERSION")
        private = json.loads(Path(args.credentials).read_text(encoding="utf-8-sig"))
        token = call("POST", "/api/v1/auth/login", {"email": private["admin_username"], "password": private["admin_password"]})["access_token"]
        public = call("GET", "/api/v1/settings/public")
        require(public.get("customer_currency") == "CNY" and public.get("customer_usd_to_cny") == "7", "UNREVIEWED_EXCHANGE_RATE")
        before = call("GET", "/api/v1/admin/payment/plans")
        for plan in plans:
            native, managed = plan["native"], plan["managed"]
            source = str(managed["source_plan_id"])
            matches = [x for x in before if (x.get("managed_entitlement") or {}).get("source_plan_id") == managed["source_plan_id"]]
            saved = journal["plans"].get(source)
            require(len(matches) <= 1, "DUPLICATE_SOURCE_PLAN")
            if not saved and matches:
                saved = {"native_id": matches[0]["id"], "phase": "FOUND_EXISTING"}
                journal["plans"][source] = saved
            if not saved:
                # Unknown prior POST outcome must be reconciled, never retried
                # into another plan merely because its metadata is still absent.
                require(not any(x.get("name") == native["name"] and x.get("group_id") == args.group_id for x in before), "UNMAPPED_PLAN_REQUIRES_RECONCILIATION")
                created = call("POST", "/api/v1/admin/payment/plans", native)
                saved = {"native_id": created["id"], "phase": "CREATED_OFF_SALE"}
                journal["plans"][source] = saved
                save()
            current = next((x for x in call("GET", "/api/v1/admin/payment/plans") if x["id"] == saved["native_id"]), None)
            require(current is not None and matches_native(current, native), "PLAN_CHANGED_REQUIRES_REVIEW")
            if current.get("managed_entitlement"):
                require(all(current["managed_entitlement"].get(k) == v for k, v in managed.items()), "ENTITLEMENT_CHANGED_REQUIRES_REVIEW")
            else:
                call("PUT", "/api/v1/admin/payment/plans/" + str(saved["native_id"]) + "/managed-entitlement", managed)
            verified = next(x for x in call("GET", "/api/v1/admin/payment/plans") if x["id"] == saved["native_id"])
            require(matches_native(verified, native) and all((verified.get("managed_entitlement") or {}).get(k) == v for k, v in managed.items()), "VERIFY_STAGED_PLAN_FAILED")
            saved["phase"] = "VERIFIED_OFF_SALE"
            save()
        journal["status"] = "STAGED_OFF_SALE_PAYMENT_UNCHANGED"
    except Exception as exc:
        if journal["first_failure"] is None:
            journal["first_failure"] = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        journal["status"] = "FAILED_REVIEW_JOURNAL"
        raise
    finally:
        save()
        print(json.dumps({"status": journal["status"], "plans": journal["plans"], "first_failure": journal["first_failure"]}))


if __name__ == "__main__":
    main()

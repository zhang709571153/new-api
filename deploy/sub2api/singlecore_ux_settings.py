"""Open reviewed username signup and personal wallet purchases via admin APIs.

Default is entirely offline: no credentials read and no network requests.
Execution never creates users, keys, orders, grants, or merchant providers.
There is no retry or automatic rollback after a possibly successful write.
"""
import argparse
import copy
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

from restore_native_catalog import build_catalog


VERSION = "realyu-singlecore-v0.2.15-20261010-ux"
CATALOG_SHA256 = "b2e27cb66d51b662406f518206d7eeff1c4b9cda419163f8cc93147432674b68"
TARGETS = ("https://api.realyu.fun", "http://127.0.0.1:29481")
SETTINGS_ROUTE = "/api/v1/admin/settings"
PLANS_ROUTE = "/api/v1/admin/payment/plans"
PROVIDERS_ROUTE = "/api/v1/admin/payment/providers"
OIDC_FIELDS = ("oidc_connect_use_pkce", "oidc_connect_validate_id_token")
SETTINGS_PATCH = {
    "registration_enabled": True,
    "realyu_username_registration_enabled": True,
    "realyu_signup_personal_group_id": 2,
    "payment_enabled": True,
    "payment_balance_disabled": True,
    "payment_enabled_types": [],
    "payment_visible_method_alipay_enabled": False,
    "payment_visible_method_wxpay_enabled": False,
    "subscription_enabled": True,
    "model_plaza_enabled": True,
}


class GuardError(Exception):
    """An allowlisted error code, never a raw HTTP response or credential."""


def require(condition, code):
    if not condition:
        raise GuardError(code)


def value_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def changed_keys(before, after):
    return sorted(k for k in before.keys() | after.keys()
                  if k not in before or k not in after or before[k] != after[k])


def settings_patch_matches(actual, expected):
    for key, value in expected.items():
        if key not in actual:
            return False
        if key == "payment_enabled_types" and value == []:
            # Native PUT [] stores ""; GetPaymentConfig leaves EnabledTypes a
            # nil slice for that value, serialized as present JSON null. Only
            # this field has the reviewed null/empty semantic equivalence.
            if actual[key] is not None and actual[key] != []:
                return False
        elif actual[key] != value:
            return False
    return True


def load_reviewed_catalog(path):
    raw = Path(path).read_bytes()
    require(hashlib.sha256(raw).hexdigest() == CATALOG_SHA256, "CATALOGUE_HASH_NOT_REVIEWED")
    preview = json.loads(raw)
    require(preview.get("reviewed_group_mapping", {}).get("default") == 2, "GROUP_MAPPING_CHANGED")
    plans = build_catalog(preview, 2)
    require([p["managed"]["source_plan_id"] for p in plans] == list(range(2, 8)), "SOURCE_PLAN_SET_CHANGED")
    require([p["managed"]["price_cents"] for p in plans] == [9800, 19800, 39800, 69800, 139800, 259800], "CATALOGUE_PRICE_CHANGED")
    return plans


def validate_version(status):
    require(isinstance(status, dict) and status.get("version") == VERSION
            and status.get("engine") == "sub2api" and status.get("single_core") is True,
            "LIVE_VERSION_NOT_REVIEWED")


def validate_settings(settings):
    require(isinstance(settings, dict), "INVALID_SETTINGS_RESPONSE")
    require(settings.get("oidc_connect_enabled") is False, "OIDC_ENABLED_REQUIRES_REVIEW")
    # The reviewed production state is explicitly false/false. Native Sub2API's
    # disabled-OIDC PUT ignores the pointers and uses its stored write defaults.
    # Do not try to enable OIDC to change those values or silently normalize an
    # unconfigured instance that still reports the different GET defaults.
    require(all(settings.get(k) is False for k in OIDC_FIELDS), "OIDC_DEFAULTS_NOT_RECONCILED")
    require(settings.get("email_verify_enabled") is False
            and settings.get("invitation_code_enabled") is False,
            "SIGNUP_REQUIREMENTS_CHANGED")


def validate_group(group):
    require(isinstance(group, dict) and type(group.get("id")) is int
            and group["id"] == 2 and group.get("name") == "RealYu Production OpenAI"
            and group.get("platform") == "openai" and group.get("status") == "active"
            and group.get("subscription_type") == "standard"
            and group.get("is_exclusive") is True
            and Decimal(str(group.get("rate_multiplier"))) == Decimal("0.125"),
            "PERSONAL_GROUP_NOT_REVIEWED")


def validate_catalog(actual, reviewed):
    require(isinstance(actual, list) and len(actual) == 6, "EXACT_SIX_PLANS_REQUIRED")
    result = {}
    native_ids = set()
    for item in actual:
        require(isinstance(item, dict) and type(item.get("id")) is int and item["id"] > 0,
                "INVALID_NATIVE_PLAN_ID")
        require(item["id"] not in native_ids, "DUPLICATE_NATIVE_PLAN_ID")
        native_ids.add(item["id"])
        managed = item.get("managed_entitlement")
        require(isinstance(managed, dict) and type(managed.get("source_plan_id")) is int,
                "MANAGED_SOURCE_ID_REQUIRED")
        source_id = managed["source_plan_id"]
        require(source_id not in result, "DUPLICATE_SOURCE_PLAN")
        result[source_id] = item
    require(set(result) == set(range(2, 8)), "SOURCE_PLAN_SET_CHANGED")
    for expected in reviewed:
        native, managed = expected["native"], expected["managed"]
        item = result[managed["source_plan_id"]]
        require(all(item.get(k) == v for k, v in native.items() if k != "for_sale"),
                "NATIVE_CATALOGUE_MISMATCH")
        require(type(item.get("for_sale")) is bool, "PLAN_SALE_STATE_INVALID")
        require(all(item["managed_entitlement"].get(k) == v for k, v in managed.items()),
                "MANAGED_CATALOGUE_MISMATCH")
        wallet_quota = int((Decimal(managed["price_cents"]) * 5000 / 7).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        require(type(item["managed_entitlement"].get("wallet_price_quota")) is int
                and item["managed_entitlement"]["wallet_price_quota"] == wallet_quota,
                "WALLET_PRICE_MISMATCH")
        expected_caps = {"purchase": item["for_sale"], "wallet_payment": item["for_sale"],
                         "renew": False, "upgrade": False, "refund": False}
        require(item["managed_entitlement"].get("capabilities") == expected_caps,
                "MANAGED_CAPABILITIES_MISMATCH")
    return result


class Evidence:
    def __init__(self, directory):
        self.directory = Path(directory)
        # Exclusive output protects the first failure from a subsequent run.
        self.directory.mkdir(parents=True, exist_ok=False)
        self.state = {"version": 1, "status": "PREPARED", "expected_version": VERSION,
                      "catalogue_sha256": CATALOG_SHA256, "writes_attempted": [],
                      "plans_published": [], "first_failure": None}
        self.save()

    def save(self):
        temporary = self.directory / "receipt.tmp"
        temporary.write_text(json.dumps(self.state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(self.directory / "receipt.json")

    def event(self, event):
        with (self.directory / "http.jsonl").open("a", encoding="utf-8") as output:
            output.write(json.dumps(event, ensure_ascii=False) + "\n")

    def failure(self, code):
        if self.state["first_failure"] is None:
            self.state["first_failure"] = code
            with (self.directory / "first-failure.json").open("x", encoding="utf-8") as output:
                json.dump({"code": code, "writes_attempted": self.state["writes_attempted"]}, output)
        self.state["status"] = "FAILED_REVIEW_RECEIPT_NO_AUTOMATIC_ROLLBACK"
        self.save()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class AdminAPI:
    def __init__(self, base_url, evidence):
        require(base_url in TARGETS, "TARGET_NOT_REVIEWED")
        self.base_url, self.evidence = base_url, evidence
        self.token = None
        self.refresh_token = None
        self.deadline = time.monotonic() + 180
        self.calls = 0
        # TLS verification stays at the Python default. Never inherit a proxy,
        # follow a redirect with credentials, retry, or save response bodies.
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, method, route, payload=None):
        self.calls += 1
        remaining = self.deadline - time.monotonic()
        require(remaining > 0 and self.calls <= 80, "BOUNDED_RUN_EXHAUSTED")
        headers = {"Accept": "application/json", "Cache-Control": "no-store",
                   "User-Agent": "RealYu-Maintenance/1.0"}
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        body = None
        if payload is not None:
            body = json.dumps(payload, allow_nan=False).encode()
            headers["Content-Type"] = "application/json"
        event = {"method": method, "route": route, "status": None}
        started = time.monotonic()
        try:
            request = urllib.request.Request(self.base_url + route, data=body, headers=headers, method=method)
            with self.http.open(request, timeout=min(15, remaining)) as response:
                event["status"] = response.status
                event["cf_ray"] = response.headers.get("CF-Ray")
                event["request_id"] = response.headers.get("X-Request-ID")
                raw = response.read(4 * 1024 * 1024 + 1)
                require(len(raw) <= 4 * 1024 * 1024, "RESPONSE_TOO_LARGE")
                result = json.loads(raw)
            require(isinstance(result, dict), "INVALID_API_RESPONSE")
            if route == "/api/status":
                require(result.get("success") is True, "INVALID_STATUS_ENVELOPE")
            else:
                require(type(result.get("code")) is int and result["code"] == 0, "INVALID_API_ENVELOPE")
            require("data" in result, "MISSING_API_DATA")
            return result["data"]
        except urllib.error.HTTPError as exc:
            event["status"] = exc.code
            event["cf_ray"] = exc.headers.get("CF-Ray") if exc.headers else None
            event["request_id"] = exc.headers.get("X-Request-ID") if exc.headers else None
            raise GuardError("HTTP_" + str(exc.code)) from None
        finally:
            event["elapsed_seconds"] = round(time.monotonic() - started, 3)
            self.evidence.event(event)

    def login(self, path):
        private = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        require(isinstance(private.get("admin_username"), str) and bool(private["admin_username"])
                and isinstance(private.get("admin_password"), str) and bool(private["admin_password"]),
                "PRIVATE_ADMIN_CREDENTIALS_INVALID")
        result = self.call("POST", "/api/v1/auth/login", {
            "email": private["admin_username"], "password": private["admin_password"]})
        require(isinstance(result, dict) and isinstance(result.get("access_token"), str)
                and bool(result["access_token"]), "ADMIN_LOGIN_INCOMPLETE")
        self.token = result["access_token"]
        self.refresh_token = result.get("refresh_token")

    def logout(self):
        if self.refresh_token:
            self.call("POST", "/api/v1/auth/logout", {"refresh_token": self.refresh_token})
        self.token = self.refresh_token = None


def inspect_live(api, reviewed):
    validate_version(api.call("GET", "/api/status"))
    public = api.call("GET", "/api/v1/settings/public")
    require(public.get("customer_currency") == "CNY" and public.get("customer_usd_to_cny") == "7",
            "EXCHANGE_RATE_NOT_REVIEWED")
    settings = api.call("GET", SETTINGS_ROUTE)
    validate_settings(settings)
    validate_group(api.call("GET", "/api/v1/admin/groups/2"))
    providers = api.call("GET", PROVIDERS_ROUTE)
    require(isinstance(providers, list) and len(providers) == 0, "ZERO_MERCHANT_INSTANCES_REQUIRED")
    catalog = api.call("GET", PLANS_ROUTE)
    validate_catalog(catalog, reviewed)
    return settings, catalog


def apply_reviewed_settings(api, reviewed, evidence, publish=False):
    before, initial_catalog = inspect_live(api, reviewed)
    patch = copy.deepcopy(SETTINGS_PATCH)
    patch.update({k: before[k] for k in OIDC_FIELDS})
    evidence.state.update({"status": "PREFLIGHT_PASSED", "settings_before_sha256": value_digest(before),
                           "safe_settings_before": {k: before.get(k) for k in patch},
                           "settings_patch": patch, "merchant_instances": 0,
                           "native_plan_ids": sorted(p["id"] for p in initial_catalog),
                           "oidc_enabled": False,
                           "oidc_note": "Disabled OIDC has stored false/false; different unconfigured GET defaults are not modified here."})
    evidence.save()
    # Detect concurrent administrative changes before opening registration.
    current, current_catalog = inspect_live(api, reviewed)
    require(current == before and current_catalog == initial_catalog, "PREFLIGHT_CHANGED_REVIEW_REQUIRED")
    if not settings_patch_matches(before, patch):
        evidence.state["writes_attempted"].append({"method": "PUT", "route": SETTINGS_ROUTE})
        evidence.save()
        api.call("PUT", SETTINGS_ROUTE, patch)
    after, current_catalog = inspect_live(api, reviewed)
    changed = changed_keys(before, after)
    evidence.state["settings_changed_keys"] = changed
    evidence.state["settings_after_sha256"] = value_digest(after)
    evidence.save()
    require(set(changed) <= set(SETTINGS_PATCH), "UNEXPECTED_SETTINGS_CHANGE")
    require(settings_patch_matches(after, patch), "SETTINGS_READBACK_MISMATCH")
    require(current_catalog == initial_catalog, "CATALOGUE_CHANGED_DURING_SETTINGS_WRITE")
    public = api.call("GET", "/api/v1/settings/public")
    require(public.get("registration_enabled") is True and public.get("username_registration_enabled") is True
            and public.get("payment_enabled") is True and public.get("subscription_enabled") is True
            and public.get("model_plaza_enabled") is True,
            "PUBLIC_SETTINGS_READBACK_MISMATCH")
    plaza = api.call("GET", "/api/v1/model-plaza")
    require(isinstance(plaza, dict) and isinstance(plaza.get("groups"), list)
            and all(isinstance(group, dict) and isinstance(group.get("models"), list)
                    for group in plaza["groups"]), "MODEL_PLAZA_RESPONSE_INVALID")
    # The authenticated caller may see a different subset from another user.
    # Keep existing group visibility; no channel/pricing/group write is made.
    evidence.state["model_plaza_visible_group_count"] = len(plaza["groups"])
    evidence.state["model_plaza_visible_model_count"] = sum(len(group["models"]) for group in plaza["groups"])
    evidence.save()
    if publish:
        for source_id in range(2, 8):
            # Zero merchants, fixed group/version, and unchanged complete
            # settings are rechecked before each narrowly scoped plan write.
            live_settings, live_catalog = inspect_live(api, reviewed)
            require(live_settings == after and live_catalog == current_catalog, "STATE_CHANGED_BEFORE_PUBLISH")
            plan = validate_catalog(live_catalog, reviewed)[source_id]
            if plan["for_sale"]:
                continue
            route = PLANS_ROUTE + "/" + str(plan["id"])
            evidence.state["writes_attempted"].append({"method": "PUT", "route": route})
            evidence.save()
            api.call("PUT", route, {"for_sale": True})
            expected_catalog = copy.deepcopy(current_catalog)
            updated = next(p for p in expected_catalog if p["id"] == plan["id"])
            updated["for_sale"] = True
            updated["managed_entitlement"]["capabilities"]["purchase"] = True
            updated["managed_entitlement"]["capabilities"]["wallet_payment"] = True
            current_catalog = api.call("GET", PLANS_ROUTE)
            validate_catalog(current_catalog, reviewed)
            # Only the published plan's updated_at timestamp may also change.
            actual_by_id = {p["id"]: p for p in current_catalog}
            for expected_plan in expected_catalog:
                actual_plan = actual_by_id[expected_plan["id"]]
                if expected_plan["id"] == plan["id"]:
                    expected_plan["updated_at"] = actual_plan.get("updated_at")
                    if "updated_at" not in actual_plan:
                        expected_plan.pop("updated_at", None)
                require(expected_plan == actual_plan, "UNEXPECTED_PLAN_CHANGE")
            evidence.state["plans_published"].append({"source_plan_id": source_id, "native_plan_id": plan["id"]})
            evidence.save()
    final_settings, final_catalog = inspect_live(api, reviewed)
    require(final_settings == after and final_catalog == current_catalog, "FINAL_STATE_CHANGED")
    if publish:
        require(all(p["for_sale"] for p in final_catalog), "PUBLICATION_INCOMPLETE")
    evidence.state["status"] = "VERIFIED_SETTINGS_AND_PERSONAL_CATALOGUE" if publish else "VERIFIED_SETTINGS_CATALOGUE_UNCHANGED"
    evidence.state["for_sale_count"] = sum(p["for_sale"] for p in final_catalog)
    evidence.save()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=str(Path(__file__).with_name("legacy-catalog-20261010.json")))
    parser.add_argument("--base-url", choices=TARGETS, default=TARGETS[0])
    parser.add_argument("--credentials", help="Private JSON containing admin_username/admin_password")
    parser.add_argument("--output-dir", help="A new private evidence directory, outside Git")
    parser.add_argument("--publish-personal-plans", action="store_true")
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    evidence = api = None
    try:
        reviewed = load_reviewed_catalog(args.catalog)
        if not args.execute:
            print(json.dumps({"status": "PREPARED_OFFLINE", "network_requests": 0, "production_changed": False,
                              "expected_version": VERSION, "settings_patch": SETTINGS_PATCH,
                              "oidc_security_fields": "preserve stored false/false; require OIDC disabled",
                              "source_plan_ids": list(range(2, 8)), "publish_personal_plans": args.publish_personal_plans}))
            return 0
        require(bool(args.credentials) and bool(args.output_dir), "EXECUTION_ARGUMENTS_REQUIRED")
        output = Path(args.output_dir).resolve()
        repository = Path(__file__).resolve().parents[2]
        require(not output.is_relative_to(repository), "EVIDENCE_MUST_BE_OUTSIDE_GIT")
        evidence = Evidence(output)
        evidence.state["target"] = args.base_url
        api = AdminAPI(args.base_url, evidence)
        # Verify target before reading the credential file or attempting login.
        validate_version(api.call("GET", "/api/status"))
        api.login(args.credentials)
        apply_reviewed_settings(api, reviewed, evidence, args.publish_personal_plans)
    except Exception as exc:
        code = str(exc) if isinstance(exc, GuardError) else type(exc).__name__
        if evidence:
            evidence.failure(code)
        print(json.dumps({"status": "FAILED", "code": code,
                          "writes_attempted": len(evidence.state["writes_attempted"]) if evidence else 0}))
        return 1
    finally:
        if api and api.refresh_token:
            try:
                api.logout()
            except Exception as exc:
                # Do not overwrite the first operational failure or claim the
                # temporary refresh session was revoked when it was not.
                evidence.state["logout_warning"] = str(exc) if isinstance(exc, GuardError) else type(exc).__name__
                evidence.save()
    print(json.dumps({"status": evidence.state["status"], "writes_attempted": len(evidence.state["writes_attempted"]),
                      "for_sale_count": evidence.state["for_sale_count"],
                      "logout_warning": evidence.state.get("logout_warning")}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

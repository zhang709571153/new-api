"""Offline operational-contract tests. No live API, database or credentials."""
import contextlib
import copy
from decimal import Decimal, ROUND_HALF_UP
from email.message import Message
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import singlecore_ux_settings as ux


class FakeAPI:
    def __init__(self):
        self.reviewed = ux.load_reviewed_catalog(Path(__file__).with_name("legacy-catalog-20261010.json"))
        self.status = {"version": ux.VERSION, "engine": "sub2api", "single_core": True}
        self.settings = {"registration_enabled": False, "realyu_username_registration_enabled": False,
                         "realyu_signup_personal_group_id": 0, "payment_enabled": False,
                         "payment_balance_disabled": False, "payment_enabled_types": None,
                         "payment_visible_method_alipay_enabled": False,
                         "payment_visible_method_wxpay_enabled": False, "subscription_enabled": True,
                         "model_plaza_enabled": False, "model_plaza_require_auth": False,
                         "oidc_connect_enabled": False, "oidc_connect_use_pkce": False,
                         "oidc_connect_validate_id_token": False,
                         "email_verify_enabled": False, "invitation_code_enabled": False,
                         "turnstile_enabled": True, "site_name": "synthetic-brand",
                         "smtp_password_configured": True, "default_balance": 0,
                         "default_subscriptions": []}
        self.group = {"id": 2, "name": "RealYu Production OpenAI", "platform": "openai", "status": "active",
                      "subscription_type": "standard", "is_exclusive": True, "rate_multiplier": 0.125}
        self.providers = []
        self.catalog = []
        for index, spec in enumerate(self.reviewed, 1):
            plan = copy.deepcopy(spec["native"])
            plan.update({"id": index, "created_at": "created", "updated_at": "before"})
            managed = copy.deepcopy(spec["managed"])
            managed["wallet_price_quota"] = int((Decimal(managed["price_cents"]) * 5000 / 7).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            managed["capabilities"] = {"purchase": False, "wallet_payment": False, "renew": False, "upgrade": False, "refund": False}
            plan["managed_entitlement"] = managed
            self.catalog.append(plan)
        self.calls = []
        self.extra_setting_change = None
        self.extra_plan_change = False
        self.fail_route = None
        self.provider_after_settings = False
        self.providers_reads = 0
        self.concurrent_provider_at_read = None

    def call(self, method, route, payload=None):
        self.calls.append((method, route, copy.deepcopy(payload)))
        if route == "/api/status":
            return copy.deepcopy(self.status)
        if route == "/api/v1/settings/public":
            return {"customer_currency": "CNY", "customer_usd_to_cny": "7",
                    "registration_enabled": self.settings["registration_enabled"],
                    "username_registration_enabled": self.settings["realyu_username_registration_enabled"],
                    "payment_enabled": self.settings["payment_enabled"],
                    "subscription_enabled": self.settings["subscription_enabled"],
                    "model_plaza_enabled": self.settings["model_plaza_enabled"]}
        if route == "/api/v1/model-plaza":
            return {"description": "", "groups": [{"id": 2, "models": [{"name": "synthetic-model"}]}]}
        if route == "/api/v1/admin/groups/2":
            return copy.deepcopy(self.group)
        if route == ux.PROVIDERS_ROUTE:
            self.providers_reads += 1
            if self.providers_reads == self.concurrent_provider_at_read:
                self.providers.append({"id": 9, "enabled": False, "private_config": "DO_NOT_RECORD"})
            return copy.deepcopy(self.providers)
        if route == ux.SETTINGS_ROUTE:
            if method == "PUT":
                self.settings.update(copy.deepcopy(payload))
                # Native empty ENABLED_PAYMENT_TYPES parses into a nil []string
                # and its non-omitempty response field is serialized as null.
                if payload.get("payment_enabled_types") == []:
                    self.settings["payment_enabled_types"] = None
                if self.extra_setting_change:
                    self.settings.update(self.extra_setting_change)
                if self.provider_after_settings:
                    self.providers = [{"id": 9, "enabled": True}]
            return copy.deepcopy(self.settings)
        if route == ux.PLANS_ROUTE:
            return copy.deepcopy(self.catalog)
        if method == "PUT" and route.startswith(ux.PLANS_ROUTE + "/"):
            if route == self.fail_route:
                raise ux.GuardError("HTTP_502")
            plan = next(p for p in self.catalog if p["id"] == int(route.rsplit("/", 1)[1]))
            plan["for_sale"] = payload["for_sale"]
            plan["updated_at"] = "after"
            plan["managed_entitlement"]["capabilities"].update(purchase=True, wallet_payment=True)
            if self.extra_plan_change:
                plan["unexpected_metadata"] = "changed"
            return copy.deepcopy(plan)
        raise AssertionError("unreviewed API request")

    def writes(self):
        return [c for c in self.calls if c[0] == "PUT"]


class UXSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.api = FakeAPI()
        self.evidence = ux.Evidence(Path(self.temporary.name) / "new-run")

    def test_default_offline_never_reads_credentials_or_constructs_http_client(self):
        out = io.StringIO()
        with patch.object(ux, "AdminAPI", side_effect=AssertionError("network forbidden")), contextlib.redirect_stdout(out):
            result = ux.main(["--credentials", "does-not-exist-private.json", "--publish-personal-plans"])
        self.assertEqual(result, 0)
        self.assertEqual(json.loads(out.getvalue())["network_requests"], 0)

    def test_settings_only_preserves_security_brand_antispam_and_financial_defaults(self):
        original = copy.deepcopy(self.api.settings)
        original_catalog = copy.deepcopy(self.api.catalog)
        ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence)
        self.assertEqual(len(self.api.writes()), 1)
        payload = self.api.writes()[0][2]
        self.assertEqual(set(payload), set(ux.SETTINGS_PATCH) | set(ux.OIDC_FIELDS))
        for field in ux.OIDC_FIELDS:
            self.assertIs(payload[field], False)
        for field in ("site_name", "turnstile_enabled", "default_balance", "default_subscriptions", "oidc_connect_enabled", "model_plaza_require_auth"):
            self.assertEqual(self.api.settings[field], original[field])
        self.assertIs(self.api.settings["model_plaza_enabled"], True)
        self.assertEqual(self.evidence.state["model_plaza_visible_model_count"], 1)
        self.assertTrue(self.api.group["is_exclusive"])
        self.assertEqual(self.api.catalog, original_catalog)
        self.assertEqual(self.evidence.state["status"], "VERIFIED_SETTINGS_CATALOGUE_UNCHANGED")

    def test_publication_uses_only_for_sale_and_validates_capabilities(self):
        ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.assertEqual(len(self.api.writes()), 7)
        self.assertEqual([p["source_plan_id"] for p in self.evidence.state["plans_published"]], list(range(2, 8)))
        self.assertTrue(all(p["for_sale"] for p in self.api.catalog))
        self.assertTrue(all(c[2] == {"for_sale": True} for c in self.api.writes()[1:]))
        self.assertTrue(all("providers" not in c[1] for c in self.api.writes()))
        # A second explicit run is idempotent and makes no repeated writes.
        self.api.calls.clear()
        ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.assertEqual(self.api.writes(), [])

    def test_resume_after_native_null_readback_publishes_without_settings_put(self):
        self.api.settings.update(copy.deepcopy(ux.SETTINGS_PATCH))
        self.api.settings["payment_enabled_types"] = None
        ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.assertEqual(len(self.api.writes()), 6)
        self.assertTrue(all(call[1].startswith(ux.PLANS_ROUTE + "/") for call in self.api.writes()))
        self.assertIsNone(self.api.settings["payment_enabled_types"])

    def test_enabled_types_semantics_require_present_null_or_empty_list(self):
        expected = {"payment_enabled_types": []}
        self.assertTrue(ux.settings_patch_matches({"payment_enabled_types": None}, expected))
        self.assertTrue(ux.settings_patch_matches({"payment_enabled_types": []}, expected))
        for value in (False, "", {}, ["wallet"], ["alipay"], [None]):
            with self.subTest(value=value):
                self.assertFalse(ux.settings_patch_matches({"payment_enabled_types": value}, expected))
        self.assertFalse(ux.settings_patch_matches({}, expected))
        self.assertFalse(ux.settings_patch_matches({"other": None}, {"other": []}))

    def test_nonempty_enabled_types_readback_stops_before_plan_publication(self):
        self.api.extra_setting_change = {"payment_enabled_types": ["alipay"]}
        with self.assertRaisesRegex(ux.GuardError, "SETTINGS_READBACK_MISMATCH"):
            ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.assertEqual(len(self.api.writes()), 1)
        self.assertFalse(any(p["for_sale"] for p in self.api.catalog))

    def test_version_group_and_any_provider_mismatch_block_all_writes(self):
        mutations = [
            lambda a: a.status.update(version="old"),
            lambda a: a.group.update(subscription_type="subscription"),
            lambda a: a.group.update(status="disabled"),
            lambda a: a.group.update(rate_multiplier=1),
            lambda a: a.providers.append({"id": 1, "enabled": False}),
            lambda a: a.providers.append({"id": 1, "enabled": True}),
            lambda a: a.settings.update(oidc_connect_enabled=True),
            lambda a: a.settings.update(oidc_connect_use_pkce=True),
            lambda a: a.settings.update(email_verify_enabled=True),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                api = FakeAPI()
                mutate(api)
                with self.assertRaises(ux.GuardError):
                    ux.apply_reviewed_settings(api, api.reviewed, self.evidence, publish=True)
                self.assertEqual(api.writes(), [])

    def test_exact_catalogue_and_wallet_price_must_match_before_any_write(self):
        mutations = [
            lambda p: p[0].update(currency="USD"),
            lambda p: p[0].update(price=99),
            lambda p: p[0].update(validity_days=30),
            lambda p: p[0]["managed_entitlement"].update(source_plan_id=3),
            lambda p: p[0]["managed_entitlement"].update(funding_scope="team"),
            lambda p: p[0]["managed_entitlement"].update(weekly_amount_quota=123),
            lambda p: p[0]["managed_entitlement"].update(allow_balance_pay=False),
            lambda p: p[0]["managed_entitlement"].update(wallet_price_quota=7000001),
            lambda p: p[0]["managed_entitlement"]["capabilities"].update(refund=True),
            lambda p: p.pop(),
        ]
        for mutate in mutations:
            with self.subTest(mutation=mutations.index(mutate)):
                api = FakeAPI()
                mutate(api.catalog)
                with self.assertRaises(ux.GuardError):
                    ux.apply_reviewed_settings(api, api.reviewed, self.evidence, publish=True)
                self.assertEqual(api.writes(), [])

    def test_concurrent_merchant_creation_blocks_opening_settings(self):
        self.api.concurrent_provider_at_read = 2
        with self.assertRaisesRegex(ux.GuardError, "ZERO_MERCHANT_INSTANCES_REQUIRED"):
            ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.assertEqual(self.api.writes(), [])

    def test_settings_side_effect_is_detected_and_stops_before_publication(self):
        self.api.extra_setting_change = {"site_name": "unrequested-brand"}
        with self.assertRaisesRegex(ux.GuardError, "UNEXPECTED_SETTINGS_CHANGE"):
            ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.assertEqual(len(self.api.writes()), 1)
        self.assertIn("site_name", self.evidence.state["settings_changed_keys"])
        self.assertFalse(any(p["for_sale"] for p in self.api.catalog))

    def test_settings_readback_catches_merchant_added_after_open_without_rollback(self):
        self.api.provider_after_settings = True
        with self.assertRaisesRegex(ux.GuardError, "ZERO_MERCHANT_INSTANCES_REQUIRED"):
            ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.assertEqual(len(self.api.writes()), 1)
        self.assertTrue(self.api.settings["registration_enabled"])
        self.assertFalse(any(p["for_sale"] for p in self.api.catalog))

    def test_partial_plan_failure_never_retries_or_hides_first_failure(self):
        self.api.fail_route = ux.PLANS_ROUTE + "/3"
        with self.assertRaisesRegex(ux.GuardError, "HTTP_502") as raised:
            ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.evidence.failure(str(raised.exception))
        self.evidence.failure("SECOND_ERROR")
        self.assertEqual(self.evidence.state["first_failure"], "HTTP_502")
        self.assertEqual(sum(p["for_sale"] for p in self.api.catalog), 2)
        self.assertEqual(sum(c[1] == self.api.fail_route for c in self.api.writes()), 1)
        self.assertEqual(json.loads((self.evidence.directory / "first-failure.json").read_text())["code"], "HTTP_502")
        with self.assertRaises(FileExistsError):
            ux.Evidence(self.evidence.directory)

    def test_narrow_plan_put_detects_unexpected_response_change(self):
        self.api.extra_plan_change = True
        with self.assertRaisesRegex(ux.GuardError, "UNEXPECTED_PLAN_CHANGE"):
            ux.apply_reviewed_settings(self.api, self.api.reviewed, self.evidence, publish=True)
        self.assertEqual(len(self.api.writes()), 2)

    def test_catalogue_file_tampering_fails_offline(self):
        bad = Path(self.temporary.name) / "catalogue.json"
        bad.write_text('{"plans":[]}', encoding="utf-8")
        with self.assertRaisesRegex(ux.GuardError, "CATALOGUE_HASH_NOT_REVIEWED"):
            ux.load_reviewed_catalog(bad)

    def test_http_client_has_no_ambient_proxy_and_does_not_follow_redirects(self):
        captured = []
        with patch.object(ux.urllib.request, "build_opener", side_effect=lambda *handlers: captured.extend(handlers)):
            ux.AdminAPI(ux.TARGETS[0], self.evidence)
        self.assertEqual(captured[0].proxies, {})
        self.assertIsNone(captured[1].redirect_request(None, None, 302, None, None, "https://untrusted.invalid"))
        with self.assertRaisesRegex(ux.GuardError, "TARGET_NOT_REVIEWED"):
            ux.AdminAPI("https://untrusted.invalid", self.evidence)

    def test_http_first_error_headers_preserved_without_response_body_or_tokens(self):
        api = ux.AdminAPI(ux.TARGETS[0], self.evidence)
        api.token = "SYNTHETIC_SECRET_TOKEN"
        headers = Message()
        headers["CF-Ray"] = "synthetic-ray-SIN"
        headers["X-Request-ID"] = "synthetic-request"
        error = ux.urllib.error.HTTPError("https://api.realyu.fun", 502, "BODY_MUST_NOT_BE_LOGGED", headers, None)
        with patch.object(api.http, "open", side_effect=error) as opened:
            with self.assertRaisesRegex(ux.GuardError, "^HTTP_502$"):
                api.call("GET", ux.SETTINGS_ROUTE)
        event_text = (self.evidence.directory / "http.jsonl").read_text()
        event = json.loads(event_text)
        self.assertEqual(event["status"], 502)
        self.assertEqual(event["cf_ray"], "synthetic-ray-SIN")
        self.assertEqual(event["request_id"], "synthetic-request")
        self.assertNotIn("SYNTHETIC_SECRET_TOKEN", event_text)
        self.assertNotIn("BODY_MUST_NOT_BE_LOGGED", event_text)
        self.assertEqual(opened.call_count, 1)
        self.assertEqual(opened.call_args.args[0].get_header("User-agent"), "RealYu-Maintenance/1.0")

    def test_wrong_live_version_prevents_reading_credentials_or_login(self):
        class WrongVersionAPI:
            refresh_token = None

            def __init__(self, *args):
                pass

            def call(self, *args):
                return {"version": "old", "engine": "sub2api", "single_core": True}

            def login(self, path):
                raise AssertionError("credentials must not be read")

        out = io.StringIO()
        with patch.object(ux, "AdminAPI", WrongVersionAPI), contextlib.redirect_stdout(out):
            result = ux.main(["--execute", "--credentials", "nonexistent-private.json",
                              "--output-dir", str(Path(self.temporary.name) / "wrong-version")])
        self.assertEqual(result, 1)
        self.assertEqual(json.loads(out.getvalue())["code"], "LIVE_VERSION_NOT_REVIEWED")


if __name__ == "__main__":
    unittest.main()

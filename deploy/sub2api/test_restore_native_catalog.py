import copy
import importlib.util
from pathlib import Path
import unittest


spec = importlib.util.spec_from_file_location("restore_native_catalog", Path(__file__).with_name("restore_native_catalog.py"))
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


def fixture():
    return {"plans": [{"id": 2, "enabled": 1, "title": "Lite", "funding_scope": "personal", "currency": "CNY",
                       "duration_unit": "day", "duration_value": 28, "upgrade_group": "", "total_amount": 8571428,
                       "weekly_amount": 2142857, "price_amount": 98, "allow_wallet_overflow": 1,
                       "allow_balance_pay": 1, "sort_order": 100, "quota_reset_period": "never"}]}


class CatalogueTests(unittest.TestCase):
    def test_preserves_legacy_entitlement_and_cny_price_without_exchange_conversion(self):
        plan, = catalog.build_catalog(fixture(), 2)
        self.assertEqual(plan["native"]["price"], 98)
        self.assertEqual(plan["native"]["currency"], "CNY")
        self.assertEqual(plan["native"]["group_id"], 2)
        self.assertFalse(plan["native"]["for_sale"])
        self.assertEqual(plan["managed"]["total_amount_quota"], 8571428)
        self.assertEqual(plan["managed"]["weekly_amount_quota"], 2142857)
        self.assertEqual(plan["managed"]["price_cents"], 9800)
        self.assertEqual(plan["managed"]["duration_seconds"], 2419200)

    def test_legacy_descending_order_becomes_native_ascending_and_disabled_plan_is_not_restored(self):
        source = fixture()
        second = copy.deepcopy(source["plans"][0])
        second.update(id=3, title="Starter", sort_order=99)
        source["plans"] = [second, {"id": 1, "enabled": False}, source["plans"][0]]
        result = catalog.build_catalog(source, 2)
        self.assertEqual([p["native"]["name"] for p in result], ["Lite", "Starter"])
        self.assertEqual([p["native"]["sort_order"] for p in result], [0, 1])
        self.assertTrue(all(p["native"]["for_sale"] is False for p in result))

    def test_fractional_cent_and_nonfinite_and_out_of_range_prices_fail_closed(self):
        for price in ("98.001", "NaN", "Infinity", "0", "-1", "10000.01"):
            with self.subTest(price=price), self.assertRaisesRegex(ValueError, "PRICE_REQUIRES_REVIEW"):
                source = fixture()
                source["plans"][0]["price_amount"] = price
                catalog.build_catalog(source, 2)

    def test_explicit_group_is_required_and_never_inferred_from_name(self):
        for group in (None, 0, -1, "2", True):
            with self.subTest(group=group), self.assertRaisesRegex(ValueError, "EXPLICIT_GROUP_REQUIRED"):
                catalog.build_catalog(fixture(), group)

    def test_unreviewed_scope_term_currency_reset_and_group_changes_are_rejected(self):
        for key, value in (("funding_scope", "team"), ("currency", "USD"), ("duration_value", 30),
                           ("duration_unit", "month"), ("quota_reset_period", "week"), ("upgrade_group", "vip")):
            with self.subTest(key=key), self.assertRaises(ValueError):
                source = fixture()
                source["plans"][0][key] = value
                catalog.build_catalog(source, 2)

    def test_bad_allowances_do_not_silently_round_or_expand(self):
        for total, weekly in ((0, 0), (100, 24), (100.0, 25), (4, True), (9007199254740992, 2251799813685248)):
            with self.subTest(total=total, weekly=weekly), self.assertRaisesRegex(ValueError, "ALLOWANCE_REQUIRES_REVIEW"):
                source = fixture()
                source["plans"][0].update(total_amount=total, weekly_amount=weekly)
                catalog.build_catalog(source, 2)

    def test_boolean_strings_cannot_turn_disabled_payment_flags_on(self):
        for key in ("enabled", "allow_balance_pay", "allow_wallet_overflow"):
            with self.subTest(key=key), self.assertRaises(ValueError):
                source = fixture()
                source["plans"][0][key] = "0"
                catalog.build_catalog(source, 2)

    def test_duplicate_legacy_identity_cannot_create_two_entitlements(self):
        source = fixture()
        source["plans"].append(copy.deepcopy(source["plans"][0]))
        with self.assertRaisesRegex(ValueError, "EMPTY_OR_DUPLICATE_CATALOGUE"):
            catalog.build_catalog(source, 2)

    def test_resume_rejects_operator_changes_to_existing_plan(self):
        expected = catalog.build_catalog(fixture(), 2)[0]["native"]
        self.assertTrue(catalog.matches_native(dict(expected, id=7), expected))
        for field, value in (("for_sale", True), ("group_id", 1), ("price", 99), ("validity_days", 30)):
            with self.subTest(field=field):
                self.assertFalse(catalog.matches_native(dict(expected, **{field: value}), expected))


if __name__ == "__main__":
    unittest.main()

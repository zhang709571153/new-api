"""Synthetic credential redaction/readback checks; never calls an upstream."""
import copy
import unittest

from production_supply import verify_refresh_transfer


class ReadbackClient:
    def __init__(self):
        self.metadata = {"id": 2, "name": "synthetic", "expires_at": None,
                         "credentials": {"chatgpt_account_id": "synthetic-account"}}
        self.export = {"accounts": [{"name": "synthetic", "platform": "openai", "type": "oauth",
            "credentials": {"access_token": "synthetic-access", "refresh_token": "synthetic-refresh",
                            "chatgpt_account_id": "synthetic-account"}}]}
        self.calls = []

    def call(self, path):
        self.calls.append(path)
        if path == "/api/v1/admin/accounts/2":
            return copy.deepcopy(self.metadata)
        if path == "/api/v1/admin/accounts/data?ids=2&include_proxies=false":
            return copy.deepcopy(self.export)
        raise AssertionError("Unexpected credential read scope")


class RefreshReadbackTests(unittest.TestCase):
    def setUp(self):
        self.client = ReadbackClient()
        self.expected = {"chatgpt_account_id": "synthetic-account", "refresh_token": "synthetic-refresh"}

    def test_redacted_get_is_verified_through_single_account_export(self):
        verify_refresh_transfer(self.client, 2, self.expected)
        self.assertEqual(len(self.client.calls), 2)
        self.assertNotIn("refresh_token", self.client.metadata["credentials"])

    def test_retained_hard_expiry_rejected_before_secret_export(self):
        self.client.metadata["expires_at"] = 1
        with self.assertRaises(RuntimeError):
            verify_refresh_transfer(self.client, 2, self.expected)
        self.assertEqual(len(self.client.calls), 1)

    def test_wrong_or_multiple_exported_accounts_rejected(self):
        for mutation in ("duplicate", "name", "identity", "missing_refresh", "different_refresh"):
            with self.subTest(mutation=mutation):
                client = ReadbackClient()
                row = client.export["accounts"][0]
                if mutation == "duplicate":
                    client.export["accounts"].append(copy.deepcopy(row))
                elif mutation == "name":
                    row["name"] = "another"
                elif mutation == "identity":
                    row["credentials"]["chatgpt_account_id"] = "another"
                elif mutation == "missing_refresh":
                    row["credentials"].pop("refresh_token")
                else:
                    row["credentials"]["refresh_token"] = "another"
                with self.assertRaises(RuntimeError):
                    verify_refresh_transfer(client, 2, self.expected)


if __name__ == "__main__":
    unittest.main()

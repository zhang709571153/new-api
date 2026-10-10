"""Apply only reviewed public branding through the native admin API.

Default is offline. Never submits a compliance acknowledgement, edits the DB,
or emits credentials/tokens. Preserve private before/after evidence locally.
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import urllib.error
import urllib.request

BASE = "https://api.realyu.fun"
BRAND = {"site_name": "RealYu API", "site_logo": "/brand/realyu-wordmark.png",
         "site_subtitle": "RealYu API", "api_base_url": BASE, "frontend_url": BASE}
HTTP = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--credentials", required=True)
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    if not args.execute:
        print(json.dumps({"status": "PREPARED_OFFLINE", "fields": sorted(BRAND)}))
        return
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "target": BASE,
              "expected_version": args.expected_version, "changed_fields": [],
              "acknowledgement_submitted": False, "ledger_modified": False,
              "requests": [], "status": "RUNNING"}

    def call(method, path, payload=None, token=None, raw=False):
        headers = {"Accept": "application/json", "Cache-Control": "no-cache"}
        body = None
        if payload is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(payload).encode()
        if token:
            headers["Authorization"] = "Bearer " + token
        req = urllib.request.Request(BASE + path, data=body, headers=headers, method=method)
        try:
            with HTTP.open(req, timeout=25) as response:
                data = response.read()
                report["requests"].append({"method": method, "path": path,
                    "status": response.status, "cf_ray": response.headers.get("cf-ray"),
                    "request_id": response.headers.get("x-request-id")})
        except urllib.error.HTTPError as exc:
            report["requests"].append({"method": method, "path": path, "status": exc.code,
                "cf_ray": exc.headers.get("cf-ray"), "request_id": exc.headers.get("x-request-id")})
            raise ValueError("HTTP_" + str(exc.code)) from None
        if raw:
            return data.decode("utf-8")
        return json.loads(data)

    def envelope(value):
        if value.get("code") != 0 or "data" not in value:
            raise ValueError("INVALID_API_ENVELOPE")
        return value["data"]

    try:
        status = call("GET", "/api/status")
        assert status.get("success") is True
        assert status["data"]["single_core"] is True and status["data"]["engine"] == "sub2api"
        assert status["data"]["version"] == args.expected_version
        private = read(args.credentials)
        auth = envelope(call("POST", "/api/v1/auth/login", {
            "email": private["admin_username"], "password": private["admin_password"]}))
        token = auth["access_token"]
        before = envelope(call("GET", "/api/v1/admin/settings", token=token))
        (out / "settings-before.private.json").write_text(json.dumps(before, ensure_ascii=False, indent=2), encoding="utf-8")
        envelope(call("PUT", "/api/v1/admin/settings", BRAND, token))
        after = envelope(call("GET", "/api/v1/admin/settings", token=token))
        (out / "settings-after.private.json").write_text(json.dumps(after, ensure_ascii=False, indent=2), encoding="utf-8")
        changed = sorted(k for k in set(before) | set(after) if before.get(k) != after.get(k))
        report["changed_fields"] = changed
        assert set(changed) <= set(BRAND), "UNRELATED_SETTINGS_CHANGED"
        assert all(after.get(k) == v for k, v in BRAND.items()), "BRAND_NOT_APPLIED"
        public = envelope(call("GET", "/api/v1/settings/public"))
        assert all(public.get(k) == BRAND[k] for k in BRAND if k != "frontend_url")
        assert public["customer_currency"] == "CNY" and public["customer_usd_to_cny"] == "7"
        html = call("GET", "/login", raw=True)
        assert '"site_name":"RealYu API"' in html and '"customer_currency":"CNY"' in html
        assert '"customer_usd_to_cny":"7"' in html
        report["public_and_html_cache_refreshed"] = True
        report["status"] = "PASS"
    except Exception as exc:
        report["status"] = "FAIL"
        report["first_failure"] = {"type": type(exc).__name__,
            "code": str(exc) if isinstance(exc, (AssertionError, ValueError)) else "PRIVATE_REQUEST_FAILURE"}
        raise
    finally:
        (out / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()

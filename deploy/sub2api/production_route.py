"""Prepare or activate an internal route using the existing RealYu admin API.

Uses a private plan and persistent receipt. Staging adds a disabled route only;
activation requires a closed, fully drained maintenance admission gate.
"""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import urllib.request


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))


def save(path, value):
    path = Path(path)
    temporary = path.with_suffix(".tmp")
    with temporary.open("w", encoding="utf-8") as output:
        json.dump(value, output, indent=2)
        output.flush()
        os.fsync(output.fileno())
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["stage", "restage", "activate", "restore-routing"])
    parser.add_argument("--plan", type=Path, required=True)
    args = parser.parse_args()
    plan = read(args.plan)
    source = Path(__file__).resolve().parents[2] / "lab/verify_provider_release.py"
    spec = importlib.util.spec_from_file_location("realyu_admin", source)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    api.BASE = "http://127.0.0.1:18300"
    config = read(plan["production_credentials"])
    admin = api.API()
    # Production cookies require the public trusted Origin for mutating routes.
    original_request = admin.request
    admin.request = lambda path, data=None, method=None, headers=None: original_request(
        path, data, method, {"Origin": "https://api.realyu.fun", **(headers or {})})
    receipt_path = Path(plan["receipt"])
    receipt = read(receipt_path) if receipt_path.exists() else {"phase": "new"}
    try:
        if admin.call("/api/status")["version"] != plan["expected_legacy_version"]:
            raise RuntimeError("Unexpected live legacy version")
        admin.login(config["admin_username"], config["admin_password"])
        channels = admin.call("/api/channel/?p=1&page_size=100")
        if channels["total"] > 100:
            raise RuntimeError("Channel inventory exceeds this deployment plan")
        items = channels["items"]
        if args.action == "restage":
            if receipt_path.exists():
                raise RuntimeError("Restaging requires a fresh operation receipt")
            prior_path = Path(plan["restored_receipt"])
            if hashlib.sha256(prior_path.read_bytes()).hexdigest() != plan["restored_receipt_sha256"]:
                raise RuntimeError("Previous restored receipt changed")
            prior = read(prior_path)
            if prior.get("phase") != "restored":
                raise RuntimeError("Previous route rollback is not complete")
            matches = [item for item in items if item["name"] == plan["route_name"]]
            if len(matches) != 1:
                raise RuntimeError("Restored route identity is ambiguous")
            restored = matches[0]
            if (restored["id"] != prior["channel_id"] or restored["status"] != 2
                    or restored["type"] != 59 or restored["base_url"] != plan["sub2api_base_url"]
                    or restored["group"] != "default"
                    or sorted(restored["models"].split(",")) != sorted(prior["models"])):
                raise RuntimeError("Restored route configuration differs")
            settings = json.loads(restored.get("setting") or "{}")
            if not all(settings.get(key) is True for key in ("pass_through_body_enabled", "responses_websocket_enabled")):
                raise RuntimeError("Restored route protocol settings differ")
            originals = [{key: item[key] for key in ("id", "type", "status", "models", "group")}
                         for item in items if item["id"] != restored["id"]]
            if sorted(originals, key=lambda item: item["id"]) != sorted(prior["original_channels"], key=lambda item: item["id"]):
                raise RuntimeError("Legacy channel baseline changed; reconcile before restaging")
            receipt = {"phase": "disabled_route_ready", "channel_id": restored["id"],
                       "models": prior["models"], "original_channels": originals,
                       "restored_receipt_sha256": plan["restored_receipt_sha256"], "restage_read_only": True}
        elif args.action == "stage":
            matches = [item for item in items if item["name"] == plan["route_name"]]
            if matches:
                if receipt.get("channel_id") != matches[0]["id"] or matches[0]["status"] != 2:
                    raise RuntimeError("Existing route needs explicit reconciliation")
            elif receipt["phase"] != "new":
                raise RuntimeError("An earlier route creation outcome needs reconciliation")
            else:
                receipt = {"phase": "creating", "original_channels": [
                    {key: item[key] for key in ("id", "type", "status", "models", "group")}
                    for item in items]}
                save(receipt_path, receipt)
                models = sorted(set(name for item in items if item["id"] in plan["source_channel_ids"]
                                    for name in item["models"].split(",")))
                route = {"type": 59, "status": 2, "name": plan["route_name"],
                    "base_url": plan["sub2api_base_url"], "models": ",".join(models),
                    "group": "default", "priority": 1, "weight": 1,
                    "key": "routing-placeholder-never-an-upstream-credential",
                    "setting": json.dumps({"pass_through_body_enabled": True,
                                           "responses_websocket_enabled": True})}
                admin.call("/api/channel/", {"mode": "single", "channel": route})
                matches = [item for item in admin.call("/api/channel/?p=1&page_size=100")["items"]
                           if item["name"] == plan["route_name"]]
                if len(matches) != 1 or matches[0]["status"] != 2 or matches[0]["type"] != 59:
                    raise RuntimeError("Staged route readback failed")
                receipt.update(phase="disabled_route_ready", channel_id=matches[0]["id"], models=models)
        else:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open("http://127.0.0.1:18302/", timeout=5) as response:
                gate = json.load(response)
            if gate.get("maintenance") is not True or gate.get("active_requests") != 0:
                raise RuntimeError("Route activation requires closed and drained admission")
            if args.action == "activate":
                if receipt["phase"] != "disabled_route_ready":
                    raise RuntimeError("Route stage is incomplete")
                receipt["phase"] = "activating"
                save(receipt_path, receipt)
                for item in receipt["original_channels"]:
                    if item["id"] in plan["source_channel_ids"]:
                        admin.call("/api/channel/" + str(item["id"]) + "/status", {"status": 2}, "POST")
                admin.call("/api/channel/" + str(receipt["channel_id"]) + "/status", {"status": 1}, "POST")
                receipt["phase"] = "active"
            else:
                # The caller must return current refresh ownership first if Sub
                # has received refresh tokens. This is routing-only rollback.
                if plan.get("refresh_ownership_returned") is not True:
                    raise RuntimeError("Refresh ownership must be reconciled before routing rollback")
                admin.call("/api/channel/" + str(receipt["channel_id"]) + "/status", {"status": 2}, "POST")
                for item in receipt["original_channels"]:
                    if item["id"] in plan["source_channel_ids"]:
                        admin.call("/api/channel/" + str(item["id"]) + "/status", {"status": item["status"]}, "POST")
                receipt["phase"] = "restored"
        save(receipt_path, receipt)
        print(json.dumps({"phase": receipt["phase"], "channel_id": receipt["channel_id"]}))
    finally:
        if admin.token:
            admin.call("/api/user/auth/logout", {})
            admin.token = ""


if __name__ == "__main__":
    main()

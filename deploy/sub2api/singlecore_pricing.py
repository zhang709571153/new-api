"""Read-only legacy price mapping. Output is a review artifact, never applied live.

Only the exact supported expression grammar is converted. Unknown models/rules
remain explicit blockers. Decimal strings preserve prices without binary-float
tolerances. Use singlecore_supply's private-output and isolated-PG boundaries.
"""
from __future__ import annotations

import argparse
import base64
from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

import singlecore_supply as supply

NUMBER = r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?"
EXPR = re.compile(r'\(len > (?P<threshold>[1-9][0-9]*) \? tier\("long", (?P<long>[^()]*)\) : tier\("standard", (?P<standard>[^()]*)\)\) \* \(\(param\("service_tier"\) == "fast" \|\| param\("service_tier"\) == "priority"\) \? (?P<fast>' + NUMBER + r') : 1\)')
TERMS = re.compile(r'p \* (?P<p>' + NUMBER + r') \+ cr \* (?P<cr>' + NUMBER + r') \+ c \* (?P<c>' + NUMBER + r')(?: \+ cc \* (?P<cc>' + NUMBER + r'))?')
FIELDS = {"p": "input_price", "cr": "cache_read_price", "c": "output_price", "cc": "cache_write_price"}


def decimal_text(value):
    return format(Decimal(value), "f")


def parse_expression(model, expression):
    match = EXPR.fullmatch(expression)
    if not match:
        raise supply.SupplyError("UNMAPPED_EXPRESSION:" + model)
    tiers = {}
    for tier in ("standard", "long"):
        terms = TERMS.fullmatch(match[tier])
        if not terms:
            raise supply.SupplyError("UNMAPPED_TERMS:" + model)
        values = terms.groupdict()
        # Legacy p excludes cache creation only when cc is explicitly priced.
        # When cc is absent, native's separated cache-creation tokens use p.
        values["cc"] = values["cc"] or values["p"]
        tiers[tier] = {k: decimal_text(v) for k, v in values.items()}
    card = {"platform": "openai", "models": [model], "billing_mode": "token",
            "fast_multiplier": decimal_text(match["fast"]), "flex_multiplier": "1",
            "reasoning_effort_multipliers": {}, "intervals": []}
    for key, value in tiers["standard"].items():
        card[FIELDS[key]] = decimal_text(Decimal(value) / 1000000)
    # Zero image-input invokes native's selected-interval input price fallback.
    # Null image-output preserves its fallback (explicit zero would mean free).
    # Requires catalog image-output/cache-image prices to be zero, verified in
    # native tests for these text models; revalidate if the price catalog changes.
    card["image_input_price"] = "0"
    card["image_output_price"] = None
    card["cache_write_1h_price"] = card["cache_write_price"]
    for tier, lo, hi in (("standard", 0, int(match["threshold"])), ("long", int(match["threshold"]), None)):
        interval = {"min_tokens": lo, "max_tokens": hi, "tier_label": tier,
                    "sort_order": len(card["intervals"])}
        interval.update({FIELDS[k]: decimal_text(Decimal(v) / 1000000) for k, v in tiers[tier].items()})
        interval["cache_write_1h_price"] = interval["cache_write_price"]
        card["intervals"].append(interval)
    return {"model": model, "source": "tiered_expr", "expression_sha256": hashlib.sha256(expression.encode()).hexdigest(),
            "threshold": int(match["threshold"]), "usd_per_million": tiers, "card": card}


def native_image_ratio_rule(options):
    load = lambda key: json.loads(options.get(key,"{}"),parse_float=Decimal)
    name="gpt-image-2"
    if name not in load("ModelRatio"):
        return None
    if name in load("ModelPrice"):
        raise supply.SupplyError("IMAGE_PER_REQUEST_PRICE_REQUIRES_REVIEW")
    base=Decimal(str(load("ModelRatio")[name]))*2
    prices={"p":base,"c":base*Decimal(str(load("CompletionRatio").get(name,1))),
            "cr":base*Decimal(str(load("CacheRatio").get(name,1))),
            "cc":base*Decimal(str(load("CreateCacheRatio").get(name,Decimal("1.25"))))}
    image_ratio=Decimal(str(load("ImageRatio").get(name,1)))
    # The current supported image path has ratio 1. Other image ratio/cache
    # overlap policies require a separate normalized-token contract.
    if image_ratio != 1:
        raise supply.SupplyError("IMAGE_INPUT_RATIO_REQUIRES_REVIEW")
    source={k:decimal_text(v) for k,v in prices.items()}
    card={"platform":"openai","models":[name],"billing_mode":"token","fast_multiplier":"1","flex_multiplier":"1",
          "reasoning_effort_multipliers":{},"intervals":[]}
    card.update({FIELDS[k]:decimal_text(v/1000000) for k,v in prices.items()})
    card["cache_write_1h_price"]=card["cache_write_price"]
    card["image_input_price"]=card["input_price"]
    card["image_output_price"]=card["output_price"]
    # A constant interval suppresses any inherited official long-context rule.
    for lo,hi,label in ((0,272000,"standard"),(272000,None,"long")):
        card["intervals"].append({"min_tokens":lo,"max_tokens":hi,"tier_label":label,"sort_order":len(card["intervals"]),
                                  **{k:card[k] for k in (*FIELDS.values(),"cache_write_1h_price")}})
    return {"model":name,"source":"ratio","expression_sha256":supply.digest(source),"threshold":272000,
            "usd_per_million":{"standard":source,"long":source},"card":card,
            "minimum_nonzero_quota":1}


def expected_quota(rule, group_ratio, tokens, service_tier="default"):
    context = tokens["p"] + tokens["cr"] + tokens["cc"]
    tier = "long" if context > rule["threshold"] else "standard"
    cost = sum(Decimal(tokens[k]) * Decimal(v) for k, v in rule["usd_per_million"][tier].items())
    factor = Decimal(rule["card"]["fast_multiplier"]) if service_tier in ("fast", "priority") else Decimal(1)
    quota=int((cost * factor * Decimal(group_ratio) / 2).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return max(quota,rule.get("minimum_nonzero_quota",0)) if sum(tokens.values())>0 else quota


def vectors(rule, ratio):
    threshold = rule["threshold"]
    token_sets = [dict(p=0, cr=0, c=0, cc=0), dict(p=1, cr=0, c=1, cc=0),
                  dict(p=23, cr=0, c=515, cc=0), dict(p=17000, cr=32000, c=1931, cc=19),
                  dict(p=threshold-100, cr=99, c=1001, cc=1),
                  dict(p=threshold-100, cr=100, c=1001, cc=1),
                  dict(p=threshold+1, cr=0, c=3000, cc=0),
                  dict(p=0, cr=threshold+1, c=121, cc=0),
                  dict(p=0, cr=0, c=121, cc=threshold+1)]
    token_sets += [dict(p=i, cr=i % 7, c=i * 3 + 1, cc=i % 3) for i in range(128)]
    result=[{"tokens": tokens, "service_tier": tier, "expected_quota": expected_quota(rule, ratio, tokens, tier)}
            for tokens in token_sets for tier in ("default", "fast", "priority", "flex")]
    # Image tokens are subsets of p/c, not extra tokens. These expressions have
    # no img terms, so standard/long use the same respective text rate.
    for tokens in (dict(p=2000,cr=100,c=513,cc=0),dict(p=threshold+1,cr=100,c=513,cc=0)):
        result.append({"tokens":{**tokens,"image_input":1800,"image_output":500},"service_tier":"default",
                       "expected_quota":expected_quota(rule,ratio,tokens)})
    return result


def build_mapping(options, sample):
    parse = lambda name, default="{}": json.loads(options.get(name, default), parse_float=Decimal)
    ratios = parse("GroupRatio")
    if set(ratios) != {"default"} or parse("GroupGroupRatio"):
        raise supply.SupplyError("GROUP_PRICE_MAPPING_REQUIRES_EXPLICIT_REVIEW")
    ratio = decimal_text(ratios["default"])
    if Decimal(ratio) <= 0:
        raise supply.SupplyError("INVALID_GROUP_RATIO")
    expressions = parse("billing_setting.billing_expr")
    rules, unmapped = [], []
    for model, expression in sorted(expressions.items()):
        try:
            rule = parse_expression(model, expression)
            rule["vectors"] = vectors(rule, ratio)
            rules.append(rule)
        except supply.SupplyError:
            unmapped.append({"model": model, "reason": "unsupported_expression",
                             "expression_sha256": hashlib.sha256(str(expression).encode()).hexdigest()})
    if parse("billing_setting.plugin_billing_expr"):
        unmapped.append({"reason": "plugin_expression_configured"})
    image_rule=native_image_ratio_rule(options)
    if image_rule:
        image_rule["vectors"]=vectors(image_rule,ratio)
        rules.append(image_rule)
    by_model, modes, tool_samples, drift = Counter(), Counter(), 0, 0
    rule_by_model = {r["model"]: r for r in rules}
    for model, other in sample:
        record = json.loads(other or "{}")
        by_model[model] += 1
        modes[str(record.get("billing_mode", "unspecified"))] += 1
        tool_samples += bool(record.get("tool_surcharges"))
        if record.get("expr_b64"):
            expr_hash = hashlib.sha256(base64.b64decode(record["expr_b64"], validate=True)).hexdigest()
            if model not in rule_by_model or expr_hash != rule_by_model[model]["expression_sha256"]:
                drift += 1
        elif model not in rule_by_model:
            unmapped.append({"model": model, "reason": "sample_has_no_supported_expression"})
    result = {"version": 1, "purpose": "review_only_no_live_apply", "quota_per_usd": 500000,
              "legacy_default_group_ratio": ratio,
              "required_group": {"rate_multiplier": ratio, "long_context_pricing_enabled": True, "model_pricing": []},
              "required_channel": {"billing_model_source": "requested", "restrict_models": True},
              "rules": rules, "unmapped": list({supply.canonical(x): x for x in unmapped}.values()),
              "sample": {"count": len(sample), "model_counts": dict(by_model), "billing_modes": dict(modes),
                         "tool_surcharge_rows": tool_samples, "current_expression_drift_rows": drift},
              "limitations": ["Token normalization must match legacy full-context p+cr+cc; exact context boundary is (min,max].",
                  "Cards belong to channels, not group.model_pricing (native strips group intervals).",
                  "Native request service-tier normalization/upstream returned tier differs for noncanonical case and ultrafast; require admission policy or explicit validation.",
                  "Text-model long-image fallback is tested with zero catalog image-output/cache-image prices; freeze or revalidate catalog values. Cached-image-specific prices remain unmapped when nonzero.",
                  "Legacy Responses image-generation CNY surcharge and web-search/tool surcharges are not implemented by token cards.",
                  "gpt-image-2 uses explicit ratio-derived text/image prices and flat long-context intervals; old minimum-one-quota differs at tiny usage and is reported separately.",
                  "Native float arithmetic is tested against exact integer quota; any mismatch is a release decision, never tolerated by epsilon.",
                  "Account scheduling cost multiplier is supply routing only, not a replacement for customer group multiplier.",
                  "Keep imported personal/team quota funding and plan caps unchanged; this maps consumption prices only."],
              "production_ready": False}
    result["mapping_sha256"] = supply.digest(result)
    return result


def read_legacy(path, limit):
    if not 0 <= limit <= 5000:
        raise supply.SupplyError("INVALID_SAMPLE_LIMIT")
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.execute("BEGIN")
        names = ("GroupRatio", "GroupGroupRatio", "billing_setting.billing_expr", "billing_setting.plugin_billing_expr",
                 "ModelRatio","CompletionRatio","CacheRatio","CreateCacheRatio","ImageRatio","ModelPrice")
        options = dict(conn.execute("SELECT key,value FROM options WHERE key IN ("+",".join("?" for _ in names)+")", names))
        sample = conn.execute("SELECT model_name,other FROM logs WHERE type=2 ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return build_mapping(options, sample)
    finally:
        conn.close()


def apply_rehearsal(conn, mapping, group_id):
    """Atomic price-only installation into an explicitly isolated, unused group.

    Caller must use supply.connect_config. Never reprice a live customer group.
    No updating existing channel: a repeat is rejected instead of overwriting.
    """
    from psycopg import sql
    content = dict(mapping)
    sha = content.pop("mapping_sha256", "")
    if sha != supply.digest(content) or content.get("version") != 1 or not content.get("rules") or content.get("unmapped"):
        raise supply.SupplyError("UNVERIFIED_OR_UNMAPPED_PRICING")
    price_fields = ("input_price", "output_price", "cache_read_price", "cache_write_price", "cache_write_1h_price")
    with conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(731924611)")
        conn.execute("LOCK TABLE groups,channels,channel_groups,channel_model_pricing,channel_pricing_intervals IN ACCESS EXCLUSIVE MODE")
        for table in ("api_keys", "usage_logs", "payment_orders"):
            if conn.execute(sql.SQL("SELECT EXISTS(SELECT 1 FROM {} LIMIT 1)").format(sql.Identifier(table))).fetchone()[0]:
                raise supply.SupplyError("PRICING_TARGET_HAS_CUSTOMER_ACTIVITY")
        if conn.execute("SELECT EXISTS(SELECT 1 FROM users WHERE role<>'admin' LIMIT 1)").fetchone()[0]:
            raise supply.SupplyError("PRICING_TARGET_HAS_CUSTOMERS")
        if conn.execute("SELECT EXISTS(SELECT 1 FROM accounts WHERE schedulable=true LIMIT 1)").fetchone()[0]:
            raise supply.SupplyError("REHEARSAL_ACCOUNT_SCHEDULING_MUST_BE_DISABLED")
        row = conn.execute("SELECT platform,model_pricing FROM groups WHERE id=%s FOR UPDATE", (group_id,)).fetchone()
        if not row or row[0] != "openai" or row[1] not in (None, []):
            raise supply.SupplyError("UNUSED_OPENAI_GROUP_REQUIRED")
        if conn.execute("SELECT EXISTS(SELECT 1 FROM channel_groups WHERE group_id=%s)", (group_id,)).fetchone()[0]:
            raise supply.SupplyError("GROUP_ALREADY_HAS_CHANNEL")
        channel = conn.execute("INSERT INTO channels(name,status,billing_model_source,restrict_models) VALUES(%s,'active','requested',true) RETURNING id", ("realyu-legacy-" + sha[:16],)).fetchone()[0]
        conn.execute("INSERT INTO channel_groups(channel_id,group_id) VALUES(%s,%s)", (channel, group_id))
        for rule in mapping["rules"]:
            card = rule["card"]
            fields = ("channel_id", "platform", "models", "billing_mode") + price_fields + ("fast_multiplier", "flex_multiplier", "image_input_price", "image_output_price")
            values = [channel, "openai", json.dumps(card["models"]), "token"] + [Decimal(card[k]) if card[k] is not None else None for k in fields[4:]]
            statement = sql.SQL("INSERT INTO channel_model_pricing ({}) VALUES ({}) RETURNING id").format(sql.SQL(",").join(map(sql.Identifier, fields)), sql.SQL(",").join([sql.Placeholder()] * len(fields)))
            pricing_id = conn.execute(statement, values).fetchone()[0]
            for iv in card["intervals"]:
                iv_fields = ("pricing_id", "min_tokens", "max_tokens", "tier_label", "sort_order") + price_fields
                iv_values = [pricing_id, iv["min_tokens"], iv["max_tokens"], iv["tier_label"], iv["sort_order"]] + [Decimal(iv[k]) for k in price_fields]
                statement = sql.SQL("INSERT INTO channel_pricing_intervals ({}) VALUES ({})").format(sql.SQL(",").join(map(sql.Identifier, iv_fields)), sql.SQL(",").join([sql.Placeholder()] * len(iv_fields)))
                conn.execute(statement, iv_values)
            stored = conn.execute(sql.SQL("SELECT {} FROM channel_model_pricing WHERE id=%s").format(sql.SQL(",").join(map(sql.Identifier, fields[4:]))), (pricing_id,)).fetchone()
            if list(stored) != values[4:]:
                raise supply.SupplyError("DATABASE_PRICE_PRECISION_LOSS")
            for iv in card["intervals"]:
                stored = conn.execute(sql.SQL("SELECT {} FROM channel_pricing_intervals WHERE pricing_id=%s AND sort_order=%s").format(sql.SQL(",").join(map(sql.Identifier, price_fields))), (pricing_id, iv["sort_order"])).fetchone()
                if list(stored) != [Decimal(iv[k]) for k in price_fields]:
                    raise supply.SupplyError("DATABASE_INTERVAL_PRECISION_LOSS")
        ratio = Decimal(mapping["legacy_default_group_ratio"])
        conn.execute("UPDATE groups SET rate_multiplier=%s,long_context_pricing_enabled=true WHERE id=%s", (ratio, group_id))
        if conn.execute("SELECT rate_multiplier FROM groups WHERE id=%s", (group_id,)).fetchone()[0] != ratio:
            raise supply.SupplyError("DATABASE_GROUP_PRECISION_LOSS")
        return {"status": "REHEARSAL_PRICE_CARDS_INSTALLED", "mapping_sha256": sha, "channel_id": channel,
                "group_id": group_id, "model_count": len(mapping["rules"]), "production_ready": False,
                "limitations": mapping["limitations"]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("map", "apply-rehearsal"), nargs="?", default="map")
    p.add_argument("--legacy-sqlite")
    p.add_argument("--output")
    p.add_argument("--mapping")
    p.add_argument("--expected-sha256")
    p.add_argument("--target-config")
    p.add_argument("--group-id", type=int)
    p.add_argument("--sample-limit", type=int, default=500)
    args = p.parse_args()
    try:
        if args.action == "apply-rehearsal":
            if not args.mapping or not args.expected_sha256 or not args.target_config or not args.group_id:
                raise supply.SupplyError("REHEARSAL_ARGUMENTS_REQUIRED")
            mapping = json.loads(Path(args.mapping).read_text(encoding="utf-8-sig"))
            if mapping.get("mapping_sha256") != args.expected_sha256:
                raise supply.SupplyError("EXPECTED_MAPPING_SHA_MISMATCH")
            with supply.connect_config(supply.load_config(args.target_config)) as conn:
                result = apply_rehearsal(conn, mapping, args.group_id)
            print(supply.canonical(result))
            return 0
        if not args.legacy_sqlite or not args.output:
            raise supply.SupplyError("MAPPING_ARGUMENTS_REQUIRED")
        result = read_legacy(args.legacy_sqlite, args.sample_limit)
        supply.private_write(Path(args.output), result)
        print(supply.canonical({"status": "MAPPED_REVIEW_REQUIRED", "models": len(result["rules"]),
                                "unmapped": result["unmapped"], "sample": result["sample"], "sha256": result["mapping_sha256"]}))
    except Exception as error:
        print(supply.canonical({"status": "ERROR", "code": str(error) if isinstance(error, supply.SupplyError) else "PRICING_MAPPING_FAILED"}))
        return 1
    return 0


if __name__ == "__main__": sys.exit(main())

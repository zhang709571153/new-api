"""Strict, bounded single-core production acceptance; offline by default.

Uses the same-socket WS flow from lab/sub2api_e2e/release_acceptance.py and
the standalone PDF construction from runtime/real-upstream-e2e.py. This script
never provisions users, edits balances, reads a SQLite ledger, or retries a
request. Run only with the already installed acceptance Python environment.
Raw requests/responses are kept in a fresh ACL-restricted directory. Console
output never includes an API key, response text, headers, or exception message.
"""
from __future__ import annotations

import argparse
import base64
import codecs
import csv
import datetime as dt
import hashlib
import importlib.metadata
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import re
import secrets
import socket
import ssl
import subprocess
import sys
import threading
import time
from urllib.parse import quote, urlsplit


ROOT = Path(__file__).resolve().parent
AUTHORITY = ROOT / "production-cutover-20261010-r3" / "authority-receipt.json"
KEY_FILE = Path(r"C:\srv\realyu-newapi-releases\20260924-provider-2bc6a0e3\.lab\credentials.json")
BASE = "https://api.realyu.fun/v1"
EXPECTED_VERSION = "realyu-singlecore-v0.2.15-20261010-ws-owner"
SG_PROXY = "http://127.0.0.1:17897"
TEXT_MODELS = (
    "gpt-6-astra", "gpt-6.1-sol", "gpt-6-sol", "gpt-6-luna",
    "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna", "gpt-5.5",
)
CASES = (
    "catalog", "responses-stream", "responses-nonstream", "pdf-inline",
    "web-search", "image-generation", "websocket", "tool-roundtrip",
    "all-text-models",
)
DEFAULT_CASES = "catalog,responses-stream,responses-nonstream,pdf-inline,web-search"
REQUEST_COUNTS = {case: 1 for case in CASES}
REQUEST_COUNTS.update(catalog=0, websocket=2, **{"tool-roundtrip": 2, "all-text-models": 8})
MAX_BODY = 64 * 1024 * 1024
HEADER_ALLOWLIST = ("cf-ray", "x-request-id", "x-oneapi-request-id", "content-type", "content-length")


class CheckFailure(Exception):
    """A fixed, non-sensitive check code. Never use upstream text as its value."""


def require(condition, code):
    if not condition:
        raise CheckFailure(code)


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def public_error(exc):
    row = {"error_type": type(exc).__name__}
    if isinstance(exc, CheckFailure):
        row["check_code"] = exc.args[0]
    return row


def output_text(response):
    return "".join(
        part.get("text", "") for item in response.get("output", [])
        if item.get("type") == "message" for part in item.get("content", [])
        if part.get("type") == "output_text"
    )


def check_final(response):
    require(isinstance(response, dict), "RESPONSE_NOT_OBJECT")
    require(response.get("object") == "response", "RESPONSE_OBJECT_MISSING")
    require(response.get("status") == "completed", "RESPONSE_NOT_COMPLETED")
    require(isinstance(response.get("id"), str) and response["id"].startswith("resp_"), "RESPONSE_ID_MISSING")
    output = response.get("output")
    require(isinstance(output, list) and bool(output), "FINAL_OUTPUT_EMPTY")
    require(all(isinstance(item, dict) for item in output), "OUTPUT_ITEM_INVALID")
    usage = response.get("usage")
    require(isinstance(usage, dict), "USAGE_MISSING")
    for field in ("input_tokens", "output_tokens"):
        require(type(usage.get(field)) is int and usage[field] >= 0, "USAGE_INVALID")
    if "total_tokens" in usage:
        require(type(usage["total_tokens"]) is int and usage["total_tokens"] == usage["input_tokens"] + usage["output_tokens"], "USAGE_TOTAL_MISMATCH")
    return {
        "response_id": response["id"], "completion_status": response["status"],
        "response_model": response.get("model"), "usage": usage,
        "output_types": [item.get("type") for item in output],
        "final_characters": len(output_text(response)),
    }


class ResponseAudit:
    def __init__(self):
        self.events = 0
        self.deltas = []
        self.done_ids = set()
        self.response_ids = set()
        self.errors = []
        self.completed = []

    def accept(self, event):
        require(isinstance(event, dict), "EVENT_NOT_OBJECT")
        self.events += 1
        require(self.events <= 20000, "EVENT_COUNT_LIMIT")
        kind = event.get("type")
        if kind in ("error", "response.failed", "response.incomplete", "response.cancelled"):
            self.errors.append(kind)
        if isinstance(event.get("response_id"), str):
            self.response_ids.add(event["response_id"])
        nested = event.get("response")
        if isinstance(nested, dict) and isinstance(nested.get("id"), str):
            self.response_ids.add(nested["id"])
        if kind == "response.output_text.delta":
            require(isinstance(event.get("delta"), str), "DELTA_NOT_TEXT")
            self.deltas.append(event["delta"])
        if kind == "response.output_item.done":
            item = event.get("item")
            require(isinstance(item, dict), "DONE_ITEM_INVALID")
            if item.get("id"):
                self.done_ids.add(item["id"])
        if kind == "response.completed":
            self.completed.append(nested)

    def finish(self):
        require(not self.errors, "UPSTREAM_FAILURE_EVENT")
        require(len(self.completed) == 1, "COMPLETED_EVENT_COUNT")
        final = self.completed[0]
        meta = check_final(final)
        require(self.response_ids == {final["id"]}, "EVENT_RESPONSE_ID_MISMATCH")
        delta = "".join(self.deltas)
        require(output_text(final) == delta, "DELTA_FINAL_TEXT_MISMATCH")
        ids = [item["id"] for item in final["output"] if item.get("id")]
        require(len(ids) == len(set(ids)), "DUPLICATE_FINAL_OUTPUT_ID")
        require(self.done_ids.issubset(set(ids)), "COMPLETED_ITEM_LOST")
        return final, {**meta, "events": self.events, "delta_characters": len(delta),
                       "delta_final_consistent": True, "completed_items_preserved": True}


class WebSocketAudit(ResponseAudit):
    """Codex WS may send a compact terminal response after completed items."""
    def __init__(self):
        super().__init__()
        self.completed_items = {}

    def accept(self, event):
        super().accept(event)
        if event.get("type") == "response.output_item.done":
            index, item = event.get("output_index"), event["item"]
            require(type(index) is int and index >= 0, "WS_DONE_INDEX_INVALID")
            require(index not in self.completed_items, "WS_DONE_INDEX_DUPLICATE")
            require(isinstance(item.get("id"), str) and bool(item["id"]), "WS_DONE_ID_MISSING")
            require(item.get("status") in (None, "completed"), "WS_DONE_ITEM_INCOMPLETE")
            self.completed_items[index] = item

    def finish(self):
        require(not self.errors, "UPSTREAM_FAILURE_EVENT")
        require(len(self.completed) == 1, "COMPLETED_EVENT_COUNT")
        terminal = self.completed[0]
        compact = isinstance(terminal, dict) and terminal.get("output") == []
        if compact:
            require(bool(self.completed_items), "WS_COMPACT_COMPLETED_ITEMS_MISSING")
            require(sorted(self.completed_items) == list(range(len(self.completed_items))), "WS_COMPLETED_ITEM_GAP")
            # Do not alter the raw terminal event retained as evidence.
            self.completed = [{**terminal, "output": [self.completed_items[i] for i in range(len(self.completed_items))]}]
        try:
            final, meta = super().finish()
            return final, {**meta, "compact_terminal_output": compact,
                           "output_reconstructed_from_done_items": compact}
        finally:
            self.completed = [terminal]


class SSEAudit(ResponseAudit):
    def __init__(self):
        super().__init__()
        self.decoder = codecs.getincrementaldecoder("utf-8")("strict")
        self.pending = ""
        self.data_lines = []

    def dispatch(self):
        if not self.data_lines:
            return
        raw = "\n".join(self.data_lines)
        self.data_lines.clear()
        if raw == "[DONE]":
            return
        try:
            self.accept(json.loads(raw))
        except json.JSONDecodeError:
            raise CheckFailure("MALFORMED_SSE_JSON") from None

    def feed(self, chunk, final=False):
        self.pending += self.decoder.decode(chunk, final=final)
        require(len(self.pending) <= MAX_BODY, "SSE_LINE_LIMIT")
        while "\n" in self.pending:
            line, self.pending = self.pending.split("\n", 1)
            line = line.removesuffix("\r")
            if not line:
                self.dispatch()
            elif line.startswith("data:"):
                value = line[5:]
                self.data_lines.append(value[1:] if value.startswith(" ") else value)
        if final:
            if self.pending:
                line = self.pending.removesuffix("\r")
                if line.startswith("data:"):
                    self.data_lines.append(line[5:].removeprefix(" "))
                self.pending = ""
            self.dispatch()


def pdf(marker):
    """Self-contained synthetic PDF; marker is never sent as adjacent text."""
    require(bool(re.fullmatch(r"RY[A-F0-9]{24}", marker)), "PDF_MARKER_INVALID")
    stream = (f"BT /F1 18 Tf 50 730 Td (RealYu migration PDF test) Tj 0 -32 Td "
              f"(Verification code: {marker}) Tj ET").encode("ascii")
    parts = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
    ]
    out, offsets = b"%PDF-1.4\n", [0]
    for index, part in enumerate(parts, 1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + part + b"\nendobj\n"
    start = len(out)
    out += b"xref\n0 6\n0000000000 65535 f \n"
    out += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets[1:])
    out += f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return out


def check_authority(value):
    require(isinstance(value, dict) and value.get("phase") == "ACTIVE" and value.get("opened") is True, "AUTHORITY_NOT_ACTIVE_OPENED")


def check_status(value, expected_version=EXPECTED_VERSION):
    require(isinstance(value, dict) and value.get("success") is True, "PUBLIC_STATUS_UNSUCCESSFUL")
    data = value.get("data", {})
    require(data.get("version") == expected_version, "PUBLIC_VERSION_MISMATCH")
    require(data.get("engine") == "sub2api" and data.get("single_core") is True, "PUBLIC_ENGINE_NOT_SINGLECORE")


def check_catalog(value):
    require(isinstance(value, dict) and isinstance(value.get("data"), list), "MODEL_CATALOG_INVALID")
    models = [item.get("id") for item in value["data"] if isinstance(item, dict)]
    require(len(models) == len(set(models)), "DUPLICATE_MODEL_CATALOG_ID")
    require(all(model in models for model in TEXT_MODELS), "PUBLISHED_TEXT_MODEL_MISSING")
    require("gpt-image-2" in models, "PUBLISHED_IMAGE_MODEL_MISSING")
    return {"catalog_count": len(models), "published_text_models_present": list(TEXT_MODELS), "image_model_present": True}


def check_search(final):
    calls = [item for item in final["output"] if item.get("type") == "web_search_call"]
    require(bool(calls) and all(item.get("status") == "completed" for item in calls), "ACTUAL_COMPLETED_SEARCH_MISSING")
    urls = [annotation.get("url", "") for item in final["output"] if item.get("type") == "message"
            for part in item.get("content", []) if part.get("type") == "output_text"
            for annotation in part.get("annotations", []) if annotation.get("type") == "url_citation"]
    valid = [url for url in urls if isinstance(url, str) and urlsplit(url).scheme in ("http", "https") and urlsplit(url).netloc]
    require(bool(valid), "SEARCH_CITATION_URL_MISSING")
    require("github.com/openai/codex" in output_text(final), "SEARCH_EXPECTED_URL_MISSING")
    return {"actual_search_calls": len(calls), "valid_citation_urls": len(valid), "expected_official_url_present": True}


class PrivateStore:
    def __init__(self, path):
        self.path = path

    def create(self):
        require(os.name == "nt", "WINDOWS_PRIVATE_ACL_REQUIRED")
        self.path.mkdir(parents=True, exist_ok=False)
        who = subprocess.run(["whoami.exe", "/user", "/fo", "csv", "/nh"], capture_output=True, text=True, check=True)
        sid = next(csv.reader(io.StringIO(who.stdout)))[-1]
        require(bool(re.fullmatch(r"S-1-5-(?:\d+-)*\d+", sid)), "OPERATOR_SID_INVALID")
        result = subprocess.run([
            "icacls.exe", str(self.path), "/inheritance:r", "/grant:r",
            f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        require(result.returncode == 0, "PRIVATE_OUTPUT_ACL_FAILED")

    def json(self, name, value):
        with (self.path / name).open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False, indent=2)
            output.flush()

    def checkpoint(self, report):
        temp = self.path / "summary.tmp"
        with temp.open("w", encoding="utf-8") as output:
            json.dump(report, output, ensure_ascii=False, indent=2)
            output.flush()
        os.replace(temp, self.path / "summary.json")

    def first_error(self, row):
        try:
            self.json("first-error.json", row)
        except FileExistsError:
            pass


class Acceptance:
    def __init__(self, args, plan, store):
        import httpx
        self.httpx = httpx
        self.args, self.store = args, store
        self.started = time.monotonic()
        self.deadline = self.started + args.budget_seconds
        self.key = None
        self.gates_passed = False
        self.counter = 0
        self.model_requests = 0
        self.report = {**plan, "status": "RUNNING", "started_at": utcnow(), "requests": [], "cases": [],
                       "authority_gate": "NOT_RUN", "public_gate": "NOT_RUN", "operator_key_read": False}
        self.ssl_context = ssl.create_default_context()
        transport = httpx.HTTPTransport(verify=self.ssl_context, trust_env=False, proxy=args.proxy, retries=0)
        self.client = httpx.Client(transport=transport, trust_env=False, follow_redirects=False,
                                   timeout=args.request_timeout, headers={"User-Agent": "RealYu-SingleCore-Production-E2E/20261010", "Accept-Encoding": "identity"})
        self.timer = threading.Timer(args.budget_seconds, self.hard_timeout)
        self.timer.daemon = True

    def hard_timeout(self):
        row = {"status": "FAIL", "at": utcnow(), "check_code": "TOTAL_WALL_BUDGET_EXCEEDED", "error_type": "TimeoutError"}
        try:
            self.store.first_error(row)
            self.store.json("wall-budget-timeout.json", row)
        finally:
            print(json.dumps({"status": "FAIL", "check_code": "TOTAL_WALL_BUDGET_EXCEEDED"}), flush=True)
            os._exit(124)

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        require(remaining > 0, "TOTAL_WALL_BUDGET_EXCEEDED")
        return min(float(self.args.request_timeout), remaining)

    def save(self):
        self.report["model_requests_attempted"] = self.model_requests
        self.report["seconds"] = round(time.monotonic() - self.started, 3)
        self.store.checkpoint(self.report)

    def reserve(self):
        require(self.gates_passed and bool(self.key), "MODEL_REQUEST_BEFORE_GATES")
        require(self.model_requests < self.report["maximum_model_requests"], "MODEL_REQUEST_BUDGET_EXCEEDED")
        self.remaining()
        self.model_requests += 1

    def headers(self, probe, auth):
        headers = {"X-Client-Request-Id": probe}
        if auth:
            require(self.gates_passed and bool(self.key), "AUTHENTICATED_REQUEST_BEFORE_GATES")
            headers["Authorization"] = "Bearer " + self.key
        return headers

    def request(self, name, method, path, body=None, *, auth=True, sse=False):
        self.counter += 1
        serial = f"{self.counter:02d}-{name}"
        probe, started = secrets.token_hex(16), time.monotonic()
        row = {"name": name, "probe_id": probe, "at": utcnow(), "method": method, "path": path,
               "status": "FAIL", "http_status": None, "bytes": 0, "body_complete": False, "automatic_retries": 0}
        self.report["requests"].append(row)
        if body is not None:
            self.store.json(serial + ".request.private.json", body)
        parser = SSEAudit() if sse else None
        buffer = bytearray()
        try:
            if method == "POST":
                self.reserve()
            with (self.store.path / (serial + ".body.private")).open("xb") as raw:
                self.report["network_requests_executed"] += 1
                with self.client.stream(method, "https://api.realyu.fun" + path,
                                        headers=self.headers(probe, auth), json=body,
                                        timeout=self.remaining()) as response:
                    row["http_status"] = response.status_code
                    row["headers"] = {key: response.headers.get(key) for key in HEADER_ALLOWLIST}
                    row["cf_ray"] = response.headers.get("cf-ray")
                    row["request_id"] = response.headers.get("x-request-id") or response.headers.get("x-oneapi-request-id")
                    is_sse = "text/event-stream" in response.headers.get("content-type", "").lower()
                    for chunk in response.iter_bytes():
                        self.remaining()
                        row["bytes"] += len(chunk)
                        require(row["bytes"] <= MAX_BODY, "RESPONSE_BODY_LIMIT")
                        raw.write(chunk)
                        raw.flush()
                        if parser and response.status_code == 200 and is_sse:
                            parser.feed(chunk)
                        else:
                            buffer.extend(chunk)
                    row["body_complete"] = True
                    require(response.status_code == 200, "HTTP_STATUS_NOT_200")
                    if sse:
                        require(is_sse, "SSE_CONTENT_TYPE_MISSING")
                        parser.feed(b"", final=True)
                        value, meta = parser.finish()
                        row.update(meta)
                    else:
                        require("json" in response.headers.get("content-type", "").lower(), "JSON_CONTENT_TYPE_MISSING")
                        try:
                            value = json.loads(buffer)
                        except (ValueError, UnicodeError):
                            raise CheckFailure("MALFORMED_JSON_BODY") from None
                        if path == "/v1/responses":
                            row.update(check_final(value))
            row["status"] = "PASS"
            return value, row
        except BaseException as exc:
            row.update(public_error(exc))
            if parser:
                row["events"] = parser.events
                row["completion_events_seen"] = len(parser.completed)
                if parser.completed and isinstance(parser.completed[0], dict):
                    row["response_id"] = parser.completed[0].get("id")
                    row["usage"] = parser.completed[0].get("usage")
            self.store.first_error({"name": name, **row})
            raise
        finally:
            row["seconds"] = round(time.monotonic() - started, 3)
            self.store.json(serial + ".result.json", row)
            self.save()

    def production_gates(self):
        require(AUTHORITY.is_file(), "AUTHORITY_RECEIPT_MISSING")
        check_authority(json.loads(AUTHORITY.read_text(encoding="utf-8-sig")))
        self.report["production_files_read"] += 1
        self.report["authority_gate"] = "PASS"
        status, _ = self.request("public-status-gate", "GET", "/api/status", auth=False)
        check_status(status, self.args.expected_version)
        self.report["public_gate"] = "PASS"
        # Reading the operator key is intentionally AFTER both independent gates.
        credentials = json.loads(KEY_FILE.read_text(encoding="utf-8-sig"))
        self.report["production_files_read"] += 1
        key = credentials.get("api_key")
        require(isinstance(key, str) and 20 <= len(key) <= 512 and not any(c.isspace() for c in key), "OPERATOR_API_KEY_INVALID")
        self.key = key
        self.gates_passed = True
        self.report["operator_key_read"] = True
        self.save()

    def response_body(self, prompt, *, stream=True, model=None, tokens=None):
        return {"model": model or self.args.model, "stream": stream, "store": False,
                "reasoning": {"effort": "low"}, "max_output_tokens": tokens or self.args.text_max_output_tokens,
                "input": [{"role": "user", "content": prompt}]}

    def exact_text(self, name, marker, *, stream=True, model=None):
        body = self.response_body("Reply exactly " + marker + ". No other text.", stream=stream, model=model)
        final, meta = self.request(name, "POST", "/v1/responses", body, sse=stream)
        require(output_text(final).strip() == marker, "EXACT_MARKER_MISMATCH")
        return {"response_id": final["id"], "usage": final["usage"], "delta_final_consistent": meta.get("delta_final_consistent"), "exact_marker_match": True}

    def websocket(self, name):
        from websockets.sync.client import connect
        probe = secrets.token_hex(16)
        headers = self.headers(probe, True)
        headers.update({"OpenAI-Beta": "responses_websockets=2026-02-06", "Originator": "codex_cli_rs"})
        marker = "RYWS" + secrets.token_hex(12).upper()
        result = {"same_socket_continuation": False, "handshake_status": None, "probe_id": probe, "turns": []}
        self.report["websocket_progress"] = result
        endpoint = BASE.replace("https://", "wss://") + "/responses?model=" + quote(self.args.model)
        self.report["network_requests_executed"] += 1
        try:
            ws = connect(endpoint, additional_headers=headers, ssl=self.ssl_context,
                         open_timeout=min(20, self.remaining()), close_timeout=min(5, self.remaining()),
                         max_size=MAX_BODY, proxy=self.args.proxy, compression=None)
        except Exception as exc:
            handshake = getattr(exc, "response", None)
            if handshake is not None:
                result.update(handshake_status=getattr(handshake, "status_code", None),
                              cf_ray=handshake.headers.get("cf-ray"),
                              request_id=handshake.headers.get("x-request-id") or handshake.headers.get("x-oneapi-request-id"))
                body = getattr(handshake, "body", b"")
                if isinstance(body, (bytes, bytearray)):
                    with (self.store.path / "websocket-handshake.body.private").open("xb") as raw:
                        raw.write(body[:MAX_BODY])
            result.update(status="FAIL", **public_error(exc))
            self.store.json("websocket-handshake-result.json", result)
            self.store.first_error({"name": name, **result})
            self.save()
            raise
        with ws:
            handshake = ws.response
            result.update(handshake_status=handshake.status_code,
                          cf_ray=handshake.headers.get("cf-ray"),
                          request_id=handshake.headers.get("x-request-id") or handshake.headers.get("x-oneapi-request-id"))
            require(handshake.status_code == 101, "WS_UPGRADE_NOT_101")
            self.store.json("websocket-handshake-result.json", {key: value for key, value in result.items() if key != "turns"})
            for turn in range(2):
                self.reserve()
                started = time.monotonic()
                audit = WebSocketAudit()
                body = self.response_body("Remember " + marker + ". Reply only ACK." if turn == 0 else "Repeat the remembered marker exactly. No other text.")
                body.pop("stream")
                body["type"] = "response.create"
                if turn:
                    body["previous_response_id"] = result["turns"][0]["response_id"]
                label = f"{name}-turn-{turn + 1}"
                self.store.json(label + ".request.private.json", body)
                row = {"name": label, "status": "FAIL", "http_status": 101, "probe_id": probe,
                       "cf_ray": result["cf_ray"], "request_id": result["request_id"], "bytes": 0}
                result["turns"].append(row)
                self.report["requests"].append(row)
                try:
                    with (self.store.path / (label + ".body.private")).open("xb") as raw:
                        ws.send(json.dumps(body))
                        deadline = min(self.deadline, time.monotonic() + self.args.request_timeout)
                        while True:
                            timeout = deadline - time.monotonic()
                            require(timeout > 0, "WS_TERMINAL_DEADLINE")
                            frame = ws.recv(timeout=timeout)
                            encoded = frame.encode("utf-8") if isinstance(frame, str) else frame
                            row["bytes"] += len(encoded)
                            require(row["bytes"] <= MAX_BODY, "WS_BODY_LIMIT")
                            raw.write(encoded + b"\n")
                            raw.flush()
                            event = json.loads(encoded)
                            audit.accept(event)
                            require(not audit.errors, "WS_FAILURE_EVENT")
                            if event.get("type") == "response.completed":
                                final, meta = audit.finish()
                                row.update(meta)
                                require(output_text(final).strip() == ("ACK" if turn == 0 else marker), "WS_CONTINUATION_MARKER_MISMATCH")
                                if turn:
                                    require(final["id"] != result["turns"][0]["response_id"], "WS_RESPONSE_ID_REUSED")
                                break
                    row["status"] = "PASS"
                except BaseException as exc:
                    row.update(public_error(exc), events=audit.events, completion_events_seen=len(audit.completed))
                    self.store.first_error(row)
                    raise
                finally:
                    row["seconds"] = round(time.monotonic() - started, 3)
                    self.store.json(label + ".result.json", row)
                    self.save()
            result["same_socket_continuation"] = True
        return result

    def run_case(self, name):
        marker = "RY" + secrets.token_hex(12).upper()
        if name == "catalog":
            value, _ = self.request(name, "GET", "/v1/models")
            return check_catalog(value)
        if name in ("responses-stream", "responses-nonstream"):
            return self.exact_text(name, marker, stream=name == "responses-stream")
        if name == "pdf-inline":
            data = pdf(marker)
            (self.store.path / "synthetic-marker.pdf").write_bytes(data)
            body = self.response_body([
                {"type": "input_file", "filename": "verification.pdf", "file_data": "data:application/pdf;base64," + base64.b64encode(data).decode()},
                {"type": "input_text", "text": "Read the attached PDF. Return only its verification code, exactly as printed."},
            ])
            final, _ = self.request(name, "POST", "/v1/responses", body, sse=True)
            require(output_text(final).strip() == marker, "PDF_RANDOM_MARKER_NOT_READ")
            return {"response_id": final["id"], "pdf_sha256": hashlib.sha256(data).hexdigest(), "exact_pdf_marker_match": True}
        if name == "web-search":
            body = self.response_body("Use web search to find the official OpenAI Codex CLI GitHub repository. Return its exact HTTPS URL and one brief fact with a source citation.", tokens=512)
            body.update(tools=[{"type": "web_search"}], tool_choice="required")
            final, _ = self.request(name, "POST", "/v1/responses", body, sse=True)
            return {"response_id": final["id"], **check_search(final)}
        if name == "image-generation":
            from PIL import Image
            body = self.response_body("Generate one colorful illustration of a small fluffy white Bichon Frise dog dressed as an adventurer fighting hilichurls in the world of Genshin Impact. One complete illustration, no text.")
            body.update(tools=[{"type": "image_generation", "quality": "low", "size": "1024x1024", "output_format": "png"}], tool_choice="required")
            final, _ = self.request(name, "POST", "/v1/responses", body, sse=True)
            calls = [item for item in final["output"] if item.get("type") == "image_generation_call"]
            require(len(calls) == 1 and calls[0].get("status") == "completed", "ONE_COMPLETED_IMAGE_CALL_REQUIRED")
            require(isinstance(calls[0].get("result"), str), "IMAGE_BASE64_MISSING")
            image_bytes = base64.b64decode(calls[0]["result"], validate=True)
            require(image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) <= MAX_BODY, "ACTUAL_PNG_REQUIRED")
            with Image.open(io.BytesIO(image_bytes)) as picture:
                require(picture.format == "PNG" and 256 <= picture.width <= 4096 and 256 <= picture.height <= 4096, "PNG_DIMENSIONS_INVALID")
                dimensions = [picture.width, picture.height]
                picture.verify()
            with Image.open(io.BytesIO(image_bytes)) as picture:
                picture.load()
            with (self.store.path / "bichon-genshin-hilichurls.png").open("xb") as output:
                output.write(image_bytes)
            return {"response_id": final["id"], "png_dimensions": dimensions, "png_bytes": len(image_bytes),
                    "png_sha256": hashlib.sha256(image_bytes).hexdigest(), "image_decode_passed": True,
                    "visual_prompt_match_review": "NOT_RUN"}
        if name == "websocket":
            return self.websocket(name)
        if name == "tool-roundtrip":
            history = [{"role": "user", "content": "Call read_marker then return only its result."}]
            tools = [{"type": "function", "name": "read_marker", "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False}, "strict": True}]
            body = self.response_body("")
            body.update(input=history, tools=tools, tool_choice={"type": "function", "name": "read_marker"}, include=["reasoning.encrypted_content"], parallel_tool_calls=False)
            first, _ = self.request(name + "-call", "POST", "/v1/responses", body, sse=True)
            calls = [item for item in first["output"] if item.get("type") == "function_call"]
            require(len(calls) == 1 and calls[0].get("name") == "read_marker" and calls[0].get("call_id"), "EXPECTED_FUNCTION_CALL_MISSING")
            require(json.loads(calls[0].get("arguments", "null")) == {}, "FUNCTION_ARGUMENTS_INVALID")
            history += first["output"]
            history.append({"type": "function_call_output", "call_id": calls[0]["call_id"], "output": marker})
            second_body = self.response_body("")
            second_body.update(input=history, tools=tools, tool_choice="none")
            final, _ = self.request(name + "-result", "POST", "/v1/responses", second_body, sse=True)
            require(output_text(final).strip() == marker, "FUNCTION_ROUNDTRIP_MARKER_MISMATCH")
            return {"response_ids": [first["id"], final["id"]], "actual_function_call": True, "function_result_used": True}
        if name == "all-text-models":
            rows = []
            for index, model in enumerate(TEXT_MODELS):
                row = {"model": model, "status": "FAIL"}
                try:
                    row.update(self.exact_text(f"{name}-{index + 1}", "RY" + secrets.token_hex(12).upper(), model=model))
                    row["status"] = "PASS"
                except Exception as exc:
                    row.update(public_error(exc))
                    rows.append(row)
                    if not self.args.continue_after_failure:
                        break
                else:
                    rows.append(row)
            self.report["all_text_model_results"] = rows
            require(len(rows) == 8 and all(row["status"] == "PASS" for row in rows), "ALL_EIGHT_TEXT_MODELS_NOT_PASSED")
            return {"models": rows}
        raise CheckFailure("UNKNOWN_CASE")

    def run(self):
        self.timer.start()
        self.save()
        try:
            self.production_gates()
            for name in self.report["cases_selected"]:
                started = time.monotonic()
                row = {"name": name, "status": "FAIL", "at": utcnow()}
                try:
                    row.update(self.run_case(name))
                    row["status"] = "PASS"
                except Exception as exc:
                    row.update(public_error(exc))
                    self.store.first_error(row)
                row["seconds"] = round(time.monotonic() - started, 3)
                self.report["cases"].append(row)
                self.store.json("case-" + name + ".json", row)
                self.save()
                print(json.dumps({k: row[k] for k in ("name", "status", "seconds", "error_type", "check_code") if k in row}), flush=True)
                if row["status"] != "PASS" and not self.args.continue_after_failure:
                    break
            passed = len(self.report["cases"]) == len(self.report["cases_selected"]) and all(row["status"] == "PASS" for row in self.report["cases"])
            self.report["status"] = "PASS" if passed else "FAIL"
        except BaseException as exc:
            self.report.update(status="FAIL", fatal_error=public_error(exc))
            self.store.first_error({"name": "production-gates-or-run", **public_error(exc), "at": utcnow()})
        finally:
            self.report["finished_at"] = utcnow()
            self.report["not_run_cases"] = [name for name in self.report["cases_selected"] if name not in {row["name"] for row in self.report["cases"]}]
            self.save()
            self.client.close()
            self.key = None
            self.timer.cancel()
        print(json.dumps({"status": self.report["status"], "cases_passed": sum(row["status"] == "PASS" for row in self.report["cases"]),
                          "cases_executed": len(self.report["cases"]), "model_requests_attempted": self.model_requests,
                          "ledger_reconciliation": "NOT_RUN"}), flush=True)
        return 0 if self.report["status"] == "PASS" else 1


def offline_self_test():
    """Pure parser/contract tests. No production file reads or network calls."""
    checked = 0

    def rejected(action, code):
        nonlocal checked
        try:
            action()
        except CheckFailure as exc:
            require(exc.args[0] == code, "SELF_TEST_WRONG_REJECTION")
            checked += 1
        else:
            raise CheckFailure("SELF_TEST_MISSING_REJECTION")

    final = {"object": "response", "id": "resp_offline", "status": "completed", "model": "offline",
             "output": [{"type": "message", "id": "msg_offline", "content": [{"type": "output_text", "text": "OK", "annotations": []}]}],
             "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
    events = [{"type": "response.created", "response": {"id": "resp_offline"}},
              {"type": "response.output_text.delta", "delta": "OK"},
              {"type": "response.output_item.done", "item": final["output"][0]},
              {"type": "response.completed", "response": final}]
    raw = b"".join(("data: " + json.dumps(event) + "\r\n\r\n").encode() for event in events)
    parser = SSEAudit()
    for offset in range(0, len(raw), 7):
        parser.feed(raw[offset:offset + 7])
    parser.feed(b"", final=True)
    require(parser.finish()[0] == final, "SELF_TEST_STREAM_FAILED")
    checked += 1
    rejected(lambda: ResponseAudit().finish(), "COMPLETED_EVENT_COUNT")
    compact_events = json.loads(json.dumps(events))
    compact_events[2]["output_index"] = 0
    compact_events[-1]["response"]["output"] = []
    ws = WebSocketAudit()
    for event in compact_events:
        ws.accept(event)
    assembled, meta = ws.finish()
    require(output_text(assembled) == "OK" and meta["compact_terminal_output"], "SELF_TEST_WS_COMPACT_FAILED")
    require(compact_events[-1]["response"]["output"] == [], "SELF_TEST_WS_RAW_TERMINAL_MUTATED")
    checked += 1
    ws.deltas = ["WRONG"]
    rejected(ws.finish, "DELTA_FINAL_TEXT_MISMATCH")
    ws.deltas = ["OK"]
    ws.completed_items = {}
    rejected(ws.finish, "WS_COMPACT_COMPLETED_ITEMS_MISSING")
    strict = ResponseAudit()
    for event in compact_events:
        strict.accept(event)
    rejected(strict.finish, "FINAL_OUTPUT_EMPTY")
    parser = ResponseAudit()
    for event in events:
        parser.accept(event)
    parser.deltas = ["WRONG"]
    rejected(parser.finish, "DELTA_FINAL_TEXT_MISMATCH")
    parser.deltas = ["OK"]
    parser.accept({"type": "response.failed"})
    rejected(parser.finish, "UPSTREAM_FAILURE_EVENT")
    parser = ResponseAudit()
    for event in events:
        parser.accept(event)
    parser.response_ids.add("resp_wrong")
    rejected(parser.finish, "EVENT_RESPONSE_ID_MISMATCH")
    parser.response_ids = {"resp_offline"}
    parser.done_ids.add("msg_lost")
    rejected(parser.finish, "COMPLETED_ITEM_LOST")
    parser = SSEAudit()
    rejected(lambda: parser.feed(b"data: {broken}\n\n"), "MALFORMED_SSE_JSON")
    check_authority({"phase": "ACTIVE", "opened": True})
    checked += 1
    rejected(lambda: check_authority({"phase": "OPENING", "opened": True}), "AUTHORITY_NOT_ACTIVE_OPENED")
    rejected(lambda: check_authority({"phase": "ACTIVE", "opened": False}), "AUTHORITY_NOT_ACTIVE_OPENED")
    status = {"success": True, "data": {"version": EXPECTED_VERSION, "engine": "sub2api", "single_core": True}}
    check_status(status)
    checked += 1
    status["data"]["version"] = "old"
    rejected(lambda: check_status(status), "PUBLIC_VERSION_MISMATCH")
    status["data"].update(version=EXPECTED_VERSION, single_core=False)
    rejected(lambda: check_status(status), "PUBLIC_ENGINE_NOT_SINGLECORE")
    check_catalog({"data": [{"id": name} for name in (*TEXT_MODELS, "gpt-image-2")]})
    checked += 1
    rejected(lambda: check_catalog({"data": []}), "PUBLISHED_TEXT_MODEL_MISSING")
    marker = "RY" + "A1" * 12
    document = pdf(marker)
    require(marker.encode() in document and document.endswith(b"%%EOF\n"), "SELF_TEST_PDF_FAILED")
    xref = int(document.split(b"startxref\n")[1].splitlines()[0])
    require(document[xref:xref + 4] == b"xref", "SELF_TEST_PDF_XREF_FAILED")
    checked += 1
    rejected(lambda: check_search(final), "ACTUAL_COMPLETED_SEARCH_MISSING")
    # Exercise the actual gate method with in-memory files and a fake status
    # request: neither rejection may even read an operator credential.
    class MemoryFile:
        def __init__(self, value):
            self.value, self.reads = value, 0

        def is_file(self):
            return True

        def read_text(self, **unused):
            self.reads += 1
            return json.dumps(self.value)

    global AUTHORITY, KEY_FILE
    original_authority, original_key = AUTHORITY, KEY_FILE
    AUTHORITY = MemoryFile({"phase": "OPENING", "opened": False})
    KEY_FILE = MemoryFile({"api_key": "TEST_ONLY_NONSECRET_OPERATOR_VALUE"})
    harness = Acceptance.__new__(Acceptance)
    harness.args = SimpleNamespace(expected_version=EXPECTED_VERSION)
    harness.report = {"production_files_read": 0}
    public = {"success": True, "data": {"version": EXPECTED_VERSION, "engine": "sub2api", "single_core": False}}
    harness.request = lambda *args, **kwargs: (public, {})
    harness.save = lambda: None
    try:
        rejected(harness.production_gates, "AUTHORITY_NOT_ACTIVE_OPENED")
        require(KEY_FILE.reads == 0, "SELF_TEST_KEY_READ_BEFORE_AUTHORITY")
        AUTHORITY.value = {"phase": "ACTIVE", "opened": True}
        rejected(harness.production_gates, "PUBLIC_ENGINE_NOT_SINGLECORE")
        require(KEY_FILE.reads == 0, "SELF_TEST_KEY_READ_BEFORE_PUBLIC_GATE")
        public["data"]["single_core"] = True
        harness.args.expected_version = "realyu-singlecore-different-review"
        rejected(harness.production_gates, "PUBLIC_VERSION_MISMATCH")
        require(KEY_FILE.reads == 0, "SELF_TEST_KEY_READ_BEFORE_EXACT_VERSION")
        harness.args.expected_version = EXPECTED_VERSION
        harness.production_gates()
        require(KEY_FILE.reads == 1 and harness.gates_passed and harness.report["operator_key_read"], "SELF_TEST_GATED_KEY_READ_FAILED")
        checked += 1
    finally:
        AUTHORITY, KEY_FILE = original_authority, original_key
    return {"status": "PASS", "checks": checked, "network_calls": 0, "production_files_read": 0, "files_written": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path, help="Fresh private output under this runtime; never reused")
    parser.add_argument("--cases", default=DEFAULT_CASES, help=",".join(CASES))
    parser.add_argument("--model", default="gpt-5.6-sol", choices=TEXT_MODELS)
    parser.add_argument("--expected-version", default=EXPECTED_VERSION, help="Exact reviewed native version; never accepts any-version health")
    parser.add_argument("--proxy", choices=(SG_PROXY,), default=None, help="Omit for direct access; ambient proxy settings are always ignored")
    parser.add_argument("--budget-seconds", type=int, default=600)
    parser.add_argument("--request-timeout", type=int, default=180)
    parser.add_argument("--text-max-output-tokens", type=int, choices=(128, 256, 512), default=256)
    parser.add_argument("--continue-after-failure", action="store_true", help="Default stops after first failed case; requests are never retried")
    parser.add_argument("--self-test", action="store_true", help="Run only offline parser/contracts; incompatible with --run")
    parser.add_argument("--run", action="store_true", help="Execute only after ACTIVE and exact public single-core gates")
    args = parser.parse_args()
    require(re.fullmatch(r"realyu-singlecore-[a-zA-Z0-9._-]{5,100}", args.expected_version), "EXPECTED_NATIVE_VERSION_INVALID")
    selected = args.cases.split(",")
    require(bool(selected) and all(case in CASES for case in selected), "UNKNOWN_CASE_SELECTION")
    require(len(selected) == len(set(selected)), "DUPLICATE_CASE_SELECTION")
    require(60 <= args.budget_seconds <= 1800 and 10 <= args.request_timeout <= 300, "BOUNDED_TIMEOUT_REQUIRED")
    require(not (args.run and args.self_test), "SELF_TEST_CANNOT_RUN_NETWORK")
    require(args.output.is_absolute(), "ABSOLUTE_OUTPUT_REQUIRED")
    args.output = args.output.resolve()
    require(args.output != ROOT and args.output.is_relative_to(ROOT), "OUTPUT_MUST_BE_UNDER_RUNTIME")
    require(not args.output.exists(), "FRESH_OUTPUT_REQUIRED")
    versions = {}
    dependencies = ["httpx"] + (["websockets"] if "websocket" in selected else []) + (["Pillow"] if "image-generation" in selected else [])
    for dependency in dependencies:
        versions[dependency] = importlib.metadata.version(dependency)
    plan = {"status": "PREPARED", "target": BASE, "expected_version": args.expected_version,
            "proxy": args.proxy, "trust_ambient_proxy": False, "tls_verification": True,
            "cases_selected": selected, "model": args.model, "dependencies": versions,
            "maximum_model_requests": sum(REQUEST_COUNTS[case] for case in selected),
            "maximum_http_requests": 1 + (1 if "catalog" in selected else 0) + sum(REQUEST_COUNTS[case] for case in selected if case != "websocket"),
            "websocket_connections": 1 if "websocket" in selected else 0, "automatic_retries": 0,
            "wall_budget_seconds": args.budget_seconds, "request_timeout_seconds": args.request_timeout,
            "text_max_output_tokens": args.text_max_output_tokens, "search_max_output_tokens": 512,
            "image_requests_maximum": 1 if "image-generation" in selected else 0,
            "network_requests_executed": 0, "production_files_read": 0,
            "ledger_reconciliation": "NOT_RUN", "browser_or_codex_desktop_e2e": "NOT_RUN",
            "cross_subject_authorization": "NOT_RUN", "long_term_stability": "NOT_PROVEN",
            "image_visual_review": "NOT_RUN", "operator_key_read": False}
    if not args.run:
        if args.self_test:
            # Reject accidental socket use even if a future self-test changes.
            def denied(*unused_args, **unused_kwargs):
                raise CheckFailure("OFFLINE_NETWORK_FORBIDDEN")
            original_socket, original_connect, original_dns = socket.socket, socket.create_connection, socket.getaddrinfo
            socket.socket = socket.create_connection = socket.getaddrinfo = denied
            try:
                plan["offline_self_test"] = offline_self_test()
            finally:
                socket.socket, socket.create_connection, socket.getaddrinfo = original_socket, original_connect, original_dns
        print(json.dumps(plan, indent=2))
        return 0
    store = PrivateStore(args.output)
    store.create()
    return Acceptance(args, plan, store).run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(json.dumps({"status": "FAIL", **public_error(exc)}), file=sys.stderr, flush=True)
        sys.exit(1)

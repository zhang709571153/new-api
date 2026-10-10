"""Prepare an offline, private SG-HY2 service package. Never install or switch it.

The desktop profile is input only. Only the single reviewed Hysteria2 node is
copied; no provider URLs, desktop selection state, TUN or system proxy settings.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import subprocess
import sys
import uuid
import xml.etree.ElementTree as ET

PORTS = (17897, 17898, 19464, 19465, 19466, 19467)
EDGES = ("198.41.192.107", "198.41.200.13", "198.41.192.47", "198.41.200.43")
NODE = "RealYu-SG-HY2"
SERVICES = ("RealYuSgHy2", "RealYuSgHy2EdgeProxy")
MIHOMO_SHA256 = "0b54ea7b10e26f6ca628b49e77c13ad9b2587b23393a5b715769cbaeed012af6"


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def node_config(profile: dict, secret: str) -> dict:
    nodes = [n for n in profile.get("proxies", []) if n.get("name") == NODE]
    if len(nodes) != 1:
        raise ValueError("The reviewed SG-HY2 node must occur exactly once")
    node = nodes[0]
    allowed = {"name", "type", "server", "port", "password", "sni",
               "skip-cert-verify", "fingerprint", "alpn"}
    if set(node) != allowed:
        raise ValueError("Node fields changed; review the source profile first")
    if (node["type"] != "hysteria2" or node["skip-cert-verify"] is not False
            or node["alpn"] != ["h3"] or not node["sni"]
            or node["port"] != 443 or node["server"] != "43.160.230.142"):
        raise ValueError("The node does not match the reviewed SG-HY2 TLS contract")
    ipaddress.ip_address(node["server"])
    pin = str(node["fingerprint"]).replace(":", "")
    if len(pin) != 64 or any(ch not in "0123456789abcdefABCDEF" for ch in pin):
        raise ValueError("An SHA256 certificate pin is required")
    if not isinstance(node["password"], str) or not node["password"]:
        raise ValueError("An existing node credential is required")
    if len(secret) < 32:
        raise ValueError("A fresh controller secret is required")
    return {
        "mixed-port": PORTS[0], "allow-lan": False, "bind-address": "127.0.0.1",
        "external-controller": f"127.0.0.1:{PORTS[1]}", "secret": secret,
        "mode": "rule", "log-level": "info", "ipv6": False,
        "tun": {"enable": False}, "profile": {"store-selected": False},
        "proxies": [dict(node)],
        "proxy-groups": [{"name": "REALYU", "type": "select", "proxies": [NODE]}],
        "rules": ["MATCH,REALYU"],
    }


def yaml_text(config: dict) -> str:
    # JSON flow values are valid YAML. Top-level scalar lines remain compatible
    # with the already-deployed read-only path observer.
    return "\n".join(f"{key}: {json.dumps(value, ensure_ascii=False)}"
                     for key, value in config.items()) + "\n"


def service_xml(name: str, install_root: Path) -> bytes:
    root = ET.Element("service")
    is_core = name == SERVICES[0]
    for key, value in {
        "id": name, "name": name,
        "description": "RealYu dedicated SG-HY2 loopback proxy" if is_core else
                       "RealYu loopback Cloudflare edge forwarding through dedicated SG-HY2",
        "executable": str(install_root / "bin" / ("mihomo.exe" if is_core else "gost.exe")),
        "arguments": (f'-d "{install_root / "state"}" -f "{install_root / "config.yaml"}"'
                      if is_core else " ".join(
                          [f"-L tcp://127.0.0.1:{port}/{edge}:7844" for port, edge in zip(PORTS[2:], EDGES)]
                          + [f"-F socks5://127.0.0.1:{PORTS[0]}"])),
        "workingdirectory": str(install_root), "startmode": "Automatic",
        "stoptimeout": "20 sec", "logpath": str(install_root / "logs" / name),
    }.items():
        ET.SubElement(root, key).text = value
    ET.SubElement(root, "delayedAutoStart")
    if not is_core:
        ET.SubElement(root, "depend").text = SERVICES[0]
    log = ET.SubElement(root, "log", {"mode": "roll-by-size"})
    ET.SubElement(log, "sizeThreshold").text = "10240"
    ET.SubElement(log, "keepFiles").text = "8"
    for delay in ("5 sec", "15 sec", "60 sec"):
        ET.SubElement(root, "onfailure", {"action": "restart", "delay": delay})
    ET.SubElement(root, "resetfailure").text = "1 hour"
    account = ET.SubElement(root, "serviceaccount")
    ET.SubElement(account, "domain").text = "NT AUTHORITY"
    ET.SubElement(account, "user").text = "LocalService"
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def prepare(args: argparse.Namespace) -> dict:
    import yaml  # Preparation only; services do not depend on Python or PyYAML.

    operation = Path(args.operation_root).resolve()
    install = Path(args.install_root).resolve()
    expected_parent = Path(os.environ["ProgramData"]) / "RealYuNetwork"
    if install.parent != expected_parent or install.name != "sg-hy2-20261010":
        raise ValueError("Unexpected installation directory")
    if operation.exists() or install.exists():
        raise ValueError("Use a new private operation directory and unused install target")
    source = Path(args.source_profile).resolve()
    node = node_config(yaml.safe_load(source.read_text("utf-8-sig")), secrets.token_urlsafe(48))
    binaries = {"bin/mihomo.exe": Path(args.mihomo).resolve(),
                "bin/gost.exe": Path(args.gost).resolve()}
    if sha256(binaries["bin/mihomo.exe"]) != MIHOMO_SHA256:
        raise ValueError("Mihomo binary differs from the recorded official v1.19.32 artifact")
    for port in PORTS:
        with socket.socket() as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            listener.bind(("127.0.0.1", port))
    manifest_path = Path(args.manifest).resolve()
    manifest = json.loads(manifest_path.read_text("utf-8-sig"))
    if tuple(manifest["edge_proxy"]["targets"]) != EDGES:
        raise ValueError("Production edge targets changed; re-review before preparing")
    services_root = manifest_path.parent
    source_xml = {role: services_root / (service + ".xml") for role, service in
                  (("tunnel-primary", "RealYuTunnelPrimary"), ("tunnel-replica", "RealYuTunnelReplica"))}
    script_names = ("SgHy2.Common.ps1", "Install-SgHy2Service.ps1", "Switch-SgHy2Tunnel.ps1", "Set-SgHy2Monitoring.ps1")
    verified = {str(Path(__file__).parent / name): sha256(Path(__file__).parent / name)
                for name in script_names}
    # Restrict the directory before writing any node credential. No inheritance
    # from the repository or the desktop profile is carried into this package.
    operation.mkdir(parents=True)
    sid = args.operator_sid
    if not sid.startswith("S-1-5-") or any(c not in "S0123456789-" for c in sid):
        raise ValueError("Invalid operator SID")
    acl = subprocess.run(["icacls.exe", str(operation), "/inheritance:r", "/grant:r",
                          f"*{sid}:(OI)(CI)F", "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F"],
                         capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    if acl.returncode:
        raise RuntimeError("Cannot protect the private operation directory")
    package = operation / "package"
    (package / "bin").mkdir(parents=True)
    files = {}
    for relative, original in binaries.items():
        destination = package / relative
        shutil.copyfile(original, destination)
        files[relative] = sha256(destination)
    for name in SERVICES:
        shutil.copyfile(args.winsw, package / (name + ".exe"))
        (package / (name + ".xml")).write_bytes(service_xml(name, install))
        for suffix in (".exe", ".xml"):
            files[name + suffix] = sha256(package / (name + suffix))
    (package / "config.yaml").write_text(yaml_text(node), encoding="utf-8")
    files["config.yaml"] = sha256(package / "config.yaml")
    monitor = operation / "monitor"
    monitor.mkdir()
    guard_path = Path(args.guard_config).resolve()
    guard = json.loads(guard_path.read_text("utf-8-sig"))
    guard.update(proxy="http://127.0.0.1:17897", proxy_config_path=str(install / "config.yaml"))
    staged_guard = monitor / "guard-config.json"
    staged_guard.write_text(json.dumps(guard, indent=2), encoding="utf-8")
    monitor_inputs = [{"name": "guard-config", "target": str(guard_path),
                       "before_sha256": sha256(guard_path), "staged_file": str(staged_guard),
                       "after_sha256": sha256(staged_guard)}]
    for name, target in (("collector-live", Path(manifest["observability"]) / "collect_evidence.py"),
                         ("collector-guard", guard_path.parent / "collect_evidence.py")):
        content = target.read_text("utf-8-sig")
        old = "'http://127.0.0.1:7890'"
        if content.count(old) != 1:
            raise ValueError("Collector explicit proxy changed; review the deployed collector first")
        staged = monitor / (name + ".py")
        staged.write_text(content.replace(old, "'http://127.0.0.1:17897'"), encoding="utf-8")
        monitor_inputs.append({"name": name, "target": str(target.resolve()),
                               "before_sha256": sha256(target), "staged_file": str(staged),
                               "after_sha256": sha256(staged)})
    plan = {
        "schema": 1, "id": str(uuid.uuid4()), "prepared_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "operation_root": str(operation), "package_root": str(package), "install_root": str(install),
        "operator_sid": sid, "files": files, "verified_scripts": verified,
        "ports": list(PORTS), "edges": list(EDGES), "services": list(SERVICES),
        "manifest_path": str(manifest_path), "manifest_sha256": sha256(manifest_path),
        "tunnel_xml": {role: {"path": str(path), "sha256": sha256(path)} for role, path in source_xml.items()},
        "guard_config": str(guard_path), "guard_config_sha256": sha256(guard_path),
        "monitor_inputs": monitor_inputs, "observability_root": manifest["observability"],
        "production_changed": False,
    }
    plan_path = operation / "plan.json"
    plan_path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    return {"plan": str(plan_path), "plan_sha256": sha256(plan_path),
            "service_names": list(SERVICES), "ports": list(PORTS), "production_changed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-profile", "mihomo", "winsw", "gost", "operation-root", "install-root",
                 "manifest", "guard-config", "operator-sid"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(prepare(args)))
    except Exception as exc:
        # Parser/runtime failures can contain credential-bearing values. Never
        # print a source profile, YAML parser context or subprocess output.
        print(json.dumps({"status": "FAILED", "error_type": type(exc).__name__,
                          "production_changed": False}), file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()

import copy
import importlib.util
from pathlib import Path
import unittest
import xml.etree.ElementTree as ET

spec = importlib.util.spec_from_file_location("sg_hy2_package", Path(__file__).with_name("sg_hy2_package.py"))
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class SgHy2PackageContractTests(unittest.TestCase):
    def setUp(self):
        self.profile = {"mixed-port": 7897, "tun": {"enable": True},
                        "proxy-providers": {"not-copied": {"url": "https://invalid/subscription"}},
                        "proxies": [{"name": "RealYu-SG-HY2", "type": "hysteria2",
                                     "server": "43.160.230.142", "port": 443,
                                     "password": "test:secret#not-real", "sni": "realyu-hy2.invalid",
                                     "skip-cert-verify": False, "fingerprint": "aa" * 32,
                                     "alpn": ["h3"]}]}

    def test_profile_is_reduced_and_tls_verification_is_required(self):
        config = package.node_config(self.profile, "C" * 48)
        self.assertEqual(config["mixed-port"], 17897)
        self.assertFalse(config["tun"]["enable"])
        self.assertFalse(config["allow-lan"])
        self.assertEqual(config["rules"], ["MATCH,REALYU"])
        self.assertEqual(config["proxy-groups"][0]["proxies"], ["RealYu-SG-HY2"])
        self.assertNotIn("proxy-providers", config)
        self.assertEqual(self.profile["mixed-port"], 7897)
        for field, value in (("skip-cert-verify", True), ("fingerprint", ""),
                             ("server", "127.0.0.1"), ("dialer-proxy", "DIRECT"),
                             ("password", ""), ("alpn", ["h2"])):
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.profile)
                invalid["proxies"][0][field] = value
                with self.assertRaises(ValueError):
                    package.node_config(invalid, "C" * 48)

    def test_yaml_roundtrip_and_existing_observer_scalars(self):
        import json
        import re
        import yaml
        config = package.node_config(self.profile, "C" * 48)
        serialized = package.yaml_text(config)
        self.assertEqual(yaml.safe_load(serialized), config)
        for field in ("external-controller", "secret"):
            match = re.search(r"^" + field + r":\s*(.*?)\s*$", serialized, re.M)
            self.assertIsNotNone(match)
            self.assertEqual(json.loads(match[1]), config[field])
        self.profile["proxies"].append(copy.deepcopy(self.profile["proxies"][0]))
        with self.assertRaises(ValueError):
            package.node_config(self.profile, "C" * 48)

    def test_service_has_no_desktop_or_secret_command_dependency(self):
        install = Path(r"C:\ProgramData\RealYuNetwork\sg-hy2-20261010")
        core = ET.fromstring(package.service_xml(package.SERVICES[0], install))
        edge = ET.fromstring(package.service_xml(package.SERVICES[1], install))
        for service in (core, edge):
            self.assertEqual(service.findtext("startmode"), "Automatic")
            self.assertEqual(service.findtext("serviceaccount/user"), "LocalService")
            self.assertNotIn("AppData", service.findtext("executable"))
            self.assertNotIn("test:secret", service.findtext("arguments"))
            self.assertEqual(service.find("onfailure").get("action"), "restart")
            self.assertIsNone(service.find("interactive"))
        self.assertEqual(edge.findtext("depend"), "RealYuSgHy2")
        self.assertIn("socks5://127.0.0.1:17897", edge.findtext("arguments"))
        for port in range(19464, 19468):
            self.assertIn(f"tcp://127.0.0.1:{port}/", edge.findtext("arguments"))


if __name__ == "__main__":
    unittest.main()

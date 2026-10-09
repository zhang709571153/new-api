"""Windows-only packager safety tests; no service installation or process start.

Set WINSW_TEST_EXE to the reviewed official WinSW v2.12.0 x64 download. The same
file stands in for the two application binaries: these tests validate packaging,
not executable compatibility or real Sub2API behavior.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

HERE = Path(__file__).resolve().parent
WINSW_SHA = '05b82d46ad331cc16bdc00de5c6332c1ef818df8ceefcd49c726553209b3a0da'


@unittest.skipUnless(os.name == 'nt', 'Windows ACL and PowerShell test')
class NativeCandidateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.winsw = Path(os.environ.get('WINSW_TEST_EXE', HERE / '.private/WinSW-x64-v2.12.0.exe'))
        if not cls.winsw.is_file():
            raise unittest.SkipTest('Supply the official WinSW binary through WINSW_TEST_EXE')
        cls.ps = Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='realyu-native-package-test-')
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name).resolve()
        self.root = self.base / 'candidates/case01'
        self.cfg = json.loads((HERE / 'native-windows.example.json').read_text())
        self.cfg['candidate_version'] = 'realyu-sub2api-candidate-test01'
        for role in ('realyu', 'sub2api', 'prewarm', 'winsw'):
            self.cfg['binaries'][role] = {'path': str(self.winsw), 'sha256': WINSW_SHA}
        for field in ('SESSION_SECRET', 'CRYPTO_SECRET'):
            self.cfg['realyu'][field] = 'fixture-only-' + 'a' * 48
        for field in ('DATABASE_PASSWORD', 'REDIS_PASSWORD', 'ADMIN_PASSWORD', 'JWT_SECRET', 'TOTP_ENCRYPTION_KEY'):
            self.cfg['sub2api'][field] = 'fixture-only-' + 'b' * 48
        self.cfg['sub2api']['TOTP_ENCRYPTION_KEY'] = 'c' * 64
        self.cfg['redis_supply'] = 'isolated-test-only'

    def run_packager(self, config=None, root=None, validate=False):
        path = self.base / 'private-config.json'
        path.write_text(json.dumps(config or self.cfg), encoding='utf-8')
        command = [str(self.ps), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                   str(HERE / 'New-NativeCandidate.ps1'), '-PrivateConfig', str(path),
                   '-CandidateRoot', str(root or self.root)]
        if validate:
            command.append('-ValidateOnly')
        return subprocess.run(command, capture_output=True, text=True, timeout=45,
                              creationflags=subprocess.CREATE_NO_WINDOW)

    def test_validation_makes_no_directory(self):
        self.cfg['redis_supply'] = 'community-windows-candidate'
        result = self.run_packager(validate=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)['valid'])
        self.assertFalse(self.root.exists())

    def test_generated_loopback_manual_services_and_secret_acl(self):
        result = self.run_packager()
        self.assertEqual(result.returncode, 0, result.stderr)
        marker = json.loads((self.root / 'candidate.json').read_text(encoding='utf-8-sig'))
        self.assertFalse(marker['services_installed'])
        self.assertFalse(marker['services_started'])
        self.assertTrue((self.root / 'bin/sub2api-prewarm.exe').is_file())
        self.assertFalse((self.root / 'state/prewarm/progress.json').exists())
        for name in ('realyu-service', 'sub2api-service', 'prewarm-service'):
            tree = ET.parse(self.root / f'services/{name}.xml')
            self.assertEqual(tree.findtext('startmode'), 'Manual')
            self.assertEqual(tree.findtext('serviceaccount/user'), 'LocalService')
            self.assertEqual(tree.findtext('log/keepFiles'), '5')
            env = {entry.attrib['name']: entry.attrib['value'] for entry in tree.findall('env')}
            if name == 'realyu-service':
                self.assertEqual(env['BIND_ADDRESS'], '127.0.0.1')
                self.assertEqual(env['REALYU_UPSTREAM_DRIVER'], '')
                self.assertTrue(env['SQLITE_PATH'].startswith(str(self.root / 'state/realyu/new-api.db')))
                self.assertIn('_pragma=busy_timeout(30000)&_pragma=journal_mode(WAL)', env['SQLITE_PATH'])
                self.assertNotIn('_busy_timeout=', env['SQLITE_PATH'])
                self.assertEqual(env['HTTPS_PROXY'], '')
                self.assertIn('127.0.0.1', env['NO_PROXY'].split(','))
            elif name == 'sub2api-service':
                self.assertEqual(env['SERVER_HOST'], '127.0.0.1')
                self.assertEqual(env['RUN_MODE'], 'standard')
                self.assertEqual(env['TOKEN_REFRESH_ENABLED'], 'false')
                self.assertEqual(env['IDEMPOTENCY_DEFAULT_TTL_SECONDS'], '86400')
            else:
                self.assertEqual(tree.findtext('arguments'), '--watch')
                self.assertEqual(env['REALYU_SUB2API_QUEUE_DIR'], str(self.root / 'state/enrollment-queue'))
                self.assertEqual(env['REALYU_SUB2API_STATE_DIR'], str(self.root / 'state/prewarm'))
                self.assertNotIn('SQLITE_PATH', env)
                self.assertNotIn('SQL_DSN', env)
        # An ACL check prints only permission metadata, never file contents.
        script = "Import-Module (Join-Path $PSHOME 'Modules/Microsoft.PowerShell.Security/Microsoft.PowerShell.Security.psd1'); $a=Get-Acl -LiteralPath $args[0]; [pscustomobject]@{protected=$a.AreAccessRulesProtected;sids=@($a.Access | ForEach-Object {$_.IdentityReference.Translate([Security.Principal.SecurityIdentifier]).Value})} | ConvertTo-Json"
        check = self.base / 'check-acl.ps1'
        check.write_text(script, encoding='utf-8')
        acl = subprocess.run([str(self.ps), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(check), str(self.root)],
                             capture_output=True, text=True, timeout=20, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertEqual(acl.returncode, 0, acl.stderr)
        info = json.loads(acl.stdout)
        self.assertTrue(info['protected'])
        self.assertNotIn('S-1-1-0', info['sids'])  # Everyone
        self.assertNotIn('S-1-5-32-545', info['sids'])  # Users
        self.assertIn('S-1-5-19', info['sids'])  # LocalService
        again = self.run_packager()
        self.assertNotEqual(again.returncode, 0)
        self.assertIn('already exists', again.stderr)

    def test_rejects_production_path_and_reserved_port(self):
        for root in ('C:/srv/candidates/test', 'C:/RealYu/releases/test'):
            result = self.run_packager(root=root, validate=True)
            self.assertNotEqual(result.returncode, 0)
        bad = copy.deepcopy(self.cfg)
        bad['ports']['realyu'] = 18300
        self.assertNotEqual(self.run_packager(bad, validate=True).returncode, 0)

    def test_rejects_macro_bad_hash_and_remote_plaintext(self):
        for section, key, value in (
            ('realyu', 'SESSION_SECRET', '%PATH%' + 'a' * 40),
            ('sub2api', 'REDIS_HOST', 'redis.example.test'),
            ('sub2api', 'DATABASE_HOST', 'postgres.example.test'),
        ):
            bad = copy.deepcopy(self.cfg)
            bad[section][key] = value
            result = self.run_packager(bad, validate=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(value, result.stdout + result.stderr)
        bad = copy.deepcopy(self.cfg)
        bad['binaries']['realyu']['sha256'] = '0' * 64
        self.assertNotEqual(self.run_packager(bad, validate=True).returncode, 0)

    def test_bad_totp_and_malformed_json_never_expose_input(self):
        bad = copy.deepcopy(self.cfg)
        bad['sub2api']['TOTP_ENCRYPTION_KEY'] = 'SENTINEL-NOT-HEX-SECRET-' + 'x' * 48
        result = self.run_packager(bad, validate=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(bad['sub2api']['TOTP_ENCRYPTION_KEY'], result.stdout + result.stderr)
        path = self.base / 'malformed.json'
        sentinel = 'PRIVATE-MALFORMED-SECRET-MUST-NOT-LEAK'
        path.write_text('{"secret": "' + sentinel + '", broken}', encoding='utf-8')
        result = subprocess.run([str(self.ps), '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
                                 str(HERE / 'New-NativeCandidate.ps1'), '-PrivateConfig', str(path),
                                 '-CandidateRoot', str(self.root), '-ValidateOnly'],
                                capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn(sentinel, result.stdout + result.stderr)

    def test_proxy_configuration_uses_explicit_fields_and_keeps_loopback_direct(self):
        self.cfg['realyu']['HTTPS_PROXY'] = 'http://proxy.example.test:3128'
        self.cfg['realyu']['NO_PROXY'] = 'localhost,127.0.0.1,::1,internal.example.test'
        result = self.run_packager(validate=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        for key, value in [('HTTPS_PROXY', 'file:///private-proxy-secret'), ('NO_PROXY', 'internal.example.test')]:
            bad = copy.deepcopy(self.cfg)
            bad['realyu'][key] = value
            result = self.run_packager(bad, validate=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertNotIn(value, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()

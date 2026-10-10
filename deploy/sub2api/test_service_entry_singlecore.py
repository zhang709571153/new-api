import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import service_entry_singlecore as launcher


class NativeLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.binary = self.root / 'native.exe'
        self.binary.write_bytes(b'synthetic executable')
        self.env = {
            'SERVER_HOST':'127.0.0.1','SERVER_PORT':'18300',
            'DATABASE_HOST':'127.0.0.1','DATABASE_PORT':'28490',
            'DATABASE_DBNAME':'realyu_singlecore_20261010_candidate',
            'DATABASE_USER':'realyu_singlecore_owner_20261010',
            'REDIS_HOST':'127.0.0.1','REDIS_PORT':'28391','REDIS_DB':'1',
            'REALYU_FUNDING_ENABLED':'true','REALYU_WS_FUNDING_ENABLED':'true',
            'DATABASE_PASSWORD':'synthetic','REDIS_PASSWORD':'synthetic',
            'JWT_SECRET':'synthetic','REALYU_LEGACY_IDENTITY_SECRET':'synthetic',
            'REALYU_LEGACY_NAMESPACE':'synthetic','TOKEN_REFRESH_ENABLED':'false',
            'HTTP_PROXY':'http://127.0.0.1:17897'}
        self.env_path = self.root / 'private-env.json'
        self.config={'release':str(self.root),'observability':str(self.root),
                     'singlecore_api':{'exe':str(self.binary),'env_file':str(self.env_path),
                                      'working_dir':str(self.root),'sha':hashlib.sha256(self.binary.read_bytes()).hexdigest()}}

    def tearDown(self): self.tmp.cleanup()
    def run_plan(self):
        self.env_path.write_text(json.dumps(self.env))
        return launcher.plan('api',self.config,self.root)

    def test_native_does_not_open_legacy_credentials(self):
        args,env,cwd,pid=self.run_plan()
        self.assertEqual(args,[str(self.binary)])
        self.assertEqual(env['REDIS_DB'],'1')
        self.assertEqual(env['HTTP_PROXY'],'http://127.0.0.1:17897')
        self.assertEqual(pid,cwd/'singlecore-api.pid')
        self.assertFalse((self.root/'.lab'/'credentials.json').exists())

    def test_tampered_executable_refused(self):
        self.binary.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.run_plan()

    def test_invalid_native_entry_never_falls_back_to_old_authority(self):
        for value in ({},None,False):
            with self.subTest(value=value):
                self.config['singlecore_api']=value
                with self.assertRaises(ValueError):self.run_plan()

    def test_wrong_authority_or_listener_refused(self):
        for key,value in [('SERVER_HOST','0.0.0.0'),('DATABASE_DBNAME','sub2api_production'),('REDIS_DB','0'),('REALYU_WS_FUNDING_ENABLED','false')]:
            with self.subTest(key=key):
                original=self.env[key];self.env[key]=value
                with self.assertRaises(ValueError):self.run_plan()
                self.env[key]=original

    def test_inherited_interpreter_override_refused(self):
        self.env['PYTHONPATH']='synthetic'
        with self.assertRaises(ValueError):self.run_plan()

    def test_bridge_remains_original_worker(self):
        private=self.root/'.lab';private.mkdir()
        args,env,cwd,pid=launcher.plan('bridge',self.config,self.root)
        self.assertEqual(args[-1],str(self.root/'lab'/'image_bridge.py'))
        self.assertEqual(env['REALYU_BACKEND_PORT'],'18300')
        self.assertEqual(env['NEWAPI_PORT'],'18300')
        self.assertEqual(env['BRIDGE_PORT'],'18301')
        self.assertEqual(pid,private/'bridge.pid')
        self.assertFalse((private/'credentials.json').exists())
        self.assertFalse((private/'old.exe').exists())

if __name__=='__main__':unittest.main()

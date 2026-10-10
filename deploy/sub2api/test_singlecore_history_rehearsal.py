import copy
from datetime import datetime,timezone,timedelta
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import singlecore_history_rehearsal as r

class RehearsalGuard(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.dump=self.root/'test.dump';self.dump.write_bytes(b'SYNTHETIC-DUMP')
        self.schema=self.root/'schema.sql';self.schema.write_bytes(b'SYNTHETIC-SCHEMA')
        self.private=self.root/'isolated.json';self.private.write_text(json.dumps(dict(host='127.0.0.1',port=29490,user='fixture',password='PRIVATE-SENTINEL')))
        now=datetime.now(timezone.utc).isoformat()
        self.data=dict(version=1,kind='singlecore-pg-snapshot-restore',status='PASS',captured_at=now,
             source={**r.backup.SOURCE,'owner':r.backup.SOURCE['user']},schema_migrations=[],
             backup=dict(path=str(self.dump),size_bytes=self.dump.stat().st_size,sha256=r.backup.sha256(self.dump),format='custom'),
             schema_dump=dict(path=str(self.schema),sha256=r.backup.sha256(self.schema)),
             restore=dict(status='PASS',host='127.0.0.1',port=29490,database='realyu_backup_verify_abcdefgh',dump_sha256=r.backup.sha256(self.dump),verified_at=now,row_hashes_match=True,schema_hash_match=True,migrations_match=True))
    def invoke(self,data=None,pin=None):
        path=self.root/'receipt.json';path.write_text(json.dumps(data or self.data))
        with patch.object(r.baseline,'verify_migrations') as verify:
            result=r.receipt_target(path,pin or r.backup.sha256(path),self.private,self.root)
            verify.assert_called_once()
            return result
    def test_exact_verified_receipt_allows_only_named_restore(self):
        receipt,cfg=self.invoke();self.assertEqual(cfg['database'],receipt['restore']['database']);self.assertEqual(29490,cfg['port'])
    def test_receipt_pin_and_actual_dump_and_schema_are_checked(self):
        with self.assertRaisesRegex(r.backup.Refused,'PIN_MISMATCH'):self.invoke(pin='0'*64)
        self.dump.write_bytes(b'CHANGED')
        with self.assertRaisesRegex(r.backup.Refused,'DUMP_PIN'):self.invoke()
        self.dump.write_bytes(b'SYNTHETIC-DUMP');self.schema.write_bytes(b'CHANGED')
        with self.assertRaisesRegex(r.backup.Refused,'SCHEMA_DUMP'):self.invoke()
    def test_production_or_other_fixture_target_and_failed_proof_denied(self):
        for field,value in [('host','remote'),('port',28490),('database','realyu_singlecore_e2e'),('status','FAIL'),('row_hashes_match',False),('schema_hash_match',False),('migrations_match',False),('dump_sha256','0'*64)]:
            d=copy.deepcopy(self.data);d['restore'][field]=value
            with self.subTest(field=field),self.assertRaises(r.backup.Refused):self.invoke(d)
    def test_receipt_expiry_and_capture_order_rejected(self):
        for field,value in [('captured_at',(datetime.now(timezone.utc)-timedelta(days=2)).isoformat()),('verified_at',(datetime.now(timezone.utc)-timedelta(hours=1)).isoformat())]:
            d=copy.deepcopy(self.data)
            if field=='captured_at':d[field]=value
            else:d['restore'][field]=value
            with self.subTest(field=field),self.assertRaises(r.backup.Refused):self.invoke(d)
    def test_default_is_offline(self):
        result=subprocess.run([sys.executable,str(Path(r.__file__))],capture_output=True,text=True)
        self.assertEqual(0,result.returncode);self.assertFalse(json.loads(result.stdout)['database_accessed'])

if __name__=='__main__':unittest.main()

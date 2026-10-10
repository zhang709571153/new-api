import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import singlecore_pg_backup307 as module

class Strict307(unittest.TestCase):
    def test_exact_history_and_managed_checksums_are_required(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);rows=[]
            for index in range(1,298):
                path=root/('%03d_old.sql'%index);path.write_text('SELECT 1;')
                rows.append(dict(filename=path.name,checksum=hashlib.sha256(b'SELECT 1;').hexdigest()))
            special={}
            for filename in module.REQUIRED:
                path=root/filename;path.write_text(' SELECT 2;\n')
                special[filename]=hashlib.sha256(b'SELECT 2;').hexdigest()
                rows.append(dict(filename=filename,checksum=special[filename]))
            with patch.object(module,'REQUIRED',special):
                module.verify_migrations(rows,root)
                with self.assertRaises(module.backup.Refused):module.verify_migrations(rows[:-1],root)
                with self.assertRaises(module.backup.Refused):module.verify_migrations(rows+[rows[0]],root)
                changed=[dict(x) for x in rows];changed[0]['checksum']='0'*64
                with self.assertRaises(module.backup.Refused):module.verify_migrations(changed,root)
                changed=[dict(x) for x in rows];changed[-1]['filename']='308_not_allowed.sql'
                with self.assertRaises(module.backup.Refused):module.verify_migrations(changed,root)
            with self.assertRaises(module.backup.Refused):module.verify_migrations(rows,root)

    def test_base_runner_pin_remains_exact(self):
        self.assertEqual(module.BASE_RUNNER_SHA256,module.backup.sha256(module.backup.__file__))

if __name__=='__main__':unittest.main()

import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('history_input_example', Path(__file__).with_name('prepare_history_update_inputs.example.py'))
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


class InputTemplateTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory(); self.addCleanup(directory.cleanup)
        self.root = Path(directory.name); self.source = self.root / 'source'; self.delivery = self.root / 'delivery'
        self.migrations = self.source / 'backend/migrations'; self.migrations.mkdir(parents=True); self.delivery.mkdir()
        self.file = self.source / 'backend/fixture.go'; self.file.write_bytes(b'package fixture\n')
        self.patch_file = self.delivery / 'singlecore-candidate.patch'; self.patch_file.write_bytes(b'synthetic patch')
        self.binary = self.root / 'candidate.exe'; self.binary.write_bytes(b'MZ synthetic PE')
        self.old = self.migrations / '307_fixture.sql'; self.old.write_bytes(b'SELECT 1;\n')
        self.new = self.migrations / example.MIGRATION; self.new.write_bytes(b'SELECT 2;\n')
        self.commit = 'a' * 40; self.version = 'synthetic-ux2'
        self.metadata = {'format': 1, 'candidate_version': self.version, 'source_commit': self.commit,
            'upstream_commit': example.UPSTREAM, 'upstream_tag': 'v0.2.15', 'patch': self.patch_file.name,
            'patch_sha256': example.sha(self.patch_file), 'patch_bytes': self.patch_file.stat().st_size,
            'changed_files': [{'path': 'backend/fixture.go', 'sha256': example.sha(self.file), 'bytes': self.file.stat().st_size}]}
        self.metadata_path = self.delivery / 'singlecore-source.json'; self.save_metadata()
        import hashlib
        old_sha = hashlib.sha256(self.old.read_bytes().strip()).hexdigest()
        new_sha = hashlib.sha256(self.new.read_bytes().strip()).hexdigest()
        for name, value in [('BASELINE_COUNT', 1), ('BASELINE_SHA', example.digest({self.old.name: old_sha})), ('MIGRATION_SHA', new_sha)]:
            patcher = patch.object(example, name, value); patcher.start(); self.addCleanup(patcher.stop)

    def save_metadata(self): self.metadata_path.write_text(json.dumps(self.metadata), encoding='utf-8')

    def inspect(self):
        return example.inspect_inputs(self.metadata_path, self.source, self.binary, example.sha(self.binary), self.version, self.commit)

    def test_valid_local_report_cannot_be_used_as_executable_plan(self):
        before = {p: p.read_bytes() for p in (self.file, self.patch_file, self.old, self.new, self.binary, self.metadata_path)}
        report = self.inspect()
        self.assertNotEqual(report['format'], 1)
        self.assertFalse(report['production_accessed']); self.assertFalse(report['execution_permitted'])
        self.assertFalse(report['candidate']['build_linkage_verified'])
        self.assertEqual(len(report['migration_inventory']), 2)
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_path_traversal_and_duplicate_sources_fail_closed(self):
        original = copy.deepcopy(self.metadata['changed_files'])
        for rows in ([{**original[0], 'path': '../delivery/singlecore-candidate.patch'}], original + original):
            self.metadata['changed_files'] = rows; self.save_metadata()
            with self.assertRaises(example.Refused): self.inspect()

    def test_tampered_source_patch_or_migration_is_rejected(self):
        for target in (self.file, self.patch_file, self.old, self.new):
            before = target.read_bytes(); target.write_bytes(before + b'changed')
            with self.subTest(target=target.name), self.assertRaises(example.Refused): self.inspect()
            target.write_bytes(before)

    def test_report_and_prior_failure_are_never_replaced(self):
        report = self.inspect(); output = self.root / 'review.json'
        example.write_report(output, report); before = output.read_bytes()
        with self.assertRaises(FileExistsError): example.write_report(output, {'changed': True})
        self.assertEqual(output.read_bytes(), before)

    def test_binary_must_match_external_expected_hash(self):
        expected = example.sha(self.binary)
        self.binary.write_bytes(b'MZ different binary')
        with self.assertRaisesRegex(example.Refused, 'CANDIDATE_HASH'):
            example.inspect_inputs(self.metadata_path, self.source, self.binary, expected, self.version, self.commit)


if __name__ == '__main__': unittest.main()

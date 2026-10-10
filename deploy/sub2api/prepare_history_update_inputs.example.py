"""Offline input inventory for a site-specific 307 -> 308 Windows update.

This example reads local source/package/binary files only. It has no database,
HTTP, credential, service, privilege or execution interface. Its output is NOT
an executable release plan and never substitutes for live authority preflight.
Python 3.11+. Run --help for explicit, portable local paths.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re


UPSTREAM = 'f2669c8cf62555cd92389b3f55920e9e6e7c6ff2'
BASELINE_COUNT = 299
BASELINE_SHA = '9e617c32ee712fd94b12f23632cf0ac68005e85c0985216eb3979590f393380f'
MIGRATION = '308_realyu_usage_history.sql'
MIGRATION_SHA = '3ee70f5ffb0910c1693641c686a24e567ea2c0b87d32a7cb2084cffdccb3048c'


class Refused(ValueError):
    pass


def require(condition, code):
    if not condition: raise Refused(code)


def sha(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def inspect_inputs(metadata_path, source_root, candidate, expected_sha, expected_version, expected_source_commit):
    metadata_path, source_root, candidate = (Path(p).resolve(strict=True) for p in (metadata_path, source_root, candidate))
    require(re.fullmatch('[0-9a-f]{64}', expected_sha or '') is not None, 'EXPECTED_BINARY_SHA_REQUIRED')
    require(re.fullmatch('[0-9a-f]{40}', expected_source_commit or '') is not None, 'EXPECTED_SOURCE_COMMIT_REQUIRED')
    metadata = json.loads(metadata_path.read_text(encoding='utf-8-sig'))
    require(metadata.get('format') == 1 and metadata.get('candidate_version') == expected_version, 'SOURCE_VERSION_MISMATCH')
    require(metadata.get('upstream_commit') == UPSTREAM and metadata.get('upstream_tag') == 'v0.2.15', 'UPSTREAM_MISMATCH')
    require(metadata.get('source_commit') == expected_source_commit, 'EXPORTED_SOURCE_COMMIT_MISMATCH')
    require(metadata.get('patch') == 'singlecore-candidate.patch', 'PATCH_FILENAME_MISMATCH')
    patch = metadata_path.parent / metadata['patch']
    require(sha(patch) == metadata.get('patch_sha256') and patch.stat().st_size == metadata.get('patch_bytes'), 'PATCH_CONTENT_MISMATCH')
    files = {str(metadata_path): sha(metadata_path), str(patch): sha(patch)}
    entries = metadata.get('changed_files')
    require(isinstance(entries, list) and entries, 'SOURCE_FILE_INVENTORY_REQUIRED')
    seen = set()
    for item in entries:
        name = item.get('path')
        require(isinstance(name, str) and name and '\\' not in name, 'SOURCE_PATH_INVALID')
        relative = PurePosixPath(name)
        require(not relative.is_absolute() and '..' not in relative.parts, 'SOURCE_PATH_ESCAPES_CHECKOUT')
        path = (source_root / name).resolve(strict=True)
        require(path.is_relative_to(source_root) and path.is_file(), 'SOURCE_PATH_ESCAPES_CHECKOUT')
        normalized = str(path).casefold()
        require(normalized not in seen, 'SOURCE_FILE_DUPLICATE')
        seen.add(normalized)
        value = sha(path)
        require(value == item.get('sha256') and path.stat().st_size == item.get('bytes'), 'SOURCE_FILE_CONTENT_MISMATCH')
        files[str(path)] = value
    inventory = {}
    for path in sorted((source_root / 'backend/migrations').glob('*.sql')):
        require(re.fullmatch(r'[0-9]{3}[a-z]?_[a-z0-9_]+\.sql', path.name) is not None, 'MIGRATION_FILENAME_INVALID')
        raw = path.read_bytes()
        # Preserve internal CR/LF bytes, matching the reviewed native runner.
        inventory[path.name] = hashlib.sha256(raw.decode('utf-8').strip().encode('utf-8')).hexdigest()
        files[str(path)] = hashlib.sha256(raw).hexdigest()
    baseline = {name: value for name, value in inventory.items() if name != MIGRATION}
    require(len(baseline) == BASELINE_COUNT and digest(baseline) == BASELINE_SHA, 'EXACT_307_SOURCE_BASELINE_REQUIRED')
    require(inventory.get(MIGRATION) == MIGRATION_SHA, 'REVIEWED_308_SOURCE_REQUIRED')
    require(sha(candidate) == expected_sha, 'CANDIDATE_HASH_MISMATCH')
    with candidate.open('rb') as stream:
        require(stream.read(2) == b'MZ', 'WINDOWS_PE_REQUIRED')
    files[str(candidate)] = expected_sha
    return {
        'format': 'review-only-history-inputs-v1',
        'kind': 'site-preparation-input-inventory',
        'status': 'LOCAL_INPUTS_CHECKED_NOT_DEPLOYABLE',
        'execution_permitted': False,
        'production_accessed': False,
        'source': {'version': expected_version, 'exported_source_commit': expected_source_commit,
            'upstream_commit': UPSTREAM, 'patch_sha256': sha(patch), 'changed_files_checked': len(entries)},
        'candidate': {'sha256': expected_sha, 'started': False, 'embedded_version_verified': False, 'build_linkage_verified': False},
        'migration_inventory': inventory,
        'verified_local_files': files,
        'site_values_to_prepare_privately': [
            'unique operation directory, id, versioned install path and adjacent env path',
            'current native manifest, executable SHA, SCM/service identity and authority/gate receipts',
            'actual PostgreSQL identity, exact applied migration inventory and Redis scope',
            'fresh consistent backup SHA with successful isolated restore and matching schema/rows',
            'sealed history source SHA, installation id and reviewed aggregate manifest',
            'same-host source/build receipts or a reconstructed-tree and local clean-build mapping',
            'site-reviewed updater/preparer and all dependency hashes; a separate executable plan'
        ],
        'unverified': [
            'live database, credentials, backup, archive or running service state',
            'binary embedded version, Go VCS metadata or correspondence with the inspected source',
            'global admission, drain, active holds/jobs, session-0 ownership, readiness or public/browser E2E'
        ],
        'notice': 'This inventory is deliberately incompatible with release plan format 1. Do not rename it to plan.json or change the format to bypass site review.'
    }


def write_report(path, value):
    # Parent must already exist. Never replace any prior report or input.
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('source-metadata', 'source-root', 'candidate-binary', 'expected-candidate-sha256', 'expected-version', 'expected-source-commit'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--output', help='Optional new local review report; never a production plan.')
    args = parser.parse_args(argv)
    try:
        result = inspect_inputs(args.source_metadata, args.source_root, args.candidate_binary,
            args.expected_candidate_sha256, args.expected_version, args.expected_source_commit)
        if args.output: write_report(args.output, result)
        print(json.dumps({'status': result['status'], 'source_files_checked': result['source']['changed_files_checked'],
            'migration_files_checked': len(result['migration_inventory']), 'execution_permitted': False,
            'production_accessed': False, 'review_report_written': bool(args.output)}))
        return 0
    except Exception as error:
        print(json.dumps({'status': 'BLOCKED', 'code': str(error) if isinstance(error, Refused) else 'LOCAL_INPUT_INSPECTION_FAILED',
            'type': type(error).__name__, 'execution_permitted': False, 'production_accessed': False}))
        return 1


if __name__ == '__main__': raise SystemExit(main())

"""Reuse the real-engine matrix on synthetic state and exact frozen binary.

No production databases or credentials are loaded. The baseline executable is
read only and must match the supplied recorded release SHA256.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from bootstrap_windows import ROOT, PRIVATE

sys.path.insert(0, str(ROOT / 'lab'))
import verify_database_matrix as base


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--engines-root', type=Path, required=True)
    parser.add_argument('--enrollment-only', action='store_true', help='Only the new read-only automatic enrollment root on three real databases')
    args = parser.parse_args()
    baseline = args.baseline.resolve(strict=True)
    candidate = args.candidate.resolve(strict=True)
    expected = 'f729e2b8f2c631e1b804b7e6ef56397efbe76560c2e8fc6528f845c2eff4de20'
    if hashlib.sha256(baseline.read_bytes()).hexdigest() != expected:
        raise RuntimeError('Frozen baseline SHA256 mismatch')
    base.PRIVATE = PRIVATE / 'matrix'
    base.MYSQL = args.engines_root / 'mysql/mysql-8.4.11-winx64'
    base.PG = args.engines_root / 'postgres/pgsql'
    for name in ('REALYU_UPSTREAM_DRIVER', 'REALYU_SUB2API_BINDINGS_FILE', 'CHANNEL_UPDATE_FREQUENCY', 'SQL_DSN', 'LOG_SQL_DSN', 'REDIS_CONN_STRING'):
        os.environ.pop(name, None)
    report_name = 'enrollment-database-summary.json' if args.enrollment_only else 'database-summary.json'
    matrix = base.Matrix(ROOT / 'lab/sub2api_e2e' / report_name, mysql_port=28606, postgres_port=28632)
    if matrix.report_path.exists():
        previous = json.loads(matrix.report_path.read_text())
        (matrix.report_path.parent / (matrix.report_path.stem + '-' + previous['run_id'] + '.json')).write_text(json.dumps(previous, indent=2), encoding='utf-8')
    matrix.report.update(baseline='frozen actual RealYu release binary; no live data', baseline_sha256=expected,
        candidate_sha256=hashlib.sha256(candidate.read_bytes()).hexdigest(),
        external_model_calls=0, production_changed=False, source='latest frozen business baseline plus Sub2API integration')
    matrix.report['command'] = ('python lab/sub2api_e2e/database_regression.py --baseline <frozen-release.exe> '
        '--candidate <isolated-candidate.exe> --engines-root <local-portable-engines>'
        + (' --enrollment-only' if args.enrollment_only else ''))
    tests = ['TestWorkspaceFundingIsolation', 'TestWorkspaceFundingScopePermissions', 'TestWorkspaceTeamLifecycle']
    if args.enrollment_only:
        tests = ['TestSub2APIEnrollmentReconcilesCommittedWorkspaceSubjects']
        matrix.report['scope'] = 'new read-only enrollment regression root on three real DB engines; no upgrade scenario repeated'
        matrix.report['candidate_binary_executed'] = False
    try:
        matrix.start_databases()
        for engine in ('sqlite', 'mysql', 'postgres'):
            # Each root initializes an empty database; never combine them into
            # one process/DSN and turn fixture reuse into a false regression.
            for test in tests:
                matrix.test_pattern = '^' + test + '$'
                matrix.expected_test_roots = [test]
                matrix.team_test(engine)
            if not args.enrollment_only:
                matrix.scenario(engine, 'fresh', baseline, candidate)
                matrix.scenario(engine, 'release_upgrade', baseline, candidate)
        matrix.report['status'] = 'PASS'
    except Exception as exc:
        matrix.report['status'] = 'FAIL'
        matrix.report['error'] = str(exc).replace(matrix.password, '[REDACTED]')[:800]
    finally:
        matrix.cleanup()
    print(json.dumps({'status': matrix.report['status'], 'report': str(matrix.report_path),
        'error': matrix.report.get('error'), 'checks': len(matrix.report['cases']), 'team_tests': len(matrix.report['team_tests'])}, indent=2))
    return int(matrix.report['status'] != 'PASS')


if __name__ == '__main__':
    sys.exit(main())

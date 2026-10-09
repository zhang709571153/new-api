"""Actual worker/RealYu processes against a loopback management protocol fake.

This layer does not prove compatibility with stock Sub2API or a real model.
No production inputs, customer credentials, paid calls, or acknowledgment.
"""
import argparse
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import time

from bootstrap_windows import PRIVATE, ROOT, FLAGS
from projection_control_fixture import ProjectionControlFixture


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--worker', type=Path, required=True)
    parser.add_argument('--gateway', type=Path, required=True)
    parser.add_argument('--retry-after-only', action='store_true', help='Target the upstream Retry-After deadline without minute waits')
    args = parser.parse_args()
    worker, gateway = args.worker.resolve(strict=True), args.gateway.resolve(strict=True)
    run = PRIVATE / ('prewarm-' + secrets.token_hex(5))
    run.mkdir()
    report = {'scope': 'actual local worker and gateway processes with fake management protocol',
        'stock_sub2api_success_verified': False, 'production_changed': False, 'inference_performed': False,
        'worker_sha256': hashlib.sha256(worker.read_bytes()).hexdigest(),
        'gateway_sha256': hashlib.sha256(gateway.read_bytes()).hexdigest(),
        'checks': {}, 'all_passed': False, 'evidence': {}}

    def save():
        (run / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')

    def check(name, condition):
        report['checks'][name] = bool(condition)
        save()
        print(json.dumps({'check': name, 'passed': bool(condition)}), flush=True)
        if not condition:
            raise AssertionError(name)

    def config_for(fixture, name, tasks):
        directory = run / name
        directory.mkdir()
        config = {'version': 1, 'namespace': name, 'identity_secret': secrets.token_hex(32),
            'admin_api_key': 'fixture-admin',
            'pools': [{'channel_id': 59, 'base_url': fixture.base_url, 'group_id': 10},
                      {'channel_id': 60, 'base_url': fixture.base_url, 'group_id': 20}],
            'provision': {'enabled': True, 'mode': 'prewarmed', 'initial_balance': 100, 'concurrency': 1},
            'bindings': []}
        (directory / 'bindings.json').write_text(json.dumps(config), encoding='utf-8')
        (directory / 'queue.json').write_text(json.dumps({'version': 1, 'tasks': tasks}), encoding='utf-8')
        return directory

    def owned_run(command, timeout, **kwargs):
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL, creationflags=FLAGS, **kwargs)
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            # Only this newly-created process tree is terminated. In particular,
            # a timed-out Python gateway runner must not leave its child alive.
            if os.name == 'nt':
                subprocess.run(['taskkill.exe', '/PID', str(process.pid), '/T', '/F'],
                    capture_output=True, timeout=10, creationflags=FLAGS)
            else:
                process.kill()
            process.communicate(timeout=10)
            raise
        return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)

    def invoke(directory, label, state='progress.json', timeout=40):
        env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()
               and not k.startswith('REALYU_SUB2API_') and k not in ('SQL_DSN', 'REDIS_CONN_STRING', 'LOG_SQL_DSN')}
        env.update(REALYU_UPSTREAM_DRIVER='sub2api', REALYU_SUB2API_BINDINGS_FILE=str(directory / 'bindings.json'))
        start = time.monotonic()
        result = owned_run([str(worker), '--queue', str(directory / 'queue.json'), '--state', str(directory / state)],
            env=env, cwd=run, timeout=timeout)
        (directory / (label + '.stdout')).write_bytes(result.stdout)
        (directory / (label + '.stderr')).write_bytes(result.stderr)
        output = result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace')
        check(label + '_no_sensitive_error_echo', 'fixture-sensitive-message' not in output)
        return result.returncode, output, time.monotonic() - start

    tasks = [{'realyu_user_id': 17, 'workspace_team_id': 91, 'channel_id': 59},
             {'realyu_user_id': 17, 'workspace_team_id': 92, 'channel_id': 59},
             {'realyu_user_id': 17, 'workspace_team_id': 91, 'channel_id': 60}]
    try:
        if args.retry_after_only:
            with ProjectionControlFixture(login_failures=[429], retry_after=75) as fixture:
                directory = config_for(fixture, 'longer-retry-after', tasks[:1])
                started_at = time.time()
                code, output, _ = invoke(directory, 'longer_retry_after')
                state = json.loads((directory / 'progress.json').read_text())
                deadline = datetime.fromisoformat(state['not_before'].replace('Z', '+00:00')).timestamp()
                check('upstream_longer_retry_after_is_respected', code != 0 and 'RETRY_WAIT' in output
                    and deadline >= started_at + 74.5 and state['tasks']['17:91:59']['http_status'] == 429)
                before = fixture.snapshot()
                code, output, _ = invoke(directory, 'longer_retry_early_resume')
                check('longer_retry_after_blocks_immediate_management_calls', code != 0 and 'RETRY_WAIT' in output
                    and fixture.snapshot()['events'] == before['events'])
            report['all_passed'] = all(report['checks'].values())
            return 0
        with ProjectionControlFixture() as fixture:
            directory = config_for(fixture, 'multi-subject', tasks)
            code, first_output, elapsed = invoke(directory, 'first')
            first = fixture.snapshot()
            check('multiple_subjects_and_pools_completed', code == 0 and first['users'] == 2 and first['keys'] == 3)
            check('subject_ownership_distinct_and_pool_identity_stable',
                sorted(sum(row['user_id'] == user for row in first['key_ownership']) for user in (1, 2)) == [1, 2])
            logins = [e['at'] for e in first['events'] if e['path'] == '/api/v1/auth/login']
            check('single_worker_real_login_spacing', len(logins) == 3 and all(b - a >= 3.8 for a, b in zip(logins, logins[1:])))
            state_text = (directory / 'progress.json').read_text()
            check('persistent_state_has_no_internal_keys_or_passwords',
                all(secret not in state_text for secret in list(fixture.keys) + list(fixture.passwords.values())))
            check('worker_output_has_no_internal_keys_or_passwords',
                all(secret not in first_output for secret in list(fixture.keys) + list(fixture.passwords.values())))
            code, _, _ = invoke(directory, 'resume')
            resumed = fixture.snapshot()
            check('new_process_resume_does_not_rebuild', code == 0 and len(resumed['events']) == len(first['events']))
            code, _, _ = invoke(directory, 'recover_remote', state='recovered-progress.json')
            recovered = fixture.snapshot()
            check('lost_local_progress_recovers_remote_identities_without_rebuilding', code == 0
                and recovered['user_creates'] == 2 and recovered['key_creates'] == 3 and recovered['login_attempts'] == 3)
            check('worker_does_not_execute_inference', recovered['inference_requests'] == 0)
            report['evidence']['multiple_subjects'] = {'users': 2, 'keys': 3, 'initial_elapsed_seconds': round(elapsed, 3)}

        with ProjectionControlFixture(login_failures=[429], retry_after=1) as fixture:
            directory = config_for(fixture, 'rate-limited', tasks[:1])
            started_at = time.time()
            code, output, _ = invoke(directory, 'rate_limit')
            first = fixture.snapshot()
            check('429_stops_with_retry_wait', code != 0 and 'RETRY_WAIT' in output and first['login_attempts'] == 1 and first['keys'] == 0)
            state_before = (directory / 'progress.json').read_text()
            persisted = json.loads(state_before)
            deadline = datetime.fromisoformat(persisted['not_before'].replace('Z', '+00:00')).timestamp()
            check('429_persists_at_least_sixty_second_deadline', deadline >= started_at + 59.5
                and persisted['tasks']['17:91:59']['status'] == 'RETRY_WAIT'
                and persisted['tasks']['17:91:59']['attempts'] == 1
                and persisted['tasks']['17:91:59']['http_status'] == 429)
            code, output, elapsed = invoke(directory, 'early_resume')
            second = fixture.snapshot()
            check('immediate_resume_respects_persisted_backoff', code != 0 and 'RETRY_WAIT' in output
                and second['events'] == first['events'] and elapsed < 10)
            check('backoff_resume_does_not_mutate_progress', (directory / 'progress.json').read_text() == state_before)
            report['evidence']['429'] = {'login_attempts': second['login_attempts'], 'early_resume_seconds': round(elapsed, 3),
                'future_time_recovery': 'requires separate controlled-clock unit test; no wall-clock minute waits'}

        with ProjectionControlFixture(group_failure=423) as fixture:
            directory = config_for(fixture, 'compliance-rejection', tasks[:2])
            code, output, _ = invoke(directory, 'compliance')
            snapshot = fixture.snapshot()
            check('423_blocks_queue_without_advancing_subjects', code != 0 and 'BLOCKED' in output and len(snapshot['events']) == 1
                and snapshot['users'] == 0 and snapshot['keys'] == 0 and snapshot['login_attempts'] == 0)

        with ProjectionControlFixture() as fixture:
            result = owned_run([sys.executable, str(ROOT / 'lab/sub2api_e2e/realyu_negative.py'),
                '--binary', str(gateway), '--team-scope', '--prewarmed-control-url', fixture.base_url], cwd=ROOT,
                timeout=45)
            (run / 'gateway.stdout').write_bytes(result.stdout)
            (run / 'gateway.stderr').write_bytes(result.stderr)
            snapshot = fixture.snapshot()
            check('actual_gateway_prewarmed_missing_identity_rejects_and_preserves_ledger', result.returncode == 0)
            check('web_path_never_creates_or_logs_in_or_infers', len(snapshot['events']) > 0
                and snapshot['user_creates'] == 0 and snapshot['login_attempts'] == 0 and snapshot['key_creates'] == 0
                and snapshot['inference_requests'] == 0)
            report['evidence']['gateway_management_reads'] = len(snapshot['events'])
        report['all_passed'] = all(report['checks'].values())
    except Exception as exc:
        # Never publish raw process logs or request bodies on failure.
        report['failure'] = {'type': type(exc).__name__, 'last_check': next(reversed(report['checks']), 'startup')}
    finally:
        save()
        print(json.dumps({'report': str(run / 'summary.json'), 'all_passed': report['all_passed'], 'failure': report.get('failure')}), flush=True)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    sys.exit(main())

"""Real CLI file-edit/test and process-restart resume in a disposable CODEX_HOME.

This never disables execpolicy, ignores rules, or changes the operator's home.
Policy refusals remain BLOCKED; API availability alone cannot pass the task.
"""
import argparse
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
from urllib.parse import urlsplit

from bootstrap_windows import PRIVATE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex', required=True, type=Path)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--model', default='gpt-6.1-sol')
    args = parser.parse_args()
    if urlsplit(args.base_url).hostname not in ('127.0.0.1', 'localhost', '::1'):
        parser.error('CLI acceptance targets the isolated loopback candidate only')
    key = os.environ.get('REALYU_API_KEY', '').strip()
    if not key:
        parser.error('Set REALYU_API_KEY through this process environment')
    codex = args.codex.resolve(strict=True)
    run = PRIVATE / ('cli-' + secrets.token_hex(6))
    home, workspace = run / 'home', run / 'workspace'
    home.mkdir(parents=True)
    shutil.copytree(Path(__file__).parent / 'portable/codex-fixture', workspace)
    clean = {k: v for k, v in os.environ.items() if k.upper() not in ('CODEX_HOME', 'OPENAI_BASE_URL', 'OPENAI_API_KEY')}
    clean.update(CODEX_HOME=str(home), REALYU_API_KEY=key)
    config = ('model_provider = "realyu_candidate"\nmodel = ' + json.dumps(args.model) + '\n'
        'approval_policy = "never"\nsandbox_mode = "workspace-write"\n'
        '[model_providers.realyu_candidate]\nname = "RealYu isolated Sub2API candidate"\n'
        'base_url = ' + json.dumps(args.base_url) + '\nenv_key = "REALYU_API_KEY"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\nrequest_max_retries = 0\nstream_max_retries = 0\n')
    (home / 'config.toml').write_text(config, encoding='utf-8')
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    before = subprocess.run([sys.executable, '-m', 'unittest', '-v'], cwd=workspace, capture_output=True, timeout=30, creationflags=flags)
    (run / 'tests-before.log').write_bytes(before.stdout + before.stderr)
    original_test = (workspace / 'test_invoice.py').read_bytes()
    original_code = (workspace / 'invoice.py').read_bytes()
    marker = 'E2E_' + secrets.token_hex(16)
    report = {'status': 'FAIL', 'desktop_e2e_performed': False, 'compact_e2e_performed': False,
              'automatic_retries': 0, 'config_isolated': True, 'sandbox': 'workspace-write',
              'version': subprocess.check_output([str(codex), '--version'], creationflags=flags).decode().strip(), 'checks': {}}
    prompt = ('Read AGENTS.md and the source and tests. Fix invoice.py so quantity is applied correctly. '
        'Do not edit test_invoice.py. Run the existing unittest suite and report the actual result. '
        f'Remember this conversation marker for a later turn: {marker}. Do not write the marker to files.')
    command = [str(codex), 'exec', '--skip-git-repo-check', '-C', str(workspace), '--json', '-']

    def execute(command, prompt, label):
        with subprocess.Popen(command, cwd=workspace, env=clean, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags) as child:
            try:
                stdout, stderr = child.communicate(prompt.encode(), timeout=240)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill.exe', '/PID', str(child.pid), '/T', '/F'], capture_output=True, timeout=20, creationflags=flags)
                stdout, stderr = child.communicate(timeout=15)
                report[label + '_timeout'] = True
        (run / (label + '.jsonl')).write_bytes(stdout)
        (run / (label + '.stderr')).write_bytes(stderr)
        events = []
        for line in stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        return child.returncode, stdout.decode(errors='replace') + stderr.decode(errors='replace'), events

    code, transcript, events = execute(command, prompt, 'initial')
    after = subprocess.run([sys.executable, '-m', 'unittest', '-v'], cwd=workspace, capture_output=True, timeout=30, creationflags=flags)
    (run / 'tests-after.log').write_bytes(after.stdout + after.stderr)
    report['checks'].update(baseline_tests_fail=before.returncode != 0, actual_source_changed=(workspace / 'invoice.py').read_bytes() != original_code,
        tests_unchanged=(workspace / 'test_invoice.py').read_bytes() == original_test, external_tests_pass=after.returncode == 0,
        cli_initial_success=code == 0 and not report.get('initial_timeout'),
        real_test_command_observed=any('unittest' in json.dumps(e) and e.get('item', {}).get('type') == 'command_execution' for e in events))
    thread = next((e.get('thread_id') for e in events if e.get('type') == 'thread.started'), None)
    if thread:
        code, resumed, _ = execute([str(codex), 'exec', 'resume', '--skip-git-repo-check', '--json', thread, '-'],
            'What conversation marker did I ask you to remember? Reply only with that exact marker, without calling tools.', 'resume')
        report['checks']['new_process_resume_recalls_marker'] = code == 0 and marker in resumed
    else:
        report['checks']['new_process_resume_recalls_marker'] = False
    policy_blocked = any(word in transcript.lower() for word in ('blocked by policy', 'rejected: blocked', 'execution policy'))
    incomplete_stream = any(word in transcript.lower() for word in
        ('stream disconnected before completion', 'stream closed before response.completed'))
    report['tool_execution_blocked'] = policy_blocked
    report['incomplete_stream_reported'] = incomplete_stream
    # A policy refusal must not mask an independently observed protocol failure.
    report['status'] = 'FAIL' if incomplete_stream else 'PASS' if all(report['checks'].values()) else 'BLOCKED' if policy_blocked else 'FAIL'
    if policy_blocked:
        report['limitation'] = 'Local execution policy rejected tools; not bypassed and not counted as an API capability failure.'
    if incomplete_stream:
        report['stream_limitation'] = 'CLI also reported a missing terminal response; diagnose separately from local tool policy.'
    (run / 'summary.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({**report, 'evidence_directory': str(run)}, indent=2))
    return 0 if report['status'] == 'PASS' else 2 if report['status'] == 'BLOCKED' else 1


if __name__ == '__main__':
    sys.exit(main())

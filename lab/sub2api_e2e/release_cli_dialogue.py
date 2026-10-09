"""Latest pinned CLI: complete no-tool dialogue and separate-process resume.

Uses a fresh CODEX_HOME and dedicated operator API key, never daily auth/config.
No policy is bypassed. Default validates inputs only; --run makes two requests.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
from urllib.parse import urlsplit

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--codex', required=True, type=Path)
    parser.add_argument('--secret-file', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--run', action='store_true')
    args = parser.parse_args()
    cfg = json.loads(args.secret_file.read_text(encoding='utf-8-sig'))
    assert cfg.get('dedicated_operator_subjects') is True
    base = cfg['base_url'].rstrip('/')
    target = urlsplit(base)
    assert target.path == '/v1' and not target.query and not target.fragment
    assert target.scheme == 'https' or (target.scheme == 'http' and target.hostname == '127.0.0.1')
    codex = args.codex.resolve(strict=True)
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    version = subprocess.check_output([str(codex), '--version'], creationflags=flags, timeout=15).decode().strip()
    assert version == 'codex-cli 0.162.0', 'Expected previously verified official stable release'
    gateway = Path(cfg['gateway_binary']).resolve(strict=True)
    assert hashlib.sha256(gateway.read_bytes()).hexdigest() == cfg['gateway_sha256']
    assert not args.output.exists(), 'Use fresh output; preserve the first failure'
    report = {'status': 'PREPARED', 'version': version,
              'codex_sha256': hashlib.sha256(codex.read_bytes()).hexdigest(),
              'gateway_sha256': cfg['gateway_sha256'], 'automatic_retries': 0,
              'isolated_codex_home': True, 'local_policy_bypassed': False,
              'desktop_e2e_performed': False, 'tools_requested': False, 'checks': []}
    if not args.run:
        print(json.dumps(report))
        return 0
    args.output.mkdir(parents=True)
    home, workspace = args.output / 'home', args.output / 'workspace'
    home.mkdir()
    workspace.mkdir()
    model = cfg.get('model', 'gpt-6.1-sol')
    settings = ('model_provider = "realyu_release"\nmodel = ' + json.dumps(model) + '\n'
        'approval_policy = "never"\nsandbox_mode = "read-only"\n'
        '[model_providers.realyu_release]\nname = "RealYu release acceptance"\n'
        'base_url = ' + json.dumps(base) + '\nenv_key = "REALYU_RELEASE_API_KEY"\n'
        'wire_api = "responses"\nrequires_openai_auth = false\n'
        'request_max_retries = 0\nstream_max_retries = 0\n')
    (home / 'config.toml').write_text(settings)
    env = {key: value for key, value in os.environ.items()
           if key.upper() not in ('CODEX_HOME', 'OPENAI_API_KEY', 'OPENAI_BASE_URL', 'REALYU_API_KEY')}
    env.update(CODEX_HOME=str(home.resolve()), REALYU_RELEASE_API_KEY=cfg['subjects'][0]['api_key'])
    report['status'] = 'RUNNING'

    def save():
        (args.output / 'summary.json').write_text(json.dumps(report, indent=2))

    def execute(command, prompt, label, expected):
        with subprocess.Popen(command, cwd=workspace, env=env, stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags) as child:
            try:
                stdout, stderr = child.communicate(prompt.encode(), timeout=240)
            except subprocess.TimeoutExpired:
                subprocess.run(['taskkill.exe', '/PID', str(child.pid), '/T', '/F'],
                               capture_output=True, timeout=20, creationflags=flags)
                stdout, stderr = child.communicate(timeout=15)
                report[label + '_timeout'] = True
        (args.output / (label + '.jsonl')).write_bytes(stdout)
        (args.output / (label + '.stderr')).write_bytes(stderr)
        events = []
        for line in stdout.splitlines():
            try:
                events.append(json.loads(line))
            except ValueError:
                pass
        messages = [e['item'].get('text', '') for e in events
                    if e.get('type') == 'item.completed' and e.get('item', {}).get('type') == 'agent_message']
        items = [e.get('item', {}) for e in events if e.get('type') == 'item.completed']
        tools = [item for item in items if item.get('type') not in ('agent_message', 'reasoning')]
        failed = [e for e in events if e.get('type') in ('error', 'turn.failed')]
        transcript = (stdout + stderr).decode(errors='replace').lower()
        stream_failure = any(t in transcript for t in ('stream disconnected before completion', 'stream closed before response.completed'))
        passed = (child.returncode == 0 and not report.get(label + '_timeout')
                  and len(messages) == 1 and messages[0].strip() == expected
                  and any(e.get('type') == 'turn.completed' for e in events)
                  and not tools and not failed and not stream_failure)
        report['checks'].append({'name': label, 'status': 'PASS' if passed else 'FAIL',
            'exit_code': child.returncode, 'tools_observed': len(tools), 'terminal_completed': any(e.get('type') == 'turn.completed' for e in events),
            'exact_reply': len(messages) == 1 and messages[0].strip() == expected, 'incomplete_stream_reported': stream_failure})
        save()
        return next((e.get('thread_id') for e in events if e.get('type') == 'thread.started'), None)

    save()
    try:
        with httpx.Client(trust_env=False, timeout=20) as client:
            health = client.get(base.removesuffix('/v1') + '/api/status').json()
        assert health['data']['version'] == cfg['expected_version']
        marker = 'REALYU_PUBLIC_CLI_' + secrets.token_hex(10)
        initial = [str(codex), 'exec', '--skip-git-repo-check', '-C', str(workspace.resolve()), '--json', '-']
        thread = execute(initial, 'Do not call any tools or read files. Remember this conversation marker: ' + marker + '. Reply only ACK.', 'initial', 'ACK')
        assert thread, 'Initial process did not expose a resumable thread'
        assert report['checks'][-1]['status'] == 'PASS', 'Stop after the first incomplete or failed CLI turn'
        execute([str(codex), 'exec', 'resume', '--skip-git-repo-check', '--json', thread, '-'],
                'Do not call any tools or read files. Repeat only the conversation marker I asked you to remember.', 'resume', marker)
        report['status'] = 'PASS' if len(report['checks']) == 2 and all(c['status'] == 'PASS' for c in report['checks']) else 'FAIL'
    except Exception as exc:
        report.update(status='FAIL', error_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            report['assertion'] = str(exc)
    finally:
        save()
    print(json.dumps({'status': report['status'], 'checks': report['checks']}))
    return int(report['status'] != 'PASS')


if __name__ == '__main__':
    raise SystemExit(main())

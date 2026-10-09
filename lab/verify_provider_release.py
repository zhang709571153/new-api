"""Bounded public release acceptance using new, disposable dashboard accounts.

Requires explicit --public-acceptance. --images additionally performs one real
Codex image generation and edit, without retries. Existing users, upstream
channels, desktop credentials, and service configuration are never modified.
Test accounts are disabled in finally; credentials and transcripts stay in .lab.
"""
import argparse
import atexit
import hashlib
import http.cookiejar
import importlib.util
import json
import os
from pathlib import Path
import secrets
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://api.realyu.fun'
UA = 'Realyu-Setup/1.0'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Redirect refused')


class API:
    def __init__(self, token=''):
        self.token = token
        self.cookiejar = http.cookiejar.CookieJar()
        self.http = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect(),
            urllib.request.HTTPCookieProcessor(self.cookiejar))

    def request(self, path, data=None, method=None, headers=None):
        h = {'User-Agent': UA, 'Content-Type': 'application/json', 'Origin': BASE}
        if self.token:
            h['Authorization'] = 'Bearer ' + self.token
        h.update(headers or {})
        req = urllib.request.Request(BASE + path, headers=h, method=method,
            data=None if data is None else json.dumps(data).encode())
        try:
            response = self.http.open(req, timeout=40)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            raw = response.read()
            try:
                value = json.loads(raw)
            except ValueError:
                value = None
            return response.status, value, response.headers, raw

    def call(self, path, data=None, method=None):
        status, value, _, _ = self.request(path, data, method)
        if status != 200 or not isinstance(value, dict) or value.get('success') is False:
            raise RuntimeError(f'API failed: {path}, HTTP {status}')
        return value.get('data', value)

    def denied(self, path, data=None, method=None, headers=None):
        status, value, _, _ = self.request(path, data, method, headers)
        return status in (401, 403) or (status == 200 and isinstance(value, dict) and value.get('success') is False)

    def login(self, username, password):
        if self.token:
            self.logout()
        value = self.call('/api/user/login', {'username': username, 'password': password})
        self.token = value['access_token']
        atexit.register(self.logout)
        return value['user']

    def logout(self):
        if not self.token:
            return
        try:
            self.call('/api/user/auth/logout', {})
            self.token = ''
        except Exception:
            print('Acceptance session cleanup needs retry.', flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--public-acceptance', action='store_true')
    parser.add_argument('--images', action='store_true')
    parser.add_argument('--admin-config', type=Path, required=True)
    args = parser.parse_args()
    if not args.public_acceptance:
        parser.error('Public test account creation requires --public-acceptance')
    run_id = 'e2e' + secrets.token_hex(4)
    private = ROOT / '.lab' / run_id
    private.mkdir(parents=True)
    results = ROOT / 'lab/results' / run_id
    results.mkdir(parents=True)
    report = {'run_id': run_id, 'base_url': BASE, 'started_at': time.strftime('%Y-%m-%dT%H:%M:%S%z'),
              'client_proxy': False, 'checks': {}, 'failure': None, 'all_passed': False,
              'images_requested': args.images, 'artifacts': [], 'cleanup': {}}
    report_file = results / 'public-e2e.json'
    users = {}
    cfg = json.loads(args.admin_config.read_text(encoding='utf-8-sig'))
    admin = API()
    owner = member = None

    def save():
        report_file.write_text(json.dumps(report, indent=2), encoding='utf-8')

    def check(name, passed):
        report['checks'][name] = bool(passed)
        save()
        print(json.dumps({'check': name, 'passed': bool(passed)}), flush=True)
        if not passed:
            raise AssertionError(name)

    def persist_private():
        (private / 'accounts.json').write_text(json.dumps({r: {k: v for k, v in user.items() if k != 'api'}
            for r, user in users.items()}), encoding='utf-8')

    def ledger(token_id):
        db_path = args.admin_config.parent / 'new-api.db'
        with sqlite3.connect(db_path.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            token = dict(db.execute('select id,user_id,workspace_user_id,remain_quota,used_quota from tokens where id=?', (token_id,)).fetchone())
            logs = [dict(row) for row in db.execute('select id,user_id,token_id,channel_id,type,model_name,quota,other from logs where token_id=? order by id', (token_id,))]
        return {'owner': owner.call('/api/user/self'), 'member': member.call('/api/user/self'), 'token': token, 'logs': logs}

    try:
        anon = API()
        status = anon.call('/api/status')
        report['version'] = status.get('version')
        check('public_registration_enabled', status['register_enabled'] and status['password_register_enabled'])
        currency = json.loads((ROOT/'lab/realyu-currency.json').read_text(encoding='utf-8'))
        check('configured_currency_display', status['quota_display_type'] == currency['display_currency']
              and status['usd_exchange_rate'] == currency['cny_per_usd'])
        check('anonymous_dashboard_denied', anon.denied('/api/workspace'))
        admin.login(cfg['admin_username'], cfg['admin_password'])
        for role in ('owner', 'member', 'outsider'):
            username, password = run_id + '_' + role, secrets.token_urlsafe(24)
            client = API()
            client.call('/api/user/register', {'username': username, 'password': password})
            profile = client.login(username, password)
            users[role] = {'id': profile['id'], 'username': username, 'password': password, 'api': client}
            persist_private()
            check(role + '_register_login', profile['username'] == username)
        owner, member, outsider = (users[r]['api'] for r in ('owner', 'member', 'outsider'))
        check('secure_refresh_cookie', any(c.name == 'new_api_refresh' and c.secure and c.has_nonstandard_attr('HttpOnly')
            and c.get_nonstandard_attr('SameSite') == 'Strict' for c in owner.cookiejar))
        for role, amount in (('owner', 1250000), ('member', 50000), ('outsider', 50000)):
            admin.call('/api/user/manage', {'id': users[role]['id'], 'action': 'add_quota', 'mode': 'add', 'value': amount})
        first = owner.call('/api/workspace/key', {})
        check('personal_single_key_idempotent', first == owner.call('/api/workspace/key', {}))
        personal_key = owner.call('/api/workspace/key/reveal', {})['api_key']
        check('personal_key_repeat_reveal', personal_key == owner.call('/api/workspace/key/reveal', {})['api_key'])
        old_session = owner.token
        owner.call('/api/user/auth/logout', {})
        check('logged_out_session_rejected', API(old_session).denied('/api/workspace'))
        owner.login(users['owner']['username'], users['owner']['password'])
        check('personal_key_survives_logout_login', personal_key == owner.call('/api/workspace/key/reveal', {})['api_key'])
        check('ordinary_user_supplier_denied', owner.denied('/api/team/workspaces'))
        check('api_key_dashboard_denied', API(personal_key).denied('/api/workspace/key/reveal', {}))
        check('cross_site_management_denied', owner.denied('/api/workspace/team', {'name': 'must not exist'},
            headers={'Sec-Fetch-Site': 'cross-site'}))
        team = owner.call('/api/workspace/team', {'name': 'Release acceptance ' + run_id})
        report['team_id'] = team['id']
        invite = owner.call('/api/workspace/team/invites', {})
        member.call('/api/workspace/team/join', {'code': invite['code']})
        check('invite_cannot_replay', outsider.denied('/api/workspace/team/join', {'code': invite['code']}))
        check('invalid_invite_rejected', outsider.denied('/api/workspace/team/join', {'code': 'invalid-' + run_id}))
        path = '/api/workspace/team/members/' + str(users['member']['id'])
        member_key = member.call('/api/workspace/key/reveal', {})['api_key']
        tiny = {'model': 'gpt-6-luna', 'input': 'Reply OK only.', 'max_output_tokens': 1, 'stream': True}
        check('new_member_zero_allowance', member.call('/api/workspace')['balance_usd'] == 0)
        check('zero_allowance_relay_denied', API(member_key).denied('/v1/responses', tiny))
        for role, client in (('member', member), ('outsider', outsider)):
            check(role + '_cannot_raise_allowance', client.denied(path, {'allowance_usd': 999}, 'PATCH'))
            check(role + '_cannot_reveal_other_member', client.denied(path + '/key/reveal', {}))
        check('member_cannot_invite', member.denied('/api/workspace/team/invites', {}))
        check('negative_allowance_denied', owner.denied(path, {'allowance_usd': -1}, 'PATCH'))
        owner.call(path, {'allowance_usd': 1.5}, 'PATCH')
        token_id = member.call('/api/workspace')['api_key']['id']
        report['user_ids'] = {r: u['id'] for r, u in users.items()}
        report['token_id'] = token_id
        check('owner_and_member_same_key', member_key == owner.call(path + '/key/reveal', {})['api_key'])
        member_view = member.call('/api/workspace/team')
        check('member_view_only_self', 'pool_balance_usd' not in member_view and len(member_view['members']) == 1
            and member_view['members'][0]['user_id'] == users['member']['id'])
        check('owner_view_whole_team', len(owner.call('/api/workspace/team')['members']) == 2)
        check('supplier_can_read_teams', isinstance(admin.call('/api/team/workspaces'), (list, dict)))
        check('native_token_uncap_denied', owner.denied('/api/token/', {'id': token_id, 'unlimited_quota': True}, 'PUT'))
        before_disable = member.call('/api/workspace')
        owner.call(path, {'status': 2}, 'PATCH')
        check('disabled_member_relay_denied', API(member_key).denied('/v1/responses', tiny))
        owner.call(path, {'status': 1}, 'PATCH')
        check('enable_preserves_allowance', member.call('/api/workspace')['balance_usd'] == before_disable['balance_usd'])
        rotated = member.call('/api/workspace/key/rotate', {})['api_key']
        check('old_key_denied_after_rotation', rotated != member_key and API(member_key).denied('/v1/responses', tiny))
        check('rotated_key_persists', rotated == member.call('/api/workspace/key/reveal', {})['api_key'])
        users['member']['api_key'] = rotated
        persist_private()
        models = API(rotated).call('/v1/models')
        check('rotated_key_models_access', {'gpt-6-luna', 'gpt-6-sol', 'gpt-6-astra'}.issubset({m['id'] for m in models}))
        check('management_rejections_free', owner.call('/api/user/self')['used_quota'] == 0)
        _, _, reveal_headers, _ = member.request('/api/workspace/key/reveal', {})
        check('key_response_no_store', 'no-store' in reveal_headers.get('Cache-Control', ''))
        before = ledger(token_id)
        if args.images:
            download = private / 'downloads'
            download.mkdir()
            for name in ('setup.py', 'realyu_images.py', 'models.json'):
                code, _, _, content = anon.request('/downloads/realyu/' + name)
                check('published_' + name, code == 200 and content == (ROOT / 'web/public/downloads/realyu' / name).read_bytes())
                (download / name).write_bytes(content)
            spec = importlib.util.spec_from_file_location('realyu_release_setup', download / 'setup.py')
            setup = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(setup)
            setup.validate_key(rotated)
            codex = setup.find_codex()
            codex_home = private / 'codex'
            workspace = codex_home / 'workspace'
            setup.install(codex_home, Path(sys.executable), codex, rotated,
                (download / 'realyu_images.py').read_bytes(), (download / 'models.json').read_bytes())
            workspace.mkdir()
            config = (codex_home / 'config.toml').read_text(encoding='utf-8')
            # Only this disposable test home authorizes the two user-requested
            # image operations. Employee/global approval settings stay intact.
            assert config.count('approval_mode = "prompt"') == 2
            config = 'project_doc_max_bytes = 0\n' + config.replace('approval_mode = "prompt"', 'approval_mode = "approve"')
            (codex_home / 'config.toml').write_text(config, encoding='utf-8')
            env = {k: v for k, v in os.environ.items() if 'proxy' not in k.lower()
                and k not in ('OPENAI_API_KEY', 'OPENAI_BASE_URL', 'REALYU_API_KEY')}
            env['CODEX_HOME'] = str(codex_home)
            report['codex_version'] = subprocess.check_output([codex, '--version'], text=True).strip()
            check('isolated_codex_api_key_install', True)
            print(json.dumps({'phase': 'public_codex_image_generation_and_edit_started'}), flush=True)
            prompt = ('Use the configured Realyu image tools. Generate one high quality 1536x1024 image: '
                'a small orange kitten and fluffy white Bichon Frise jointly run a barbecue street-food shop '
                'in a rainy cyberpunk city, both clearly visible wearing aprons, sizzling skewers, neon teal '
                'and magenta lights, cozy warm charcoal, detailed cinematic illustration, no watermark. '
                'Then edit the returned image exactly once at high quality 1536x1024: make the kitten apron '
                'emerald green and the Bichon apron orange, preserving the animals, shop and composition. '
                'Exactly one generate_image and one edit_image call. Do not use shell or other tools. '
                'Never retry; if generation fails do not attempt the edit. Report the two returned image paths.')
            started = time.monotonic()
            run = subprocess.run([codex, '-c', 'model_providers.realyu.request_max_retries=0', '-c',
                'model_providers.realyu.stream_max_retries=0', '-a', 'never', 'exec', '--skip-git-repo-check',
                '--ephemeral', '--ignore-rules', '-s', 'workspace-write', '-C', str(workspace), '--json', prompt],
                env=env, capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=600)
            (private / 'codex.jsonl').write_text(run.stdout, encoding='utf-8')
            (private / 'codex.stderr').write_text(run.stderr, encoding='utf-8')
            report['image_seconds'] = round(time.monotonic() - started, 2)
            calls = []
            for line in run.stdout.splitlines():
                event = json.loads(line)
                if event.get('type') == 'item.completed' and event.get('item', {}).get('type') == 'mcp_tool_call':
                    calls.append(event['item'])
            report['image_tools'] = [{'name': c.get('tool'), 'status': c.get('status'), 'failed': bool(c.get('error') or c.get('result', {}).get('isError'))} for c in calls]
            check('codex_process_completed', run.returncode == 0)
            check('exactly_generate_and_edit_succeeded', [c['tool'] for c in calls] == ['generate_image', 'edit_image']
                and all(c.get('status') == 'completed' and not c.get('error') and not c.get('result', {}).get('isError') for c in calls))
            outputs = [json.loads(c['result']['content'][0]['text']) for c in calls]
            check('edit_uses_generated_file', calls[1]['arguments']['image_path'] == outputs[0]['file'])
            for name, output in zip(('generation', 'edit'), outputs):
                source = Path(output['file']).resolve()
                check(name + '_within_test_directory', source.is_relative_to(codex_home.resolve()))
                data = source.read_bytes()
                check(name + '_valid_png_1536x1024', data.startswith(b'\x89PNG\r\n\x1a\n') and
                    (int.from_bytes(data[16:20], 'big'), int.from_bytes(data[20:24], 'big')) == (1536, 1024))
                target = results / (name + '.png')
                target.write_bytes(data)
                report['artifacts'].append({'file': str(target), 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)})
            check('edit_is_distinct', report['artifacts'][0]['sha256'] != report['artifacts'][1]['sha256'])
            after = ledger(token_id)
            old_ids = {row['id'] for row in before['logs']}
            rows = [row for row in after['logs'] if row['id'] not in old_ids and row['type'] == 2]
            image_rows = [row for row in rows if any(s.get('name') == 'image_generation' for s in json.loads(row['other'] or '{}').get('tool_surcharges', []))]
            charge = before['owner']['quota'] - after['owner']['quota']
            report['charged_usd'] = charge / 500000
            report['consume_log_ids'] = [r['id'] for r in rows]
            report['image_channels'] = [r['channel_id'] for r in image_rows]
            check('two_image_bills', len(image_rows) == 2)
            check('each_image_charged_once', all(sum(s.get('count', 0) for s in json.loads(r['other']).get('tool_surcharges', []) if s.get('name') == 'image_generation') == 1 for r in image_rows))
            check('owner_token_ledger_exactly_reconcile', 0 < charge == after['token']['used_quota'] - before['token']['used_quota'] == sum(r['quota'] for r in rows))
            check('member_allowance_decreases_exactly', before['token']['remain_quota'] - after['token']['remain_quota'] == charge)
            check('member_personal_wallet_untouched', before['member']['quota'] == after['member']['quota'] and before['member']['used_quota'] == after['member']['used_quota'])
            check('image_attribution_correct', after['token']['workspace_user_id'] == users['member']['id'] and all(r['user_id'] == users['owner']['id'] for r in rows))
            check('bounded_test_spend', charge <= 750000)
            usage = member.call('/api/workspace/usage?user_id=' + str(users['owner']['id']))
            check('member_usage_matches_requests', usage['total'] == len(rows))
            check('usage_no_secrets', all(s not in json.dumps(usage) for s in (rotated, personal_key, invite['code'])))
        check('health_after_acceptance', anon.call('/api/status')['version'] == report['version'])
    except Exception as error:
        report['failure'] = {'type': type(error).__name__, 'stage': next(reversed(report['checks']), 'startup')}
        # Exception text and HTTP bodies can contain credentials; never publish.
        print(json.dumps({'failed': report['failure']}), flush=True)
    finally:
        for role in ('member', 'outsider', 'owner'):
            user = users.get(role)
            if not user:
                continue
            try:
                admin.call('/api/user/manage', {'id': user['id'], 'action': 'disable'})
                report['cleanup'][role + '_disabled'] = True
            except Exception:
                report['cleanup'][role + '_disabled'] = False
        report['all_passed'] = report['failure'] is None and bool(report['checks']) and all(report['checks'].values()) and all(report['cleanup'].values())
        report['finished_at'] = time.strftime('%Y-%m-%dT%H:%M:%S%z')
        save()
        print(json.dumps({'report': str(report_file), 'all_passed': report['all_passed'], 'cleanup': report['cleanup']}), flush=True)
    return 0 if report['all_passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

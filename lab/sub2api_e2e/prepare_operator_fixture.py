"""Create explicitly authorized, bounded release-only identities via normal APIs.

No model, payment, channel, option or existing-user mutation. Output is private
and is retained after failure for manual recovery, never silently replayed.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import secrets
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_provider_release as api


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--admin-config', required=True, type=Path)
    parser.add_argument('--database', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--create-authorized-operator-fixture', action='store_true')
    args = parser.parse_args()
    if not args.create_authorized_operator_fixture:
        parser.error('Explicit operator fixture authorization flag is required')
    if args.output.exists():
        raise RuntimeError('Existing output must be reviewed, not replayed')
    args.output.mkdir(parents=True)
    api.BASE = 'http://127.0.0.1:18300'

    class Session(api.API):
        def request(self, path, data=None, method=None, headers=None):
            return super().request(path, data, method, {'Origin': 'https://api.realyu.fun', **(headers or {})})

    cfg = json.loads(args.admin_config.read_text(encoding='utf-8-sig'))
    report = {'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'scope': 'Dedicated production operator identities only; no model request',
              'status': 'PREPARING', 'accounts': {}, 'subjects': [], 'dedicated_operator_subjects': True,
              'base_url': 'https://api.realyu.fun/v1', 'ledger_db': str(args.database.resolve()),
              'model': 'gpt-6.1-sol', 'channel_id': 4, 'sub2api_group_id': 2,
              'text_models': ['gpt-6-astra', 'gpt-6.1-sol', 'gpt-6-sol', 'gpt-6-luna',
                              'gpt-5.6-sol', 'gpt-5.6-terra', 'gpt-5.6-luna', 'gpt-5.5']}
    private = args.output / 'fixture.json'

    def save():
        private.write_text(json.dumps(report, indent=2), encoding='utf-8')

    sessions = []
    save()
    try:
        admin = Session()
        admin.login(cfg['admin_username'], cfg['admin_password'])
        sessions.append(admin)
        suffix = secrets.token_hex(2)
        for label, wallet_quota in (('owner', 250000), ('member', 500000)):
            username = 'realyu_release_' + suffix + label[0]
            account = {'username': username, 'password': secrets.token_urlsafe(24)}
            report['accounts'][label] = account
            save()
            admin.call('/api/user/', {**account, 'display_name': username, 'role': 1})
            session = Session()
            profile = session.login(account['username'], account['password'])
            sessions.append(session)
            assert profile['username'] == username and profile['role'] == 1
            account['id'] = profile['id']
            save()
            with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as db:
                existing = db.execute('SELECT username,quota,used_quota FROM users WHERE id=?', (account['id'],)).fetchone()
            assert existing and existing[0] == username and existing[2] == 0
            assert 0 <= existing[1] < wallet_quota, 'Unexpected starting test balance'
            grant = wallet_quota - existing[1]
            admin.call('/api/user/manage', {'id': account['id'], 'action': 'add_quota',
                                           'mode': 'add', 'value': grant})
            account['wallet_quota_granted'] = grant
            key_info = session.call('/api/workspace/key?scope=personal', {})
            key = session.call('/api/workspace/key/reveal?scope=personal', {})['api_key']
            report['subjects'].append({'label': 'personal-' + label, 'user_id': account['id'],
                'billing_owner_id': account['id'], 'workspace_team_id': 0, 'token_id': key_info['id'], 'api_key': key})
            save()
        owner_id = report['accounts']['owner']['id']
        member_id = report['accounts']['member']['id']
        team = admin.call('/api/subscription/admin/users/' + str(owner_id) + '/team-provision',
                          {'name': 'Release acceptance ' + suffix, 'weekly_usd': 0.5})
        report['team_id'] = team['team']['id']
        report['subscription_id'] = team['subscription']['id']
        report['team_weekly_quota_granted'] = 250000
        save()
        owner, member = sessions[1:]
        invite = owner.call('/api/workspace/team/invites', {})
        member.call('/api/workspace/team/join', {'code': invite['code']})
        owner.call('/api/workspace/team/members/' + str(member_id), {'allowance_usd': 0.5}, 'PATCH')
        team_key_info = member.call('/api/workspace/key?scope=team', {})
        team_key = member.call('/api/workspace/key/reveal?scope=team', {})['api_key']
        report['subjects'].append({'label': 'team-member', 'user_id': member_id, 'billing_owner_id': owner_id,
            'workspace_team_id': report['team_id'], 'token_id': team_key_info['id'], 'api_key': team_key})
        with sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            report['baseline'] = {
                'users': [dict(row) for row in db.execute('SELECT id,quota,used_quota,request_count,status FROM users WHERE id IN (?,?)', (owner_id, member_id))],
                'tokens': [dict(row) for row in db.execute('SELECT id,user_id,workspace_user_id,status,remain_quota,used_quota FROM tokens WHERE user_id IN (?,?)', (owner_id, member_id))],
                'subscription': dict(db.execute('SELECT id,user_id,amount_total,amount_used FROM user_subscriptions WHERE id=?', (report['subscription_id'],)).fetchone())}
        assert all(row['used_quota'] == 0 and row['request_count'] == 0 for row in report['baseline']['users'])
        assert all(row['used_quota'] == 0 for row in report['baseline']['tokens'])
        assert report['baseline']['subscription']['amount_used'] == 0
        report['status'] = 'IDENTITIES_READY_NO_INFERENCE'
        save()
        print(json.dumps({'status': report['status'], 'users': [owner_id, member_id],
                          'team_id': report['team_id'], 'token_ids': [s['token_id'] for s in report['subjects']]}))
    except Exception as exc:
        report['status'] = 'FAILED_PARTIAL_REVIEW_REQUIRED'
        report['error_type'] = type(exc).__name__
        save()
        raise
    finally:
        for session in reversed(sessions):
            session.logout()


if __name__ == '__main__':
    main()

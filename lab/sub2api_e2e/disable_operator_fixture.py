"""Disable only the reviewed release operator keys/users, retaining audit.

Uses normal APIs and read-only ownership/accounting verification. Does not
delete records, reset balances, or disable any upstream customer identity.
"""
import argparse
import datetime as dt
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import verify_provider_release as api


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--admin-config', required=True, type=Path)
    parser.add_argument('--secret-file', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--disable-authorized-operator-fixture', action='store_true')
    args = parser.parse_args()
    if not args.disable_authorized_operator_fixture:
        parser.error('Explicit fixture disable flag required')
    assert not args.output.exists(), 'Keep existing cleanup receipts'
    cfg = json.loads(args.secret_file.read_text(encoding='utf-8-sig'))
    assert cfg.get('dedicated_operator_subjects') is True
    accounts = cfg['accounts']
    assert len(accounts) == 2 and all(a['username'].startswith('realyu_release_') for a in accounts.values())
    user_ids = tuple(sorted(a['id'] for a in accounts.values()))
    expected_tokens = {row['id'] for row in cfg['baseline']['tokens']}
    assert len(user_ids) == 2 and len(expected_tokens) == 4
    database = Path(cfg['ledger_db']).resolve(strict=True)
    credentials = json.loads(args.admin_config.read_text(encoding='utf-8-sig'))
    api.BASE = 'http://127.0.0.1:18300'

    class Session(api.API):
        def request(self, path, data=None, method=None, headers=None):
            return super().request(path, data, method, {'Origin': 'https://api.realyu.fun', **(headers or {})})

    def snapshot():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            db.row_factory = sqlite3.Row
            users = [dict(r) for r in db.execute('SELECT id,username,status,quota,used_quota,request_count FROM users WHERE id IN (?,?) ORDER BY id', user_ids)]
            tokens = [dict(r) for r in db.execute('SELECT id,user_id,workspace_user_id,status,remain_quota,used_quota FROM tokens WHERE user_id IN (?,?) ORDER BY id', user_ids)]
        assert {t['id'] for t in tokens} == expected_tokens, 'Unexpected operator key inventory; review before changing it'
        assert {u['id']: u['username'] for u in users} == {a['id']: a['username'] for a in accounts.values()}, 'Operator identity mismatch'
        return {'users': users, 'tokens': tokens}

    report = {'status': 'RUNNING', 'started_at': dt.datetime.now(dt.timezone.utc).isoformat(),
              'scope': 'Only dedicated release test users and their personal keys; all four keys must be denied; no deletes or ledger resets',
              'before': snapshot(), 'keys_disabled': [], 'users_disabled': [], 'team_keys_revoked_by_user_status': []}

    def save():
        args.output.write_text(json.dumps(report, indent=2))

    save()
    admin = Session()
    try:
        admin.login(credentials['admin_username'], credentials['admin_password'])
        for account in accounts.values():
            session = Session()
            try:
                session.login(account['username'], account['password'])
                for token in report['before']['tokens']:
                    if token['user_id'] != account['id']:
                        continue
                    # Team keys belong to the workspace API; generic token
                    # updates are intentionally rejected. Disabling both fixture
                    # users revokes their access while preserving team audit.
                    if token['workspace_user_id']:
                        report['team_keys_revoked_by_user_status'].append(token['id'])
                        continue
                    if token['status'] != 2:
                        session.call('/api/token/?status_only=1', {'id': token['id'], 'status': 2}, 'PUT')
                    report['keys_disabled'].append(token['id'])
                    save()
            finally:
                session.logout()
        for account in accounts.values():
            admin.call('/api/user/manage', {'id': account['id'], 'action': 'disable'})
            report['users_disabled'].append(account['id'])
            save()
        report['after'] = snapshot()
        assert all(row['status'] == 2 for row in report['after']['users'])
        assert all(row['status'] == 2 for row in report['after']['tokens'] if not row['workspace_user_id'])
        for kind in ('users', 'tokens'):
            strip_status = lambda rows: [{k: v for k, v in row.items() if k != 'status'} for row in rows]
            assert strip_status(report['before'][kind]) == strip_status(report['after'][kind]), 'Cleanup altered accounting state'
        report['disabled_key_readonly_denials'] = []
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True) as db:
            key_rows = db.execute('SELECT id,key FROM tokens WHERE user_id IN (?,?) ORDER BY id', user_ids).fetchall()
        assert {row[0] for row in key_rows} == expected_tokens
        for token_id, key in key_rows:
            status, value, _, _ = Session('sk-' + key.removeprefix('sk-')).request('/v1/models')
            denied = status in (401, 403)
            report['disabled_key_readonly_denials'].append({'token_id': token_id, 'http_status': status, 'denied': denied})
            assert denied, 'Disabled key still passed model catalog authentication'
        report['status'] = 'PASS'
    except Exception as exc:
        report.update(status='FAIL_PARTIAL_REVIEW_REQUIRED', error_type=type(exc).__name__)
        if isinstance(exc, AssertionError):
            report['assertion'] = str(exc)
        raise
    finally:
        admin.logout()
        save()
    print(json.dumps({'status': report['status'], 'keys_disabled': report['keys_disabled'], 'users_disabled': report['users_disabled']}))


if __name__ == '__main__':
    main()

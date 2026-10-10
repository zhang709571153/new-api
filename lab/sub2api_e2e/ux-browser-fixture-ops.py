"""Strictly isolated fixture evidence for the RealYu UX browser E2E.

Default is offline. The only SQL mutation is a one-time $20 synthetic wallet
seed for a newly browser-registered ux-e2e user with zero prior activity.
No production configuration, imported customer, credential or API key is output.
"""
import argparse
import json
import re
from decimal import Decimal
from pathlib import Path

ROOT = Path(r'C:\srv\realyu-singlecore-dev\runtime')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--isolated-confirmed', action='store_true')
    parser.add_argument('--mode', choices=['preflight', 'registered', 'seed-wallet', 'purchase'], default='preflight')
    parser.add_argument('--login')
    parser.add_argument('--user-id', type=int)
    parser.add_argument('--plan-id', type=int)
    args = parser.parse_args()
    if not args.isolated_confirmed:
        print(json.dumps({'status': 'PREPARED_NOT_RUN', 'database_accessed': False}))
        return
    import psycopg
    p = json.loads((ROOT / 'private.json').read_text(encoding='utf-8-sig'))
    f = json.loads((ROOT / 'team-http-fixture.private.json').read_text(encoding='utf-8-sig'))
    assert (p['host'], p['port'], p['database'], p['api_port']) == ('127.0.0.1', 29490, 'realyu_singlecore_e2e', 29480)
    assert f['database'] == p['database'] and f['prefix'] == 'team-e2e-20261010'
    assert f['target'] == 'http://127.0.0.1:29480'
    is_seed = args.mode == 'seed-wallet'
    options = '-c statement_timeout=5000 -c lock_timeout=3000'
    if not is_seed:
        options += ' -c default_transaction_read_only=on'
    with psycopg.connect(host=p['host'], port=p['port'], dbname=p['database'], user=p['user'], password=p['password'],
                         application_name='synthetic-ux-browser-fixture', options=options) as conn:
        for identity in ['owner', 'member', 'admin', 'role10']:
            uid = f['users'][identity]['id']
            login = f['prefix'] + '-' + identity
            assert conn.execute('SELECT login_name FROM realyu_legacy_identities WHERE user_id=%s', (uid,)).fetchone() == (login,)
        assert conn.execute('SELECT status,schedulable FROM accounts WHERE id=%s', (f['account_id'],)).fetchone() == ('disabled', False)
        assert conn.execute('SELECT status,subscription_type,deleted_at FROM groups WHERE id=%s', (f['group_id'],)).fetchone() == ('active', 'standard', None)
        owner, member, team = f['users']['owner']['id'], f['users']['member']['id'], f['teams']['a']
        assert conn.execute('SELECT owner_user_id FROM realyu_teams WHERE id=%s', (team,)).fetchone() == (owner,)
        snapshot = {'synthetic_only': True, 'database': p['database'], 'upstream_disabled': True}
        snapshot['owner_wallet_usd'] = conn.execute('SELECT balance::text FROM users WHERE id=%s', (owner,)).fetchone()[0]
        snapshot['member_state'] = conn.execute('SELECT weekly_cap_quota,status,nickname FROM realyu_team_members WHERE team_id=%s AND user_id=%s', (team, member)).fetchone()
        snapshot['member_period_usage'] = conn.execute('SELECT subscription_id,weekly_reset_at,used_quota,opening_used_quota FROM realyu_member_period_usage WHERE team_id=%s AND user_id=%s ORDER BY subscription_id,weekly_reset_at', (team, member)).fetchall()
        snapshot['owner_subscription'] = conn.execute('SELECT amount_total,amount_used,weekly_amount,weekly_used,weekly_reset_at,status FROM realyu_funding_subscriptions WHERE id=%s AND user_id=%s AND team_id=%s', (f['subscription_id'], owner, team)).fetchone()
        snapshot['owner_scope_counts'] = conn.execute('SELECT CASE WHEN s.team_id IS NULL THEN %s ELSE %s END,count(*) FROM api_keys k JOIN realyu_key_scopes s ON s.api_key_id=k.id WHERE k.user_id=%s AND k.deleted_at IS NULL GROUP BY 1 ORDER BY 1', ('personal', 'team', owner)).fetchall()
        if args.mode != 'preflight':
            assert args.login and re.fullmatch(r'ux-e2e-20261010-[a-z0-9-]{8,30}', args.login)
            assert args.user_id and args.user_id not in [u['id'] for u in f['users'].values()]
            row = conn.execute('SELECT u.id,u.signup_source,u.balance::text,u.status,u.deleted_at,i.source_user_id FROM users u JOIN realyu_legacy_identities i ON i.user_id=u.id WHERE i.login_name=%s', (args.login,)).fetchone()
            assert row and row[0] == args.user_id and row[1] == 'realyu_username' and row[3:] == ('active', None, None)
            snapshot['new_user_id'] = row[0]
            snapshot['new_wallet_usd'] = row[2]
            keys = conn.execute('SELECT k.id,k.status,k.group_id,s.actor_user_id,s.payer_user_id,s.team_id FROM api_keys k JOIN realyu_key_scopes s ON s.api_key_id=k.id WHERE k.user_id=%s AND k.deleted_at IS NULL ORDER BY k.id', (args.user_id,)).fetchall()
            assert len(keys) == 1 and keys[0][1:] == ('active', f['group_id'], args.user_id, args.user_id, None)
            snapshot['default_personal_keys'] = len(keys)
            snapshot['new_team_memberships'] = conn.execute('SELECT count(*) FROM realyu_team_members WHERE user_id=%s', (args.user_id,)).fetchone()[0]
            snapshot['new_stock_subscriptions'] = conn.execute('SELECT count(*) FROM user_subscriptions WHERE user_id=%s', (args.user_id,)).fetchone()[0]
            assert snapshot['new_team_memberships'] == 0 and snapshot['new_stock_subscriptions'] == 0
            order_count = conn.execute('SELECT count(*) FROM payment_orders WHERE user_id=%s', (args.user_id,)).fetchone()[0]
            managed_count = conn.execute('SELECT count(*) FROM realyu_funding_subscriptions WHERE user_id=%s', (args.user_id,)).fetchone()[0]
            snapshot['new_order_count'] = order_count
            snapshot['new_managed_count'] = managed_count
            if args.mode in ('registered', 'seed-wallet'):
                assert Decimal(row[2]) == 0 and order_count == 0 and managed_count == 0
            if is_seed:
                updated = conn.execute('UPDATE users SET balance=20,updated_at=NOW() WHERE id=%s AND balance=0 AND signup_source=%s RETURNING balance::text', (args.user_id, 'realyu_username')).fetchone()
                assert updated and Decimal(updated[0]) == 20
                snapshot['new_wallet_usd'] = updated[0]
                snapshot['synthetic_wallet_seed_usd'] = '20'
            if args.mode == 'purchase':
                assert args.plan_id and args.plan_id > 0
                name = conn.execute('SELECT name FROM subscription_plans WHERE id=%s', (args.plan_id,)).fetchone()
                assert name and name[0] == args.login + '-plan'
                snapshot['orders'] = conn.execute('SELECT id,plan_id,status,amount::text,pay_amount::text,payment_type,order_type FROM payment_orders WHERE user_id=%s ORDER BY id', (args.user_id,)).fetchall()
                snapshot['grants'] = conn.execute('SELECT s.order_id,s.payment_source,g.subscription_id,g.wallet_debit_quota FROM realyu_purchase_snapshots s JOIN realyu_purchase_grants g ON g.order_id=s.order_id WHERE s.actor_user_id=%s AND s.payer_user_id=%s AND s.team_id IS NULL ORDER BY s.order_id', (args.user_id, args.user_id)).fetchall()
                snapshot['subscriptions'] = conn.execute('SELECT id,team_id,amount_total,amount_used,weekly_amount,weekly_used,status,purchase_price_cents,purchase_title FROM realyu_funding_subscriptions WHERE user_id=%s ORDER BY id', (args.user_id,)).fetchall()
        print(json.dumps(snapshot, default=str))


if __name__ == '__main__':
    main()

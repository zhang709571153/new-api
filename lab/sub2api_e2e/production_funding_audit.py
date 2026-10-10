"""Read-only, post-ACTIVE RealYu funding reconciliation. Never sends API requests.

Snapshots contain pseudonymous IDs and monetary state, but no keys, names,
emails, request payloads, passwords or connection strings. Keep them private.
Each capture is one PostgreSQL REPEATABLE READ READ ONLY transaction. Financial
residuals are evidence requiring investigation, not proof of a billing defect:
admin credits, purchases, deletions and native non-managed traffic can coexist.
Usage logs are asynchronous; a missing log is PENDING, never fabricated PASS.
"""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
from pathlib import Path
import sys
import uuid

ROOT = Path(r'C:\srv\realyu-singlecore-dev\runtime')
PLAN = ROOT / 'production-cutover-20261010' / 'plan.json'
CREDENTIALS = Path(r'C:\srv\realyu-newapi-releases\20260924-provider-2bc6a0e3\.lab\credentials.json')
QUOTA = Decimal(500000)


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def quota(value):
    value = Decimal(str(value)) * QUOTA
    if not value.is_finite() or value != value.to_integral_value():
        raise ValueError('NON_INTEGER_QUOTA_NO_ROUNDING_ALLOWED')
    return int(value)


def scaled_decimal(value):
    """Preserve native numeric precision, including fractional quota evidence."""
    value = Decimal(str(value)) * QUOTA
    if not value.is_finite():
        raise ValueError('NON_FINITE_MONETARY_VALUE')
    return value


def save_new(path, value):
    # Exclusive creation preserves the first result, including failed checks.
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, sort_keys=True, indent=2)
        stream.flush()
        import os
        os.fsync(stream.fileno())


class Labels:
    def __init__(self, key):
        self.key = hmac.new(key.encode(), b'realyu-production-audit-v1', hashlib.sha256).digest()

    def __call__(self, kind, *parts):
        if len(parts) == 1 and parts[0] is None:
            return None
        raw = json.dumps([kind, *parts], separators=(',', ':')).encode()
        return kind + '-' + hmac.new(self.key, raw, hashlib.sha256).hexdigest()[:24]


def capture(plan_path=PLAN, credentials_path=CREDENTIALS):
    import psycopg
    from psycopg.rows import dict_row
    plan = read(plan_path)
    authority = read(Path(plan['runtime_directory']) / 'authority-receipt.json')
    if authority.get('phase') != 'ACTIVE' or authority.get('opened') is not True:
        raise ValueError('ACTIVE_AUTHORITY_REQUIRED_NO_PRE_MIGRATION_BASELINE')
    cfg = read(plan['target_config'])
    if (cfg.get('host'), cfg.get('port'), cfg.get('database'), cfg.get('user')) != (
        '127.0.0.1', 28490, 'realyu_singlecore_20261010_candidate', 'realyu_singlecore_owner_20261010'
    ):
        raise ValueError('UNEXPECTED_PRODUCTION_TARGET')
    key = read(credentials_path)['api_key']
    key = 'sk-' + key.removeprefix('sk-')
    label = Labels(key)
    with psycopg.connect(host=cfg['host'], port=cfg['port'], dbname=cfg['database'],
                         user=cfg['user'], password=cfg['password'], connect_timeout=5,
                         options='-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=2000',
                         application_name='realyu-readonly-postactive-audit', row_factory=dict_row) as conn:
        conn.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        phase = conn.execute('SELECT installation_id,phase FROM realyu_migration_state WHERE singleton').fetchone()
        if not phase or phase['phase'] != 'active' or phase['installation_id'] != cfg['installation_id']:
            raise ValueError('ACTIVE_TARGET_GENERATION_REQUIRED')
        matched = conn.execute('SELECT id FROM api_keys WHERE key=%s', (key,)).fetchall()
        if len(matched) != 1:
            raise ValueError('EXACT_ACCEPTANCE_KEY_REQUIRED')
        selected = label('key', matched[0]['id'])
        timestamp = conn.execute('SELECT transaction_timestamp() AS at,pg_current_snapshot()::text AS snapshot').fetchone()
        users = {label('user', r['id']): str(r['balance']) for r in conn.execute('SELECT id,balance FROM users')}
        scopes = {}
        keys = {}
        for r in conn.execute('SELECT s.*,k.user_id,k.quota,k.quota_used,k.status FROM realyu_key_scopes s JOIN api_keys k ON k.id=s.api_key_id'):
            k = label('key', r['api_key_id'])
            scopes[k] = {'actor': label('user', r['actor_user_id']), 'payer': label('user', r['payer_user_id']),
                         'team': label('team', r['team_id']), 'key_owner': label('user', r['user_id'])}
            keys[k] = {'used': str(scaled_decimal(r['quota_used'])), 'used_usd': str(r['quota_used']),
                       'limit': str(r['quota']), 'status': r['status']}
        if selected not in scopes:
            raise ValueError('ACCEPTANCE_KEY_NOT_MANAGED')
        teams = {label('team', r['id']): label('user', r['owner_user_id']) for r in conn.execute('SELECT id,owner_user_id FROM realyu_teams')}
        subscriptions = {}
        for r in conn.execute('SELECT id,user_id,team_id,amount_used,weekly_amount,weekly_used,weekly_reset_at FROM realyu_funding_subscriptions'):
            subscriptions[label('sub', r['id'])] = {'user': label('user', r['user_id']), 'team': label('team', r['team_id']),
                'used': r['amount_used'], 'weekly_amount': r['weekly_amount'], 'weekly_used': r['weekly_used'], 'reset': r['weekly_reset_at']}
        members = {}
        for r in conn.execute('SELECT * FROM realyu_member_period_usage'):
            m = label('period', r['team_id'], r['user_id'], r['subscription_id'], r['weekly_reset_at'])
            members[m] = {'used': r['used_quota'], 'opening': r['opening_used_quota']}
        requests = {}
        for r in conn.execute('SELECT request_id,api_key_id,actor_user_id,payer_user_id,team_id,subscription_id,weekly_reset_at,status,subscription_quota,wallet_quota,target_quota,request_fingerprint FROM realyu_funding_requests'):
            rid = label('request', r['request_id'], r['api_key_id'])
            requests[rid] = {'key': label('key', r['api_key_id']), 'actor': label('user', r['actor_user_id']),
                'payer': label('user', r['payer_user_id']), 'team': label('team', r['team_id']), 'sub': label('sub', r['subscription_id']),
                'period': label('period', r['team_id'], r['actor_user_id'], r['subscription_id'], r['weekly_reset_at']) if r['team_id'] else None,
                'reset': r['weekly_reset_at'], 'status': r['status'], 'subscription': r['subscription_quota'],
                'wallet': r['wallet_quota'], 'target': r['target_quota'], 'fingerprint': r['request_fingerprint']}
        usage = defaultdict(list)
        for r in conn.execute('SELECT u.request_id,u.api_key_id,u.user_id,u.actual_cost FROM usage_logs u JOIN realyu_funding_requests f ON f.request_id=u.request_id AND f.api_key_id=u.api_key_id'):
            usage[label('request', r['request_id'], r['api_key_id'])].append({'actor': label('user', r['user_id']), 'actual_usd': str(r['actual_cost'])})
        dedup = defaultdict(set)
        for r in conn.execute('SELECT d.request_id,d.api_key_id,d.request_fingerprint FROM (SELECT request_id,api_key_id,request_fingerprint FROM usage_billing_dedup UNION ALL SELECT request_id,api_key_id,request_fingerprint FROM usage_billing_dedup_archive) d JOIN realyu_funding_requests f ON f.request_id=d.request_id AND f.api_key_id=d.api_key_id'):
            dedup[label('request', r['request_id'], r['api_key_id'])].add(r['request_fingerprint'])
        result = {'schema': 1, 'captured_at': timestamp['at'].isoformat(), 'pg_snapshot': timestamp['snapshot'],
                  'installation_id': cfg['installation_id'], 'selected_key': selected, 'read_only': True,
                  'users': users, 'keys': keys, 'scopes': scopes, 'teams': teams, 'subscriptions': subscriptions,
                  'members': members, 'requests': requests, 'usage': dict(usage), 'dedup': {k: sorted(v) for k, v in dedup.items()}}
        conn.rollback()
        return result


def totals(snapshot):
    out = {k: defaultdict(int) for k in ('wallet', 'sub', 'member', 'key')}
    for r in snapshot['requests'].values():
        out['wallet'][r['payer']] += r['wallet']
        if r['sub']:
            out['sub'][r['sub']] += r['subscription']
        if r['period']:
            out['member'][r['period']] += r['target']
        if r['status'] == 'settled':
            out['key'][r['key']] += r['target']
    return out


def compare(before, after):
    if before['installation_id'] != after['installation_id'] or before['selected_key'] != after['selected_key']:
        raise ValueError('SNAPSHOT_GENERATION_OR_KEY_MISMATCH')
    if after['captured_at'] < before['captured_at']:
        raise ValueError('SNAPSHOT_ORDER_INVALID')
    a, b = totals(before), totals(after)
    violations, residuals, pending, period_changes = [], [], [], []
    changed = {k for k in before['requests'].keys() | after['requests'].keys() if before['requests'].get(k) != after['requests'].get(k)}
    for rid in changed:
        r = after['requests'].get(rid)
        old = before['requests'].get(rid)
        if r is None:
            violations.append({'request': rid, 'code': 'FUNDING_RECORD_REMOVED'})
            continue
        s = after['scopes'].get(r['key'])
        if not s or any(r[k] != s[k] for k in ('actor', 'payer', 'team')) or s['actor'] != s['key_owner']:
            violations.append({'request': rid, 'code': 'ACTOR_PAYER_SCOPE_MISMATCH'})
        if r['team'] and (after['teams'].get(r['team']) != r['payer'] or r['wallet'] != 0):
            violations.append({'request': rid, 'code': 'TEAM_PAYER_OR_PERSONAL_SPILL'})
        if not r['team'] and r['actor'] != r['payer']:
            violations.append({'request': rid, 'code': 'PERSONAL_PAYER_MISMATCH'})
        if r['subscription'] + r['wallet'] != r['target'] or min(r['subscription'], r['wallet'], r['target']) < 0:
            violations.append({'request': rid, 'code': 'QUOTA_SPLIT_INVALID'})
        if r['sub']:
            sub = after['subscriptions'].get(r['sub'])
            if not sub or sub['team'] != r['team'] or sub['user'] != r['payer']:
                violations.append({'request': rid, 'code': 'SUBSCRIPTION_SCOPE_MISMATCH'})
        if old and old['status'] in ('settled', 'refunded') and old != r:
            violations.append({'request': rid, 'code': 'FINAL_FUNDING_RECORD_MUTATED'})
        if r['status'] == 'refunded' and r['target'] != 0:
            violations.append({'request': rid, 'code': 'REFUND_NOT_ZERO'})
        d, logs = after['dedup'].get(rid, []), after['usage'].get(rid, [])
        if r['status'] == 'settled':
            if len(d) != 1:
                violations.append({'request': rid, 'code': 'SETTLED_DEDUP_MISSING_OR_CONFLICT'})
            if not logs:
                pending.append({'request': rid, 'code': 'ASYNC_USAGE_LOG_PENDING'})
            elif len(logs) != 1:
                violations.append({'request': rid, 'code': 'DUPLICATE_USAGE_LOG'})
            elif logs[0]['actor'] != r['actor'] or scaled_decimal(logs[0]['actual_usd']) != r['target']:
                violations.append({'request': rid, 'code': 'USAGE_CHARGE_OR_ACTOR_MISMATCH',
                                   'native_actual_quota_exact': str(scaled_decimal(logs[0]['actual_usd'])),
                                   'funding_quota': r['target']})
        elif r['status'] == 'reserved':
            pending.append({'request': rid, 'code': 'RESERVATION_PENDING'})
            if d:
                violations.append({'request': rid, 'code': 'UNSETTLED_WITH_BILLING_DEDUP'})
        elif d:
            violations.append({'request': rid, 'code': 'REFUNDED_WITH_BILLING_DEDUP'})
    checks = Counter()
    def check(kind, entity, actual, expected):
        checks[kind] += 1
        if actual != expected:
            residuals.append({'kind': kind, 'entity': entity, 'actual_delta_quota': str(actual),
                              'funding_delta_quota': str(expected), 'unexplained_quota': str(actual - expected)})
    for user in before['users'].keys() | after['users'].keys():
        if user not in before['users'] or user not in after['users']:
            residuals.append({'kind': 'user', 'entity': user, 'code': 'USER_CREATED_OR_REMOVED_DURING_WINDOW'})
            continue
        actual = (Decimal(after['users'][user]) - Decimal(before['users'][user])) * QUOTA
        check('wallet', user, actual, -(b['wallet'][user] - a['wallet'][user]))
    for table, total, kind in (('keys', 'key', 'key_quota_used'), ('subscriptions', 'sub', 'subscription_used'), ('members', 'member', 'member_period_used')):
        for entity in before[table].keys() | after[table].keys():
            old, new = before[table].get(entity), after[table].get(entity)
            if new is None or (old is None and table != 'members'):
                residuals.append({'kind': kind, 'entity': entity, 'code': 'COUNTER_CREATED_OR_REMOVED_DURING_WINDOW'})
                continue
            check(kind, entity, Decimal(str(new['used'])) - Decimal(str((old or {}).get('used', 0))), b[total][entity] - a[total][entity])
            if table == 'members' and old and new['opening'] != old['opening']:
                violations.append({'entity': entity, 'code': 'MEMBER_OPENING_MUTATED'})
            if table == 'subscriptions' and old and new['weekly_amount'] > 0:
                if new['reset'] != old['reset']:
                    period_changes.append(entity)
                else:
                    def period_sum(snap):
                        return sum(r['subscription'] for r in snap['requests'].values() if r['sub'] == entity and r['reset'] == new['reset'])
                    check('subscription_weekly', entity, new['weekly_used'] - old['weekly_used'], period_sum(after) - period_sum(before))
    def native_usage(snap, key):
        exact = Decimal(0)
        for rid, request in snap['requests'].items():
            if request['key'] != key or request['status'] != 'settled':
                continue
            logs = snap['usage'].get(rid, [])
            if len(logs) != 1:
                return None
            exact += scaled_decimal(logs[0]['actual_usd'])
        return exact
    precision = []
    for key in before['keys'].keys() & after['keys'].keys():
        old = Decimal(str(before['keys'][key]['used']))
        new = Decimal(str(after['keys'][key]['used']))
        if old != old.to_integral_value() or new != new.to_integral_value():
            precision.append({'key': key, 'before_quota_exact': str(old), 'after_quota_exact': str(new),
                              'delta_quota_exact': str(new - old), 'rounded': False})
        old_usage, new_usage = native_usage(before, key), native_usage(after, key)
        if old_usage is None or new_usage is None:
            if any(r['key'] == key for r in after['requests'].values()):
                pending.append({'key': key, 'code': 'KEY_ACTUAL_COST_COVERAGE_PENDING'})
        else:
            check('key_quota_vs_native_actual_cost', key, new - old, new_usage - old_usage)
    scoped = [after['requests'][r] for r in changed if r in after['requests']]
    selected = [r for r in scoped if r['key'] == after['selected_key']]
    status = ('REVIEW_REQUIRED' if violations or residuals or period_changes else 'PENDING' if pending
              else 'PASS' if changed else 'NO_NEW_REQUESTS')
    return {'schema': 1, 'status': status, 'before_at': before['captured_at'], 'after_at': after['captured_at'],
            'selected_key_request_statuses': dict(Counter(r['status'] for r in selected)),
            'concurrent_other_key_request_statuses': dict(Counter(r['status'] for r in scoped if r['key'] != after['selected_key'])),
            'selected_key_settled_quota': sum(r['target'] for r in selected if r['status'] == 'settled'),
            'all_request_statuses_after': dict(Counter(r['status'] for r in after['requests'].values())),
            'changed_requests': len(changed), 'checks': dict(checks), 'violations': violations,
            'unexplained_financial_changes': residuals, 'pending': pending, 'weekly_period_changes': period_changes,
            'native_fractional_quota_observations': precision,
            'limitations': ['No paid requests were generated or replayed.', 'One key can have concurrent callers; selected-key totals are not an exact test-run attribution.',
                           'Administrative credits, purchases and non-managed usage require separate attribution if residuals exist.',
                           'A missing asynchronous log is pending; repeat after against the same before file to retain the first result.',
                           'Native USD numeric precision is retained exactly. Fractional opening quota is observed, never rounded or rejected during capture.',
                           'This observes database settlement and dedup state; it does not prove upstream usage with no submitted funding record was durably recorded.']}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action', choices=('before', 'after'))
    p.add_argument('--plan', type=Path, default=PLAN)
    p.add_argument('--credentials', type=Path, default=CREDENTIALS)
    p.add_argument('--before', type=Path)
    p.add_argument('--out-dir', type=Path, default=ROOT / 'production-funding-audit')
    args = p.parse_args()
    suffix = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:10]
    try:
        if args.action == 'after' and not args.before:
            raise ValueError('BEFORE_SNAPSHOT_REQUIRED')
        args.out_dir.resolve().relative_to(ROOT.resolve())
        args.out_dir.mkdir(exist_ok=True)
        snap = capture(args.plan, args.credentials)
        path = args.out_dir / (args.action + '-' + suffix + '.private.json')
        save_new(path, snap)
        result = {'status': 'CAPTURED', 'snapshot_file': str(path), 'read_only': True}
        if args.action == 'after':
            report = compare(read(args.before), snap)
            report['before_file'] = str(args.before)
            report['after_file'] = str(path)
            report_path = args.out_dir / ('reconciliation-' + suffix + '.json')
            save_new(report_path, report)
            result.update(status=report['status'], report_file=str(report_path), changed_requests=report['changed_requests'],
                          violations=len(report['violations']), residuals=len(report['unexplained_financial_changes']), pending=len(report['pending']))
        print(json.dumps(result)); return 0 if result['status'] in ('CAPTURED', 'PASS') else 2
    except Exception as error:
        # Do not echo exception messages: connection/JSON errors may contain secrets.
        failure = {'status': 'BLOCKED', 'error_type': type(error).__name__, 'private_values_logged': False}
        try:
            args.out_dir.resolve().relative_to(ROOT.resolve())
            save_new(args.out_dir / ('capture-error-' + suffix + '.json'), failure)
        except Exception:
            pass
        print(json.dumps(failure))
        return 1


if __name__ == '__main__':
    sys.exit(main())

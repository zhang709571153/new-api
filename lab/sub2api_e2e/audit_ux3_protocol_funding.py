"""Host-specific READ ONLY audit of the already-completed UX3 three-turn probe.

Never sends model/API requests. Credentials are read in memory; result files do
not include keys, customer IDs, names, prompts or responses. Correlation is
explicitly key + bounded time + unique token/cache signature and HTTP log key,
not a claim that upstream response IDs equal the immutable funding request ID.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import hashlib
import json
import uuid

ROOT = Path(r'C:\srv\realyu-singlecore-dev\runtime')
PLAN = ROOT / 'production-cutover-20261010-r3/plan.json'
SUMMARY = ROOT / 'production-ux3-protocol-20261011-0828/summary.json'
CREDS = Path(r'C:\srv\realyu-newapi-releases\20260924-provider-2bc6a0e3\.lab\credentials.json')
MANIFEST = Path(r'C:\ProgramData\RealYuServices\manifest.json')
LOG = Path(r'C:\ProgramData\RealYuServices\logs\api\RealYuApi.out.log')
EXPECTED_SHA = '353428ccb6a09a3f512bc8bced5c23364c0d4f7cb46f5cecccaea24782446569'
NAMES = ('responses-stream', 'websocket-turn-1', 'websocket-turn-2')


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def require(ok, code):
    if not ok:
        raise ValueError(code)


def save(path, value):
    with Path(path).open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2)


def token_signature(row):
    u = row['usage']
    detail = u.get('input_tokens_details', {})
    cached, written = detail.get('cached_tokens', 0), detail.get('cache_write_tokens', 0)
    return (u['input_tokens'] - cached - written, u['output_tokens'], cached, written)


def run(result):
    import psycopg
    from psycopg.rows import dict_row
    protocol = read(SUMMARY)
    require(protocol['status'] == 'PASS' and protocol['model_requests_attempted'] == 3, 'EXACT_THREE_SUCCESSFUL_PROBES_REQUIRED')
    selected = [r for r in protocol['requests'] if r['name'] in NAMES]
    require(tuple(r['name'] for r in selected) == NAMES and all(r['status'] == 'PASS' for r in selected), 'EXACT_PROBE_SET_REQUIRED')
    require(len({r['response_id'] for r in selected}) == 3, 'DISTINCT_RESPONSE_IDS_REQUIRED')
    require(len({token_signature(r) for r in selected}) == 3, 'TOKEN_CORRELATION_AMBIGUOUS')
    started = datetime.fromisoformat(protocol['started_at'])
    finished = datetime.fromisoformat(protocol['finished_at'])
    require(timedelta(0) < finished - started < timedelta(seconds=20), 'PROBE_WINDOW_UNEXPECTED')
    lower, upper = started - timedelta(milliseconds=500), finished + timedelta(milliseconds=500)
    result.update(protocol_summary_sha256=digest(SUMMARY), expected_binary_sha256=EXPECTED_SHA,
                  window_start=lower.isoformat(), window_end_exclusive=upper.isoformat(),
                  correlation='Exact acceptance key + fixed protocol window + three unique input/output/cache signatures; native HTTP/WS logs bind protocol request IDs to that key. Upstream response ID is not the funding request primary key.')
    manifest = read(MANIFEST)
    native = manifest['singlecore_api']
    require(native['sha'] == EXPECTED_SHA and digest(native['exe']) == EXPECTED_SHA, 'ACTIVE_MANIFEST_BINARY_MISMATCH')
    plan = read(PLAN)
    authority = read(Path(plan['runtime_directory']) / 'authority-receipt.json')
    require(authority.get('phase') == 'ACTIVE' and authority.get('opened') is True, 'ACTIVE_REQUIRED')
    cfg = read(plan['target_config'])
    require((cfg['host'], cfg['port'], cfg['database'], cfg['user']) == (
        '127.0.0.1', 28490, 'realyu_singlecore_20261010_candidate', 'realyu_singlecore_owner_20261010'), 'WRONG_DATABASE')
    env = read(native['env_file'])
    require(str(env['DATABASE_PORT']) == str(cfg['port']) and env['DATABASE_HOST'] == cfg['host']
            and env['DATABASE_DBNAME'] == cfg['database'] and env['DATABASE_USER'] == cfg['user'], 'MANIFEST_DATABASE_MISMATCH')
    key = 'sk-' + read(CREDS)['api_key'].removeprefix('sk-')
    with psycopg.connect(host=cfg['host'], port=cfg['port'], dbname=cfg['database'], user=cfg['user'],
                         password=cfg['password'], connect_timeout=5,
                         options='-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=2000',
                         application_name='realyu-readonly-ux3-protocol-audit', row_factory=dict_row) as db:
        db.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        require(db.execute('SHOW transaction_read_only').fetchone()['transaction_read_only'] == 'on', 'READ_ONLY_REQUIRED')
        phase = db.execute('SELECT installation_id,phase FROM realyu_migration_state WHERE singleton').fetchone()
        require(phase['phase'] == 'active' and phase['installation_id'] == cfg['installation_id'], 'INSTALLATION_MISMATCH')
        keys = db.execute('SELECT id,user_id FROM api_keys WHERE key=%s', (key,)).fetchall()
        require(len(keys) == 1, 'EXACT_ACCEPTANCE_KEY_REQUIRED')
        keyid = keys[0]['id']
        scope = db.execute('SELECT actor_user_id,payer_user_id,team_id FROM realyu_key_scopes WHERE api_key_id=%s', (keyid,)).fetchone()
        require(scope and scope['actor_user_id'] == keys[0]['user_id'], 'KEY_ACTOR_SCOPE_MISMATCH')
        # Log correlation checks only known request IDs and allowlisted numeric fields.
        seen_log_ids = set()
        wanted = {r['request_id'] for r in selected}
        for line in LOG.open(encoding='utf-8'):
            if not any(v in line for v in wanted):
                continue
            pos = line.find('{')
            if pos < 0:
                continue
            try:
                item = json.loads(line[pos:])
            except ValueError:
                continue
            if item.get('request_id') in wanted and item.get('api_key_id') == keyid:
                seen_log_ids.add(item['request_id'])
        require(seen_log_ids == wanted, 'HTTP_REQUEST_ID_KEY_LOG_CORRELATION_MISSING')
        funding = db.execute('''SELECT request_id,actor_user_id,payer_user_id,team_id,subscription_id,
          status,subscription_quota,wallet_quota,target_quota,created_at
          FROM realyu_funding_requests WHERE api_key_id=%s AND created_at>=%s AND created_at<%s ORDER BY created_at''',
          (keyid, lower, upper)).fetchall()
        usage = db.execute('''SELECT request_id,user_id,input_tokens,output_tokens,cache_read_tokens,
          cache_creation_tokens,actual_cost,created_at,openai_ws_mode
          FROM usage_logs WHERE api_key_id=%s AND created_at>=%s AND created_at<%s ORDER BY created_at''',
          (keyid, lower, upper)).fetchall()
        result.update(window_funding_rows=len(funding), window_usage_rows=len(usage),
                      window_reserved_rows=sum(r['status'] == 'reserved' for r in funding))
        require(len(funding) == 3 and len(usage) == 3, 'EXACT_THREE_WINDOW_ROWS_REQUIRED')
        require(len({r['request_id'] for r in funding}) == 3 and len({r['request_id'] for r in usage}) == 3,
                'DUPLICATE_WINDOW_REQUEST')
        require({r['request_id'] for r in funding} == {r['request_id'] for r in usage}, 'FUNDING_USAGE_REQUEST_SET_MISMATCH')
        cases = []
        for p in selected:
            signature = token_signature(p)
            hits = [u for u in usage if tuple(u[k] for k in ('input_tokens', 'output_tokens', 'cache_read_tokens', 'cache_creation_tokens')) == signature]
            require(len(hits) == 1, 'PROBE_USAGE_CORRELATION_NOT_UNIQUE')
            u = hits[0]
            f = next(f for f in funding if f['request_id'] == u['request_id'])
            require(f['status'] == 'settled', 'NOT_SETTLED')
            require(all(f[k] == scope[k] for k in ('actor_user_id','payer_user_id','team_id')) and u['user_id'] == scope['actor_user_id'], 'SETTLEMENT_SCOPE_MISMATCH')
            require((scope['team_id'] is not None and f['wallet_quota'] == 0) or (scope['team_id'] is None and scope['actor_user_id'] == scope['payer_user_id']), 'PAYER_SCOPE_VIOLATION')
            require(f['target_quota'] == f['subscription_quota'] + f['wallet_quota'], 'FUNDING_SPLIT_MISMATCH')
            require(Decimal(str(u['actual_cost'])) * 500000 == f['target_quota'], 'EXACT_COST_QUOTA_MISMATCH')
            require(u['openai_ws_mode'] == p['name'].startswith('websocket'), 'TRANSPORT_MODE_MISMATCH')
            counts = db.execute('''SELECT
              (SELECT count(*) FROM usage_logs WHERE api_key_id=%s AND request_id=%s) AS usage_count,
              (SELECT count(*) FROM usage_billing_dedup WHERE api_key_id=%s AND request_id=%s) +
              (SELECT count(*) FROM usage_billing_dedup_archive WHERE api_key_id=%s AND request_id=%s) AS dedup_count''',
              (keyid, f['request_id'], keyid, f['request_id'], keyid, f['request_id'])).fetchone()
            require(counts['usage_count'] == 1 and counts['dedup_count'] == 1, 'DUPLICATE_OR_MISSING_USAGE_OR_DEDUP')
            cases.append({'name': p['name'], 'status': 'PASS', 'funding_status': f['status'],
                          'usage_rows': counts['usage_count'], 'billing_dedup_rows': counts['dedup_count'],
                          'upstream_response_id_sha256': hashlib.sha256(p['response_id'].encode()).hexdigest(),
                          'funding_request_id_sha256': hashlib.sha256(f['request_id'].encode()).hexdigest(),
                          'input_tokens_uncached': signature[0], 'output_tokens': signature[1],
                          'cache_read_tokens': signature[2], 'cache_creation_tokens': signature[3],
                          'upstream_input_tokens': p['usage']['input_tokens'],
                          'actual_cost_usd': str(u['actual_cost']), 'funding_quota': f['target_quota'],
                          'scope_matches': True, 'exact_cost_quota_matches': True})
        result.update(status='PASS', cases=cases, total_usage_rows=3, total_settled_requests=3,
                      total_billing_dedup_rows=3, total_funding_quota=sum(c['funding_quota'] for c in cases),
                      total_actual_cost_usd=str(sum(Decimal(c['actual_cost_usd']) for c in cases)),
                      captured_at=db.execute('SELECT transaction_timestamp() AS at').fetchone()['at'].isoformat(),
                      transaction_read_only=True)
        db.rollback()


def main():
    out = ROOT / 'ux3-protocol-funding-audit' / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:10])
    out.mkdir(parents=True, exist_ok=False)
    result = {'status': 'FAIL', 'schema': 1, 'production_writes': 0, 'http_requests': 0, 'model_requests': 0,
              'limitations': ['Read-only post-request consistency check, not an independent pre/post wallet snapshot.',
                              'No direct response_id foreign key exists; correlation is documented and requires a unique match.',
                              'Reserved count applies only to the acceptance key and fixed acceptance window, not all customer traffic.']}
    try:
        run(result)
    except Exception as exc:
        result['error_type'] = type(exc).__name__
        result['error_code'] = str(exc) if isinstance(exc, ValueError) and str(exc).replace('_', '').isalnum() else 'READ_ONLY_AUDIT_FAILED'
        save(out / 'first-failure.json', result)
    save(out / 'result.json', result)
    print(json.dumps({'status': result['status'], 'result': str(out / 'result.json'),
                      'cases': len(result.get('cases', [])), 'error_code': result.get('error_code')}))
    return 0 if result['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())

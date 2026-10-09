"""Authenticated management acceptance; no inference, account import or billing writes.

Creates one empty RealYu test login to exercise role isolation, then disables it.
The report contains counts/checks only. Credentials and customer records are not saved.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import secrets
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--admin-config', type=Path, required=True)
    parser.add_argument('--expected-version', required=True)
    parser.add_argument('--expected-console', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    assert not args.report.exists(), 'Keep previous results'
    spec = importlib.util.spec_from_file_location('portal_acceptance_api', Path(__file__).parents[1]/'verify_provider_release.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.BASE = args.base_url.rstrip('/')
    cfg = json.loads(args.admin_config.read_text(encoding='utf-8-sig'))
    admin, member, anon = module.API(), module.API(), module.API()
    if module.BASE.startswith('http://127.0.0.1:'):
        for api in (admin, member, anon):
            original = api.request
            api.request = lambda path, data=None, method=None, headers=None, request=original: request(path, data, method, {'Origin':'https://api.realyu.fun', **(headers or {})})
    result = {'scope':'management HTTP E2E; no desktop or inference acceptance', 'base_url':module.BASE,
              'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'checks':{}, 'all_passed':False}
    uid = None

    def check(name, ok):
        result['checks'][name] = bool(ok)
        if not ok: raise AssertionError(name)

    try:
        check('expected_version', anon.call('/api/status')['version'] == args.expected_version)
        check('anonymous_overview_denied', anon.denied('/api/channel/sub2api/overview'))
        admin.login(cfg['admin_username'], cfg['admin_password'])
        status = admin.call('/api/channel/sub2api/status')
        check('https_console_entry', status['admin_url'] == args.expected_console and status['enabled'] and status['configured'])
        overview = admin.call('/api/channel/sub2api/overview')
        pools = overview['pools']
        check('real_account_pool', bool(pools) and all(p['available'] and not p['truncated'] and p['accounts'] for p in pools))
        allowed = {'id','name','platform','type','status','schedulable','concurrency','current_concurrency','five_hour_percent','weekly_percent','today'}
        check('account_metadata_allowlist', all(set(a) <= allowed for p in pools for a in p['accounts']))
        check('native_today_stats', all(a['today'] is not None and set(a['today']) == {'requests','tokens'} for p in pools for a in p['accounts']))
        result['pools'] = len(pools)
        result['accounts'] = sum(len(p['accounts']) for p in pools)
        logs = admin.call('/api/log/?type=2&p=0&page_size=20')['items']
        attributions = [json.loads(log.get('other') or '{}').get('admin_info',{}).get('sub2api') for log in logs]
        matched = [a for a in attributions if a and a['status'] == 'matched']
        check('real_request_attribution', len(matched) > 0)
        check('attribution_allowlist', all(set(a) <= {'status','usage_id','account_id','account_name','group_id','group_name','model','upstream_model'} for a in attributions if a))
        result['sampled_logs'] = len(logs)
        result['matched_logs'] = len(matched)
        username, password = 'portal'+secrets.token_hex(4), secrets.token_urlsafe(24)
        member.call('/api/user/register', {'username':username,'password':password})
        uid = member.login(username,password)['id']
        check('ordinary_overview_denied', member.denied('/api/channel/sub2api/overview'))
        check('ordinary_admin_logs_denied', member.denied('/api/log/?p=0&page_size=1'))
        own_logs = member.call('/api/log/self?p=0&page_size=10')['items']
        check('ordinary_logs_no_supply_metadata', all('admin_info' not in json.loads(row.get('other') or '{}') for row in own_logs))
        result['all_passed'] = True
    except Exception as exc:
        result['failure'] = str(exc) if isinstance(exc,AssertionError) else type(exc).__name__
        raise
    finally:
        member.logout()
        if uid is not None:
            try:
                admin.call('/api/user/manage', {'id':uid,'action':'disable'})
                result['test_user_disabled'] = True
            except Exception:
                result['test_user_disabled'] = False
                result['all_passed'] = False
        admin.logout()
        args.report.write_text(json.dumps(result, indent=2),encoding='utf-8')
        print(json.dumps(result))


if __name__ == '__main__':
    main()

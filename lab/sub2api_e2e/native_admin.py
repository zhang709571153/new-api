"""Verify native console HTTPS, authentication and inference ingress isolation.

Uses the existing native administrator login, then revokes the test refresh token.
Never prints or persists credentials, returned tokens or native account records.
"""
import argparse
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Native management redirect refused')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--runtime-private', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    parser.add_argument('--proxy')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    parsed = urllib.parse.urlsplit(base)
    assert parsed.scheme == 'https' and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment and not parsed.path
    assert not args.report.exists(), 'Keep previous evidence'
    cfg = json.loads(args.runtime_private.read_text(encoding='utf-8-sig'))
    http = urllib.request.build_opener(urllib.request.ProxyHandler({'https': args.proxy} if args.proxy else {}), NoRedirect())
    report = {'base_url': base, 'scope': 'native administration HTTP E2E; no browser or inference request',
              'proxy_used':bool(args.proxy), 'started_at':time.strftime('%Y-%m-%dT%H:%M:%S%z'), 'checks':{}, 'all_passed':False}
    token = refresh = ''

    def request(path, body=None, authenticated=False):
        headers={'Content-Type':'application/json','Origin':base}
        if authenticated: headers['Authorization']='Bearer '+token
        req=urllib.request.Request(base+path, data=None if body is None else json.dumps(body).encode(),headers=headers)
        try: response=http.open(req,timeout=20)
        except urllib.error.HTTPError as error: response=error
        with response: return response.status, response.read()

    def check(name, passed):
        report['checks'][name]=bool(passed)
        if not passed: raise AssertionError(name)

    try:
        status, raw=request('/login')
        check('login_html',status==200 and b'<script type="module"' in raw)
        assets=re.findall(rb'(?:src|href)="(/assets/[^" ]+)"',raw)
        check('login_assets',len(assets)>0 and all(request(path.decode())[0]==200 for path in assets))
        check('registration_disabled',b'"registration_enabled":false' in raw)
        check('anonymous_admin_denied',request('/api/v1/admin/accounts?page_size=1')[0] in (401,403))
        for path in ('/v1/models','/v1/responses','/v1beta/models','/backend-api/codex/responses','/responses','/metrics','/setup','/assets/../v1/models'):
            check('blocked_'+path,request(path)[0]==404)
        status, raw=request('/api/v1/auth/login',{'email':cfg['admin_email'],'password':cfg['admin_password']})
        data=json.loads(raw).get('data',{})
        token,refresh=data.get('access_token',''),data.get('refresh_token','')
        check('native_login',status==200 and bool(token) and bool(refresh))
        status, raw=request('/api/v1/admin/accounts?page_size=5',authenticated=True)
        check('native_admin_accounts',status==200 and bool(json.loads(raw).get('data',{}).get('items')))
        report['all_passed']=True
    except Exception as error:
        report['failure']=str(error) if isinstance(error,AssertionError) else type(error).__name__
        raise
    finally:
        if refresh:
            try:
                status,_=request('/api/v1/auth/logout',{'refresh_token':refresh})
                report['refresh_revoked']=status==200
            except Exception: report['refresh_revoked']=False
            report['all_passed']=report['all_passed'] and report['refresh_revoked']
        args.report.write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps(report))


if __name__=='__main__':
    main()

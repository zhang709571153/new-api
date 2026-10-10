"""Local candidate application smoke. No upstream requests or real payment calls.

Creates one synthetic username account. Requires an explicitly isolated target
configuration accepted by singlecore_migrate.validate_target; public URLs are
not accepted. Customer credentials are read only from the rehearsal database.
"""
import argparse
import hashlib
import json
from pathlib import Path
import secrets
import urllib.error
import urllib.parse
import urllib.request

from singlecore_migrate import validate_target


def run(base, private_target, report):
    p=json.loads(private_target.read_text(encoding='utf-8-sig'));validate_target(p)
    url=urllib.parse.urlsplit(base)
    if url.scheme!='http' or url.hostname!='127.0.0.1' or url.port!=29480 or url.path not in ('','/'):
        raise ValueError('only explicit loopback candidate port 29480 is supported')
    import psycopg
    results=[]
    def check(name, condition, **metadata):
        results.append({'check':name,'status':'PASS' if condition else 'FAIL',**metadata})
        report.write_text(json.dumps({'status':'RUNNING' if condition else 'FAIL','results':results,'paid_model_calls':0,'production_actions':False},ensure_ascii=True,indent=2),encoding='utf-8')
        if not condition:
            report.write_text(json.dumps({'results':results,'paid_model_calls':0,'production_actions':False},ensure_ascii=True,indent=2),encoding='utf-8')
            raise AssertionError(name)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(path,method='GET',body=None,bearer=None):
        headers={'Content-Type':'application/json'}
        if bearer: headers['Authorization']='Bearer '+bearer
        req=urllib.request.Request(base+path, data=None if body is None else json.dumps(body,ensure_ascii=False).encode(), method=method,headers=headers)
        try: response=opener.open(req,timeout=15)
        except urllib.error.HTTPError as e: response=e
        with response:
            raw=response.read(2*1024*1024)
            try: payload=json.loads(raw)
            except (json.JSONDecodeError,UnicodeDecodeError): payload=None
            return response.status,payload,response.headers,raw
    status,body,_,_=request('/health');check('candidate-health',status==200 and body.get('status')=='ok')
    status,settings,_,_=request('/api/v1/settings/public')
    check('username-registration-enabled',status==200 and settings['data']['username_registration_enabled'] is True)
    login='smoke_'+secrets.token_hex(6);password=secrets.token_urlsafe(18)+'甲';nick='测试昵称'+secrets.token_hex(2)
    status,body,_,_=request('/api/v1/auth/register','POST',{'login_name':login,'display_name':nick,'password':password,'role':'admin'})
    check('username-register-without-email',status==200,http_status=status)
    status,body,_,_=request('/api/v1/auth/login','POST',{'email':login,'password':password})
    check('username-password-login',status==200,http_status=status)
    token=body['data']['access_token']
    status,body,_,_=request('/api/v1/auth/me',bearer=token);user=body.get('data',{})
    check('independent-display-name-and-no-email-required',status==200 and user.get('login_name')==login and user.get('display_name')==nick and user.get('email')=='' and user.get('role')=='user')
    uid=user['id']
    nickname='昵称可独立修改'+secrets.token_hex(2)
    status,body,_,_=request('/api/v1/user','PUT',{'username':nickname},token)
    check('update-nickname-retains-login-name',status==200 and body['data'].get('display_name')==nickname and body['data'].get('login_name')==login,http_status=status)
    status,body,_,_=request('/api/v1/auth/login','POST',{'email':login,'password':password})
    check('original-login-after-nickname-change',status==200,http_status=status)
    status,_,_,_=request('/api/v1/auth/login','POST',{'email':nickname,'password':password})
    check('nickname-is-not-login-alias',status==401,http_status=status)
    status,_,_,_=request('/api/v1/admin/users',bearer=token)
    check('registration-cannot-self-elevate',status==403,http_status=status)
    status,_,_,_=request('/api/v1/auth/register','POST',{'login_name':login,'password':password})
    check('duplicate-username-rejected',status in (400,409),http_status=status)
    status,body,_,_=request('/api/v1/keys','POST',{'name':'synthetic-personal-key','group_id':p['group_mapping']['default'],'quota':1},token)
    check('native-key-creation',status in (200,201),http_status=status)
    new_key_id=body['data']['id'];new_key=body['data']['key']
    status,body,_,_=request('/api/client/key-check',bearer=new_key)
    check('new-key-check-with-zero-wallet',status==200 and body.get('code') is True,http_status=status)
    with psycopg.connect(host=p['host'],port=p['port'],dbname=p['database'],user=p['user'],password=p['password']) as c:
        scope=c.execute('SELECT actor_user_id,payer_user_id,team_id FROM realyu_key_scopes WHERE api_key_id=%s',(new_key_id,)).fetchone()
        check('new-key-automatically-enrolled-as-personal',scope==(uid,uid,None))
        row=c.execute('SELECT username,balance,signup_source,password_hash FROM users WHERE id=%s',(uid,)).fetchone()
        check('username-registration-persisted-without-bonus',row[0]==nickname and row[1]==0 and row[2]=='realyu_username' and row[3].startswith('$argon2id$'))
        candidates=c.execute("SELECT k.key,COALESCE(s.team_id,0) FROM api_keys k JOIN users u ON u.id=k.user_id JOIN realyu_key_scopes s ON s.api_key_id=k.id WHERE k.status='active' AND k.deleted_at IS NULL AND u.status='active' AND u.deleted_at IS NULL AND (k.expires_at IS NULL OR k.expires_at>NOW()) AND k.ip_whitelist='[]'::jsonb ORDER BY k.id").fetchall()
        selected=[]
        for team in [False,True]:
            eligible=[k for k in candidates if bool(k[1])==team]
            if eligible: selected.append((team,eligible[0][0]))
        check('imported-personal-and-team-fixtures-present',len(selected)==2)
        before=c.execute('SELECT count(*) FROM realyu_funding_requests').fetchone()[0]
        for team,key in selected:
            status,body,headers,raw=request('/api/client/key-check',bearer=key)
            check('team-key-check' if team else 'personal-key-check',status==200 and body.get('code') is True and body['data']['object']=='token_usage' and headers.get('Cache-Control')=='no-store' and key.encode() not in raw,http_status=status)
            check('integer-usage-contract-team' if team else 'integer-usage-contract-personal',all(type(body['data'][f]) is int for f in ['total_granted','total_used','total_available','expires_at']))
        check('key-inspection-does-not-reserve-or-charge',before==c.execute('SELECT count(*) FROM realyu_funding_requests').fetchone()[0])
    status,_,_,_=request('/api/client/key-check');check('anonymous-key-check-rejected',status==401,http_status=status)
    status,_,_,raw=request('/login');check('embedded-brand-favicon',status==200 and b'/brand/favicon.ico' in raw)
    status,_,_,raw=request('/brand/realyu-wordmark.png');check('brand-logo-readable',status==200 and raw.startswith(b'\x89PNG'))
    report.write_text(json.dumps({'status':'PASS','results':results,'paid_model_calls':0,'production_actions':False,'scope':'real loopback HTTP with native application and PostgreSQL; no upstream model or merchant acceptance'},ensure_ascii=True,indent=2),encoding='utf-8')
    return {'status':'PASS','checks':len(results),'paid_model_calls':0,'production_actions':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base',default='http://127.0.0.1:29480');parser.add_argument('--private-target',required=True,type=Path);parser.add_argument('--report',required=True,type=Path);args=parser.parse_args()
    created_report=False
    try:
        with args.report.open('x',encoding='utf-8') as output:
            json.dump({'status':'RUNNING','results':[],'production_actions':False},output)
        created_report=True
        print(json.dumps(run(args.base,args.private_target,args.report)))
    except Exception as e:
        # Avoid driver/request exception text, which may contain customer data.
        if created_report:
            failure=json.loads(args.report.read_text(encoding='utf-8'))
            failure.update(status='FAIL',error_type=type(e).__name__)
            args.report.write_text(json.dumps(failure,ensure_ascii=True,indent=2),encoding='utf-8')
        print(json.dumps({'status':'FAIL','error_type':type(e).__name__,'details':'see sanitized report; no credentials printed'}));raise SystemExit(1)

"""Use the existing CowAgent config without modifying it. No retry hides a failure."""
import argparse, hashlib, json, re, urllib.error, urllib.parse, urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT=None
CONFIG=None
parser=argparse.ArgumentParser()
parser.add_argument('--config',type=Path)
parser.add_argument('--private-output-dir',type=Path)
parser.add_argument('--readonly-current',action='store_true')
parser.add_argument('--after-update',action='store_true')
parser.add_argument('--expected-version')
parser.add_argument('--allow-tiny-paid',action='store_true')
args=parser.parse_args()
if not args.readonly_current and not args.after_update:
    print(json.dumps({'status':'PREPARED_NOT_RUN','config_read':False,'network_accessed':False,
        'readonly_command':'--readonly-current','after_update_command':'--after-update --expected-version <new> --allow-tiny-paid'}))
    raise SystemExit(0)
assert not (args.readonly_current and args.after_update)
assert not args.allow_tiny_paid or (args.after_update and args.expected_version)
if args.after_update: assert args.expected_version
assert args.config and args.private_output_dir, 'Explicit private config/output paths required'
CONFIG=args.config.resolve()
ROOT=args.private_output_dir.resolve()
ROOT.mkdir(parents=True,exist_ok=True)
cfg=json.loads(CONFIG.read_text(encoding='utf-8-sig'))
providers=[p for p in cfg['custom_providers'] if p.get('id')=='realyu']
assert len(providers)==1
provider=providers[0]
key,base=provider['api_key'],provider['api_base'].rstrip('/')
model=cfg['model']
parsed=urllib.parse.urlsplit(base)
assert (parsed.scheme,parsed.hostname,parsed.port,parsed.path) in [('http','127.0.0.1',18301,'/v1'),('https','api.realyu.fun',None,'/v1')]
assert isinstance(key,str) and key.startswith('sk-')
origin=urllib.parse.urlunsplit((parsed.scheme,parsed.netloc,'','',''))
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
out=ROOT/('cowagent-recovery-'+stamp)
out.mkdir()
report={'started_at':datetime.now(timezone.utc).isoformat(),'target':base,'model':model,
    'credential_fingerprint_sha256_12':hashlib.sha256(key.encode()).hexdigest()[:12],
    'config_changed':False,'key_changed':False,'mode':'readonly-before' if args.readonly_current else 'after-update',
    'paid_requests_sent':0,'retries':0,'checks':[]}
opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
def sanitized(value):
    return re.sub(r'sk-[A-Za-z0-9_-]+','[REDACTED]',str(value).replace(key,'[REDACTED]'))[:600]
def request(label,path,body=None,authenticated=True):
    headers={'Content-Type':'application/json','Cache-Control':'no-store'}
    if authenticated: headers['Authorization']='Bearer '+key
    encoded=None if body is None else json.dumps(body).encode()
    req=urllib.request.Request(origin+path,data=encoded,headers=headers,method='GET' if body is None else 'POST')
    try:
        response=opener.open(req,timeout=90 if body else 20)
    except urllib.error.HTTPError as error:
        response=error
    with response:
        raw=response.read()
        private=out/(label+'.private.json')
        private.write_bytes(raw)
        data=json.loads(raw)
        error=data.get('error',{}) if isinstance(data,dict) else {}
        record={'name':label,'status':response.status,'request_id':response.headers.get('x-request-id'),
            'cf_ray':response.headers.get('cf-ray'),'body_sha256':hashlib.sha256(raw).hexdigest(),
            'error_code':error.get('code') or (data.get('code') if isinstance(data.get('code'),str) else data.get('reason')),
            'error_type':error.get('type'),
            'message':sanitized(error.get('message') or data.get('message') or '')}
        report['checks'].append(record)
        (out/'result.private.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        return response.status,data,record
try:
    _,status,_=request('native-status','/api/status',authenticated=False)
    assert status.get('success') and status['data']['single_core'] and status['data']['engine']=='sub2api'
    report['version']=status['data']['version']
    if args.after_update: assert report['version']==args.expected_version, 'Unexpected deployed version; paid request blocked'
    key_status,key_data,key_record=request('key-check','/api/client/key-check')
    models_status,models_data,models_record=request('models','/v1/models')
    key_record['pass']=key_status==200 and key_data.get('code') is True
    models_record['pass']=models_status==200 and isinstance(models_data.get('data'),list)
    if models_record['pass']:
        ids=[item.get('id') for item in models_data['data']]
        models_record['model_count']=len(ids)
        models_record['configured_model_present']=model in ids
    if args.allow_tiny_paid:
        assert key_record['pass'] and models_record['pass'] and models_record.get('configured_model_present'), 'Readonly recovery checks failed; paid request blocked'
        report['paid_requests_sent']=1
        # One authorized tiny call. Never retries and never changes client config.
        code,data,row=request('tiny-responses','/v1/responses',{'model':model,'input':'Reply exactly COWAGENT-OK.',
            'reasoning':{'effort':'low'},'max_output_tokens':64,'stream':False})
        text=''.join(part.get('text','') for item in data.get('output',[]) for part in item.get('content',[]) if part.get('type')=='output_text')
        row['pass']=code==200 and data.get('status')=='completed' and text.strip()=='COWAGENT-OK'
        row['response_id']=data.get('id')
        row['usage']=data.get('usage')
        row['exact_response_matched']=text.strip()=='COWAGENT-OK'
except Exception as error:
    report['first_exception']={'type':type(error).__name__,'message':sanitized(error)}
finally:
    report['finished_at']=datetime.now(timezone.utc).isoformat()
    report['success']=all(row.get('pass',True) for row in report['checks']) and 'first_exception' not in report
    (out/'result.private.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    public=out/'result.summary.json'
    public.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'evidence':str(out),'sanitized_report':str(public),**report},ensure_ascii=True))
raise SystemExit(0 if report['success'] else 1)

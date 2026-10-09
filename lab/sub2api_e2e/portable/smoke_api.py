"""Portable RealYu API smoke runner. Python standard library only; no retries."""
import argparse
import datetime as dt
import getpass
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import urllib.parse

import api_probe as p
import api_matrix as matrix


def evaluate(row):
    name=row['case'];body=row.get('json_body',{})
    if name.endswith('unoffered-model') and body.get('error',{}).get('code')=='model_not_found':
        return 'NOT_OFFERED'
    if row.get('status')!=200 or row.get('transport_error') or row.get('errors'): return 'FAIL'
    # SDKs consume the terminal response object. Correct deltas cannot excuse a
    # completed response that silently discards every output item.
    if row.get('completed') and row.get('output_types') == []:
        return 'FAIL'
    if row.get('endpoint','').endswith(('/v1/responses','/v1/chat/completions','/v1/completions')) and not row.get('completed'):
        return 'FAIL'
    if name in ('models-list','discovery-models'):
        return 'PASS' if isinstance(body.get('data'),list) and body['data'] else 'FAIL'
    if name=='models-detail': return 'PASS' if body.get('id')==matrix.MODEL else 'FAIL'
    if name=='claude-messages':
        return 'PASS' if body.get('stop_reason')=='end_turn' and any(x.get('text')=='CLAUDE-COMPAT-OK' for x in body.get('content',[])) else 'FAIL'
    if name=='alpha-search':
        return 'PASS' if body.get('results') and 'docs.python.org' in body.get('output','') else 'FAIL'
    if name.endswith('chat-web'):
        return 'PASS' if row.get('completed') and row.get('text') and row.get('citations') else 'FAIL'
    if row.get('image_artifact'): return 'VISUAL_REVIEW_REQUIRED'
    return 'PASS' if row.get('answer_contract_passed') is True else 'FAIL'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--base-url',default='https://api.realyu.fun/v1')
    ap.add_argument('--model',default='gpt-6.1-sol')
    ap.add_argument('--suite',choices=['quick','api','images','all'],default='api')
    ap.add_argument('--network',choices=['system','direct'],default='system')
    ap.add_argument('--user-agent',default='RealYu-Portable-Smoke/20261009')
    ap.add_argument('--all-models',action='store_true',help='One additional small text call per other offered GPT text model')
    ap.add_argument('--output',type=Path)
    ap.add_argument('--list',action='store_true',help='Describe suites without authentication or network requests')
    a=ap.parse_args()
    if a.list:
        print(json.dumps({'quick':['authenticated model list','streamed text exact marker'],
            'api':['PDF base64/URL/upload/Chat','web_search/web_search_preview/standalone search','Responses nonstream','Chat text/stream','JSON schema in both formats','function call and return in both formats','image recognition in both formats','full history/previous_response_id/compact','Claude Messages format','unoffered embedding/speech/rerank models','legacy completions'],
            'images':['image generation','multipart edit','JSON edit with same reference','native Responses image tool','image stream option','image URL output option'],
            'all':'api plus images','dependencies':'Python 3.10+ standard library; included synthetic fixtures',
            'limits':'No retries. Images suite sends up to six image requests including two option probes; all six could be billed if supported. Visual comparison and native Codex scenarios require the receiving agent.'},ensure_ascii=False,indent=2))
        return 0
    url=urllib.parse.urlsplit(a.base_url.rstrip('/'))
    if url.scheme not in ('http','https') or not url.hostname or url.username or url.password or url.query or url.fragment or not url.path.endswith('/v1'):
        ap.error('--base-url must be an API base ending in /v1, without credentials, query, or fragment')
    if url.scheme=='http' and url.hostname not in ('127.0.0.1','localhost','::1'):
        ap.error('Remote API tests require HTTPS')
    if not os.environ.get('REALYU_API_KEY','').strip():
        os.environ['REALYU_API_KEY']=getpass.getpass('RealYu API key (hidden; never saved): ').strip()
    if not os.environ['REALYU_API_KEY']: ap.error('An API key is required')
    stamp=dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    out=(a.output or p.ROOT/'results'/stamp).resolve()
    if out.exists() and any(out.iterdir()): ap.error('Use a new output directory; previous failures must be preserved')
    out.mkdir(parents=True,exist_ok=True)
    p.OUT=out;p.NETWORK=a.network;p.USER_AGENT=a.user_agent;matrix.MODEL=a.model
    base=a.base_url.rstrip('/')[:-3]
    original_call=p.call
    def request(*args,**kwargs):
        kwargs['base']=base
        return original_call(*args,**kwargs)
    p.call=request
    metadata={'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),'base_url':a.base_url,'model':a.model,
        'suite':a.suite,'os':platform.platform(),'python':platform.python_version(),'network':a.network,
        'user_agent':a.user_agent,'automatic_retries':0,'api_key_saved':False,
        'native_codex_equivalence_verified':False,'desktop_e2e_performed':False,
        'raw_evidence':'Synthetic requests and provider responses only; do not upload unreviewed raw files.'}
    codex=shutil.which('codex')
    if codex:
        try:metadata['installed_codex_version']=subprocess.run([codex,'--version'],capture_output=True,text=True,timeout=10).stdout.strip()
        except Exception:metadata['installed_codex_version']='UNVERIFIED'
    else: metadata['installed_codex_version']='NOT_INSTALLED'
    exit_code=0
    try:
        catalog=p.call('discovery-models',a.model,'/v1/models',None)
        names=[x.get('id') for x in catalog.get('json_body',{}).get('data',[])]
        metadata['offered_models']=names
        if catalog.get('status')!=200 or a.model not in names:
            raise RuntimeError('Model discovery/authentication failed or requested model is not offered; remaining tests blocked.')
        if a.suite=='quick':
            cases=['baseline']
        elif a.suite in ('api','all'):
            cases=['baseline','pdf','web','preview','upload','extracted','pdf-nonstream','web-nonstream','pdf-url','chat-pdf','chat-web']
        else:cases=[]
        if cases:
            sys.argv=['api_probe.py','--model',a.model,'--cases',*cases]
            p.main()
        if a.suite in ('api','all'):matrix.text_tests()
        if a.suite in ('images','all'):matrix.image_tests()
        if a.all_models:
            for model in names:
                if model and model!=a.model and model.startswith('gpt-') and not model.startswith('gpt-image'):
                    r=p.call('catalog-'+model,model,'/v1/responses',p.payload(model,'Reply exactly CATALOG-OK'))
                    r['answer_contract_passed']=r.get('completed') and r.get('text','').strip()=='CATALOG-OK'
                    matrix.save(r)
    except Exception as e:
        metadata['runner_error']=type(e).__name__+': '+str(e).replace(os.environ['REALYU_API_KEY'],'[redacted]')[:500]
        exit_code=2
    rows=[]
    for path in sorted(out.glob('*.json')):
        value=json.loads(path.read_text(encoding='utf-8'))
        if not value.get('case'):continue
        rows.append({'case':value['case'],'status':evaluate(value),'http_status':value.get('status'),
            'seconds':value.get('seconds'),'request_id':next((v for k,v in value.get('headers',{}).items() if k.lower() in ('x-oneapi-request-id','x-request-id')),None),
            'cf_ray':next((v for k,v in value.get('headers',{}).items() if k.lower()=='cf-ray'),None),'model':value.get('model'),
            'error':value.get('json_body',{}).get('error') or value.get('transport_error'),
            'evidence_file':path.name,'image':value.get('image_artifact'),
            'terminal_output_empty':value.get('completed') and value.get('output_types')==[]})
    metadata.update(finished_at=dt.datetime.now(dt.timezone.utc).isoformat(),checks=rows,
        counts={status:sum(x['status']==status for x in rows) for status in ['PASS','FAIL','NOT_OFFERED','VISUAL_REVIEW_REQUIRED']})
    if metadata['counts']['FAIL'] and exit_code==0:exit_code=1
    metadata['exit_code']=exit_code
    (out/'summary.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'summary':str(out/'summary.json'),'counts':metadata['counts'],'exit_code':exit_code,'native_codex_equivalence_verified':False},ensure_ascii=False))
    return exit_code


if __name__=='__main__':raise SystemExit(main())

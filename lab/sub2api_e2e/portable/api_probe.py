"""Bounded public PDF/search probes. Synthetic data; no retries or account changes."""
import argparse
import base64
import collections
import datetime as dt
import hashlib
import json
from pathlib import Path
import secrets
import os
import sys
import time
import urllib.error
import urllib.request


sys.stdout.reconfigure(encoding='utf-8')
ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results'
FIXTURE = ROOT / 'fixtures'
NETWORK = 'system'
USER_AGENT = 'RealYu-Portable-Smoke/20261009'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise RuntimeError('Redirect refused')


def key():
    value=os.environ.get('REALYU_API_KEY','').strip()
    if not value: raise RuntimeError('Set REALYU_API_KEY in this process or use the hidden prompt.')
    return value


def fixture():
    return (FIXTURE/'reading-fixture.pdf',json.loads((FIXTURE/'expected.json').read_text()),
            (FIXTURE/'extracted.txt').read_text(encoding='utf-8'))


def safe_json(value):
    if isinstance(value,dict):
        return {k:({'encoded_characters':len(v)} if (k in ('b64_json','encrypted_content','encrypted_output') or (k=='result' and value.get('type')=='image_generation_call')) and isinstance(v,str) else safe_json(v)) for k,v in value.items()}
    if isinstance(value,list): return [safe_json(v) for v in value]
    return value


def call(name, model, path, data, content_type='application/json', base='https://api.realyu.fun', timeout=100):
    raw=None if data is None else data if isinstance(data,bytes) else json.dumps(data).encode()
    report={'case':name,'model':model,'endpoint':base+path,'started_at':dt.datetime.now(dt.timezone.utc).isoformat(),
            'request_bytes':len(raw or b''),'attempts':1,'completed':False}
    http=urllib.request.build_opener(urllib.request.ProxyHandler({}) if NETWORK=='direct' else urllib.request.ProxyHandler(),NoRedirect())
    req=urllib.request.Request(base+path,data=raw,headers={'Authorization':'Bearer '+key(),
        'Content-Type':content_type,'User-Agent':USER_AGENT,
        'Accept':'text/event-stream, application/json'})
    start=time.monotonic()
    print('START '+name+' '+model,flush=True)
    body=b''
    try:
        try:
            response=http.open(req,timeout=timeout)
        except urllib.error.HTTPError as error:
            response=error
        with response:
            report['status']=response.status
            report['headers']={k:v for k,v in response.headers.items() if k.lower() in
                ('x-request-id','x-oneapi-request-id','cf-ray','content-type','server')}
            body=response.read()
    except Exception as error:
        report['transport_error']=type(error).__name__+': '+str(error)[:500]
    report['seconds']=round(time.monotonic()-start,3)
    report['response_bytes']=len(body)
    events=[]
    for line in body.splitlines():
        if line.startswith(b'data:'):
            try: events.append(json.loads(line[5:].strip()))
            except ValueError: pass
    report['event_counts']=dict(collections.Counter(e.get('type','unknown') for e in events))
    report['text']=''.join(e.get('delta','') for e in events if e.get('type')=='response.output_text.delta')
    report['tool_calls']=[]
    report['output_items']=[]
    report['citations']=[]
    report['errors']=[]
    chat_stream=False
    for e in events:
        if e.get('type')=='response.output_item.done':
            report['output_items'].append(e.get('item',{}))
        if e.get('object')=='chat.completion.chunk':
            chat_stream=True
            for choice in e.get('choices',[]):
                report['text']+=choice.get('delta',{}).get('content','') or ''
                if choice.get('finish_reason') in ('stop','tool_calls'): report['completed']=True
        if e.get('type')=='response.output_item.done' and e.get('item',{}).get('type')=='web_search_call':
            report['tool_calls'].append(e['item'])
        if e.get('type')=='response.output_text.annotation.added':
            report['citations'].append(e.get('annotation'))
        if e.get('type') in ('response.failed','error'):
            report['errors'].append(e.get('error') or e.get('response',{}).get('error') or e)
        if e.get('type')=='response.completed':
            result=e.get('response',{})
            report['completed']=result.get('status')=='completed'
            report['response_id']=result.get('id')
            report['output_types']=[x.get('type') for x in result.get('output',[])]
            for item in result.get('output',[]):
                if item.get('type')=='message':
                    for part in item.get('content',[]):
                        for a in part.get('annotations',[]):
                            if a not in report['citations']: report['citations'].append(a)
    if chat_stream:
        report['sse_done_received']=any(line.startswith(b'data:') and line[5:].strip()==b'[DONE]' for line in body.splitlines())
        report['completed']=report['completed'] and report['sse_done_received']
    if not events and body:
        try:
            result=json.loads(body)
            report['json_body']=safe_json(result)
            if result.get('object')=='response':
                report['completed']=result.get('status')=='completed'
                report['response_id']=result.get('id')
                report['output_types']=[x.get('type') for x in result.get('output',[])]
                for item in result.get('output',[]):
                    if item.get('type')=='web_search_call': report['tool_calls'].append(item)
                    for part in item.get('content',[]):
                        report['text']+=part.get('text','')
                        report['citations'].extend(part.get('annotations',[]))
            elif result.get('object')=='chat.completion':
                choices=result.get('choices',[])
                report['completed']=bool(choices) and choices[0].get('finish_reason') in ('stop','tool_calls')
                report['text']=''.join(x.get('message',{}).get('content','') or '' for x in choices)
                report['citations']=[a for x in choices for a in x.get('message',{}).get('annotations',[])]
            elif result.get('object')=='text_completion':
                choices=result.get('choices',[])
                report['completed']=bool(choices) and all(x.get('finish_reason')=='stop' for x in choices)
                report['text']=''.join(x.get('text','') for x in choices)
        except ValueError: report['body_excerpt']=body.decode(errors='replace')[:2000]
    (OUT/(name+'.response.txt')).write_bytes(body)
    report=safe_json(report)
    (OUT/(name+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('json_body','output_items','citations')},ensure_ascii=False),flush=True)
    return report


def payload(model, text, parts=None):
    return {'model':model,'stream':True,'store':False,'instructions':'Follow the test request precisely. Keep the final answer short.',
            'input':[{'role':'user','content':parts or [{'type':'input_text','text':text}]}]}


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--cases',nargs='+',default=['baseline','pdf','web','preview','upload','extracted'])
    ap.add_argument('--model',default='gpt-6-astra')
    a=ap.parse_args()
    p,expected,extracted=fixture()
    results=[]
    pdf_question='Read both pages of the attached PDF. Return only the verification code, the combined number of blue and green boxes, and the warehouse name.'
    for case in a.cases:
        name=a.model.replace('.','_')+'-'+case
        body=payload(a.model,'Reply exactly REALYU-E2E-TEXT-OK')
        path='/v1/responses'
        ct='application/json'
        if case in ('pdf','pdf-nonstream'):
            body=payload(a.model,pdf_question,[{'type':'input_file','filename':p.name,'file_data':'data:application/pdf;base64,'+base64.b64encode(p.read_bytes()).decode()},
                {'type':'input_text','text':pdf_question}])
            if case.endswith('nonstream'): body['stream']=False
        elif case in ('web','preview','web-nonstream'):
            body=payload(a.model,'Use the web search tool to search the official Python documentation for pathlib Path.read_text. Give one short factual sentence and a source citation. You must actually search; do not answer from memory.')
            tool={'type':'web_search_preview' if case=='preview' else 'web_search'}
            if case!='preview': tool['external_web_access']=True
            body['tools']=[tool]
            body['tool_choice']='required'
            if case.endswith('nonstream'): body['stream']=False
        elif case=='pdf-url':
            body=payload(a.model,'Transcribe all text from the attached PDF.',[
                {'type':'input_file','file_url':'https://www.w3.org/WAI/ER/tests/xhtml/testfiles/resources/pdf/dummy.pdf'},
                {'type':'input_text','text':'Transcribe all text from the attached PDF. Do not use web search.'}])
        elif case=='chat-pdf':
            path='/v1/chat/completions'
            body={'model':a.model,'stream':False,'messages':[{'role':'user','content':[
                {'type':'file','file':{'filename':p.name,'file_data':'data:application/pdf;base64,'+base64.b64encode(p.read_bytes()).decode()}},
                {'type':'text','text':pdf_question}]}]}
        elif case=='chat-web':
            path='/v1/chat/completions'
            body={'model':a.model,'stream':False,'web_search_options':{},'messages':[{'role':'user','content':'Search official Python documentation for pathlib Path.read_text and give one short fact with a source citation.'}]}
        elif case=='extracted':
            body=payload(a.model,pdf_question+'\n\nExtracted PDF text:\n'+extracted)
        elif case=='upload':
            boundary='RealyuPDFE2E'+secrets.token_hex(6)
            body=(f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nuser_data\r\n--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="reading-fixture.pdf"\r\nContent-Type: application/pdf\r\n\r\n'.encode()+p.read_bytes()+f'\r\n--{boundary}--\r\n'.encode())
            ct='multipart/form-data; boundary='+boundary
            path='/v1/files'
        r=call(name,a.model,path,body,ct)
        if case in ('pdf','extracted','pdf-nonstream','chat-pdf'):
            r['answer_contract_passed']=r['completed'] and all(str(v) in r['text'] for v in expected.values())
        elif case in ('web','preview','web-nonstream'):
            r['answer_contract_passed']=r['completed'] and any(t.get('status')=='completed' for t in r['tool_calls']) and bool(r['citations'])
        elif case=='pdf-url':
            r['answer_contract_passed']=r['completed'] and 'Dummy PDF file' in r['text']
        elif case=='baseline':
            r['answer_contract_passed']=r['completed'] and r['text'].strip()=='REALYU-E2E-TEXT-OK'
        elif case=='upload':
            uploaded=r.get('json_body',{})
            file_id=uploaded.get('id')
            r['answer_contract_passed']=r.get('status')==200 and isinstance(file_id,str) and bool(file_id)
            if r['answer_contract_passed']:
                follow=payload(a.model,pdf_question,[{'type':'input_file','file_id':file_id},{'type':'input_text','text':pdf_question}])
                read=call(name+'-read',a.model,'/v1/responses',follow)
                read['answer_contract_passed']=read['completed'] and all(str(v) in read['text'] for v in expected.values())
                (OUT/(read['case']+'.json')).write_text(json.dumps(read,ensure_ascii=False,indent=2),encoding='utf-8')
                results.append(read)
        (OUT/(name+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')
        results.append(r)
    print('SUMMARY '+json.dumps([{'case':x['case'],'status':x.get('status'),'completed':x['completed'],'contract':x.get('answer_contract_passed')} for x in results]),flush=True)


if __name__=='__main__': main()

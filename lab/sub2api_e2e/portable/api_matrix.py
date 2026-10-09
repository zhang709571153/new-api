"""Current public API capability matrix; bounded, synthetic, no retries."""
import argparse
import base64
import hashlib
import io
import json
from pathlib import Path
import secrets

import struct
import zlib
import api_probe as p

MODEL='gpt-6.1-sol'
RESULTS=[]


def run(name,path,data,expected=None,**kwargs):
    request_model=data.get('model',MODEL) if isinstance(data,dict) else 'gpt-image-2' if name.startswith('images-') else MODEL
    r=p.call(name,request_model,path,data,**kwargs)
    if expected is not None:
        r['answer_contract_passed']=r['completed'] and expected in r['text']
    RESULTS.append(r)
    save(r)
    return r


def save(r):
    (p.OUT/(r['case']+'.json')).write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding='utf-8')


def raw_json(r):
    return json.loads((p.OUT/(r['case']+'.response.txt')).read_bytes())


def raw_items(r):
    items=[]
    for line in (p.OUT/(r['case']+'.response.txt')).read_bytes().splitlines():
        if not line.startswith(b'data:'): continue
        try: e=json.loads(line[5:])
        except ValueError: continue
        if e.get('type')=='response.output_item.done': items.append(e['item'])
    return items


def chat(text,stream=False):
    return {'model':MODEL,'stream':stream,'messages':[{'role':'user','content':text}]}


def text_tests():
    r=run('models-list','/v1/models',None)
    r['models']=[x['id'] for x in r.get('json_body',{}).get('data',[])];save(r)
    run('models-detail','/v1/models/'+MODEL,None)
    run('chat-text','/v1/chat/completions',chat('Reply exactly CHAT-TEXT-OK'),'CHAT-TEXT-OK')
    run('chat-stream','/v1/chat/completions',chat('Reply exactly CHAT-STREAM-OK',True),'CHAT-STREAM-OK')
    schema={'type':'object','properties':{'status':{'type':'string','enum':['ok']},'value':{'type':'integer'}},'required':['status','value'],'additionalProperties':False}
    b=p.payload(MODEL,'Return status ok and value 37 in the required JSON schema.')
    b['text']={'format':{'type':'json_schema','name':'e2e_contract','strict':True,'schema':schema}}
    r=run('responses-json-schema','/v1/responses',b)
    try:r['answer_contract_passed']=json.loads(r['text'])=={'status':'ok','value':37}
    except ValueError:r['answer_contract_passed']=False
    save(r)
    b=chat('Return status ok and value 37 in the required JSON schema.')
    b['response_format']={'type':'json_schema','json_schema':{'name':'e2e_contract','strict':True,'schema':schema}}
    r=run('chat-json-schema','/v1/chat/completions',b)
    try:r['answer_contract_passed']=json.loads(r['text'])=={'status':'ok','value':37}
    except ValueError:r['answer_contract_passed']=False
    save(r)

    tool={'type':'function','name':'read_test_record','description':'Get the synthetic test record.','parameters':{'type':'object','properties':{'record_id':{'type':'string'}},'required':['record_id'],'additionalProperties':False},'strict':True}
    b=p.payload(MODEL,'Call read_test_record with record_id DEMO-17, then report the returned verification code exactly.')
    b.update(tools=[tool],tool_choice={'type':'function','name':'read_test_record'})
    r=run('responses-function-call','/v1/responses',b)
    items=raw_items(r)
    calls=[x for x in items if x.get('type')=='function_call']
    r['answer_contract_passed']=r['completed'] and len(calls)==1 and calls[0].get('name')==tool['name'] and json.loads(calls[0]['arguments'])=={'record_id':'DEMO-17'}
    save(r)
    if calls:
        marker='TOOL-'+secrets.token_hex(6).upper()
        follow=p.payload(MODEL,'')
        follow['input']=b['input']+items+[{'type':'function_call_output','call_id':calls[0]['call_id'],'output':json.dumps({'verification_code':marker})}]
        follow.update(tools=[tool],tool_choice='none')
        run('responses-function-return','/v1/responses',follow,marker)

    b=chat('Call read_test_record with record_id DEMO-17, then report the returned verification code exactly.')
    b.update(tools=[{'type':'function','function':{k:v for k,v in tool.items() if k!='type'}}],tool_choice={'type':'function','function':{'name':tool['name']}})
    r=run('chat-function-call','/v1/chat/completions',b)
    result=r.get('json_body',{})
    msg=result.get('choices',[{}])[0].get('message',{})
    calls=msg.get('tool_calls',[])
    r['answer_contract_passed']=r.get('status')==200 and len(calls)==1 and json.loads(calls[0]['function']['arguments'])=={'record_id':'DEMO-17'};save(r)
    if calls:
        marker='CHAT-TOOL-'+secrets.token_hex(6).upper()
        follow=chat('')
        follow['messages']=b['messages']+[msg,{'role':'tool','tool_call_id':calls[0]['id'],'content':json.dumps({'verification_code':marker})}]
        follow.update(tools=b['tools'],tool_choice='none')
        run('chat-function-return','/v1/chat/completions',follow,marker)

    img=p.FIXTURE/'page2.png'
    image_url='data:image/png;base64,'+base64.b64encode(img.read_bytes()).decode()
    question='Read this image. Return the count of green boxes and the warehouse name only.'
    b=p.payload(MODEL,question,[{'type':'input_image','image_url':image_url},{'type':'input_text','text':question}])
    r=run('responses-image-read','/v1/responses',b,'NORTH-QUAY');r['answer_contract_passed']&='26' in r['text'];save(r)
    b=chat('');b['messages'][0]['content']=[{'type':'image_url','image_url':{'url':image_url}},{'type':'text','text':question}]
    r=run('chat-image-read','/v1/chat/completions',b,'NORTH-QUAY');r['answer_contract_passed']&='26' in r['text'];save(r)

    marker='HISTORY-'+secrets.token_hex(6).upper()
    first=p.payload(MODEL,'Remember this verification code for our conversation: '+marker+'. Reply only ACK.')
    first['prompt_cache_key']='realyu-e2e-history-20261009'
    r=run('responses-history-start','/v1/responses',first,'ACK')
    items=raw_items(r)
    second=p.payload(MODEL,'Repeat the verification code from the earlier user message, exactly.')
    second['prompt_cache_key']=first['prompt_cache_key']
    second['input']=first['input']+items+second['input']
    run('responses-full-history','/v1/responses',second,marker)
    if r.get('response_id'):
        previous=p.payload(MODEL,'Repeat the verification code from the earlier user message, exactly.')
        previous.update(previous_response_id=r['response_id'],prompt_cache_key=first['prompt_cache_key'])
        run('responses-previous-id','/v1/responses',previous,marker)
    compact={'model':MODEL,'input':second['input'],'instructions':'Preserve the verification code exactly for the next user question.'}
    r=run('responses-compact','/v1/responses/compact',compact)
    value=r.get('json_body',{})
    r['answer_contract_passed']=r.get('status')==200 and isinstance(value.get('output'),list) and bool(value['output'])
    save(r)
    if r.get('status')==200 and value.get('output'):
        follow=p.payload(MODEL,'Repeat the verification code from the earlier user message, exactly.')
        follow['input']=raw_json(r)['output']+follow['input']
        follow['prompt_cache_key']=first['prompt_cache_key']
        run('responses-compact-resume','/v1/responses',follow,marker)

    run('alpha-search','/v1/alpha/search',{'id':'e2e-'+secrets.token_hex(6),'model':MODEL,'input':[],
        'commands':{'search_query':[{'q':'site:docs.python.org pathlib Path.read_text'}]}})
    run('claude-messages','/v1/messages',{'model':MODEL,'max_tokens':256,'messages':[{'role':'user','content':'Reply exactly CLAUDE-COMPAT-OK'}]})
    run('embeddings-unoffered-model','/v1/embeddings',{'model':'text-embedding-3-small','input':'Synthetic test.'})
    run('speech-unoffered-model','/v1/audio/speech',{'model':'tts-1','input':'Synthetic test.','voice':'alloy'})
    run('rerank-unoffered-model','/v1/rerank',{'model':'rerank-v3.5','query':'test','documents':['synthetic test']})
    run('legacy-completions','/v1/completions',{'model':MODEL,'prompt':'Reply exactly LEGACY-OK','stream':False},'LEGACY-OK')


def multipart(fields,path=None):
    boundary='RealyuMatrix'+secrets.token_hex(8)
    parts=[]
    for k,v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    if path:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="reference.png"\r\nContent-Type: image/png\r\n\r\n'.encode()+path.read_bytes()+b'\r\n')
    parts.append(f'--{boundary}--\r\n'.encode())
    return b''.join(parts),'multipart/form-data; boundary='+boundary


def image_artifact(r,encoded,label):
    raw=base64.b64decode(encoded)
    target=p.OUT/(label+'.png');target.write_bytes(raw)
    if raw[:8]!=b'\x89PNG\r\n\x1a\n': raise ValueError('Expected an actual PNG payload')
    width,height=struct.unpack('>II',raw[16:24])
    position=8;compressed=[];ended=False
    while position+12<=len(raw):
        length=struct.unpack('>I',raw[position:position+4])[0]
        kind=raw[position+4:position+8]
        data=raw[position+8:position+8+length]
        crc=raw[position+8+length:position+12+length]
        if len(data)!=length or len(crc)!=4 or zlib.crc32(kind+data)&0xffffffff!=struct.unpack('>I',crc)[0]:
            raise ValueError('Incomplete or corrupt PNG chunk')
        if kind==b'IDAT':compressed.append(data)
        position+=length+12
        if kind==b'IEND':ended=True;break
    if not ended or not compressed or not zlib.decompress(b''.join(compressed)):
        raise ValueError('PNG pixel stream is incomplete')
    r['image_artifact']={'path':str(target),'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),
                         'dimensions':[width,height],'requested_dimensions':[1024,1024],'requested_size_honored':width==height==1024,
                         'format':'PNG','png_crc_checked':True,'pixel_stream_decompressed':True,'visual_review':'REQUIRED'}
    r['answer_contract_passed']=r.get('status')==200 and min(r['image_artifact']['dimensions'])>256
    save(r)
    return target


def image_tests():
    prompt='Create a clean editorial photograph of a sunlit office desk with an open cream notebook, a dark green desk lamp and a small succulent in a terracotta pot. Natural materials, realistic lighting, no people, no lettering.'
    body={'model':'gpt-image-2','prompt':prompt,'n':1,'size':'1024x1024','quality':'low','response_format':'b64_json'}
    r=run('images-generation','/v1/images/generations',body,timeout=300)
    if r.get('status')==200:
        result=raw_json(r)
        data=result.get('data',[])
        if data and data[0].get('b64_json'):
            original=image_artifact(r,data[0]['b64_json'],'generated-office')
            fields={'model':'gpt-image-2','prompt':'Preserve the desk, open cream notebook, green lamp, succulent and composition. Add a bright red ceramic coffee mug on the desk beside the notebook. Change nothing else.','size':'1024x1024','quality':'low','response_format':'b64_json'}
            raw,ct=multipart(fields,original)
            edited=run('images-edit-multipart','/v1/images/edits',raw,content_type=ct,timeout=300)
            if edited.get('status')==200:
                result=raw_json(edited)
                if result.get('data') and result['data'][0].get('b64_json'):
                    image_artifact(edited,result['data'][0]['b64_json'],'edited-office')
            body_json={**fields,'images':[{'image_url':'data:image/png;base64,'+base64.b64encode(original.read_bytes()).decode()}]}
            edited_json=run('images-edit-json','/v1/images/edits',body_json,timeout=300)
            if edited_json.get('status')==200:
                data=raw_json(edited_json).get('data',[])
                if data and data[0].get('b64_json'):
                    image_artifact(edited_json,data[0]['b64_json'],'json-edited-office')
                    edited_json['answer_contract_passed']=None
                    edited_json['visual_review']='Compare scene with generated-office.png; image delivery alone is not an edit PASS.'
                    save(edited_json)
    native=p.payload(MODEL,'Generate an editorial photograph of a small white Bichon dog sitting on a blue chair in a sunny reading room, bookshelves in the background, no lettering.')
    native['tools']=[{'type':'image_generation','model':'gpt-image-2','quality':'low','size':'1024x1024','output_format':'png'}]
    native['tool_choice']={'type':'image_generation'}
    r=run('responses-native-image','/v1/responses',native,timeout=300)
    images=[x for x in raw_items(r) if x.get('type')=='image_generation_call' and x.get('result')]
    if images: image_artifact(r,images[0]['result'],'native-bichon')
    # Each option is checked once. A future deployment may support and bill these requests.
    run('images-stream-option','/v1/images/generations',{**body,'stream':True})
    run('images-url-option','/v1/images/generations',{**body,'response_format':'url'})


if __name__=='__main__':
    a=argparse.ArgumentParser();a.add_argument('phase',choices=['text','images']);args=a.parse_args()
    (text_tests if args.phase=='text' else image_tests)()
    print('EXTENDED SUMMARY '+json.dumps([{'case':r['case'],'status':r.get('status'),'completed':r['completed'],'contract':r.get('answer_contract_passed')} for r in RESULTS]),flush=True)

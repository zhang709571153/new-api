"""Authorized UX4 post-release READ ONLY acceptance, offline without --released.

No model, payment, settings, database, account or Key mutation is implemented.
Only native login POST is allowed; tokens and customer rows stay in memory.
Each operation has a new directory and retains its first failure without retry.
"""
from __future__ import annotations
import argparse
from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
MANIFEST = Path(r'C:\ProgramData\RealYuServices\manifest.json')
ADMIN_CREDENTIALS = Path(r'C:\srv\realyu-newapi-releases\20260924-provider-2bc6a0e3\.lab\credentials.json')
EXPECTED = 'realyu-singlecore-v0.2.15-20261011-ux4'
BASES = {'direct':'https://api.realyu.fun','proxy':'https://api.realyu.fun','local':'http://127.0.0.1:18300'}
PROXY = 'http://127.0.0.1:17897'
HISTORY = '/api/v1/realyu/usage'
COUNTERS = ('requests','input_tokens','output_tokens','total_tokens','cache_read_tokens',
            'cache_creation_tokens','cache_read_known_requests','cache_creation_known_requests')
HISTORY_COUNTERS = COUNTERS + ('legacy_requests','native_requests','unknown_scope_requests')


class CheckFailure(Exception):
    pass


def require(value, code):
    if not value:
        raise CheckFailure(code)


def safe_error(error):
    return str(error) if isinstance(error, CheckFailure) and re.fullmatch(r'[A-Z0-9_]{1,100}',str(error)) else type(error).__name__


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write_new(path, value):
    with Path(path).open('x',encoding='utf-8') as file:
        json.dump(value,file,ensure_ascii=False,indent=2)
        file.write('\n')


def sha(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file,'sha256').hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()


def instant(value):
    result = datetime.fromisoformat(value.replace('Z','+00:00'))
    require(result.tzinfo is not None,'EXPLICIT_TIMEZONE_REQUIRED')
    return result.astimezone(timezone.utc)


def dec(value):
    require(isinstance(value,str),'COST_MUST_BE_DECIMAL_STRING')
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise CheckFailure('INVALID_DECIMAL_COST') from None
    require(result.is_finite() and result>=0,'INVALID_DECIMAL_COST')
    return result


def normalized_stats(value, history=True):
    result = {}
    for field in HISTORY_COUNTERS if history else COUNTERS:
        require(type(value.get(field)) is int and value[field]>=0,'STATS_SCHEMA_'+field.upper())
        result[field] = value[field]
    result['cost_usd'] = dec(value.get('cost_usd'))
    require(result['cache_read_known_requests']<=result['requests'] and
            result['cache_creation_known_requests']<=result['requests'],'CACHE_KNOWN_REQUEST_COUNT')
    if history:
        require(result['requests']==result['legacy_requests']+result['native_requests'],'SOURCE_COUNTS_DO_NOT_SUM')
    return result


def sums(values, history=True):
    normalized = [normalized_stats(item,history) for item in values]
    return {field:sum((item[field] for item in normalized),Decimal(0) if field=='cost_usd' else 0)
            for field in (HISTORY_COUNTERS if history else COUNTERS)+('cost_usd',)}


def url(path, params):
    return path+'?'+urllib.parse.urlencode(params)


def safe_path(path):
    split = urllib.parse.urlsplit(path)
    redacted = re.sub(r'/[0-9]+(?=/|$)','/{id}',split.path)
    # Only non-identifying filter names are retained; no model, team or actor ID.
    return redacted + ('?' + '&'.join(sorted(urllib.parse.parse_qs(split.query))) if split.query else '')


def live_process_proof(actual, executable):
    path = actual.get('path')
    if isinstance(path,str) and path:
        require(os.path.normcase(os.path.abspath(path))==os.path.normcase(os.path.abspath(executable)), 'LIVE_LISTENER_BINARY_PATH_MISMATCH')
        return {'executable_path_readable':True,'proof':'listener executable path plus manifest binary SHA'}
    # Medium-integrity Windows cannot read LocalService ExecutablePath. Do not
    # pretend the disk hash is the process-image hash. Require the exact native
    # filename AND a checked ancestry chain to the running, named SCM service.
    require(actual.get('name','').casefold()==Path(executable).name.casefold(),'LIVE_LISTENER_BINARY_NAME_MISMATCH')
    require(actual.get('owned_service') is True and actual.get('service_state')=='Running','LIVE_LISTENER_SCM_OWNERSHIP_REQUIRED')
    return {'executable_path_readable':False,'proof':'listener filename and ancestry to Running RealYuApi; manifest file SHA verified separately',
            'limit':'OS denied full process executable path; no claim of direct process-image hash'}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        raise CheckFailure('HTTP_REDIRECT_FORBIDDEN')


class Audit:
    def __init__(self,out,args):
        self.out,self.args = out,args
        self.report = {'status':'RUNNING','expected_version':EXPECTED,'expected_binary_sha256':args.expected_sha256,
            'runner_sha256':sha(__file__),
            'started_at':datetime.now(timezone.utc).isoformat(),'checks':[],'http':[],
            'production_configuration_or_financial_mutations':False,'database_accessed':False,'model_requests':0,
            'customer_rows_or_credentials_exported':False,
            'ordinary_user_live_authorization':'NOT_RUN_NO_EXISTING_CREDENTIALS',
            'ordinary_user_reference':'isolated UX4 JWT team-scope acceptance; not a live-role claim',
            'authenticated_request_pacing':'at most 40 requests per 61 seconds; no failed-request retry'}
        self.openers={route:urllib.request.build_opener(urllib.request.ProxyHandler({'https':PROXY} if route=='proxy' else {}),NoRedirect()) for route in BASES}
        self.deadline=time.monotonic()+args.budget_seconds
        self.authenticated_times=deque()

    def save(self):
        (self.out/'progress.json').write_text(json.dumps(self.report,ensure_ascii=False,indent=2),encoding='utf-8')

    def first_failure(self,name,error):
        path=self.out/'first-failure.json'
        if not path.exists():
            write_new(path,{'name':name,'code':safe_error(error),'at':datetime.now(timezone.utc).isoformat()})

    def case(self,name,fn):
        try:
            require(time.monotonic()<self.deadline,'OPERATION_BUDGET_EXHAUSTED')
            fn()
            self.report['checks'].append({'name':name,'status':'PASS'})
        except Exception as error:
            self.first_failure(name,error)
            self.report['checks'].append({'name':name,'status':'FAIL','code':safe_error(error)})
        self.save()

    def fetch(self,path,route='proxy',token=None,payload=None):
        require(route in BASES,'ROUTE_NOT_ALLOWED')
        require(path.startswith('/') and not any(x in path for x in (':\\','://','\\','/../')),'PATH_NOT_ALLOWED')
        require(not path.startswith('/v1/'),'MODEL_API_FORBIDDEN')
        require(payload is None or path=='/api/v1/auth/login','HTTP_MUTATION_FORBIDDEN')
        if payload is None:
            allowed = (path.split('?')[0] in ('/api/status','/health','/home','/docs','/pricing','/api/v1/realyu/public-models','/api/v1/settings/public',HISTORY,HISTORY+'/summary',HISTORY+'/trend','/api/v1/realyu/teams') or
                       re.fullmatch(r'/api/v1/realyu/teams/\d+(/usage)?',path.split('?')[0]))
            require(allowed,'HTTP_READ_PATH_NOT_ALLOWED')
        if token:
            # Count all authenticated reads conservatively, even if this admin
            # is exempt from native panel rate limits. Never retry a 429.
            while True:
                now=time.monotonic()
                require(now<self.deadline,'OPERATION_BUDGET_EXHAUSTED')
                while self.authenticated_times and now-self.authenticated_times[0]>=61:
                    self.authenticated_times.popleft()
                if len(self.authenticated_times)<40:
                    self.authenticated_times.append(now)
                    break
                time.sleep(min(5,max(0.001,self.authenticated_times[0]+61-now),max(0.001,self.deadline-now)))
        require(time.monotonic()<self.deadline,'OPERATION_BUDGET_EXHAUSTED')
        probe=uuid.uuid4().hex
        headers={'Accept':'application/json','User-Agent':'RealYu-authorized-UX4-readonly-acceptance','X-Client-Request-Id':probe}
        if token:headers['Authorization']='Bearer '+token
        if payload is not None:headers['Content-Type']='application/json'
        request=urllib.request.Request(BASES[route]+path,headers=headers,data=None if payload is None else json.dumps(payload).encode())
        row={'route':route,'path':safe_path(path),'probe_id':probe,'at':datetime.now(timezone.utc).isoformat()}
        self.report['http'].append(row)
        started=time.monotonic()
        try:
            try:
                response=self.openers[route].open(request,timeout=min(18,max(0.1,self.deadline-time.monotonic())))
            except urllib.error.HTTPError as error:
                response=error
            with response:
                raw=response.read(16*1024*1024+1)
                require(len(raw)<=16*1024*1024,'RESPONSE_BODY_TOO_LARGE')
                row.update(http_status=response.status,bytes=len(raw),content_type=response.headers.get('content-type'),
                           cf_ray=response.headers.get('cf-ray'),request_id=response.headers.get('x-request-id'),
                           seconds=round(time.monotonic()-started,3))
                if payload is None:row['body_sha256']=hashlib.sha256(raw).hexdigest()
                if path.split('?')[0] in ('/home','/docs','/pricing'):
                    return row,raw.decode('utf-8',errors='strict')
                try:value=json.loads(raw)
                except Exception:raise CheckFailure('RESPONSE_NOT_JSON') from None
                require(isinstance(value,dict),'RESPONSE_NOT_OBJECT')
                return row,value
        except Exception as error:
            row.update(error=safe_error(error),seconds=round(time.monotonic()-started,3))
            raise

    def get(self,path,route='proxy',token=None,expected=200):
        row,body=self.fetch(path,route,token)
        require(row['http_status']==expected,'HTTP_EXPECTED_'+str(expected)+'_ACTUAL_'+str(row['http_status']))
        if expected!=200:return body
        require(body.get('code',0)==0,'API_ENVELOPE_CODE')
        return body.get('data',body)


def check_trend(trend, expected, start, end, tz, granularity):
    require(trend.get('tz')==tz and trend.get('granularity')==granularity,'TREND_CONFIGURATION')
    require(instant(trend['start'])==start and instant(trend['end'])==end,'TREND_FIXED_RANGE')
    instant(trend['as_of'])
    require(normalized_stats(trend['stats'])==expected,'TREND_VS_HISTORY_STATS')
    series=trend['series']
    require(isinstance(series,list),'TREND_SERIES_SCHEMA')
    require(sums(series)==expected,'TREND_BUCKET_SUM_MISMATCH')
    starts=[]
    zone=ZoneInfo(tz)
    for bucket in series:
        lo,hi=instant(bucket['bucket_start']),instant(bucket['bucket_end'])
        local=lo.astimezone(zone)
        require(lo<end and hi>start and hi>lo,'TREND_BUCKET_RANGE')
        if granularity=='hour':
            require(local.minute==local.second==local.microsecond==0 and hi-lo==timedelta(hours=1),'TREND_HOUR_BOUNDARY')
        else:
            require(local.hour==local.minute==local.second==local.microsecond==0 and
                    hi==(local+timedelta(days=1)).astimezone(timezone.utc),'TREND_LOCAL_DAY_BOUNDARY')
        starts.append(lo)
    require(starts==sorted(set(starts)),'TREND_ORDER_OR_DUPLICATE')


def check_team_scope(value,team_id):
    require(value.get('scope')=='team' and type(value.get('team_id')) is int
            and value['team_id']==team_id,'LIVE_TEAM_RESPONSE_SCOPE_MISMATCH')


def compare_team_pages(history,team_usage,team_id):
    """Full-range totals plus bounded detail samples; return only anonymous data."""
    check_team_scope(history,team_id)
    expected=normalized_stats(history['stats'])
    shared={key:expected[key] for key in COUNTERS+('cost_usd',)}
    require(normalized_stats(team_usage['stats'],False)==shared,'LIVE_TEAM_VS_EXISTING_STATS')
    require(history['total']==team_usage['total']==expected['requests'],'LIVE_TEAM_SOURCE_TOTAL_MISMATCH')
    require(history['coverage']['legacy_archive_complete'] is True,'LIVE_TEAM_ARCHIVE_COVERAGE')
    members=team_usage['member_summary']
    require(sums(members,False)==shared,'LIVE_TEAM_MEMBER_TOTAL_MISMATCH')
    names={member['member_id']:member['nickname'] for member in members}
    require(len(names)==len(members),'LIVE_TEAM_DUPLICATE_MEMBER_SUMMARY')
    require(all(isinstance(name,str) for name in names.values()),'LIVE_TEAM_MEMBER_NICKNAME_SCHEMA')
    items,other=history['items'],team_usage['items']
    require(isinstance(items,list) and isinstance(other,list) and len(items)==len(other)==min(2,expected['requests']),
            'LIVE_TEAM_DETAIL_SAMPLE_COUNT')
    require([item['id'] for item in items]==[item['id'] for item in other],'LIVE_TEAM_DETAIL_SOURCE_IDS')
    for item,previous in zip(items,other):
        require(item.get('scope')=='team' and item.get('team_id')==team_id,'LIVE_TEAM_DETAIL_SCOPE')
        require(item['actor_user_id']==previous['member_id'],'LIVE_TEAM_DETAIL_ACTOR')
        require(item['actor_nickname']==previous['nickname']==names.get(previous['member_id']),
                'LIVE_TEAM_DETAIL_NICKNAME')
        require('source_actor_id' not in item,'LIVE_TEAM_INTERNAL_ACTOR_EXPOSED')
        require(instant(item['created_at'])==instant(previous['created_at']),'LIVE_TEAM_DETAIL_ORIGINAL_TIME')
        require(dec(item['cost_usd'])==dec(previous['cost_usd']),'LIVE_TEAM_DETAIL_DECIMAL_COST')
        for field in ('input_tokens','output_tokens','total_tokens','cache_read_tokens','cache_creation_tokens','token_semantics','model'):
            require(item[field]==previous[field],'LIVE_TEAM_DETAIL_'+field.upper())
        for field in ('cache_read_tokens','cache_creation_tokens'):
            require(item[field] is None or type(item[field]) is int,'LIVE_TEAM_DETAIL_CACHE_NULL_SCHEMA')
    return expected,{'requests':expected['requests'],'legacy_requests':expected['legacy_requests'],
                     'native_requests':expected['native_requests'],'stats_sha256':digest(expected),
                     'member_count':len(members),'detail_samples':len(items),
                     'detail_source_ids_sha256':digest([item['id'] for item in items]),
                     'nickname_matches':len(items),'customer_identifiers_exported':False}


def check_live_team_scopes(audit,token,teams,params,start,end,tz):
    managed=[team for team in teams if team.get('closed_at')==0 and team.get('can_manage') is True]
    require(0<len(managed)<=10,'LIVE_MANAGEABLE_TEAM_COUNT_REQUIRED')
    audit.report['ux4_live_team_scope']={'active_manageable_teams':len(managed),'teams':[],
        'ordinary_member_live_403':'NOT_RUN_NO_EXISTING_CREDENTIALS',
        'ordinary_member_reference':'isolated UX4 candidate JWT owner/admin/member/former tests',
        'detail_limit_per_team':2,'full_range_statistics':True}
    for index,team in enumerate(managed,1):
        def projection(team=team,index=index):
            team_id=team['id']
            query={**params,'scope':'team','team_id':team_id}
            page=audit.get(url(HISTORY,{**query,'page':1,'page_size':2}),'proxy',token)
            previous=audit.get(url('/api/v1/realyu/teams/'+str(team_id)+'/usage',
                                   {**params,'page':1,'page_size':2}),'proxy',token)
            expected,anonymous=compare_team_pages(page,previous,team_id)
            summary=audit.get(url(HISTORY+'/summary',query),'proxy',token)
            check_team_scope(summary,team_id)
            require(normalized_stats(summary['all_time'])==expected,'LIVE_TEAM_SUMMARY_VS_HISTORY')
            for granularity,route in (('day','local'),('hour','proxy')):
                trend=audit.get(url(HISTORY+'/trend',{**query,'granularity':granularity}),route,token)
                check_team_scope(trend,team_id)
                check_trend(trend,expected,start,end,tz,granularity)
            audit.report['ux4_live_team_scope']['teams'].append({'ordinal':index,**anonymous,
                'summary_day_hour_exact':True})
        audit.case('ux4-manageable-team-'+str(index)+'-exact-scope-projection',projection)
    query={**params,'scope':'team','team_id':managed[0]['id']}
    for suffix,label in (('','list'),('/summary','summary'),('/trend','trend')):
        endpoint=HISTORY+suffix
        audit.case('ux4-team-'+label+'-anonymous-401',lambda endpoint=endpoint:
                   audit.get(url(endpoint,query),'proxy',expected=401))
        for field in ('user_id','member_id'):
            audit.case('ux4-team-'+label+'-forbidden-'+field,lambda endpoint=endpoint,field=field:
                       audit.get(url(endpoint,{**query,field:1}),'proxy',token,400))
        audit.case('ux4-team-'+label+'-duplicate-team-id',lambda endpoint=endpoint:
                   audit.get(url(endpoint,list(query.items())+[('team_id',managed[0]['id'])]),'proxy',token,400))


def execute(audit,args):
    # Local manifest guard catches stale pages/versions without a write or service operation.
    manifest=read(MANIFEST)['singlecore_api']
    require(manifest['sha']==args.expected_sha256==sha(manifest['exe']),'ACTIVE_BINARY_PIN_MISMATCH')
    command=("$ErrorActionPreference='Stop';[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false);"
             "$OutputEncoding=[Console]::OutputEncoding;"
             "$ids=@(Get-NetTCPConnection -LocalPort 18300 -State Listen | Select-Object -ExpandProperty OwningProcess -Unique);"
             "if($ids.Count -ne 1){exit 4};$p=Get-CimInstance Win32_Process -Filter ('ProcessId='+$ids[0]);"
             "$svc=Get-CimInstance Win32_Service -Filter \"Name='RealYuApi'\";"
             "$owned=$false;$walk=$p;for($i=0;$i -lt 8 -and $null -ne $walk;$i++){"
             "if($walk.ProcessId -eq $svc.ProcessId -and $svc.ProcessId -gt 0){$owned=$true;break};"
             "if($walk.ParentProcessId -le 0){break};$walk=Get-CimInstance Win32_Process -Filter ('ProcessId='+$walk.ParentProcessId)};"
             "@{path=$p.ExecutablePath;name=$p.Name;pid=$p.ProcessId;owned_service=$owned;service_state=$svc.State}|ConvertTo-Json -Compress")
    process=subprocess.run(['powershell.exe','-NoProfile','-Command',command],capture_output=True,encoding='utf-8',errors='strict',timeout=20,
                           creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    require(process.returncode==0,'LIVE_LISTENER_PROCESS_REQUIRED')
    actual=json.loads(process.stdout)
    audit.report['live_process_identity']=live_process_proof(actual,manifest['exe'])
    audit.report['manifest_binary_pin']='PASS'
    for route in ('local','direct','proxy'):
        def version(route=route):
            status=audit.get('/api/status',route)
            require(status.get('version')==EXPECTED and status.get('engine')=='sub2api' and status.get('single_core') is True,'LIVE_UX4_IDENTITY_REQUIRED')
        audit.case(route+'-exact-version',version)
    require(all(x['status']=='PASS' for x in audit.report['checks']),'VERSION_GATE_FAILED_NO_AUTHENTICATED_READS')
    for route in ('direct','proxy'):
        for path in ('/home?lang=zh','/home?lang=en','/docs?lang=zh','/docs?lang=en','/pricing?lang=zh','/pricing?lang=en'):
            def html(route=route,path=path):
                row,body=audit.fetch(path,route)
                require(row['http_status']==200,'HTML_HTTP_NOT_200')
                require('text/html' in (row['content_type'] or '') and '<!doctype html' in body.lower() and '</html>' in body.lower() and '<title' in body.lower() and 'RealYu' in body,'COMPLETE_REALYU_HTML_REQUIRED')
            audit.case(route+'-'+path,html)
        for suffix in ('','/summary','/trend'):
            audit.case(route+'-anonymous-'+(suffix or 'history'),lambda route=route,suffix=suffix:audit.get(HISTORY+suffix,route,expected=401))
    private=read(ADMIN_CREDENTIALS)
    row,body=audit.fetch('/api/v1/auth/login',payload={'email':private['admin_username'],'password':private['admin_password']})
    require(row['http_status']==200 and body.get('code',0)==0,'ADMIN_LOGIN_FAILED')
    data=body['data']
    require(data['user']['role']=='admin' and isinstance(data.get('access_token'),str),'ADMIN_SUBJECT_ROLE_REQUIRED')
    token=data['access_token']
    del private,body,data
    audit.report['admin_login']='PASS'
    zone=ZoneInfo(args.tz)
    end=instant(args.end) if args.end else datetime.now(zone).replace(hour=0,minute=0,second=0,microsecond=0).astimezone(timezone.utc)
    start=instant(args.start) if args.start else end-timedelta(days=30)
    require(start<end and end-start<=timedelta(days=31),'FIXED_HOURLY_RANGE_MAX_31_DAYS')
    require(end<=datetime.now(timezone.utc)-timedelta(minutes=5),'COMPLETED_FIXED_WINDOW_REQUIRED')
    params={'start':start.isoformat(),'end':end.isoformat(),'tz':args.tz}
    audit.report['fixed_window']={**params,'end_exclusive':True,'live_late_settlement_can_still_change_old_rows':True}
    baseline={}
    for scope in ('platform','self','personal'):
        def projection(scope=scope):
            query={**params,'scope':scope}
            page=audit.get(url(HISTORY,{**query,'page_size':1}),'proxy',token)
            expected=normalized_stats(page['stats'])
            require(page['scope']==scope and page['total']==expected['requests'],'HISTORY_PAGE_TOTAL')
            require(page['coverage']['legacy_archive_complete'] is True,'ARCHIVE_COVERAGE_REQUIRED')
            for item in page['items']:
                require('actor_nickname' in item and (item['actor_nickname'] is None or isinstance(item['actor_nickname'],str)),'HISTORY_NICKNAME_SCHEMA')
                require(item['cache_read_tokens'] is None or type(item['cache_read_tokens']) is int,'CACHE_NULL_SCHEMA')
                require(item['cache_creation_tokens'] is None or type(item['cache_creation_tokens']) is int,'CACHE_NULL_SCHEMA')
                dec(item['cost_usd'])
            summary=audit.get(url(HISTORY+'/summary',query),'proxy',token)
            require(normalized_stats(summary['all_time'])==expected,'SUMMARY_VS_HISTORY_STATS')
            for route in ('local','proxy'):
                for granularity in ('day','hour'):
                    trend=audit.get(url(HISTORY+'/trend',{**query,'granularity':granularity}),route,token)
                    check_trend(trend,expected,start,end,args.tz,granularity)
            baseline[scope]=expected
            audit.report.setdefault('fixed_scope_checks',{})[scope]={'requests':expected['requests'],'legacy_requests':expected['legacy_requests'],'native_requests':expected['native_requests'],'stats_sha256':digest(expected),'local_proxy_day_hour_equal':True}
        audit.case(scope+'-fixed-list-summary-local-public-day-hour',projection)
    def archive():
        summary=audit.get(HISTORY+'/summary?scope=platform','proxy',token)
        stats=normalized_stats(summary['all_time'])
        require(summary['coverage']['legacy_archive_complete'] is True and stats['legacy_requests']==31291,'IMMUTABLE_ARCHIVE_COUNT_OR_COVERAGE')
        audit.report['archive']={'legacy_visible_requests':31291,'archive_complete':True,'all_time_requests':stats['requests'],'native_requests':stats['native_requests'],'observed_at':datetime.now(timezone.utc).isoformat()}
    audit.case('immutable-31291-platform-archive',archive)
    for query in ({'scope':'self','user_id':1},{'scope':'platform','team_id':1}):
        audit.case('forbidden-scope-filter-'+next(k for k in query if k!='scope'),lambda query=query:audit.get(url(HISTORY+'/trend',{**params,**query}),'proxy',token,400))
    def self_subset():
        require(all(scope in baseline for scope in ('self','personal')),'SELF_SCOPE_BASELINES_REQUIRED')
        require(all(baseline['personal'][key]<=baseline['self'][key] for key in HISTORY_COUNTERS+('cost_usd',)),'PERSONAL_EXCEEDS_SELF')
    audit.case('personal-is-subset-of-self',self_subset)
    teams=audit.get('/api/v1/realyu/teams','proxy',token)
    require(isinstance(teams,list) and len(teams)<=100,'TEAM_LIST_SCHEMA_OR_LIMIT')
    actual_teams=[t for t in teams if t['closed_at']==0]
    require(actual_teams,'NO_ACTIVE_TEAM_FOR_LIVE_PROJECTION')
    audit.report['team_projection_count']=len(actual_teams)
    for index,team in enumerate(actual_teams,1):
        def team_case(team=team):
            root='/api/v1/realyu/teams/'+str(team['id'])
            overview=audit.get(root,'proxy',token)
            names={member['user_id']:member['nickname'] for member in overview['members']}
            first=audit.get(url(root+'/usage',{**params,'page':1,'page_size':1}),'proxy',token)
            second=audit.get(url(root+'/usage',{**params,'page':2,'page_size':1}),'local',token)
            expected=normalized_stats(first['stats'],False)
            require(first['total']==expected['requests'],'TEAM_PAGE_TOTAL')
            require(normalized_stats(second['stats'],False)==expected and second['member_summary']==first['member_summary'],'TEAM_PAGINATION_CHANGED_AGGREGATES')
            members=first['member_summary']
            require(sums(members,False)==expected,'TEAM_MEMBER_SUMMARY_DOES_NOT_SUM')
            ids=[row['member_id'] for row in members]
            require(ids==sorted(set(ids)),'TEAM_MEMBER_SUMMARY_NOT_ONE_PER_ACTOR')
            for member in members:
                require(member['member_id'] in names and member['nickname']==names[member['member_id']] and isinstance(member['nickname'],str),'TEAM_NICKNAME_MISMATCH')
            for item in first['items']+second['items']:
                require(item['nickname']==names.get(item['member_id']),'TEAM_ITEM_NICKNAME_MISMATCH')
                require('total_tokens' in item and 'token_semantics' in item,'TEAM_TOKEN_SCHEMA')
                require(item['cache_read_tokens'] is None or type(item['cache_read_tokens']) is int,'TEAM_CACHE_NULL_SCHEMA')
                require(item['cache_creation_tokens'] is None or type(item['cache_creation_tokens']) is int,'TEAM_CACHE_NULL_SCHEMA')
        audit.case('actual-team-'+str(index)+'-full-member-summary-nickname',team_case)
    def currency():
        settings=audit.get('/api/v1/settings/public','proxy')
        rate=Decimal(str(settings.get('customer_usd_to_cny','0')))
        require(settings.get('customer_currency')=='CNY' and rate.is_finite() and rate>0,'CUSTOMER_CURRENCY_CONFIG')
        catalog=audit.get('/api/v1/realyu/public-models','proxy')
        require(catalog.get('currency')=='USD' and catalog.get('price_unit')=='million_tokens','REFERENCE_PRICE_CURRENCY')
        require(isinstance(catalog.get('models'),list) and len(catalog['models'])>0,'PUBLIC_CATALOG_EMPTY')
        allowed={'id','pricing_type','input_price','output_price','cache_read_price','per_request_price','intervals','has_adjustments'}
        for model in catalog['models']:
            require(set(model)<=allowed,'PUBLIC_SUPPLY_FIELD_LEAK')
        audit.report['currency']={'api_cost_unit':'USD','customer_display_unit':'CNY','usd_to_cny':str(rate),'model_unit':'USD per million tokens','no_rebilling':True}
    audit.case('customer-cny-and-reference-usd',currency)
    check_live_team_scopes(audit,token,teams,params,start,end,args.tz)
    audit.report['limits']=['Ordinary-user live login/403 NOT_RUN: no existing authorized credentials; no forged JWT or account mutation',
        'HTTP metadata/body hashes only; no customer rows, nicknames, identities, keys, tokens or full HTML exported',
        'No model/SSE/WS/real payment/browser behavior in this read-only run',
        'Fixed past window avoids current requests; a late settlement may still change a historical row between requests and needs review, never a silent retry',
        'Login may update native authentication timestamps/session cache; no financial/configuration mutation is attempted']


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--released',action='store_true')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--out-root',type=Path,default=ROOT/'ux4-production-readonly')
    parser.add_argument('--start');parser.add_argument('--end');parser.add_argument('--tz',default='Asia/Shanghai')
    parser.add_argument('--budget-seconds',type=int,default=420)
    args=parser.parse_args()
    if not args.released:
        print(json.dumps({'status':'PREPARED_NOT_RUN','network_accessed':False,'private_files_read':False,'expected_version':EXPECTED}))
        return 0
    require(re.fullmatch(r'[0-9a-f]{64}',args.expected_sha256 or ''),'REVIEWED_BINARY_SHA_REQUIRED')
    require(60<=args.budget_seconds<=600,'BOUNDED_BUDGET_REQUIRED')
    require(bool(args.start)==bool(args.end),'BOTH_FIXED_RANGE_ENDPOINTS_REQUIRED')
    ZoneInfo(args.tz)
    args.out_root.mkdir(exist_ok=True,parents=True)
    out=args.out_root/(datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid.uuid4().hex[:10])
    out.mkdir()
    audit=Audit(out,args)
    try:
        execute(audit,args)
    except Exception as error:
        audit.first_failure('operation',error)
        audit.report['checks'].append({'name':'operation','status':'FAIL','code':safe_error(error)})
    audit.report.update(status='PASS' if all(row['status']=='PASS' for row in audit.report['checks']) else 'FAIL',
        checks_passed=sum(row['status']=='PASS' for row in audit.report['checks']),checks_total=len(audit.report['checks']),
        ended_at=datetime.now(timezone.utc).isoformat())
    write_new(out/'result.json',audit.report)
    print(json.dumps({'status':audit.report['status'],'passed':audit.report['checks_passed'],'total':audit.report['checks_total'],'report':str(out/'result.json')}))
    return 0 if audit.report['status']=='PASS' else 1


if __name__=='__main__':
    raise SystemExit(main())

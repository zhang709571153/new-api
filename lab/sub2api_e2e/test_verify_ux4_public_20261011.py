import argparse
import ast
import copy
from decimal import Decimal
import json
import inspect
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import verify_ux4_public_20261011 as e


class PublicReadOnlySafety(unittest.TestCase):
    def test_original_execute_checks_are_preserved_before_one_team_extension(self):
        import verify_ux3_public_20261011 as old
        original=ast.parse(inspect.getsource(old.execute))
        new=ast.parse(inspect.getsource(e.execute).replace('UX4','UX3').replace('ux4','ux3'))
        body=new.body[0].body
        calls=[node for node in body if isinstance(node,ast.Expr) and isinstance(node.value,ast.Call)
               and isinstance(node.value.func,ast.Name) and node.value.func.id=='check_live_team_scopes']
        self.assertEqual(1,len(calls))
        body.remove(calls[0])
        self.assertEqual(ast.dump(original),ast.dump(new))
        self.assertEqual('realyu-singlecore-v0.2.15-20261011-ux4',e.EXPECTED)

    def test_localservice_path_denial_requires_real_named_service_ancestry(self):
        actual={'path':None,'name':'sub2api.exe','owned_service':True,'service_state':'Running'}
        proof=e.live_process_proof(actual,r'C:\isolated\sub2api.exe')
        self.assertFalse(proof['executable_path_readable'])
        for changed in ({'owned_service':False},{'name':'other.exe'},{'service_state':'Stopped'}):
            with self.subTest(changed=changed),self.assertRaises(e.CheckFailure):
                e.live_process_proof({**actual,**changed},r'C:\isolated\sub2api.exe')

    def test_default_offline(self):
        result=subprocess.run([sys.executable,e.__file__],capture_output=True,text=True)
        self.assertEqual(0,result.returncode)
        value=json.loads(result.stdout)
        self.assertEqual('PREPARED_NOT_RUN',value['status'])
        self.assertFalse(value['network_accessed'])
        self.assertFalse(value['private_files_read'])

    def test_no_model_payment_or_general_mutation_and_no_redirect(self):
        with tempfile.TemporaryDirectory() as temp:
            a=e.Audit(Path(temp),argparse.Namespace(expected_sha256='a'*64,budget_seconds=300))
            a.openers={route:Mock() for route in e.BASES}
            for path,payload in [('/v1/responses',{}),('/api/v1/payment/orders',{}),('/api/v1/admin/settings',{}),('/api/v1/keys',None),('https://example.org',None)]:
                with self.subTest(path=path),self.assertRaises(e.CheckFailure):a.fetch(path,payload=payload)
            for opener in a.openers.values():opener.open.assert_not_called()
            with self.assertRaises(e.CheckFailure):e.NoRedirect().redirect_request()

    def test_first_failure_is_immutable_and_external_messages_suppressed(self):
        with tempfile.TemporaryDirectory() as temp:
            a=e.Audit(Path(temp),argparse.Namespace(expected_sha256='a'*64,budget_seconds=300))
            a.case('first',lambda:e.require(False,'FIRST_FAILURE'))
            first=(Path(temp)/'first-failure.json').read_bytes()
            a.case('second',lambda:(_ for _ in ()).throw(ValueError('PASSWORD_PRIVATE_SENTINEL')))
            self.assertEqual(first,(Path(temp)/'first-failure.json').read_bytes())
            self.assertNotIn('PASSWORD_PRIVATE_SENTINEL',(Path(temp)/'progress.json').read_text())
            self.assertEqual('/api/v1/realyu/teams/{id}/usage?member_id&scope',e.safe_path('/api/v1/realyu/teams/123/usage?member_id=987&scope=team'))


class StatComparison(unittest.TestCase):
    def sample(self):
        return dict(requests=1,input_tokens=100,output_tokens=10,total_tokens=110,cache_read_tokens=80,cache_creation_tokens=0,
            cache_read_known_requests=1,cache_creation_known_requests=0,legacy_requests=1,native_requests=0,unknown_scope_requests=0,cost_usd='0.00000200000000000000')

    def test_exact_decimal_and_null_coverage_counters(self):
        value=self.sample()
        self.assertEqual(Decimal('0.000002'),e.sums([value])['cost_usd'])
        for key,wrong in [('cost_usd',0.000002),('cost_usd','NaN'),('requests',True),('cache_creation_known_requests',2),('native_requests',1)]:
            with self.subTest(key=key),self.assertRaises(e.CheckFailure):e.normalized_stats({**value,key:wrong})

    def test_trend_exact_sum_range_and_no_duplicate_buckets(self):
        value=self.sample()
        lo,hi=e.instant('2026-10-01T00:00:00Z'),e.instant('2026-10-02T00:00:00Z')
        trend={'tz':'UTC','granularity':'day','start':lo.isoformat(),'end':hi.isoformat(),'as_of':hi.isoformat(),'stats':value,
               'series':[{'bucket_start':lo.isoformat(),'bucket_end':hi.isoformat(),**value}]}
        e.check_trend(trend,e.normalized_stats(value),lo,hi,'UTC','day')
        for corruption in ('money','tokens','duplicate','date'):
            actual=copy.deepcopy(trend)
            if corruption=='money':actual['series'][0]['cost_usd']='0.000003'
            elif corruption=='tokens':actual['series'][0]['total_tokens']=190
            elif corruption=='duplicate':actual['series'].append(actual['series'][0].copy())
            else:actual['series'][0]['bucket_start']='2026-10-01T01:00:00Z'
            with self.subTest(corruption=corruption),self.assertRaises(e.CheckFailure):e.check_trend(actual,e.normalized_stats(value),lo,hi,'UTC','day')


class LiveTeamScope(unittest.TestCase):
    def pages(self):
        stats=StatComparison().sample()
        item=dict(id='legacy:100',scope='team',team_id=11,actor_user_id=21,actor_nickname='Private nickname',
                  created_at='2026-10-01T00:00:00Z',cost_usd='0.000002',input_tokens=100,output_tokens=10,
                  total_tokens=110,cache_read_tokens=80,cache_creation_tokens=None,token_semantics='legacy_reported',model='synthetic')
        shared={key:stats[key] for key in e.COUNTERS+('cost_usd',)}
        history={'scope':'team','team_id':11,'stats':stats,'total':1,'items':[item],
                 'coverage':{'legacy_archive_complete':True}}
        other={'stats':shared,'total':1,'items':[{**item,'member_id':21,'nickname':'Private nickname'}],
               'member_summary':[{**shared,'member_id':21,'nickname':'Private nickname'}]}
        return history,other

    def test_live_details_and_global_stats_exact_without_identity_export(self):
        history,other=self.pages()
        stats,report=e.compare_team_pages(history,other,11)
        self.assertEqual(e.normalized_stats(history['stats']),stats)
        self.assertEqual(1,report['detail_samples'])
        encoded=json.dumps(report)
        for private in ('Private nickname','legacy:100','actor_user_id','member_id','team_id'):
            self.assertNotIn(private,encoded)
        self.assertRegex(report['stats_sha256'],r'^[a-f0-9]{64}$')

    def test_scope_source_identity_cache_and_one_quota_corruption_rejected(self):
        for corruption in ('scope','team','cost','nickname','actor','cache','source','members','count'):
            history,other=self.pages()
            if corruption=='scope':history['scope']='self'
            if corruption=='team':history['team_id']=12
            if corruption=='cost':other['stats']['cost_usd']='0.000004'
            if corruption=='nickname':history['items'][0]['actor_nickname']='Wrong'
            if corruption=='actor':history['items'][0]['actor_user_id']=22
            if corruption=='cache':history['items'][0]['cache_creation_tokens']=0
            if corruption=='source':other['items'][0]['id']='legacy:101'
            if corruption=='members':other['member_summary'].append(copy.deepcopy(other['member_summary'][0]))
            if corruption=='count':history['total']=2
            with self.subTest(corruption=corruption),self.assertRaises(e.CheckFailure):
                e.compare_team_pages(history,other,11)

    def test_only_explicit_active_manageable_teams_are_queried(self):
        for teams in ([],[{'id':11,'closed_at':1,'can_manage':True}],
                      [{'id':11,'closed_at':0,'can_manage':False}],
                      [{'id':11,'closed_at':0}],
                      [{'id':n,'closed_at':0,'can_manage':True} for n in range(11)]):
            audit=Mock();audit.report={}
            with self.subTest(teams=teams),self.assertRaises(e.CheckFailure):
                e.check_live_team_scopes(audit,'PRIVATE_TOKEN',teams,{},None,None,'UTC')
            audit.get.assert_not_called()

    def test_authenticated_reads_paced_and_429_is_not_retried(self):
        with tempfile.TemporaryDirectory() as temp:
            audit=e.Audit(Path(temp),argparse.Namespace(expected_sha256='a'*64,budget_seconds=300))
            now=[0.0];audit.deadline=300;sleeps=[]
            def sleep(seconds):now[0]+=seconds;sleeps.append(seconds)
            response=Mock();response.status=429;response.headers={};response.read.return_value=b'{"code":429}'
            response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
            opener=Mock();opener.open.return_value=response;audit.openers['proxy']=opener
            with patch.object(e.time,'monotonic',side_effect=lambda:now[0]),patch.object(e.time,'sleep',side_effect=sleep):
                for _ in range(41):
                    with self.assertRaises(e.CheckFailure):audit.get(e.HISTORY,token='PRIVATE_TOKEN')
            self.assertEqual(41,opener.open.call_count)
            self.assertGreaterEqual(now[0],61)
            self.assertTrue(all(0<s<=5 for s in sleeps))
            self.assertNotIn('PRIVATE_TOKEN',json.dumps(audit.report))


if __name__=='__main__':unittest.main()

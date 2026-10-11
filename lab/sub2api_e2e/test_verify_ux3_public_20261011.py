import argparse
import copy
from decimal import Decimal
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock
import verify_ux3_public_20261011 as e


class PublicReadOnlySafety(unittest.TestCase):
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


if __name__=='__main__':unittest.main()

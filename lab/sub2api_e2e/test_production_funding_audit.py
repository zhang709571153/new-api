import copy
import tempfile
import unittest
from pathlib import Path
from decimal import Decimal
import production_funding_audit as audit


def base():
    return {'installation_id': 'synthetic', 'selected_key': 'k1', 'captured_at': '2026-10-10T00:00:00Z',
            'users': {'u1': '1', 'u2': '2'},
            'keys': {'k1': {'used': 0}, 'k2': {'used': 0}},
            'scopes': {'k1': {'actor': 'u1', 'payer': 'u1', 'team': None, 'key_owner': 'u1'},
                       'k2': {'actor': 'u2', 'payer': 'u1', 'team': 't', 'key_owner': 'u2'}},
            'teams': {'t': 'u1'},
            'subscriptions': {'s': {'user': 'u1', 'team': 't', 'used': 100, 'weekly_amount': 1000, 'weekly_used': 100, 'reset': 1}},
            'members': {}, 'requests': {}, 'usage': {}, 'dedup': {}}


def request(snap, rid='r1', key='k1', target=10, status='settled'):
    scope = snap['scopes'][key]
    team = scope['team'] is not None
    r = dict(scope, key=key, sub='s' if team else None, period='p' if team else None, reset=1,
             status=status, subscription=target if team else 0, wallet=0 if team else target,
             target=target, fingerprint='synthetic')
    r.pop('key_owner')
    snap['requests'][rid] = r
    if team:
        snap['subscriptions']['s']['used'] += target
        snap['subscriptions']['s']['weekly_used'] += target
        snap['members'].setdefault('p', {'used': 0, 'opening': 0})['used'] += target
    else:
        snap['users'][scope['payer']] = str(Decimal(snap['users'][scope['payer']]) - Decimal(target) / audit.QUOTA)
    if status == 'settled':
        snap['keys'][key]['used'] += target
        snap['dedup'][rid] = ['fingerprint']
        snap['usage'][rid] = [{'actor': scope['actor'], 'actual_usd': str(Decimal(target) / audit.QUOTA)}]


class AuditTests(unittest.TestCase):
    def test_concurrent_personal_and_team_are_exact_without_conflating_selected(self):
        before = base(); after = copy.deepcopy(before)
        request(after); request(after, 'r2', 'k2', 21)
        out = audit.compare(before, after)
        self.assertEqual(out['status'], 'PASS')
        self.assertEqual(out['selected_key_settled_quota'], 10)
        self.assertEqual(out['concurrent_other_key_request_statuses'], {'settled': 1})

    def test_reservation_refund_restores_wallet_without_key_charge(self):
        before = base(); request(before, status='reserved', target=35)
        after = copy.deepcopy(before)
        after['requests']['r1'].update(status='refunded', wallet=0, target=0)
        after['users']['u1'] = '1'
        self.assertEqual(audit.compare(before, after)['status'], 'PASS')

    def test_reserve_to_settle_accounts_only_difference_wallet_and_full_key(self):
        before = base(); request(before, status='reserved', target=35)
        after = base(); request(after, target=10)
        self.assertEqual(audit.compare(before, after)['status'], 'PASS')

    def test_team_spill_and_actor_mismatch_are_reported(self):
        before = base(); after = copy.deepcopy(before); request(after, 'r2', 'k2', 20)
        after['requests']['r2'].update(wallet=1, subscription=19, actor='u1')
        out = audit.compare(before, after)
        self.assertEqual(out['status'], 'REVIEW_REQUIRED')
        self.assertTrue({'ACTOR_PAYER_SCOPE_MISMATCH', 'TEAM_PAYER_OR_PERSONAL_SPILL'} <= {v['code'] for v in out['violations']})

    def test_duplicate_dedup_or_missing_log_never_passes(self):
        before = base(); after = copy.deepcopy(before); request(after)
        after['usage'].clear()
        self.assertEqual(audit.compare(before, after)['status'], 'PENDING')
        after['dedup']['r1'].append('different')
        self.assertEqual(audit.compare(before, after)['status'], 'REVIEW_REQUIRED')

    def test_credit_concurrency_is_residual_not_hidden_as_tolerance(self):
        before = base(); after = copy.deepcopy(before); request(after)
        after['users']['u1'] = str(Decimal(after['users']['u1']) + Decimal('0.000002'))
        out = audit.compare(before, after)
        self.assertEqual(out['status'], 'REVIEW_REQUIRED')
        self.assertEqual(Decimal(out['unexplained_financial_changes'][0]['unexplained_quota']), 1)
        self.assertEqual(out['violations'], [])

    def test_week_change_requires_review_instead_of_false_mismatch(self):
        before = base(); after = copy.deepcopy(before)
        after['subscriptions']['s'].update(reset=2, weekly_used=0)
        self.assertEqual(audit.compare(before, after)['weekly_period_changes'], ['s'])

    def test_pseudonyms_do_not_include_raw_subject_and_files_are_exclusive(self):
        label = audit.Labels('synthetic-secret')
        self.assertEqual(label('user', 7), label('user', 7))
        self.assertNotEqual(label('user', 7), label('key', 7))
        self.assertNotIn('synthetic-secret', label('user', 7))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'snapshot.json'
            audit.save_new(path, {'first': True})
            with self.assertRaises(FileExistsError): audit.save_new(path, {'second': True})
            self.assertEqual(audit.read(path), {'first': True})

    def test_decimal_quota_cannot_round_off_residual(self):
        self.assertEqual(audit.quota('0.000002'), 1)
        with self.assertRaises(ValueError): audit.quota('0.000001')

    def test_fractional_native_opening_is_preserved_without_blocking(self):
        before = base(); before['keys']['k1']['used'] = Decimal('0.125')
        after = copy.deepcopy(before); request(after)
        out = audit.compare(before, after)
        self.assertEqual(out['status'], 'PASS')
        self.assertEqual(out['native_fractional_quota_observations'][0]['delta_quota_exact'], '10.000')
        self.assertFalse(out['native_fractional_quota_observations'][0]['rounded'])
        self.assertEqual(audit.scaled_decimal('0.00000025'), Decimal('0.125'))

    def test_fractional_native_charge_is_reported_against_integer_funding(self):
        before = base(); after = copy.deepcopy(before); request(after)
        after['keys']['k1']['used'] = Decimal('10.005')
        after['usage']['r1'][0]['actual_usd'] = '0.00002001'
        out = audit.compare(before, after)
        self.assertEqual(out['status'], 'REVIEW_REQUIRED')
        self.assertEqual(out['violations'][0]['native_actual_quota_exact'], '10.00500000')
        self.assertEqual(len(out['unexplained_financial_changes']), 1)
        self.assertEqual(out['unexplained_financial_changes'][0]['kind'], 'key_quota_used')
        self.assertEqual(out['checks']['key_quota_vs_native_actual_cost'], 2)

    def test_late_async_log_does_not_invent_new_key_charge(self):
        before = base(); request(before); before['usage'].clear()
        after = copy.deepcopy(before)
        after['usage']['r1'] = [{'actor': 'u1', 'actual_usd': '0.000020'}]
        out = audit.compare(before, after)
        self.assertEqual(out['status'], 'PENDING')
        self.assertEqual(out['unexplained_financial_changes'], [])


if __name__ == '__main__': unittest.main()

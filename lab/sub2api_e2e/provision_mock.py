"""Provision test pools only after stock Sub2API administrator setup is complete.

Never accepts legal commitments, bypasses the compliance guard, or imports
production OAuth material. Credentials and generated management keys stay local.
"""
import argparse
import json
from pathlib import Path
import secrets
import sys

from bootstrap_windows import get_json, PRIVATE


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--credentials', type=Path)
    parser.add_argument('--mock-port', type=int, default=28083)
    args = parser.parse_args()
    path = args.credentials or Path(json.loads((PRIVATE / 'current.json').read_text())['credentials_file'])
    state = json.loads(path.read_text())
    base = f'http://127.0.0.1:{state["sub2api_port"]}'
    token = get_json(base + '/api/v1/auth/login', {'email': state['admin_email'], 'password': state['admin_password']})['data']['access_token']
    status = get_json(base + '/api/v1/admin/compliance', token=token)['data']
    if status['required']:
        report = {'status': 'BLOCKED', 'reason': 'ADMIN_COMPLIANCE_ACK_REQUIRED', 'version': status['version'],
                  'document': 'https://github.com/Wei-Shaw/sub2api/blob/v0.2.15/docs/legal/admin-compliance.zh.md',
                  'automatic_acceptance': False, 'mock_e2e_performed': False, 'real_upstream_e2e_performed': False}
        (path.parent / 'provision-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report, indent=2))
        return 2
    if state.get('mock_fixtures'):
        raise RuntimeError('Already provisioned; refusing to create duplicate fixtures')
    management = get_json(base + '/api/v1/admin/settings/admin-api-key/regenerate', {}, token)['data']['key']
    state['admin_api_key'] = management
    state['mock_fixtures'] = []
    path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    suffix = secrets.token_hex(4)
    for tenant in ('alpha', 'beta'):
        group = get_json(base + '/api/v1/admin/groups', {'name': 'e2e-' + tenant + '-' + suffix,
            'platform': 'openai', 'rate_multiplier': 1, 'is_exclusive': True, 'subscription_type': 'standard',
            'allow_image_generation': True, 'allow_messages_dispatch': True}, token)['data']
        gid = group['id']
        account = get_json(base + '/api/v1/admin/accounts', {'name': 'mock-' + tenant + '-' + suffix,
            'platform': 'openai', 'type': 'apikey', 'credentials': {'api_key': 'mock-only-' + tenant,
                'base_url': f'http://127.0.0.1:{args.mock_port}', 'model_mapping': {'gpt-6.1-sol': 'gpt-6.1-sol'}},
            'concurrency': 2, 'priority': 1, 'group_ids': [gid], 'upstream_billing_probe_enabled': False}, token)['data']
        state['mock_fixtures'].append({'tenant': tenant, 'group_id': gid, 'account_id': account['id'], 'mock_port': args.mock_port})
        path.write_text(json.dumps(state, indent=2), encoding='utf-8')
    print(json.dumps({'status': 'PASS', 'groups': state['mock_fixtures'], 'credentials_file': str(path)}, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())

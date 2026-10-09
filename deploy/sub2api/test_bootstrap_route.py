import copy
import unittest

from bootstrap_route import BootstrapError, bootstrap, validate


def fixture():
    return {
        'candidate_url': 'http://127.0.0.1:23000', 'expected_version': 'isolated-test',
        'admin_access_token': 'synthetic-test-only', 'admin_user_id': 1,
        'route': {'name': 'sub2api-test', 'base_url': 'http://127.0.0.1:28080',
                  'models': 'test-model', 'group': 'default', 'sub2api_group_id': 7},
    }


class FixtureAPI:
    def __init__(self):
        self.channels = []
        self.creates = 0
        self.enabled = False
        self.version = 'isolated-test'
        self.hide_created = False

    def call(self, path, payload=None):
        if path == '/api/status':
            return {'version': self.version}
        if path == '/api/channel/sub2api/status':
            return {'enabled': self.enabled}
        if payload is not None:
            self.creates += 1
            if not self.hide_created:
                self.channels.append({**payload['channel'], 'id': 12})
            return {}
        return {'items': self.channels, 'total': len(self.channels)}


class BootstrapTests(unittest.TestCase):
    def test_rejects_production_ports_remote_hosts_and_userinfo(self):
        for url in ('http://127.0.0.1:18300', 'http://127.0.0.1:18301',
                    'https://api.realyu.fun', 'http://localhost:23000',
                    'http://secret@127.0.0.1:23000', 'http://127.0.0.1:23000/?x=1'):
            with self.subTest(url=url):
                config = fixture()
                config['candidate_url'] = url
                with self.assertRaises(BootstrapError):
                    validate(config)

    def test_idempotent_rerun_and_no_credential_in_created_channel(self):
        config, api = fixture(), FixtureAPI()
        self.assertTrue(bootstrap(config, api)['created'])
        self.assertFalse(bootstrap(config, api)['created'])
        self.assertEqual(1, api.creates)
        self.assertNotIn(config['admin_access_token'], repr(api.channels))
        self.assertEqual(59, api.channels[0]['type'])

    def test_no_write_when_driver_enabled_or_version_wrong(self):
        for changes in ({'enabled': True}, {'version': 'live-unexpected'}):
            api = FixtureAPI()
            for key, value in changes.items():
                setattr(api, key, value)
            with self.assertRaises(BootstrapError):
                bootstrap(fixture(), api)
            self.assertEqual(0, api.creates)

    def test_active_legacy_route_prevents_bootstrap(self):
        api = FixtureAPI()
        api.channels = [{'name': 'legacy', 'type': 57, 'status': 1}]
        with self.assertRaises(BootstrapError):
            bootstrap(fixture(), api)
        self.assertEqual(0, api.creates)

    def test_name_collision_does_not_modify_existing_route(self):
        config, api = fixture(), FixtureAPI()
        bootstrap(config, api)
        original = copy.deepcopy(api.channels)
        config['route']['models'] = 'changed-model'
        with self.assertRaises(BootstrapError):
            bootstrap(config, api)
        self.assertEqual(original, api.channels)
        self.assertEqual(1, api.creates)

    def test_missing_readback_never_retries_a_create(self):
        api = FixtureAPI()
        api.hide_created = True
        with self.assertRaises(BootstrapError):
            bootstrap(fixture(), api)
        self.assertEqual(1, api.creates)


if __name__ == '__main__':
    unittest.main()

"""Create a type-59 route on an explicitly isolated, legacy-mode candidate.

Uses existing authenticated channel APIs, never edits a database or changes the
selected driver. Credentials come from a private JSON file, not CLI arguments.
Run before activating REALYU_UPSTREAM_DRIVER=sub2api. See README.md for scope.
"""
import argparse
import json
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request


class BootstrapError(Exception):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BootstrapError('Redirects are forbidden for candidate administration')


def validate(config):
    url = urllib.parse.urlsplit(config['candidate_url'])
    if (url.scheme != 'http' or url.hostname != '127.0.0.1'
            or url.port is None or url.port < 1024 or url.port in (18300, 18301)
            or url.username or url.password or url.path not in ('', '/')
            or url.query or url.fragment):
        raise BootstrapError('Use an explicit isolated 127.0.0.1 HTTP port, excluding old production ports')
    if not config.get('expected_version') or not config.get('admin_access_token'):
        raise BootstrapError('An exact candidate version and private administrator token are required')
    if not isinstance(config.get('admin_user_id'), int) or config['admin_user_id'] < 1:
        raise BootstrapError('A valid administrator identity is required')
    route = config['route']
    upstream = urllib.parse.urlsplit(route['base_url'])
    if (upstream.scheme not in ('http', 'https') or not upstream.hostname
            or upstream.username or upstream.password or upstream.path not in ('', '/')
            or upstream.query or upstream.fragment):
        raise BootstrapError('Sub2API base_url must be an origin without credentials or a /v1 suffix')
    for key in ('name', 'models', 'group'):
        if not isinstance(route.get(key), str) or not route[key].strip():
            raise BootstrapError('Route name, models and RealYu permission groups are required')
    if not route['name'].startswith('sub2api-'):
        raise BootstrapError('Route names must start with sub2api-')
    if not isinstance(route.get('sub2api_group_id'), int) or route['sub2api_group_id'] < 1:
        raise BootstrapError('Supply the existing Sub2API group ID')
    return url.geturl().rstrip('/')


class Client:
    def __init__(self, config):
        self.base = validate(config)
        self.headers = {'Authorization': 'Bearer ' + config['admin_access_token'],
                        'New-Api-User': str(config['admin_user_id']), 'Content-Type': 'application/json'}
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def call(self, path, payload=None):
        request = urllib.request.Request(self.base + path, headers=self.headers,
            data=None if payload is None else json.dumps(payload).encode('utf-8'))
        try:
            with self.opener.open(request, timeout=20) as response:
                raw = response.read(4 * 1024 * 1024 + 1)
                if len(raw) > 4 * 1024 * 1024:
                    raise BootstrapError('Candidate response exceeded the size limit')
                result = json.loads(raw)
        except (urllib.error.URLError, OSError, ValueError):
            raise BootstrapError('Candidate request failed; inspect private server logs') from None
        if result.get('success') is not True:
            raise BootstrapError('Candidate rejected the operation; inspect private server logs')
        return result.get('data', {})


def bootstrap(config, client, allow_create=True):
    """Fail closed on a mismatched version, enabled driver or active legacy pool."""
    validate(config)
    if client.call('/api/status').get('version') != config['expected_version']:
        raise BootstrapError('Candidate version mismatch')
    if client.call('/api/channel/sub2api/status').get('enabled') is not False:
        raise BootstrapError('Create routes before activating the Sub2API driver')
    items = []
    page = 1
    while True:
        result = client.call(f'/api/channel/?p={page}&page_size=100')
        batch = result.get('items')
        if not isinstance(batch, list):
            raise BootstrapError('Unexpected channel listing')
        items.extend(batch)
        if len(items) >= result.get('total', len(items)):
            break
        if not batch or page >= 100:
            raise BootstrapError('Channel listing is incomplete')
        page += 1
    if any(item.get('type') != 59 and item.get('status') == 1 for item in items):
        raise BootstrapError('Active legacy channels exist; this is not a prepared isolated candidate')
    route = config['route']
    matches = [item for item in items if item.get('name') == route['name']]
    if len(matches) > 1:
        raise BootstrapError('Duplicate route names require manual reconciliation')
    if matches:
        item = matches[0]
        if (item.get('type') != 59 or item.get('base_url', '').rstrip('/') != route['base_url'].rstrip('/')
                or item.get('models') != route['models'] or item.get('group') != route['group']
                or item.get('status') != 1):
            raise BootstrapError('Existing route differs; refusing to overwrite it')
        return {'channel_id': item['id'], 'base_url': route['base_url'].rstrip('/'),
                'group_id': route['sub2api_group_id'], 'created': False}
    if not allow_create:
        raise BootstrapError('Created route was not found; inspect before rerunning')
    channel = {key: route[key] for key in ('name', 'base_url', 'models', 'group')}
    channel.update(type=59, status=1, priority=1, weight=1,
        key='routing-placeholder-never-an-upstream-credential',
        setting=json.dumps({'pass_through_body_enabled': True, 'responses_websocket_enabled': True}))
    client.call('/api/channel/', {'mode': 'single', 'channel': channel})
    # Read back through the same checks. A failed readback is not retried as a
    # second create; the deterministic name allows safe operator reruns.
    receipt = bootstrap(config, client, allow_create=False)
    receipt['created'] = True
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--private-config', type=Path, required=True)
    parser.add_argument('--confirm-isolated-candidate', action='store_true', required=True)
    args = parser.parse_args()
    try:
        config = json.loads(args.private_config.read_text(encoding='utf-8-sig'))
        print(json.dumps(bootstrap(config, Client(config)), indent=2))
    except (BootstrapError, KeyError, TypeError, ValueError, OSError) as exc:
        # Do not echo config, URLs with userinfo, auth data or raw API bodies.
        message = str(exc) if isinstance(exc, BootstrapError) else 'Invalid private configuration'
        print(message, file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())

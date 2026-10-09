"""Loopback fake Sub2API management protocol for worker contract tests.

This is explicitly not the stock Sub2API service. No inference is implemented.
Credentials stay in memory; snapshots contain counts and hashes only.
"""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
from urllib.parse import parse_qs, urlsplit


class ProjectionControlFixture:
    def __init__(self, login_failures=(), group_failure=0, retry_after=1):
        self.users = {}
        self.passwords = {}
        self.keys = {}
        self.events = []
        self.login_failures = list(login_failures)
        self.group_failure = group_failure
        self.retry_after = retry_after
        self.lock = threading.RLock()
        fixture = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass

            def do_GET(self):
                fixture.handle(self)

            def do_POST(self):
                fixture.handle(self)

            def do_PUT(self):
                fixture.handle(self)

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.base_url = 'http://127.0.0.1:' + str(self.server.server_port)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)

    def handle(self, handler):
        raw_path = urlsplit(handler.path)
        path, query = raw_path.path, parse_qs(raw_path.query)
        length = int(handler.headers.get('Content-Length', '0'))
        if length > 1024 * 1024:
            handler.send_error(413)
            return
        body = json.loads(handler.rfile.read(length)) if length else {}
        with self.lock:
            event = {'method': handler.command, 'path': path, 'at': time.monotonic()}
            self.events.append(event)

            def respond(value=None, status=200):
                event['status'] = status
                payload = {'code': 0, 'data': value} if status == 200 else {'message': 'fixture-sensitive-message'}
                encoded = json.dumps(payload).encode()
                handler.send_response(status)
                handler.send_header('Content-Type', 'application/json')
                handler.send_header('Content-Length', str(len(encoded)))
                if status == 429:
                    handler.send_header('Retry-After', str(self.retry_after))
                handler.end_headers()
                handler.wfile.write(encoded)

            if path.startswith('/api/v1/admin/') and handler.headers.get('x-api-key') != 'fixture-admin':
                respond(status=401)
            elif path.startswith('/api/v1/admin/groups/'):
                if self.group_failure:
                    respond(status=self.group_failure)
                else:
                    respond({'id': int(path.rsplit('/', 1)[1]), 'platform': 'openai', 'status': 'active', 'subscription_type': 'standard'})
            elif path == '/api/v1/admin/users' and handler.command == 'GET':
                user = self.users.get(query.get('search', [''])[0])
                respond({'items': [user] if user else [], 'pages': 1})
            elif path == '/api/v1/admin/users' and handler.command == 'POST':
                if body['email'] in self.users:
                    respond(status=409)
                    return
                user = {key: body[key] for key in ('email', 'notes', 'role', 'allowed_groups', 'restrict_public_groups')}
                user.update(id=len(self.users) + 1, status='active')
                self.users[user['email']] = user
                self.passwords[user['email']] = body['password']
                event['created_user_id'] = user['id']
                respond(user)
            elif path.startswith('/api/v1/admin/users/') and handler.command == 'PUT':
                user_id = int(path.rsplit('/', 1)[1])
                for user in self.users.values():
                    if user['id'] == user_id:
                        user.update({key: body[key] for key in ('allowed_groups', 'restrict_public_groups')})
                respond()
            elif path.startswith('/api/v1/admin/users/') and path.endswith('/api-keys'):
                user_id = int(path.split('/')[-2])
                respond({'items': [key for key in self.keys.values() if key['user_id'] == user_id], 'pages': 1})
            elif path == '/api/v1/auth/login':
                if self.login_failures:
                    respond(status=self.login_failures.pop(0))
                    return
                user = self.users.get(body.get('email'))
                if not user or self.passwords[user['email']] != body.get('password'):
                    respond(status=401)
                    return
                event['login_user_id'] = user['id']
                respond({'access_token': 'fixture-user-' + str(user['id'])})
            elif path == '/api/v1/keys':
                prefix = 'Bearer fixture-user-'
                authorization = handler.headers.get('Authorization', '')
                if not authorization.startswith(prefix):
                    respond(status=401)
                    return
                user_id = int(authorization.removeprefix(prefix))
                user = next((u for u in self.users.values() if u['id'] == user_id), None)
                if not user or body['group_id'] not in user['allowed_groups']:
                    respond(status=403)
                    return
                if body['custom_key'] in self.keys:
                    respond(status=409)
                    return
                self.keys[body['custom_key']] = {'user_id': user_id, 'group_id': body['group_id'], 'key': body['custom_key'], 'status': 'active'}
                event['created_key_user_id'] = user_id
                respond(self.keys[body['custom_key']])
            else:
                respond(status=500)

    def snapshot(self):
        with self.lock:
            return {'users': len(self.users), 'keys': len(self.keys),
                'user_creates': sum('created_user_id' in e for e in self.events),
                'key_creates': sum('created_key_user_id' in e for e in self.events),
                'login_attempts': sum(e['path'] == '/api/v1/auth/login' for e in self.events),
                'inference_requests': sum(e['path'].startswith('/v1/') for e in self.events),
                'events': [dict(e) for e in self.events],
                'key_ownership': [{'user_id': k['user_id'], 'group_id': k['group_id'],
                    'key_sha256': hashlib.sha256(k['key'].encode()).hexdigest()} for k in self.keys.values()]}

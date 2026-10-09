"""Deterministic loopback upstream for the *real* RealYu/Sub2API chain.

Synthetic protocol responses only. This is not a real-model E2E result.
"""
import argparse
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import re
import secrets
import threading
import time


class Handler(BaseHTTPRequestHandler):
    records = []
    lock = threading.Lock()
    released = threading.Event()
    active = 0
    max_active = 0

    def log_message(self, *_):
        pass

    def reply(self, status, body, kind='application/json'):
        payload = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(status)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/health':
            return self.reply(200, {'mock': True})
        if self.path == '/observations':
            with self.lock:
                snapshot = {'calls': len(self.records), 'active': self.active, 'max_active': self.max_active, 'records': list(self.records)}
            return self.reply(200, snapshot)
        if self.path.endswith('/models'):
            return self.reply(200, {'object': 'list', 'data': [{'id': 'gpt-6.1-sol', 'object': 'model'}]})
        return self.reply(404, {'error': {'message': 'Unknown mock route'}})

    def do_POST(self):
        if self.path == '/control/release':
            self.released.set()
            return self.reply(200, {'released': True})
        if self.path == '/control/reset':
            with self.lock:
                if self.active:
                    return self.reply(409, {'error': 'requests still active'})
                self.records.clear()
                type(self).max_active = 0
            self.released.clear()
            return self.reply(200, {'reset': True})
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', '0'))))
        encoded = json.dumps(body, ensure_ascii=False)
        marker = re.search(r'E2E_[A-Za-z0-9_]+', encoded)
        text = marker.group() if marker else 'MOCK_OK'
        with self.lock:
            type(self).active += 1
            type(self).max_active = max(self.max_active, self.active)
            self.records.append({'path': self.path, 'body': body, 'authorization_sha256': hashlib.sha256(self.headers.get('Authorization', '').encode()).hexdigest(),
                'headers': {k: v for k, v in self.headers.items() if k.lower() in ('session_id', 'x-codex-turn-state', 'x-codex-beta-features', 'originator')}})
        try:
            if 'MOCK_HOLD' in encoded and not self.released.wait(20):
                return self.reply(504, {'error': {'type': 'timeout', 'message': 'fixture release timeout'}})
            if 'MOCK_FAIL_429' in encoded:
                return self.reply(429, {'error': {'type': 'rate_limit_error', 'message': 'synthetic rate limit'}})
            if 'MOCK_FAIL_500' in encoded:
                return self.reply(500, {'error': {'type': 'server_error', 'message': 'synthetic failure'}})
            ident = secrets.token_hex(8)
            item = {'id': 'msg_' + ident, 'type': 'message', 'role': 'assistant', 'status': 'completed',
                'content': [{'type': 'output_text', 'text': text, 'annotations': []}]}
            result = {'id': 'resp_' + ident, 'object': 'response', 'created_at': int(time.time()), 'model': body.get('model', 'gpt-6.1-sol'),
                'status': 'completed', 'output': [item], 'parallel_tool_calls': True, 'error': None, 'incomplete_details': None,
                'usage': {'input_tokens': 100, 'output_tokens': 10, 'total_tokens': 110,
                    'input_tokens_details': {'cached_tokens': 20}, 'output_tokens_details': {'reasoning_tokens': 2}}}
            # Stock Sub2API probes actual Responses function support when an
            # APIKey account is created/updated. Reply truthfully in the mock
            # protocol instead of letting an incomplete fixture trigger CC mode.
            if body.get('tool_choice') == 'required' and any(t.get('name') == 'probe_ping' for t in body.get('tools', [])):
                result['output'] = [{'id': 'fc_' + ident, 'type': 'function_call', 'call_id': 'call_' + ident,
                    'name': 'probe_ping', 'arguments': '{"ok":true}', 'status': 'completed'}]
            if self.path.endswith('/chat/completions'):
                if body.get('stream'):
                    common = {'id': 'chatcmpl_' + ident, 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': result['model']}
                    chunks = [{**common, 'choices': [{'index': 0, 'delta': {'role': 'assistant', 'content': text}, 'finish_reason': None}]},
                        {**common, 'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]},
                        {**common, 'choices': [], 'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}}]
                    data = ''.join('data: ' + json.dumps(chunk) + '\n\n' for chunk in chunks) + 'data: [DONE]\n\n'
                    return self.reply(200, data.encode(), 'text/event-stream')
                return self.reply(200, {'id': 'chatcmpl_' + ident, 'object': 'chat.completion', 'created': int(time.time()),
                    'model': result['model'], 'choices': [{'index': 0, 'message': {'role': 'assistant', 'content': text}, 'finish_reason': 'stop'}],
                    'usage': {'prompt_tokens': 100, 'completion_tokens': 10, 'total_tokens': 110}})
            if not body.get('stream'):
                return self.reply(200, result)
            events = [
                {'type': 'response.created', 'response': {**result, 'status': 'in_progress', 'output': []}},
                {'type': 'response.output_item.added', 'output_index': 0, 'item': {**item, 'status': 'in_progress', 'content': []}},
                {'type': 'response.content_part.added', 'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'part': {'type': 'output_text', 'text': '', 'annotations': []}},
                {'type': 'response.output_text.delta', 'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'delta': text},
                {'type': 'response.output_text.done', 'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'text': text},
                {'type': 'response.content_part.done', 'item_id': item['id'], 'output_index': 0, 'content_index': 0, 'part': item['content'][0]},
                {'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                {'type': 'response.completed', 'response': {**result, 'output': []} if 'MOCK_EMPTY_TERMINAL' in encoded else result},
            ]
            data = b''
            for sequence, event in enumerate(events):
                event['sequence_number'] = sequence
                data += ('event: ' + event['type'] + '\ndata: ' + json.dumps(event) + '\n\n').encode()
            return self.reply(200, data, 'text/event-stream')
        finally:
            with self.lock:
                type(self).active -= 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=28083)
    args = parser.parse_args()
    ThreadingHTTPServer(('127.0.0.1', args.port), Handler).serve_forever()


if __name__ == '__main__':
    main()

"""Real loopback HTTP and RFC6455 frame tests; no external accounts or billing."""
import base64
import hashlib
import http.client
import json
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler

import edge_proxy as edge


def frame(payload, opcode=1, fin=True, masked=False):
    head = bytes([(0x80 if fin else 0) | opcode])
    length = len(payload)
    maskbit = 0x80 if masked else 0
    if length < 126:
        head += bytes([maskbit | length])
    elif length < 65536:
        head += bytes([maskbit | 126]) + struct.pack('!H', length)
    else:
        head += bytes([maskbit | 127]) + struct.pack('!Q', length)
    if masked:
        key = b'abcd'
        return head + key + bytes(v ^ key[i % 4] for i, v in enumerate(payload))
    return head + payload


def receive_frame(reader):
    first, second = edge.read_exact(reader, 2)
    length = second & 127
    if length == 126:
        length = struct.unpack('!H', edge.read_exact(reader, 2))[0]
    elif length == 127:
        length = struct.unpack('!Q', edge.read_exact(reader, 8))[0]
    key = edge.read_exact(reader, 4) if second & 128 else None
    payload = edge.read_exact(reader, length)
    if key:
        payload = bytes(v ^ key[i % 4] for i, v in enumerate(payload))
    return bool(first & 128), first & 15, payload


class Backend(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    rbufsize = 0
    seen = []
    release_stream = threading.Event()
    release_auth = threading.Event()

    def log_message(self, *_):
        pass

    def do_POST(self):
        payload = edge.read_exact(self.rfile, int(self.headers.get('Content-Length', '0')))
        self.seen.append((self.path, dict(self.headers), payload))
        self.send_response(200)
        self.send_header('Content-Length', str(len(payload)))
        self.send_header('Content-Type', 'application/octet-stream')
        self.send_header('X-Oneapi-Request-Id', 'safe-request-123')
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        self.seen.append((self.path, dict(self.headers), b''))
        if self.headers.get('Upgrade') == 'websocket':
            if 'denied' in self.path:
                body = b'{"error":{"type":"invalid_api_key"}}'
                self.send_response(401)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Connection', 'keep-alive')
                if 'chunked' in self.path:
                    self.send_header('Transfer-Encoding', 'chunked')
                else:
                    self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if 'chunked' in self.path:
                    self.wfile.write(f'{len(body):x}\r\n'.encode() + body + b'\r\n0\r\n\r\n')
                else:
                    self.wfile.write(body)
                self.wfile.flush()
                self.release_auth.wait(5)
                self.close_connection = True
                return
            key = self.headers['Sec-WebSocket-Key']
            accept = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            wire = ('HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n'
                    f'Sec-WebSocket-Accept: {accept}\r\nSec-WebSocket-Protocol: realtime\r\n\r\n').encode()
            # Deliberately combine first frame with headers in one socket write.
            self.connection.sendall(wire + frame(b'{"type":"hello"}'))
            try:
                while True:
                    fin, opcode, data = receive_frame(self.rfile)
                    self.connection.sendall(frame(data, 10 if opcode == 9 else opcode, fin))
                    if opcode == 8:
                        return
            except (OSError, ValueError):
                pass
            finally:
                self.close_connection = True
            return
        if self.path == '/stream':
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            first = b'event: response.output_text.delta\ndata: {"delta":"first"}\n\n'
            last = b'event: response.completed\ndata: {"usage":{"input_tokens":1}}\n\n'
            self.wfile.write(f'{len(first):x}\r\n'.encode() + first + b'\r\n')
            self.wfile.flush()
            self.release_stream.wait(5)
            self.wfile.write(f'{len(last):x}\r\n'.encode() + last + b'\r\n0\r\n\r\n')
            self.wfile.flush()
            return
        self.send_response(200)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'ok')


class EdgeTests(unittest.TestCase):
    def setUp(self):
        self.private = tempfile.TemporaryDirectory(prefix='realyu-edge-test-')
        edge.ADMISSION = edge.Admission(Path(self.private.name) / 'release-maintenance.json')
        Backend.seen = []
        Backend.release_stream.clear()
        Backend.release_auth.clear()
        self.backend = edge.BridgeHTTPServer(('127.0.0.1', 0), Backend)
        edge.BACKEND_PORT = self.backend.server_port
        self.proxy = edge.BridgeHTTPServer(('127.0.0.1', 0), edge.Handler)
        self.status = edge.BridgeHTTPServer(('127.0.0.1', 0), edge.MaintenanceStatusHandler)
        self.threads = []
        for server in (self.backend, self.proxy, self.status):
            thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': .02}, daemon=True)
            thread.start()
            self.threads.append(thread)
        self.sockets = []

    def tearDown(self):
        Backend.release_stream.set()
        Backend.release_auth.set()
        for sock in self.sockets:
            sock.close()
        for server, thread in zip((self.backend, self.proxy, self.status), self.threads):
            server.shutdown()
            server.server_close()
            thread.join(2)
        self.private.cleanup()

    def get_status(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.status.server_port, timeout=2)
        conn.request('GET', '/')
        value = json.loads(conn.getresponse().read())
        conn.close()
        return value

    def websocket(self, query='', pipeline=b''):
        sock = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=2)
        sock.settimeout(2)
        self.sockets.append(sock)
        key = base64.b64encode(b'0123456789abcdef').decode()
        request = (f'GET /v1/responses{query} HTTP/1.1\r\nHost: api.example.invalid\r\n'
                   'Connection: Upgrade\r\nUpgrade: websocket\r\nSec-WebSocket-Version: 13\r\n'
                   f'Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Protocol: realtime\r\nAuthorization: Bearer local-test-only\r\n\r\n').encode()
        sock.sendall(request + pipeline)
        reader = sock.makefile('rb', buffering=0)
        self.addCleanup(reader.close)
        status = reader.readline()
        headers = http.client.parse_headers(reader)
        if b'101' in status:
            expected = base64.b64encode(hashlib.sha1((key + '258EAFA5-E914-47DA-95CA-C5AB0DC85B11').encode()).digest()).decode()
            self.assertEqual(headers['Sec-WebSocket-Accept'], expected)
            self.assertEqual(headers['Sec-WebSocket-Protocol'], 'realtime')
        return sock, reader, status, headers

    def test_http_json_and_multipart_image_bytes_and_headers_unchanged(self):
        cases = [('/v1/responses', b'{"model":"gpt-test","temperature":0,"future":{"value":false}}', 'application/json'),
                 ('/v1/images/generations', b'{"model":"gpt-image-2","n":1}', 'application/json'),
                 ('/v1/images/edits', b'--test\r\nContent-Disposition: form-data; name="image"; filename="x.png"\r\n\r\n\x89PNG\x00\xff\r\n--test--\r\n', 'multipart/form-data; boundary=test')]
        for path, body, content_type in cases:
            with self.subTest(path=path):
                conn = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=2)
                conn.request('POST', path, body, {'Content-Type': content_type, 'Authorization': 'Bearer local-test-only', 'OpenAI-Beta': 'responses_websockets=2026-02-06', 'X-Session-Id': 'test-session'})
                resp = conn.getresponse()
                self.assertEqual(resp.status, 200)
                self.assertEqual(resp.read(), body)
                self.assertEqual(resp.getheader('X-Oneapi-Request-Id'), 'safe-request-123')
                conn.close()
                received_path, headers, received = Backend.seen[-1]
                self.assertEqual((received_path, received), (path, body))
                self.assertEqual(headers['Content-Type'], content_type)
                self.assertEqual(headers['Authorization'], 'Bearer local-test-only')
                self.assertEqual(headers['X-Session-Id'], 'test-session')

    def test_chunked_request_and_sse_first_event_before_completion(self):
        conn = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=2)
        conn.request('POST', '/echo', iter([b'first', b'\x00last']), {}, encode_chunked=True)
        self.assertEqual(conn.getresponse().read(), b'first\x00last')
        conn.close()
        conn = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=2)
        conn.request('GET', '/stream')
        resp = conn.getresponse()
        self.assertEqual(resp.status, 200)
        first = resp.readline() + resp.readline() + resp.readline()
        self.assertIn(b'"first"', first)
        self.assertFalse(Backend.release_stream.is_set())
        Backend.release_stream.set()
        last = resp.read()
        self.assertIn(b'response.completed', last)
        self.assertIn(b'"input_tokens":1', last)
        conn.close()

    def test_websocket_pipelined_large_fragmented_ping_close_frames(self):
        first = b'{"type":"response.create","model":"gpt-test"}'
        sock, reader, status, _ = self.websocket('?model=gpt-test', frame(first, masked=True))
        self.assertIn(b'101', status)
        self.assertEqual(receive_frame(reader), (True, 1, b'{"type":"hello"}'))
        self.assertEqual(receive_frame(reader), (True, 1, first))
        for fin, opcode, data in [(False, 1, b'first fragment'), (True, 0, b'last fragment'),
                                  (True, 2, b'x' * 300), (True, 2, b'y' * 70000),
                                  (True, 9, b'ping'), (True, 8, struct.pack('!H', 1000))]:
            sock.sendall(frame(data, opcode, fin, masked=True))
            self.assertEqual(receive_frame(reader), (fin, 10 if opcode == 9 else opcode, data))
        self.assertEqual(reader.read(1), b'')
        path, headers, _ = Backend.seen[0]
        self.assertEqual(path, '/v1/responses?model=gpt-test')
        self.assertEqual(headers['Authorization'], 'Bearer local-test-only')

    def test_websocket_auth_errors_complete_without_backend_eof(self):
        for query in ('?denied', '?denied&chunked'):
            with self.subTest(query=query):
                _, reader, status, headers = self.websocket(query)
                self.assertIn(b'401', status)
                self.assertEqual(headers['Connection'], 'close')
                self.assertEqual(reader.read(), b'{"error":{"type":"invalid_api_key"}}')
                self.assertFalse(Backend.release_auth.is_set())

    def test_maintenance_retains_live_websocket_and_rejects_new_requests(self):
        sock, reader, _, _ = self.websocket()
        receive_frame(reader)
        self.assertEqual(self.get_status()['active_requests'], 1)
        edge.ADMISSION.marker.write_text('{}')
        current = self.get_status()
        self.assertTrue(current['maintenance'])
        self.assertEqual(current['active_requests'], 1)
        before = len(Backend.seen)
        for path in ('/v1/images/generations', '/v1/responses'):
            conn = http.client.HTTPConnection('127.0.0.1', self.proxy.server_port, timeout=2)
            conn.request('POST', path, b'{"test":true}', {'Content-Type': 'application/json'})
            response = conn.getresponse()
            self.assertEqual(response.status, 503)
            self.assertEqual(response.getheader('Retry-After'), '30')
            self.assertEqual(json.loads(response.read())['error']['type'], 'maintenance')
            conn.close()
        self.assertEqual(len(Backend.seen), before)
        sock.sendall(frame(b'live', masked=True))
        self.assertEqual(receive_frame(reader), (True, 1, b'live'))
        sock.sendall(frame(struct.pack('!H', 1000), 8, masked=True))
        receive_frame(reader)
        self.assertEqual(reader.read(1), b'')
        self.assertEqual(self.get_status()['active_requests'], 0)
        edge.ADMISSION.marker.unlink()
        self.assertFalse(self.get_status()['maintenance'])

    def test_ambiguous_body_rejected_before_backend(self):
        sock = socket.create_connection(('127.0.0.1', self.proxy.server_port), timeout=2)
        self.sockets.append(sock)
        sock.sendall(b'POST /v1/responses HTTP/1.1\r\nHost: localhost\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n{}')
        result = http.client.HTTPResponse(sock)
        result.begin()
        self.assertEqual(result.status, 400)
        self.assertIn(b'invalid_request_error', result.read())
        self.assertEqual(Backend.seen, [])


if __name__ == '__main__':
    unittest.main()

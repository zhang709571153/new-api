"""Loopback Sub2API edge: transparent HTTP/SSE and Responses WebSocket.

Install beside release_control.py and bridge_observability.py. New API owns all
authentication, image handling and billing. The separate maintenance port and
existing release-maintenance.json admission marker retain the old bridge API.
No upstream credentials, request bodies or WebSocket frames are logged.
"""
import http.client
import io
import json
import os
import pathlib
import select
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import bridge_observability
from release_control import Admission

BACKEND_PORT = int(os.environ.get('NEWAPI_PORT', '18300'))
PORT = int(os.environ.get('BRIDGE_PORT', '18301'))
PRIVATE = pathlib.Path(os.environ.get('REALYU_PRIVATE_DIR', str(pathlib.Path(__file__).resolve().parent / '.private')))
ADMISSION = Admission(PRIVATE / 'release-maintenance.json')
MAX_BODY = 128 * 1024 * 1024
MAX_HEADERS = 64 * 1024
HOP = {'connection', 'keep-alive', 'proxy-authenticate', 'proxy-authorization',
       'te', 'trailer', 'transfer-encoding', 'upgrade', 'expect'}


def read_exact(stream, size):
    chunks = []
    while size:
        block = stream.read(min(size, 65536))
        if not block:
            raise ValueError('Incomplete request body')
        chunks.append(block)
        size -= len(block)
    return b''.join(chunks)


class BridgeHTTPServer(ThreadingHTTPServer):
    request_queue_size = 128
    daemon_threads = True


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    # Avoid swallowing a client frame pipelined after the upgrade headers.
    rbufsize = 0

    def setup(self):
        super().setup()
        self.connection.settimeout(300)

    def log_message(self, *_):
        pass

    def send_response(self, code, message=None):
        if getattr(self, 'observation', None):
            self.observation.http_status = code
        super().send_response(code, message)

    def end_headers(self):
        super().end_headers()
        if getattr(self, 'observation', None):
            self.observation.headers_sent = True
            self.observation.event('downstream_headers')

    def send_json(self, status, payload):
        self.close_connection = True
        raw = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('Connection', 'close')
        if status == 503:
            self.send_header('Retry-After', '30')
        self.end_headers()
        if self.command != 'HEAD':
            self.wfile.write(raw)
            self.observation.written(len(raw))

    def body(self):
        lengths = self.headers.get_all('Content-Length', [])
        transfers = self.headers.get_all('Transfer-Encoding', [])
        if len(lengths) > 1 or len(transfers) > 1 or lengths and transfers:
            raise ValueError('Ambiguous request framing')
        if transfers:
            if transfers[0].lower().strip() != 'chunked':
                raise ValueError('Unsupported request transfer encoding')
            chunks, total = [], 0
            while True:
                line = self.rfile.readline(8193)
                if len(line) > 8192 or not line.endswith(b'\r\n'):
                    raise ValueError('Invalid chunk header')
                try:
                    size = int(line[:-2].split(b';', 1)[0], 16)
                except ValueError:
                    raise ValueError('Invalid chunk size') from None
                if size < 0 or total + size > MAX_BODY:
                    raise ValueError('Request body exceeds limit')
                if size == 0:
                    trailer_size = 0
                    while True:
                        trailer = self.rfile.readline(8193)
                        trailer_size += len(trailer)
                        if not trailer.endswith(b'\r\n') or trailer_size > MAX_HEADERS:
                            raise ValueError('Invalid request trailers')
                        if trailer == b'\r\n':
                            return b''.join(chunks)
                chunks.append(read_exact(self.rfile, size))
                total += size
                if read_exact(self.rfile, 2) != b'\r\n':
                    raise ValueError('Invalid chunk terminator')
        text = lengths[0] if lengths else '0'
        if not text.isdecimal() or int(text) > MAX_BODY:
            raise ValueError('Invalid request body length')
        return read_exact(self.rfile, int(text))

    def forward(self):
        self.observation = bridge_observability.RequestObservation(self)
        admitted = False
        try:
            if not ADMISSION.enter():
                self.observation.end_reason = 'maintenance_rejected'
                self.send_json(503, {'error': {'type': 'maintenance', 'message': 'Service updating. Please retry shortly.'}})
                return
            admitted = True
            if not self.path.startswith('/') or any('\r' in v or '\n' in v for _, v in self.headers.items()):
                raise ValueError('Invalid request headers')
            upgrades = {v.strip().lower() for header in self.headers.get_all('Upgrade', []) for v in header.split(',')}
            if 'websocket' in upgrades:
                if self.command != 'GET' or self.path.split('?', 1)[0].rstrip('/') != '/v1/responses':
                    self.send_json(400, {'error': {'type': 'invalid_request_error', 'message': 'Unsupported WebSocket route'}})
                    return
                self.websocket()
            else:
                self.http()
        except (ValueError, TypeError):
            self.observation.end_reason = 'invalid_request'
            if not self.observation.headers_sent:
                self.send_json(400, {'error': {'type': 'invalid_request_error', 'message': 'Invalid request framing'}})
        except Exception as error:
            self.observation.error(error)
            if not self.observation.headers_sent:
                try:
                    self.send_json(502, {'error': {'type': 'edge_proxy_error', 'message': 'Local upstream request failed'}})
                except (OSError, ValueError):
                    pass
        finally:
            self.close_connection = True
            if admitted:
                ADMISSION.leave()
            self.observation.event('end')

    def http(self):
        body = self.body()
        hop = HOP | {v.strip().lower() for h in self.headers.get_all('Connection', []) for v in h.split(',')}
        headers = {k: v for k, v in self.headers.items() if k.lower() not in hop and k.lower() != 'content-length'}
        headers['Content-Length'] = str(len(body))
        conn = http.client.HTTPConnection('127.0.0.1', BACKEND_PORT, timeout=300)
        try:
            self.observation.begin_attempt()
            conn.request(self.command, self.path, body, headers)
            resp = conn.getresponse()
            self.observation.upstream_headers(resp)
            self.send_response(resp.status)
            response_hop = HOP | {v.strip().lower() for v in resp.getheader('Connection', '').split(',')}
            for key, value in resp.getheaders():
                if key.lower() not in response_hop:
                    self.send_header(key, value)
            self.send_header('Connection', 'close')
            self.end_headers()
            if self.command != 'HEAD':
                while True:
                    self.observation.operation = 'read_upstream'
                    chunk = resp.read1(65536)
                    if not chunk:
                        if resp.length is not None and resp.length > 0:
                            raise http.client.IncompleteRead(b'', resp.length)
                        break
                    self.observation.read(len(chunk))
                    self.observation.operation = 'write_client'
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    self.observation.written(len(chunk))
        finally:
            conn.close()

    def websocket(self):
        lengths = self.headers.get_all('Content-Length', [])
        if self.headers.get_all('Transfer-Encoding') or len(lengths) > 1 or lengths and lengths[0] != '0':
            raise ValueError('WebSocket upgrade cannot include a body')
        self.observation.begin_attempt()
        with socket.create_connection(('127.0.0.1', BACKEND_PORT), timeout=300) as upstream:
            # Preserve the authentication and negotiated WebSocket headers. The
            # gateway, not this transport, authorizes the caller and each turn.
            headers = [(k, v) for k, v in self.headers.items() if k.lower() not in HOP]
            headers += [('Connection', 'Upgrade'), ('Upgrade', 'websocket')]
            wire = f'GET {self.path} HTTP/1.1\r\n' + ''.join(f'{k}: {v}\r\n' for k, v in headers) + '\r\n'
            upstream.sendall(wire.encode('iso-8859-1'))
            with upstream.makefile('rb', buffering=0) as reader:
                status_line = reader.readline(8193)
                parts = status_line.rstrip(b'\r\n').split(b' ', 2)
                if len(status_line) > 8192 or len(parts) < 2 or not parts[0].startswith(b'HTTP/') or not parts[1].isdigit():
                    raise OSError('Invalid upstream upgrade status')
                status = int(parts[1])
                raw_headers, size = [], len(status_line)
                while True:
                    line = reader.readline(8193)
                    size += len(line)
                    if not line.endswith(b'\r\n') or size > MAX_HEADERS:
                        raise OSError('Invalid upstream upgrade headers')
                    raw_headers.append(line)
                    if line == b'\r\n':
                        break
                self.observation.http_status = status
                parsed = http.client.parse_headers(io.BytesIO(b''.join(raw_headers)))
                self.observation.native_request_id = self.observation.clean(parsed.get('X-Oneapi-Request-Id', ''), r'[a-zA-Z0-9_-]{1,128}')
                self.observation.event('upstream_headers')
                if status != 101:
                    self.websocket_error(status, parsed, reader)
                    return
                self.wfile.write(status_line + b''.join(raw_headers))
                self.wfile.flush()
                self.observation.headers_sent = True
                self.observation.event('downstream_headers')
                sockets = [self.connection, upstream]
                while True:
                    ready, _, _ = select.select(sockets, [], [], 300)
                    if not ready:
                        raise TimeoutError('Upgraded connection idle timeout')
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        target = upstream if source is self.connection else self.connection
                        target.sendall(data)
                        if source is upstream:
                            self.observation.read(len(data))
                            self.observation.written(len(data))

    def websocket_error(self, status, headers, reader):
        """Finish framed auth errors without waiting for a keep-alive socket EOF."""
        lengths = headers.get_all('Content-Length', [])
        transfers = headers.get_all('Transfer-Encoding', [])
        if len(lengths) > 1 or len(transfers) > 1 or lengths and transfers:
            raise OSError('Ambiguous upstream error framing')
        chunked = bool(transfers)
        if chunked and transfers[0].lower().strip() != 'chunked':
            raise OSError('Unsupported upstream error framing')
        if lengths and (not lengths[0].isdecimal() or int(lengths[0]) > MAX_BODY):
            raise OSError('Invalid upstream error length')
        self.send_response(status)
        hop = HOP | {v.strip().lower() for h in headers.get_all('Connection', []) for v in h.split(',')}
        for key, value in headers.items():
            if key.lower() not in hop:
                self.send_header(key, value)
        self.send_header('Connection', 'close')
        self.end_headers()

        def write(data):
            self.observation.read(len(data))
            self.wfile.write(data)
            self.wfile.flush()
            self.observation.written(len(data))

        if status in (204, 304) or status < 200:
            return
        remaining = int(lengths[0]) if lengths else None
        if chunked:
            total = 0
            while True:
                line = reader.readline(8193)
                if len(line) > 8192 or not line.endswith(b'\r\n'):
                    raise OSError('Invalid upstream chunk')
                size = int(line[:-2].split(b';', 1)[0], 16)
                total += size
                if size < 0 or total > MAX_BODY:
                    raise OSError('Upstream error exceeds limit')
                if not size:
                    return
                write(read_exact(reader, size))
                if read_exact(reader, 2) != b'\r\n':
                    raise OSError('Invalid upstream chunk ending')
        while remaining is None or remaining > 0:
            data = reader.read(min(65536, remaining) if remaining is not None else 65536)
            if not data:
                if remaining:
                    raise OSError('Incomplete upstream error')
                return
            write(data)
            if remaining is not None:
                remaining -= len(data)

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = forward


class MaintenanceStatusHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        raw = json.dumps({**ADMISSION.status(), 'observation': bridge_observability.status()}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)


if __name__ == '__main__':
    if os.environ.get('REALYU_UPSTREAM_DRIVER', '').lower() != 'sub2api':
        raise SystemExit('This edge proxy requires REALYU_UPSTREAM_DRIVER=sub2api')
    PRIVATE.mkdir(parents=True, exist_ok=True)
    bridge_observability.configure(PRIVATE)
    status_server = BridgeHTTPServer(('127.0.0.1', int(os.environ.get('REALYU_MAINTENANCE_PORT', str(PORT + 1)))), MaintenanceStatusHandler)
    threading.Thread(target=status_server.serve_forever, daemon=True).start()
    print(f'Sub2API edge 127.0.0.1:{PORT} -> New API 127.0.0.1:{BACKEND_PORT}', flush=True)
    BridgeHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()

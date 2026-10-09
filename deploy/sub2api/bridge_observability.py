"""Bounded metadata-only bridge observations. No request or response contents."""
import datetime as dt
import json
import logging
from logging.handlers import RotatingFileHandler
import re
import time
import uuid

_write_errors = 0


class MetadataHandler(RotatingFileHandler):
    def handleError(self, record):
        global _write_errors
        _write_errors += 1


def configure(private):
    logger = logging.getLogger('realyu.bridge.observations')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        handler = MetadataHandler(private / 'bridge-observation.jsonl', maxBytes=10 * 1024 ** 2,
                                  backupCount=5, encoding='utf-8', delay=True)
        handler.setFormatter(logging.Formatter('%(message)s'))
        logger.addHandler(handler)


def status():
    return {'write_errors': _write_errors, 'schema': 1}


class RequestObservation:
    def __init__(self, handler):
        self.started = time.monotonic()
        self.bridge_id = uuid.uuid4().hex
        self.probe_id = self.clean(handler.headers.get('X-Realyu-Probe-Id', ''), r'[a-fA-F0-9]{32}')
        self.cf_ray = self.clean(handler.headers.get('CF-Ray', ''), r'[a-fA-F0-9]{16,32}(?:-[A-Z]{3})?')
        path = handler.path.split('?', 1)[0].rstrip('/') or '/'
        known = {'/', '/api/status', '/v1/models', '/v1/responses', '/v1/chat/completions',
                 '/v1/images/generations', '/v1/images/edits'}
        self.route = path if path in known else '/other'
        self.method = handler.command
        self.operation = 'read_client_request'
        self.native_request_id = None
        self.attempt = 0
        self.http_status = None
        self.headers_sent = False
        self.bytes_read = self.bytes_written = 0
        self.first_read = self.first_write = False
        self.error_class = None
        self.failure_operation = None
        self.end_reason = 'transport_finished'
        self.event('arrived')

    @staticmethod
    def clean(value, pattern):
        return value if isinstance(value, str) and len(value) <= 128 and re.fullmatch(pattern, value) else None

    def event(self, phase):
        global _write_errors
        try:
            logging.getLogger('realyu.bridge.observations').info(json.dumps({
                'schema': 1, 'timestamp': dt.datetime.now(dt.timezone.utc).isoformat(),
                'bridge_id': self.bridge_id, 'probe_id': self.probe_id, 'cf_ray': self.cf_ray,
                'native_request_id': self.native_request_id, 'attempt': self.attempt,
                'method': self.method, 'route': self.route, 'phase': phase,
                'elapsed_ms': round((time.monotonic() - self.started) * 1000, 3),
                'http_status': self.http_status, 'operation': self.operation,
                'bytes_read': self.bytes_read, 'bytes_written': self.bytes_written,
                'error_class': self.error_class, 'end_reason': self.end_reason if phase == 'end' else None,
                'failure_operation': self.failure_operation,
                'scope': 'bridge transport metadata; no proof of model completion or client receipt',
            }, ensure_ascii=True))
        except Exception:
            _write_errors += 1

    def begin_attempt(self):
        self.attempt += 1
        self.native_request_id = None
        self.first_read = False
        self.operation = 'request_upstream'

    def upstream_headers(self, response):
        self.http_status = response.status
        self.native_request_id = self.clean(response.getheader('X-Oneapi-Request-Id', ''), r'[a-zA-Z0-9_-]{1,128}')
        self.event('upstream_headers')

    def read(self, size):
        self.bytes_read += size
        if size and not self.first_read:
            self.first_read = True
            self.event('upstream_first_body')

    def written(self, size):
        self.bytes_written += size
        if size and not self.first_write:
            self.first_write = True
            self.event('downstream_first_body')

    def error(self, error):
        self.error_class = type(error).__name__
        self.failure_operation = self.operation
        self.end_reason = 'transport_error'

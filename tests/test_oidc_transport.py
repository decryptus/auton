"""Real local transport regressions; no external identities or credentials."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
import time
import unittest
from unittest.mock import patch

from auton.classes import oidc_transport as transport
from httpdis.authentication import AuthenticationDenied, AuthenticationUnavailable


@contextmanager
def endpoint(mode):
    attempts = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            attempts.append(self.path)
            self.rfile.read(int(self.headers['Content-Length']))
            try:
                if mode == 'headers':
                    time.sleep(2)
                self.send_response(302 if mode == 'redirect' else 401 if mode == 'denied' else 200)
                if mode == 'redirect':
                    self.send_header('Location', '/must-not-follow')
                body = json.dumps({'id_token': 'synthetic-token'}).encode()
                if mode == 'oversize':
                    body = b'x' * (transport.MAX_DOCUMENT_BYTES + 1)
                elif mode == 'malformed':
                    body = b'not-json'
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                if mode == 'body':
                    for value in body:
                        self.wfile.write(bytes([value]))
                        self.wfile.flush()
                        time.sleep(.08)
                else:
                    self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield 'http://127.0.0.1:%s/token' % server.server_port, attempts
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


class OIDCTransportTests(unittest.TestCase):
    def exchange(self, uri):
        return transport.OIDCTokenExchange(uri, 'synthetic-client', 'synthetic-secret',
                                           'https://synthetic.example/callback')('code', 'verifier')

    def test_real_success(self):
        with endpoint('success') as (uri, attempts):
            self.assertEqual(self.exchange(uri), 'synthetic-token')
            self.assertEqual(attempts, ['/token'])

    def test_slow_body_is_bounded_without_retry(self):
        with endpoint('body') as (uri, attempts), patch.object(transport, 'OIDC_TIMEOUT', 1):
            started = time.monotonic()
            with self.assertRaises(AuthenticationUnavailable):
                self.exchange(uri)
            self.assertLess(time.monotonic() - started, 1.8)
            self.assertEqual(attempts, ['/token'])

    def test_slow_headers_are_bounded_without_retry(self):
        with endpoint('headers') as (uri, attempts), patch.object(transport, 'OIDC_TIMEOUT', 1):
            started = time.monotonic()
            with self.assertRaises(AuthenticationUnavailable):
                self.exchange(uri)
            self.assertLess(time.monotonic() - started, 1.8)
            self.assertEqual(attempts, ['/token'])

    def test_redirect_and_refusal_are_not_retried(self):
        for mode in ('redirect', 'denied'):
            with self.subTest(mode=mode), endpoint(mode) as (uri, attempts):
                with self.assertRaises(AuthenticationDenied):
                    self.exchange(uri)
                self.assertEqual(attempts, ['/token'])

    def test_oversize_and_invalid_json_are_unavailable(self):
        for mode in ('oversize', 'malformed'):
            with self.subTest(mode=mode), endpoint(mode) as (uri, attempts):
                with self.assertRaises(AuthenticationUnavailable):
                    self.exchange(uri)
                self.assertEqual(attempts, ['/token'])

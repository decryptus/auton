"""Exercise header and body deadlines on a real daemon with two workers."""
from http.client import HTTPConnection
import socket
import ssl
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from unittest.mock import patch
from urllib.parse import urlsplit

import requests
import yaml

from auton.classes.http import BODY_GRACE_SECONDS, HEADER_READ_SECONDS
from tls_fixture import certificates
from web_fixture import WebDaemon


class HeaderDeadlineTests(unittest.TestCase):
    def check_transport(self, tls, body=False):
        with ExitStack() as stack:
            daemon = WebDaemon()
            stack.callback(daemon.close)
            config_path = daemon.path / 'auton.yml'
            config = yaml.safe_load(config_path.read_text())
            config['general']['max_workers'] = 2
            transport = {}
            if tls:
                daemon.uri = daemon.uri.replace('http:', 'https:')
                transport = certificates(daemon.path)
                config['general'].update(web_origin=daemon.uri, tls={
                    'certificate': str(daemon.path / 'server.pem'),
                    'private_key': str(daemon.path / 'server.key'), 'client_ca': transport['verify']})
                context = ssl.create_default_context(cafile=transport['verify'])
                context.load_cert_chain(*transport['cert'])
            config_path.write_text(yaml.safe_dump(config))
            get = requests.get
            with patch('web_fixture.requests.get', side_effect=lambda *a, **kw: get(*a, **kw, **transport)):
                daemon.start()

            def health():
                with get(daemon.uri + '/health', headers={'Authorization': 'Bearer ' + daemon.token},
                         timeout=HEADER_READ_SECONDS + 5, **transport) as response:
                    return response.status_code

            self.assertEqual(health(), 200)
            clients = []
            prefixes = (b'GET /hea', b'GET /health HTTP/1.1\r\nHost: localhost\r\nX-Test: ')
            budget = HEADER_READ_SECONDS
            if body:
                headers = ('POST /ui/auth/login HTTP/1.1\r\nHost: ' + urlsplit(daemon.uri).netloc +
                           '\r\nOrigin: ' + daemon.uri +
                           '\r\nContent-Type: application/json\r\nContent-Length: 4096\r\n\r\n{').encode()
                prefixes = (headers, headers)
                budget = BODY_GRACE_SECONDS + 1
            for prefix in prefixes:
                sock = socket.create_connection(('127.0.0.1', urlsplit(daemon.uri).port), timeout=2)
                if tls:
                    sock = context.wrap_socket(sock, server_hostname='localhost')
                stack.callback(sock.close)
                sock.sendall(prefix)
                clients.append(sock)
            with ThreadPoolExecutor(1) as pool:
                pending = pool.submit(health)
                start = time.monotonic()
                time.sleep(0.3)
                self.assertFalse(pending.done(), 'both workers should be occupied')
                while not pending.done() and time.monotonic() - start < budget + 3:
                    for sock in clients:
                        try:
                            sock.sendall(b'x')
                        except OSError:
                            pass  # The server may already have closed this connection.
                    time.sleep(0.2)
                self.assertTrue(pending.done(), 'trickling bytes extended the absolute deadline')
                self.assertEqual(pending.result(), 200)
            self.assertEqual(health(), 200)
            if body:
                for sock in clients:
                    self.assertIn(b' 408 ', sock.recv(256).split(b'\r\n', 1)[0])

    def test_http_body(self):
        self.check_transport(False, body=True)

    def test_mtls_body(self):
        self.check_transport(True, body=True)

    def test_http(self):
        self.check_transport(False)

    def test_native_mtls(self):
        self.check_transport(True)

    def test_larger_valid_body_can_outlast_the_initial_grace_period(self):
        daemon = WebDaemon()
        self.addCleanup(daemon.close)
        daemon.start()
        conn = HTTPConnection('127.0.0.1', urlsplit(daemon.uri).port, timeout=5)
        self.addCleanup(conn.close)
        body = b'{"args":["-c","pass"]}'.ljust(256 * 1024)
        conn.putrequest('POST', '/run/diagnostic/upload-test')
        conn.putheader('Authorization', 'Bearer ' + daemon.token)
        conn.putheader('Content-Type', 'application/json')
        conn.putheader('Content-Length', str(len(body)))
        conn.endheaders()
        conn.send(body[:1024])
        time.sleep(BODY_GRACE_SECONDS + 1)
        conn.send(body[1024:])
        self.assertEqual(conn.getresponse().status, 200)

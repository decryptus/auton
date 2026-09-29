"""Browser authentication on the real daemon with the existing job services."""
import copy
import http.client
import time
import unittest
from urllib.parse import urlsplit
import requests
from auton.classes.web import configure_web, WEB_ROUTES, SECURITY_HEADERS
from auton.classes.exceptions import AutonConfigurationError
from web_fixture import WebDaemon, FIXTURE_PASSWORD


class WebConfigurationTests(unittest.TestCase):
    def test_opt_in_explicit_origin_backend_and_reserved_routes(self):
        conf = {'general': {}, 'modules': {}}
        configure_web(conf)
        self.assertEqual(conf['modules'], {})
        base = {'general': {'web_enabled': True, 'web_origin': 'https://daemon.example', 'auth_mode': 'required',
                            'authentication': {'backend': 'sqlite', 'path': '/private/auth.db'}},
                'modules': {'job': {'routes': {'run': {'handler': 'job_run', 'safe_init': True, 'auth': True}}}}}
        valid = copy.deepcopy(base)
        configure_web(valid)
        self.assertFalse(valid['modules']['job']['routes']['web_login']['auth'])
        self.assertTrue(valid['modules']['job']['routes']['run']['auth'])
        self.assertTrue(valid['modules']['job']['routes']['web_session']['auth'])
        for override in ({'web_enabled': 'yes'}, {'web_origin': 'http://remote.example'},
                         {'auth_mode': 'anonymous'}, {'authentication': None}):
            conf = copy.deepcopy(base)
            conf['general'].update(override)
            with self.assertRaises(AutonConfigurationError):
                configure_web(conf)
        conf = copy.deepcopy(base)
        conf['modules']['other'] = {'routes': {'overlap': {'regexp': '^ui/.*', 'handler': 'anything'}}}
        with self.assertRaises(AutonConfigurationError):
            configure_web(conf)


class WebHTTPTests(unittest.TestCase):
    def setUp(self):
        self.daemon = WebDaemon()
        self.addCleanup(self.daemon.close)
        self.daemon.start()
        self.uri = self.daemon.uri

    def test_public_assets_login_headers_rotation_logout_and_restart(self):
        for path in ('/ui', '/ui/', '/ui/app.js', '/ui/style.css', '/ui/logo.svg', '/missing'):
            response = requests.get(self.uri + path, timeout=2)
            self.assertEqual(response.status_code, 404 if path == '/missing' else 200)
            for name, value in SECURITY_HEADERS.items():
                self.assertEqual(response.headers[name], value)
            self.assertNotIn('Access-Control-Allow-Origin', response.headers)
        self.assertEqual(requests.get(self.uri + '/health', timeout=2).status_code, 401)
        browser, login = self.daemon.login()
        self.addCleanup(browser.close)
        self.assertEqual(login.json()['principal'], 'operator')
        cookie = login.headers['Set-Cookie']
        for flag in ('HttpOnly', 'SameSite=Strict', 'Path=/', 'Max-Age='):
            self.assertIn(flag, cookie)
        self.assertNotIn('secret', login.json())
        old = browser.cookies.get('autond-session')
        self.daemon.stop()
        self.daemon.start()
        self.assertEqual(browser.get(self.uri + '/ui/auth/session', timeout=2).status_code, 200)
        rotated = browser.post(self.uri + '/ui/auth/login', json={'principal': 'operator', 'password': FIXTURE_PASSWORD}, timeout=5)
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(browser.cookies.get('autond-session'), old)
        self.assertEqual(requests.get(self.uri + '/health', cookies={'autond-session': old}, timeout=2).status_code, 401)
        browser.headers['X-CSRF-Token'] = rotated.json()['csrf']
        response = browser.post(self.uri + '/ui/auth/logout', json={}, timeout=2)
        self.assertEqual(response.status_code, 200)
        self.assertIn('Max-Age=0', response.headers['Set-Cookie'])
        self.assertEqual(browser.get(self.uri + '/jobs', timeout=2).status_code, 401)

    def test_origin_csrf_body_limits_duplicates_cors_and_bearer_compatibility(self):
        payload = {'principal': 'operator', 'password': FIXTURE_PASSWORD}
        for headers in ({}, {'Origin': 'null'}, {'Origin': 'https://foreign.example'},
                        {'Origin': self.uri, 'Sec-Fetch-Site': 'same-site'},
                        {'Origin': self.uri, 'Host': 'foreign.example'}):
            response = requests.post(self.uri + '/ui/auth/login', json=payload, headers=headers, timeout=2)
            self.assertEqual(response.status_code, 403)
        response = requests.post(self.uri + '/ui/auth/login', data=payload, headers={'Origin': self.uri}, timeout=2)
        self.assertEqual(response.status_code, 403)
        response = requests.post(self.uri + '/ui/auth/login', json={'principal': 'x' * 10000, 'password': ''}, headers={'Origin': self.uri}, timeout=2)
        self.assertEqual(response.status_code, 413)
        port = urlsplit(self.uri).port
        conn = http.client.HTTPConnection('127.0.0.1', port, timeout=2)
        self.addCleanup(conn.close)
        conn.putrequest('POST', '/ui/auth/login')
        conn.putheader('Origin', self.uri)
        conn.putheader('Origin', self.uri)
        conn.putheader('Content-Type', 'application/json')
        conn.putheader('Content-Length', '2')
        conn.endheaders(b'{}')
        self.assertEqual(conn.getresponse().status, 403)
        response = requests.options(self.uri + '/run/diagnostic/test-job', headers={'Origin': 'https://foreign.example'}, timeout=2)
        self.assertEqual(response.status_code, 405)
        self.assertNotIn('Access-Control-Allow-Origin', response.headers)
        browser, _ = self.daemon.login()
        self.addCleanup(browser.close)
        csrf = browser.headers.pop('X-CSRF-Token')
        self.assertEqual(browser.post(self.uri + '/maintenance', json={'enabled': True}, timeout=2).status_code, 401)
        self.assertEqual(browser.post(self.uri + '/ui/auth/logout', json={}, timeout=2).status_code, 401)
        browser.headers['X-CSRF-Token'] = csrf
        self.assertEqual(browser.post(self.uri + '/maintenance', json={'enabled': True}, timeout=2).status_code, 200)
        response = requests.get(self.uri + '/health', headers={'Authorization': 'Bearer ' + self.daemon.token}, timeout=2)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['maintenance']['enabled'])

    def test_server_side_scopes_acl_owner_revocation_and_safe_output(self):
        operator, _ = self.daemon.login()
        reader, _ = self.daemon.login('reader')
        other, _ = self.daemon.login('other')
        for browser in (operator, reader, other):
            self.addCleanup(browser.close)
        catalogue = operator.get(self.uri + '/endpoints', timeout=2).json()['endpoints']
        self.assertEqual(next(item for item in catalogue if item['name'] == 'diagnostic'),
                         {'name': 'diagnostic', 'description': '<script>discovery()</script> Diagnostic checks'})
        self.assertNotIn('private', [item['name'] for item in catalogue])
        jobpath = '/run/diagnostic/browser-job'
        payload = {'args': ['-c', 'print("<script>window.pwned = true</script>")']}
        self.assertEqual(reader.post(self.uri + jobpath, json=payload, timeout=2).status_code, 403)
        self.assertEqual(other.post(self.uri + '/maintenance', json={'enabled': True}, timeout=2).status_code, 403)
        self.assertEqual(operator.post(self.uri + '/run/private/private-job', json=payload, timeout=2).status_code, 403)
        self.assertEqual(operator.post(self.uri + jobpath, json=payload, timeout=2).status_code, 200)
        for _ in range(100):
            result = operator.get(self.uri + '/jobs/diagnostic/browser-job', timeout=2).json()
            if result['status'] == 'complete':
                break
            time.sleep(0.02)
        self.assertEqual(result['stream'], ['<script>window.pwned = true</script>\n'])
        self.assertEqual(other.get(self.uri + '/jobs/diagnostic/browser-job', timeout=2).status_code, 403)
        self.assertEqual(reader.get(self.uri + '/jobs', timeout=2).json()['jobs'], [])
        self.daemon.auth.disable('operator')
        self.assertEqual(operator.get(self.uri + '/health', timeout=2).status_code, 401)

    def test_failed_login_rate_limit_and_storage_failure_do_not_fallback(self):
        for _ in range(5):
            response = requests.post(self.uri + '/ui/auth/login', headers={'Origin': self.uri},
                json={'principal': 'operator', 'password': 'wrong'}, timeout=3)
            self.assertEqual(response.status_code, 401)
        response = requests.post(self.uri + '/ui/auth/login', headers={'Origin': self.uri},
            json={'principal': 'operator', 'password': FIXTURE_PASSWORD}, timeout=3)
        self.assertEqual(response.status_code, 401)
        (self.daemon.path / 'auth.db').chmod(0o644)
        response = requests.post(self.uri + '/ui/auth/login', headers={'Origin': self.uri},
            json={'principal': 'reader', 'password': FIXTURE_PASSWORD}, timeout=3)
        self.assertEqual(response.status_code, 503)
        self.assertNotIn(str(self.daemon.path), response.text)

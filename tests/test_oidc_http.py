"""Actual HTTPS callback, provider exchange and browser-cookie authorization."""
import os
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit
import requests
import yaml
from oidc_fixture import IdentityProvider
from tls_fixture import certificates
from web_fixture import WebDaemon


class OIDCHTTPTests(unittest.TestCase):
    def test_real_code_flow_cookie_csrf_replay_and_account_disable(self):
        daemon = WebDaemon()
        self.addCleanup(daemon.close)
        transport = certificates(daemon.path)
        provider = IdentityProvider(daemon.path)
        self.addCleanup(provider.close)
        daemon.uri = daemon.uri.replace('http:', 'https:')
        path = daemon.path / 'auton.yml'
        conf = yaml.safe_load(path.read_text())
        conf['general'].update(web_origin=daemon.uri,
            tls=dict(certificate=str(daemon.path / 'server.pem'), private_key=str(daemon.path / 'server.key'), client_ca=transport['verify']),
            oidc=dict(issuer=provider.uri, authorization_endpoint=provider.uri + '/authorize', token_endpoint=provider.uri + '/token',
                      client_id='auton-integration', jwks_file=str(provider.jwks), subjects={'subject-1': 'reader'}))
        path.write_text(yaml.safe_dump(conf))
        original_get = requests.get
        with patch.dict(os.environ, REQUESTS_CA_BUNDLE=transport['verify']):
            with patch('web_fixture.requests.get', side_effect=lambda *a, **kw: original_get(*a, **kw, **transport)):
                daemon.start()
        browser = requests.Session()
        browser.trust_env = False
        browser.verify, browser.cert = transport['verify'], transport['cert']
        self.addCleanup(browser.close)
        self.assertTrue(browser.get(daemon.uri + '/ui/auth/options', timeout=2).json()['oidc'])
        denied = browser.post(daemon.uri + '/ui/auth/oidc', json={}, timeout=2)
        self.assertEqual(denied.status_code, 403)
        started = browser.post(daemon.uri + '/ui/auth/oidc', json={}, headers={'Origin': daemon.uri}, timeout=2)
        self.assertEqual(started.status_code, 200, started.text)
        flow = {k: v[0] for k, v in parse_qs(urlsplit(started.json()['authorization_url']).query).items()}
        provider.flow = flow
        callback = daemon.uri + '/oidc/callback'
        params = {'code': 'test-code', 'state': flow['state'], 'iss': provider.uri}
        forged = requests.get(callback, params=params, timeout=2, **transport)
        self.assertEqual(forged.status_code, 401)
        self.assertEqual(provider.calls, 0)
        response = browser.get(callback, params=params, allow_redirects=False, timeout=5)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn('content="0;url=/ui/"', response.text)
        self.assertIn('HttpOnly', response.headers['Set-Cookie'])
        session = browser.get(daemon.uri + '/ui/auth/session', timeout=2)
        self.assertEqual(session.status_code, 200, session.text)
        self.assertEqual(session.json()['scopes'], ['read'])
        self.assertEqual(browser.get(daemon.uri + '/jobs', timeout=2).status_code, 200)
        self.assertEqual(browser.get(callback, params=params, allow_redirects=False, timeout=2).status_code, 401)
        self.assertEqual(provider.calls, 1)
        mutation = dict(Origin=daemon.uri, **{'X-CSRF-Token': session.json()['csrf']})
        refused = browser.post(daemon.uri + '/run/diagnostic/oidc-denied', json={}, headers=mutation, timeout=2)
        self.assertEqual(refused.status_code, 403)
        self.assertEqual(browser.post(daemon.uri + '/ui/auth/logout', json={}, headers={'Origin': daemon.uri}, timeout=2).status_code, 401)
        daemon.auth.disable('reader')
        self.assertEqual(browser.get(daemon.uri + '/jobs', timeout=2).status_code, 401)

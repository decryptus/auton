"""OIDC signature, flow binding, account policy, expiry and failure acceptance."""
import json
from pathlib import Path
import tempfile
import subprocess
import sys
import time
import unittest
from urllib.parse import parse_qs, urlsplit
from unittest.mock import Mock
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from httpdis.authentication import AuthenticationDenied, AuthenticationUnavailable
from auton.classes.oidc import OIDCService, oidc_config
from auton.classes.exceptions import AutonConfigurationError


class OIDCTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid='fixture', alg='RS256', use='sig')
        (self.path / 'keys.json').write_text(json.dumps({'keys': [jwk]}))
        self.settings = dict(issuer='https://issuer.example', authorization_endpoint='https://issuer.example/authorize',
                             token_endpoint='https://issuer.example/token', client_id='auton-test',
                             jwks_file=str(self.path / 'keys.json'), subjects={'subject-1': 'reader'})
        self.local = Mock(session_ttl=28800)
        self.lookup = Mock(return_value=(1, frozenset(['read'])))
        self.exchange = Mock()
        self.service = OIDCService(self.settings, 'https://auton.example', self.local, self.lookup, exchange=self.exchange)
        self.addCleanup(self.service.close)

    def flow(self, **claims):
        url, binding = self.service.begin()
        query = {key: value[0] for key, value in parse_qs(urlsplit(url).query).items()}
        body = dict(iss=self.settings['issuer'], aud=self.settings['client_id'], sub='subject-1',
                    iat=int(time.time()), exp=int(time.time()) + 120, nonce=query['nonce'])
        body.update(claims)
        self.exchange.return_value = jwt.encode(body, self.key, algorithm='RS256', headers={'kid': 'fixture'})
        return query, binding

    def test_pkce_state_binding_single_use_and_authorization(self):
        query, binding = self.flow(roles=['admin'], scope='read run maintenance cancel')
        self.assertEqual(query['code_challenge_method'], 'S256')
        self.assertEqual(query['response_type'], 'code')
        with self.assertRaises(AuthenticationDenied):
            self.service.finish(query['state'], 'x' * 43, 'code')
        self.exchange.assert_not_called()
        grant = self.service.finish(query['state'], binding, 'code')
        self.assertEqual(grant.identity.scopes, frozenset(['read']))
        self.assertEqual(grant.identity.principal, 'reader')
        with self.assertRaises(AuthenticationDenied):
            self.service.finish(query['state'], binding, 'code')
        self.assertEqual(self.exchange.call_count, 1)
        with self.assertRaises(AuthenticationDenied):
            self.service.authenticate_session(grant.secret, 'wrong', True)
        self.assertEqual(self.service.authenticate_session(grant.secret, grant.csrf, True).principal, 'reader')
        self.lookup.return_value = (2, frozenset(['read']))
        with self.assertRaises(AuthenticationDenied):
            self.service.authenticate_session(grant.secret)

    def test_invalid_claims_and_unknown_subject_are_rejected(self):
        for claims in ({'iss': 'https://foreign.example'}, {'aud': 'other'}, {'sub': 'unmapped'},
                       {'nonce': 'wrong'}, {'exp': 1}, {'iat': int(time.time()) + 300}, {'azp': 'other'},
                       {'aud': ['auton-test', 'other']}, {'iat': int(time.time()) - 600}):
            with self.subTest(claims=claims):
                query, binding = self.flow(**claims)
                with self.assertRaises(AuthenticationDenied):
                    self.service.finish(query['state'], binding, 'code')
        self.assertEqual(self.service.sessions, {})

    def test_unsigned_wrong_key_and_wrong_algorithm_are_rejected(self):
        for mode in ('none', 'HS256', 'wrong-key'):
            query, binding = self.flow()
            body = dict(iss=self.settings['issuer'], aud='auton-test', sub='subject-1', iat=int(time.time()),
                        exp=int(time.time()) + 120, nonce=query['nonce'])
            key = None if mode == 'none' else 'untrusted-secret-long-enough-for-test' if mode == 'HS256' else rsa.generate_private_key(public_exponent=65537, key_size=2048)
            self.exchange.return_value = jwt.encode(body, key, algorithm=mode if mode != 'wrong-key' else 'RS256', headers={'kid': 'fixture'})
            with self.assertRaises(AuthenticationDenied):
                self.service.finish(query['state'], binding, 'code')

    def test_failure_consumes_flow_and_never_retries(self):
        query, binding = self.flow()
        self.exchange.side_effect = AuthenticationUnavailable()
        with self.assertRaises(AuthenticationUnavailable):
            self.service.finish(query['state'], binding, 'code')
        with self.assertRaises(AuthenticationDenied):
            self.service.finish(query['state'], binding, 'code')
        self.assertEqual(self.exchange.call_count, 1)

    def test_account_disable_and_logout_are_effective(self):
        query, binding = self.flow()
        grant = self.service.finish(query['state'], binding, 'code')
        self.lookup.side_effect = AuthenticationDenied()
        with self.assertRaises(AuthenticationDenied):
            self.service.authenticate_session(grant.secret)
        self.service.logout(grant.secret)
        self.assertEqual(self.service.sessions, {})
        self.local.logout.assert_called_once_with(grant.secret)

    def test_service_runs_without_http_cli_or_outbound_adapter_imports(self):
        code = r'''
import importlib.abc, sys, json, tempfile, time
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('httpdis.httpdis', 'auton.classes.http', 'auton.classes.config', 'auton.classes.oidc_transport') or fullname.split('.')[0] in ('dwho', 'curses', 'requests', 'argparse'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from types import SimpleNamespace
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
from cryptography.hazmat.primitives.asymmetric import rsa
import jwt
from auton.classes.oidc import OIDCService
with tempfile.TemporaryDirectory() as directory:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk['kid'] = 'key'
    path = Path(directory) / 'keys.json'
    path.write_text(json.dumps({'keys': [jwk]}))
    settings = dict(issuer='https://idp.example', client_id='client', subjects={'subject': 'alice'},
                    authorization_endpoint='https://idp.example/authorize', jwks_file=str(path))
    tokens = []
    service = OIDCService(settings, 'https://auton.example', SimpleNamespace(session_ttl=3600),
                          lambda principal: (1, ['read']), exchange=lambda *args: tokens[0])
    url, binding = service.begin()
    query = parse_qs(urlsplit(url).query)
    tokens.append(jwt.encode(dict(iss=settings['issuer'], aud='client', sub='subject', iat=int(time.time()),
                                  exp=int(time.time())+60, nonce=query['nonce'][0]), key, algorithm='RS256', headers={'kid': 'key'}))
    grant = service.finish(query['state'][0], binding, 'code')
    assert service.authenticate_session(grant.secret, grant.csrf, True).principal == 'alice'
    service.close()
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_expired_flow_and_idle_session_cannot_authenticate(self):
        query, binding = self.flow()
        now = time.time()
        self.service.clock = lambda: now + 301
        with self.assertRaises(AuthenticationDenied):
            self.service.finish(query['state'], binding, 'code')
        self.exchange.assert_not_called()
        self.service.clock = time.time
        query, binding = self.flow(exp=int(time.time()) + 2000)
        grant = self.service.finish(query['state'], binding, 'code')
        self.local.authenticate_session.side_effect = AuthenticationDenied()
        self.service.clock = lambda: now + 901
        with self.assertRaises(AuthenticationDenied):
            self.service.authenticate_session(grant.secret)
        self.assertEqual(self.service.sessions, {})

    def test_configuration_rejects_insecure_or_unbounded_options(self):
        general = dict(web_enabled=True, web_origin='https://auton.example', auth_mode='required',
                       authentication={'backend': 'sqlite'}, oidc=self.settings)
        self.assertEqual(oidc_config(general)['issuer'], self.settings['issuer'])
        for change in ({'issuer': 'http://issuer.example'}, {'authorization_endpoint': 'https://user:pass@host/path'},
                       {'subjects': {}}, {'unknown': True}):
            with self.assertRaises(AutonConfigurationError):
                oidc_config(dict(general, oidc=dict(self.settings, **change)))
        with self.assertRaises(AutonConfigurationError):
            oidc_config(dict(general, web_origin='http://127.0.0.1'))

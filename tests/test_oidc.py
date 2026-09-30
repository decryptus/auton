"""OIDC signature, flow binding, account policy, expiry and failure acceptance."""
import json
from pathlib import Path
import tempfile
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

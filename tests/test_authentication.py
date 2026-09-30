"""Authentication policy and transport-independent credential verification."""
import base64
try:
    import crypt
except ImportError:
    import legacycrypt as crypt
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from auton.classes.authentication import PasswordAuthenticator, apply_auth_policy
from auton.classes.exceptions import AutonConfigurationError
from auton.classes.http import AutonHttpReqHandler
from httpdis.ext.httpdis_json import HttpReqErrJson


def basic(user, password):
    return 'Basic ' + base64.b64encode((user + ':' + password).encode()).decode()


class AuthenticationTests(unittest.TestCase):
    def test_required_policy_protects_new_routes_and_preserves_allowlists(self):
        routes = {'run': {'auth': False}, 'future': {}, 'admin': {'auth': ['alice']}}
        conf = {'general': {'auth_mode': 'required', 'auth_basic_file': '/credentials'},
                'modules': {'job': {'routes': routes}}}
        apply_auth_policy(conf)
        self.assertTrue(routes['run']['auth'])
        self.assertTrue(routes['future']['auth'])
        self.assertEqual(routes['admin']['auth'], ['alice'])
        conf['general'].pop('auth_basic_file')
        with self.assertRaises(AutonConfigurationError):
            apply_auth_policy(conf)

    def test_anonymous_is_explicitly_local_and_legacy_preserves_routes(self):
        for address in ('0.0.0.0', '::', 'localhost', '192.0.2.1', ''):
            with self.subTest(address=address), self.assertRaises(AutonConfigurationError):
                apply_auth_policy({'general': {'auth_mode': 'anonymous', 'listen_addr': address}})
        for address in ('127.0.0.1', '::1'):
            apply_auth_policy({'general': {'auth_mode': 'anonymous', 'listen_addr': address}})
        conf = {'general': {}, 'modules': {'job': {'routes': {'run': {'auth': False}}}}}
        with self.assertLogs('auton.classes.authentication', level='WARNING'):
            apply_auth_policy(conf)
        self.assertFalse(conf['modules']['job']['routes']['run']['auth'])
        for mode in (None, [], 'typo'):
            with self.assertRaises(AutonConfigurationError):
                apply_auth_policy({'general': {'auth_mode': mode}})

    def test_bcrypt_and_concurrent_identities(self):
        secret = crypt.crypt('correct', '$2b$12$abcdefghijklmnopqrstuu')
        self.assertTrue(secret.startswith('$2'), 'bcrypt is required on supported platforms')
        auth = PasswordAuthenticator({'alice': secret, 'bob': secret})
        attempts = [('alice', 'correct'), ('bob', 'correct'), ('alice', 'wrong'), ('unknown', 'correct')]
        with ThreadPoolExecutor(max_workers=4) as pool:
            result = list(pool.map(lambda pair: auth.authenticate(basic(*pair)), attempts))
        self.assertEqual(result, ['alice', 'bob', None, None])
        self.assertIsNone(auth.authenticate(basic('alice', 'correct'), ['bob']))
        with self.assertRaises(TypeError):
            auth.users['alice'] = 'changed'

    def test_legacy_sha_and_malformed_credentials_fail_closed(self):
        auth = PasswordAuthenticator({'alice': '{SHA}5en6G6MezRroT3XKqkdPOmY/BfQ='})
        self.assertEqual(auth.authenticate(basic('alice', 'secret')), 'alice')
        for header in ('', None, 'Bearer secret', 'Basic !!!', basic('alice', 'bad'),
                       basic('alice', 'secret\x00'), 'Basic ' + 'A' * 8192):
            self.assertIsNone(auth.authenticate(header))

    def test_bad_password_files_fail_without_secret_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'credentials'
            for value in ('alice:PRIVATE\nalice:OTHER', 'invalidPRIVATE', '', 'x' * 1048577):
                path.write_text(value)
                with self.assertRaises(AutonConfigurationError) as caught:
                    PasswordAuthenticator.from_file(path, required=True)
                self.assertNotIn('PRIVATE', str(caught.exception))
            path.write_text('alice:{SHA}5en6G6MezRroT3XKqkdPOmY/BfQ=\n')
            self.assertEqual(PasswordAuthenticator.from_file(path, required=True).authenticate(basic('alice', 'secret')), 'alice')

    def test_transport_clears_previous_identity_on_failure(self):
        handler = object.__new__(AutonHttpReqHandler)
        handler.authenticator = PasswordAuthenticator()
        handler._SERVER = {'HTTP_AUTH_USER': 'alice', 'HTTP_AUTH_PASSWD': 'secret'}
        handler.headers = {'Authorization': 'Basic invalid'}
        with self.assertRaises(HttpReqErrJson):
            handler.authenticate()
        self.assertEqual(handler._SERVER, {})

    def test_verification_does_not_import_interfaces(self):
        code = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('httpdis', 'dwho', 'curses', 'argparse'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from auton.classes.authentication import PasswordAuthenticator
assert PasswordAuthenticator().authenticate('Basic invalid') is None
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

"""Persistent authentication composition, local admin and real daemon authorization."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest import mock

import requests
import yaml

from auton.classes.auth_store import (PersistentAuthentication, authentication_config,
                                      load_authentication_config, authorized_principal)
from auton.classes.auth_cli import parser, run
from auton.classes.authentication import apply_auth_policy
from auton.classes.exceptions import AutonConfigurationError
from auton.classes.jobs import AccessDenied
from auton_client.credentials import BearerCredentials, credentials_from_options
from httpdis.authentication import Identity, AuthenticationDenied

ROOT = Path(__file__).resolve().parents[1]
PASSWORD = 'integration-only-password'


class PersistentAuthTests(unittest.TestCase):
    def test_config_is_explicit_and_relative_to_declaration(self):
        with tempfile.TemporaryDirectory() as tmp:
            general = {'auth_mode': 'required', 'authentication': {'backend': 'sqlite', 'path': 'auth.db'}}
            self.assertEqual(authentication_config(general, tmp)['path'], str(Path(tmp) / 'auth.db'))
            conf = {'general': general, '_config_directory': tmp,
                    'modules': {'job': {'routes': {'health': {'auth': False}}}}}
            apply_auth_policy(conf)
            self.assertTrue(conf['modules']['job']['routes']['health']['auth'])
            for settings in (None, {}, {'backend': 'redis', 'path': '/tmp/x'},
                             {'backend': 'sqlite', 'path': ':memory:'},
                             {'backend': 'sqlite', 'path': '/tmp/x', 'timeout': True},
                             {'backend': 'sqlite', 'path': '/tmp/x', 'unknown': 1}):
                with self.subTest(settings=settings), self.assertRaises(AutonConfigurationError):
                    authentication_config({'auth_mode': 'required', 'authentication': settings}, tmp)
            for override in ({'auth_mode': 'legacy'}, {'auth_mode': 'anonymous'},
                             {'auth_basic_file': '/tmp/passwd'}, {'auth_provider': 'anything'}):
                with self.assertRaises(AutonConfigurationError):
                    authentication_config(dict(general, **override), tmp)
            with self.assertRaises(AutonConfigurationError):
                authentication_config({'auth_mode': 'required', 'authentication': {'backend': 'sqlite', 'path': 'auth.db'}})

    def test_scope_policy_keeps_principal_and_action_independent(self):
        identity = Identity('alice', 'token', ['read'])
        self.assertEqual(authorized_principal('alice', identity, 'read'), 'alice')
        for principal, action in (('alice', 'run'), ('alice', 'maintenance'), ('bob', 'read')):
            with self.assertRaises(AccessDenied):
                authorized_principal(principal, identity, action)
        self.assertEqual(authorized_principal('legacy', None, 'run'), 'legacy')

    def test_local_admin_reuses_service_and_never_prints_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'auton.yml'
            config.write_text(yaml.safe_dump({'general': {'auth_mode': 'required',
                'authentication': {'backend': 'sqlite', 'path': 'auth.db'}}}))
            listing = subprocess.run([sys.executable, str(ROOT / 'bin/autond-auth'), '-c', str(config), 'user', 'list'],
                                     capture_output=True, text=True, timeout=10, env=dict(os.environ, PYTHONPATH=str(ROOT)))
            self.assertEqual(listing.returncode, 0, listing.stderr)
            self.assertEqual(json.loads(listing.stdout), {'accounts': []})
            settings = load_authentication_config(config)
            auth = PersistentAuthentication(settings)
            self.addCleanup(auth.close)
            user = parser().parse_args(['user', 'set', '-u', 'alice', '-s', 'read', '-s', 'run'])
            output = run(user, auth, lambda prompt: PASSWORD)
            self.assertNotIn(PASSWORD, repr(output))
            self.assertEqual(run(parser().parse_args(['user', 'list']), auth)['accounts'][0]['principal'], 'alice')
            token_path = Path(tmp) / 'token'
            issue = parser().parse_args(['token', 'create', '-u', 'alice', '-s', 'read', '-o', str(token_path)])
            metadata = run(issue, auth)
            token = token_path.read_text().strip()
            self.assertNotIn(token, repr(metadata))
            self.assertEqual(token_path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(auth.service.authenticate_token(token).scopes, frozenset(['read']))
            with self.assertRaises(FileExistsError):
                run(issue, auth)
            run(parser().parse_args(['token', 'revoke', '-i', metadata['credential_id']]), auth)
            with self.assertRaises(AuthenticationDenied):
                auth.service.authenticate_token(token)
            self.assertNotIn('hash', repr(auth.list_accounts()))
            run(parser().parse_args(['user', 'disable', '-u', 'alice']), auth)
            self.assertFalse(auth.list_accounts()[0]['enabled'])

    def test_failed_token_export_revokes_token_and_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            auth = PersistentAuthentication({'path': str(Path(tmp) / 'auth.db'), 'timeout': 1})
            self.addCleanup(auth.close)
            auth.provision('alice', PASSWORD, ['read'])
            destination = Path(tmp) / 'token'
            options = parser().parse_args(['token', 'create', '-u', 'alice', '-o', str(destination)])
            with mock.patch('auton.classes.auth_cli.os.fsync', side_effect=OSError('write failure')):
                with self.assertRaises(OSError):
                    run(options, auth)
            self.assertFalse(destination.exists())
            with auth.store.transaction() as tx:
                self.assertEqual(tx.items('tokens'), [])

    def test_neutral_auth_services_do_not_import_daemon_or_cli_interfaces(self):
        code = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in ('httpdis.httpdis', 'auton.classes.http', 'auton.classes.config', 'auton.classes.auth_cli') or fullname.split('.')[0] in ('dwho', 'curses', 'argparse'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from auton.classes.auth_store import PersistentAuthentication
with __import__('tempfile').TemporaryDirectory() as tmp:
    auth = PersistentAuthentication({'path': tmp + '/auth.db', 'timeout': 1})
    auth.provision('alice', 'integration-only-password', ['read'])
    grant = auth.issue_token('alice', ['read'], 60)
    assert auth.service.authenticate_token(grant.secret).principal == 'alice'
    auth.close()
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)


class ClientCredentialTests(unittest.TestCase):
    def test_private_token_files_and_transport_guards(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'token'
            path.write_text('a' * 43 + '\n')
            path.chmod(0o600)
            credential = BearerCredentials.from_file(path)
            self.assertNotIn('a' * 43, repr(credential))
            for uri in ('https://example.org/run', 'http://127.0.0.1/run', 'http://[::1]/health'):
                request = requests.Request('GET', uri, auth=credential).prepare()
                self.assertEqual(request.headers['Authorization'], 'Bearer ' + 'a' * 43)
            for uri in ('http://example.org', 'http://localhost', 'https://user:pass@example.org'):
                with self.assertRaises(ValueError):
                    requests.Request('GET', uri, auth=credential).prepare()
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                BearerCredentials.from_file(path)
            path.chmod(0o600)
            link = Path(tmp) / 'link'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                BearerCredentials.from_file(link)
            path.write_text('PRIVATE INVALID TOKEN')
            with self.assertRaises(ValueError) as caught:
                BearerCredentials.from_file(path)
            self.assertNotIn('PRIVATE', str(caught.exception))
        with self.assertRaises(ValueError):
            credentials_from_options(SimpleNamespace(token_file='unused', auth_user='alice', auth_passwd=None))
        self.assertEqual(credentials_from_options(SimpleNamespace(auth_user='alice', auth_passwd='old')), ('alice', 'old'))


class PersistentDaemonTests(unittest.TestCase):
    def test_bearer_scopes_acl_revocation_cli_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            auth = PersistentAuthentication({'path': str(path / 'auth.db'), 'timeout': 1})
            self.addCleanup(auth.close)
            auth.provision('alice', PASSWORD, ['read', 'run', 'maintenance'])
            auth.provision('bob', PASSWORD, ['read', 'run', 'maintenance'])
            read = auth.issue_token('alice', ['read'], 60)
            full = auth.issue_token('alice', ['read', 'run', 'maintenance'], 60)
            denied = auth.issue_token('bob', ['read', 'run', 'maintenance'], 60)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            config = yaml.safe_load((ROOT / 'etc/auton/auton.yml.example').read_text())
            config['general'].update(listen_port=port, max_life_time=0, max_requests=0,
                authentication={'backend': 'sqlite', 'path': 'auth.db'}, maintenance_operators=['alice'])
            config.pop('import_modules', None)
            config['modules'] = yaml.safe_load((ROOT / 'etc/auton/modules/job.yml').read_text())
            config['modules']['job']['routes']['stats']['auth'] = ['alice']
            config['endpoints'] = {'test': {'plugin': 'subproc', 'users': {'alice': True},
                'config': {'prog': sys.executable, 'timeout': 2}}}
            conf = path / 'auton.yml'
            conf.write_text(yaml.safe_dump(config))
            token_file = path / 'token'
            token_file.write_text(full.secret)
            token_file.chmod(0o600)
            uri = 'http://127.0.0.1:%d' % port
            log = path / 'daemon.log'
            environment = dict(os.environ, PYTHONPATH=str(ROOT))
            for restart in (False, True):
                with (path / 'stderr.log').open('w') as errors:
                    process = subprocess.Popen([sys.executable, str(ROOT / 'bin/autond'), '-f', '-c', str(conf),
                        '-p', str(path / 'daemon.pid'), '--logfile', str(log)], cwd=ROOT, env=environment,
                        stdout=subprocess.DEVNULL, stderr=errors)
                    try:
                        ready = False
                        for _ in range(150):
                            if process.poll() is not None:
                                break
                            try:
                                ready = requests.get(uri + '/health', auth=BearerCredentials(full.secret), timeout=.2).status_code == 200
                                if ready:
                                    break
                            except requests.RequestException:
                                pass
                            time.sleep(.02)
                        self.assertTrue(ready, (path / 'stderr.log').read_text() + (log.read_text() if log.exists() else ''))
                        def request(method, route, grant=None, **kwargs):
                            return requests.request(method, uri + route, auth=BearerCredentials(grant.secret) if grant else None,
                                                    timeout=3, **kwargs)
                        self.assertEqual(request('GET', '/health').status_code, 401)
                        if restart:
                            self.assertEqual(request('GET', '/health', read).status_code, 401)
                            self.assertEqual(request('GET', '/jobs', full).json()['jobs'], [])
                            auth.disable('alice')
                            self.assertEqual(request('GET', '/health', full).status_code, 401)
                            continue
                        for route in ('/health', '/stats', '/endpoints', '/jobs'):
                            self.assertEqual(request('GET', route, read).status_code, 200)
                        self.assertEqual(request('GET', '/health', denied).status_code, 200)
                        self.assertEqual(request('GET', '/stats', denied).status_code, 403)
                        (path / 'auth.db').chmod(0o644)
                        try:
                            self.assertEqual(request('GET', '/health', full).status_code, 503)
                        finally:
                            (path / 'auth.db').chmod(0o600)
                        payload = {'args': ['-c', 'print("permitted")']}
                        self.assertEqual(request('POST', '/run/test/readonly-job', read, json=payload).status_code, 403)
                        self.assertEqual(request('POST', '/run/test/deniedacl-job', denied, json=payload).status_code, 403)
                        self.assertEqual(request('POST', '/maintenance', read, json={'enabled': True}).status_code, 403)
                        self.assertEqual(request('POST', '/maintenance', denied, json={'enabled': True}).status_code, 403)
                        self.assertEqual(request('POST', '/maintenance', full, json={'enabled': True}).status_code, 200)
                        self.assertEqual(request('POST', '/maintenance', full, json={'enabled': False}).status_code, 200)
                        self.assertEqual(requests.get(uri + '/health', auth=('alice', PASSWORD), timeout=3).status_code, 401)
                        execution = subprocess.run([sys.executable, str(ROOT / 'bin/auton'), '--uri', uri,
                            '-k', str(token_file), '--endpoint', 'test', '--delay', '0.01',
                            '-a', '-c', '-a', 'print("token-client")'], cwd=ROOT, env=environment,
                            capture_output=True, text=True, timeout=10)
                        self.assertEqual(execution.returncode, 0, execution.stderr)
                        self.assertIn('token-client', execution.stdout)
                        jobs = request('GET', '/jobs', full).json()['jobs']
                        self.assertEqual(len(jobs), 1)
                        self.assertEqual(request('GET', '/jobs', denied).json()['jobs'], [])
                        auth.revoke_token(read.credential_id)
                        self.assertEqual(request('GET', '/health', read).status_code, 401)
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
            logs = log.read_text()
            for secret in (full.secret, read.secret, PASSWORD):
                self.assertNotIn(secret, logs)

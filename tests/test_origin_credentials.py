"""Origin-scoped credentials, flat imports and real independent daemon stores."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
import yaml

from auton_client import RemoteClient
from auton_client.config import load_inventory
from auton_client.connections import origin_key
from auton_client.credentials import (BearerCredentials, OriginCredentials,
    bind_credentials, credentials_from_options)
from auton_client.operations import OperationService
from auton_client.scenarios import ScenarioService
from auton_client.visibility import DaemonClient
from web_fixture import WebDaemon, ROOT


class OriginCredentialTests(unittest.TestCase):
    def test_exact_origin_routing_normalization_and_no_fallback(self):
        auth = OriginCredentials({origin_key('https://ONE:443'): BearerCredentials('a' * 43),
                                  origin_key('https://two:8443'): BearerCredentials('b' * 43),
                                  origin_key('http://[::1]:8666'): BearerCredentials('c' * 43)})
        for uri, token in [('https://one/jobs', 'a'), ('https://two:8443/run/e/job', 'b'),
                           ('http://[::1]:8666/health', 'c')]:
            request = requests.Request('GET', uri, auth=auth).prepare()
            self.assertEqual(request.headers['Authorization'], 'Bearer ' + token * 43)
        for uri in ('https://one:444/jobs', 'https://two/jobs', 'https://other/jobs',
                    'http://one/jobs', 'https://user:password@one/jobs'):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                requests.Request('GET', uri, auth=auth).prepare()
        with self.assertRaises(ValueError):
            requests.Request('GET', 'https://two:8443/jobs', auth=auth.restricted(['https://one'])).prepare()
        self.assertNotIn('a' * 43, repr(auth))

    def test_single_token_is_bound_by_each_transport_and_operation_preflights_all_targets(self):
        token = BearerCredentials('a' * 43)
        for auth in (RemoteClient(['https://one'], 'test', 'job', auth=token)._auth,
                     DaemonClient('https://one', auth=token).auth):
            with self.assertRaises(ValueError):
                requests.Request('GET', 'https://other/jobs', auth=auth).prepare()
        auth = bind_credentials(token, ['https://one'])
        factory = Mock()
        with self.assertRaises(ValueError):
            OperationService({'one': 'https://one', 'two': 'https://two'}, 'test', auth=auth,
                             client_factory=factory)
        with self.assertRaises(ValueError):
            ScenarioService({'one': {'uris': ['https://one', 'https://two']}},
                            {'check': {'version': 1, 'steps': [{'name': 'check', 'endpoint': 'test'}]}},
                            auth=auth, client_factory=factory)
        factory.assert_not_called()

    def test_tui_preflights_backup_credentials_before_starting_monitor(self):
        from auton_client import tui
        auth = bind_credentials(BearerCredentials('a' * 43), ['https://one'])
        with patch.object(sys.stdin, 'isatty', return_value=True), \
             patch.object(sys.stdout, 'isatty', return_value=True), \
             patch.object(tui, 'FleetMonitor') as monitor:
            with self.assertRaises(ValueError):
                tui.run([], [], auth=auth, selected={'one': {'uris': ['https://one', 'https://two']}})
            monitor.assert_not_called()

    def test_imported_profiles_resolve_token_paths_and_read_only_selected_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'auth').mkdir()
            token = root / 'auth/token'
            token.write_text('a' * 43)
            token.chmod(0o600)
            (root / 'auth/profiles.yml').write_text(yaml.safe_dump({
                'one': {'origins': ['https://one'], 'token_file': 'token'},
                'unused': {'origins': ['https://unused'], 'token_file': 'missing'}}))
            main = root / 'main.yml'
            main.write_text('targets: {one: https://one}\nimport_credentials: auth/profiles.yml')
            inventory = load_inventory(main)
            options = SimpleNamespace(configured_credentials=inventory['credentials'],
                                      selected_targets=inventory['targets'])
            auth = credentials_from_options(options)
            self.assertEqual(requests.Request('GET', 'https://one/jobs', auth=auth).prepare().headers['Authorization'],
                             'Bearer ' + 'a' * 43)
            for field, value in [('token_file', 'ignored'), ('auth_user', 'alice'), ('auth_passwd', 'secret')]:
                with self.subTest(field=field), self.assertRaises(ValueError):
                    credentials_from_options(SimpleNamespace(**vars(options), **{field: value}))
            token.chmod(0o644)
            with self.assertRaises(ValueError):
                credentials_from_options(options)

    def test_rejects_ambiguous_or_unsafe_profiles_and_nested_imports(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            main = root / 'main.yml'
            profiles = [
                {},
                {'one': {'origins': ['https://one', 'https://ONE:443'], 'token_file': 'token'}},
                {'one': {'origins': ['http://remote'], 'token_file': 'token'}},
                {'one': {'origins': ['https://one'], 'token_file': 'token', 'secret': 'PRIVATE'}},
                {'Bad': {'origins': ['https://one'], 'token_file': 'token'}},
                {'one': {'origins': [], 'token_file': 'token'}},
                {'one': {'origins': ['https://one/path'], 'token_file': 'token'}},
            ]
            for profile in profiles:
                main.write_text(yaml.safe_dump({'targets': {'one': 'https://one'}, 'credentials': profile}))
                with self.subTest(profile=profile), self.assertRaises(ValueError) as caught:
                    load_inventory(main)
                self.assertNotIn('PRIVATE', str(caught.exception))
            main.write_text('targets: {one: https://one}\nimport_credentials: profiles.yml')
            (root / 'profiles.yml').write_text('import_credentials: nested.yml')
            with self.assertRaises(ValueError):
                load_inventory(main)

    def test_independent_daemons_cli_scenarios_visibility_and_maintenance_failover(self):
        one, two = WebDaemon(), WebDaemon()
        try:
            one.start(); two.start()
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                profiles = {}
                for name, daemon in [('one', one), ('two', two)]:
                    token = root / (name + '.token')
                    token.write_text(daemon.token); token.chmod(0o600)
                    profiles[name] = {'origins': [daemon.uri], 'token_file': str(token)}
                targets = {'one': one.uri, 'two': two.uri}
                main = root / 'inventory.yml'
                scenarios = {'check': {'version': 1, 'steps': [
                    {'name': 'first', 'endpoint': 'diagnostic', 'args': ['-c', 'print("profile-ok")']},
                    {'name': 'second', 'endpoint': 'diagnostic', 'args': ['-c', 'print("step-two")']}]}}
                main.write_text(yaml.safe_dump({'targets': targets, 'credentials': profiles, 'scenarios': scenarios}))
                inventory = load_inventory(main)
                auth = credentials_from_options(SimpleNamespace(configured_credentials=inventory['credentials'],
                                                               selected_targets=targets))
                for daemon in (one, two):
                    self.assertEqual(DaemonClient(daemon.uri, auth=auth).health()['status'], 'ok')
                result = ScenarioService(targets, scenarios, auth=auth, delay=.01).run()
                self.assertEqual(result['status'], 'completed', result)
                env = {k: v for k, v in os.environ.items() if not k.startswith('AUTON_')}
                cli = subprocess.run([sys.executable, str(ROOT / 'bin/auton'), '-c', str(main),
                    '-t', 'one', '-t', 'two', '-s', 'check', '--delay', '0.01'],
                    cwd=ROOT, env=dict(env, PYTHONPATH=str(ROOT)), capture_output=True, text=True, timeout=20)
                self.assertEqual(cli.returncode, 0, cli.stderr)
                for daemon in (one, two):
                    self.assertNotIn(daemon.token, cli.stdout + cli.stderr)
                session, _ = one.login()
                try:
                    response = session.post(one.uri + '/maintenance', json={'enabled': True}, timeout=3)
                    self.assertEqual(response.status_code, 200)
                finally:
                    session.close()
                result = OperationService({'replacement': {'uris': [one.uri, two.uri]}}, 'diagnostic',
                    payload={'args': ['-c', 'print("failover-ok")']}, auth=auth, delay=.01).run()
                self.assertEqual(result['status'], 'completed', result)
                # Removing the second profile must fail before any target is contacted.
                only_one = auth.restricted([one.uri])
                with self.assertRaises(ValueError):
                    OperationService(targets, 'diagnostic', auth=only_one)
        finally:
            one.close(); two.close()

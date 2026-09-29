"""Endpoint catalogue imports preserve legacy components and declaring-file paths."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from auton.classes.endpoint_imports import load_endpoint_imports, load_component
from auton.classes.exceptions import AutonConfigurationError
from auton.classes import config

ROOT = Path(__file__).resolve().parents[1]


class EndpointImportTests(unittest.TestCase):
    def test_imports_and_components_resolve_from_the_declaring_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'endpoints').mkdir()
            (root / 'endpoints/catalogue.yml').write_text('deploy.v1:\n  plugin: fake\n  import_vars: vars.yml\n  import_config: config.yml\n  import_users: users.yml\n  vars: {region: inline}\n  config: {timeout: 9}\n  users: {bob: false}\n')
            (root / 'endpoints/vars.yml').write_text('region: imported')
            (root / 'endpoints/config.yml').write_text('region: "${vars[\'region\']}"\ntimeout: 2')
            (root / 'endpoints/users.yml').write_text('alice: true\nbob: true')
            (root / 'legacy.yml').write_text('timeout: 3')
            main = root / 'main.yml'
            main.write_text('general: {}\nimport_endpoints: endpoints/catalogue.yml\nendpoints:\n  legacy:\n    plugin: fake\n    import_config: legacy.yml\n')
            factory = Mock(side_effect=lambda name: Mock(name=name))
            with patch.object(config, 'parse_conf', side_effect=lambda value: value), \
                 patch.object(config, 'init_modules'), patch.object(config.signal, 'signal'), \
                 patch.object(config, 'PLUGINS', {'fake': factory}), \
                 patch.object(config, 'ENDPOINTS'), patch.object(config, 'DWHO_THREADS', []):
                loaded = config.load_conf(str(main))
                self.assertEqual(set(loaded['endpoints']), {'legacy', 'deploy.v1'})
                instances = config.ENDPOINTS.register.call_args_list
                legacy = instances[0].args[0].init.call_args.args[0]
                imported = instances[1].args[0].init.call_args.args[0]
                self.assertEqual(legacy['config'], {'timeout': 3})
                self.assertEqual(legacy['auton']['config_dir'], str(root))
                self.assertEqual(imported['config'], {'region': 'inline', 'timeout': 9})
                self.assertEqual(imported['users'], {'alice': True, 'bob': False})
                self.assertEqual(imported['auton']['config_dir'], str(root / 'endpoints'))

    def test_duplicates_nested_imports_and_malformed_catalogues_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / 'endpoints.yml'
            conf = {'_config_directory': tmp, 'import_endpoints': 'endpoints.yml'}
            for content in ('a: {plugin: fake}\na: {plugin: fake}',
                            'import_endpoints: more.yml', 'a: {plugin: fake, import_endpoints: more.yml}',
                            'a: {plugin: fake, config: []}', 'a: {config: {}}', '[]',
                            'a: {plugin: fake, plugin: other}', 'a: !include other.yml'):
                path.write_text(content)
                with self.subTest(content=content), self.assertRaises(AutonConfigurationError) as caught:
                    load_endpoint_imports(conf)
                self.assertIn(str(path), str(caught.exception))
            path.write_text('a: {plugin: fake}')
            for extra in ({'endpoints': {'a': {'plugin': 'fake'}}},
                          {'import_endpoints': ['endpoints.yml', 'endpoints.yml']},
                          {'import_endpoints': []}, {'import_endpoints': 'https://host/endpoints.yml'}):
                with self.assertRaises(AutonConfigurationError):
                    load_endpoint_imports(dict(conf, **extra))
            (root / 'alias.yml').symlink_to(path)
            with self.assertRaises(AutonConfigurationError):
                load_endpoint_imports(dict(conf, import_endpoints=['endpoints.yml', 'alias.yml']))

    def test_components_are_terminal_and_reusable_without_secret_diagnostics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / 'endpoints.yml'
            component = root / 'shared.yml'
            component.write_text('alice: true')
            self.assertEqual(load_component('shared.yml', source, {}), {'alice': True})
            self.assertEqual(load_component('shared.yml', source, {}), {'alice': True})
            for content in ('import_users: another.yml', 'token: SECRET\ntoken: other',
                            '${1 / 0}', '- SECRET'):
                component.write_text(content)
                with self.assertRaises(AutonConfigurationError) as caught:
                    load_component('shared.yml', source, {})
                self.assertIn(str(component), str(caught.exception))
                self.assertNotIn('SECRET', str(caught.exception))

    def test_bad_later_component_fails_before_any_endpoint_initialization(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'endpoints.yml').write_text('a: {plugin: fake}\nb: {plugin: fake, import_config: missing.yml}')
            main = root / 'main.yml'
            main.write_text('general: {}\nimport_endpoints: endpoints.yml')
            factory = Mock()
            with patch.object(config, 'parse_conf', side_effect=lambda value: value), \
                 patch.object(config, 'init_modules'), patch.object(config.signal, 'signal'), \
                 patch.object(config, 'PLUGINS', {'fake': factory}), \
                 patch.object(config, 'ENDPOINTS'), patch.object(config, 'DWHO_THREADS', []):
                with self.assertRaises(AutonConfigurationError):
                    config.load_conf(str(main))
                factory.assert_not_called()

    def test_limits_and_missing_files_fail_explicitly(self):
        with tempfile.TemporaryDirectory() as tmp:
            conf = {'_config_directory': tmp, 'import_endpoints': 'missing.yml'}
            with self.assertRaises(AutonConfigurationError):
                load_endpoint_imports(conf)
            path = Path(tmp) / 'large.yml'
            path.write_text('a: {plugin: fake}\n#' + 'x' * 1048576)
            with self.assertRaises(AutonConfigurationError):
                load_endpoint_imports(dict(conf, import_endpoints='large.yml'))
            with self.assertRaises(AutonConfigurationError):
                load_endpoint_imports(dict(conf, import_endpoints=['a.yml'] * 33))

    def test_resolver_runs_without_interface_imports(self):
        script = '''
import importlib.abc, sys, tempfile
from pathlib import Path
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('dwho', 'httpdis', 'curses', 'argparse'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from auton.classes.endpoint_imports import load_endpoint_imports
with tempfile.TemporaryDirectory() as tmp:
    Path(tmp, 'endpoints.yml').write_text('test: {plugin: subproc}')
    definitions, sources = load_endpoint_imports({'_config_directory': tmp, 'import_endpoints': 'endpoints.yml'})
    assert definitions['test']['plugin'] == 'subproc'
    assert sources['test'] == str(Path(tmp, 'endpoints.yml'))
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

"""Bounded downward-only imports, precedence and actual startup preparation."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import yaml

from auton.classes import config
from auton.classes.config_imports import load_config
from auton.classes.endpoint_imports import BLOCK_IMPORTS, IMPORT_FIELDS
from auton.classes.exceptions import AutonConfigurationError
from auton.classes.migration import migration_findings


class ConfigImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'auton.yml'

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(value))
        return name

    def test_all_six_blocks_under_imported_config_use_declaring_paths(self):
        values = {'args': ['--format', 'pdf', '--tag', 'a', '--tag', 'a'],
                  'env': {'TZ': 'UTC'}, 'allow-args': ['month'],
                  'disallow-args': ['dangerous'], 'allow-env': {'MODE': {'choices': ['safe']}},
                  'disallow-env': ['LD_PRELOAD']}
        imported = {'prog': '${vars["program"]}'}
        for key, field in BLOCK_IMPORTS.items():
            self.write('nested/' + key + '.yml', values[field])
            imported[key] = key + '.yml'
        self.write('nested/config.yml', imported)
        result = load_config({'import_config': 'nested/config.yml'}, self.source,
                             {'vars': {'program': '/bin/echo'}})
        self.assertEqual(result, dict(values, prog='/bin/echo'))

    def test_inline_blocks_replace_whole_imports_and_endpoint_imports_override_base(self):
        self.write('base.yml', {'args': ['base'], 'env': {'A': 'base'}, 'allow-args': True})
        self.write('args.yml', ['direct'])
        self.write('allow.yml', ['month', 'unsafe'])
        self.write('inline-env.yml', {'B': 'imported'})
        definition = {'import_config': 'base.yml', 'import_args': 'args.yml',
                      'import_allow_args': 'allow.yml', 'config': {
                          'import_env': 'inline-env.yml', 'env': {'C': 'inline'}, 'allow-args': []}}
        self.assertEqual(load_config(definition, self.source, {}), {
            'args': ['direct'], 'env': {'C': 'inline'}, 'allow-args': []})

    def test_blocks_are_plain_data_and_are_not_shared_mutable_state(self):
        marker = self.root / 'executed'
        literal = '<% open(' + repr(str(marker)) + ', "w").close() %>'
        self.write('args.yml', [literal, '${vars["secret"]}'])
        endpoint = {'import_args': 'args.yml'}
        first = load_config(endpoint, self.source, {})
        first['args'].append('changed')
        self.assertEqual(load_config(endpoint, self.source, {})['args'], [literal, '${vars["secret"]}'])
        self.assertFalse(marker.exists())

    def test_imported_config_cannot_import_config_vars_users_or_catalogues(self):
        for key in IMPORT_FIELDS - BLOCK_IMPORTS.keys():
            self.write('config.yml', {key: 'missing.yml'})
            with self.subTest(key=key), self.assertRaisesRegex(AutonConfigurationError, 'cannot import'):
                load_config({'import_config': 'config.yml'}, self.source, {})

    def test_terminal_blocks_reject_every_import_at_any_depth(self):
        for key in BLOCK_IMPORTS:
            for nested in IMPORT_FIELDS:
                for value in ({nested: 'missing.yml'}, {'outer': [{nested: 'missing.yml'}]}):
                    self.write('block.yml', value)
                    with self.subTest(key=key, nested=nested), self.assertRaisesRegex(
                            AutonConfigurationError, 'block cannot import'):
                        load_config({key: 'block.yml'}, self.source, {})

    def test_missing_remote_bad_shape_duplicate_keys_and_cycles_fail_without_values(self):
        for key, value in [('import_args', ['SECRET', 1]), ('import_env', {'X': 123}),
                           ('import_allow_args', {'month': {'regex': '['}}),
                           ('import_disallow_env', {'SECRET': 'value'})]:
            self.write('block.yml', value)
            with self.subTest(key=key), self.assertRaises(AutonConfigurationError) as caught:
                load_config({key: 'block.yml'}, self.source, {})
            self.assertNotIn('SECRET', str(caught.exception))
        for text in ('X: SECRET\nX: other', '&loop [*loop]', '!include SECRET', 'null'):
            (self.root / 'block.yml').write_text(text)
            with self.subTest(text=text), self.assertRaises(AutonConfigurationError):
                load_config({'import_args': 'block.yml'}, self.source, {})
        for value in ('missing.yml', 'https://example.test/args.yml', [], '', None):
            with self.subTest(value=value), self.assertRaises(AutonConfigurationError):
                load_config({'import_args': value}, self.source, {})

    def test_oversized_block_is_rejected(self):
        (self.root / 'block.yml').write_bytes(b'#' * (1048576 + 1))
        with self.assertRaises(AutonConfigurationError):
            load_config({'import_args': 'block.yml'}, self.source, {})

    def test_catalogue_and_inline_endpoints_are_resolved_before_initialization(self):
        self.write('catalogue/args.yml', ['catalogue'])
        self.write('catalogue/endpoints.yml', {'catalogue': {
            'plugin': 'fake', 'import_args': 'args.yml'}})
        self.write('args.yml', ['inline'])
        self.write('auton.yml', {'general': {'auth_mode': 'legacy'},
                                'import_endpoints': 'catalogue/endpoints.yml',
                                'endpoints': {'inline': {'plugin': 'fake', 'import_args': 'args.yml'}}})
        factory = Mock(side_effect=lambda name: Mock(name=name))
        with patch.object(config, 'parse_conf', side_effect=lambda value: value), \
                patch.object(config, 'init_modules'), patch.object(config.signal, 'signal'), \
                patch.object(config, 'PLUGINS', {'fake': factory}), \
                patch.object(config, 'ENDPOINTS'), patch.object(config, 'DWHO_THREADS', []):
            config.load_conf(str(self.source))
            configs = [call.args[0].init.call_args.args[0]['config']
                       for call in config.ENDPOINTS.register.call_args_list]
            self.assertEqual(configs, [{'args': ['inline']}, {'args': ['catalogue']}])
            factory.reset_mock()
            self.write('catalogue/args.yml', {'import_args': 'args.yml'})
            with self.assertRaises(AutonConfigurationError):
                config.load_conf(str(self.source))
            factory.assert_not_called()

    def test_migration_report_does_not_claim_unresolved_imports_are_effective_policies(self):
        self.write('auton.yml', {'general': {'auth_mode': 'required'}, 'endpoints': {
            'work': {'plugin': 'subproc', 'import_allow_args': 'not-read.yml'}}})
        self.assertTrue(any(line.startswith("REVIEW 'work'") for line in migration_findings(self.source)))

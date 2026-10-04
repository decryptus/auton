"""Configuration contracts use XYS without coercing values or initializing services."""
import copy
import unittest
try:
    from unittest.mock import patch
except ImportError:
    from mock import patch

from auton.classes.configuration_schema import validate_configuration, AutonConfigurationError


class ConfigurationSchemaTests(unittest.TestCase):
    def test_extensions_and_values_are_preserved_without_mutation(self):
        conf = {'general': {'max_jobs': '128'}, 'endpoints': {'demo': {'plugin': 'fake', 'config': {'opaque': [1]}, 'vars': {}, 'users': {}, 'extension': 'PRIVATE'}}}
        original = copy.deepcopy(conf)
        self.assertIs(validate_configuration(conf), conf)
        self.assertEqual(conf, original)

    def test_invalid_known_fields_cannot_hide_behind_extensions(self):
        cases = [None,
                 {'general': []},
                 {'general': {}, 'endpoints': []},
                 {'general': {}, 'endpoints': {'a': {'plugin': False}}},
                 {'general': {}, 'endpoints': {'a': {'plugin': 'fake', 'vars': []}}},
                 {'general': {}, 'endpoints': {'a': {'plugin': 'fake', 'import_config': 42}}}]
        for conf in cases:
            with self.assertRaises(AutonConfigurationError) as caught:
                validate_configuration(conf)
            self.assertNotIn('PRIVATE', str(caught.exception))

    def test_invalid_values_are_not_in_validation_logs(self):
        with patch('auton.classes.configuration_schema.xys.LOG') as logger:
            with self.assertRaises(AutonConfigurationError) as caught:
                validate_configuration({'general': 'PRIVATE-CONFIGURATION-VALUE'})
        self.assertNotIn('PRIVATE-CONFIGURATION-VALUE', str(caught.exception))
        self.assertNotIn('PRIVATE-CONFIGURATION-VALUE', str(logger.mock_calls))

    def test_invalid_file_precedes_module_initialization(self):
        import os
        import tempfile
        from auton.classes import config
        fd, path = tempfile.mkstemp()
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write('general: [PRIVATE]')
            with patch.object(config.signal, 'signal'), patch.object(config, 'init_modules') as init:
                with self.assertRaises(AutonConfigurationError):
                    config.load_conf(path)
                init.assert_not_called()
        finally:
            os.unlink(path)

    def test_legacy_component_import_is_validated_after_template_rendering(self):
        from pathlib import Path
        import tempfile
        from auton.classes.config import import_file
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, 'component.yml')
            path.write_text('timeout: ${vars["timeout"]}')
            self.assertEqual(import_file('component.yml', tmp, {'vars': {'timeout': 9}}), {'timeout': 9})
            path.write_text('- [PRIVATE, value]')
            with self.assertRaises(AutonConfigurationError):
                import_file('component.yml', tmp)

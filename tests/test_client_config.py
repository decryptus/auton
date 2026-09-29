"""Client inventories are validated completely and never imply execution."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auton_client.config import load_targets, load_inventory, MAX_CONFIG_BYTES
from auton_client.connections import named_connections, select_connections
from auton_client.tui import daemon_specs
from test_regressions import client_module


class ClientConfigTests(unittest.TestCase):
    def read(self, source):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'targets.yml'
            path.write_bytes(source.encode('utf-8'))
            return load_targets(path)

    def test_complete_inventory_normalizes_origins_without_selecting_them(self):
        targets = self.read('targets:\n  one: https://ONE:443/\n  two: http://[::1]:8666\n')
        self.assertEqual(targets, {'one': 'https://one:443', 'two': 'http://[::1]:8666'})
        self.assertEqual(named_connections([], targets), {})
        self.assertEqual(named_connections(['one'], targets), {'one': 'https://one:443'})
        self.assertEqual(daemon_specs(['two'], [], targets), {'two': 'http://[::1]:8666'})

    def test_named_and_inline_selections_can_be_mixed(self):
        targets = {'one': 'https://one'}
        self.assertEqual(named_connections(['one', 'adhoc=https://adhoc'], targets),
                         {'one': 'https://one', 'adhoc': 'https://adhoc'})
        self.assertEqual(daemon_specs(['one', 'adhoc=https://adhoc'], [], targets),
                         {'one': 'https://one', 'adhoc': 'https://adhoc'})
        for selection in (['unknown'], ['one', 'one'], ['one=https://other']):
            with self.subTest(selection=selection), self.assertRaises(ValueError):
                named_connections(selection, targets)

    def test_rejects_invalid_schema_duplicates_tags_and_alias_cycles(self):
        sources = ['', '[]', 'targets: {}', 'targets: []', 'targets: null',
                   'targets: {one: https://one}\nother: true',
                   'targets: {one: https://one, one: https://two}',
                   'targets: {one: https://one}\ntargets: {two: https://two}',
                   'targets: {one: {uri: https://one}}', 'targets: {one: 123}',
                   'targets: {One: https://one}', 'targets: {bad_name: https://one}',
                   'targets: {one: https://user:secret@one}',
                   'targets: {one: https://one, two: "http://ho st"}',
                   'targets: {1: https://one}', 'targets: [',
                   'targets: &cycle {one: *cycle}',
                   'targets: {one: !!python/object:example {}}',
                   'defaults: &d {one: https://one}\ntargets: {<<: *d}']
        for source in sources:
            with self.subTest(source=source), self.assertRaises(ValueError):
                self.read(source)
        self.assertEqual(self.read('targets: {"01": https://one}'), {'01': 'https://one'})

    def test_file_size_count_encoding_and_missing_file(self):
        for source in ('#' * (MAX_CONFIG_BYTES + 1),
                       'targets:\n' + ''.join('  node-%s: https://host%s\n' % (i, i) for i in range(129))):
            with self.assertRaises(ValueError):
                self.read(source)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yml'
            with self.assertRaises(ValueError):
                load_targets(path)
            path.write_bytes(b'\xff')
            with self.assertRaises(ValueError):
                load_targets(path)

    def test_cli_requires_explicit_selection_and_validates_unselected_entries(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'config.yml'
            path.write_text('targets: {one: https://one, two: https://two}')
            for flags in ([], ['--uri', 'https://one'], ['--target', 'missing'],
                          ['--target', 'one=https://other']):
                with patch.object(client_module.sys, 'argv', ['auton', '--config', str(path)] + flags), self.assertRaises(SystemExit):
                    client_module.argv_parse_check()
            for flags in (['--target', 'one'], ['--tui', '--daemon', 'one']):
                with patch.object(client_module.sys, 'argv', ['auton', '--config', str(path)] + flags):
                    options = client_module.argv_parse_check()
                    self.assertEqual(options.configured_targets['one'], 'https://one')
                    self.assertEqual(options.uri, [])
            path.write_text('targets: {one: https://one, two: "http://ho st"}')
            with patch.object(client_module.sys, 'argv', ['auton', '--config', str(path), '--target', 'one']), self.assertRaises(SystemExit):
                client_module.argv_parse_check()


class GroupAndImportTests(unittest.TestCase):
    def test_stable_union_deduplicates_group_members_and_explicit_targets(self):
        targets = {'one': 'https://one', 'two': 'https://two', 'three': 'https://three'}
        groups = {'web': ['one', 'two'], 'all': ['two', 'three']}
        result = select_connections(['three'], ['web', 'all', 'web'], targets, groups)
        self.assertEqual(list(result), ['three', 'one', 'two'])
        with self.assertRaises(ValueError):
            select_connections([], ['missing'], targets, groups)

    def test_inline_groups_validate_all_members_and_reject_nesting(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'config.yml'
            for group in ('{web: []}', '{web: [missing]}', '{web: one}', '{Web: [one]}',
                          '{web: [1]}', '{web: [other], other: [one]}', '{web: [[one]]}'):
                path.write_text('targets: {one: https://one}\ngroups: ' + group)
                with self.subTest(group=group), self.assertRaises(ValueError):
                    load_inventory(path)
            path.write_text('targets: {one: https://one}\ngroups: {web: [one, one]}')
            self.assertEqual(load_inventory(path)['groups'], {'web': ['one']})
            self.assertEqual(load_targets(path), {'one': 'https://one'})

    def test_relative_imports_merge_disjoint_sections_before_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'parts').mkdir()
            path = root / 'config.yml'
            path.write_text('import_targets: [parts/a.yml, parts/b.yml]\nimport_groups: parts/groups.yml\ntargets: {three: https://three}\ngroups: {extra: [three]}')
            (root / 'parts/a.yml').write_text('one: https://one')
            (root / 'parts/b.yml').write_text('two: https://two')
            (root / 'parts/groups.yml').write_text('web: [one, two, three]')
            inventory = load_inventory(path)
            self.assertEqual(list(inventory['targets']), ['one', 'two', 'three'])
            self.assertEqual(inventory['groups'], {'web': ['one', 'two', 'three'], 'extra': ['three']})

    def test_invalid_imports_duplicates_and_recursion_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'config.yml'
            (root / 'targets.yml').write_text('one: https://one')
            for config in ('import_targets: missing.yml', 'import_targets: config.yml',
                           'import_targets: [targets.yml, targets.yml]',
                           'import_targets: targets.yml\ntargets: {one: https://different}',
                           'import_targets: https://example.com/targets.yml',
                           'import_targets: []', 'import_targets: 42'):
                path.write_text(config)
                with self.subTest(config=config), self.assertRaises(ValueError):
                    load_inventory(path)
            path.write_text('import_targets: targets.yml')
            (root / 'targets.yml').write_text('import_targets: config.yml')
            with self.assertRaises(ValueError):
                load_inventory(path)
            (root / 'targets.yml').write_text('targets: {one: https://one}')
            with self.assertRaises(ValueError):
                load_inventory(path)

    def test_import_limits_apply_to_whole_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'config.yml'
            path.write_text('import_targets: targets.yml')
            (root / 'targets.yml').write_text('one: https://one\n#' + 'x' * MAX_CONFIG_BYTES)
            with self.assertRaises(ValueError):
                load_inventory(path)
            paths = []
            for i in range(17):
                name = 'part-%s.yml' % i
                paths.append(name)
                (root / name).write_text('node-%s: https://node%s' % (i, i))
            path.write_text('import_targets: [' + ', '.join(paths) + ']')
            with self.assertRaises(ValueError):
                load_inventory(path)

    def test_cli_short_options_group_selection_and_tui_are_equivalent(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'config.yml'
            path.write_text('targets: {one: https://one, two: https://two}\ngroups: {web: [one, two]}')
            variants = [ ['-c', str(path), '-t', 'one', '-g', 'web'],
                         ['--config', str(path), '--target', 'one', '--target-group', 'web'],
                         ['--tui', '-c', str(path), '-g', 'web'],
                         ['--tui', '-c', str(path), '-t', 'one', '-g', 'web'],
                         ['--tui', '-c', str(path), '--daemon', 'one', '-g', 'web'] ]
            for args in variants:
                with patch.object(client_module.sys, 'argv', ['auton'] + args):
                    options = client_module.argv_parse_check()
                    self.assertEqual(options.selected_targets, {'one': 'https://one', 'two': 'https://two'})
            for flags in (['-g', 'unknown', '-c', str(path)], ['-g', 'web'],
                          ['-g', 'web', '-c', str(path), '--uri', 'https://other']):
                with patch.object(client_module.sys, 'argv', ['auton'] + flags), self.assertRaises(SystemExit):
                    client_module.argv_parse_check()

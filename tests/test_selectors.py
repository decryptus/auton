"""Same selection grammar as monit-docker, with bounded regex evaluation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from auton_client.connections import select_connections
from auton_client.selectors import NameSelector, MAX_PATTERN_LENGTH, MAX_SELECTORS
from test_regressions import client_module


class SelectorTests(unittest.TestCase):
    def test_glob_full_name_and_regex_start_semantics(self):
        names = ['web-01', 'web-02', 'api-01', 'web-010']
        self.assertEqual(NameSelector().select('web-0?', names), ['web-01', 'web-02'])
        self.assertEqual(NameSelector().select('~web-01', names), ['web-01', 'web-010'])
        self.assertEqual(NameSelector().select('~web-01$', names), ['web-01'])
        self.assertEqual(NameSelector().select('~web-[0-9]{1,2}$', names), ['web-01', 'web-02'])
        for pattern in ('WEB-*', 'web', '*missing*'):
            with self.assertRaises(ValueError):
                NameSelector().select(pattern, names)

    def test_targets_groups_inline_uris_and_overlap(self):
        targets = {'web-01': 'https://one', 'web-02': 'https://two', 'api-01': 'https://api'}
        groups = {'prod-web': ['web-01', 'web-02'], 'prod-api': ['api-01']}
        selected = select_connections(['web-*', '~web-(?=01)01$', 'extra=https://extra'],
                                      ['prod-*'], targets, groups)
        self.assertEqual(list(selected), ['web-01', 'web-02', 'extra', 'api-01'])
        self.assertEqual(selected['extra'], 'https://extra')
        with self.assertRaises(ValueError):
            select_connections(['web-01', 'web-01'], configured=targets)

    def test_invalid_oversized_and_unmatched_selectors_reject_entire_selection(self):
        targets = {'web-01': 'https://one'}
        for pattern in ('~[', '~a{99999999999999999999}', 'x' * (MAX_PATTERN_LENGTH + 1),
                        'missing-*', 'glob:web-*', 'regex:web-*'):
            with self.subTest(pattern=pattern[:30]), self.assertRaises(ValueError):
                select_connections(['web-01', pattern], configured=targets)
        with self.assertRaises(ValueError):
            select_connections(['web-*'] * (MAX_SELECTORS + 1), configured=targets)
        with self.assertRaises(ValueError):
            select_connections(['web-01'], ['missing-*'], targets, {'prod': ['web-01']})

    def test_pathological_regex_times_out(self):
        with self.assertRaisesRegex(ValueError, 'timed out'):
            NameSelector().select('~(a+)+$', ['a' * 10000 + '!'])

    def test_total_selection_budget_is_enforced(self):
        now = [0]
        selector = NameSelector(clock=lambda: now[0])
        now[0] = 2
        with self.assertRaisesRegex(ValueError, 'budget'):
            selector.select('*', ['web-01'])

    def test_cli_and_tui_share_patterns_and_reject_unknowns_before_startup(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'config.yml'
            path.write_text('targets: {web-01: https://one, web-02: https://two}\ngroups: {prod: [web-01, web-02]}')
            for args in (['-t', 'web-*'], ['-g', '~prod$'], ['--tui', '--daemon', '~web-0[12]$']):
                with patch.object(client_module.sys, 'argv', ['auton', '-c', str(path)] + args):
                    options = client_module.argv_parse_check()
                    self.assertEqual(list(options.selected_targets), ['web-01', 'web-02'])
            with patch.object(client_module.sys, 'argv', ['auton', '--tui', '-c', str(path), '-g', 'missing-*']), self.assertRaises(SystemExit):
                client_module.argv_parse_check()

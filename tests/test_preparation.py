"""Interactive execution requires a reviewed plan and explicit confirmation."""
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from auton_client.preparation import PreparationView
from auton_client.session import ExecutionSession
from auton_client.tui import OperatorView
from test_regressions import client_module
from test_scenarios import factory_with, scenario


class PreparationTests(unittest.TestCase):
    def panel(self, session=None, calls=None):
        if session is None:
            session = Mock(running=False)
            session.poll.return_value = None
        return PreparationView({'one': 'http://one', 'two': 'http://two'},
            {'both': ['one', 'two'], 'outside': ['one', 'three']},
            {'check': scenario('check', 'verify')}, {'checks': ['check'], 'hidden': ['other']},
            session, {'client_factory': factory_with([] if calls is None else calls), 'delay': 0})

    def test_open_selection_preview_and_cancel_never_submit(self):
        session = Mock(running=False)
        panel = self.panel(session)
        self.assertNotIn('outside', panel.groups)
        self.assertNotIn('hidden', panel.scenario_groups)
        panel.handle(ord(' '))
        panel.handle(9)
        panel.handle(9)
        panel.handle(ord(' '))
        panel.handle(10)
        self.assertEqual(panel.mode, 'confirm')
        self.assertTrue(any('one = http://one' in line for line in panel.lines()))
        self.assertTrue(any('verify -> verify' in line for line in panel.lines()))
        panel.handle(ord('n'))
        self.assertEqual(panel.mode, 'select')
        session.start.assert_not_called()
        panel.result = {'status': 'completed'}
        panel.result_lines = ['old operation']
        panel.handle(10)
        panel.handle(ord('y'))
        self.assertIsNone(panel.result)
        self.assertIsNone(panel.result_lines)
        session.start.assert_called_once()
        panel.handle(ord('y'))
        session.start.assert_called_once()

    def test_group_execution_uses_shared_service_and_shows_outputs(self):
        session = ExecutionSession()
        calls = []
        try:
            panel = self.panel(session, calls)
            panel.selected[1] = ['both']
            panel.selected[3] = ['checks']
            panel.handle(10)
            panel.handle(ord('y'))
            session.future.result(timeout=2)
            panel.update({})
            self.assertEqual(panel.mode, 'result')
            self.assertEqual(panel.result['status'], 'completed')
            self.assertEqual(len(calls), 4)
            self.assertTrue(any('check/verify' in line for line in panel.lines()))
            self.assertIn('    stdout:', panel.lines())
            self.assertIn('      ok', panel.lines())
        finally:
            session.close()

    def test_invalid_inputs_block_preview_and_endpoint_switch_clears_scenarios(self):
        panel = self.panel()
        panel.selected[0] = ['one']
        panel.selected[2] = ['check']
        panel.update({'one': {'endpoints': [{'name': 'echo'}]}})
        panel.section = 4
        panel.handle(ord(' '))
        self.assertEqual(panel.selected[2], [])
        for payload in ('bad', '{"unknown": []}', '{"args": [1]}', '{"env": {"X=Y": "z"}}'):
            panel.input = payload
            panel.handle(10)
            self.assertEqual(panel.mode, 'select')
            self.assertTrue(panel.error)
        panel.input = '{"args": ["hello"], "env": {"LANG": "C"}}'
        panel.handle(10)
        self.assertEqual(panel.service.payload['args'], ['hello'])
        self.assertEqual(panel.mode, 'confirm')

    def test_background_stop_prevents_later_steps_and_duplicate_start(self):
        entered, release = threading.Event(), threading.Event()
        session = ExecutionSession()
        calls = []
        panel = self.panel(session, calls)
        original = panel.settings['client_factory']
        def factory(*args, **kwargs):
            client = original(*args, **kwargs)
            submit = client.do_run.side_effect
            def delayed():
                entered.set()
                release.wait(2)
                return submit()
            client.do_run.side_effect = delayed
            return client
        panel.settings['client_factory'] = factory
        try:
            panel.selected[0], panel.selected[2] = ['one'], ['check']
            panel.handle(10)
            panel.handle(ord('y'))
            self.assertTrue(entered.wait(1))
            with self.assertRaises(ValueError):
                session.start(panel.service)
            self.assertTrue(panel.handle(ord('q')))
            panel.handle(ord('x'))
            release.set()
            session.future.result(timeout=2)
            panel.update({})
            self.assertEqual(len(calls), 1)
            self.assertEqual(panel.result['status'], 'incomplete')
            self.assertEqual(panel.result['targets'][0]['scenarios'][0]['steps'][1]['status'], 'not_submitted')
        finally:
            release.set()
            session.close()

    def test_cli_catalogue_filters_do_not_preselect_execution(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'client.yml'
            path.write_text('targets: {one: http://one}\nscenarios: {check: {version: 1, steps: [{name: check, endpoint: test}]}}\nscenario_groups: {checks: [check]}')
            with patch.object(client_module.sys, 'argv', ['auton', '--tui', '-c', str(path), '-S', 'ch*']):
                options = client_module.argv_parse_check()
            self.assertEqual(options.selected_targets, {'one': 'http://one'})
            self.assertEqual(list(options.selected_scenarios), ['check'])
            panel = PreparationView(options.selected_targets, {}, options.selected_scenarios,
                                    options.configured_scenario_groups, Mock(), {})
            self.assertFalse(any(panel.selected))
            monitor = Mock(clients={'one': Mock()}, worker=None)
            view = OperatorView(monitor, preparation=panel)
            view.handle(ord('e'))
            self.assertTrue(view.preparing)
            panel.session.start.assert_not_called()

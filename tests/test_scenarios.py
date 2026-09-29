"""Linear scenario execution, configuration imports and failure isolation."""
import copy
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from auton_client.config import load_inventory
from auton_client.execution import ExecutionError
from auton_client.operations import MAX_RETAINED_OUTPUT_BYTES
from auton_client.scenarios import (ScenarioService, select_scenarios,
                                    validate_scenarios, validate_scenario_groups)
from test_regressions import client_module


def scenario(*names):
    return {'version': 1, 'steps': [{'name': name, 'endpoint': name, 'args': ['hello']} for name in names]}


def factory_with(calls, failure=None, output='ok'):
    def factory(uris, endpoint, uid, **kwargs):
        client = Mock(output_offset=0)
        def submit():
            calls.append((uris[0], endpoint, uid, kwargs['payload']))
            if failure and failure == (uris[0], endpoint, 'unknown'):
                raise ExecutionError('network failure; remote outcome unknown')
            rc = 7 if failure and failure == (uris[0], endpoint, 'failed') else 0
            return {'uid': endpoint + ':' + uid, 'status': 'complete',
                    'stream': [output], 'next_offset': 1, 'return_code': rc}
        client.do_run.side_effect = submit
        return client
    return factory


class ScenarioTests(unittest.TestCase):
    def test_steps_and_scenarios_run_in_order_with_independent_job_ids(self):
        calls = []
        service = ScenarioService({'one': 'http://one', 'two': 'http://two'},
                                  {'deploy': scenario('check', 'deploy'), 'verify': scenario('verify')},
                                  client_factory=factory_with(calls), delay=0)
        result = service.run('operation-1')
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['operation_id'], 'operation-1')
        self.assertEqual(len({call[2] for call in calls}), 6)
        for target in ('one', 'two'):
            self.assertEqual([call[1] for call in calls if call[0] == 'http://' + target],
                             ['check', 'deploy', 'verify'])
        self.assertEqual(result['targets'][0]['scenarios'][0]['steps'][1]['step'], 'deploy')

    def test_failure_and_unknown_skip_only_that_target_and_never_replay(self):
        for state in ('failed', 'unknown'):
            calls = []
            result = ScenarioService({'one': 'http://one', 'two': 'http://two'},
                {'deploy': scenario('check', 'deploy'), 'verify': scenario('verify')},
                client_factory=factory_with(calls, ('http://one', 'check', state)), delay=0).run()
            self.assertEqual(result['status'], 'incomplete' if state == 'unknown' else 'failed')
            self.assertEqual([call[1] for call in calls if call[0] == 'http://one'], ['check'])
            one, two = result['targets']
            self.assertEqual(one['scenarios'][0]['steps'][1]['status'], 'skipped')
            self.assertIsNone(one['scenarios'][0]['steps'][1]['job_id'])
            self.assertEqual(one['scenarios'][1]['status'], 'skipped')
            self.assertEqual(two['status'], 'completed')

    def test_output_limit_is_shared_by_all_steps_on_a_target(self):
        calls = []
        output = 'x' * (MAX_RETAINED_OUTPUT_BYTES // 2 + 1)
        result = ScenarioService({'one': 'http://one'}, {'deploy': scenario('one', 'two', 'three')},
                                 client_factory=factory_with(calls, output=output)).run()['targets'][0]
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['output_truncated'])
        self.assertEqual(sum(len(chunk) for step in result['scenarios'][0]['steps']
                             for chunk in step['stdout']), MAX_RETAINED_OUTPUT_BYTES)
        self.assertEqual(len(calls), 3)

    def test_deadline_blocks_following_steps_without_submitting(self):
        now = [0]
        calls = []
        original = factory_with(calls)
        def factory(*args, **kwargs):
            client = original(*args, **kwargs)
            submit = client.do_run.side_effect
            def delayed():
                now[0] = 2
                return submit()
            client.do_run.side_effect = delayed
            return client
        data = ScenarioService({'one': 'http://one'}, {'deploy': scenario('one', 'two', 'three')},
                               client_factory=factory, timeout=1, clock=lambda: now[0]).run()
        self.assertEqual(data['status'], 'incomplete')
        self.assertEqual(len(calls), 1)
        self.assertEqual([s['status'] for s in data['targets'][0]['scenarios'][0]['steps']],
                         ['completed', 'not_submitted', 'skipped'])

    def test_validate_entire_plan_before_any_submission(self):
        factory = Mock()
        bad = scenario('one', 'two')
        bad['steps'][1]['endpoint'] = 'invalid/path'
        with self.assertRaises(ValueError):
            ScenarioService({'one': 'http://one'}, {'deploy': bad}, client_factory=factory).run()
        factory.assert_not_called()
        for mutation in ({'version': 2}, {'steps': []}, {'extra': 'bad'}, {'version': True}):
            definition = scenario('one')
            definition.update(mutation)
            with self.assertRaises(ValueError):
                validate_scenarios({'deploy': definition})
        duplicate = scenario('one', 'one')
        with self.assertRaises(ValueError):
            validate_scenarios({'deploy': duplicate})
        for field, value in (('args', [1]), ('args', ['bad\x00']), ('env', {'BAD=KEY': 'x'})):
            definition = scenario('one')
            definition['steps'][0][field] = value
            with self.assertRaises(ValueError):
                validate_scenarios({'deploy': definition})

    def test_group_order_patterns_and_deduplication(self):
        scenarios = {'check': scenario('check'), 'deploy': scenario('deploy'), 'verify': scenario('verify')}
        groups = validate_scenario_groups({'release': ['check', 'deploy', 'verify']}, scenarios)
        selected = select_scenarios(['check'], ['rel*', '~release$'], scenarios, groups)
        self.assertEqual(list(selected), ['check', 'deploy', 'verify'])
        for members in (['missing'], ['check', 'check'], ['nested']):
            with self.assertRaises(ValueError):
                validate_scenario_groups({'release': members}, scenarios)
        with self.assertRaises(ValueError):
            select_scenarios(['missing-*'], [], scenarios, groups)

    def test_scenario_config_imports_are_single_level_and_validated_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'client.yml'
            path.write_text('targets: {one: http://one}\nimport_scenarios: deploy.yml\nimport_scenario_groups: groups.yml')
            (root / 'deploy.yml').write_text('deploy:\n  version: 1\n  steps:\n    - name: check\n      endpoint: test\n      args: [hello]')
            (root / 'groups.yml').write_text('maintenance: [deploy]')
            data = load_inventory(path)
            self.assertEqual(data['scenario_groups'], {'maintenance': ['deploy']})
            (root / 'groups.yml').write_text('maintenance: [missing]')
            with self.assertRaises(ValueError):
                load_inventory(path)
            (root / 'groups.yml').write_text('import_scenario_groups: nested.yml')
            with self.assertRaisesRegex(ValueError, 'nested imports'):
                load_inventory(path)

    def test_cli_scenarios_require_targets_and_reject_ambiguous_inputs(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True):
            path = Path(directory) / 'client.yml'
            path.write_text('targets: {one: http://one}\nscenarios: {check: {version: 1, steps: [{name: check, endpoint: test}]}}\nscenario_groups: {maintenance: [check]}')
            for selection in (['-s', 'check'], ['-S', 'maint*']):
                with patch.object(client_module.sys, 'argv', ['auton', '-c', str(path), '-t', 'one'] + selection):
                    options = client_module.argv_parse_check()
                    self.assertEqual(list(options.selected_scenarios), ['check'])
            for flags in ([], ['-t', 'one', '--endpoint', 'test'], ['-t', 'one', '--tui'],
                          ['-t', 'one', '-a', 'argument']):
                with patch.object(client_module.sys, 'argv', ['auton', '-c', str(path), '-s', 'check'] + flags), self.assertRaises(SystemExit):
                    client_module.argv_parse_check()

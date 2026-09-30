"""Ordered target origins are replacements, never implicit broadcast targets."""
import tempfile
from pathlib import Path
import unittest
from unittest.mock import Mock

import requests
from urllib3.exceptions import NewConnectionError

from auton_client.config import load_inventory
from auton_client.connections import resolve_targets, select_connections, target_origins
from auton_client.execution import ExecutionClient, ExecutionError
from auton_client.operations import OperationService
from auton_client.scenarios import ScenarioService
from test_scenarios import scenario


def factory_for(calls, failures):
    def factory(uris, endpoint, uid, **kwargs):
        uri = uris[0]
        client = Mock(output_offset=0)
        def submit():
            calls.append((uri, endpoint))
            error = failures.get((uri, endpoint))
            if error:
                raise error
            return {'uid': endpoint + ':' + uid, 'status': 'complete',
                    'return_code': 0, 'next_offset': 1, 'stream': [uri]}
        client.do_run.side_effect = submit
        return client
    return factory


class TargetFailoverTests(unittest.TestCase):
    def test_reference_order_deduplication_and_selector_compatibility(self):
        catalogue = resolve_targets({'one': 'https://ONE', 'two': 'http://two',
            'nested': {'uris': [{'target': 'two'}]},
            'deploy': {'uris': [{'target': 'one'}, 'https://one:443', {'target': 'nested'}]}})
        self.assertEqual(catalogue['one'], 'https://one')
        self.assertEqual(catalogue['deploy'], {'uris': ['https://one', 'http://two']})
        self.assertEqual(select_connections(['dep*'], ['release'], catalogue, {'release': ['deploy']}),
                         {'deploy': catalogue['deploy']})
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'targets.yml').write_text('one: https://one\ndeploy: {uris: [{target: one}, https://two]}')
            (root / 'main.yml').write_text('import_targets: targets.yml')
            self.assertEqual(target_origins(load_inventory(root / 'main.yml')['targets']['deploy']),
                             ['https://one', 'https://two'])

    def test_invalid_references_and_overlapping_execution_targets_fail_offline(self):
        for entries in (
            {'a': {'uris': [{'target': 'missing'}]}},
            {'a': {'uris': [{'target': 'a'}]}},
            {'a': {'uris': [{'target': 'b'}]}, 'b': {'uris': [{'target': 'a'}]}},
            {'a': {'uris': [{'group': 'production'}]}},
            {'a': {'uris': []}}, {'a': {'uris': ['http://one/path']}},
            {'a': {'uris': ['http://x%s' % i for i in range(17)]}},
        ):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                resolve_targets(entries)
        factory = Mock()
        with self.assertRaises(ValueError):
            OperationService({'a': {'uris': ['https://one', 'http://two']}, 'b': 'https://one:443'},
                             'test', client_factory=factory)
        factory.assert_not_called()
        chain = {'node0': 'http://one'}
        for index in range(1, 17):
            chain['node%s' % index] = {'uris': [{'target': 'node%s' % (index - 1)}]}
        for entries in (chain, dict(reversed(list(chain.items())))):
            with self.assertRaises(ValueError):
                resolve_targets(entries)

    def test_safe_refusal_tries_next_origin_once_and_records_attempts(self):
        calls = []
        service = OperationService({'deploy': {'uris': ['http://one', 'http://two']}}, 'check',
            client_factory=factory_for(calls, {('http://one', 'check'):
                ExecutionError('maintenance', rejected=True, retry_safe=True)}))
        target = service.run()['targets'][0]
        self.assertEqual(calls, [('http://one', 'check'), ('http://two', 'check')])
        self.assertEqual(target['uri'], 'http://two')
        self.assertEqual(target['status'], 'completed')
        self.assertEqual([a['status'] for a in target['attempts']], ['not_admitted', 'admitted'])

    def test_ambiguous_post_and_authorization_refusal_never_move(self):
        for error in (ExecutionError('unknown'), ExecutionError('HTTP 403', rejected=True)):
            calls = []
            data = OperationService({'deploy': {'uris': ['http://one', 'http://two']}}, 'check',
                client_factory=factory_for(calls, {('http://one', 'check'): error})).run()
            self.assertEqual(len(calls), 1)
            self.assertEqual(data['targets'][0]['uri'], 'http://one')

    def test_scenario_stays_on_first_accepting_origin_even_if_it_becomes_unavailable(self):
        calls = []
        refused = ExecutionError('maintenance', rejected=True, retry_safe=True)
        service = ScenarioService({'deploy': {'uris': ['http://one', 'http://two', 'http://three']}},
            {'release': scenario('check', 'deploy', 'verify')},
            client_factory=factory_for(calls, {('http://one', 'check'): refused, ('http://two', 'deploy'): refused}))
        result = service.run()
        self.assertEqual(calls, [('http://one', 'check'), ('http://two', 'check'), ('http://two', 'deploy')])
        target = result['targets'][0]
        self.assertEqual(target['uri'], 'http://two')
        self.assertEqual([step['status'] for step in target['scenarios'][0]['steps']],
                         ['completed', 'rejected', 'skipped'])

    def test_post_connection_refusal_is_safe_but_read_timeout_is_not(self):
        for error, safe in [(requests.ConnectionError(NewConnectionError(None, 'refused')), True),
                            (requests.ReadTimeout('lost response'), False),
                            (requests.ConnectionError('disconnect'), False)]:
            session = Mock()
            session.get.return_value = Mock(status_code=404)
            session.post.side_effect = error
            with self.assertRaises(ExecutionError) as caught:
                ExecutionClient(['http://one'], 'check', 'example-job', session=session).do_run()
            self.assertEqual(caught.exception.retry_safe, safe)
            session.post.assert_called_once()

    def test_expired_scenario_preserves_the_pinned_origin_without_new_submission(self):
        now, calls = [0], []
        base = factory_for(calls, {('http://one', 'check'):
            ExecutionError('maintenance', rejected=True, retry_safe=True)})
        def factory(*args, **kwargs):
            client = base(*args, **kwargs)
            submit = client.do_run.side_effect
            def run():
                data = submit()
                now[0] = 2
                return data
            client.do_run.side_effect = run
            return client
        result = ScenarioService({'deploy': {'uris': ['http://one', 'http://two']}},
            {'release': scenario('check', 'deploy')}, client_factory=factory,
            clock=lambda: now[0], timeout=1).run()
        self.assertEqual(len(calls), 2)
        self.assertEqual(result['targets'][0]['uri'], 'http://two')
        self.assertEqual(result['targets'][0]['scenarios'][0]['steps'][1]['status'], 'not_submitted')

    def test_observation_remains_pinned_after_admission(self):
        calls = []
        def factory(uris, endpoint, uid, **kwargs):
            calls.append(uris[0])
            client = Mock(output_offset=0)
            client.do_run.return_value = {'uid': 'check:' + uid, 'status': 'processing', 'next_offset': 0}
            client.do_status.side_effect = ExecutionError('read timeout', retry_safe=True)
            return client
        result = OperationService({'deploy': {'uris': ['http://one', 'http://two']}}, 'check',
                                  client_factory=factory, delay=0).run()
        self.assertEqual(calls, ['http://one'])
        self.assertEqual(result['targets'][0]['status'], 'unknown')


class MonitoringOriginTests(unittest.TestCase):
    def test_read_expansion_preserves_logical_execution_targets(self):
        from auton_client.connections import monitoring_origins, target_origins
        targets = {'deploy': {'uris': ['http://one', 'http://two']}, 'db': 'http://db'}
        self.assertEqual(monitoring_origins(targets),
                         {'deploy': 'http://one', 'deploy@2': 'http://two', 'db': 'http://db'})
        self.assertEqual(list(targets), ['deploy', 'db'])
        self.assertEqual(target_origins(targets['deploy']), ['http://one', 'http://two'])

    def test_monitoring_bounds_total_expanded_origins(self):
        from auton_client.connections import monitoring_origins
        with self.assertRaisesRegex(ValueError, '128'):
            monitoring_origins({'node-%s' % i: 'http://node-%s' % i for i in range(129)})

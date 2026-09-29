"""Explicit execution, transport ambiguity and bounded operation behavior."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import Mock

import requests

from auton_client.execution import ExecutionClient, ExecutionError
from auton_client.operations import OperationService, named_connections, MAX_RETAINED_OUTPUT_BYTES

ROOT = Path(__file__).resolve().parents[1]


def result(uid, status='complete', stream=None, offset=0, rc=0, errors=None):
    chunks = stream or []
    return {'uid': 'test:' + uid, 'status': status, 'stream': chunks,
            'next_offset': offset + len(chunks), 'return_code': rc, 'errors': errors or []}


class OperationTests(unittest.TestCase):
    def test_independent_targets_payload_and_job_identity(self):
        clients = []
        def factory(uris, endpoint, uid, **kwargs):
            client = Mock(output_offset=0)
            client.do_run.return_value = result(uid, rc=7 if 'bad' in uris[0] else 0,
                                                stream=[uris[0]], errors=['diagnostic'])
            clients.append((uid, kwargs, client))
            return client
        payload = {'args': ['a']}
        service = OperationService({'good': 'https://good', 'bad': 'https://bad'}, 'test',
                                   payload=payload, client_factory=factory)
        payload['args'].append('mutated')
        data = service.run('deploy-42')
        self.assertEqual(data['operation_id'], 'deploy-42')
        self.assertEqual(data['status'], 'failed')
        self.assertEqual([t['status'] for t in data['targets']], ['completed', 'failed'])
        self.assertEqual(len({t['job_id'] for t in data['targets']}), 2)
        for uid, kwargs, client in clients:
            self.assertEqual(kwargs['payload'], {'args': ['a']})
            client.do_run.assert_called_once()
            client.do_status.assert_not_called()

    def test_offsets_and_stderr_are_not_duplicated(self):
        def factory(uris, endpoint, uid, **kwargs):
            client = Mock(output_offset=0)
            client.do_run.return_value = result(uid, 'processing', ['one'], errors=['err'])
            client.do_status.return_value = result(uid, stream=['two'], offset=1, errors=['err', 'two'])
            return client
        data = OperationService({'a': 'http://a'}, 'test', delay=0, client_factory=factory).run()
        self.assertEqual(data['targets'][0]['stdout'], ['one', 'two'])
        self.assertEqual(data['targets'][0]['stderr'], ['err', 'two'])

    def test_ambiguous_post_is_not_replayed_and_other_target_completes(self):
        calls = []
        def factory(uris, endpoint, uid, **kwargs):
            client = Mock(output_offset=0)
            if uris == ['https://lost']:
                client.do_run.side_effect = ExecutionError('remote outcome unknown')
            else:
                client.do_run.return_value = result(uid)
            calls.append(client)
            return client
        data = OperationService({'lost': 'https://lost', 'ok': 'https://ok'}, 'test', client_factory=factory).run()
        self.assertEqual(data['status'], 'incomplete')
        self.assertEqual([t['status'] for t in data['targets']], ['unknown', 'completed'])
        for client in calls:
            client.do_run.assert_called_once()
            client.do_status.assert_not_called()

    def test_deadline_prevents_queued_submission(self):
        now = [0]
        calls = []
        def factory(uris, endpoint, uid, **kwargs):
            client = Mock(output_offset=0)
            def submit():
                now[0] = 2
                return result(uid, 'processing')
            client.do_run.side_effect = submit
            calls.append(client)
            return client
        data = OperationService({'a': 'http://a', 'b': 'http://b'}, 'test', parallel=1,
                                timeout=1, clock=lambda: now[0], client_factory=factory).run()
        self.assertEqual([t['status'] for t in data['targets']], ['unknown', 'not_submitted'])
        self.assertEqual(len(calls), 1)
        calls[0].do_status.assert_not_called()

    def test_parallel_limit_and_actual_concurrency(self):
        lock = threading.Lock()
        ready = threading.Barrier(2)
        active = [0, 0]
        def factory(uris, endpoint, uid, **kwargs):
            client = Mock(output_offset=0)
            def submit():
                with lock:
                    active[0] += 1
                    active[1] = max(active)
                ready.wait(timeout=2)
                with lock:
                    active[0] -= 1
                return result(uid)
            client.do_run.side_effect = submit
            return client
        data = OperationService({str(i): 'http://host%s' % i for i in range(4)}, 'test',
                                parallel=2, client_factory=factory).run()
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(active[1], 2)

    def test_origin_normalization_preserves_valid_hosts_and_ipv6(self):
        from auton_client.connections import normalize_origin
        for source, expected in [('https://Example.COM:443/', 'https://example.com:443'),
                                 ('http://localhost:8666', 'http://localhost:8666'),
                                 ('http://127.0.0.1', 'http://127.0.0.1'),
                                 ('http://[2001:0db8::1]:8666', 'http://[2001:db8::1]:8666'),
                                 ('https://éxemple.fr', 'https://xn--xemple-9ua.fr')]:
            self.assertEqual(normalize_origin(source), expected)

    def test_connection_names_use_exact_ascii_grammar(self):
        from auton_client.tui import daemon_specs
        for name in ('autond-01', 'a', '0', '-', 'a' * 32):
            spec = name + '=https://host'
            self.assertEqual(named_connections([spec]), {name: 'https://host'})
            self.assertEqual(daemon_specs([spec], []), {name: 'https://host'})
        for name in ('', 'Autond', 'a_b', 'a.b', 'été', '１', 'a b', 'a\n', 'a' * 33):
            with self.subTest(name=name):
                for parse in (named_connections, lambda specs: daemon_specs(specs, [])):
                    with self.assertRaises(ValueError):
                        parse([name + '=https://host'])
                with self.assertRaises(ValueError):
                    OperationService({name: 'https://host'}, 'test')

    def test_validation_before_any_post(self):
        factory = Mock()
        for targets, kwargs in [({'a': 'http://a', 'b': 'http://a:80'}, {}),
                                ({'a': 'http://a', 'b': 'http://user:secret@b'}, {}),
                                ({'a': 'http://a', 'b': 'http://ho st'}, {}),
                                ({'a': 'http://host', 'b': 'http://HOST.:80'}, {}),
                                ({'a': 'http://a'}, {'parallel': 0}),
                                ({'a': 'http://a'}, {'timeout': float('nan')}),
                                ({'a': 'http://a'}, {'payload': []})]:
            with self.subTest(targets=targets, kwargs=kwargs), self.assertRaises(ValueError):
                OperationService(targets, 'test', client_factory=factory, **kwargs).run()
        factory.assert_not_called()
        with self.assertRaises(ValueError):
            named_connections(['a=http://a', 'a=http://b'])

    def test_invalid_response_is_unknown_and_output_is_bounded(self):
        for invalid in (True, False):
            def factory(uris, endpoint, uid, **kwargs):
                client = Mock(output_offset=0)
                client.do_run.return_value = result('wrong' if invalid else uid,
                    stream=['x' * (MAX_RETAINED_OUTPUT_BYTES + 1)])
                return client
            data = OperationService({'a': 'http://a'}, 'test', client_factory=factory).run()['targets'][0]
            self.assertEqual(data['status'], 'unknown' if invalid else 'completed')
            if not invalid:
                self.assertTrue(data['output_truncated'])
                self.assertEqual(len(''.join(data['stdout'])), MAX_RETAINED_OUTPUT_BYTES)

    def test_cli_rejects_mixed_modes_without_network(self):
        for args in (['--uri', 'http://b'], ['--mode', 'run'], ['--uid', 'example-job'],
                     ['--no-return-code']):
            env = {k: v for k, v in os.environ.items() if not k.startswith('AUTON_')}
            call = subprocess.run([sys.executable, str(ROOT / 'bin/auton'), '--target', 'a=http://a'] + args,
                                  env=dict(env, PYTHONPATH=str(ROOT)), capture_output=True, timeout=5)
            self.assertEqual(call.returncode, 2, call.stderr)


class ExecutionTransportTests(unittest.TestCase):
    def test_job_error_response_is_preserved_and_offsets_sent(self):
        session = Mock()
        response = Mock(status_code=400)
        response.json.return_value = result('example-job', rc=7, errors=['oops'])
        session.post.return_value = session.get.return_value = response
        client = ExecutionClient(['https://a'], 'test', 'example-job', session=session, auth=('a', 'b'))
        self.assertEqual(client.do_run()['return_code'], 7)
        client.output_offset = 3
        self.assertEqual(client.do_status()['errors'], ['oops'])
        self.assertEqual(session.get.call_args.kwargs['headers']['X-Auton-Output-Offset'], '3')
        self.assertFalse(session.post.call_args.kwargs['allow_redirects'])
        self.assertEqual(response.close.call_count, 2)

    def test_refusal_redirect_and_timeout_do_not_retry(self):
        for code in (403, 302, 503, None):
            session = Mock()
            if code is None:
                session.post.side_effect = requests.exceptions.ReadTimeout('SECRET')
            else:
                session.post.return_value = Mock(status_code=code)
            client = ExecutionClient(['https://a'], 'test', 'example-job', session=session)
            with self.assertRaises(ExecutionError) as caught:
                client.do_run()
            self.assertEqual(caught.exception.rejected, code == 403)
            self.assertNotIn('SECRET', str(caught.exception))
            session.post.assert_called_once()
            session.get.assert_not_called()

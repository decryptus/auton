"""Read-only client, asynchronous monitor and terminal presentation boundaries."""
import curses
import importlib.abc
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

import requests

from auton_client.visibility import DaemonClient, VisibilityError
from auton_client.monitor import Monitor, FleetMonitor, snapshot, aggregate_snapshots
from auton_client.tui import OperatorView, daemon_specs, safe_text
from test_regressions import client_module, response

ROOT = Path(__file__).resolve().parents[1]
JOB = {'uid': 'test:job-0001', 'endpoint': 'test', 'status': 'complete', 'return_code': 0}


def fake_client():
    client = Mock()
    client.health.return_value = {'code': 200, 'status': 'ok'}
    client.jobs.return_value = [dict(JOB)]
    client.endpoints.return_value = [{'name': 'test'}]
    client.stats.return_value = {'jobs': 1, 'endpoints': 1, 'jobs_by_status': {'complete': 1}}
    client.detail.return_value = dict(JOB, stream=['hello\n'], errors=['diagnostic\n'])
    return client


class VisibilityClientTests(unittest.TestCase):
    def test_read_is_pinned_authenticated_and_closes_response(self):
        session = Mock()
        res = response(dict(JOB, code=200, stream=['hello'], errors=[]))
        session.get.return_value = res
        client = DaemonClient('https://one', auth=('alice', 'secret'), session=session, http_timeout=3)
        self.assertEqual(client.detail('test', 'test:job-0001')['stream'], ['hello'])
        args, kwargs = session.get.call_args
        self.assertEqual(args, ('https://one/jobs/test/job-0001',))
        self.assertEqual(kwargs['auth'], ('alice', 'secret'))
        self.assertEqual(kwargs['timeout'], 3)
        self.assertFalse(kwargs['allow_redirects'])
        session.post.assert_not_called()
        res.close.assert_called_once()

    def test_http_failures_do_not_redirect_retry_or_leak_credentials(self):
        for code in (301, 401, 403, 404, 503):
            with self.subTest(code=code):
                session = Mock()
                session.get.return_value = response(status=code)
                with self.assertRaisesRegex(VisibilityError, 'HTTP %s' % code):
                    DaemonClient('https://one', session=session).health()
                self.assertEqual(session.get.call_count, 1)
                session.get.return_value.close.assert_called_once()
        for error in (requests.Timeout('secret'), requests.ConnectionError('secret')):
            session = Mock()
            session.get.side_effect = error
            with self.assertRaises(VisibilityError) as caught:
                DaemonClient('https://one', session=session).health()
            self.assertNotIn('secret', str(caught.exception))

    def test_bad_payloads_and_origins_are_rejected(self):
        for uri in ('file:///tmp/a', 'https://user:secret@host', 'https://host/path',
                    'https://host?token=secret', 'https://host:invalid',
                    'http://host:0', 'http://host:65536', 'http://host:',
                    ' http://host', 'http://ho st', 'http://host\n', 'http://ho\tst',
                    'http://host%00', 'http://host\\path', 'http://-host', 'http://host..org', 'http://host..',
                    'http://999.1.2.3', 'http://[invalid]', 'http://host?', 'http://host#'):
            with self.subTest(uri=uri), self.assertRaises(ValueError):
                DaemonClient(uri)
        for method, data in (('jobs', {'jobs': [None]}), ('endpoints', {'endpoints': ['x']}),
                             ('endpoints', {'endpoints': [{'name': 'test', 'description': []}]}),
                             ('stats', {'stats': {'jobs_by_status': []}}), ('health', {'status': 'bad'})):
            session = Mock()
            session.get.return_value = response(dict(data, code=200))
            with self.subTest(method=method), self.assertRaises(VisibilityError):
                getattr(DaemonClient('https://one', session=session), method)()
            session.get.return_value.close.assert_called_once()


class MonitorTests(unittest.TestCase):
    def test_partial_errors_are_visible_and_detail_is_separate(self):
        client = fake_client()
        client.stats.side_effect = VisibilityError('HTTP 403')
        data = snapshot(client, ('test', JOB['uid']))
        self.assertEqual(data['errors'], {'stats': 'HTTP 403'})
        self.assertEqual(data['jobs'], [JOB])
        self.assertEqual(data['detail']['stream'], ['hello\n'])

    def test_refresh_is_bounded_and_close_does_not_wait_for_network(self):
        entered, release = threading.Event(), threading.Event()
        client = fake_client()
        def health():
            entered.set()
            release.wait(3)
            return {'status': 'ok'}
        client.health.side_effect = health
        monitor = Monitor({'one': client})
        try:
            self.assertTrue(monitor.refresh('one'))
            self.assertTrue(entered.wait(1))
            self.assertFalse(monitor.refresh('one'))
            start = time.monotonic()
            monitor.close()
            self.assertLess(time.monotonic() - start, 0.2)
            self.assertFalse(monitor.refresh('one'))
        finally:
            release.set()
            monitor.worker.join(2)
        client.jobs.assert_not_called()

    def test_result_tagging_and_worker_reuse(self):
        monitor = Monitor({'one': fake_client()})
        try:
            monitor.refresh('one', ('test', JOB['uid']))
            monitor.worker.join(2)
            name, job, data = monitor.poll()
            self.assertEqual((name, job), ('one', ('test', JOB['uid'])))
            self.assertEqual(data['detail']['stream'], ['hello\n'])
            self.assertTrue(monitor.refresh('one'))
            monitor.worker.join(2)
        finally:
            monitor.close()

    def test_monitor_runs_without_terminal_or_daemon_imports(self):
        script = '''
import importlib.abc, sys
class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('auton', 'dwho', 'httpdis', 'curses', 'argparse'):
            raise AssertionError(fullname)
sys.meta_path.insert(0, Block())
from auton_client.monitor import Monitor, FleetMonitor, snapshot, aggregate_snapshots
from auton_client.visibility import DaemonClient
class FakeClient:
    def health(self): return {'status': 'ok'}
    def jobs(self): return []
    def endpoints(self): return []
    def stats(self): return {}
c = FakeClient()
assert snapshot(c)['jobs'] == []
assert aggregate_snapshots(['one'], {'one': snapshot(c)})['stats']['jobs'] == 0
m = FleetMonitor({'one': c})
assert m.refresh(None)
m.close()
assert DaemonClient('http://localhost').uri == 'http://localhost'
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


class OperatorViewTests(unittest.TestCase):
    def setUp(self):
        self.monitor = Mock(clients={'one': fake_client(), 'two': fake_client()}, worker=None)
        self.monitor.poll.return_value = None
        self.view = OperatorView(self.monitor, clock=lambda: 1)
        self.view.cache['one'] = snapshot(fake_client())

    def test_endpoint_search_includes_published_description(self):
        self.view.view = 1
        self.view.cache['one']['endpoints'] = [{'name': 'test', 'description': 'Database checks'}]
        self.view.search = 'DATABASE'
        self.assertEqual(self.view.rows()[0]['name'], 'test')
        self.view.search = 'missing'
        self.assertEqual(self.view.rows(), [])

    def test_navigation_search_state_and_endpoint_filter(self):
        self.view.handle(ord('/'))
        for char in 'missing':
            self.view.handle(ord(char))
        self.view.handle(10)
        self.assertEqual(self.view.rows(), [])
        self.view.handle(ord('c'))
        self.view.handle(ord('s'))
        self.assertEqual(self.view.rows(), [])
        self.view.handle(ord('c'))
        self.view.handle(9)
        self.view.handle(10)
        self.assertEqual(self.view.endpoint, 'test')
        self.view.handle(10)
        self.assertEqual(self.view.selected_job(), ('test', JOB['uid']))
        self.view.handle(ord('v'))
        self.assertTrue(self.view.stderr)
        self.assertFalse(self.view.handle(ord('q')))

    def test_old_daemon_and_job_results_cannot_replace_current_output(self):
        self.view.handle(ord(']'))
        self.monitor.poll.return_value = ('one', None, {'detail': {'stream': ['secret']}})
        self.view.tick()
        self.assertNotIn('detail', self.view.data)
        self.monitor.poll.return_value = ('two', ('test', 'test:old-job'), {'detail': {'stream': ['wrong']}})
        self.view.tick()
        self.assertNotIn('detail', self.view.data)

    def test_small_screen_and_remote_control_characters(self):
        screen = Mock()
        screen.getmaxyx.return_value = (5, 20)
        self.view.draw(screen)
        self.assertIn('enlarge', screen.addnstr.call_args.args[2])
        self.assertEqual(safe_text('x\x1b[31m\x07\r'), 'x [31m  ')
        self.view.handle(10)
        self.view.cache['one']['detail'] = dict(JOB, stream=['a\x1b[31m\n' * 100], errors=[])
        screen.getmaxyx.return_value = (24, 80)
        self.view.draw(screen)
        self.assertTrue(all('\x1b' not in call.args[2] for call in screen.addnstr.call_args_list))

    def test_failover_uris_are_not_implicitly_daemon_targets(self):
        self.assertEqual(daemon_specs([], ['http://one']), {'default': 'http://one'})
        self.assertEqual(daemon_specs(['a=http://one', 'b=http://two'], []),
                         {'a': 'http://one', 'b': 'http://two'})
        for specs, uris in (([], []), ([], ['http://one', 'http://two']),
                            (['a=http://one'], ['http://two']),
                            (['a=http://one', 'a=http://two'], [])):
            with self.subTest(specs=specs), self.assertRaises(ValueError):
                daemon_specs(specs, uris)

    def test_tui_cli_rejects_execution_and_non_terminal(self):
        for args in (['--daemon', 'a=http://one'], ['--tui', '-a', 'dangerous'],
                     ['--tui', '--refresh', 'nan']):
            with patch.object(sys, 'argv', ['auton'] + args), self.assertRaises(SystemExit):
                client_module.argv_parse_check()
        result = subprocess.run([sys.executable, str(ROOT / 'bin/auton'), '--tui', '--uri', 'http://one'],
                                env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 1)
        self.assertIn('interactive terminal', result.stderr)

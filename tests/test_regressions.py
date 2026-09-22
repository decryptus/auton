import base64
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from requests import exceptions
from urllib3.exceptions import NewConnectionError

from auton.classes.exceptions import AutonTargetFailed, AutonTargetTimeout
from auton.classes.plugins import (AutonEPTObject, AutonEPTSync, ENDPOINTS,
                                    EPTS_SYNC, STATUS_COMPLETE)
from auton.classes.target import AutonTarget
from auton.modules.job import JobModule
from auton.plugins.subproc import AutonSubProcPlugin
from httpdis.ext.httpdis_json import HttpReqErrJson

ROOT = Path(__file__).resolve().parents[1]
loader = importlib.machinery.SourceFileLoader('auton_client', str(ROOT / 'bin/auton'))
spec = importlib.util.spec_from_loader(loader.name, loader)
client_module = importlib.util.module_from_spec(spec)
loader.exec_module(client_module)


class Request:
    def __init__(self, payload=None, user=None, offset=None, xid='test-job'):
        self.payload = payload or {}
        self.user, self.offset, self.xid = user, offset, xid

    def payload_params(self):
        return self.payload

    def get_server_vars(self):
        return {'HTTP_AUTH_USER': self.user}

    def get_headers(self):
        return {} if self.offset is None else {'X-Auton-Output-Offset': str(self.offset)}

    def query_params(self):
        return {'endpoint': 'test', 'id': self.xid}


def job(request=None):
    return AutonEPTObject('test', 'test:test-job', 'test', 'run', request or Request())


def response(data=None, status=200):
    res = Mock(status_code=status, text='json')
    res.json.return_value = data or {'code': 200, 'status': 'new'}
    if status >= 400:
        res.raise_for_status.side_effect = exceptions.HTTPError('rejected')
    return res


class ClientTests(unittest.TestCase):
    def setUp(self):
        with patch.object(sys, 'argv', ['auton', '--uri', 'http://one', '--uri', 'http://two',
                                        '--endpoint', 'test']):
            self.client = client_module.AutonClient(client_module.argv_parse_check())

    def test_first_success_stops_failover_and_closes_response(self):
        res = response()
        with patch.object(client_module.requests, 'post', return_value=res) as post:
            self.client.do_run()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(self.client.uri, 'http://one')
        self.assertEqual(post.call_args.kwargs['timeout'], 30)
        self.assertFalse(post.call_args.kwargs['allow_redirects'])
        res.close.assert_called_once()

    def test_connection_refused_allows_failover(self):
        error = exceptions.ConnectionError(NewConnectionError(None, 'refused'))
        with patch.object(client_module.requests, 'post', side_effect=[error, response()]) as post:
            self.client.do_run()
        self.assertEqual(post.call_count, 2)
        self.assertEqual(self.client.uri, 'http://two')

    def test_uncertain_post_never_retried(self):
        for error in (exceptions.ReadTimeout('read timeout'),
                      exceptions.ConnectionError('connection dropped')):
            with self.subTest(error=error), patch.object(client_module.requests, 'post', side_effect=error) as post:
                with self.assertRaises(type(error)):
                    self.client.do_run()
                self.assertEqual(post.call_count, 1)

    def test_http_errors_and_redirects_never_retried_and_are_closed(self):
        for status in (302, 401, 403, 500):
            res = response(status=status)
            with self.subTest(status=status), patch.object(client_module.requests, 'post', return_value=res) as post:
                with self.assertRaises(exceptions.HTTPError):
                    self.client.do_run()
                self.assertEqual(post.call_count, 1)
                res.close.assert_called_once()

    def test_initial_complete_response_is_printed(self):
        self.client.do_run = Mock(return_value={'code': 200, 'status': 'complete',
                                                'stream': ['initial'], 'return_code': 0,
                                                'next_offset': 1})
        with patch.object(sys, 'stdout', new_callable=io.StringIO) as output:
            self.assertEqual(self.client.do_autorun(), 0)
            self.assertEqual(output.getvalue(), 'initial')
        self.assertEqual(self.client.output_offset, 1)

    def test_initial_partial_response_is_printed_once(self):
        self.client.options.delay = 0
        self.client.do_run = Mock(return_value={'code': 200, 'status': 'processing', 'stream': ['a'], 'next_offset': 1})
        self.client.do_status = Mock(return_value={'code': 200, 'status': 'complete', 'stream': ['b'], 'next_offset': 2, 'return_code': 7})
        with patch.object(sys, 'stdout', new_callable=io.StringIO) as output:
            self.assertEqual(self.client.do_autorun(), 7)
            self.assertEqual(output.getvalue(), 'ab')

    def test_status_finds_job_on_second_server_and_sends_offset(self):
        missing = response(status=404)
        found = response()
        self.client.output_offset = 4
        with patch.object(client_module.requests, 'get', side_effect=[missing, found]) as get:
            self.client.do_status()
        self.assertEqual(self.client.uri, 'http://two')
        self.assertEqual(get.call_args.kwargs['headers']['X-Auton-Output-Offset'], '4')
        missing.close.assert_called_once()
        found.close.assert_called_once()


class SubprocessTests(unittest.TestCase):
    def setUp(self):
        self.plugin = AutonSubProcPlugin('test')
        self.plugin.target = AutonTarget('test', {'prog': sys.executable, 'timeout': 2})

    def tearDown(self):
        self.plugin.do_terminate()

    def execute(self, code, timeout=2):
        self.plugin.target = AutonTarget('test', {'prog': sys.executable, 'timeout': timeout})
        obj = job(Request({'args': ['-c', code]}))
        self.plugin.do_run(obj)
        return obj

    def test_client_arguments_are_literal_but_config_is_templated(self):
        values = {'_env_': {'SECRET': 'hidden'}, '_uid_': '123'}
        args = ['{"x": 1}', '{_env_[SECRET]}']
        self.assertEqual(self.plugin._mk_args(['echo'], ['{_uid_}'], args, values), ['echo', '123'] + args)

    def test_upload_paths_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            outside = str(Path(tmp) / 'outside')
            for filename in (outside, '../outside', '..', '.', 'a/b', 'a\\b', 'x\x00y'):
                with self.subTest(filename=filename), self.assertRaises(AutonTargetFailed):
                    self.plugin._mk_argfiles([], None, [{'arg': '-f', 'filename': filename, 'content': 'eA=='}])
            self.assertFalse(Path(outside).exists())

    def test_uploaded_file_roundtrip_and_cleanup(self):
        args = self.plugin._mk_argfiles([], None, [{'arg': '-f', 'filename': 'data.txt',
                                                   'content': base64.b64encode(b'hello').decode()}])
        self.assertEqual(Path(args[1]).read_bytes(), b'hello')
        self.plugin.do_terminate()
        self.assertFalse(Path(args[1]).exists())

    def test_output_unicode_partial_lines_stderr_and_invalid_utf8(self):
        obj = self.execute("import os; os.write(1, b'hello\\xe2\\x82\\xac\\xff'); os.write(2, b'warning')")
        self.assertEqual(''.join(obj.result), 'hello\u20ac\ufffd')
        self.assertEqual(''.join(obj.errors), 'warning')
        json.dumps(JobModule._build_result(obj, 0))

    def test_nonzero_exit_is_preserved(self):
        with self.assertRaises(AutonTargetFailed) as raised:
            self.execute('raise SystemExit(7)')
        self.assertEqual(raised.exception.code, 7)

    def test_timeout_kills_and_reaps_child(self):
        real_popen = subprocess.Popen
        processes = []
        def spawn(*args, **kwargs):
            proc = real_popen(*args, **kwargs)
            processes.append(proc)
            return proc
        with patch('auton.plugins.subproc.subprocess.Popen', side_effect=spawn):
            with self.assertRaises(AutonTargetTimeout) as raised:
                self.execute('import time; time.sleep(20)', timeout=0.15)
        self.assertEqual(raised.exception.code, 124)
        self.assertIsNotNone(processes[0].returncode)
        self.assertTrue(processes[0].stdout.closed)

    def test_descendant_cannot_outlive_timeout(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = str(Path(tmp) / 'survived')
            child = "import time,pathlib; time.sleep(0.6); pathlib.Path(%r).write_text('bad')" % marker
            code = 'import subprocess,sys,time; subprocess.Popen([sys.executable,"-c",%r]); time.sleep(20)' % child
            with self.assertRaises(AutonTargetTimeout):
                self.execute(code, timeout=0.2)
            time.sleep(0.7)
            self.assertFalse(Path(marker).exists())

    def test_closed_pipes_do_not_busy_spin(self):
        start = time.process_time()
        self.execute('import os,time; os.close(1); os.close(2); time.sleep(0.3)')
        self.assertLess(time.process_time() - start, 0.15)

    def test_output_limit_stops_process(self):
        obj = job(Request({'args': ['-c', 'import os; os.write(1, b"x" * 100000)']}))
        obj.max_output_bytes = 100
        with self.assertRaises(AutonTargetFailed):
            self.plugin.do_run(obj)
        self.assertLessEqual(obj.output_size, 100)

    def test_stop_interrupts_job(self):
        self.plugin.at_stop()
        with self.assertRaises(AutonTargetFailed) as raised:
            self.execute('import time; time.sleep(20)')
        self.assertEqual(raised.exception.code, 130)


class JobTests(unittest.TestCase):
    def setUp(self):
        self.module = JobModule()
        self.module.config = {'general': {'lock_timeout': 1}}
        self.module.safe_init(None)
        self.plugin = AutonSubProcPlugin('test')
        ENDPOINTS['test'] = self.plugin
        EPTS_SYNC['test'] = AutonEPTSync('test')

    def tearDown(self):
        self.plugin.at_stop()
        if self.plugin.is_alive():
            self.plugin.join(timeout=3)
        ENDPOINTS.pop('test', None)
        EPTS_SYNC.pop('test', None)

    def complete(self, obj):
        obj.set_return_code(0)
        obj.set_ended_at()
        obj.set_status(STATUS_COMPLETE)

    def test_completed_result_is_replayable_with_independent_offsets(self):
        self.module.job_run(Request(offset=0))
        obj = self.module.objs['test:test-job']
        obj.add_result('a'); obj.add_result('b')
        self.complete(obj)
        self.assertEqual(self.module.job_status(Request(offset=0))['stream'], ['a', 'b'])
        self.assertEqual(self.module.job_status(Request(offset=0))['stream'], ['a', 'b'])
        self.assertEqual(self.module.job_status(Request(offset=1))['stream'], ['b'])
        self.assertIn('test:test-job', self.module.objs)

    def test_expired_results_are_removed_but_pending_jobs_remain(self):
        self.module.job_run(Request())
        obj = self.module.objs['test:test-job']
        self.complete(obj)
        obj.ended_at = time.time() - 4000
        self.module.job_run(Request(xid='pending-job'))
        self.assertNotIn('test:test-job', self.module.objs)
        self.assertIn('test:pending-job', self.module.objs)

    def test_capacity_rejects_before_queueing(self):
        self.module.max_jobs = 1
        self.module.job_run(Request())
        with self.assertRaises(HttpReqErrJson):
            self.module.job_run(Request(xid='second-job'))
        self.assertEqual(EPTS_SYNC['test'].queue.qsize(), 1)

    def test_completed_cache_eviction_allows_new_jobs(self):
        self.module.max_jobs = 1
        self.module.job_run(Request())
        self.complete(self.module.objs['test:test-job'])
        self.module.job_run(Request(xid='second-job'))
        self.assertNotIn('test:test-job', self.module.objs)
        self.assertIn('test:second-job', self.module.objs)

    def test_duplicate_uid_never_queues_twice(self):
        self.module.job_run(Request())
        with self.assertRaises(HttpReqErrJson):
            self.module.job_run(Request())
        self.assertEqual(EPTS_SYNC['test'].queue.qsize(), 1)

    def test_endpoint_acl_checked_before_queueing(self):
        self.plugin.users = {'alice': True}
        with self.assertRaises(HttpReqErrJson):
            self.module.job_run(Request(user='bob'))
        self.assertEqual(EPTS_SYNC['test'].queue.qsize(), 0)

    def test_job_owner_checked_on_status(self):
        self.module.job_run(Request(user='alice'))
        with self.assertRaises(HttpReqErrJson):
            self.module.job_status(Request(user='bob'))

    def test_invalid_offset_does_not_enqueue_or_leak_lock(self):
        with self.assertRaises(HttpReqErrJson):
            self.module.job_run(Request(offset='-1'))
        self.assertEqual(EPTS_SYNC['test'].queue.qsize(), 0)
        self.module.job_run(Request(offset=0))

    def test_worker_survives_output_limit_and_processes_next_job(self):
        self.module.max_output_bytes = 100
        self.plugin.target = AutonTarget('test', {'prog': sys.executable, 'timeout': 2})
        self.module.job_run(Request({'args': ['-c', 'print("x" * 10000)']}))
        self.module.job_run(Request({'args': ['-c', 'print("ok")']}, xid='second-job'))
        self.plugin.start()
        deadline = time.monotonic() + 4
        second = self.module.objs['test:second-job']
        while second.status != STATUS_COMPLETE and time.monotonic() < deadline:
            time.sleep(0.02)
        self.assertEqual(second.status, STATUS_COMPLETE)
        self.assertEqual(''.join(second.result), 'ok\n')
        self.assertEqual(self.module.objs['test:test-job'].return_code, 1)
        self.assertIsNone(second.request)


if __name__ == '__main__':
    unittest.main()

"""Behavioral boundaries and compatibility of the extracted application layer."""
import gc
import importlib.abc
import os
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import weakref
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import Mock

from auton.classes.job import JobObject, STATUS_COMPLETE
from auton.classes.job_schema import InvalidArguments, InvalidArgumentsType
from auton.classes.jobs import (JobService, AccessDenied, DuplicateJob, JobUnavailable,
                                InvalidOffset, UnknownJob)
from auton.classes.plugins import AutonEPTObject, AutonEPTSync, EPTS_SYNC, ENDPOINTS
from auton.plugins.subproc import AutonSubProcPlugin
from auton.classes.target import AutonTarget
from auton_client import RemoteClient
from test_regressions import Request, response
from auton.modules.job import JobModule
from httpdis.ext.httpdis_json import HttpReqErrJson

ROOT = Path(__file__).resolve().parents[1]


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = 100
        self.queue = AutonEPTSync('test')
        self.endpoint = SimpleNamespace(users={'alice': True})
        self.service = JobService({'test': self.endpoint}, {'test': self.queue},
                                  clock=lambda: self.now, result_ttl=10, max_jobs=2)

    def submit(self, xid='one', payload=None, user='alice', offset=0):
        return self.service.submit('test', xid, payload or {}, user, offset)

    def finish(self, xid='one', output='done'):
        obj = self.service.objs['test:' + xid]
        obj.add_result(output)
        obj.set_return_code(0)
        obj.ended_at = self.now
        obj.set_status(STATUS_COMPLETE)
        return obj

    def test_direct_admission_detaches_input_and_retains_no_request(self):
        payload = {'args': ['original'], 'env': {'NAME': 'before'}}
        self.submit(payload=payload)
        payload['args'].append('changed')
        payload['env']['NAME'] = 'after'
        obj = self.queue.qget(timeout=1)
        self.assertEqual(obj.get_payload(), {'args': ['original'], 'env': {'NAME': 'before'}})
        self.assertEqual(obj.owner, 'alice')
        self.assertFalse(hasattr(obj, 'request'))

    def test_acl_and_ownership_apply_to_direct_callers(self):
        with self.assertRaises(AccessDenied):
            self.submit(user='bob')
        self.assertEqual(self.queue.queue.qsize(), 0)
        self.submit()
        self.endpoint.users = None
        with self.assertRaises(AccessDenied):
            self.service.status('test', 'one', 'bob')
        self.endpoint.users = {'alice': False}
        with self.assertRaises(AccessDenied):
            self.service.status('test', 'one', 'alice')

    def test_validation_cannot_be_bypassed_by_direct_calls(self):
        for payload in ({'args': [1]}, {'env': {'BAD\n': 'x'}}, {'argfiles': [{}]}):
            with self.subTest(payload=payload), self.assertRaises(InvalidArguments):
                self.submit(payload=payload)
        with self.assertRaises(InvalidArgumentsType):
            self.service.submit('test', 'one', ['not a mapping'], 'alice')
        for offset in (-1, True, 1.2, '1', 1000000000000):
            with self.subTest(offset=offset), self.assertRaises(InvalidOffset):
                self.submit(offset=offset)
        self.assertEqual(self.queue.queue.qsize(), 0)

    def test_replay_and_legacy_cursor(self):
        self.submit()
        obj = self.finish()
        obj.add_result('again')
        for _ in range(2):
            self.assertEqual(self.service.status('test', 'one', 'alice', 0)['stream'], ['done', 'again'])
        self.assertEqual(self.service.status('test', 'one', 'alice')['stream'], ['done', 'again'])
        self.assertEqual(self.service.status('test', 'one', 'alice')['stream'], [])

    def test_expiry_and_eviction_leave_pending_jobs_intact(self):
        self.submit()
        self.finish()
        self.submit('pending')
        self.now += 11
        with self.assertRaises(UnknownJob):
            self.service.status('test', 'one', 'alice')
        self.assertIn('test:pending', self.service.objs)
        self.submit('two')
        self.finish('two')
        self.submit('three')
        self.assertEqual(set(self.service.objs), {'test:pending', 'test:three'})

    def test_duplicate_completed_job_is_not_evicted(self):
        self.service.max_jobs = 1
        self.submit()
        original = self.finish()
        with self.assertRaises(DuplicateJob):
            self.submit()
        self.assertIs(self.service.objs['test:one'], original)
        self.assertEqual(self.queue.queue.qsize(), 1)

    def test_concurrent_admission_respects_capacity(self):
        self.service.max_jobs = 1
        barrier = threading.Barrier(2)
        def admit(xid):
            barrier.wait(timeout=2)
            try:
                self.submit(xid)
                return True
            except JobUnavailable:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sorted(pool.map(admit, ['one', 'two'])), [False, True])
        self.assertEqual(len(self.service.objs), 1)
        self.assertEqual(self.queue.queue.qsize(), 1)

    def test_queue_failure_cleans_admission_and_unlocks(self):
        self.queue.qput = Mock(side_effect=RuntimeError('unavailable'))
        with self.assertRaises(RuntimeError):
            self.submit()
        self.assertEqual(self.service.objs, {})
        self.queue.qput = Mock()
        self.submit()
        self.assertIn('test:one', self.service.objs)

    def test_lock_timeout_queues_nothing(self):
        self.service.lock = Mock()
        self.service.lock.acquire.return_value = False
        with self.assertRaises(JobUnavailable):
            self.submit()
        self.service.lock.release.assert_not_called()
        self.assertEqual(self.queue.queue.qsize(), 0)

    def test_instances_do_not_share_jobs_or_locks(self):
        other = JobService(self.service.endpoints, self.service.queues)
        self.submit()
        self.assertEqual(other.objs, {})
        self.assertIsNot(self.service.lock, other.lock)


class CompatibilityTests(unittest.TestCase):
    def test_legacy_constructor_snapshots_request_and_preserves_callback(self):
        request = Request({'args': ['x']}, user='alice', offset=0)
        reference = weakref.ref(request)
        callback = Mock()
        obj = AutonEPTObject('test', 'test:one', 'test', 'run', request, callback)
        request.payload['args'].append('changed')
        del request
        gc.collect()
        self.assertIsNone(reference())
        self.assertEqual(obj.get_request().payload_params(), {'args': ['x']})
        self.assertEqual(obj.get_request().get_server_vars()['HTTP_AUTH_USER'], 'alice')
        self.assertEqual(obj.get_request().get_headers()['X-Auton-Output-Offset'], '0')
        obj()
        callback.assert_called_once_with(obj)
        obj.clear_input()
        self.assertIsNone(obj.get_request())
        self.assertIsNone(obj.get_payload())

    def test_http_admission_releases_live_request_and_rejects_offset_newline(self):
        module = JobModule()
        module.config = {'general': {'lock_timeout': 1}}
        module.safe_init(None)
        ENDPOINTS['test'] = SimpleNamespace(users={'alice': True})
        EPTS_SYNC['test'] = AutonEPTSync('test')
        try:
            request = Request({'args': ['x']}, user='alice', offset=0)
            reference = weakref.ref(request)
            result = module.job_run(request)
            self.assertEqual(result['code'], 200)
            del request
            gc.collect()
            self.assertIsNone(reference())
            obj = module.objs['test:test-job']
            self.assertEqual(obj.get_payload(), {'args': ['x']})
            with self.assertRaises(HttpReqErrJson):
                module.job_run(Request(user='alice', offset='0\n', xid='other'))
            self.assertEqual(EPTS_SYNC['test'].queue.qsize(), 1)
        finally:
            ENDPOINTS.pop('test', None)
            EPTS_SYNC.pop('test', None)

    def test_worker_executes_neutral_object_and_rechecks_acl(self):
        plugin = AutonSubProcPlugin('boundary-test')
        plugin.users = {'alice': True}
        plugin.target = AutonTarget('boundary-test', {'prog': sys.executable, 'timeout': 2})
        queue = AutonEPTSync('boundary-test')
        EPTS_SYNC[plugin.name] = queue
        done = threading.Event()
        obj = JobObject(plugin.name, 'one', plugin.name, 'run',
                        payload={'args': ['-c', 'print("neutral")']}, principal='alice',
                        callback=lambda _: done.set())
        try:
            queue.qput(obj)
            plugin.start()
            self.assertTrue(done.wait(4))
            self.assertEqual(obj.return_code, 0)
            self.assertEqual(''.join(obj.result), 'neutral\n')
            self.assertIsNone(obj.get_payload())
            done.clear()
            plugin.users = {'alice': False}
            denied = JobObject(plugin.name, 'two', plugin.name, 'run',
                               payload={'args': ['-c', 'print("forbidden")']}, principal='alice',
                               callback=lambda _: done.set())
            queue.qput(denied)
            self.assertTrue(done.wait(4))
            self.assertEqual(denied.return_code, 1)
            self.assertEqual(denied.result, [])
        finally:
            plugin.at_stop()
            EPTS_SYNC.pop(plugin.name, None)


class RemoteTests(unittest.TestCase):
    def test_injected_session_stream_offsets_and_no_terminal_output(self):
        session = Mock()
        session.post.return_value = response({'code': 200, 'status': 'processing',
                                              'stream': ['a'], 'next_offset': 1})
        session.get.return_value = response({'code': 200, 'status': 'complete',
                                             'stream': ['b'], 'next_offset': 2, 'return_code': 7})
        sleep = Mock()
        payload = {'args': ['x']}
        client = RemoteClient(['http://test'], 'test', 'one', payload, session=session, sleep=sleep)
        payload['args'].append('changed')
        results = list(client.iter_results(delay=0.25))
        self.assertEqual([r['stream'] for r in results], [['a'], ['b']])
        self.assertEqual(client.output_offset, 2)
        self.assertEqual(session.post.call_args.kwargs['json'], {'args': ['x']})
        self.assertEqual(session.get.call_args.kwargs['headers']['X-Auton-Output-Offset'], '1')
        sleep.assert_called_once_with(0.25)
        self.assertEqual(session.post.call_count, 1)

    def test_invalid_delay_does_not_submit(self):
        session = Mock()
        client = RemoteClient(['http://test'], 'test', 'one', session=session)
        for delay in (-1, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                list(client.iter_results(delay))
        session.post.assert_not_called()


class ImportBoundaryTests(unittest.TestCase):
    def test_service_executes_with_interface_imports_blocked(self):
        script = r'''
import importlib.abc
import sys
class BlockInterfaces(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in ('httpdis', 'dwho', 'argparse', 'curses') or fullname.startswith('auton.modules'):
            raise AssertionError('interface import: ' + fullname)
sys.meta_path.insert(0, BlockInterfaces())
from types import SimpleNamespace
from auton.classes.jobs import JobService, UnknownJob, AccessDenied
from auton.classes.job import STATUS_COMPLETE
class FakeQueue:
    name = 'fake'
    def qput(self, obj):
        assert obj.owner == 'alice'
        obj.add_result(obj.get_payload()['args'][0])
        obj.set_return_code(0)
        obj.ended_at = 10
        obj.set_status(STATUS_COMPLETE)
        obj.clear_input()
now = [10]
service = JobService({'fake': SimpleNamespace(users={'alice': True})}, {'fake': FakeQueue()},
                     clock=lambda: now[0], result_ttl=5)
r = service.submit('fake', 'one', {'args': ['done']}, 'alice', 0)
assert r['stream'] == ['done'] and 'code' not in r
assert service.status('fake', 'one', 'alice', 0)['stream'] == ['done']
try:
    service.status('fake', 'one', 'bob')
except AccessDenied:
    pass
else:
    raise AssertionError('owner isolation missing')
now[0] = 16
try:
    service.status('fake', 'one', 'alice')
except UnknownJob:
    pass
else:
    raise AssertionError('expiry missing')
from auton_client import RemoteClient
assert RemoteClient
'''
        result = subprocess.run([sys.executable, '-c', script], cwd=ROOT,
                                env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

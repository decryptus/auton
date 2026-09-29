"""Atomic maintenance admission, queued launch and authenticated administration."""
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
import sys

from auton.classes.availability import Availability, MaintenanceActive, LaunchStopped
from auton.classes.jobs import JobService, AccessDenied
from auton.classes.plugins import AutonEPTSync
from auton_client.execution import ExecutionClient, ExecutionError
from auton_client.monitor import aggregate_snapshots
from auton.classes.job import JobObject
from auton.classes.target import AutonTarget
from auton.classes.exceptions import AutonTargetUnauthorized
from auton.plugins.subproc import AutonSubProcPlugin


class MaintenanceTests(unittest.TestCase):
    def service(self):
        return JobService({'test': SimpleNamespace(users={'alice': True})},
                          {'test': AutonEPTSync('test')}, maintenance_operators=['admin'])

    def test_refusal_preserves_capacity_and_requires_operator(self):
        service = self.service()
        service.submit('test', 'first', {}, 'alice')
        obj = service.objs['test:first']
        obj.set_status('complete')
        obj.set_ended_at()
        service.max_jobs = 1
        for principal in (None, 'alice', 'unknown'):
            with self.assertRaises(AccessDenied):
                service.set_maintenance(principal, True)
        service.set_maintenance('admin', True, 'Upgrade')
        self.assertEqual(service.health()['maintenance'], {'enabled': True, 'reason': 'Upgrade'})
        with self.assertRaises(MaintenanceActive):
            service.submit('test', 'second', {}, 'alice')
        self.assertEqual(list(service.objs), ['test:first'])
        self.assertEqual(len(service.list_jobs('alice')), 1)
        service.set_maintenance('admin', False)
        service.submit('test', 'second', {}, 'alice')
        self.assertTrue(service.health()['accepting_jobs'])

    def test_queued_jobs_remain_new_until_resume_and_running_jobs_continue(self):
        service = self.service()
        service.submit('test', 'first', {}, 'alice')
        obj = service.objs['test:first']
        service.set_maintenance('admin', True)
        launched, finished, release = threading.Event(), threading.Event(), threading.Event()
        def worker():
            with service.availability.launch(obj):
                launched.set()
            release.wait(2)
            finished.set()
        thread = threading.Thread(target=worker)
        thread.start()
        try:
            self.assertFalse(launched.wait(0.1))
            self.assertEqual(obj.get_status(), 'new')
            self.assertIsNone(obj.get_started_at())
            service.set_maintenance('admin', False)
            self.assertTrue(launched.wait(1))
            service.set_maintenance('admin', True)
            self.assertEqual(obj.get_status(), 'processing')
            release.set()
            self.assertTrue(finished.wait(1))
        finally:
            service.set_maintenance('admin', False)
            release.set()
            thread.join(2)

    def test_gate_serializes_transition_with_launch_and_shutdown_unblocks_wait(self):
        gate = Availability()
        entered, changed = threading.Event(), threading.Event()
        obj = Mock()
        def maintenance():
            entered.set()
            gate.set(True)
            changed.set()
        with gate.launch(obj):
            thread = threading.Thread(target=maintenance)
            thread.start()
            self.assertTrue(entered.wait(1))
            self.assertFalse(changed.wait(0.05))
        thread.join(1)
        self.assertTrue(changed.is_set())
        with self.assertRaises(LaunchStopped), gate.launch(Mock(), lambda: True):
            self.fail('launch must not proceed')

    def test_client_precheck_and_atomic_refusal_never_post_twice(self):
        for enabled in (True, False):
            session = Mock()
            session.get.return_value = Mock(status_code=200)
            session.get.return_value.json.return_value = {'status': 'ok', 'maintenance': {'enabled': enabled}}
            session.post.return_value = Mock(status_code=503)
            session.post.return_value.json.return_value = {'code': 503, 'message': 'daemon_maintenance'}
            session.post.return_value.headers = {'X-Auton-Admission': 'not-admitted'}
            client = ExecutionClient(['https://one'], 'test', 'test-job', session=session)
            with self.assertRaises(ExecutionError) as caught:
                client.do_run()
            self.assertTrue(caught.exception.rejected)
            self.assertEqual(session.post.call_count, 0 if enabled else 1)
            self.assertIn('maintenance', str(caught.exception))
        data = aggregate_snapshots(['one'], {'one': {'jobs': [], 'health': {
            'status': 'ok', 'maintenance': {'enabled': True, 'reason': 'Upgrade'}}}})
        self.assertEqual(data['daemons'][0]['state'], 'maintenance')
        self.assertFalse(data['partial'])

    def test_invalid_configuration_and_payloads_are_rejected(self):
        for enabled, reason in [(1, ''), (True, 1), (True, 'x' * 513)]:
            with self.assertRaises(ValueError):
                Availability(enabled, reason)
        with self.assertRaises(ValueError):
            JobService({}, {}, maintenance_operators='admin')

    def test_process_launch_rechecks_acl_after_maintenance(self):
        plugin = AutonSubProcPlugin('test')
        plugin.target = AutonTarget('test', {'prog': sys.executable, 'timeout': 2})
        plugin.users = {'alice': True}
        obj = JobObject('test', 'test:queued-job', 'test', 'run', principal='alice')
        obj.availability = Availability(True)
        entered = threading.Event()
        errors = []
        def run():
            entered.set()
            try:
                plugin.do_run(obj)
            except Exception as error:
                errors.append(error)
        with patch('auton.plugins.subproc.subprocess.Popen') as spawn:
            thread = threading.Thread(target=run)
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                plugin.users['alice'] = False
                obj.availability.set(False)
                thread.join(2)
                self.assertFalse(thread.is_alive())
                self.assertEqual(len(errors), 1)
                self.assertIsInstance(errors[0], AutonTargetUnauthorized)
                spawn.assert_not_called()
            finally:
                obj.availability.set(False)
                thread.join(2)

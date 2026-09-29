"""Read-only aggregation, bounded concurrency, and cross-daemon isolation."""
import threading
import time
import unittest
from unittest.mock import Mock

from auton_client.monitor import FleetMonitor, aggregate_snapshots, snapshot
from auton_client.tui import OperatorView
from auton_client.visibility import VisibilityError
from test_tui import fake_client, JOB


class FleetTests(unittest.TestCase):
    def collect(self, monitor, count):
        results = []
        deadline = time.monotonic() + 3
        while len(results) < count and time.monotonic() < deadline:
            result = monitor.poll()
            if result is not None:
                results.append(result)
            else:
                time.sleep(0.005)
        self.assertEqual(len(results), count)
        return results

    def test_duplicate_uids_are_kept_and_detail_is_pinned(self):
        one, two = fake_client(), fake_client()
        two.detail.return_value = dict(JOB, stream=['two only'], errors=[])
        monitor = FleetMonitor({'one': one, 'two': two})
        try:
            monitor.refresh(None, ('two', 'test', JOB['uid']))
            result = self.collect(monitor, 2)[-1][2]
            self.assertEqual([(job['daemon'], job['uid']) for job in result['jobs']],
                             [('one', JOB['uid']), ('two', JOB['uid'])])
            self.assertEqual(result['detail']['daemon'], 'two')
            self.assertEqual(result['detail']['stream'], ['two only'])
            one.detail.assert_not_called()
            two.detail.assert_called_once_with('test', JOB['uid'])
            self.assertEqual(result['stats']['jobs'], 2)
            self.assertFalse(result['partial'])
        finally:
            monitor.close()

    def test_partial_failure_does_not_hide_healthy_results_or_keep_stale_jobs(self):
        one, two = fake_client(), fake_client()
        monitor = FleetMonitor({'one': one, 'two': two})
        try:
            monitor.refresh(None)
            self.assertEqual(self.collect(monitor, 2)[-1][2]['stats']['jobs'], 2)
            for name in ('health', 'jobs', 'endpoints', 'stats'):
                getattr(two, name).side_effect = VisibilityError('HTTP 401')
            monitor.refresh(None)
            data = self.collect(monitor, 2)[-1][2]
            self.assertTrue(data['partial'])
            self.assertEqual(data['stats']['responding'], 1)
            self.assertEqual([job['daemon'] for job in data['jobs']], ['one'])
            self.assertEqual(data['daemons'][1]['state'], 'error')
            self.assertIsNone(data['daemons'][1]['jobs'])
            self.assertEqual(data['errors']['two/jobs'], 'HTTP 401')
        finally:
            monitor.close()

    def test_fast_daemon_is_delivered_before_slow_daemon_and_concurrency_is_bounded(self):
        entered, release = threading.Event(), threading.Event()
        slow, fast, queued = fake_client(), fake_client(), fake_client()
        def wait_health():
            entered.set()
            release.wait(3)
            return {'status': 'ok'}
        slow.health.side_effect = wait_health
        monitor = FleetMonitor({'slow': slow, 'fast': fast, 'queued': queued}, max_workers=2)
        try:
            monitor.refresh(None)
            self.assertTrue(entered.wait(1))
            queued.health.assert_not_called()
            first = self.collect(monitor, 1)[0][2]
            self.assertEqual(first['jobs'][0]['daemon'], 'fast')
            self.assertEqual(first['daemons'][0]['state'], 'pending')
            self.assertLessEqual(len(monitor.active), 2)
            self.assertFalse(monitor.refresh(None))
            release.set()
            final = self.collect(monitor, 2)[-1][2]
            self.assertFalse(final['partial'])
        finally:
            release.set()
            monitor.close()
            for item in monitor.monitors.values():
                if item.worker:
                    item.worker.join(2)

    def test_single_selection_never_contacts_other_daemons_and_close_drops_pending(self):
        one, two = fake_client(), fake_client()
        monitor = FleetMonitor({'one': one, 'two': two}, max_workers=1)
        monitor.refresh('one')
        self.assertEqual(self.collect(monitor, 1)[0][0], 'one')
        two.health.assert_not_called()
        monitor.refresh(None)
        monitor.close()
        self.assertFalse(monitor.refresh(None))
        self.assertEqual(list(monitor.pending), [])
        two.health.assert_not_called()
        for item in monitor.monitors.values():
            if item.worker:
                item.worker.join(2)

    def test_aggregation_does_not_mutate_snapshots(self):
        raw = snapshot(fake_client())
        result = aggregate_snapshots(['one', 'two'], {'one': raw})
        self.assertNotIn('daemon', raw['jobs'][0])
        self.assertEqual(result['stats']['responding'], 1)
        self.assertEqual(result['stats']['total'], 2)
        self.assertTrue(result['partial'])


class FleetViewTests(unittest.TestCase):
    def setUp(self):
        self.monitor = Mock(clients={'one': fake_client(), 'two': fake_client()}, worker=None)
        self.monitor.poll.return_value = None
        self.view = OperatorView(self.monitor, clock=lambda: 1)
        self.view.handle(ord('a'))
        self.raw = {'one': snapshot(fake_client()), 'two': snapshot(fake_client())}
        self.view.cache[None] = aggregate_snapshots(['one', 'two'], self.raw)

    def test_identity_is_preserved_when_duplicate_jobs_arrive_incrementally(self):
        self.view.index = 1
        self.view.handle(10)
        target = ('two', 'test', JOB['uid'])
        self.assertEqual(self.view.selected_job(), target)
        partial = aggregate_snapshots(['one', 'two'], {'one': self.raw['one']}, target)
        self.monitor.poll.return_value = (None, target, partial)
        self.view.tick()
        self.assertEqual(self.view.selected_job(), target)
        self.assertNotIn('detail', self.view.data)
        self.raw['two']['detail'] = dict(JOB, stream=['two only'])
        final = aggregate_snapshots(['one', 'two'], self.raw, target)
        self.monitor.poll.return_value = (None, target, final)
        self.view.tick()
        self.assertEqual(self.view.data['detail']['daemon'], 'two')
        self.view.handle(27)
        self.view.index = 0
        self.view.handle(10)
        self.assertEqual(self.view.selected_job(), ('one', 'test', JOB['uid']))

    def test_endpoint_filter_is_scoped_to_its_daemon(self):
        self.view.handle(9)
        self.view.index = 1
        self.view.handle(10)
        self.assertEqual([job['daemon'] for job in self.view.rows()], ['two'])
        self.view.handle(ord('c'))
        self.assertEqual(len(self.view.rows()), 2)

    def test_daemon_table_and_all_mode_do_not_reinterpret_execution_uris(self):
        for _ in range(3):
            self.view.handle(9)
        self.assertEqual(self.view.view, 3)
        self.assertEqual([row['name'] for row in self.view.rows()], ['one', 'two'])
        self.view.index = 1
        self.view.handle(10)
        self.assertEqual(self.view.daemon, 'two')
        self.assertEqual(self.view.view, 0)

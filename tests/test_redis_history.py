"""Real Redis acceptance runs in the dedicated CI service, never against a default server."""
import os
import time
import unittest
import uuid

from auton.classes.job import JobObject
from auton.classes.job_store import JobStoreUnavailable, snapshot, history_config
from auton.classes.job_redis import RedisJobStore, redis_settings

REDIS_URL = os.environ.get('AUTON_TEST_REDIS_URL')


class RedisConfigurationTests(unittest.TestCase):
    def test_explicit_namespace_and_bounded_timeouts(self):
        for cfg in ({'url': 'redis://localhost'}, {'url': 'http://localhost', 'namespace': 'one'},
                    {'url': 'redis://localhost', 'namespace': 'one', 'timeout': 6}):
            with self.assertRaises(ValueError):
                redis_settings(cfg)
        config = history_config({'job_storage': {'backend': 'redis', 'url': 'redis://localhost/1',
                                                 'namespace': 'one'}})
        self.assertEqual(config['backend'], 'redis')
        self.assertIn('socket_timeout=3', config['url'])


@unittest.skipUnless(REDIS_URL, 'requires explicit AUTON_TEST_REDIS_URL; exercised by redis-history CI job')
class RedisHistoryTests(unittest.TestCase):
    def setUp(self):
        self.namespace = 'test-' + uuid.uuid4().hex
        self.store = RedisJobStore(REDIS_URL, self.namespace)
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.store.close()
        self.store.conn.delete(*self.store.keys)
        self.store.adapter.disconnect()

    def test_single_owner_and_uncertain_recovery_without_replay(self):
        with self.assertRaises(JobStoreUnavailable):
            RedisJobStore(REDIS_URL, self.namespace)
        obj = JobObject('test', 'test:job-1234', 'test', 'run', principal='alice')
        obj.admitted_at = time.time()
        obj.set_started_at()
        obj.set_status('processing')
        self.store.save(snapshot(obj))
        self.store.close()
        self.store = RedisJobStore(REDIS_URL, self.namespace)
        rows = self.store.recover(time.time(), 3600, 128)
        self.assertEqual(len(rows), 1)
        self.assertTrue(rows[0]['execution_uncertain'])
        self.assertEqual(rows[0]['outcome'], 'job.interrupted')
        self.assertEqual(rows[0]['status'], 'complete')
        self.store.delete(rows[0]['uid'])
        self.assertEqual(self.store.recover(time.time(), 3600, 128), [])

    def test_lost_lease_fences_writes_and_cannot_release_another_owner(self):
        obj = JobObject('test', 'test:job-1234', 'test', 'run', principal='alice')
        obj.admitted_at = time.time()
        self.store.conn.set(self.store.keys[0], 'another-owner', ex=30)
        with self.assertRaises(JobStoreUnavailable):
            self.store.save(snapshot(obj))
        self.assertTrue(self.store.failed)
        self.store.close()
        self.assertEqual(self.store.conn.get(self.store.keys[0]), 'another-owner')

"""Idle-daemon retention deletes only expired terminal results and stops cleanly."""
import tempfile
import time
import unittest
from pathlib import Path
from auton.classes.job import JobObject, STATUS_COMPLETE
from auton.classes.jobs import JobService, JobUnavailable
from auton.classes.job_store import SQLiteJobStore
from auton.classes.retention import ResultRetention


class RetentionTests(unittest.TestCase):
    def test_periodic_expiry_removes_durable_record_without_http_reads(self):
        with tempfile.TemporaryDirectory() as directory:
            store = SQLiteJobStore(str(Path(directory) / 'jobs.db'))
            self.addCleanup(store.close)
            service = JobService({}, {}, store=store, result_ttl=0.05)
            completed = JobObject('test', 'test:complete', 'test', 'run', principal='owner')
            completed.set_return_code(0)
            completed.set_ended_at()
            completed.set_status(STATUS_COMPLETE)
            service.objs[completed.uid] = completed
            service._save_job(completed)
            active = JobObject('test', 'test:active', 'test', 'run', principal='owner')
            service.objs[active.uid] = active
            worker = ResultRetention(service).start()
            try:
                deadline = time.monotonic() + 2
                while completed.uid in service.objs and time.monotonic() < deadline:
                    time.sleep(0.02)
                self.assertNotIn(completed.uid, service.objs)
                self.assertIn(active.uid, service.objs)
                self.assertEqual(store.recover(time.time(), 3600, 128), [])
            finally:
                worker.close()
            self.assertFalse(worker.thread.is_alive())

    def test_deletion_failure_retains_evidence_and_degrades_admission(self):
        class Store:
            def recover(self, *args): return []
            def delete(self, uid): raise OSError('unavailable')
        service = JobService({}, {}, store=Store(), clock=lambda: 100, result_ttl=1)
        obj = JobObject('test', 'test:complete', 'test', 'run')
        obj.set_return_code(0)
        obj.set_status(STATUS_COMPLETE)
        obj.ended_at = 1
        service.objs[obj.uid] = obj
        with self.assertRaises(JobUnavailable):
            ResultRetention(service).sweep()
        self.assertIn(obj.uid, service.objs)
        self.assertTrue(service.persistence_failed)

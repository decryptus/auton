"""JSONL lifecycle privacy, rotation, concurrency and real worker events."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import stat
import sys
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from auton.classes.journal import JSONLJournal
from auton.classes.jobs import JobService, AccessDenied, DuplicateJob
from auton.classes.plugins import AutonEPTSync, EPTS_SYNC
from auton.classes.target import AutonTarget
from auton.plugins.subproc import AutonSubProcPlugin


class JournalTests(unittest.TestCase):
    def test_record_is_one_line_and_whitelisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jobs.jsonl'
            journal = JSONLJournal(str(path), clock=lambda: 0)
            journal.record('job.completed', uid='endpoint:line\nbreak', endpoint='endpoint',
                           principal='alice', return_code=0, duration_ms=12.3456)
            rows = path.read_text().splitlines()
            self.assertEqual(len(rows), 1)
            row = json.loads(rows[0])
            self.assertEqual(row['job_id'], 'endpoint:line\nbreak')
            self.assertEqual(row['timestamp'], '1970-01-01T00:00:00.000+00:00')
            self.assertEqual(row['duration_ms'], 12.346)
            self.assertEqual(set(row), {'schema_version', 'timestamp', 'event', 'job_id',
                                       'endpoint', 'principal', 'reason', 'return_code', 'duration_ms', 'execution_id'})
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_concurrent_records_rotate_with_bounded_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jobs.jsonl'
            journal = JSONLJournal(str(path), max_bytes=16384, backup_count=2)
            def write(index):
                journal.record('job.started', uid=str(index), principal='alice')
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(write, range(400)))
            files = list(Path(tmp).glob('jobs.jsonl*'))
            self.assertEqual(len(files), 3)
            self.assertTrue(all(file.stat().st_size <= 16384 for file in files))
            records = [json.loads(line) for file in files for line in file.read_text().splitlines()]
            ids = [row['job_id'] for row in records]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(journal.failures, 0)

    def test_long_fields_are_bounded_and_marked(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'jobs.jsonl'
            value = '\U0001f600' * 10000
            JSONLJournal(str(path), max_bytes=16384).record(
                'job.rejected', uid=value, endpoint=value, principal=value, reason=value)
            self.assertLess(path.stat().st_size, 16384)
            row = json.loads(path.read_text())
            self.assertEqual(len(row['job_id']), 256)
            self.assertEqual(len(row['truncated_fields']), 4)

    def test_write_failure_is_visible_rate_limited_and_does_not_raise(self):
        with tempfile.TemporaryDirectory() as tmp:
            journal = JSONLJournal(str(Path(tmp) / 'missing' / 'jobs.jsonl'))
            with self.assertLogs('auton.journal', level='ERROR') as logs:
                journal.record('job.started')
                journal.record('job.completed')
            self.assertEqual(len(logs.output), 1)
            self.assertEqual(journal.failures, 2)

    def test_symlink_is_not_followed_and_invalid_limits_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / 'target'
            target.write_text('untouched')
            link = Path(tmp) / 'link'
            link.symlink_to(target)
            with self.assertLogs('auton.journal', level='ERROR'):
                JSONLJournal(str(link)).record('job.started')
            self.assertEqual(target.read_text(), 'untouched')
            for options in ({'max_bytes': 0}, {'backup_count': 0}, {'backup_count': True}):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    JSONLJournal(str(target), **options)
            with self.assertRaises(ValueError):
                JSONLJournal('relative.jsonl')

    def test_admission_rejections_and_failing_injected_adapter(self):
        sink = Mock()
        queue = AutonEPTSync('test')
        service = JobService({'test': SimpleNamespace(users={'alice': True})}, {'test': queue}, journal=sink)
        with self.assertRaises(AccessDenied):
            service.submit('test', 'one', {}, 'bob')
        self.assertEqual(sink.record.call_args.args, ('job.rejected',))
        self.assertEqual(sink.record.call_args.kwargs['reason'], 'AccessDenied')
        service.submit('test', 'one', {}, 'alice')
        with self.assertRaises(DuplicateJob):
            service.submit('test', 'one', {}, 'alice')
        sink.record.side_effect = OSError('secret path')
        with self.assertLogs('auton.journal', level='ERROR') as logs:
            service.submit('test', 'two', {}, 'alice')
        self.assertNotIn('secret path', ''.join(logs.output))
        self.assertEqual(queue.queue.qsize(), 2)

    def test_real_worker_success_failure_timeout_and_acl_rejection(self):
        for label, code, expected, denied in (
                ('success', 'print("TOP_SECRET_OUTPUT")', 'job.completed', False),
                ('failure', 'raise SystemExit(3)', 'job.failed', False),
                ('timeout', 'import time; time.sleep(1)', 'job.timeout', False),
                ('denied', 'print("must not run")', 'job.rejected', True)):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / 'jobs.jsonl'
                plugin = AutonSubProcPlugin('journal-test')
                plugin.users = {'alice': True}
                plugin.target = AutonTarget(plugin.name, {'prog': sys.executable, 'timeout': 0.2})
                queue = AutonEPTSync(plugin.name)
                EPTS_SYNC[plugin.name] = queue
                service = JobService({plugin.name: plugin}, {plugin.name: queue}, journal=JSONLJournal(str(path)))
                done = threading.Event()
                try:
                    service.submit(plugin.name, 'one', {'args': ['-c', code],
                                                        'env': {'SECRET': 'TOP_SECRET_ENV'}}, 'alice', 0)
                    obj = service.objs[plugin.name + ':one']
                    obj.callback = lambda _: done.set()
                    if denied:
                        plugin.users = {'alice': False}
                    plugin.start()
                    self.assertTrue(done.wait(3))
                    text = path.read_text()
                    rows = [json.loads(line) for line in text.splitlines()]
                    self.assertEqual([row['event'] for row in rows],
                                     ['job.admitted'] + ([] if denied else ['job.started']) + [expected])
                    self.assertNotIn('TOP_SECRET', text)
                    self.assertNotIn('payload', text)
                    self.assertEqual({row['execution_id'] for row in rows}, {obj.execution_id})
                    if not denied:
                        self.assertGreaterEqual(rows[-1]['duration_ms'], 0)
                    else:
                        self.assertEqual(rows[-1]['reason'], 'execution_acl')
                    before = len(rows)
                    service.list_jobs('alice')
                    self.assertEqual(len(path.read_text().splitlines()), before)
                finally:
                    plugin.at_stop()
                    EPTS_SYNC.pop(plugin.name, None)

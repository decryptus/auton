"""Live observation never changes submission semantics; exports preserve evidence."""
import json
from pathlib import Path
import stat
import tempfile
import threading
import unittest
from unittest.mock import Mock

from auton_client.export import export_result
from auton_client.operations import OperationService
from auton_client.progress import OperationProgress
from auton_client.scenarios import ScenarioService
from auton_client.session import ExecutionSession
from test_scenarios import factory_with, scenario


class ProgressTests(unittest.TestCase):
    def test_live_states_are_visible_before_completion_with_one_submission(self):
        entered, release = threading.Event(), threading.Event()
        client = Mock(output_offset=0)
        def factory(uris, endpoint, uid, **kwargs):
            def data(state):
                return {'uid': endpoint + ':' + uid, 'status': state,
                        'next_offset': 0, 'stream': [], 'return_code': 0}
            client.do_run.return_value = data('new')
            def observe():
                entered.set()
                release.wait(2)
                return data('complete')
            client.do_status.side_effect = observe
            return client
        session = ExecutionSession()
        try:
            session.start(OperationService({'one': 'http://one'}, 'check', client_factory=factory, delay=0))
            self.assertTrue(entered.wait(1))
            first = session.progress.snapshot()
            self.assertEqual(first['rows'][0]['status'], 'queued')
            self.assertTrue(first['rows'][0]['job_id'])
            self.assertFalse(session.future.done())
            first['rows'][0]['status'] = 'tampered'
            self.assertEqual(session.progress.snapshot()['rows'][0]['status'], 'queued')
            release.set()
            result = session.future.result(timeout=2)
            final = session.progress.snapshot()
            self.assertEqual(final['status'], 'completed')
            self.assertEqual(final['rows'][0]['job_id'], result['targets'][0]['job_id'])
            self.assertNotIn('stdout', final['rows'][0])
            self.assertGreater(final['revision'], first['revision'])
            client.do_run.assert_called_once()
        finally:
            release.set()
            session.close()

    def test_unknown_skips_later_steps_without_replay_on_only_affected_target(self):
        calls, progress = [], OperationProgress()
        service = ScenarioService({'one': 'http://one', 'two': 'http://two'},
            {'deploy': scenario('check', 'apply')},
            client_factory=factory_with(calls, ('http://one', 'check', 'unknown')))
        result = service.run(progress=progress)
        rows = progress.snapshot()['rows']
        self.assertEqual([r['status'] for r in rows], ['unknown', 'skipped', 'completed', 'completed'])
        self.assertEqual(len(calls), 3)
        self.assertIsNone(rows[1]['job_id'])
        self.assertEqual(progress.snapshot()['status'], result['status'])

    def test_snapshot_resets_between_operations_and_tracks_rejection_and_stop(self):
        from auton_client.execution import ExecutionError
        progress = OperationProgress()
        factory = Mock()
        factory.return_value.do_run.side_effect = ExecutionError('refused', rejected=True)
        service = OperationService({'one': 'http://one'}, 'check', client_factory=factory)
        service.run('first', progress=progress)
        self.assertEqual(progress.snapshot()['rows'][0]['status'], 'rejected')
        stopped = threading.Event()
        stopped.set()
        service.run('second', stopped=stopped, progress=progress)
        self.assertEqual(progress.snapshot()['operation_id'], 'second')
        self.assertEqual(progress.snapshot()['rows'][0]['status'], 'not_submitted')
        factory.return_value.do_run.assert_called_once()

    def test_running_and_pending_steps_are_visible_before_final_results(self):
        entered, release = threading.Event(), threading.Event()
        calls = []
        base = factory_with(calls)
        session = ExecutionSession()
        def factory(uris, endpoint, uid, **kwargs):
            client = base(uris, endpoint, uid, **kwargs)
            if uris == ['http://one'] and endpoint == 'check':
                client.do_run.return_value = None
                def submit():
                    calls.append((uris[0], endpoint, uid, kwargs['payload']))
                    return {'uid': endpoint + ':' + uid, 'status': 'processing', 'next_offset': 0}
                client.do_run.side_effect = submit
                def observe():
                    entered.set()
                    release.wait(2)
                    return {'uid': endpoint + ':' + uid, 'status': 'complete', 'next_offset': 0, 'return_code': 0}
                client.do_status.side_effect = observe
            return client
        try:
            session.start(ScenarioService({'one': 'http://one', 'two': 'http://two'},
                {'deploy': scenario('check', 'apply')}, client_factory=factory, delay=0))
            self.assertTrue(entered.wait(1))
            rows = session.progress.snapshot()['rows']
            self.assertEqual([r['status'] for r in rows[:2]], ['running', 'pending'])
            release.set()
            session.future.result(timeout=2)
            self.assertEqual(len(calls), 4)
        finally:
            release.set()
            session.close()


class ExportTests(unittest.TestCase):
    def test_private_export_preserves_full_result_and_refuses_existing_file_and_symlink(self):
        result = {'operation_id': 'op', 'status': 'incomplete', 'targets': [
            {'status': 'unknown', 'job_id': 'job', 'stdout': ['évidence'], 'stderr': ['diagnostic']}]}
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            export_result(result, path)
            self.assertEqual(json.loads(path.read_text()), result)
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
            with self.assertRaises(FileExistsError):
                export_result({'operation_id': 'other'}, path)
            link = Path(directory) / 'link.json'
            link.symlink_to(path)
            with self.assertRaises(FileExistsError):
                export_result(result, link)
            self.assertEqual(json.loads(path.read_text()), result)

    def test_failed_serialization_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'result.json'
            with self.assertRaises(ValueError):
                export_result({'operation_id': 'op', 'value': float('nan')}, path)
            self.assertFalse(path.exists())

"""Reconciliation must preserve uncertainty and never execute saved reports."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from auton_client.reconcile import ReconciliationService, load_report
from auton_client.visibility import VisibilityError


class ReconciliationTests(unittest.TestCase):
    def report(self):
        return {'operation_id': 'saved', 'endpoint': 'check', 'status': 'incomplete',
                'targets': [{'target': 'node', 'uri': 'http://127.0.0.1:1234',
                             'job_id': 'job-1', 'uid': 'check:job-1', 'status': 'unknown',
                             'stdout': ['old'], 'stderr': [], 'return_code': None}]}

    def service(self, report=None, **kwargs):
        return ReconciliationService(report or self.report(),
            {'node': 'http://127.0.0.1:1234'}, **kwargs)

    def test_read_only_snapshot_and_repeat_do_not_accumulate_output(self):
        calls = []
        class Reader:
            def __init__(self, uri, **kwargs):
                calls.append(uri)
            def detail(self, endpoint, uid):
                calls.append((endpoint, uid))
                return {'status': 'complete', 'return_code': 0, 'stream': ['new'], 'errors': []}
        report = self.report()
        report['targets'][0]['attempts'] = [{'uri': report['targets'][0]['uri'], 'status': 'accepted', 'reason': None}]
        original = copy.deepcopy(report)
        service = self.service(report, client_factory=Reader)
        result = service.run()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(result['targets'][0]['stdout'], ['new'])
        self.assertEqual(service.run(), result)
        self.assertEqual(report, original)
        self.assertEqual(calls, ['http://127.0.0.1:1234', ('check', 'check:job-1')] * 2)

    def test_untrusted_origin_and_malformed_late_target_fail_before_io(self):
        calls = []
        report = self.report()
        report['targets'][0]['uri'] = 'https://attacker.example'
        with self.assertRaisesRegex(ValueError, 'outside'):
            self.service(report, client_factory=lambda *a, **kw: calls.append(a))
        report = self.report()
        report['targets'].append({'target': 'missing'})
        with self.assertRaises(ValueError):
            self.service(report, client_factory=lambda *a, **kw: calls.append(a))
        report = self.report()
        report['targets'][0]['attempts'] = ['malformed']
        with self.assertRaises(ValueError):
            self.service(report, client_factory=lambda *a, **kw: calls.append(a))
        self.assertEqual(calls, [])

    def test_missing_job_keeps_evidence_and_uncertainty(self):
        class Reader:
            def __init__(self, *args, **kwargs):
                pass
            def detail(self, *args):
                raise VisibilityError('HTTP 404')
        result = self.service(client_factory=Reader).run()
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['targets'][0]['stdout'], ['old'])
        self.assertEqual(result['targets'][0]['error'], 'HTTP 404')

    def test_scenario_skipped_steps_remain_skipped_even_if_prior_job_succeeded(self):
        class Reader:
            def __init__(self, *args, **kwargs):
                pass
            def detail(self, *args):
                return {'status': 'complete', 'return_code': 0}
        report = self.report()
        step = dict(report['targets'][0], endpoint='check', step='first')
        report.update(kind='scenario')
        report['targets'] = [{'target': 'node', 'scenarios': [{'name': 'sequence', 'steps': [step,
            {'step': 'second', 'endpoint': 'check', 'status': 'skipped', 'job_id': None}]}]}]
        result = self.service(report, client_factory=Reader).run()
        self.assertEqual(result['status'], 'incomplete')
        self.assertEqual(result['targets'][0]['scenarios'][0]['steps'][1]['status'], 'skipped')

    def test_deadline_never_contacts_later_jobs(self):
        clock = iter([0, 301])
        calls = []
        result = self.service(clock=lambda: next(clock),
                              client_factory=lambda *a, **kw: calls.append(a)).run()
        self.assertEqual(calls, [])
        self.assertIn('deadline', result['targets'][0]['error'])

    def test_duplicate_keys_and_oversized_input_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'report.json'
            path.write_text('{"operation_id":"a","operation_id":"b"}')
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                load_report(path)
            path.write_bytes(b' ' * (16 * 1024 * 1024 + 1))
            with self.assertRaisesRegex(ValueError, 'limit'):
                load_report(path)

    def test_real_cli_reads_existing_job_without_resubmission(self):
        import os
        import subprocess
        import sys
        from web_fixture import WebDaemon
        import requests
        daemon = WebDaemon()
        self.addCleanup(daemon.close)
        daemon.start()
        headers = {'Authorization': 'Bearer ' + daemon.token}
        accepted = requests.post(daemon.uri + '/run/diagnostic/saved-report', headers=headers,
            json={'args': ['-c', 'print("reconciled output")']}, timeout=3)
        self.assertEqual(accepted.status_code, 200, accepted.text)
        report = self.report()
        report['endpoint'] = 'diagnostic'
        report['targets'][0].update(uri=daemon.uri, job_id='saved-report', uid='diagnostic:saved-report')
        path = daemon.path / 'saved.json'
        path.write_text(json.dumps(report))
        token = daemon.path / 'read.token'
        token.write_text(daemon.token)
        token.chmod(0o600)
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'bin/auton'), '--reconcile', str(path),
            '--target', 'node=' + daemon.uri, '-k', str(token)], capture_output=True,
            text=True, timeout=10, env={**{k: v for k, v in os.environ.items()
                if not k.startswith('AUTON_')}, 'PYTHONPATH': str(root)})
        self.assertIn(result.returncode, (0, 1), result.stderr)
        saved = json.loads(result.stdout)
        self.assertTrue(saved['reconciled'])
        jobs = requests.get(daemon.uri + '/jobs', headers=headers, timeout=3).json()['jobs']
        self.assertEqual([job['uid'] for job in jobs], ['diagnostic:saved-report'])
        self.assertEqual(json.loads(path.read_text()), report)

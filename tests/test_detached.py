"""Unattended worker outlives its launcher and leaves read-only recovery evidence."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import requests
import yaml

from auton_client.detached import ObservationWriter, DurableProgress
from web_fixture import WebDaemon


class DetachedTests(unittest.TestCase):
    def test_checkpoint_precedes_submission_and_never_contains_inputs(self):
        with tempfile.TemporaryDirectory() as parent:
            path = Path(parent) / 'new'
            writer = ObservationWriter(path)
            self.addCleanup(writer.close)
            stopped = threading.Event()
            progress = DurableProgress(writer, stopped)
            progress.begin('operation', [('node', {'target': 'node', 'endpoint': 'echo',
                'scenario': '', 'step': '', 'uri': 'http://localhost'})])
            progress.update('node', {'status': 'submitting', 'job_id': 'job-1234',
                                    'payload': {'token': 'MUST-NOT-APPEAR'}})
            report = json.loads((path / 'observation.json').read_text())
            self.assertEqual(report['targets'][0]['status'], 'unknown')
            self.assertEqual(report['targets'][0]['uid'], 'echo:job-1234')
            self.assertNotIn('MUST-NOT-APPEAR', json.dumps(report))
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            self.assertEqual((path / 'observation.json').stat().st_mode & 0o777, 0o600)
            with self.assertRaises(FileExistsError):
                ObservationWriter(path)

    def test_real_scenario_finishes_after_cli_launcher_exits(self):
        self._check_detached_scenario(False)

    def test_real_scenario_with_closed_standard_input(self):
        self._check_detached_scenario(True)

    def _check_detached_scenario(self, close_stdin):
        daemon = WebDaemon()
        self.addCleanup(daemon.close)
        daemon.start()
        token = daemon.path / 'worker.token'
        token.write_text(daemon.token)
        token.chmod(0o600)
        config = daemon.path / 'client.yml'
        config.write_text(yaml.safe_dump({'targets': {'node': daemon.uri}, 'scenarios': {
            'checks': {'version': 1, 'steps': [
                {'name': 'first', 'endpoint': 'diagnostic', 'args': ['-c', 'import time; time.sleep(1); print("first")']},
                {'name': 'second', 'endpoint': 'diagnostic', 'args': ['-c', 'print("second")']} ]}}}))
        directory = daemon.path / 'observations'
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, str(root / 'bin/auton'), '-c', str(config),
            '-t', 'node', '-s', 'checks', '-k', str(token), '--detach-dir', str(directory),
            '--operation-timeout', '5'], capture_output=True, text=True, timeout=3,
            preexec_fn=(lambda: os.close(0)) if close_stdin else None,
            env={**{k: v for k, v in os.environ.items() if not k.startswith('AUTON_')}, 'PYTHONPATH': str(root)})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['status'], 'detached')
        report = None
        for _ in range(160):
            report = json.loads((directory / 'observation.json').read_text())
            if report.get('checkpoint') is False:
                break
            time.sleep(0.05)
        self.assertFalse(report['checkpoint'], report)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual([s['stdout'] for s in report['targets'][0]['scenarios'][0]['steps']],
                         [['first\n'], ['second\n']])
        jobs = requests.get(daemon.uri + '/jobs', headers={'Authorization': 'Bearer ' + daemon.token}, timeout=3).json()['jobs']
        self.assertEqual(len(jobs), 2)

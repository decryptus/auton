"""Cancellation permissions, queued launch races and real process cleanup."""
from pathlib import Path
import time
import unittest

import requests

from auton_client.execution import cancel_job
from auton_client.credentials import BearerCredentials
from web_fixture import WebDaemon


class CancellationTests(unittest.TestCase):
    def setUp(self):
        self.daemon = WebDaemon()
        self.addCleanup(self.daemon.close)
        self.daemon.auth.provision('operator', 'fixture-password-only', ['read', 'run', 'maintenance', 'cancel'])
        self.secret = self.daemon.auth.issue_token('operator', ['read', 'run', 'maintenance', 'cancel'], 60).secret
        self.headers = {'Authorization': 'Bearer ' + self.secret}
        self.daemon.start()

    def request(self, path, payload=None, headers=None):
        kwargs = dict(headers=self.headers if headers is None else headers, timeout=3)
        if payload is None:
            return requests.get(self.daemon.uri + path, **kwargs)
        return requests.post(self.daemon.uri + path, json=payload, **kwargs)

    def wait_complete(self, job_id):
        for _ in range(100):
            data = self.request('/jobs/diagnostic/' + job_id).json()
            if data['status'] == 'complete':
                return data
            time.sleep(0.03)
        self.fail('cancelled job did not finish')

    def test_queued_job_cancels_during_maintenance_without_launch(self):
        # Occupy the endpoint worker, then queue another job before maintenance.
        self.request('/run/diagnostic/occupy-worker', {'args': ['-c', 'import time; time.sleep(0.8)']}).raise_for_status()
        self.request('/run/diagnostic/queued-cancel', {'args': ['-c', 'print("MUST NOT RUN")']}).raise_for_status()
        self.request('/maintenance', {'enabled': True}).raise_for_status()
        response = self.request('/cancel/diagnostic/queued-cancel', {})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()['cancel_requested'])
        done = self.wait_complete('queued-cancel')
        self.assertEqual(done['outcome'], 'job.cancelled')
        self.assertIsNone(done['started_at'])
        self.assertEqual(done['stream'], [])
        again = self.request('/cancel/diagnostic/queued-cancel', {})
        self.assertEqual(again.json()['ended_at'], done['ended_at'])

    def test_scope_and_owner_are_checked_before_cancellation(self):
        self.request('/run/diagnostic/private-job', {'args': ['-c', 'import time; time.sleep(1)']}).raise_for_status()
        token = self.daemon.auth.issue_token('operator', ['read', 'run'], 60).secret
        no_scope = {'Authorization': 'Bearer ' + token}
        self.assertEqual(self.request('/cancel/diagnostic/private-job', {}, no_scope).status_code, 403)
        self.daemon.auth.provision('other', 'fixture-password-only', ['read', 'run', 'cancel'])
        other = self.daemon.auth.issue_token('other', ['cancel'], 60).secret
        self.assertEqual(self.request('/cancel/diagnostic/private-job', {},
            {'Authorization': 'Bearer ' + other}).status_code, 403)
        self.assertFalse(self.request('/jobs/diagnostic/private-job').json()['cancel_requested'])

    def test_running_process_and_child_are_stopped_before_completion(self):
        code = ('import subprocess,time; p=subprocess.Popen(["/bin/sleep","60"]); '
                'print(p.pid,flush=True); time.sleep(60)')
        self.request('/run/diagnostic/running-cancel', {'args': ['-c', code]}).raise_for_status()
        child = None
        for _ in range(100):
            data = self.request('/jobs/diagnostic/running-cancel').json()
            if data.get('stream'):
                child = int(''.join(data['stream']).strip())
                break
            time.sleep(0.02)
        self.assertIsNotNone(child)
        response = cancel_job(self.daemon.uri, 'diagnostic', 'running-cancel',
                              BearerCredentials(self.secret), http_timeout=3)
        self.assertTrue(response['cancel_requested'])
        done = self.wait_complete('running-cancel')
        self.assertEqual(done['outcome'], 'job.cancelled')
        self.assertEqual(done['return_code'], 130)
        state = Path('/proc/%s/stat' % child)
        if state.exists():
            self.assertEqual(state.read_text().split()[2], 'Z', 'child still executing after terminal state')

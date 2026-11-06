"""Exercise the two 2.0 defaults together on a real daemon."""
import sys
import time
import unittest

import requests
import yaml

from web_fixture import WebDaemon


class MigrationHTTPTests(unittest.TestCase):
    def test_legacy_config_requires_auth_and_checks_files_before_queueing(self):
        daemon = WebDaemon()
        self.addCleanup(daemon.close)
        config_path = daemon.path / 'auton.yml'
        config = yaml.safe_load(config_path.read_text())
        del config['general']['auth_mode']
        fixed, optional = (daemon.path / name for name in ('fixed.env', 'optional.env'))
        fixed.write_text('FIXED=one\n')
        optional.write_text('OPTIONAL=two\n')
        config['endpoints']['diagnostic']['config'] = {
            'prog': sys.executable, 'allow-args': True, 'allow-env': True,
            'args': ['-c', 'import os; print(os.environ["FIXED"], os.environ.get("OPTIONAL", "absent"))'],
            'envfiles': [str(fixed)], 'disallow-envfiles': False,
            'allow-envfiles': [str(optional)],
        }
        config_path.write_text(yaml.safe_dump(config))
        daemon.start()
        headers = {'Authorization': 'Bearer ' + daemon.token, 'X-Auton-Output-Offset': '0'}
        url = daemon.uri + '/run/diagnostic/file-test'
        self.assertEqual(requests.post(url, json={}, timeout=3).status_code, 401)
        rejected = requests.post(url, headers=headers, json={'envfiles': [str(optional), str(fixed)]}, timeout=3)
        self.assertEqual(rejected.status_code, 400, rejected.text)
        jobs = requests.get(daemon.uri + '/jobs', headers=headers, timeout=3)
        self.assertEqual(jobs.json()['jobs'], [])
        for job, payload, expected in [('file-test', {'envfiles': [str(optional)]}, 'one two'),
                                       ('fixed-only', {}, 'one absent')]:
            path = '/diagnostic/' + job
            response = requests.post(daemon.uri + '/run' + path, headers=headers, json=payload, timeout=3)
            response.raise_for_status()
            for _ in range(100):
                response = requests.get(daemon.uri + '/status' + path, headers=headers, timeout=3)
                response.raise_for_status()
                detail = response.json()
                if detail['status'] == 'complete':
                    break
                time.sleep(0.05)
            self.assertEqual(detail['status'], 'complete')
            self.assertEqual(detail['return_code'], 0)
            self.assertEqual(''.join(detail['stream']).strip(), expected)

"""Real HTTP admission and unchanged argv/environment forwarding."""
import json
import sys
import time
import unittest

import requests
import yaml
from web_fixture import WebDaemon


class ClientPolicyHTTPTests(unittest.TestCase):
    def test_rejected_values_never_queue_and_valid_repeats_reach_program_unchanged(self):
        self._check_policy(imported=False)

    def test_imported_blocks_enforce_admission_and_forward_fixed_args_and_environment(self):
        self._check_policy(imported=True)

    def _check_policy(self, imported):
        daemon = WebDaemon()
        self.addCleanup(daemon.close)
        path = daemon.path / 'auton.yml'
        config = yaml.safe_load(path.read_text())
        config['endpoints']['diagnostic']['config'] = {
            'prog': sys.executable,
            'args': ['-c', 'import json, os, sys; print(json.dumps([sys.argv[1:], os.environ["TZ"], os.environ["FIXED"]]))'],
            'env': {'FIXED': 'server'},
            'allow-args': {'month': {'regex': r'2026-(0[1-9]|1[0-2])', 'repeatable': True}},
            'disallow-args': ['blocked'],
            'allow-env': {'TZ': {'choices': ['UTC']}},
            'disallow-env': ['FIXED'],
            'disallow-argfiles': True, 'disallow-envfiles': True,
        }
        if imported:
            from auton.classes.endpoint_imports import BLOCK_IMPORTS
            endpoint = config['endpoints']['diagnostic']
            settings = endpoint.pop('config')
            directory = daemon.path / 'shared'
            directory.mkdir()
            for key, field in BLOCK_IMPORTS.items():
                (directory / (key + '.yml')).write_text(yaml.safe_dump(settings.pop(field)))
                settings[key] = key + '.yml'
            (directory / 'config.yml').write_text(yaml.safe_dump(settings))
            endpoint['import_config'] = 'shared/config.yml'
        path.write_text(yaml.safe_dump(config))
        daemon.start()
        headers = {'Authorization': 'Bearer ' + daemon.token, 'X-Auton-Output-Offset': '0'}
        for payload in ({'args': ['--month', 'secret-invalid-value']},
                        {'args': ['--unknown', 'x']}, {'env': {'TZ': 'invalid'}},
                        {'env': {'FIXED': 'client'}},
                        {'argfiles': [{'arg': '-f', 'filename': 'x', 'content': 'eA=='}]}):
            result = requests.post(daemon.uri + '/run/diagnostic/rejected', headers=headers,
                                   json=payload, timeout=3)
            self.assertEqual(result.status_code, 400, result.text)
            self.assertNotIn('secret-invalid-value', result.text)
        result = requests.get(daemon.uri + '/jobs', headers=headers, timeout=3)
        self.assertEqual(result.json()['jobs'], [])
        args = ['--month=2026-09', '--month', '2026-10']
        result = requests.post(daemon.uri + '/run/diagnostic/accepted', headers=headers,
                               json={'args': args, 'env': {'TZ': 'UTC'}}, timeout=3)
        self.assertEqual(result.status_code, 200, result.text)
        for _ in range(100):
            detail = requests.get(daemon.uri + '/status/diagnostic/accepted', headers=headers, timeout=3).json()
            if detail['status'] == 'complete':
                break
            time.sleep(.02)
        self.assertEqual(detail['status'], 'complete')
        self.assertEqual(detail['return_code'], 0)
        self.assertEqual(json.loads(''.join(detail['stream'])), [args, 'UTC', 'server'])

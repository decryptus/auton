"""Exercise the real daemon, HTTP framework and command-line client together."""
import json
import base64
import hashlib
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest

import requests
import yaml

ROOT = Path(__file__).resolve().parents[1]


class HTTPIntegrationTests(unittest.TestCase):
    def test_client_output_exit_code_and_replay(self):
        self.run_scenario(False)

    def test_authenticated_client_and_owner_isolation(self):
        self.run_scenario(True)

    def run_scenario(self, authenticated):
        with tempfile.TemporaryDirectory() as tmp:
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            config = yaml.safe_load((ROOT / 'etc/auton/auton.yml.example').read_text())
            config['general'].update(listen_addr='127.0.0.1', listen_port=port,
                                     max_life_time=0, max_requests=0)
            config.pop('import_modules', None)
            config['modules'] = yaml.safe_load((ROOT / 'etc/auton/modules/job.yml').read_text())
            auth = None
            auth_args = []
            if authenticated:
                passwd = Path(tmp) / 'htpasswd'
                hashed = '{SHA}' + base64.b64encode(hashlib.sha1(b'secret').digest()).decode()
                passwd.write_text('alice:' + hashed + '\nbob:' + hashed + '\n')
                config['general'].update(auth_basic='Test', auth_basic_file=str(passwd))
                for route in config['modules']['job']['routes'].values():
                    route['auth'] = True
                auth = ('alice', 'secret')
                auth_args = ['--auth-user', 'alice', '--auth-passwd', 'secret']
            config['endpoints'] = {'test': {'plugin': 'subproc', 'config': {
                'prog': sys.executable, 'timeout': 2}}}
            conf = Path(tmp) / 'auton.yml'
            conf.write_text(yaml.safe_dump(config))
            env = dict(os.environ, PYTHONPATH=str(ROOT))
            with open(Path(tmp) / 'daemon-output.log', 'w+') as log:
                proc = subprocess.Popen([sys.executable, str(ROOT / 'bin/autond'), '-f',
                                         '-c', str(conf), '-p', str(Path(tmp) / 'autond.pid'),
                                         '--logfile', str(Path(tmp) / 'autond.log')],
                                        cwd=ROOT, env=env, stdout=log, stderr=log)
                uri = 'http://127.0.0.1:%s' % port
                try:
                    ready = False
                    for _ in range(100):
                        if proc.poll() is not None:
                            break
                        try:
                            requests.get(uri + '/status/test/missing-job', timeout=0.1)
                            ready = True
                            break
                        except requests.RequestException:
                            time.sleep(0.05)
                    if not ready:
                        log.flush(); log.seek(0)
                        self.fail('daemon failed to start: ' + log.read())
                    result = subprocess.run([sys.executable, str(ROOT / 'bin/auton'),
                                             '--uri', uri, '--endpoint', 'test', '--uid', 'http-test-job',
                                             '--delay', '0.01', '--http-timeout', '2',
                                             '-a', '-c', '-a', 'print("hello"); raise SystemExit(7)'] + auth_args,
                                            env=env, capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 7, result.stderr)
                    self.assertEqual(result.stdout, 'hello\n')
                    url = uri + '/status/test/http-test-job'
                    a = requests.get(url, headers={'X-Auton-Output-Offset': '0'}, auth=auth, timeout=2)
                    b = requests.get(url, headers={'X-Auton-Output-Offset': '0'}, auth=auth, timeout=2)
                    self.assertEqual(a.status_code, 200, a.text)
                    self.assertEqual(a.json()['stream'], b.json()['stream'])
                    self.assertEqual(''.join(a.json()['stream']), 'hello\n')
                    self.assertEqual(a.json()['return_code'], 7)
                    if authenticated:
                        self.assertEqual(requests.get(url, timeout=2).status_code, 401)
                        self.assertEqual(requests.get(url, auth=('alice', 'wrong'), timeout=2).status_code, 401)
                        self.assertEqual(requests.get(url, auth=('bob', 'secret'), timeout=2).status_code, 403)
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()

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
import pty
import select
import fcntl
import struct
import termios

import requests
import yaml

from auton_client.monitor import FleetMonitor
from auton_client.visibility import DaemonClient

ROOT = Path(__file__).resolve().parents[1]


class HTTPIntegrationTests(unittest.TestCase):
    def test_two_daemon_aggregation_preserves_identity_and_auth(self):
        processes = []
        monitor = None
        with tempfile.TemporaryDirectory() as tmp:
            try:
                passwd = Path(tmp) / 'htpasswd'
                hashed = '{SHA}' + base64.b64encode(hashlib.sha1(b'secret').digest()).decode()
                passwd.write_text('alice:' + hashed + '\nbob:' + hashed + '\n')
                clients = {}
                for name in ('one', 'two'):
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0))
                        port = sock.getsockname()[1]
                    config = yaml.safe_load((ROOT / 'etc/auton/auton.yml.example').read_text())
                    config['general'].update(listen_addr='127.0.0.1', listen_port=port,
                                             max_life_time=0, max_requests=0,
                                             auth_basic='Test', auth_basic_file=str(passwd))
                    config.pop('import_modules', None)
                    config['modules'] = yaml.safe_load((ROOT / 'etc/auton/modules/job.yml').read_text())
                    for route in config['modules']['job']['routes'].values():
                        route['auth'] = True
                    config['endpoints'] = {'test': {'plugin': 'subproc', 'config': {
                        'prog': sys.executable, 'timeout': 2}}}
                    conf = Path(tmp) / (name + '.yml')
                    conf.write_text(yaml.safe_dump(config))
                    proc = subprocess.Popen([sys.executable, str(ROOT / 'bin/autond'), '-f',
                                             '-c', str(conf), '-p', str(Path(tmp) / (name + '.pid')),
                                             '--logfile', str(Path(tmp) / (name + '.log'))],
                                            env=dict(os.environ, PYTHONPATH=str(ROOT)), cwd=ROOT,
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    processes.append(proc)
                    uri = 'http://127.0.0.1:%s' % port
                    ready = False
                    for _ in range(100):
                        try:
                            ready = requests.get(uri + '/health', auth=('alice', 'secret'), timeout=0.1).status_code == 200
                            if ready:
                                break
                        except requests.RequestException:
                            pass
                        time.sleep(0.02)
                    self.assertTrue(ready, 'daemon did not start: ' + name)
                    response = requests.post(uri + '/run/test/shared-job', auth=('alice', 'secret'),
                                             json={'args': ['-c', 'print(%r)' % name]}, timeout=2)
                    self.assertEqual(response.status_code, 200, response.text)
                    client = DaemonClient(uri, auth=('alice', 'secret'), http_timeout=1)
                    for _ in range(100):
                        detail = client.detail('test', 'test:shared-job')
                        if detail['status'] == 'complete':
                            break
                        time.sleep(0.02)
                    self.assertEqual(detail['status'], 'complete')
                    self.assertEqual(DaemonClient(uri, auth=('bob', 'secret')).jobs(), [])
                    clients[name] = client
                # An authenticated but unauthorized connection fails independently.
                clients['denied'] = DaemonClient(clients['one'].uri, auth=('alice', 'wrong'), http_timeout=1)
                monitor = FleetMonitor(clients)
                monitor.refresh(None, ('two', 'test', 'test:shared-job'))
                results = []
                deadline = time.monotonic() + 5
                while len(results) < 3 and time.monotonic() < deadline:
                    result = monitor.poll()
                    if result is not None:
                        results.append(result[2])
                    else:
                        time.sleep(0.01)
                self.assertEqual(len(results), 3)
                data = results[-1]
                self.assertEqual([(job['daemon'], job['uid']) for job in data['jobs']],
                                 [('one', 'test:shared-job'), ('two', 'test:shared-job')])
                self.assertEqual(''.join(data['detail']['stream']), 'two\n')
                self.assertTrue(data['partial'])
                self.assertEqual(data['errors']['denied/jobs'], 'HTTP 401')
                self.assertEqual(data['stats']['responding'], 2)
                self.run_tui(None, ['--auth-user', 'alice', '--auth-passwd', 'secret'],
                             dict(os.environ, PYTHONPATH=str(ROOT)),
                             daemon_args=['--daemon', 'one=' + clients['one'].uri,
                                          '--daemon', 'two=' + clients['two'].uri],
                             expected_job=b'shared-job', expected_output=b'one')
                self.assertEqual(len(clients['one'].jobs()), 1)
                self.assertEqual(len(clients['two'].jobs()), 1)
                target_args = ['--target', 'one=' + clients['one'].uri,
                               '--target', 'two=' + clients['two'].uri]
                for exit_code in (0, 7):
                    execution = subprocess.run([sys.executable, str(ROOT / 'bin/auton'),
                        '--endpoint', 'test', '--auth-user', 'alice', '--auth-passwd', 'secret',
                        '--delay', '0.01', '--operation-id', 'integration',
                        '-a', '-c', '-a', 'import sys; print("hello"); print("diagnostic", file=sys.stderr); sys.exit(%s)' % exit_code]
                        + target_args, env=dict(os.environ, PYTHONPATH=str(ROOT), AUTON_URI='http://unused'),
                        capture_output=True, text=True, timeout=10)
                    self.assertEqual(execution.returncode, int(exit_code != 0), execution.stderr)
                    operation = json.loads(execution.stdout)
                    self.assertEqual(operation['operation_id'], 'integration')
                    self.assertEqual(len({t['job_id'] for t in operation['targets']}), 2)
                    for target in operation['targets']:
                        self.assertEqual(target['return_code'], exit_code, target)
                        self.assertEqual(''.join(target['stdout']), 'hello\n')
                        self.assertTrue(''.join(target['stderr']).startswith('diagnostic\n'), target)
                        uri = clients[target['target']].uri
                        detail_path = '/jobs/test/' + target['job_id']
                        self.assertEqual(requests.get(uri + detail_path, auth=('bob', 'secret'), timeout=2).status_code, 403)
                denied = subprocess.run([sys.executable, str(ROOT / 'bin/auton'), '--endpoint', 'test',
                                         '--auth-user', 'alice', '--auth-passwd', 'wrong'] + target_args,
                                        env=dict(os.environ, PYTHONPATH=str(ROOT)),
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(denied.returncode, 1, denied.stderr)
                self.assertEqual([t['status'] for t in json.loads(denied.stdout)['targets']], ['rejected', 'rejected'])
                self.assertEqual(len(clients['one'].jobs()), 3)
                self.assertEqual(len(clients['two'].jobs()), 3)
            finally:
                if monitor is not None:
                    monitor.close()
                for proc in processes:
                    proc.terminate()
                    try:
                        proc.wait(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.wait()

    def run_tui(self, uri, auth_args, env, daemon_args=None,
                expected_job=b'http-test-job', expected_output=b'hello'):
        master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 24, 100, 0, 0))
        proc = subprocess.Popen([sys.executable, str(ROOT / 'bin/auton'), '--tui',
                                 '--refresh', '60' if daemon_args else '0.2', '--http-timeout', '1'] +
                                (daemon_args or ['--uri', uri]) + auth_args,
                                stdin=slave, stdout=slave, stderr=slave,
                                env=dict(env, TERM='xterm'), cwd=ROOT)
        os.close(slave)
        captured = bytearray()
        def until(text):
            if text in captured:
                return
            end = time.monotonic() + 5
            while time.monotonic() < end:
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        captured.extend(os.read(master, 65536))
                    except OSError:
                        break
                    if text in captured:
                        return
                if proc.poll() is not None:
                    break
            self.fail('TUI did not display %r: %r' % (text, bytes(captured)))
        try:
            if daemon_args:
                until(b'AUTON')
                os.write(master, b'a')
                until(b'one /')
                until(b'two /')
            until(expected_job)
            os.write(master, b'k\n')
            captured.clear()
            until(expected_output)
            os.write(master, b'v')
            until(b'diagnostics')
            os.write(master, b'q')
            self.assertEqual(proc.wait(timeout=3), 0, bytes(captured))
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait()
            os.close(master)

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
                                     max_life_time=0, max_requests=0,
                                     journal_path=str(Path(tmp) / 'jobs.jsonl'))
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
                    def inspect(path, identity=auth):
                        return requests.get(uri + path, auth=identity, timeout=2)
                    jobs = inspect('/jobs')
                    self.assertEqual(jobs.status_code, 200, jobs.text)
                    self.assertEqual([j['uid'] for j in jobs.json()['jobs']], ['test:http-test-job'])
                    self.assertNotIn('stream', jobs.json()['jobs'][0])
                    self.assertEqual(inspect('/jobs?status=complete&endpoint=test').json()['jobs'],
                                     jobs.json()['jobs'])
                    self.assertEqual(inspect('/jobs?status=new').json()['jobs'], [])
                    for query in ('status=invalid', 'owner=bob', 'status[]=new'):
                        self.assertEqual(inspect('/jobs?' + query).status_code, 400)
                    detail = inspect('/jobs/test/http-test-job')
                    self.assertEqual(detail.status_code, 200, detail.text)
                    self.assertEqual(''.join(detail.json()['stream']), 'hello\n')
                    self.assertEqual(detail.json()['return_code'], 7)
                    self.assertEqual(inspect('/jobs/test/missing-job').status_code, 404)
                    self.assertEqual(inspect('/endpoints').json()['endpoints'], [{'name': 'test'}])
                    self.assertEqual(inspect('/health').json()['status'], 'ok')
                    self.assertEqual(inspect('/stats').json()['stats']['jobs'], 1)
                    self.run_tui(uri, auth_args, env)
                    self.assertEqual(len(inspect('/jobs').json()['jobs']), 1)
                    journal_text = (Path(tmp) / 'jobs.jsonl').read_text()
                    journal = [json.loads(line) for line in journal_text.splitlines()]
                    self.assertEqual([row['event'] for row in journal],
                                     ['job.admitted', 'job.started', 'job.failed'])
                    self.assertEqual(journal[-1]['return_code'], 7)
                    self.assertEqual(journal[-1]['principal'], 'alice' if authenticated else None)
                    self.assertNotIn('hello', journal_text)
                    if authenticated:
                        for path in ('/jobs', '/jobs/test/http-test-job', '/endpoints', '/health', '/stats'):
                            self.assertEqual(inspect(path, None).status_code, 401)
                            self.assertEqual(inspect(path, ('alice', 'wrong')).status_code, 401)
                        self.assertEqual(inspect('/jobs', ('bob', 'secret')).json()['jobs'], [])
                        self.assertEqual(inspect('/stats', ('bob', 'secret')).json()['stats']['jobs'], 0)
                        self.assertEqual(inspect('/jobs/test/http-test-job', ('bob', 'secret')).status_code, 403)
                        self.assertEqual(requests.get(url, timeout=2).status_code, 401)
                        self.assertEqual(requests.get(url, auth=('alice', 'wrong'), timeout=2).status_code, 401)
                        self.assertEqual(requests.get(url, auth=('bob', 'secret'), timeout=2).status_code, 403)
                finally:
                    proc.terminate()
                    try:
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill(); proc.wait()

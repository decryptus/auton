"""Real private daemon fixture shared by HTTP and browser acceptance tests."""
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import requests
import yaml
from auton.classes.auth_store import PersistentAuthentication

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PASSWORD = 'fixture-password-only'


class WebDaemon:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name)
        self.proc = None
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        self.uri = 'http://127.0.0.1:%s' % port
        self.auth = PersistentAuthentication({'path': str(self.path / 'auth.db'), 'timeout': 5})
        for principal, scopes in [('operator', ['read', 'run', 'maintenance']), ('reader', ['read']), ('other', ['read', 'run'])]:
            self.auth.provision(principal, FIXTURE_PASSWORD, scopes)
        self.token = self.auth.issue_token('operator', ['read', 'run', 'maintenance'], 3600).secret
        conf = yaml.safe_load((ROOT / 'etc/auton/auton.yml.example').read_text())
        conf['general'].update(listen_addr='127.0.0.1', listen_port=port, max_life_time=0, max_requests=0,
            auth_mode='required', web_enabled=True, web_origin=self.uri, maintenance_operators=['operator'],
            authentication={'backend': 'sqlite', 'path': str(self.path / 'auth.db')},
            job_storage={'backend': 'sqlite', 'path': str(self.path / 'jobs.db')})
        conf.pop('import_modules', None)
        conf['modules'] = yaml.safe_load((ROOT / 'etc/auton/modules/job.yml').read_text())
        conf['endpoints'] = {name: {'plugin': 'subproc', 'users': {'operator': True, 'reader': True, 'other': True},
                                  'config': {'prog': sys.executable, 'timeout': 10}}
                             for name in ('diagnostic', 'health-check', 'deployment-check')}
        conf['endpoints']['private'] = {'plugin': 'subproc', 'users': {'other': True},
                                       'config': {'prog': sys.executable, 'timeout': 2}}
        (self.path / 'auton.yml').write_text(yaml.safe_dump(conf))

    def start(self):
        self.proc = subprocess.Popen([sys.executable, str(ROOT / 'bin/autond'), '-f', '-c', str(self.path / 'auton.yml'),
            '-p', str(self.path / 'daemon.pid'), '--logfile', str(self.path / 'daemon.log')],
            env=dict(os.environ, PYTHONPATH=str(ROOT)), cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(150):
            try:
                response = requests.get(self.uri + '/ui/', timeout=0.2)
                if response.status_code == 200:
                    return self
            except requests.RequestException:
                pass
            if self.proc.poll() is not None:
                break
            time.sleep(0.02)
        self.stop()
        raise RuntimeError('daemon failed to start: ' + (self.path / 'daemon.log').read_text())

    def stop(self):
        if self.proc is not None and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=6)
        self.proc = None

    def close(self):
        self.stop()
        self.auth.close()
        self.directory.cleanup()

    def login(self, principal='operator'):
        session = requests.Session()
        session.headers.update(Origin=self.uri)
        response = session.post(self.uri + '/ui/auth/login', json={'principal': principal, 'password': FIXTURE_PASSWORD}, timeout=5)
        if response.status_code != 200:
            session.close()
            raise RuntimeError('fixture login failed: %s' % response.text)
        session.headers['X-CSRF-Token'] = response.json()['csrf']
        return session, response

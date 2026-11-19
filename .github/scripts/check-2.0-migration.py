"""Check installed 1.5.3 -> 2.0 migration on disposable copied SQLite stores.

This is reproducible engineering acceptance, not an operator's production report.
No source imports, real credentials, external commands or customer data are used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

import requests
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_BASELINE = '1.5.3'
_DEADLINE = 12
_PROGRAM = ('import json, os, sys; '
            'print(json.dumps([sys.argv[1:], os.environ.get("MODE"), '
            'os.environ.get("FIXED"), os.environ.get("OPTIONAL")]))')


def run(python, args, directory):
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    return subprocess.run([str(python), *args], cwd=directory, env=env,
                          capture_output=True, text=True, timeout=30, check=True)


def versions(python, directory):
    result = run(python, ['-I', '-c',
        'import json; from importlib.metadata import version; '
        'print(json.dumps({p: version(p) for p in ("auton", "autond", "httpdis", "sonicprobe")}))'], directory)
    return json.loads(result.stdout)


class Daemon:
    def __init__(self, python, directory, config):
        self.python, self.directory, self.config = python, directory, config
        self.process = None
        self.output = None
        self.uri = 'http://127.0.0.1:%s' % config['general']['listen_port']

    def start(self, expect_failure=False):
        path = self.directory / 'auton.yml'
        path.write_text(yaml.safe_dump(self.config))
        executable = self.python.parent / 'autond'
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        self.output = (self.directory / 'process.log').open('w')
        self.process = subprocess.Popen([str(self.python), '-I', str(executable),
            '-f', '-c', str(path), '-p', str(self.directory / 'daemon.pid'),
            '--logfile', str(self.directory / 'daemon.log')], cwd=self.directory,
            env=env, stdout=self.output, stderr=self.output)
        end = time.monotonic() + _DEADLINE
        while time.monotonic() < end:
            code = self.process.poll()
            if code is not None:
                if expect_failure and code != 0:
                    return self
                diagnostic = (self.directory / 'process.log').read_text()[-4000:]
                raise RuntimeError('installed synthetic daemon exited unexpectedly: ' + diagnostic)
            try:
                response = requests.get(self.uri + '/health', timeout=.2)
                if response.status_code in (200, 401):
                    if expect_failure:
                        raise AssertionError('invalid configuration started a daemon')
                    return self
            except requests.ConnectionError:
                pass
            time.sleep(.05)
        raise RuntimeError('installed daemon readiness deadline')

    def stop(self):
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)
        if self.output is not None:
            self.output.close()
            self.output = None

    def request(self, method, path, token=None, payload=None, status=200):
        headers = {'X-Auton-Output-Offset': '0'}
        if token:
            headers['Authorization'] = 'Bearer ' + token
        response = requests.request(method, self.uri + path, headers=headers,
                                    json=payload, timeout=3)
        if response.status_code != status:
            raise AssertionError('%s %s: expected %d, received %d' %
                                 (method, path, status, response.status_code))
        return response.json()

    def complete(self, job, token, endpoint='report'):
        end = time.monotonic() + _DEADLINE
        while time.monotonic() < end:
            detail = self.request('GET', '/status/' + endpoint + '/' + job, token)
            if detail['status'] == 'complete':
                assert detail['return_code'] == 0, detail['return_code']
                return json.loads(''.join(detail['stream']))
            time.sleep(.05)
        raise AssertionError('job completion deadline')


def copy_installation(source, target, config):
    shutil.copytree(source, target)
    # All source files are synthetic and daemons are stopped before copying.
    return yaml.safe_load(yaml.safe_dump(config).replace(str(source), str(target)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--previous-python', type=Path, required=True)
    parser.add_argument('--candidate-python', type=Path, required=True)
    args = parser.parse_args()
    previous = args.previous_python.absolute()
    candidate = args.candidate_python.absolute()
    checks, daemons = [], []
    with tempfile.TemporaryDirectory(prefix='auton-migration-') as temporary:
        root = Path(temporary)
        baseline = root / 'baseline'
        baseline.mkdir(mode=0o700)
        old_versions, new_versions = versions(previous, root), versions(candidate, root)
        assert old_versions['auton'] == old_versions['autond'] == _BASELINE
        assert new_versions['auton'] == new_versions['autond']
        assert new_versions['autond'].startswith('2.0.')
        tokens = json.loads(run(previous, ['-I', '-c', '''
import json, sys
from auton.classes.auth_store import PersistentAuthentication
store = PersistentAuthentication({'path': sys.argv[1], 'timeout': 5})
tokens = {}
for name in ('alice', 'bob'):
    store.provision(name, 'disposable-migration-fixture', ['read', 'run', 'maintenance'])
    tokens[name] = store.issue_token(name, ['read', 'run', 'maintenance'], 3600).secret
store.close()
print(json.dumps(tokens))
''', str(baseline / 'auth.db')], root).stdout)
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        config = yaml.safe_load((_ROOT / 'etc/auton/auton.yml.example').read_text())
        config.pop('import_modules', None)
        config['modules'] = yaml.safe_load((_ROOT / 'etc/auton/modules/job.yml').read_text())
        for route in config['modules']['job']['routes'].values():
            route['auth'] = True
        config['general'].update(listen_port=port, max_life_time=0, max_requests=0,
            max_jobs=32, max_jobs_per_user=1, maintenance_operators=['alice'],
            authentication={'backend': 'sqlite', 'path': str(baseline / 'auth.db')},
            job_storage={'backend': 'sqlite', 'path': str(baseline / 'jobs.db')})
        (baseline / 'fixed.env').write_text('FIXED=server\n')
        (baseline / 'optional.env').write_text('OPTIONAL=selected\n')
        config['endpoints'] = {'report': {'plugin': 'subproc',
            'users': {'alice': True, 'bob': True}, 'config': {
                'prog': str(previous), 'args': ['-c', _PROGRAM],
                'envfiles': [str(baseline / 'fixed.env')], 'timeout': 5}}}
        config['endpoints']['waiting'] = {'plugin': 'subproc',
            'users': {'alice': True, 'bob': True}, 'config': {
                'prog': str(previous), 'args': ['-c',
                    'from pathlib import Path; import time\n'
                    'gate = Path(%r)\n' % str(baseline / 'release-waiting') +
                    'while not gate.exists(): time.sleep(.02)\nprint("[]")'],
                'disallow-args': True, 'disallow-env': True,
                'disallow-argfiles': True, 'disallow-envfiles': True, 'timeout': 10}}
        config['endpoints']['private'] = {'plugin': 'subproc', 'users': {'alice': True},
            'config': {'prog': str(previous), 'args': ['-c', 'print("[]")'], 'timeout': 5}}
        try:
            old = Daemon(previous, baseline, config)
            daemons.append(old)
            old.start()
            old.request('POST', '/run/report/old-record', tokens['alice'],
                        {'args': ['--month', '2026-11'], 'env': {'MODE': 'safe'}})
            expected = [['--month', '2026-11'], 'safe', 'server', None]
            assert old.complete('old-record', tokens['alice']) == expected
            old.stop()
            originals = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                         for p in (baseline / 'auth.db', baseline / 'jobs.db', baseline / 'auton.yml')}
            checks.append('1.5.3 installed daemon created authenticated durable history')

            directory = root / 'candidate'
            migrated = copy_installation(baseline, directory, config)
            migrated['general'].pop('auth_mode')
            new = Daemon(candidate, directory, migrated)
            daemons.append(new)
            new.start()
            new.request('GET', '/jobs', status=401)
            assert new.complete('old-record', tokens['alice']) == expected
            assert new.request('GET', '/jobs', tokens['bob'])['jobs'] == []
            new.request('POST', '/run/report/blocked-job', tokens['alice'],
                        {'args': ['--month', '2026-11']}, status=400)
            new.request('POST', '/run/report/blocked-job', tokens['alice'],
                        {'env': {'MODE': 'safe'}}, status=400)
            assert len(new.request('GET', '/jobs', tokens['alice'])['jobs']) == 1
            new.request('POST', '/run/report/fixed-only', tokens['alice'], {})
            assert new.complete('fixed-only', tokens['alice']) == [[], None, 'server', None]
            new.stop()
            checks.append('copied tokens/history survive; default auth/input denial precedes admission')

            blocks = directory / 'blocks'
            blocks.mkdir()
            settings = migrated['endpoints']['report']['config']
            definitions = {'args': settings.pop('args'), 'env': {'MODE': 'safe'},
                'allow-args': {'month': {'choices': ['2026-11'], 'repeatable': True}},
                'disallow-args': ['blocked'], 'allow-env': {'MODE': {'choices': ['safe']}, 'OPTIONAL': True},
                'disallow-env': ['FIXED']}
            for name, value in definitions.items():
                (blocks / (name + '.yml')).write_text(yaml.safe_dump(value))
                settings['import_' + name.replace('-', '_')] = 'blocks/' + name + '.yml'
            settings['allow-envfiles'] = [str(directory / 'optional.env')]
            settings['disallow-argfiles'] = True
            new.start()
            payload = {'args': ['--month', '2026-11'], 'env': {'MODE': 'safe'},
                       'envfiles': [str(directory / 'optional.env')]}
            owner_results = {}
            for owner, token in tokens.items():
                owner_payload = dict(payload, args=payload['args'] * (1 if owner == 'alice' else 2))
                owner_results[owner] = [owner_payload['args'], 'safe', 'server', 'selected']
                new.request('POST', '/run/report/shared-id', token, owner_payload)
                assert new.complete('shared-id', token) == owner_results[owner]
                new.request('POST', '/run/report/shared-id', token, owner_payload, status=409)
            before = len(new.request('GET', '/jobs', tokens['alice'])['jobs'])
            for invalid in ({'args': ['--month', 'invalid']}, {'env': {'FIXED': 'client'}},
                            {'envfiles': [str(directory / 'fixed.env')]},
                            {'argfiles': [{'arg': '-f', 'filename': 'x', 'content': 'eA=='}]}):
                new.request('POST', '/run/report/invalid-job', tokens['alice'], invalid, status=400)
            assert len(new.request('GET', '/jobs', tokens['alice'])['jobs']) == before
            checks.append('six imports, explicit permissions, envfile selection, 400/409 and owner isolation')
            new.request('POST', '/run/private/denied-job', tokens['bob'], {}, status=403)
            new.request('POST', '/run/waiting/quota-job', tokens['alice'], {})
            new.request('POST', '/run/report/quota-refusal', tokens['alice'], {}, status=429)
            new.request('POST', '/run/waiting/quota-job', tokens['bob'], {})
            (directory / 'release-waiting').touch()
            for token in tokens.values():
                assert new.complete('quota-job', token, endpoint='waiting') == []
            checks.append('endpoint ACLs and per-principal capacity return 403/429 without blocking other owners')
            new.stop()
            new.start()
            for owner, token in tokens.items():
                assert new.complete('shared-id', token) == owner_results[owner]
            new.request('POST', '/maintenance', tokens['alice'], {'enabled': True})
            new.request('POST', '/run/report/maintenance-job', tokens['alice'], {}, status=503)
            new.stop()
            migrated['general']['maintenance'] = True
            new.start()
            # Existing completed history remains visible during maintenance.
            assert new.complete('old-record', tokens['alice']) == expected
            new.stop()
            migrated['general']['maintenance'] = False
            (blocks / 'env.yml').write_text('MODE: safe\nimport_env: forbidden.yml\n')
            new.start(expect_failure=True)
            new.stop()
            checks.append('restart retains owner isolation; maintenance refuses; terminal imports reject nesting')

            assert originals == {name: hashlib.sha256((baseline / name).read_bytes()).hexdigest()
                                 for name in originals}
            rollback_dir = root / 'rollback'
            rollback_config = copy_installation(baseline, rollback_dir, config)
            rollback = Daemon(previous, rollback_dir, rollback_config)
            daemons.append(rollback)
            rollback.start()
            assert rollback.complete('old-record', tokens['alice']) == expected
            assert rollback.request('GET', '/jobs', tokens['bob'])['jobs'] == []
            rollback.stop()
            checks.append('original backup unchanged; rollback restores 1.5.3 credentials and history')
        finally:
            for daemon in reversed(daemons):
                daemon.stop()
        print(json.dumps({'scope': 'synthetic installed-package migration, not production acceptance',
                          'previous': old_versions, 'candidate': new_versions,
                          'checks': checks, 'result': 'passed'}, indent=2))


if __name__ == '__main__':
    main()

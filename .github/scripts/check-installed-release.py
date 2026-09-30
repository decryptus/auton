"""Exercise installed release wheels and actual 0.3.2 CLI/API compatibility.

All daemons, credentials, environments and jobs are disposable and loopback-only.
No application imports are resolved from the source checkout.
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[2]
PASSWORD = 'installed-acceptance-only'


def run(command, cwd, **kwargs):
    return subprocess.run(command, cwd=cwd, check=True, timeout=240, **kwargs)


def main():
    with tempfile.TemporaryDirectory() as temp:
        work = Path(temp)
        candidate, legacy = work / 'candidate', work / 'legacy'
        clean_env = dict(os.environ)
        clean_env.pop('PYTHONPATH', None)
        clean_env.pop('AUTON_PACKAGE', None)
        for directory in (candidate, legacy):
            venv.create(directory, with_pip=True)
        wheels = []
        for package in ('auton', 'autond'):
            matches = list((ROOT / 'dist' / package).glob('*.whl'))
            assert len(matches) == 1, package
            wheels.append(str(matches[0].resolve()) + ('[auth]' if package == 'autond' else ''))
        run([str(candidate / 'bin/python'), '-m', 'pip', 'install', *wheels], work, env=clean_env)
        run([str(legacy / 'bin/python'), '-m', 'pip', 'install', 'auton==0.3.2', 'autond==0.3.2'], work, env=clean_env)
        expected = (ROOT / 'VERSION').read_text().strip()
        run([str(candidate / 'bin/python'), '-I', '-c', '''
from importlib.metadata import version
from importlib.resources import files
import sys
import auton, auton_client
assert version('auton') == version('autond') == sys.argv[1]
assert auton_client.__version__ == sys.argv[1]
assert files('auton').joinpath('web/index.html').is_file()
''', expected], work, env=clean_env)
        for executable, flag in [('auton', '--help'), ('autond', '-d'), ('autond-auth', '--help')]:
            run([str(candidate / 'bin' / executable), flag], work, env=clean_env, stdout=subprocess.DEVNULL)
        password_file = work / 'passwd'
        # Explicit legacy SHA entry exercises migration compatibility, not a new-account recommendation.
        password_file.write_text('operator:{SHA}' + base64.b64encode(hashlib.sha1(PASSWORD.encode()).digest()).decode() + '\n')
        password_file.chmod(0o600)
        for daemon_env, client_envs in ((legacy, [candidate]), (candidate, [legacy, candidate])):
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            routes = {}
            for mode, verb in (('run', 'POST'), ('status', 'GET')):
                routes[mode] = {'handler': 'job_' + mode, 'op': verb, 'auth': True,
                    'regexp': '^' + mode + '/(?P<endpoint>[^/]+)/(?P<id>[a-z0-9][a-z0-9\\-]{7,63})$'}
            routes['run']['safe_init'] = True
            conf = {'general': {'listen_addr': '127.0.0.1', 'listen_port': port,
                'max_workers': 2, 'max_requests': 0, 'max_life_time': 0, 'lock_timeout': 60,
                'charset': 'utf-8', 'content_type': 'application/json; charset=utf-8',
                'auth_basic': 'Release acceptance', 'auth_basic_file': str(password_file)},
                'modules': {'job': {'routes': routes}},
                'endpoints': {'release-command': {'plugin': 'subproc', 'users': {'operator': True},
                    'config': {'prog': str(daemon_env / 'bin/python'), 'args': ['-c', 'print("installed-release-ok")'], 'timeout': 5}}}}
            # JSON is valid YAML; the same unchanged legacy configuration serves both versions.
            config = work / 'daemon.yml'
            config.write_text(json.dumps(conf))
            with (work / 'daemon-output.log').open('w') as output:
                daemon = subprocess.Popen([str(daemon_env / 'bin/autond'), '-f', '-c', str(config),
                    '-p', str(work / 'daemon.pid'), '--logfile', str(work / 'daemon.log')],
                    cwd=work, env=clean_env, stdout=output, stderr=output)
                try:
                    for _ in range(100):
                        if daemon.poll() is not None:
                            raise RuntimeError('Installed daemon exited before readiness')
                        try:
                            with socket.create_connection(('127.0.0.1', port), timeout=.1):
                                break
                        except OSError:
                            time.sleep(.05)
                    else:
                        raise RuntimeError('Installed daemon readiness deadline')
                    uri = 'http://127.0.0.1:%s' % port
                    try:
                        urllib.request.urlopen(urllib.request.Request(uri + '/run/release-command/denied-job', data=b'{}', headers={'Content-Type':'application/json'}), timeout=3)
                    except urllib.error.HTTPError as error:
                        assert error.code == 401, error.code
                    else:
                        raise AssertionError('Unauthenticated execution accepted')
                    for client in client_envs:
                        result = run([str(client / 'bin/auton'), '--uri', uri, '--endpoint', 'release-command',
                            '--auth-user', 'operator', '--auth-passwd', PASSWORD,
                            '--http-timeout', '3', '--logfile', str(work / 'client.log')],
                            work, env=clean_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                        assert 'installed-release-ok' in result.stdout
                        print('Installed compatibility:', client.name, 'client ->', daemon_env.name, 'daemon OK')
                finally:
                    daemon.terminate()
                    try:
                        daemon.wait(timeout=8)
                    except subprocess.TimeoutExpired:
                        daemon.kill(); daemon.wait(timeout=3)
        print('Installed wheels, entry points, web assets, auth refusal and 0.3.2 compatibility passed')


if __name__ == '__main__':
    main()

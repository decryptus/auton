#!/usr/bin/env python3
"""Exercise the shipped Compose config and durable volume on a disposable project.

Requires Docker Compose and the already built auton:local image. Never uses the
operator's normal Compose project/volume. The password is a disposable test value.
"""
import http.client
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URI = 'http://127.0.0.1:8666'
PROJECT = 'auton-acceptance-%s' % os.getpid()
PASSWORD = 'compose-fixture-password-only'
COMPOSE = ['docker', 'compose', '-p', PROJECT]
PROVISION = '''from auton.classes.auth_store import PersistentAuthentication
store = PersistentAuthentication({'path': '/var/lib/autond/auth/auth.db', 'timeout': 5})
try:
    store.provision('operator', 'compose-fixture-password-only', ['read', 'run', 'maintenance'])
finally:
    store.close()
'''


def compose(*args, **kwargs):
    return subprocess.run(COMPOSE + list(args), cwd=ROOT, check=True, timeout=90, **kwargs)


def wait_ready():
    for _ in range(100):
        try:
            with urllib.request.urlopen(URI + '/ui/', timeout=1) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError, http.client.HTTPException):
            pass
        time.sleep(0.1)
    raise RuntimeError('Compose daemon did not become ready')


def main():
    browser = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
    headers = {'Origin': URI}

    def request(path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request_headers = dict(headers)
        if data is not None:
            request_headers['Content-Type'] = 'application/json'
        with browser.open(urllib.request.Request(URI + path, data=data, headers=request_headers), timeout=5) as response:
            return json.load(response)

    try:
        compose('run', '--rm', '--no-deps', '--entrypoint', 'python', 'auton', '-c', PROVISION)
        compose('up', '-d', '--no-build')
        wait_ready()
        try:
            request('/jobs')
        except urllib.error.HTTPError as error:
            assert error.code == 401, error.code
        else:
            raise AssertionError('Unauthenticated job listing was accepted')
        login = request('/ui/auth/login', {'principal': 'operator', 'password': PASSWORD})
        headers['X-CSRF-Token'] = login['csrf']
        request('/run/hello/compose-test-job', {})
        for _ in range(100):
            result = request('/jobs/hello/compose-test-job')
            if result['status'] == 'complete':
                break
            time.sleep(0.05)
        assert result['status'] == 'complete', result
        assert result['return_code'] == 0, result
        assert result['stream'] == ['Hello from Auton!\n'], result
        # Recreate the container, retaining only the named volume and cookie.
        compose('down')
        compose('up', '-d', '--no-build')
        wait_ready()
        restored = request('/jobs/hello/compose-test-job')
        assert restored['stream'] == result['stream'], restored
        assert restored['return_code'] == 0, restored
        print('Compose quickstart: authentication, execution, session and job recovery passed')
    except BaseException:
        subprocess.run(COMPOSE + ['logs', '--no-color'], cwd=ROOT, timeout=15)
        raise
    finally:
        compose('down', '-v', '--remove-orphans')


if __name__ == '__main__':
    main()

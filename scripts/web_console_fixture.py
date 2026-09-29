#!/usr/bin/env python3
"""Private, disposable real daemon for browser acceptance/captures; never public."""
import json
from pathlib import Path
import sys
import time
import requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from web_fixture import WebDaemon

daemon = WebDaemon()
try:
    daemon.start()
    headers = {'Authorization': 'Bearer ' + daemon.token}
    for endpoint, uid, code in (
        ('health-check', 'check-runtime', 'print("Runtime available\\nAll checks passed.")'),
        ('deployment-check', 'check-release', 'import sys; print("Release validation\\nConfiguration valid."); print("A service restart is required.", file=sys.stderr); sys.exit(2)'),
        ('diagnostic', 'check-storage', 'print("Local storage ready\\nHistory: SQLite\\nRetention: 1 hour\\nStatus: healthy")'),
        ('diagnostic', 'check-output', 'print("<img src=x onerror=window.pwned=true>")'),
    ):
        response = requests.post(daemon.uri + '/run/' + endpoint + '/' + uid, headers=headers,
                                 json={'args': ['-c', code]}, timeout=3)
        response.raise_for_status()
        for _ in range(100):
            result = requests.get(daemon.uri + '/jobs/' + endpoint + '/' + uid, headers=headers, timeout=2).json()
            if result['status'] == 'complete':
                break
            time.sleep(0.02)
        if result['status'] != 'complete':
            raise RuntimeError('fixture job did not finish')
    print(json.dumps({'uri': daemon.uri}), flush=True)
    sys.stdin.read()
finally:
    daemon.close()

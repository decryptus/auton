"""Real CLI failure paths: retain output, deadlines and recovery references."""
import http.server
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import unittest
import yaml

from web_fixture import WebDaemon

ROOT = Path(__file__).resolve().parents[1]


class FailureCLITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.daemon = WebDaemon()
        config_path = cls.daemon.path / 'auton.yml'
        config = yaml.safe_load(config_path.read_text())
        config['endpoints']['diagnostic']['config']['timeout'] = 0.3
        config_path.write_text(yaml.safe_dump(config))
        cls.daemon.start()
        cls.token = cls.daemon.path / 'client.token'
        cls.token.write_text(cls.daemon.token)
        cls.token.chmod(0o600)

    @classmethod
    def tearDownClass(cls):
        cls.daemon.close()

    def invoke(self, *args, uri=None, token=None):
        env = {key: value for key, value in os.environ.items() if not key.startswith('AUTON_')}
        env['PYTHONPATH'] = str(ROOT)
        return subprocess.run([sys.executable, str(ROOT / 'bin/auton'),
            '--uri', uri or self.daemon.uri, '-k', str(token or self.token),
            '--endpoint', 'diagnostic', '--http-timeout', '2', *args],
            cwd=str(self.daemon.path), env=env, capture_output=True, text=True, timeout=8)

    def test_command_failure_preserves_output_and_exit_code(self):
        result = self.invoke('-a=-c', '-a=import sys; print("before failure"); print("reason", file=sys.stderr); sys.exit(7)')
        self.assertEqual(result.returncode, 7)
        self.assertIn('before failure', result.stdout)
        self.assertIn('reason', result.stderr)

    def test_command_timeout_returns_124(self):
        result = self.invoke('-a=-c', '-a=import time; time.sleep(2)')
        self.assertEqual(result.returncode, 124, result.stderr)

    def test_expired_token_refuses_submission_and_explains_reference(self):
        grant = self.daemon.auth.issue_token('operator', ['read', 'run'], 1)
        token = self.daemon.path / 'expired.token'
        token.write_text(grant.secret)
        token.chmod(0o600)
        time.sleep(1.1)
        result = self.invoke('--uid', 'expired-check', token=token)
        self.assertEqual(result.returncode, 1)
        self.assertIn('401', result.stderr)
        self.assertIn('Job reference: diagnostic:expired-check', result.stderr)

    def test_unreachable_daemon_reports_generated_job_reference(self):
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0))
            port = sock.getsockname()[1]
        result = self.invoke(uri='http://127.0.0.1:%s' % port)
        self.assertEqual(result.returncode, 1)
        self.assertRegex(result.stderr, r'Job reference: diagnostic:[a-z0-9-]{8,64}')
        self.assertIn('--mode status', result.stderr)

    def test_lost_post_response_is_not_replayed_and_preserves_reference(self):
        posts = []

        class LostResponse(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                posts.append(self.path)
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(('127.0.0.1', 0), LostResponse)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            result = self.invoke(uri='http://127.0.0.1:%s' % server.server_port)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(len(posts), 1)
            uid = posts[0].rsplit('/', 1)[1]
            self.assertIn('Job reference: diagnostic:' + uid, result.stderr)
            self.assertIn('--uid ' + uid, result.stderr)
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()

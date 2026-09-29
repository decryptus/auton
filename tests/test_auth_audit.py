"""Authentication audit privacy, rate bounds and real HTTP refusal coverage."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import requests

from auton.classes.auth_audit import AuthenticationAudit, audit_config
from auton.classes.exceptions import AutonConfigurationError
from web_fixture import WebDaemon


class AuthAuditTests(unittest.TestCase):
    def test_concurrent_rate_bound_and_suppression_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'auth.jsonl'
            now = [0]
            audit = AuthenticationAudit(str(path), max_events_per_minute=2, monotonic=lambda: now[0])
            with ThreadPoolExecutor(max_workers=8) as pool:
                list(pool.map(lambda _: audit.record('auth.denied'), range(100)))
            self.assertEqual(len(path.read_text().splitlines()), 2)
            self.assertEqual(audit.suppressed, 98)
            now[0] = 60
            audit.service_event('login.throttled', principal='PRIVATE', password='PRIVATE')
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual(rows[-2]['event'], 'auth.audit_suppressed')
            self.assertEqual(rows[-2]['count'], 98)
            self.assertEqual(rows[-1]['event'], 'auth.login_throttled')
            self.assertNotIn('PRIVATE', path.read_text())
            for row in rows:
                self.assertEqual(set(row), {'schema_version', 'timestamp', 'event', 'count'})
            with self.assertRaises(ValueError):
                audit.record('PRIVATE')

    def test_private_files_rotation_and_write_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'auth.jsonl'
            audit = AuthenticationAudit(str(path), max_bytes=16384, backup_count=2, max_events_per_minute=1000)
            for _ in range(500):
                audit.record('auth.denied')
            files = list(Path(tmp).glob('auth.jsonl*'))
            self.assertEqual(len(files), 3)
            for file in files:
                self.assertLessEqual(file.stat().st_size, 16384)
                self.assertEqual(file.stat().st_mode & 0o777, 0o600)
                for line in file.read_text().splitlines():
                    json.loads(line)
            with patch.object(audit, '_append', side_effect=OSError('PRIVATE')):
                with self.assertLogs('auton.journal', 'ERROR') as logs:
                    audit.record('auth.denied'); audit.record('auth.denied')
                self.assertEqual(len(logs.output), 1)
                self.assertNotIn('PRIVATE', str(logs.output))
            self.assertEqual(audit.failures, 2)
            path.chmod(0o644)
            with self.assertRaises(OSError):
                AuthenticationAudit(str(path))
            path.chmod(0o600)
            hardlink = Path(tmp) / 'hardlink'
            os.link(path, hardlink)
            with self.assertRaises(OSError):
                AuthenticationAudit(str(path))
            hardlink.unlink()
            Path(tmp).chmod(0o777)
            try:
                with self.assertRaises(ValueError):
                    AuthenticationAudit(str(path))
            finally:
                Path(tmp).chmod(0o700)
            link = Path(tmp) / 'link'
            link.symlink_to(path)
            with self.assertRaises(OSError):
                AuthenticationAudit(str(link))

    def test_configuration_is_explicit_separate_and_bounded(self):
        self.assertIsNone(audit_config({}))
        with tempfile.TemporaryDirectory() as tmp:
            path = str(Path(tmp) / 'auth.jsonl')
            self.assertEqual(audit_config({'auth_audit': {'path': 'auth.jsonl'}}, tmp)['path'], path)
            for settings in ({}, {'path': path, 'secret': 'PRIVATE'},
                             {'path': path, 'max_events_per_minute': True},
                             {'path': path, 'max_events_per_minute': 1},
                             {'path': path, 'max_bytes': 1}):
                with self.subTest(settings=settings), self.assertRaises(AutonConfigurationError):
                    audit_config({'auth_audit': settings})
            for section in ('authentication', 'job_storage'):
                with self.assertRaises(AutonConfigurationError):
                    audit_config({'auth_audit': {'path': path}, section: {'path': path}})
            with self.assertRaises(AutonConfigurationError):
                audit_config({'auth_audit': {'path': path}, 'journal_path': path})

    def test_http_refusals_throttling_and_backend_failure_contain_no_request_fields(self):
        daemon = WebDaemon()
        try:
            daemon.start()
            audit_path = daemon.path / 'auth.jsonl'
            self.assertEqual(requests.get(daemon.uri + '/jobs?PRIVATE_QUERY', timeout=3).status_code, 401)
            self.assertEqual(requests.get(daemon.uri + '/jobs',
                headers={'Authorization': 'Bearer PRIVATE_TOKEN'}, timeout=3).status_code, 401)
            for _ in range(6):
                response = requests.post(daemon.uri + '/ui/auth/login', headers={'Origin': daemon.uri},
                    json={'principal': 'PRIVATE_USER', 'password': 'PRIVATE_PASSWORD'}, timeout=5)
                self.assertEqual(response.status_code, 401)
            reader, _ = daemon.login('reader')
            try:
                self.assertEqual(reader.post(daemon.uri + '/maintenance', json={'enabled': True}, timeout=3).status_code, 403)
            finally:
                reader.close()
            operator, _ = daemon.login()
            try:
                self.assertEqual(operator.post(daemon.uri + '/maintenance', json={'enabled': True}, timeout=3).status_code, 200)
                before = audit_path.read_text()
                self.assertEqual(operator.post(daemon.uri + '/run/diagnostic/audit-job', json={}, timeout=3).status_code, 503)
                self.assertEqual(audit_path.read_text(), before)
            finally:
                operator.close()
            (daemon.path / 'auth.db').chmod(0o644)
            self.assertEqual(requests.get(daemon.uri + '/jobs',
                headers={'Authorization': 'Bearer ' + daemon.token}, timeout=3).status_code, 503)
            self.assertEqual(requests.post(daemon.uri + '/ui/auth/login', headers={'Origin': daemon.uri},
                json={'principal': 'PRIVATE_USER', 'password': 'PRIVATE_PASSWORD'}, timeout=3).status_code, 503)
            text = audit_path.read_text()
            events = [json.loads(line)['event'] for line in text.splitlines()]
            for event in ('auth.denied', 'auth.forbidden', 'auth.login_throttled', 'auth.unavailable'):
                self.assertIn(event, events)
            self.assertEqual(events.count('auth.unavailable'), 2)
            self.assertNotIn('PRIVATE', text)
            self.assertNotIn(daemon.token, text)
        finally:
            (daemon.path / 'auth.db').chmod(0o600)
            daemon.close()

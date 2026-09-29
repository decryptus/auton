"""Bounded, metadata-only authentication audit using the existing JSONL writer."""
from datetime import datetime, timezone
import json
import os
import stat
import time

from auton.classes.exceptions import AutonConfigurationError
from auton.classes.journal import JSONLJournal, DEFAULT_JOURNAL_BYTES, DEFAULT_JOURNAL_BACKUPS

AUDIT_FIELDS = frozenset(('path', 'max_bytes', 'backup_count', 'max_events_per_minute'))
AUDIT_EVENTS = frozenset(('auth.denied', 'auth.forbidden', 'auth.unavailable', 'auth.login_throttled'))
DEFAULT_AUDIT_RATE = 60
MAX_AUDIT_RATE = 10000
AUDIT_WINDOW_SECONDS = 60
MAX_SUPPRESSED = 2147483647


def audit_config(general, directory=None):
    if 'auth_audit' not in general:
        return None
    settings = general['auth_audit']
    if not isinstance(settings, dict) or not set(settings) <= AUDIT_FIELDS:
        raise AutonConfigurationError('auth_audit requires path and optional rotation/rate limits')
    path = settings.get('path')
    if not isinstance(path, str) or not path or '\x00' in path or '://' in path:
        raise AutonConfigurationError('auth_audit.path must name a local file')
    if not os.path.isabs(path):
        if not directory:
            raise AutonConfigurationError('relative auth_audit.path requires a configuration file')
        path = os.path.join(directory, path)
    path = os.path.abspath(path)
    others = [general.get('journal_path')]
    for section in ('authentication', 'job_storage'):
        value = general.get(section)
        if isinstance(value, dict):
            others.append(value.get('path'))
    for other in others:
        if other and os.path.realpath(path) == os.path.realpath(os.path.join(directory or '', other)):
            raise AutonConfigurationError('auth_audit requires a distinct file from jobs and databases')
    result = dict(path=path, max_bytes=settings.get('max_bytes', DEFAULT_JOURNAL_BYTES),
                  backup_count=settings.get('backup_count', DEFAULT_JOURNAL_BACKUPS),
                  max_events_per_minute=settings.get('max_events_per_minute', DEFAULT_AUDIT_RATE))
    rate = result['max_events_per_minute']
    if isinstance(rate, bool) or not isinstance(rate, int) or not 2 <= rate <= MAX_AUDIT_RATE:
        raise AutonConfigurationError('auth_audit.max_events_per_minute must be between 2 and 10000')
    try:
        JSONLJournal(path, result['max_bytes'], result['backup_count'])
    except ValueError:
        raise AutonConfigurationError('invalid auth_audit rotation settings') from None
    return result


class AuthenticationAudit(JSONLJournal):
    def __init__(self, path, max_bytes=DEFAULT_JOURNAL_BYTES, backup_count=DEFAULT_JOURNAL_BACKUPS,
                 max_events_per_minute=DEFAULT_AUDIT_RATE, clock=time.time, monotonic=time.monotonic):
        settings = audit_config({'auth_audit': dict(path=path, max_bytes=max_bytes,
            backup_count=backup_count, max_events_per_minute=max_events_per_minute)})
        super().__init__(settings['path'], max_bytes, backup_count, clock)
        self.rate, self.monotonic = max_events_per_minute, monotonic
        self.window = monotonic()
        self.written = self.suppressed = 0
        parent = os.stat(os.path.dirname(self.path))
        if parent.st_uid != os.geteuid() or parent.st_mode & 0o022:
            raise ValueError('authentication audit directory must be owned by the daemon and not writable by others')
        descriptor = self._open()
        os.close(descriptor)

    def _open(self):
        descriptor = super()._open()
        info = os.fstat(descriptor)
        if info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
            os.close(descriptor)
            raise OSError('authentication audit must be a private, singly linked file owned by the daemon')
        return descriptor

    def _row(self, event, count=1):
        row = dict(schema_version=1, timestamp=datetime.fromtimestamp(
            self.clock(), timezone.utc).isoformat(timespec='milliseconds'), event=event, count=count)
        self._write((json.dumps(row, separators=(',', ':')) + '\n').encode('utf-8'))
        self.written += 1

    def record(self, event):
        if event not in AUDIT_EVENTS:
            raise ValueError('invalid authentication audit event')
        with self.lock:
            now = self.monotonic()
            if now - self.window >= AUDIT_WINDOW_SECONDS:
                self.window, self.written = now, 0
                if self.suppressed:
                    self._row('auth.audit_suppressed', self.suppressed)
                    self.suppressed = 0
            if self.written >= self.rate:
                self.suppressed = min(MAX_SUPPRESSED, self.suppressed + 1)
                return
            self._row(event)

    def service_event(self, event, **unused):
        # Never serialize caller-supplied fields (including attempted principals).
        if event == 'login.throttled':
            self.record('auth.login_throttled')

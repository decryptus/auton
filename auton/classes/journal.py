# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional local lifecycle journal, independent of HTTP and terminal interfaces."""

from datetime import datetime, timezone
import json
import logging
import math
import os
import stat
import threading
import time

LOG = logging.getLogger('auton.journal')
DEFAULT_JOURNAL_BYTES = 10485760
DEFAULT_JOURNAL_BACKUPS = 5
JOURNAL_EVENTS = frozenset(('job.admitted', 'job.started', 'job.completed',
                            'job.failed', 'job.timeout', 'job.rejected', 'job.cancel_requested', 'job.cancelled', 'daemon.maintenance'))
MAX_FIELD_LENGTH = 256
ERROR_LOG_INTERVAL = 60


def record_event(journal, event, **fields):
    if journal is not None:
        try:
            journal.record(event, **fields)
        except Exception:
            # An injected adapter must not turn observability into an execution failure.
            LOG.error('job journal adapter failed')


class JSONLJournal:
    def __init__(self, path, max_bytes=DEFAULT_JOURNAL_BYTES,
                 backup_count=DEFAULT_JOURNAL_BACKUPS, clock=time.time):
        if not isinstance(path, str) or not os.path.isabs(path):
            raise ValueError('journal_path must be an absolute filename')
        if (isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 16384
                or isinstance(backup_count, bool) or not isinstance(backup_count, int)
                or not 1 <= backup_count <= 100):
            raise ValueError('journal_max_bytes must be >= 16384 and journal_backup_count between 1 and 100')
        self.path = path
        self.max_bytes = max_bytes
        self.backup_count = backup_count
        self.clock = clock
        self.lock = threading.Lock()
        self.failures = 0
        self.last_error_log = float('-inf')

    def record(self, event, uid=None, endpoint=None, principal=None,
               return_code=None, duration_ms=None, reason=None, execution_id=None):
        if event not in JOURNAL_EVENTS:
            raise ValueError('invalid journal event')
        row = {'schema_version': 1, 'timestamp': datetime.fromtimestamp(
            self.clock(), timezone.utc).isoformat(timespec='milliseconds'), 'event': event}
        truncated = []
        for name, value in (('job_id', uid), ('endpoint', endpoint),
                            ('principal', principal), ('reason', reason), ('execution_id', execution_id)):
            if isinstance(value, str):
                if len(value) > MAX_FIELD_LENGTH:
                    truncated.append(name)
                row[name] = value[:MAX_FIELD_LENGTH]
            else:
                row[name] = None
        row['return_code'] = return_code if isinstance(return_code, int) else None
        row['duration_ms'] = (round(duration_ms, 3) if isinstance(duration_ms, (int, float))
                              and math.isfinite(duration_ms) and duration_ms >= 0 else None)
        if truncated:
            row['truncated_fields'] = truncated
        encoded = (json.dumps(row, ensure_ascii=True, separators=(',', ':')) + '\n').encode('utf-8')
        with self.lock:
            self._write(encoded)

    def _write(self, encoded):
        """Write under the caller's lock; observability failures stay non-fatal."""
        try:
            self._append(encoded)
        except OSError:
            self.failures += 1
            now = time.monotonic()
            if now - self.last_error_log >= ERROR_LOG_INTERVAL:
                LOG.error('journal write failed; events may be missing (failures=%s)', self.failures)
                self.last_error_log = now

    def _open(self):
        descriptor = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                             os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise OSError('journal must be a regular file')
        return descriptor

    def _append(self, encoded):
        descriptor = self._open()
        try:
            size = os.fstat(descriptor).st_size
            if size and size + len(encoded) > self.max_bytes:
                os.close(descriptor)
                descriptor = None
                for index in range(self.backup_count - 1, 0, -1):
                    source = '%s.%s' % (self.path, index)
                    if os.path.lexists(source):
                        os.replace(source, '%s.%s' % (self.path, index + 1))
                os.replace(self.path, self.path + '.1')
                descriptor = self._open()
            with os.fdopen(descriptor, 'ab') as stream:
                descriptor = None
                stream.write(encoded)
                stream.flush()
        finally:
            if descriptor is not None:
                os.close(descriptor)

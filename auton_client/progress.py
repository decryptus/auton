# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Thread-safe, bounded current operation state; no event history or UI imports."""
import threading

PROGRESS_FIELDS = ('uri', 'job_id', 'status', 'return_code', 'duration_ms')


class OperationProgress:
    def __init__(self):
        self.lock = threading.Lock()
        self.operation_id = None
        self.status = 'pending'
        self.rows = {}
        self.revision = 0

    def begin(self, operation_id, rows):
        # Rows come from the already validated, bounded service plan.
        with self.lock:
            self.operation_id = operation_id
            self.status = 'running'
            self.rows = {key: dict(row, status='pending', job_id=None,
                                   return_code=None, duration_ms=0) for key, row in rows}
            self.revision += 1

    def update(self, key, result):
        with self.lock:
            self.rows[key].update({field: result[field] for field in PROGRESS_FIELDS if field in result})
            self.revision += 1

    def finish(self, status):
        with self.lock:
            self.status = status
            self.revision += 1

    def snapshot(self):
        with self.lock:
            return {'operation_id': self.operation_id, 'status': self.status,
                    'revision': self.revision, 'rows': [dict(row) for row in self.rows.values()]}

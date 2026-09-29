# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transport-independent job values and output retention."""

import copy
import os
import threading
import time
import uuid
from datetime import datetime

from auton.classes.exceptions import AutonTargetFailed
from auton.classes.journal import record_event

STATUS_NEW = 'new'
STATUS_PROCESSING = 'processing'
STATUS_COMPLETE = 'complete'


class JobObject(object): # pylint: disable=useless-object-inheritance
    def __init__(self, name, uid, endpoint, method, payload=None, principal=None, callback=None):
        self.name        = name
        self.uid         = uid
        self.endpoint    = endpoint
        self.method      = method
        self.payload     = copy.deepcopy(payload or {})
        self.result      = []
        self.callback    = callback
        self.status      = STATUS_NEW
        self.return_code = None
        self.prv_pos     = 0
        self.cur_pos     = 0
        self.errors      = []
        self.output_lock = threading.RLock()
        self.output_size = 0
        self.max_output_bytes = 1048576
        self.owner       = principal
        self.journal     = None
        self.outcome     = None
        self.outcome_reason = None
        self.started_monotonic = None
        self.ended_monotonic = None
        self.started_at  = None
        self.ended_at    = None
        self.execution_id = str(uuid.uuid4())
        self.vars        = {'_env_':    os.environ.copy(),
                            '_time_':   datetime.now(),
                            '_gmtime_': datetime.utcnow(),
                            '_uid_':    uid,
                            '_uuid_':   self.execution_id}

    def get_uid(self):
        return self.uid

    def add_error(self, error):
        self._append_output(self.errors, error)
        return self

    def _append_output(self, destination, value):
        with self.output_lock:
            size = len(value.encode('utf-8'))
            if self.output_size + size > self.max_output_bytes:
                raise AutonTargetFailed('job output limit exceeded', code=1)
            destination.append(value)
            self.output_size += size

    def has_error(self):
        return len(self.errors) != 0

    def get_errors(self):
        return self.errors

    def set_return_code(self, rc):
        self.return_code = rc
        return self

    def get_return_code(self):
        return self.return_code

    def add_result(self, result):
        self._append_output(self.result, result)

        return self

    def get_result(self):
        return self.result

    def get_last_result(self, offset=None):
        with self.output_lock:
            if offset is not None:
                return self.result[offset:]
            self.prv_pos = self.cur_pos
            self.cur_pos = len(self.result)
            return self.result[self.prv_pos:self.cur_pos]

    def get_endpoint(self):
        return self.endpoint

    def get_method(self):
        return self.method

    def get_payload(self):
        return self.payload

    def clear_input(self):
        self.payload = None
        self.vars = {}

    def set_status(self, status):
        with self.output_lock:
            previous = self.status
            self.status = status
            if status != previous and status in (STATUS_PROCESSING, STATUS_COMPLETE):
                event = 'job.started' if status == STATUS_PROCESSING else (
                    self.outcome or ('job.completed' if self.return_code == 0 else 'job.failed'))
                duration = None
                if status == STATUS_COMPLETE and self.started_monotonic is not None and self.ended_monotonic is not None:
                    duration = max(0, (self.ended_monotonic - self.started_monotonic) * 1000)
                record_event(self.journal, event, uid=self.uid, endpoint=self.endpoint,
                             principal=self.owner, return_code=self.return_code,
                             duration_ms=duration, reason=self.outcome_reason,
                             execution_id=self.execution_id)

        return self

    def get_status(self):
        return self.status

    def set_started_at(self):
        self.started_at = time.time()
        self.started_monotonic = time.monotonic()

    def get_started_at(self):
        return self.started_at

    def set_ended_at(self):
        self.ended_at = time.time()
        self.ended_monotonic = time.monotonic()

    def get_ended_at(self):
        return self.ended_at

    def get_vars(self):
        return self.vars

    def __call__(self):
        if self.callback:
            self.callback(self)

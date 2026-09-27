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
        self.started_at  = None
        self.ended_at    = None
        self.vars        = {'_env_':    os.environ.copy(),
                            '_time_':   datetime.now(),
                            '_gmtime_': datetime.utcnow(),
                            '_uid_':    uid,
                            '_uuid_':   "%s" % uuid.uuid4()}

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
        self.status = status

        return self

    def get_status(self):
        return self.status

    def set_started_at(self):
        self.started_at = time.time()

    def get_started_at(self):
        return self.started_at

    def set_ended_at(self):
        self.ended_at = time.time()

    def get_ended_at(self):
        return self.ended_at

    def get_vars(self):
        return self.vars

    def __call__(self):
        if self.callback:
            self.callback(self)


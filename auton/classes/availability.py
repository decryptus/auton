# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local admission/launch gate, independent of HTTP and terminal interfaces."""
import threading
from contextlib import contextmanager

from auton.classes.job import STATUS_PROCESSING

MAX_MAINTENANCE_REASON = 512
LAUNCH_WAIT_INTERVAL = 0.1


class MaintenanceActive(Exception):
    pass


class LaunchStopped(Exception):
    pass


class Availability:
    def __init__(self, enabled=False, reason=''):
        self.condition = threading.Condition(threading.RLock())
        self.set(enabled, reason)

    def set(self, enabled, reason=''):
        if type(enabled) is not bool or not isinstance(reason, str) or len(reason) > MAX_MAINTENANCE_REASON:
            raise ValueError('maintenance requires a boolean enabled and a reason of at most 512 characters')
        with self.condition:
            self.enabled = enabled
            self.reason = reason if enabled else ''
            self.condition.notify_all()
            return self.snapshot()

    def snapshot(self):
        with self.condition:
            return {'enabled': self.enabled, 'reason': self.reason}

    @contextmanager
    def admission(self):
        with self.condition:
            if self.enabled:
                raise MaintenanceActive('daemon is in maintenance; no job admitted')
            yield

    @contextmanager
    def launch(self, obj, stopped=lambda: False):
        with self.condition:
            while self.enabled and not stopped():
                self.condition.wait(LAUNCH_WAIT_INTERVAL)
            if stopped():
                raise LaunchStopped('daemon stopped before execution')
            # Transition and adapter launch share the gate with maintenance changes.
            obj.set_started_at()
            obj.set_status(STATUS_PROCESSING)
            yield

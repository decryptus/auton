# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Terminal-independent daemon snapshots and bounded background refresh."""

import queue
import threading
import time

from auton_client.visibility import VisibilityError

SNAPSHOT_READS = ('health', 'endpoints', 'jobs', 'stats')


def snapshot(client, job=None, stopped=None):
    """Collect independently useful reads; preserve partial errors explicitly."""
    data = {'errors': {}, 'checked_at': time.time()}
    for name in SNAPSHOT_READS:
        if stopped is not None and stopped.is_set():
            return data
        try:
            data[name] = getattr(client, name)()
        except VisibilityError as error:
            data['errors'][name] = str(error)
    if job is not None and not (stopped is not None and stopped.is_set()):
        try:
            data['detail'] = client.detail(*job)
        except VisibilityError as error:
            data['errors']['detail'] = str(error)
    return data


class Monitor:
    """At most one active refresh; the caller tags results to reject stale selections."""
    def __init__(self, clients):
        self.clients = dict(clients)
        self.results = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.worker = None

    def refresh(self, daemon, job=None):
        if self.stopped.is_set() or self.worker is not None or not self.results.empty():
            return False
        client = self.clients[daemon]
        def collect():
            try:
                data = snapshot(client, job, self.stopped)
            except Exception:
                # Never send exception reprs containing credentials to a terminal.
                data = {'errors': {'refresh': 'unable to read daemon response'}}
            if not self.stopped.is_set():
                self.results.put_nowait((daemon, job, data))
        self.worker = threading.Thread(target=collect, name='auton-monitor', daemon=True)
        self.worker.start()
        return True

    def poll(self):
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            return None
        self.worker.join()
        self.worker = None
        return result

    def close(self):
        # In-flight GET is bounded by its HTTP timeout. Do not block terminal exit.
        self.stopped.set()

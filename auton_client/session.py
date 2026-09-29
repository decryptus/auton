# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""One explicitly started operation with cooperative observation shutdown."""
import threading
from concurrent.futures import ThreadPoolExecutor


class ExecutionSession:
    def __init__(self):
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.stopped = threading.Event()
        self.closed = False

    @property
    def running(self):
        return self.future is not None and not self.future.done()

    def start(self, service):
        if self.closed or self.running:
            raise ValueError('execution session is unavailable')
        self.stopped = threading.Event()
        self.future = self.pool.submit(service.run, stopped=self.stopped)

    def poll(self):
        if self.future is None or not self.future.done():
            return None
        future, self.future = self.future, None
        return future.result()

    def stop(self):
        self.stopped.set()

    def close(self):
        self.closed = True
        self.stop()
        self.pool.shutdown(wait=True)

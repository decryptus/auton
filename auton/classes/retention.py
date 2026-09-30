"""Daemon-owned periodic retention, including when no clients are polling."""
import logging
import threading
import time

from auton.classes.job import STATUS_COMPLETE

LOG = logging.getLogger(__name__)
MAX_SWEEP_SECONDS = 1
MAX_SWEEP_JOBS = 16
MAX_INTERVAL = 60


class ResultRetention:
    def __init__(self, service):
        self.service = service
        self.stopped = threading.Event()
        self.thread = None
        self.interval = min(MAX_INTERVAL, service.result_ttl)

    def sweep(self):
        deadline = time.monotonic() + MAX_SWEEP_SECONDS
        removed = 0
        with self.service._locked():
            cutoff = self.service.clock() - self.service.result_ttl
            for uid, obj in list(self.service.objs.items()):
                if self.stopped.is_set() or removed >= MAX_SWEEP_JOBS or time.monotonic() >= deadline:
                    break
                if obj.get_status() == STATUS_COMPLETE and obj.get_ended_at() <= cutoff:
                    self.service._delete_job(uid)
                    removed += 1
        return removed

    def _run(self):
        while not self.stopped.wait(self.interval):
            try:
                self.sweep()
            except Exception:
                # Storage failures already mark service health/admission as failed.
                LOG.warning('periodic job retention failed; inspect storage and daemon health')

    def start(self):
        if self.thread is not None:
            raise RuntimeError('retention already started')
        self.thread = threading.Thread(target=self._run, name='auton-retention', daemon=True)
        self.thread.start()
        return self

    def close(self):
        self.stopped.set()
        if self.thread is not None:
            # Stop between deletions; one in-flight bounded store operation may finish.
            self.thread.join()

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job admission, access and retention, independent of transport interfaces."""

import math
import threading
import time
from contextlib import contextmanager

from auton.classes.job import JobObject, STATUS_COMPLETE
from auton.classes.job_schema import validate_input

DEFAULT_RESULT_TTL = 3600
DEFAULT_MAX_JOBS = 128
DEFAULT_MAX_OUTPUT_BYTES = 1048576
DEFAULT_LOCK_TIMEOUT = 5
MAX_OUTPUT_OFFSET = 999999999999


class JobError(Exception):
    pass


class UnknownEndpoint(JobError):
    pass


class UnknownJob(JobError):
    pass


class AccessDenied(JobError):
    pass


class DuplicateJob(JobError):
    pass


class JobUnavailable(JobError):
    pass


class InvalidOffset(JobError):
    pass


def validate_offset(offset):
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int)
                               or offset < 0 or offset > MAX_OUTPUT_OFFSET):
        raise InvalidOffset('invalid output offset')


def job_result(obj, offset=None):
    validate_offset(offset)
    with obj.output_lock:
        result = {'uid': obj.get_uid(),
                  'status': obj.get_status(),
                  'return_code': obj.get_return_code(),
                  'started_at': obj.get_started_at(),
                  'stream': obj.get_last_result(offset),
                  'next_offset': len(obj.result),
                  'ended_at': obj.get_ended_at()}
        if obj.has_error():
            result['errors'] = list(obj.get_errors())
        return result


def job_summary(obj):
    """Return non-streaming metadata suitable for fleet/job listings."""
    with obj.output_lock:
        return {
            'uid': obj.get_uid(),
            'endpoint': obj.get_endpoint(),
            'status': obj.get_status(),
            'return_code': obj.get_return_code(),
            'started_at': obj.get_started_at(),
            'ended_at': obj.get_ended_at(),
            'owner': obj.owner,
            'output_chunks': len(obj.result),
            'error_chunks': len(obj.errors),
        }


class JobService(object):
    def __init__(self, endpoints, queues, objects=None, object_factory=JobObject,
                 clock=time.time, lock=None, lock_timeout=DEFAULT_LOCK_TIMEOUT,
                 result_ttl=DEFAULT_RESULT_TTL, max_jobs=DEFAULT_MAX_JOBS,
                 max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES):
        self.endpoints = endpoints
        self.queues = queues
        self.objs = {} if objects is None else objects
        self.object_factory = object_factory
        self.clock = clock
        self.lock = threading.RLock() if lock is None else lock
        self.lock_timeout = lock_timeout
        self.result_ttl = float(result_ttl)
        self.max_jobs = int(max_jobs)
        self.max_output_bytes = int(max_output_bytes)
        if (not math.isfinite(self.result_ttl) or self.result_ttl <= 0
                or self.max_jobs <= 0 or self.max_output_bytes <= 0):
            raise ValueError('job limits must be positive')

    @contextmanager
    def _locked(self):
        if not self.lock.acquire(timeout=self.lock_timeout):
            raise JobUnavailable('unable to take LOCK for writing after %s seconds'
                                 % self.lock_timeout)
        try:
            yield
        finally:
            self.lock.release()

    @staticmethod
    def uid(endpoint, xid):
        return '%s:%s' % (endpoint, xid)

    def authorize(self, endpoint, principal, obj=None):
        plugin = self.endpoints.get(endpoint)
        if plugin is None:
            raise UnknownEndpoint('unknown endpoint')
        if plugin.users and not plugin.users.get(principal):
            raise AccessDenied('endpoint access denied')
        if obj is not None and obj.owner != principal:
            raise AccessDenied('job access denied')

    def expire_results(self):
        cutoff = self.clock() - self.result_ttl
        for uid, obj in list(self.objs.items()):
            if obj.get_status() == STATUS_COMPLETE and obj.get_ended_at() <= cutoff:
                del self.objs[uid]

    def make_capacity(self):
        if len(self.objs) < self.max_jobs:
            return
        completed = [(obj.get_ended_at(), uid) for uid, obj in self.objs.items()
                     if obj.get_status() == STATUS_COMPLETE]
        if not completed:
            raise JobUnavailable('job capacity reached; retry later')
        del self.objs[min(completed)[1]]

    def get_queue(self, endpoint):
        if endpoint not in self.queues:
            raise UnknownEndpoint('unable to find endpoint: %r' % endpoint)
        return self.queues[endpoint]

    def get_object(self, endpoint, xid):
        uid = self.uid(endpoint, xid)
        if uid not in self.objs:
            raise UnknownJob('unable to find object with uid: %r' % uid)
        return self.objs[uid]

    def clear_object(self, endpoint, xid):
        self.objs.pop(self.uid(endpoint, xid), None)

    def _enqueue(self, endpoint, xid, method, payload, principal):
        queue = self.get_queue(endpoint)
        uid = self.uid(endpoint, xid)
        if uid in self.objs:
            raise DuplicateJob('uid already exists: %r' % uid)
        obj = self.object_factory(queue.name, uid, endpoint, method,
                                  payload=payload, principal=principal)
        obj.max_output_bytes = self.max_output_bytes
        self.objs[uid] = obj
        try:
            queue.qput(obj)
        except Exception:
            del self.objs[uid]
            raise
        return obj

    def submit(self, endpoint, xid, payload, principal, offset=None, method='run'):
        validate_input({'endpoint': endpoint, 'id': xid}, payload)
        validate_offset(offset)
        with self._locked():
            self.authorize(endpoint, principal)
            self.expire_results()
            if self.uid(endpoint, xid) in self.objs:
                raise DuplicateJob('uid already exists')
            # Resolve the queue before evicting a retained result.
            self.get_queue(endpoint)
            self.make_capacity()
            return job_result(self._enqueue(endpoint, xid, method, payload, principal), offset)

    def status(self, endpoint, xid, principal, offset=None):
        validate_input({'endpoint': endpoint, 'id': xid})
        validate_offset(offset)
        with self._locked():
            self.expire_results()
            obj = self.get_object(endpoint, xid)
            self.authorize(endpoint, principal, obj)
            return job_result(obj, offset)

    def list_jobs(self, principal):
        """Return visible jobs without consuming output cursors."""
        with self._locked():
            self.expire_results()
            result = []
            for obj in self.objs.values():
                try:
                    self.authorize(obj.get_endpoint(), principal, obj)
                except AccessDenied:
                    continue
                result.append(job_summary(obj))
            return sorted(result, key=lambda item: (item['started_at'] is None,
                                                    item['started_at'] or 0,
                                                    item['uid']))

    def list_endpoints(self, principal):
        """Return endpoint names visible to the authenticated principal."""
        result = []
        for name in sorted(self.endpoints):
            try:
                self.authorize(name, principal)
            except AccessDenied:
                continue
            queue = self.queues.get(name)
            queue_depth = None
            if queue is not None and hasattr(queue, 'queue') and hasattr(queue.queue, 'qsize'):
                queue_depth = queue.queue.qsize()
            result.append({'name': name, 'queue_depth': queue_depth})
        return result

    def stats(self, principal):
        """Return a compact summary derived from visible jobs/endpoints."""
        jobs = self.list_jobs(principal)
        counts = {}
        for item in jobs:
            counts[item['status']] = counts.get(item['status'], 0) + 1
        return {
            'jobs': len(jobs),
            'jobs_by_status': counts,
            'endpoints': len(self.list_endpoints(principal)),
        }

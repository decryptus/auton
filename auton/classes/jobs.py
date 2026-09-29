# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Job admission, access and retention, independent of transport interfaces."""

import math
import logging
import threading
import time
from contextlib import contextmanager

from auton.classes.job import JobObject, STATUS_NEW, STATUS_PROCESSING, STATUS_COMPLETE
from auton.classes.job_schema import validate_input
from auton.classes.journal import record_event
from auton.classes.availability import Availability, MaintenanceActive
from auton.classes.job_store import JobStoreUnavailable, snapshot, restore

LOG = logging.getLogger(__name__)

DEFAULT_RESULT_TTL = 3600
DEFAULT_MAX_JOBS = 128
DEFAULT_MAX_OUTPUT_BYTES = 1048576
DEFAULT_LOCK_TIMEOUT = 5
MAX_OUTPUT_OFFSET = 999999999999
JOB_STATUSES = (STATUS_NEW, STATUS_PROCESSING, STATUS_COMPLETE)


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


class InvalidFilter(JobError):
    pass


def validate_offset(offset):
    if offset is not None and (isinstance(offset, bool) or not isinstance(offset, int)
                               or offset < 0 or offset > MAX_OUTPUT_OFFSET):
        raise InvalidOffset('invalid output offset')


def job_result(obj, offset=None):
    validate_offset(offset)
    with obj.output_lock:
        if obj.persistence_error:
            raise JobUnavailable('job completion could not be persisted; inspect daemon storage')
        result = {'uid': obj.get_uid(),
                  'status': obj.get_status(),
                  'return_code': obj.get_return_code(),
                  'started_at': obj.get_started_at(),
                  'stream': obj.get_last_result(offset),
                  'next_offset': len(obj.result),
                  'ended_at': obj.get_ended_at()}
        if obj.outcome == 'job.interrupted':
            result.update(outcome=obj.outcome, outcome_reason=obj.outcome_reason,
                          execution_uncertain=obj.execution_uncertain)
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
            'outcome': obj.outcome,
            'outcome_reason': obj.outcome_reason,
            'execution_uncertain': obj.execution_uncertain,
            'storage_error': obj.persistence_error,
        }


class JobService(object):
    def __init__(self, endpoints, queues, objects=None, object_factory=JobObject,
                 clock=time.time, lock=None, lock_timeout=DEFAULT_LOCK_TIMEOUT,
                 result_ttl=DEFAULT_RESULT_TTL, max_jobs=DEFAULT_MAX_JOBS,
                 max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES, journal=None, availability=None,
                 maintenance_operators=(), store=None):
        self.endpoints = endpoints
        self.queues = queues
        self.objs = {} if objects is None else objects
        self.object_factory = object_factory
        self.clock = clock
        self.journal = journal
        self.store = store
        self.persistence_failed = False
        self.availability = Availability() if availability is None else availability
        if (not isinstance(maintenance_operators, (list, tuple)) or
                any(not isinstance(name, str) or not name.strip() for name in maintenance_operators)):
            raise ValueError('maintenance_operators must be a list of authenticated principal names')
        self.maintenance_operators = frozenset(maintenance_operators)
        self.lock = threading.RLock() if lock is None else lock
        self.lock_timeout = lock_timeout
        self.result_ttl = float(result_ttl)
        self.max_jobs = int(max_jobs)
        self.max_output_bytes = int(max_output_bytes)
        if (not math.isfinite(self.result_ttl) or self.result_ttl <= 0
                or self.max_jobs <= 0 or self.max_output_bytes <= 0):
            raise ValueError('job limits must be positive')

        if self.store is not None:
            if self.objs:
                raise ValueError('cannot recover durable jobs into an existing object collection')
            try:
                for record in self.store.recover(self.clock(), self.result_ttl, self.max_jobs):
                    obj = restore(record, self.max_output_bytes)
                    self.objs[obj.uid] = obj
            except JobStoreUnavailable:
                raise JobUnavailable('cannot recover durable jobs') from None

    def _save_job(self, obj):
        try:
            self.store.save(snapshot(obj))
        except Exception:
            self.persistence_failed = True
            obj.persistence_error = True
            LOG.error('job persistence failed; new admissions are disabled')
            if obj.get_status() != STATUS_COMPLETE:
                raise JobUnavailable('job storage unavailable; command not launched') from None
            # Keep the worker alive after final-write failure. Results fail closed,
            # health is degraded, and the last durable state recovers as interrupted.

    def _delete_job(self, uid):
        obj = self.objs.get(uid)
        if obj is None:
            return
        # A terminal status is assigned before its durable write. Wait for that
        # write before deleting, so expiry/eviction cannot resurrect the row.
        with obj.output_lock:
            if self.store is not None:
                if obj.get_status() != STATUS_COMPLETE:
                    raise JobUnavailable('cannot remove an unfinished durable job')
                try:
                    self.store.delete(uid)
                except Exception:
                    self.persistence_failed = True
                    raise JobUnavailable('job storage unavailable') from None
            self.objs.pop(uid, None)

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
                self._delete_job(uid)

    def make_capacity(self):
        if len(self.objs) < self.max_jobs:
            return
        completed = [(obj.get_ended_at(), uid) for uid, obj in self.objs.items()
                     if obj.get_status() == STATUS_COMPLETE]
        if not completed:
            raise JobUnavailable('job capacity reached; retry later')
        self._delete_job(min(completed)[1])

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
        self._delete_job(self.uid(endpoint, xid))

    def _enqueue(self, endpoint, xid, method, payload, principal):
        queue = self.get_queue(endpoint)
        uid = self.uid(endpoint, xid)
        if uid in self.objs:
            raise DuplicateJob('uid already exists: %r' % uid)
        obj = self.object_factory(queue.name, uid, endpoint, method,
                                  payload=payload, principal=principal)
        obj.max_output_bytes = self.max_output_bytes
        obj.journal = self.journal
        obj.availability = self.availability
        obj.admitted_at = self.clock()
        if self.store is not None:
            obj.persistence = self._save_job
            self._save_job(obj)
        self.objs[uid] = obj
        record_event(self.journal, 'job.admitted', uid=uid, endpoint=endpoint,
                     principal=principal, execution_id=obj.execution_id)
        try:
            queue.qput(obj)
        except Exception:
            if self.store is None:
                del self.objs[uid]
            else:
                with obj.output_lock:
                    if obj.get_status() == STATUS_NEW:
                        obj.launch_cancelled = True
                        obj.outcome = 'job.rejected'
                        obj.outcome_reason = 'queue_unavailable'
                        obj.set_return_code(1)
                        obj.set_ended_at()
                        obj.set_status(STATUS_COMPLETE)
                        obj.clear_input()
            record_event(self.journal, 'job.rejected', uid=uid, endpoint=endpoint,
                         principal=principal, reason='queue_unavailable', execution_id=obj.execution_id)
            raise
        return obj

    def submit(self, endpoint, xid, payload, principal, offset=None, method='run'):
        validate_input({'endpoint': endpoint, 'id': xid}, payload)
        validate_offset(offset)
        with self._locked():
            try:
                self.authorize(endpoint, principal)
                if self.persistence_failed:
                    raise JobUnavailable('job storage unavailable; new admissions disabled until restart')
                with self.availability.admission():
                    self.expire_results()
                    if self.uid(endpoint, xid) in self.objs:
                        raise DuplicateJob('uid already exists')
                    self.get_queue(endpoint)
                    self.make_capacity()
                    return job_result(self._enqueue(endpoint, xid, method, payload, principal), offset)
            except (AccessDenied, UnknownEndpoint, DuplicateJob, JobUnavailable, MaintenanceActive) as error:
                record_event(self.journal, 'job.rejected', uid=self.uid(endpoint, xid),
                             endpoint=endpoint, principal=principal, reason=type(error).__name__)
                raise

    def status(self, endpoint, xid, principal, offset=None):
        validate_input({'endpoint': endpoint, 'id': xid})
        validate_offset(offset)
        with self._locked():
            self.expire_results()
            obj = self.get_object(endpoint, xid)
            self.authorize(endpoint, principal, obj)
            return job_result(obj, offset)

    def list_jobs(self, principal, endpoint=None, status=None):
        """Return visible jobs without consuming output cursors."""
        if endpoint is not None and (not isinstance(endpoint, str) or not endpoint):
            raise InvalidFilter('invalid endpoint filter')
        if status is not None and status not in JOB_STATUSES:
            raise InvalidFilter('invalid status filter')
        with self._locked():
            self.expire_results()
            result = []
            for obj in self.objs.values():
                try:
                    self.authorize(obj.get_endpoint(), principal, obj)
                except (AccessDenied, UnknownEndpoint):
                    continue
                item = job_summary(obj)
                if endpoint is not None and item['endpoint'] != endpoint:
                    continue
                if status is not None and item['status'] != status:
                    continue
                result.append(item)
            return sorted(result, key=lambda item: (item['started_at'] is None,
                                                    item['started_at'] or 0,
                                                    item['uid']))

    def list_endpoints(self, principal):
        """Return names and explicitly published descriptions after endpoint ACL checks."""
        result = []
        for name in sorted(self.endpoints):
            try:
                self.authorize(name, principal)
            except AccessDenied:
                continue
            # Shared queue depths would disclose activity belonging to other users.
            item = {'name': name}
            description = getattr(self.endpoints[name], 'discovery', {}).get('description')
            if description:
                item['description'] = description
            result.append(item)
        return result

    def detail(self, endpoint, xid, principal, offset=0):
        """Read metadata and output without advancing the legacy status cursor."""
        validate_input({'endpoint': endpoint, 'id': xid})
        validate_offset(offset)
        if offset is None:
            offset = 0
        with self._locked():
            self.expire_results()
            obj = self.get_object(endpoint, xid)
            self.authorize(endpoint, principal, obj)
            with obj.output_lock:
                result = job_summary(obj)
                result.update(job_result(obj, offset))
                return result

    def health(self):
        """Report local service availability without exposing job data."""
        with self._locked():
            maintenance = self.availability.snapshot()
            result = {'status': 'degraded' if self.persistence_failed else 'ok', 'maintenance': maintenance,
                      'accepting_jobs': not maintenance['enabled'] and not self.persistence_failed}
            if self.store is not None:
                result['storage'] = {'durable': True, 'available': not self.persistence_failed}
            return result

    def set_maintenance(self, principal, enabled, reason=''):
        if principal is None or principal not in self.maintenance_operators:
            raise AccessDenied('maintenance operator access required')
        with self._locked():
            result = self.availability.set(enabled, reason)
            record_event(self.journal, 'daemon.maintenance', principal=principal,
                         reason='enabled' if enabled else 'disabled')
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

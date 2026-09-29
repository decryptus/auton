# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""HTTP decoding and compatibility facade for the job application service."""

import logging
import re

from dwho.classes.modules import DWhoModuleBase, MODULES
from httpdis.ext.httpdis_json import HttpReqErrJson
from sonicprobe.libs.moresynchro import RWLock
from auton.classes.journal import JSONLJournal, DEFAULT_JOURNAL_BYTES, DEFAULT_JOURNAL_BACKUPS

from auton.classes.plugins import (AutonEPTObject, EPTS_SYNC, ENDPOINTS,
                                   STATUS_NEW, STATUS_PROCESSING, STATUS_COMPLETE)
from auton.classes.job_schema import (RUN_QSCHEMA, RUN_PSCHEMA, validate_input,
                                      InvalidArguments, InvalidArgumentsType)
from auton.classes.jobs import (JobService, job_result, UnknownEndpoint, UnknownJob,
                                AccessDenied, DuplicateJob, JobUnavailable, InvalidOffset, InvalidFilter,
                                DEFAULT_RESULT_TTL, DEFAULT_MAX_JOBS,
                                DEFAULT_MAX_OUTPUT_BYTES)

from auton.classes.availability import Availability, MaintenanceActive

MAINTENANCE_FIELDS = frozenset(('enabled', 'reason'))

LOG = logging.getLogger('auton.modules.job')
OUTPUT_OFFSET_PATTERN = re.compile(r'[0-9]{1,12}')
JOB_FILTER_FIELDS = frozenset(('endpoint', 'status'))
HTTP_ERROR_CODES = {UnknownEndpoint: 404, UnknownJob: 404, AccessDenied: 403,
                    DuplicateJob: 415, JobUnavailable: 503, InvalidOffset: 400, InvalidFilter: 400,
                    InvalidArgumentsType: 400, InvalidArguments: 415}
HTTP_ERRORS = tuple(HTTP_ERROR_CODES)


def _http_call(func, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except HTTP_ERRORS as error:
        raise HttpReqErrJson(HTTP_ERROR_CODES[type(error)], str(error))


def _service_property(name):
    return property(lambda self: getattr(self.service, name),
                    lambda self, value: setattr(self.service, name, value))


class _WriteLock(object):
    def __init__(self, module):
        self.module = module

    def acquire(self, timeout):
        return self.module.LOCK.acquire_write(timeout)

    def release(self):
        self.module.LOCK.release()


class JobModule(DWhoModuleBase):
    MODULE_NAME = 'job'
    LOCK = RWLock()  # Legacy class attribute; initialized instances own their lock.
    RUN_QSCHEMA = RUN_QSCHEMA
    RUN_PSCHEMA = RUN_PSCHEMA
    STATUS_QSCHEMA = RUN_QSCHEMA

    objs = _service_property('objs')
    lock_timeout = _service_property('lock_timeout')
    result_ttl = _service_property('result_ttl')
    max_jobs = _service_property('max_jobs')
    max_output_bytes = _service_property('max_output_bytes')

    def safe_init(self, options):
        general = self.config['general']
        self.LOCK = RWLock()
        journal = None
        if general.get('journal_path'):
            journal = JSONLJournal(general['journal_path'],
                                   general.get('journal_max_bytes', DEFAULT_JOURNAL_BYTES),
                                   general.get('journal_backup_count', DEFAULT_JOURNAL_BACKUPS))
        self.service = JobService(ENDPOINTS, EPTS_SYNC, object_factory=AutonEPTObject,
                                  lock=_WriteLock(self), journal=journal,
                                  availability=Availability(general.get('maintenance', False), general.get('maintenance_reason', '')),
                                  maintenance_operators=general.get('maintenance_operators', []),
                                  lock_timeout=general['lock_timeout'],
                                  result_ttl=general.get('result_ttl', DEFAULT_RESULT_TTL),
                                  max_jobs=general.get('max_jobs', DEFAULT_MAX_JOBS),
                                  max_output_bytes=general.get('max_output_bytes', DEFAULT_MAX_OUTPUT_BYTES))

    def _expire_results(self):
        return self.service.expire_results()

    def _make_capacity(self):
        return _http_call(self.service.make_capacity)

    @staticmethod
    def _authorize(endpoint, request, obj=None):
        return _http_call(JobService(ENDPOINTS, EPTS_SYNC).authorize, endpoint,
                          request.get_server_vars().get('HTTP_AUTH_USER'), obj)

    @staticmethod
    def _output_offset(request):
        headers = {key.lower(): value for key, value in request.get_headers().items()}
        value = headers.get('x-auton-output-offset')
        if value is None:
            return None
        if not isinstance(value, str) or not OUTPUT_OFFSET_PATTERN.fullmatch(value):
            raise HttpReqErrJson(400, 'invalid output offset')
        return int(value)

    @staticmethod
    def _get_ept_sync(endpoint):
        return _http_call(JobService(ENDPOINTS, EPTS_SYNC).get_queue, endpoint)

    _get_uid = staticmethod(JobService.uid)

    def _get_obj(self, endpoint, xid):
        return _http_call(self.service.get_object, endpoint, xid)

    def _clear_obj(self, endpoint, xid):
        return self.service.clear_object(endpoint, xid)

    def _push_epts_sync(self, endpoint, xid, method, request):
        # Kept for callers of the historical helper. Admission still checks ACLs,
        # capacity and duplicates through the same service as the HTTP endpoint.
        _http_call(self.service.submit, endpoint, xid, request.payload_params() or {},
                   request.get_server_vars().get('HTTP_AUTH_USER'), offset=0, method=method)
        return self._get_obj(endpoint, xid)

    @staticmethod
    def _http_result(result):
        result['code'] = 400 if result.get('errors') else 200
        return result

    @staticmethod
    def _build_result(obj, offset=None):
        return JobModule._http_result(job_result(obj, offset))

    _build_result_locked = _build_result

    def _handle(self, request, submit=False):
        params = request.query_params()
        if submit:
            params = params or {}
        payload = (request.payload_params() or {}) if submit else None
        _http_call(validate_input, params, payload)
        offset = self._output_offset(request)
        principal = request.get_server_vars().get('HTTP_AUTH_USER')
        try:
            if submit:
                result = _http_call(self.service.submit, params['endpoint'], params['id'],
                                    payload, principal, offset)
            else:
                result = _http_call(self.service.status, params['endpoint'], params['id'],
                                    principal, offset)
            return self._http_result(result)
        except MaintenanceActive:
            raise
        except HttpReqErrJson:
            raise
        except Exception as error:
            LOG.exception(error)
            raise HttpReqErrJson(503, repr(error))

    def job_run(self, request):
        try:
            return self._handle(request, submit=True)
        except MaintenanceActive:
            raise HttpReqErrJson(503, 'daemon_maintenance',
                                headers={'X-Auton-Admission': 'not-admitted'})

    def daemon_maintenance(self, request):
        payload = request.payload_params()
        if not isinstance(payload, dict) or not set(payload) <= MAINTENANCE_FIELDS or 'enabled' not in payload:
            raise HttpReqErrJson(400, 'invalid maintenance payload')
        try:
            result = _http_call(self.service.set_maintenance,
                request.get_server_vars().get('HTTP_AUTH_USER'), **payload)
        except ValueError as error:
            raise HttpReqErrJson(400, str(error))
        return {'code': 200, 'maintenance': result}

    def job_status(self, request):
        return self._handle(request)

    def job_list(self, request):
        params = request.query_params() or {}
        if not isinstance(params, dict) or set(params) - JOB_FILTER_FIELDS:
            raise HttpReqErrJson(400, 'invalid job filters')
        principal = request.get_server_vars().get('HTTP_AUTH_USER')
        return {'code': 200, 'jobs': _http_call(self.service.list_jobs, principal, **params)}

    def job_detail(self, request):
        params = request.query_params()
        _http_call(validate_input, params)
        result = _http_call(self.service.detail, params['endpoint'], params['id'],
                            request.get_server_vars().get('HTTP_AUTH_USER'),
                            self._output_offset(request))
        # A failed job is still a successful inspection request.
        result['code'] = 200
        return result

    def endpoint_list(self, request):
        principal = request.get_server_vars().get('HTTP_AUTH_USER')
        return {'code': 200, 'endpoints': _http_call(self.service.list_endpoints, principal)}

    def daemon_health(self, request):
        result = _http_call(self.service.health)
        result['code'] = 200
        return result

    def daemon_stats(self, request):
        principal = request.get_server_vars().get('HTTP_AUTH_USER')
        return {'code': 200, 'stats': _http_call(self.service.stats, principal)}


if __name__ != '__main__':
    MODULES.register(JobModule())

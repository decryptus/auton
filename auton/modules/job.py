# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""auton.modules.job"""

import copy
import logging
import re

from dwho.classes.modules import DWhoModuleBase, MODULES
from httpdis.ext.httpdis_json import HttpReqErrJson
from sonicprobe.libs import xys
from sonicprobe.libs.moresynchro import RWLock

# pylint: disable=unused-import
from auton.classes.plugins import (AutonEPTObject,
                                   EPTS_SYNC,
                                   STATUS_NEW,
                                   STATUS_PROCESSING,
                                   STATUS_COMPLETE)

LOG = logging.getLogger('auton.modules.job')
xys.add_regex('job.envname', re.compile(r'^[a-zA-Z_][a-zA-Z0-9_]{0,63}$').match)


class JobModule(DWhoModuleBase):
    MODULE_NAME = 'job'

    LOCK        = RWLock()

    # pylint: disable=attribute-defined-outside-init
    def safe_init(self, options):
        self.objs         = {}
        self.lock_timeout = self.config['general']['lock_timeout']

    @staticmethod
    def _get_ept_sync(endpoint):
        if endpoint not in EPTS_SYNC:
            raise HttpReqErrJson(404, "unable to find endpoint: %r" % endpoint)

        return EPTS_SYNC[endpoint]

    @staticmethod
    def _get_uid(endpoint, xid):
        return "%s:%s" % (endpoint, xid)

    def _get_obj(self, endpoint, xid):
        uid      = self._get_uid(endpoint, xid)

        if uid not in self.objs:
            raise HttpReqErrJson(404, "unable to find object with uid: %r" % uid)

        return self.objs[uid]

    def _clear_obj(self, endpoint, xid):
        uid = self._get_uid(endpoint, xid)

        if uid in self.objs:
            del self.objs[uid]

    def _push_epts_sync(self, endpoint, xid, method, request):
        ept_sync = self._get_ept_sync(endpoint)
        uid      = self._get_uid(endpoint, xid)

        if uid in self.objs:
            raise HttpReqErrJson(415, "uid already exists: %r" % uid)

        obj      = AutonEPTObject(ept_sync.name,
                                  uid,
                                  endpoint,
                                  method,
                                  request)
        self.objs[uid] = obj
        ept_sync.qput(obj)

        return obj

    @staticmethod
    def _build_result(obj):
        r = {'code':        200,
             'uid':         obj.get_uid(),
             'status':      obj.get_status(),
             'return_code': obj.get_return_code(),
             'started_at':  obj.get_started_at(),
             'stream':      obj.get_last_result(),
             'ended_at':    obj.get_ended_at()}

        if obj.has_error():
            r['code']   = 400
            r['errors'] = obj.get_errors()

        return r

    RUN_QSCHEMA = xys.load("""
    endpoint: !!str
    id: !!str
    """)

    RUN_PSCHEMA = xys.load("""
    env*:
      !~~regex? (0,64) job.envname: !!str
    envfiles*: !~~seqlen(0,64) [ !!str ]
    args*: !~~seqlen(0,64) [ !!str ]
    argfiles*: !~~seqlen(0,64)
      - arg: !!str
        content: !!str
        filename: !!str
    """)

    def job_run(self, request):
        params  = request.query_params() or {}
        payload = request.payload_params() or {}

        if not isinstance(params, dict):
            raise HttpReqErrJson(400, "invalid arguments type")

        if not xys.validate(params, self.RUN_QSCHEMA):
            raise HttpReqErrJson(415, "invalid arguments for command")

        if not isinstance(payload, dict):
            raise HttpReqErrJson(400, "invalid arguments type")

        if not xys.validate(payload, self.RUN_PSCHEMA):
            raise HttpReqErrJson(415, "invalid arguments for command")

        if not self.LOCK.acquire_write(self.lock_timeout):
            raise HttpReqErrJson(503, "unable to take LOCK for writing after %s seconds" % self.lock_timeout)

        try:
            obj = self._push_epts_sync(params['endpoint'],
                                       params['id'],
                                       'run',
                                       copy.copy(request))

            return self._build_result(obj)
        except HttpReqErrJson:
            raise
        except Exception as e:
            LOG.exception(e)
            raise HttpReqErrJson(503, repr(e))
        finally:
            self.LOCK.release()


    STATUS_QSCHEMA = xys.load("""
    endpoint: !!str
    id: !!str
    """)

    def job_status(self, request):
        params = request.query_params()

        if not isinstance(params, dict):
            raise HttpReqErrJson(400, "invalid arguments type")

        if not xys.validate(params, self.STATUS_QSCHEMA):
            raise HttpReqErrJson(415, "invalid arguments for command")

        obj = self._get_obj(params['endpoint'], params['id'])

        if not self.LOCK.acquire_read(self.lock_timeout):
            raise HttpReqErrJson(503, "unable to take LOCK for reading after %s seconds" % self.lock_timeout)

        try:
            return self._build_result(obj)
        except HttpReqErrJson:
            raise
        except Exception as e:
            LOG.exception(e)
            raise HttpReqErrJson(503, repr(e))
        finally:
            if obj.get_status() == STATUS_COMPLETE:
                self._clear_obj(params['endpoint'], params['id'])
            self.LOCK.release()


if __name__ != "__main__":
    def _start():
        MODULES.register(JobModule())
    _start()

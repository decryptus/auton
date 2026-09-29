# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""auton.classes.plugins"""

import abc
import logging
import copy
import threading

from six.moves import queue as _queue

from dwho.classes.plugins import DWhoPluginBase
from dwho.config import load_credentials

from auton.classes.job import JobObject, STATUS_NEW, STATUS_PROCESSING, STATUS_COMPLETE
from auton.classes.target import AutonTarget
from auton.classes.exceptions import AutonTargetUnauthorized, AutonTargetFailed, AutonTargetTimeout

LOG                   = logging.getLogger('auton.plugins')

DEFAULT_BECOME_METHOD = 'sudo'
DEFAULT_BECOME_USER   = 'root'
DEFAULT_BECOME_OPTS   = {'sudo': ['-H', '-E']}

class AutonPlugins(dict):
    def register(self, plugin):
        if not issubclass(plugin, AutonPlugBase):
            raise TypeError("Invalid Plugin class. (class: %r)" % plugin)
        return dict.__setitem__(self, plugin.PLUGIN_NAME, plugin)

PLUGINS   = AutonPlugins()


class AutonEndpoints(dict):
    def register(self, endpoint):
        if not isinstance(endpoint, AutonPlugBase):
            raise TypeError("Invalid Endpoint class. (class: %r)" % endpoint)
        return dict.__setitem__(self, endpoint.name, endpoint)

ENDPOINTS = AutonEndpoints()


class AutonEPTsSync(dict):
    def register(self, ept_sync):
        if not isinstance(ept_sync, AutonEPTSync):
            raise TypeError("Invalid Endpoint Sync class. (class: %r)" % ept_sync)
        return dict.__setitem__(self, ept_sync.name, ept_sync)

EPTS_SYNC = AutonEPTsSync()


class _RequestSnapshot(object):
    """Legacy plugin view containing detached values, never a live request."""
    def __init__(self, payload, principal, headers=None, params=None):
        self.payload = payload
        self.principal = principal
        self.headers = copy.deepcopy(headers or {})
        self.params = copy.deepcopy(params or {})

    def payload_params(self):
        return self.payload

    def get_server_vars(self):
        return {'HTTP_AUTH_USER': self.principal}

    def get_headers(self):
        return self.headers

    def query_params(self):
        return self.params


class AutonEPTObject(JobObject):
    """Compatibility constructor for plugins using the old request argument."""
    def __init__(self, name, uid, endpoint, method, request=None, callback=None,
                 payload=None, principal=None):
        headers, params = None, None
        if request is not None:
            payload = request.payload_params()
            principal = request.get_server_vars().get('HTTP_AUTH_USER')
            if hasattr(request, 'get_headers'):
                headers = request.get_headers()
            if hasattr(request, 'query_params'):
                params = request.query_params()
        JobObject.__init__(self, name, uid, endpoint, method,
                           payload=payload, principal=principal, callback=callback)
        self.request = _RequestSnapshot(self.payload, self.owner, headers, params)

    def get_request(self):
        return self.request

    def clear_input(self):
        JobObject.clear_input(self)
        self.request = None


class AutonEPTSync(object): # pylint: disable=useless-object-inheritance
    __metaclass__ = abc.ABCMeta

    def __init__(self, name):
        self.name       = name
        self.queue      = _queue.Queue()
        self.results    = {}

    def qput(self, item):
        return self.queue.put(item)

    def qget(self, block = True, timeout = None):
        return self.queue.get(block, timeout)


class AutonPlugBase(threading.Thread, DWhoPluginBase):
    __metaclass__ = abc.ABCMeta

    @abc.abstractproperty
    def PLUGIN_NAME(self):
        return

    def __init__(self, name):
        threading.Thread.__init__(self)
        DWhoPluginBase.__init__(self)

        self.daemon      = True
        self.name        = name
        self.credentials = None
        self.users       = None
        self.target      = None

    def safe_init(self):
        if self.config.get('users'):
            self.users = self.config['users']

        if self.config.get('credentials'):
            self.credentials = load_credentials(self.config['credentials'],
                                                config_dir = self.config['auton']['config_dir'])

        self.target = AutonTarget(**{'name':        self.name,
                                     'config':      self.config['config'],
                                     'credentials': self.credentials})

        EPTS_SYNC.register(AutonEPTSync(self.name))

    def at_start(self):
        if self.name in EPTS_SYNC:
            self.start()

    @staticmethod
    def _set_default_env(env, xvars):
        env.update({'AUTON':            'true',
                    'AUTON_JOB_TIME':   "%s" % xvars['_time_'],
                    'AUTON_JOB_GMTIME': "%s" % xvars['_gmtime_'],
                    'AUTON_JOB_UID':    "%s" % xvars['_uid_'],
                    'AUTON_JOB_UUID':   "%s" % xvars['_uuid_']})

        return env

    @staticmethod
    def _get_become(cfg):
        if not isinstance(cfg, dict) or not cfg.get('enabled'):
            return []

        method = cfg.get('method') or DEFAULT_BECOME_METHOD
        become = [method]

        if method in DEFAULT_BECOME_OPTS:
            become += DEFAULT_BECOME_OPTS[method]

        if method == 'sudo':
            become += ['-u', cfg.get('user') or DEFAULT_BECOME_USER]

        return become

    def run(self):
        while not getattr(self, '_killed', False):
            try:
                obj = EPTS_SYNC[self.name].qget(True, 0.1)
            except _queue.Empty:
                continue
            try:
                if self.users:
                    user = obj.owner
                    if user is None or not self.users.get(user):
                        raise AutonTargetUnauthorized("unauthorized user: %r" % user)

                func = "do_%s" % obj.get_method()
                if not hasattr(self, func):
                    LOG.warning("unknown method %r for endpoint %r", func, self.name)
                    continue

                obj.set_started_at()
                obj.set_status(STATUS_PROCESSING)
                getattr(self, func)(obj)
                obj.set_return_code(0)
            except Exception as e:
                if isinstance(e, AutonTargetTimeout):
                    obj.outcome = 'job.timeout'
                elif isinstance(e, AutonTargetUnauthorized):
                    obj.outcome = 'job.rejected'
                    obj.outcome_reason = 'execution_acl'
                # Preserve an explicit failure even when the output budget is exhausted.
                with obj.output_lock:
                    obj.errors.append("ERROR: %s\n" % str(e)[:4096])
                obj.set_return_code(getattr(e, 'code', None) or 1)
                LOG.exception(e)
            finally:
                self.terminate()
                with obj.output_lock:
                    obj.set_ended_at()
                    obj.set_status(STATUS_COMPLETE)
                    obj.clear_input()
                obj()

    def terminate(self):
        func = 'do_terminate'

        if not hasattr(self, func):
            return

        try:
            getattr(self, func)()
        except Exception as e:
            LOG.debug(e)

    def __call__(self):
        self.start()
        return self

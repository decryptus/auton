# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""auton.classes.plugins"""

import abc
import logging
import os
import threading
import time
import uuid

from datetime import datetime

from six.moves import queue as _queue

from dwho.classes.plugins import DWhoPluginBase
from dwho.config import load_credentials

from auton.classes.target import AutonTarget
from auton.classes.exceptions import AutonTargetUnauthorized

LOG                   = logging.getLogger('auton.plugins')

DEFAULT_BECOME_METHOD = 'sudo'
DEFAULT_BECOME_USER   = 'root'
DEFAULT_BECOME_OPTS   = {'sudo': ['-H', '-E']}

STATUS_NEW            = 'new'
STATUS_PROCESSING     = 'processing'
STATUS_COMPLETE       = 'complete'


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


class AutonEPTObject(object): # pylint: disable=useless-object-inheritance
    def __init__(self, name, uid, endpoint, method, request, callback = None):
        self.name        = name
        self.uid         = uid
        self.endpoint    = endpoint
        self.method      = method
        self.request     = request
        self.result      = []
        self.callback    = callback
        self.status      = STATUS_NEW
        self.return_code = None
        self.prv_pos     = 0
        self.cur_pos     = 0
        self.errors      = []
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
        self.errors.append(error)
        return self

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
        self.result.append(result)

        return self

    def get_result(self):
        return self.result

    def get_last_result(self):
        self.prv_pos = self.cur_pos
        self.cur_pos = len(self.result)

        return self.result[self.prv_pos:self.cur_pos]

    def get_endpoint(self):
        return self.endpoint

    def get_method(self):
        return self.method

    def get_request(self):
        return self.request

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
        while True:
            try:
                obj  = EPTS_SYNC[self.name].qget(True)

                if self.users:
                    user = obj.get_request().get_server_vars().get('HTTP_AUTH_USER')
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
                obj.add_error("ERROR: %s\n" % e)
                obj.set_return_code(getattr(e, 'code', None))
                LOG.exception(e)
            finally:
                obj.set_status(STATUS_COMPLETE)
                obj.set_ended_at()
                obj()

            self.terminate()

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

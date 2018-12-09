# -*- coding: utf-8 -*-
"""auton plugins"""

__author__  = "Adrien DELLE CAVE <adc@doowan.net>"
__license__ = """
    Copyright (C) 2018  fjord-technologies

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along
    with this program; if not, write to the Free Software Foundation, Inc.,
    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA..
"""

import abc
import logging
import os
import Queue
import re
import threading
import time
import uuid

from datetime import datetime
from auton.classes.target import AutonTarget
from auton.classes.exceptions import AutonTargetFailed, AutonTargetTimeout
from dwho.classes.plugins import DWhoPluginBase
from dwho.config import load_credentials

LOG                          = logging.getLogger('auton.plugins')

STATUS_NEW                   = 'new'
STATUS_PROCESSING            = 'processing'
STATUS_COMPLETE              = 'complete'

_RE_MATCH_OBJECT_FUNCS       = ('match', 'search')
_PARAMS_DICT_MODIFIERS_MATCH = re.compile(r'^(?:(?P<modifiers>[\+\-~=%]+)\s)?(?P<key>.+)$').match


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


class AutonEPTObject(object):
    def __init__(self, name, uid, endpoint, method, request, callback = None):
        self.name       = name
        self.uid        = uid
        self.endpoint   = endpoint
        self.method     = method
        self.request    = request
        self.result     = []
        self.callback   = callback
        self.status     = STATUS_NEW
        self.prv_pos    = 0
        self.cur_pos    = 0
        self.errors     = []
        self.started_at = None
        self.ended_at   = None
        self.vars       = {'_env_': os.environ.copy(),
                           '_time_': datetime.now(),
                           '_gmtime_': datetime.utcnow(),
                           '_uid_': uid,
                           '_uuid_': "%s" % uuid.uuid4()}

    def get_uid(self):
        return self.uid

    def add_error(self, error):
        self.errors.append(error)
        return self

    def has_error(self):
        return len(self.errors) != 0

    def get_errors(self):
        return self.errors

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
            return self.callback(self)


class AutonEPTSync(object):
    __metaclass__ = abc.ABCMeta

    def __init__(self, name):
        self.name       = name
        self.queue      = Queue.Queue()
        self.results    = {}

    def qput(self, item):
        return self.queue.put(item)

    def qget(self, block = True, timeout = None):
        return self.queue.get(block, timeout)


class AutonPlugBase(threading.Thread, DWhoPluginBase):
    __metaclass__ = abc.ABCMeta

    def __init__(self, name):
        threading.Thread.__init__(self)
        DWhoPluginBase.__init__(self)
        self.daemon      = True
        self.name        = name
        self.credentials = None
        self.target      = None

    @classmethod
    def _parse_re_flags(cls, flags):
        if isinstance(flags, int):
            return flags
        elif isinstance(flags, list):
            r = 0
            for x in flags:
                r |= cls._parse_re_flags(x)
            return r
        elif isinstance(flags, basestring):
            if flags.isdigit():
                return int(flags)
            return getattr(re, flags)

        return 0

    def _param_regex(self, args, value):
        args         = args.copy()
        func         = args.get('func') or 'sub'
        rfunc        = args.get('return')
        rargs        = args.get('return_args')
        is_match_obj = func in _RE_MATCH_OBJECT_FUNCS

        if is_match_obj and not rfunc:
            rfunc = 'group'
            rargs = [1]

        if is_match_obj and not rargs:
            rargs = [1]

        for x in ('default', 'func', 'return', 'return_args'):
            if x in args:
                del args[x]

        if 'pattern' in args:
            flags = 0
            if 'flags' in args:
                flags = self._parse_re_flags(args.pop('flags'))
            func = getattr(re.compile(pattern = args.pop('pattern'),
                                      flags = flags),
                           func)
        else:
            func = getattr(re, func)

        args['string'] = value
        ret            = func(**args)

        if ret is None:
            return ''

        if not rfunc:
            return ret

        if rargs:
            return getattr(ret, rfunc)(*rargs)

        return getattr(ret, rfunc)()

    def _build_params_dict(self, xtype, cfg, xdict, xvars = None, r = None):
        if r is None:
            r = {}

        if not cfg or not isinstance(cfg, list):
            return r

        fkwargs        = {}
        fkwargs[xtype] = xdict.copy()

        if isinstance(xvars, dict):
            fkwargs.update(xvars)

        for elt in cfg:
            ename = elt.keys()[0]
            m = _PARAMS_DICT_MODIFIERS_MATCH(ename)
            if m:
                modifiers = m.group('modifiers') or '+'
                key       = m.group('key')
            else:
                modifiers = '+'
                key       = ename

            if '+' in modifiers:
                r[key] = elt[ename]
            elif '-' in modifiers:
                if key not in r:
                    continue
                elif elt[ename] in (None, r[key]):
                    del r[key]
            elif '~' in modifiers:
                args = elt[ename]

                if key not in r:
                    r[key] = args.get('default') or ''
                else:
                    r[key] = self._param_regex(args, r[key])
            elif '=' in modifiers:
                if key in r:
                    r[elt[ename]] = r[key]

            if '%' in modifiers:
                r[key] = r[key].format(**fkwargs)

        return r

    def safe_init(self):
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

    def run(self):
        while True:
            r = None

            try:
                obj  = EPTS_SYNC[self.name].qget(True)
                func = "do_%s" % obj.get_method()
                if not hasattr(self, func):
                    LOG.warning("unknown method %r for endpoint %r", func, self.name)
                    continue

                obj.set_started_at()
                obj.set_status(STATUS_PROCESSING)
                getattr(self, func)(obj)
            except Exception, e:
                obj.add_error(str(e))
                LOG.exception("%r", e)
            finally:
                obj.set_status(STATUS_COMPLETE)
                obj.set_ended_at()
                obj()

    def __call__(self):
        self.start()
        return self

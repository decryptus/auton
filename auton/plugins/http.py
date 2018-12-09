# -*- coding: utf-8 -*-
"""auton plugin http"""

__author__  = "Adrien DELLE CAVE"
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

import logging
import os
import re
import requests

from datetime import datetime
from auton.classes.exceptions import AutonConfigurationError
from auton.classes.plugins import AutonPlugBase, AutonTargetFailed, PLUGINS
from sonicprobe.libs import urisup

LOG = logging.getLogger('auton.plugins.http')

_ALLOWED_METHODS = ('delete',
                    'get',
                    'head',
                    'patch',
                    'post',
                    'put')

_RE_MATCH_OBJECT_FUNCS     = ('match', 'search')
_HTTP_DICT_MODIFIERS_MATCH = re.compile(r'^(?:(?P<modifiers>[\+\-~=%]+)\s)?(?P<key>.*)$').match


class AutonHttpPlugin(AutonPlugBase):
    PLUGIN_NAME = 'http'

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

    def _regex(self, args, value):
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

    def _build_http_dict(self, xtype, cfg, xdict, r = None):
        if not r:
            r = {}

        fkwargs = {'env': os.environ,
                   'time': datetime.now(),
                   'gmtime': datetime.utcnow(),
                   xtype: xdict.copy}

        for elt in cfg:
            ename = elt.keys()[0]
            m = _HTTP_DICT_MODIFIERS_MATCH(ename)
            if m:
                modifiers = m.group('modifiers')
                key       = m.group('key')
            else:
                modifiers = '+'
                key       = ename

            if xtype == 'header':
                key = key.lower()

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
                    r[key] = self._regex(args, r[key])
            elif '=' in modifiers:
                if key in r:
                    r[elt[ename]] = r[key]

            if '%' in modifiers:
                r[key] = r[key].format(**fkwargs)

        return r

    def _mk_uri(self, cfg, path):
        uri    = list(urisup.uri_help_split(cfg['url']))
        uri[2] = path
        uri[3] = None

        if 'path' in cfg:
            if isinstance(cfg['path'], dict):
                uri[2] = self._regex(cfg['path'], path)
            del cfg['path']

        return urisup.uri_help_unsplit(uri)

    def _mk_headers(self, cfg, headers):
        r = {}

        for k, v in headers.iteritems():
            if k.lower() != 'content-length':
                r[k.lower()] = v

        if isinstance(cfg, list):
            self._build_http_dict('header', cfg, headers, r)

        return r

    def _mk_params(self, cfg, params):
        r = params

        if isinstance(cfg, list):
            self._build_http_dict('params', cfg, params, r)

        return r

    def do_deploy(self, obj):
        (data, req) = (None, None)

        cfg         = self.target.config
        request     = obj.get_request()
        method      = request.get_method().lower()

        if not cfg.get('url'):
            LOG.error("error on target: %r, missing url in configuration",
                      self.target.name)
            return

        if 'method' in cfg:
            method = cfg.pop('method').lower()

        cfg['url']     = self._mk_uri(cfg, request.get_path())
        cfg['data']    = None

        cfg['headers'] = self._mk_headers(cfg.get('headers'),
                                          request.get_headers().copy())

        cfg['params']  = self._mk_params(cfg.get('params'),
                                         request.query_params().copy())

        if 'remove_payload' in cfg and not cfg.pop('remove_payload'):
            cfg['data'] = request.get_payload()

        if self.target.credentials:
            cfg['auth'] = (self.target.credentials['username'],
                           self.target.credentials['password'])

        if method not in _ALLOWED_METHODS:
            raise AutonConfigurationError("invalid http method: %r" % method)

        try:
            req  = getattr(requests, method)(**cfg)
            data = req.text
            LOG.debug("target: %r, url: %r, headers: %r, params: %r, response payload: %r",
                      self.target.name,
                      cfg['url'],
                      cfg['headers'],
                      cfg['params'],
                      data)
        except Exception, e:
            data = AutonTargetFailed(e)
            LOG.exception("error on target: %r. exception: %r",
                          self.target.name,
                          e)
        finally:
            if req:
                req.close()

        return data


if __name__ != "__main__":
    def _start():
        PLUGINS.register(AutonHttpPlugin)
    _start()

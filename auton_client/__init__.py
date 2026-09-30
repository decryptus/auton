# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Reusable remote Auton client; no terminal or daemon imports."""

import copy
import math
import time

import requests
from requests import exceptions
from urllib3.exceptions import NewConnectionError
from sonicprobe.libs import urisup

SYSLOG_NAME = 'auton'
__version__ = '1.1.0'
DEFAULT_DELAY = 0.5
DEFAULT_HTTP_TIMEOUT = 30


class RemoteClient(object):
    def __init__(self, uris, endpoint, uid, payload=None, auth=None,
                 http_timeout=DEFAULT_HTTP_TIMEOUT, session=None, sleep=time.sleep):
        if not uris:
            raise ValueError('missing variable AUTON_URI')
        if not uid:
            raise ValueError('missing variable AUTON_UID')
        if not endpoint:
            raise ValueError('missing variable AUTON_ENDPOINT')
        if not math.isfinite(http_timeout) or http_timeout <= 0:
            raise ValueError('http-timeout must be positive')
        self.uris = list(uris)
        self.endpoint = endpoint
        self.uid = uid
        self.payload = copy.deepcopy(payload or {})
        from auton_client.credentials import bind_credentials
        self._auth = bind_credentials(auth, self.uris)
        self.http_timeout = http_timeout
        self.session = requests if session is None else session
        self.sleep = sleep
        self.uri = None
        self.output_offset = 0

    @staticmethod
    def _check_results(res = None):
        if not res:
            raise LookupError("unknown error")

        if res.get('message'):
            raise LookupError("invalid request, message: %s" % res['message'])

        if res.get('code'):
            return res

        raise LookupError("errors occurred: %r" % res)

    def _build_uri(self, uri, method):
        r    = list(urisup.uri_help_split(uri))
        r[2] = "/%s/%s/%s" % (method, self.endpoint, self.uid)

        return urisup.uri_help_unsplit(r)

    @staticmethod
    def _build_headers(headers = None):
        if not headers:
            headers = {}

        headers['User-Agent'] = ("%s/%s" % (SYSLOG_NAME, __version__))

        return headers

    @staticmethod
    def _safe_to_retry(error):
        # A disconnect after sending a POST may hide an accepted job. Only
        # fail over when no connection was established, never on a read timeout.
        pending, seen = [error], set()
        while pending:
            exc = pending.pop()
            if id(exc) in seen:
                continue
            seen.add(id(exc))
            if isinstance(exc, (exceptions.ConnectTimeout, NewConnectionError)):
                return True
            pending.extend(x for x in getattr(exc, 'args', ()) if isinstance(x, Exception))
            pending.extend(x for x in (getattr(exc, 'reason', None),
                                       getattr(exc, '__cause__', None)) if isinstance(x, Exception))
        return False

    def do_run(self):
        req      = None
        self.uri = None
        self.output_offset = 0

        try:
            for uri in self.uris:
                try:
                    req  = self.session.post(self._build_uri(uri, 'run'),
                                         auth = self._auth,
                                         headers = self._build_headers(),
                                         timeout = self.http_timeout,
                                         allow_redirects = False,
                                         json = self.payload)
                    self.uri = uri
                    if not 200 <= req.status_code < 300:
                        raise exceptions.HTTPError('run request rejected: HTTP %s' % req.status_code,
                                                   response=req)
                    break
                except exceptions.ConnectionError as error:
                    if not self._safe_to_retry(error):
                        raise

            if not self.uri:
                raise exceptions.ConnectionError("unable to connect autond")

            if not req.text:
                return self._check_results()

            try:
                rs = req.json()
            except ValueError:
                req.raise_for_status()
                return self._check_results()

            return self._check_results(rs)
        finally:
            if req is not None:
                req.close()

    def do_status(self):
        req = None

        try:
            if not self.uri:
                for uri in self.uris:
                    try:
                        req = self.session.get(self._build_uri(uri, 'status'),
                                           auth = self._auth,
                                           headers = self._build_headers({'X-Auton-Output-Offset': str(self.output_offset)}),
                                           timeout = self.http_timeout,
                                           allow_redirects = False)
                        if req.status_code == 404:
                            req.close()
                            continue
                        req.raise_for_status()
                        self.uri = uri
                        break
                    except exceptions.ConnectionError:
                        continue

                if not self.uri:
                    raise exceptions.ConnectionError("unable to connect autond")
            else:
                req = self.session.get(self._build_uri(self.uri, 'status'),
                                   auth = self._auth,
                                   headers = self._build_headers({'X-Auton-Output-Offset': str(self.output_offset)}),
                                   timeout = self.http_timeout,
                                   allow_redirects = False)
                req.raise_for_status()

            if not req.text:
                return self._check_results()

            try:
                rs = req.json()
            except ValueError:
                req.raise_for_status()
                return self._check_results()

            return self._check_results(rs)
        finally:
            if req is not None:
                req.close()


    def iter_results(self, delay=DEFAULT_DELAY):
        """Submit once and yield result batches, advancing explicit output offsets."""
        if not math.isfinite(delay) or delay < 0:
            raise ValueError('delay must be non-negative')
        data = self.do_run()
        while True:
            self.output_offset = data.get('next_offset', self.output_offset)
            yield data
            if data['status'] == 'complete':
                return
            self.sleep(delay)
            data = self.do_status()

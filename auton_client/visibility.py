# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only HTTP adapter for one explicit daemon; never performs failover."""

import math
from urllib.parse import quote, urlsplit, urlunsplit

import requests

from auton_client import RemoteClient, DEFAULT_HTTP_TIMEOUT

READ_ROUTES = {'health': '/health', 'jobs': '/jobs', 'endpoints': '/endpoints',
               'stats': '/stats'}
DETAIL_ROUTE = '/jobs/%s/%s'
JOB_STATES = ('new', 'processing', 'complete')


class VisibilityError(Exception):
    """Safe diagnostic suitable for an operator display (no URL or credentials)."""


class DaemonClient:
    def __init__(self, uri, auth=None, http_timeout=DEFAULT_HTTP_TIMEOUT, session=None):
        parsed = urlsplit(uri)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
            raise ValueError('daemon URI must be an HTTP(S) origin without credentials, path or query')
        try:
            parsed.port
        except ValueError:
            raise ValueError('invalid daemon port') from None
        if not math.isfinite(http_timeout) or http_timeout <= 0:
            raise ValueError('http-timeout must be positive')
        self.uri = urlunsplit((parsed.scheme, parsed.netloc, '', '', ''))
        self.auth = auth
        self.http_timeout = http_timeout
        self.session = requests if session is None else session

    def _read(self, path, params=None):
        response = None
        try:
            response = self.session.get(self.uri + path, params=params,
                                        auth=self.auth, timeout=self.http_timeout,
                                        headers=RemoteClient._build_headers(),
                                        allow_redirects=False)
            if response.status_code != 200:
                raise VisibilityError('HTTP %s' % response.status_code)
            data = response.json()
            if not isinstance(data, dict) or data.get('code') != 200:
                raise VisibilityError('invalid daemon response')
            return data
        except requests.exceptions.Timeout:
            raise VisibilityError('request timed out') from None
        except requests.exceptions.RequestException:
            raise VisibilityError('connection or TLS failure') from None
        except ValueError:
            raise VisibilityError('invalid JSON response') from None
        finally:
            if response is not None:
                response.close()

    def health(self):
        data = self._read(READ_ROUTES['health'])
        if data.get('status') != 'ok':
            raise VisibilityError('daemon is not healthy')
        return data

    def jobs(self):
        data = self._read(READ_ROUTES['jobs']).get('jobs')
        if not isinstance(data, list) or any(
                not isinstance(job, dict) or not isinstance(job.get('uid'), str)
                or not isinstance(job.get('endpoint'), str)
                or not job['uid'].startswith(job['endpoint'] + ':')
                or job.get('status') not in JOB_STATES for job in data):
            raise VisibilityError('invalid jobs response')
        return data

    def endpoints(self):
        data = self._read(READ_ROUTES['endpoints']).get('endpoints')
        if not isinstance(data, list) or any(not isinstance(item, dict)
                or not isinstance(item.get('name'), str) for item in data):
            raise VisibilityError('invalid endpoints response')
        return data

    def stats(self):
        data = self._read(READ_ROUTES['stats']).get('stats')
        if (not isinstance(data, dict)
                or not isinstance(data.get('jobs_by_status'), dict)
                or any(not isinstance(value, int) or value < 0
                       for value in data['jobs_by_status'].values())):
            raise VisibilityError('invalid stats response')
        return data

    def detail(self, endpoint, uid):
        if not uid.startswith(endpoint + ':'):
            raise ValueError('job does not belong to endpoint')
        xid = uid[len(endpoint) + 1:]
        path = DETAIL_ROUTE % (quote(endpoint, safe=''), quote(xid, safe=''))
        data = self._read(path)
        if data.get('uid') != uid or data.get('status') not in JOB_STATES:
            raise VisibilityError('invalid job detail')
        for field in ('stream', 'errors'):
            value = data.get(field, [])
            if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
                raise VisibilityError('invalid job output')
        return data

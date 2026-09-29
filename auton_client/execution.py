# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned execution transport with safe, presentation-independent diagnostics."""
from urllib.parse import quote
import time

from requests import exceptions

from auton_client import RemoteClient

EXECUTION_ROUTE = '/%s/%s/%s'
REJECTED_HTTP_CODES = frozenset((400, 401, 403, 404, 405, 415, 422))
JOB_RESPONSE_CODES = frozenset((200, 400))


class ExecutionError(Exception):
    def __init__(self, message, rejected=False):
        super().__init__(message)
        self.rejected = rejected


class ExecutionClient(RemoteClient):
    """One explicit origin; each submit performs exactly one POST, without replay."""
    def _request(self, method):
        response = None
        try:
            path = EXECUTION_ROUTE % (method, quote(self.endpoint, safe=''), self.uid)
            request = self.session.post if method == 'run' else self.session.get
            kwargs = {'json': self.payload} if method == 'run' else {}
            response = request(self.uris[0] + path, auth=self._auth,
                               headers=self._build_headers({'X-Auton-Output-Offset': str(self.output_offset)}),
                               timeout=self.http_timeout, allow_redirects=False, **kwargs)
            code = response.status_code
            if method == 'run' and code == 503:
                data = response.json()
                if (isinstance(data, dict) and data.get('message') == 'daemon_maintenance'
                        and data.get('code') == 503 and 'uid' not in data
                        and response.headers.get('X-Auton-Admission') == 'not-admitted'):
                    raise ExecutionError('daemon maintenance; no job admitted', rejected=True)
            if code in JOB_RESPONSE_CODES:
                data = response.json()
                # Legacy run/status returns HTTP 400 for a job with stderr.
                if isinstance(data, dict) and data.get('uid') == self.endpoint + ':' + self.uid:
                    return data
                if isinstance(data, dict) and 'uid' in data:
                    raise ExecutionError('unexpected job identity; remote outcome unknown')
            raise ExecutionError('HTTP %s' % code,
                                 rejected=method == 'run' and code in REJECTED_HTTP_CODES)
        except exceptions.RequestException:
            raise ExecutionError('network/TLS failure; remote outcome unknown') from None
        except ValueError:
            raise ExecutionError('invalid JSON response; remote outcome unknown') from None
        finally:
            if response is not None:
                response.close()

    def do_run(self):
        self.output_offset = 0
        timeout = self.http_timeout
        deadline = time.monotonic() + timeout
        self.check_availability()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ExecutionError('availability check exhausted request budget; no job submitted', rejected=True)
        try:
            self.http_timeout = remaining
            return self._request('run')
        finally:
            self.http_timeout = timeout

    def check_availability(self):
        response = None
        try:
            response = self.session.get(self.uris[0] + '/health', auth=self._auth,
                headers=self._build_headers(), timeout=self.http_timeout, allow_redirects=False)
            if response.status_code == 404:
                return  # Older daemons may not expose visibility routes.
            if response.status_code != 200:
                raise ExecutionError('availability check HTTP %s; no job submitted' % response.status_code, rejected=True)
            data = response.json()
            if not isinstance(data, dict) or data.get('status') != 'ok':
                raise ExecutionError('invalid availability response; no job submitted', rejected=True)
            maintenance = data.get('maintenance', {'enabled': False})
            if not isinstance(maintenance, dict) or type(maintenance.get('enabled')) is not bool:
                raise ExecutionError('invalid maintenance response; no job submitted', rejected=True)
            if maintenance['enabled']:
                raise ExecutionError('daemon maintenance; no job submitted', rejected=True)
        except (exceptions.RequestException, ValueError):
            raise ExecutionError('availability check failed; no job submitted', rejected=True) from None
        finally:
            if response is not None:
                response.close()

    def do_status(self):
        return self._request('status')

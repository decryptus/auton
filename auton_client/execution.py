# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pinned execution transport with safe, presentation-independent diagnostics."""
from urllib.parse import quote

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
        return self._request('run')

    def do_status(self):
        return self._request('status')

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit multi-target execution, independent of CLI and terminal presentation."""

import copy
import math
import json
import time
import uuid
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit

from auton_client import DEFAULT_HTTP_TIMEOUT, DEFAULT_DELAY
from auton_client.connections import named_connections, validate_connection_name
from auton_client.execution import ExecutionClient, ExecutionError
from auton_client.visibility import DaemonClient, JOB_STATES

DEFAULT_PARALLEL = 4
MAX_PARALLEL = 32
MAX_TARGETS = 128
DEFAULT_OPERATION_TIMEOUT = 300
MAX_RETAINED_OUTPUT_BYTES = 1048576
OUTPUT_FIELDS = ('stream', 'errors')
MAX_OPERATION_ID = 128


class OperationService:
    def __init__(self, targets, endpoint, payload=None, auth=None,
                 http_timeout=DEFAULT_HTTP_TIMEOUT, parallel=DEFAULT_PARALLEL,
                 timeout=DEFAULT_OPERATION_TIMEOUT, delay=DEFAULT_DELAY,
                 client_factory=ExecutionClient, clock=time.monotonic, sleep=time.sleep):
        if not isinstance(targets, dict) or not 1 <= len(targets) <= MAX_TARGETS:
            raise ValueError('provide between 1 and %s explicit targets' % MAX_TARGETS)
        if (not isinstance(endpoint, str) or not endpoint or
                any(char in endpoint for char in '/?#\r\n')):
            raise ValueError('endpoint must be a nonempty URL path segment')
        if isinstance(parallel, bool) or not isinstance(parallel, int) or not 1 <= parallel <= MAX_PARALLEL:
            raise ValueError('parallel must be between 1 and %s' % MAX_PARALLEL)
        if (not math.isfinite(timeout) or timeout <= 0 or
                not math.isfinite(delay) or delay < 0):
            raise ValueError('operation timeout must be positive and delay non-negative')
        if payload is not None and not isinstance(payload, dict):
            raise ValueError('payload must be a mapping')
        json.dumps(payload, allow_nan=False)  # Reject unserializable inputs before any submission.
        self.targets = {}
        seen = set()
        for name, uri in targets.items():
            validate_connection_name(name)
            origin = DaemonClient(uri, http_timeout=http_timeout).uri
            parsed = urlsplit(origin)
            key = parsed.scheme, parsed.hostname.lower(), parsed.port or (443 if parsed.scheme == 'https' else 80)
            if key in seen:
                raise ValueError('duplicate target origin; aliases must not cause duplicate execution')
            seen.add(key)
            self.targets[name] = origin
        self.endpoint, self.payload, self.auth = endpoint, copy.deepcopy(payload or {}), auth
        self.http_timeout, self.parallel = http_timeout, parallel
        self.timeout, self.delay = timeout, delay
        self.client_factory, self.clock, self.sleep = client_factory, clock, sleep

    @staticmethod
    def _retain(result, field, chunks):
        for chunk in chunks:
            remaining = MAX_RETAINED_OUTPUT_BYTES - result['_output_bytes']
            encoded = chunk.encode('utf-8')
            if len(encoded) > remaining:
                result['output_truncated'] = True
            text = encoded[:remaining].decode('utf-8', errors='ignore')
            if text:
                result[field].append(text)
                result['_output_bytes'] += len(text.encode('utf-8'))

    @staticmethod
    def _validate(data, uid, offset):
        if (not isinstance(data, dict) or data.get('uid') != uid or
                data.get('status') not in JOB_STATES or
                not isinstance(data.get('next_offset'), int) or isinstance(data.get('next_offset'), bool)
                or data['next_offset'] < offset):
            raise ValueError('invalid job response')
        for field in OUTPUT_FIELDS:
            if not isinstance(data.get(field, []), list) or any(
                    not isinstance(item, str) for item in data.get(field, [])):
                raise ValueError('invalid output')
        if data['next_offset'] != offset + len(data.get('stream', [])):
            raise ValueError('invalid output offset')
        if data['status'] == 'complete' and (isinstance(data.get('return_code'), bool)
                                            or not isinstance(data.get('return_code'), int)):
            raise ValueError('missing final exit code')

    def _target(self, name, job_id, deadline, stopped):
        started = self.clock()
        result = {'target': name, 'uri': self.targets[name], 'job_id': job_id,
                  'uid': self.endpoint + ':' + job_id, 'status': 'not_submitted',
                  'return_code': None, 'stdout': [], 'stderr': [], 'error': None,
                  'output_truncated': False, '_output_bytes': 0}
        client = None
        submitted = False
        try:
            remaining = deadline - self.clock()
            if remaining <= 0 or stopped.is_set():
                result['error'] = 'observation stopped before submission'
                return result
            client = self.client_factory([self.targets[name]], self.endpoint, job_id,
                                          payload=copy.deepcopy(self.payload), auth=self.auth,
                                          http_timeout=min(self.http_timeout, remaining))
            # Exactly one POST per target. No application retry, even for connection refusal.
            data = client.do_run()
            submitted = True
            error_offset = 0
            while True:
                self._validate(data, result['uid'], client.output_offset)
                self._retain(result, 'stdout', data.get('stream', []))
                errors = data.get('errors', [])
                if len(errors) < error_offset:
                    raise ValueError('stderr history regressed')
                self._retain(result, 'stderr', errors[error_offset:])
                error_offset = len(errors)
                client.output_offset = data['next_offset']
                result['remote_status'] = data['status']
                if data['status'] == 'complete':
                    result['return_code'] = data['return_code']
                    result['status'] = 'completed' if data['return_code'] == 0 else 'failed'
                    return result
                remaining = deadline - self.clock()
                if remaining <= 0 or stopped.is_set():
                    result.update(status='unknown', error='observation stopped; remote job may still run')
                    return result
                if self.sleep is time.sleep:
                    stopped.wait(min(self.delay, remaining))
                else:
                    self.sleep(min(self.delay, remaining))
                remaining = deadline - self.clock()
                if remaining <= 0 or stopped.is_set():
                    result.update(status='unknown', error='observation stopped; remote job may still run')
                    return result
                client.http_timeout = min(self.http_timeout, remaining)
                data = client.do_status()
        except ExecutionError as error:
            result.update(status='rejected' if not submitted and error.rejected else 'unknown',
                          error=str(error))
        except Exception:
            result.update(status='unknown', error='unable to confirm remote result')
        finally:
            result['duration_ms'] = round(max(0, self.clock() - started) * 1000, 3)
            result.pop('_output_bytes', None)
        return result

    def run(self, operation_id=None):
        operation_id = str(uuid.uuid4()) if operation_id is None else operation_id
        if not isinstance(operation_id, str) or not operation_id or len(operation_id) > MAX_OPERATION_ID:
            raise ValueError('operation_id must contain 1 to 128 characters')
        jobs = {name: str(uuid.uuid4()) for name in self.targets}
        deadline = self.clock() + self.timeout
        stopped = threading.Event()
        with ThreadPoolExecutor(max_workers=self.parallel) as pool:
            futures = [pool.submit(self._target, name, jobs[name], deadline, stopped) for name in self.targets]
            try:
                results = [future.result() for future in futures]
            except KeyboardInterrupt:
                stopped.set()
                results = [future.result() for future in futures]
        success = all(result['status'] == 'completed' for result in results)
        return {'operation_id': operation_id, 'endpoint': self.endpoint,
                'status': 'completed' if success else 'incomplete' if any(
                    result['status'] in ('unknown', 'not_submitted') for result in results) else 'failed',
                'targets': results}

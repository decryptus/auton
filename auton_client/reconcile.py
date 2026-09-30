# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Read-only reconciliation of saved observations against explicit trusted targets."""
import copy
import json
import math
import re
import threading
import time

from auton_client.connections import normalize_origin, origin_key, target_origins
from auton_client.credentials import bind_credentials
from auton_client.operations import (MAX_TARGETS, MAX_RETAINED_OUTPUT_BYTES,
                                     operation_identity, validate_endpoint)
from auton_client.visibility import DaemonClient, VisibilityError

MAX_REPORT_BYTES = 16 * 1024 * 1024
MAX_REPORT_JOBS = 16384
MAX_RECONCILED_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_ENDPOINT_LENGTH = 256
MAX_JOB_ID_LENGTH = 128
JOB_ID_PATTERN = re.compile(r'[a-zA-Z0-9_-]+')
OBSERVATION_STATES = frozenset(('completed', 'failed', 'unknown', 'rejected',
                               'not_submitted', 'skipped', 'queued', 'running'))
UNSUBMITTED_STATES = frozenset(('not_submitted', 'skipped', 'rejected'))


def _reject_constant(value):
    raise ValueError('invalid JSON number')


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key in report')
        result[key] = value
    return result


def load_report(path):
    with open(path, 'rb') as stream:
        raw = stream.read(MAX_REPORT_BYTES + 1)
    if len(raw) > MAX_REPORT_BYTES:
        raise ValueError('report exceeds input limit')
    try:
        return json.loads(raw, object_pairs_hook=_unique_object,
                          parse_constant=_reject_constant)
    except (UnicodeError, RecursionError, json.JSONDecodeError):
        raise ValueError('invalid JSON report') from None


def summary_status(rows):
    states = [row['status'] for row in rows]
    if states and all(state == 'completed' for state in states):
        return 'completed'
    if any(state in ('unknown', 'not_submitted', 'skipped', 'queued', 'running', 'incomplete') for state in states):
        return 'incomplete'
    return 'failed'


class ReconciliationService:
    """One bounded snapshot; never submits, fails over or resumes scenario steps."""
    def __init__(self, report, targets, auth=None, http_timeout=30, timeout=300,
                 client_factory=DaemonClient, clock=time.monotonic):
        if not isinstance(report, dict) or not report.get('operation_id'):
            raise ValueError('report requires an operation_id')
        operation_identity(report['operation_id'])
        if (not math.isfinite(http_timeout) or http_timeout <= 0
                or not math.isfinite(timeout) or timeout <= 0):
            raise ValueError('http-timeout must be positive')
        if not isinstance(targets, dict) or not 1 <= len(targets) <= MAX_TARGETS:
            raise ValueError('explicit trusted targets are required')
        try:
            raw = json.dumps(report, allow_nan=False).encode('utf-8')
        except (TypeError, ValueError, RecursionError):
            raise ValueError('invalid JSON report') from None
        if len(raw) > MAX_REPORT_BYTES:
            raise ValueError('report exceeds input limit')
        self.report = copy.deepcopy(report)
        rows = self.report.get('targets')
        if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_TARGETS:
            raise ValueError('invalid report targets')
        self.jobs = []
        job_count = 0
        if report.get('kind') not in (None, 'scenario'):
            raise ValueError('unknown report kind')
        self.scenario = report.get('kind') == 'scenario'
        seen = set()
        for target in rows:
            if not isinstance(target, dict):
                raise ValueError('invalid target record')
            name = target.get('target')
            if not isinstance(name, str) or name not in targets or name in seen:
                raise ValueError('report targets must be unique and explicitly selected')
            seen.add(name)
            allowed = {origin_key(uri): uri for uri in target_origins(targets[name])}
            if self.scenario:
                scenarios = target.get('scenarios')
                if not isinstance(scenarios, list) or not scenarios:
                    raise ValueError('invalid report scenarios')
                step_rows = []
                for scenario in scenarios:
                    if (not isinstance(scenario, dict) or not isinstance(scenario.get('steps'), list)
                            or not scenario['steps']):
                        raise ValueError('invalid report steps')
                    if not isinstance(scenario.get('name'), str) or not scenario['name']:
                        raise ValueError('invalid scenario name')
                    for step in scenario['steps']:
                        if not isinstance(step, dict) or not isinstance(step.get('step'), str):
                            raise ValueError('invalid step name')
                    step_rows.extend(scenario['steps'])
            else:
                step_rows = [target]
            job_count += len(step_rows)
            if job_count > MAX_REPORT_JOBS:
                raise ValueError('too many report jobs')
            for row in step_rows:
                if not isinstance(row, dict) or row.get('status') not in OBSERVATION_STATES:
                    raise ValueError('invalid observation status')
                if (row.get('error') is not None and not isinstance(row['error'], str)
                        or type(row.get('output_truncated', False)) is not bool):
                    raise ValueError('invalid saved diagnostics')
                for field in ('stdout', 'stderr'):
                    if (not isinstance(row.get(field, []), list)
                            or any(not isinstance(chunk, str) for chunk in row.get(field, []))):
                        raise ValueError('invalid saved output')
                row.setdefault('stdout', [])
                row.setdefault('stderr', [])
                row.setdefault('return_code', None)
                row.setdefault('job_id', None)
                row.setdefault('output_truncated', False)
                if row['status'] in UNSUBMITTED_STATES:
                    continue
                endpoint = validate_endpoint(row.get('endpoint', report.get('endpoint')))
                if (len(endpoint) > MAX_ENDPOINT_LENGTH or endpoint in ('.', '..')
                        or any(not char.isprintable() for char in endpoint)):
                    raise ValueError('invalid endpoint identity')
                row['target'] = name
                job_id = row.get('job_id')
                if (not isinstance(job_id, str) or len(job_id) > MAX_JOB_ID_LENGTH
                        or JOB_ID_PATTERN.fullmatch(job_id) is None
                        or row.get('uid') != endpoint + ':' + job_id):
                    raise ValueError('invalid job identity')
                key = origin_key(normalize_origin(row.get('uri')))
                if key not in allowed:
                    raise ValueError('report origin is outside the selected target inventory')
                # Use the inventory spelling, not an untrusted report's destination.
                self.jobs.append((row, endpoint, allowed[key]))
        self.auth = bind_credentials(auth, [origin for _, _, origin in self.jobs])
        self.http_timeout = http_timeout
        self.client_factory = client_factory
        self.timeout, self.clock = timeout, clock

    def run(self, stopped=None, progress=None):
        stopped = threading.Event() if stopped is None else stopped
        result = copy.deepcopy(self.report)
        deadline = self.clock() + self.timeout
        rows = [step for target in result['targets']
                for scenario in target.get('scenarios', []) for step in scenario['steps']] if self.scenario else result['targets']
        if progress is not None:
            progress.begin(result['operation_id'], [(str(index), {'target': row.get('target', ''),
                'endpoint': endpoint, 'scenario': '', 'step': row.get('step', ''), 'uri': origin})
                for index, (row, endpoint, origin) in enumerate(self.jobs)])
        pending = iter(self.jobs)
        index = 0
        output_budget = MAX_RECONCILED_OUTPUT_BYTES
        target_budgets = {target['target']: MAX_RETAINED_OUTPUT_BYTES for target in result['targets']}
        for row in rows:
            if row['status'] in UNSUBMITTED_STATES:
                continue
            _, endpoint, origin = next(pending)
            row['previous_status'] = row['status']
            try:
                remaining_time = deadline - self.clock()
                if remaining_time <= 0 or stopped.is_set():
                    raise VisibilityError('reconciliation deadline reached')
                client = self.client_factory(origin, auth=self.auth,
                                             http_timeout=min(self.http_timeout, remaining_time))
                data = client.detail(endpoint, row['uid'])
                if data.get('status') == 'complete' and type(data.get('return_code')) is not int:
                    raise VisibilityError('invalid terminal return code')
                if data.get('execution_uncertain'):
                    raise VisibilityError('daemon recovery: execution outcome is uncertain')
                if data.get('outcome'):
                    row['outcome'] = data['outcome']
                state = data['status']
                row.update(remote_status=state, error=None,
                           status=('completed' if data['return_code'] == 0 else 'failed') if state == 'complete'
                           else ('queued' if state == 'new' else 'running'),
                           return_code=data.get('return_code') if state == 'complete' else None)
                remaining = min(output_budget, target_budgets[row['target']])
                row['output_truncated'] = False
                for source, field in (('stream', 'stdout'), ('errors', 'stderr')):
                    row[field] = []
                    for chunk in data.get(source, []):
                        encoded = chunk.encode('utf-8')
                        row['output_truncated'] |= len(encoded) > remaining
                        text = encoded[:remaining].decode('utf-8', errors='ignore')
                        row[field].append(text)
                        retained = len(text.encode('utf-8'))
                        remaining -= retained
                        output_budget -= retained
                        target_budgets[row['target']] -= retained
            except VisibilityError as error:
                row.update(status='unknown', error=str(error))
            if progress is not None:
                progress.update(str(index), row)
            index += 1
        if self.scenario:
            for target in result['targets']:
                for scenario in target['scenarios']:
                    scenario['status'] = summary_status(scenario['steps'])
                target['status'] = summary_status([step for scenario in target['scenarios'] for step in scenario['steps']])
        result['status'] = summary_status(result['targets'])
        if progress is not None:
            progress.finish(result['status'])
        result['reconciled'] = True
        return result

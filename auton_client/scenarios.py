# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Validated linear scenarios and per-target execution, independent of interfaces."""
import copy
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from auton_client.connections import validate_connection_name
from auton_client.operations import (OperationService, MAX_RETAINED_OUTPUT_BYTES,
                                     validate_endpoint, operation_identity, operation_status)
from auton_client.selectors import NameSelector, MAX_SELECTORS

MAX_SCENARIOS = 128
MAX_STEPS = 32
MAX_SELECTED_STEPS = 128
MAX_STEP_INPUT_BYTES = 65536
MAX_DESCRIPTION_LENGTH = 512
SCENARIO_FIELDS = frozenset(('version', 'description', 'steps'))
STEP_FIELDS = frozenset(('name', 'endpoint', 'args', 'env', 'continue_on_error'))
OUTPUT_FIELDS = ('stdout', 'stderr')


def validate_scenarios(scenarios):
    if not isinstance(scenarios, dict) or len(scenarios) > MAX_SCENARIOS:
        raise ValueError('scenarios must be a mapping with at most 128 entries')
    validated = {}
    for name, definition in scenarios.items():
        validate_connection_name(name)
        if not isinstance(definition, dict) or not set(definition) <= SCENARIO_FIELDS:
            raise ValueError('invalid scenario fields')
        if type(definition.get('version')) is not int or definition['version'] != 1:
            raise ValueError('scenario version must be 1')
        description = definition.get('description', '')
        if not isinstance(description, str) or len(description) > MAX_DESCRIPTION_LENGTH:
            raise ValueError('invalid scenario description')
        steps = definition.get('steps')
        if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
            raise ValueError('scenario must have 1-32 ordered steps')
        names = set()
        for step in steps:
            if not isinstance(step, dict) or not set(step) <= STEP_FIELDS:
                raise ValueError('invalid scenario step fields')
            if type(step.get('continue_on_error', False)) is not bool:
                raise ValueError('continue_on_error must be a boolean')
            validate_connection_name(step.get('name'))
            if step['name'] in names:
                raise ValueError('duplicate step name')
            names.add(step['name'])
            validate_endpoint(step.get('endpoint'))
            args, env = step.get('args', []), step.get('env', {})
            if not isinstance(args, list) or any(not isinstance(value, str) or '\x00' in value for value in args):
                raise ValueError('step args must be a list of strings')
            if not isinstance(env, dict) or any(not isinstance(key, str) or not key or '=' in key or '\x00' in key
                    or not isinstance(value, str) or '\x00' in value for key, value in env.items()):
                raise ValueError('step env must map nonempty names to strings')
            if len(json.dumps(step).encode('utf-8')) > MAX_STEP_INPUT_BYTES:
                raise ValueError('step input exceeds 64 KiB')
        validated[name] = copy.deepcopy(definition)
    return validated


def validate_scenario_groups(groups, scenarios):
    if not isinstance(groups, dict) or len(groups) > MAX_SCENARIOS:
        raise ValueError('scenario_groups must be a mapping with at most 128 entries')
    for name, members in groups.items():
        validate_connection_name(name)
        if not isinstance(members, list) or not 1 <= len(members) <= MAX_SCENARIOS:
            raise ValueError('scenario group must contain 1-128 scenario names')
        seen = set()
        for member in members:
            validate_connection_name(member)
            if member not in scenarios or member in seen:
                raise ValueError('unknown or duplicate scenario group member')
            seen.add(member)
    return copy.deepcopy(groups)


def select_scenarios(patterns, group_patterns, scenarios, groups):
    if len(patterns) + len(group_patterns) > MAX_SELECTORS:
        raise ValueError('too many scenario selectors')
    matcher = NameSelector()
    selected = {}
    for pattern in patterns:
        for name in matcher.select(pattern, scenarios):
            selected.setdefault(name, scenarios[name])
    for pattern in group_patterns:
        for group in matcher.select(pattern, groups):
            for name in groups[group]:
                selected.setdefault(name, scenarios[name])
    if not selected:
        raise ValueError('select at least one scenario')
    return selected


class ScenarioService:
    def __init__(self, targets, scenarios, **options):
        self.scenarios = validate_scenarios(scenarios)
        if not self.scenarios or sum(len(s['steps']) for s in self.scenarios.values()) > MAX_SELECTED_STEPS:
            raise ValueError('select between 1 and 128 total steps')
        self.steps = []
        # Construct/validate every step adapter before any target can submit.
        for scenario, definition in self.scenarios.items():
            for step in definition['steps']:
                service = OperationService(targets, step['endpoint'],
                    payload={'args': step.get('args', []), 'env': step.get('env', {})}, **options)
                self.steps.append((scenario, step['name'], service))
        self.settings = self.steps[0][2]

    def _target(self, name, deadline, stopped, progress):
        started = self.settings.clock()
        target = {'target': name, 'uri': self.settings.targets[name], 'status': 'completed',
                  'scenarios': [], 'output_truncated': False}
        remaining_output = MAX_RETAINED_OUTPUT_BYTES
        blocked = None
        origin = None
        current = None
        for scenario, step_name, service in self.steps:
            if current is None or current['name'] != scenario:
                current = {'name': scenario, 'status': 'skipped' if blocked else 'completed', 'steps': []}
                target['scenarios'].append(current)
            if blocked:
                result = {'status': 'skipped', 'job_id': None, 'uid': None,
                          'return_code': None, 'stdout': [], 'stderr': [],
                          'error': 'earlier step did not complete successfully: ' + blocked,
                          'output_truncated': False, 'duration_ms': 0}
            else:
                result = service.execute_target(name, str(uuid.uuid4()), deadline, stopped,
                                                output_limit=remaining_output, origin=origin,
                                                progress=progress, progress_key=(name, scenario, step_name))
                if result['status'] == 'completed':
                    origin = result['uri']
                target['uri'] = result['uri']
                remaining_output -= sum(len(chunk.encode('utf-8')) for field in OUTPUT_FIELDS
                                        for chunk in result[field])
                target['output_truncated'] |= result['output_truncated']
                if result['status'] != 'completed':
                    definition = next(step for step in self.scenarios[scenario]['steps'] if step['name'] == step_name)
                    continuing = result['status'] == 'failed' and definition.get('continue_on_error', False)
                    result['continued_after_error'] = continuing
                    if continuing:
                        origin = result['uri']
                    else:
                        blocked = scenario + '/' + step_name
                    current['status'] = target['status'] = result['status']
            if blocked and result['status'] == 'skipped' and progress is not None:
                progress.update((name, scenario, step_name), result)
            result.update(step=step_name, endpoint=service.endpoint)
            current['steps'].append(result)
        target['duration_ms'] = round(max(0, self.settings.clock() - started) * 1000, 3)
        return target

    def run(self, operation_id=None, stopped=None, progress=None):
        operation_id = operation_identity(operation_id)
        if progress is not None:
            progress.begin(operation_id, [((name, scenario, step),
                {'target': name, 'scenario': scenario, 'step': step,
                 'endpoint': service.endpoint, 'uri': self.settings.targets[name]})
                for name in self.settings.targets for scenario, step, service in self.steps])
        deadline = self.settings.clock() + self.settings.timeout
        stopped = threading.Event() if stopped is None else stopped
        with ThreadPoolExecutor(max_workers=self.settings.parallel) as pool:
            futures = [pool.submit(self._target, name, deadline, stopped, progress) for name in self.settings.targets]
            try:
                results = [future.result() for future in futures]
            except KeyboardInterrupt:
                stopped.set()
                results = [future.result() for future in futures]
        if progress is not None:
            progress.finish(operation_status(results))
        return {'operation_id': operation_id, 'kind': 'scenario', 'scenarios': list(self.scenarios),
                'status': operation_status(results), 'targets': results}

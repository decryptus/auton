# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Terminal preparation and explicit confirmation; execution lives in services."""
import curses
import json

from auton_client.operations import OperationService
from auton_client.scenarios import ScenarioService, select_scenarios
from auton_client.connections import select_connections

SECTIONS = ('Targets', 'Target groups', 'Scenarios', 'Scenario groups', 'Endpoints')
MAX_INPUT = 4096
PAYLOAD_FIELDS = frozenset(('args', 'env'))


class PreparationView:
    def __init__(self, connections, groups, scenarios, scenario_groups, session, settings):
        self.connections = dict(connections)
        self.groups = {name: members for name, members in groups.items()
                       if all(member in connections for member in members)}
        self.scenarios = scenarios
        self.scenario_groups = {name: members for name, members in scenario_groups.items()
                                if all(member in scenarios for member in members)}
        self.session, self.settings = session, settings
        self.selected = [[] for _ in SECTIONS]
        self.section = self.index = self.scroll = 0
        self.search = ''
        self.editing = None
        self.input = '{"args": [], "env": {}}'
        self.mode = 'select'
        self.error = ''
        self.preview = []
        self.service = None
        self.result = None
        self.result_lines = None
        self.endpoints = []

    def update(self, snapshots):
        self.endpoints = sorted({item['name'] for data in snapshots.values()
                                 for item in data.get('endpoints', [])})
        try:
            result = self.session.poll()
        except Exception:
            self.error = 'Unable to collect operation result; do not replay. Inspect daemon jobs.'
            self.mode = 'result'
        else:
            if result is not None:
                self.result = result
                self.result_lines = None
                self.mode = 'result'
                self.scroll = 0

    def rows(self):
        catalogues = (self.connections, self.groups, self.scenarios, self.scenario_groups, self.endpoints)
        return [name for name in catalogues[self.section] if self.search.casefold() in name.casefold()]

    def prepare(self):
        targets = select_connections(self.selected[0], self.selected[1], self.connections, self.groups)
        if not targets:
            raise ValueError('Select at least one target or target group')
        preview = ['Targets:'] + ['  %s = %s' % item for item in targets.items()]
        if self.selected[2] or self.selected[3]:
            scenarios = select_scenarios(self.selected[2], self.selected[3], self.scenarios, self.scenario_groups)
            service = ScenarioService(targets, scenarios, **self.settings)
            for name, definition in scenarios.items():
                preview.append('Scenario: ' + name)
                for step in definition['steps']:
                    preview.append('  %s -> %s' % (step['name'], step['endpoint']))
                    preview.append('    args=' + json.dumps(step.get('args', [])))
                    preview.append('    env=' + json.dumps(step.get('env', {})))
        else:
            if len(self.selected[4]) != 1:
                raise ValueError('Select one endpoint or one or more scenarios')
            try:
                payload = json.loads(self.input)
            except ValueError:
                raise ValueError('Inputs must be valid JSON') from None
            if (not isinstance(payload, dict) or not set(payload) <= PAYLOAD_FIELDS
                    or not isinstance(payload.get('args', []), list)
                    or any(not isinstance(value, str) or '\x00' in value for value in payload.get('args', []))
                    or not isinstance(payload.get('env', {}), dict)
                    or any(not isinstance(key, str) or not key or '=' in key or '\x00' in key
                           or not isinstance(value, str) or '\x00' in value
                           for key, value in payload.get('env', {}).items())):
                raise ValueError('Inputs require args: string list and env: string mapping')
            endpoint = self.selected[4][0]
            service = OperationService(targets, endpoint, payload=payload, **self.settings)
            preview.extend(['Endpoint: ' + endpoint, 'Inputs: ' + json.dumps(payload)])
        preview.extend(['Parallel targets: %s; observation deadline: %ss' %
                        (service.settings.parallel if isinstance(service, ScenarioService) else service.parallel,
                         self.settings.get('timeout', 300)),
                        'Server authentication and endpoint ACLs remain authoritative.',
                        'No automatic replay, rollback or remote cancellation.'])
        self.service, self.preview = service, preview
        self.mode, self.scroll = 'confirm', 0

    def handle(self, key):
        if self.editing is not None:
            if key in (10, 13, 27):
                self.editing = None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                setattr(self, self.editing, getattr(self, self.editing)[:-1])
            elif 32 <= key <= 126 and len(getattr(self, self.editing)) < MAX_INPUT:
                setattr(self, self.editing, getattr(self, self.editing) + chr(key))
            return True
        if key in (ord('q'), 27):
            if self.session.running:
                self.error = 'Execution active: x stops observation; remote jobs are not cancelled.'
                return True
            if self.mode == 'confirm':
                self.mode = 'select'
                self.service = None
                return True
            return False
        if self.mode == 'running':
            if key == ord('x'):
                self.session.stop()
                self.error = 'Stopping observation; waiting for in-flight requests. Remote jobs may continue.'
            return True
        if self.mode == 'confirm':
            if key == ord('y'):
                self.session.start(self.service)
                self.result = self.result_lines = None
                self.mode, self.error = 'running', ''
            elif key == ord('n'):
                self.mode = 'select'
                self.service = None
            elif key in (curses.KEY_DOWN, ord('j')):
                self.scroll += 1
            elif key in (curses.KEY_UP, ord('k')):
                self.scroll = max(0, self.scroll - 1)
            return True
        if self.mode == 'result':
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_NPAGE):
                self.scroll += 10 if key == curses.KEY_NPAGE else 1
            elif key in (curses.KEY_UP, ord('k'), curses.KEY_PPAGE):
                self.scroll = max(0, self.scroll - (10 if key == curses.KEY_PPAGE else 1))
            return True
        if key == 9:
            self.section = (self.section + 1) % len(SECTIONS)
            self.index = 0
            self.search = ''
        elif key == ord('/'):
            self.editing = 'search'
            self.index = 0
        elif key == ord('i'):
            self.editing = 'input'
            self.input = ''
        elif key in (curses.KEY_DOWN, ord('j'), curses.KEY_UP, ord('k')):
            self.index = min(max(0, len(self.rows()) - 1), max(0, self.index +
                (-1 if key in (curses.KEY_UP, ord('k')) else 1)))
        elif key == ord(' '):
            rows = self.rows()
            if rows:
                name = rows[min(self.index, len(rows) - 1)]
                selection = self.selected[self.section]
                if name in selection:
                    selection.remove(name)
                else:
                    if self.section == 4:
                        self.selected[2], self.selected[3], self.selected[4] = [], [], [name]
                    else:
                        selection.append(name)
                        if self.section in (2, 3):
                            self.selected[4] = []
        elif key in (10, 13):
            try:
                self.prepare()
                self.error = ''
            except ValueError as error:
                self.error = str(error)
        return True

    def lines(self):
        if self.mode == 'confirm':
            return ['CONFIRM EXECUTION - y submits; n/Esc returns'] + self.preview
        if self.mode == 'running':
            return ['EXECUTING - x stops observation, not remote jobs'] + self.preview
        if self.mode == 'result':
            if self.result_lines is not None:
                return self.result_lines
            lines = ['OPERATION RESULT - Esc returns to monitor']
            if not self.result:
                return lines
            lines.append('%s | %s' % (self.result['operation_id'], self.result['status']))
            for target in self.result['targets']:
                lines.append('%s | %s | %sms' % (target['target'], target['status'], target['duration_ms']))
                steps = [(scenario['name'], step) for scenario in target.get('scenarios', [])
                         for step in scenario['steps']] if 'scenarios' in target else [('', target)]
                for scenario, step in steps:
                    lines.append('  %s%s | %s | exit=%s | job=%s' %
                        (scenario + '/' if scenario else '', step.get('step', self.result.get('endpoint', '')),
                         step['status'], step['return_code'], step['job_id']))
                    if step.get('error'):
                        lines.append('    error: ' + step['error'])
                    if step['output_truncated']:
                        lines.append('    OUTPUT TRUNCATED')
                    for field in ('stdout', 'stderr'):
                        lines.append('    ' + field + ':')
                        lines.extend('      ' + line for line in ''.join(step[field]).splitlines())
            self.result_lines = lines
            return lines
        rows = self.rows()
        self.index = min(self.index, max(0, len(rows) - 1))
        return ['PREPARE - Tab section; Space toggle; Enter preview; Esc back',
                ' | '.join('[' + name + ']' if i == self.section else name for i, name in enumerate(SECTIONS)),
                'Selected targets/groups: %s/%s; scenarios/groups: %s/%s; endpoints: %s' %
                tuple(len(selected) for selected in self.selected),
                'Search (/): ' + self.search, 'Inputs (i, JSON): ' + self.input] + [
                ('> ' if i == self.index else '  ') + ('[x] ' if name in self.selected[self.section] else '[ ] ') + name
                for i, name in enumerate(rows)]

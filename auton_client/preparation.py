# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Terminal preparation and explicit confirmation; execution lives in services."""
import curses
import json

from auton_client.operations import OperationService
from auton_client.export import export_result
from auton_client.reconcile import ReconciliationService, load_report
from auton_client.scenarios import ScenarioService, select_scenarios
from auton_client.connections import select_connections, target_origins

SECTIONS = ('Targets', 'Target groups', 'Scenarios', 'Scenario groups', 'Endpoints')
MAX_INPUT = 4096
PAYLOAD_FIELDS = frozenset(('args', 'env'))


from auton_client.preparation_model import PreparationModel


class PreparationView(PreparationModel):




    def handle(self, key):
        if self.editing == 'parameter_input':
            if key == 27:
                self.editing = None
            elif key in (10, 13):
                field = self.parameter_fields[self.parameter_index]
                value = self.parameter_input
                if field.get('required', True) and not value:
                    self.error = 'This parameter is required.'
                    return True
                if value and field.get('choices') and value not in field['choices']:
                    self.error = 'Choose one of the published values.'
                    return True
                self.parameter_values.append(value)
                self.parameter_index += 1
                self.parameter_input, self.error = '', ''
                if self.parameter_index == len(self.parameter_fields):
                    while (self.parameter_values and not self.parameter_values[-1]
                           and not self.parameter_fields[len(self.parameter_values) - 1].get('required', True)):
                        self.parameter_values.pop()
                    self.input = json.dumps({'args': self.parameter_values, 'env': {}})
                    self.editing = None
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.parameter_input = self.parameter_input[:-1]
            elif 32 <= key <= 126 and len(self.parameter_input) < MAX_INPUT:
                self.parameter_input += chr(key)
            return True
        if self.editing is not None:
            if key in (10, 13, 27):
                importing = self.editing == 'import_path' and key != 27
                exporting = self.editing == 'export_path' and key != 27
                self.editing = None
                if importing:
                    try:
                        targets = select_connections(self.selected[0], self.selected[1], self.connections, self.groups)
                        settings = {key: self.settings[key] for key in ('auth', 'http_timeout', 'timeout', 'transport')
                                    if key in self.settings}
                        self.service = ReconciliationService(load_report(self.import_path), targets, **settings)
                        self.session.start(self.service)
                        self.result = self.result_lines = None
                        self.mode, self.error, self.scroll = 'running', '', 0
                    except (OSError, ValueError) as error:
                        self.error = 'Cannot reconcile report: ' + str(error)
                if exporting:
                    try:
                        export_result(self.result, self.export_path)
                        self.error = 'Export saved: ' + self.export_path
                    except (OSError, ValueError):
                        self.error = 'Export failed: choose a new writable path; existing files are never overwritten.'
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
            if key in (curses.KEY_DOWN, ord('j'), curses.KEY_NPAGE):
                self.scroll += 10 if key == curses.KEY_NPAGE else 1
            elif key in (curses.KEY_UP, ord('k'), curses.KEY_PPAGE):
                self.scroll = max(0, self.scroll - (10 if key == curses.KEY_PPAGE else 1))
            elif key == ord('x'):
                self.session.stop()
                self.error = 'Stopping observation; waiting for in-flight requests. Remote jobs may continue.'
            return True
        if self.mode == 'confirm':
            if key == ord('y'):
                self.session.start(self.service)
                self.result = self.result_lines = None
                self.mode, self.error = 'running', ''
                self.scroll = 0
            elif key == ord('n'):
                self.mode = 'select'
                self.service = None
            elif key in (curses.KEY_DOWN, ord('j')):
                self.scroll += 1
            elif key in (curses.KEY_UP, ord('k')):
                self.scroll = max(0, self.scroll - 1)
            return True
        if self.mode == 'result':
            if key == ord('w') and self.result is not None:
                self.editing = 'export_path'
                self.export_path = ''
                self.error = ''
            elif key in (curses.KEY_DOWN, ord('j'), curses.KEY_NPAGE):
                self.scroll += 10 if key == curses.KEY_NPAGE else 1
            elif key in (curses.KEY_UP, ord('k'), curses.KEY_PPAGE):
                self.scroll = max(0, self.scroll - (10 if key == curses.KEY_PPAGE else 1))
            return True
        if key == ord('p'):
            schema = self.parameter_catalogue.get(self.selected[4][0]) if len(self.selected[4]) == 1 else None
            if not schema or not schema.get('args'):
                self.error = 'Select one endpoint with a consistent published parameter schema.'
            else:
                self.parameter_fields = schema['args']
                self.parameter_values, self.parameter_index = [], 0
                self.parameter_input, self.error = '', ''
                self.editing = 'parameter_input'
        elif key == ord('r'):
            self.editing = 'import_path'
            self.import_path = ''
            self.error = ''
        elif key == 9:
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
        if self.editing == 'parameter_input':
            field = self.parameter_fields[self.parameter_index]
            return ['GUIDED ARGUMENTS - Enter next; Esc cancels without changing inputs',
                    'This form replaces args and clears env; server validation remains authoritative.',
                    '%s/%s: %s (%s, %s)' % (self.parameter_index + 1, len(self.parameter_fields),
                        field['name'], field.get('type', 'string'), 'required' if field.get('required', True) else 'optional'),
                    field.get('description', ''), 'Choices: ' + ', '.join(field.get('choices', [])),
                    'Value: ' + self.parameter_input]
        if self.editing == 'import_path':
            return ['RECONCILE REPORT - Enter reads; Esc cancels',
                    'Select trusted targets first. No submission or scenario continuation.',
                    'Report path: ' + self.import_path]
        if self.mode == 'confirm':
            return ['CONFIRM EXECUTION - y submits; n/Esc returns'] + self.preview
        if self.mode == 'running':
            label = 'READING SAVED JOBS' if isinstance(self.service, ReconciliationService) else 'EXECUTING'
            lines = [label + ' - x stops observation, not remote jobs',
                     'Operation: ' + str(self.progress['operation_id']),
                     'Observed states; pending means not yet submitted.']
            for row in self.progress['rows']:
                label = (row['scenario'] + '/' + row['step']) if row['scenario'] else row['endpoint']
                lines.append('%s | %s | %s | %sms' %
                             (row['target'], label, row['status'], row['duration_ms']))
                lines.append('  %s | job=%s' % (row['uri'], row['job_id'] or '-'))
            return lines
        if self.mode == 'result':
            if self.editing == 'export_path':
                return ['EXPORT JSON - Enter writes; Esc cancels',
                        'Contains job outputs; keep this file private.',
                        'New file path: ' + self.export_path]
            if self.result_lines is not None:
                return self.result_lines
            lines = ['OPERATION RESULT - w exports JSON; Esc returns to monitor']
            if not self.result:
                return lines
            if self.result.get('reconciled'):
                lines.append('READ-ONLY RECONCILIATION - durations are from the saved observation')
            lines.append('%s | %s' % (self.result['operation_id'], self.result['status']))
            for target in self.result['targets']:
                lines.append('%s | %s | %sms' % (target['target'], target['status'], target.get('duration_ms', '?')))
                lines.append('  Origin: ' + str(target.get('uri', '-')))
                steps = [(scenario['name'], step) for scenario in target.get('scenarios', [])
                         for step in scenario['steps']] if 'scenarios' in target else [('', target)]
                for scenario, step in steps:
                    lines.append('  %s%s | %s | exit=%s | job=%s' %
                        (scenario + '/' if scenario else '', step.get('step', self.result.get('endpoint', '')),
                         step['status'], step['return_code'], step['job_id']))
                    if step.get('error'):
                        lines.append('    error: ' + step['error'])
                    for attempt in step.get('attempts', []):
                        lines.append('    %s: %s%s' % (attempt['uri'], attempt['status'],
                            ' - ' + attempt['reason'] if attempt['reason'] else ''))
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
                'Search (/): ' + self.search, 'Inputs (i, JSON): ' + self.input, 'p guided arguments | r reconcile a saved report'] + [
                ('> ' if i == self.index else '  ') + ('[x] ' if name in self.selected[self.section] else '[ ] ') + name
                for i, name in enumerate(rows)]

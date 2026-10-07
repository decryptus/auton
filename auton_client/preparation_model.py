# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Interface-neutral operation preparation; execution lives in services."""
import json

from auton_client.operations import OperationService
from auton_client.export import export_result
from auton_client.reconcile import ReconciliationService, load_report
from auton_client.scenarios import ScenarioService, select_scenarios
from auton_client.connections import select_connections, target_origins

SECTIONS = ('Targets', 'Target groups', 'Scenarios', 'Scenario groups', 'Endpoints')
MAX_INPUT = 4096
PAYLOAD_FIELDS = frozenset(('args', 'env'))


class PreparationModel:
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
        self.progress = {'revision': 0, 'rows': [], 'operation_id': None}
        self.export_path = ''
        self.import_path = ''
        self.parameter_catalogue = {}
        self.parameter_fields = []
        self.parameter_values = []
        self.parameter_index = 0
        self.parameter_input = ''


    def update(self, snapshots):
        self.endpoints = sorted({item['name'] for data in snapshots.values()
                                 for item in data.get('endpoints', [])})
        catalogues = {}
        for data in snapshots.values():
            for item in data.get('endpoints', []):
                catalogues.setdefault(item['name'], set()).add(json.dumps(item.get('parameters'), sort_keys=True))
        self.parameter_catalogue = {name: json.loads(next(iter(values)))
                                    for name, values in catalogues.items() if len(values) == 1}
        self.poll_result()

    def poll_result(self):
        if self.mode == 'running':
            self.progress = self.session.progress.snapshot()
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
        preview = ['Targets:']
        for name, value in targets.items():
            origins = target_origins(value)
            preview.append('  %s = %s' % (name, origins[0]))
            if len(origins) > 1:
                preview.append('    Ordered failover (one destination, no broadcast):')
                preview.extend('      %s' % origin for origin in origins[1:])
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



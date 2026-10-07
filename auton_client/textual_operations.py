"""Textual operation adapter over the shared preparation and execution services."""
import json

from dwho.tui.textual import Confirmation, InputForm, StatusLine, plain_text
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, SelectionList, Static

from auton_client.connections import select_connections
from auton_client.export import export_result
from auton_client.reconcile import ReconciliationService, load_report

SECTIONS = ('Targets', 'Target groups', 'Scenarios', 'Scenario groups', 'Endpoints')


class OperationsScreen(Screen):
    CSS = '''
    OperationsScreen { background: #0a1220; }
    #op-title { height: 3; padding: 1 2; background: #101f34; color: #83cfff; text-style: bold; }
    #op-selection { height: 13; margin-top: 1; }
    .op-section Static { color: #b7dcff; text-style: bold; }
    .op-section SelectionList { background: #102137; border: round #305778; }
    .op-section SelectionList:focus { border: round #83cfff; }
    #op-actions { padding: 0 2; }
    #op-actions Button { background: #173e62; color: #b7e2ff; border: none; height: 3; }
    #op-actions Button:focus { background: #245d86; color: #ffffff; }
    #op-actions Button:disabled { background: #102137; color: #66819e; }
    #op-preview { background: #245d86; }

    .op-section { width: 1fr; padding: 0 1; }
    .op-section SelectionList { height: 10; }
    #op-actions { height: auto; layout: grid; grid-size: 4; grid-gutter: 1; }
    #op-actions Button { width: 100%; }
    #op-result { height: auto; padding: 1 2; }
    #op-status { height: auto; padding: 1 2; }
    #op-body { height: 1fr; }
    '''
    BINDINGS = [('escape', 'back', 'Back'), ('q', 'back', 'Back')]

    def __init__(self, model, demo=False):
        super().__init__()
        self.model, self.demo = model, demo
        self.last_display = None

    def compose(self):
        yield Static('AUTON · OPERATIONS' + (' · SYNTHETIC DEMO' if self.demo else '') + '\nSelect targets and either scenarios or one endpoint', id='op-title')
        yield StatusLine('info', 'Nothing is submitted until you confirm the preview.', id='op-status')
        with VerticalScroll(id='op-body'):
            with Horizontal(id='op-selection'):
                catalogues = (self.model.connections, self.model.groups, self.model.scenarios,
                              self.model.scenario_groups, self.model.endpoints)
                for index, (title, catalogue) in enumerate(zip(SECTIONS, catalogues)):
                    with VerticalScroll(classes='op-section'):
                        yield Static(title)
                        yield SelectionList(*[(plain_text(name), name, name in self.model.selected[index])
                                              for name in catalogue], id='op-select-%s' % index)
            with Horizontal(id='op-actions'):
                for key, label in (('preview', 'Preview execution'), ('inputs', 'JSON inputs'),
                                   ('parameters', 'Guided arguments'), ('reconcile', 'Read saved jobs'),
                                   ('export', 'Export result'), ('stop', 'Stop observation'),
                                   ('new', 'New preparation'),
                                   ('back', 'Back to monitor')):
                    yield Button(label, id='op-' + key)
            yield Static('', markup=False, id='op-result')
        yield Footer()

    def on_mount(self):
        self.set_interval(0.1, self.render_state)
        self.render_state()

    def collect_selection(self):
        self.model.selected = [list(self.query_one('#op-select-%s' % index, SelectionList).selected)
                               for index in range(len(SECTIONS))]

    def render_state(self):
        if not self.is_mounted:
            return
        model = self.model
        model.poll_result() if model.mode == 'running' else None
        busy = model.mode in ('running', 'confirm') or model.session.running
        self.query_one('#op-selection').display = model.mode in ('select', 'confirm')
        for widget in self.query(SelectionList):
            widget.disabled = busy
        for key in ('preview', 'inputs', 'parameters', 'reconcile'):
            self.query_one('#op-' + key, Button).disabled = busy or model.mode == 'result'
        self.query_one('#op-new', Button).disabled = busy
        self.query_one('#op-export', Button).disabled = model.result is None or busy
        self.query_one('#op-stop', Button).disabled = model.mode != 'running'
        if model.error:
            state, notice = 'warning', model.error
        elif model.mode == 'running':
            state, notice = 'running', 'Observing execution. Acceptance is not completion.'
        elif model.mode == 'result' and model.result:
            status = model.result['status']
            state = 'success' if status == 'completed' else 'warning' if status == 'incomplete' else 'failure'
            notice = 'Operation result: ' + status
        else:
            state, notice = 'info', 'Preview first. Server authentication and endpoint ACLs remain authoritative.'
        self.query_one('#op-status', StatusLine).set_status(state, notice)
        body = (result_text(model.result) if model.result is not None
                else progress_text(model.progress) if model.mode == 'running'
                else 'Inputs: ' + model.input)
        if body != self.last_display:
            self.query_one('#op-result', Static).update(plain_text(body, multiline=True))
            self.last_display = body

    def on_button_pressed(self, event):
        event.stop()
        action = event.button.id
        try:
            if action == 'op-back':
                self.action_back()
            elif action == 'op-new':
                self.model.mode, self.model.error = 'select', ''
                self.model.result = self.model.result_lines = None
            elif action == 'op-stop':
                self.model.session.stop()
                self.model.error = 'Stopping observation; remote jobs may continue. Do not replay uncertain jobs.'
            elif action == 'op-inputs':
                self.app.push_screen(InputForm('Operation inputs', [('payload', 'JSON args / env', self.model.input, False)]), self.inputs_received)
            elif action == 'op-preview':
                self.preview()
            elif action == 'op-parameters':
                self.parameters()
            elif action in ('op-export', 'op-reconcile'):
                self.collect_selection()
                exporting = action == 'op-export'
                self.app.push_screen(InputForm('Export result' if exporting else 'Read saved jobs',
                    [('path', 'New private file path' if exporting else 'Report path', '', False)],
                    hint='Existing files are never overwritten.' if exporting else
                    'Select trusted targets first. Reads existing jobs; never submits or continues a scenario.'),
                    self.export_received if exporting else self.reconcile_received)
        except (ValueError, OSError) as error:
            self.model.error = str(error)
        self.render_state()

    def inputs_received(self, values):
        if values is not None:
            self.model.input = values['payload']
            self.model.error = ''

    def preview(self):
        if self.model.mode == 'confirm' or self.model.session.running:
            return
        self.collect_selection()
        if (self.model.selected[2] or self.model.selected[3]) and self.model.selected[4]:
            raise ValueError('Select scenarios OR one endpoint, not both.')
        self.model.error = ''
        self.model.prepare()
        self.app.push_screen(Confirmation('Confirm execution', '\n'.join(self.model.preview),
                                         confirm_label='Submit operation'), self.confirmed)

    def confirmed(self, accepted):
        if self.model.mode != 'confirm':
            return
        service, self.model.service = self.model.service, None
        self.model.mode = 'select'
        if accepted:
            try:
                self.model.session.start(service)
                self.model.result = self.model.result_lines = None
                self.model.error = ''
                self.model.mode = 'running'
            except Exception:
                self.model.error = 'Unable to start observation; inspect daemon jobs before any retry.'
                self.model.mode = 'result'

    def parameters(self):
        self.collect_selection()
        schema = self.model.parameter_catalogue.get(self.model.selected[4][0]) if len(self.model.selected[4]) == 1 else None
        if not schema or not schema.get('args'):
            raise ValueError('Select one endpoint with a consistent published parameter schema.')
        fields = schema['args']
        def received(values):
            if values is None:
                return
            args = [values[str(index)] for index in range(len(fields))]
            for field, value in zip(fields, args):
                if (field.get('required', True) and not value) or (value and field.get('choices') and value not in field['choices']):
                    self.model.error = 'Invalid argument: ' + field['name']
                    return
            while args and not args[-1] and not fields[len(args)-1].get('required', True):
                args.pop()
            self.model.input = json.dumps({'args': args, 'env': {}})
            self.model.error = ''
        self.app.push_screen(InputForm('Guided arguments',
            [(str(index), '%s (%s) %s' % (field['name'], 'required' if field.get('required', True) else 'optional',
                                        ', '.join(field.get('choices', []))), '', False)
             for index, field in enumerate(fields)], hint='Replaces args and clears env. Server validation remains authoritative.'), received)

    def export_received(self, values):
        if values is None:
            return
        try:
            export_result(self.model.result, values['path'])
            self.model.error = ''
            self.notify('Result exported to a new private file.')
        except (OSError, ValueError):
            self.model.error = 'Export failed. Choose a new writable path; existing files are never overwritten.'

    def reconcile_received(self, values):
        if values is None:
            return
        try:
            targets = select_connections(self.model.selected[0], self.model.selected[1], self.model.connections, self.model.groups)
            settings = {key: self.model.settings[key] for key in ('auth', 'http_timeout', 'timeout', 'transport') if key in self.model.settings}
            service = ReconciliationService(load_report(values['path']), targets, **settings)
            self.model.session.start(service)
            self.model.result = self.model.result_lines = None
            self.model.mode, self.model.error = 'running', ''
        except (OSError, ValueError):
            self.model.error = 'Cannot read report. Check its format and the selected trusted targets.'

    def action_back(self):
        if self.model.session.running:
            self.model.error = 'Execution active. Stop observation first; this does not cancel remote jobs.'
        else:
            self.app.pop_screen()


def progress_text(progress):
    lines = ['Operation: ' + str(progress['operation_id']), 'Observed states; pending means not yet submitted.', '']
    for row in progress['rows']:
        lines.append('%s · %s%s · %s · %sms' % (row['target'], row['scenario'] + '/' if row['scenario'] else '',
                     row['step'] or row['endpoint'], row['status'].upper(), row['duration_ms']))
    return '\n'.join(lines)


def result_text(result):
    lines = ['OPERATION RESULT · ' + result['status'].upper(), 'Operation: ' + result['operation_id'], '']
    for target in result['targets']:
        lines.append('%s · %s · %sms' % (target['target'], target['status'].upper(), target.get('duration_ms', '?')))
    lines.extend(['', 'JOB DETAILS'])
    for target in result['targets']:
        steps = [(scenario['name'], step) for scenario in target.get('scenarios', [])
                 for step in scenario['steps']] if 'scenarios' in target else [('', target)]
        for scenario, step in steps:
            lines.append('%s / %s%s · %s · exit=%s' % (target['target'], scenario + '/' if scenario else '',
                         step.get('step', result.get('endpoint', '')), step['status'].upper(), step['return_code']))
            lines.append('  job: %s · %s' % (step['job_id'], target.get('uri', '?')))
            if step.get('error'):
                lines.append('  ' + step['error'])
            for attempt in step.get('attempts', []):
                lines.append('  Attempt %s · %s · %s' % (attempt['uri'], attempt['status'], attempt.get('reason') or ''))
            if step.get('output_truncated'):
                lines.append('  OUTPUT TRUNCATED')
            for field in ('stdout', 'stderr'):
                content = ''.join(step.get(field, []))
                if content:
                    lines.append('  ' + field + ':')
                    lines.extend('    ' + line for line in content.splitlines())
    return '\n'.join(lines)

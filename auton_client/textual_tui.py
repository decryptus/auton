"""Textual adapter over observation and explicit operation services."""
import hashlib
import json
import math
import time

from dwho.tui.textual import DashboardApp, DetailPanel, TableRow
from textual.widgets import DataTable

from auton_client.monitor import FleetMonitor
from auton_client.tui_model import daemon_specs, status_label
from auton_client.visibility import DaemonClient, JOB_STATES

DEFAULT_REFRESH = 2.0
POLL_SECONDS = 0.1
NAVIGATION = (('jobs', 'Jobs'), ('endpoints', 'Endpoints'), ('output', 'Output'), ('daemons', 'Daemons'))
COLUMNS = ('RESOURCE', 'DAEMON', 'STATE / TYPE', 'EXIT / COUNT')


def identity(parts):
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()


class OperatorApp(DashboardApp):
    BINDINGS = DashboardApp.BINDINGS + [
        ('e', 'prepare', 'Operations'), ('r', 'refresh', 'Refresh'), ('p', 'pause', 'Pause'),
        ('s', 'state', 'State'), ('c', 'clear_filters', 'Clear filters'),
        ('v', 'stream', 'Stdout/stderr'), ('left_square_bracket', 'previous_daemon', 'Previous daemon'),
        ('right_square_bracket', 'next_daemon', 'Next daemon'), ('a', 'all_daemons', 'All daemons')]

    def __init__(self, monitor, refresh=DEFAULT_REFRESH, clock=time.monotonic, demo=False, preparation=None):
        super().__init__(product='Auton', heading='Operations overview', columns=COLUMNS,
                         navigation=NAVIGATION, mode='interactive' if preparation is not None else 'read_only',
                         subtitle='SYNTHETIC DEMO' if demo else 'DAEMON OBSERVATION')
        self.monitor, self.refresh_seconds, self.clock, self.demo = monitor, refresh, clock, demo
        self.preparation = preparation
        self.names = list(monitor.clients)
        self.daemon = self.names[0]
        self.view, self.state, self.endpoint, self.output_job = 'jobs', None, None, None
        self.stderr, self.paused = False, False
        self.data, self.items = {}, {}
        self.next_refresh = 0
        self._output_display = None

    def on_mount(self):
        self.set_interval(POLL_SECONDS, self.tick)
        self.tick()

    def requested_job(self):
        if self.view != 'output' or self.output_job is None:
            return None
        return self.output_job if self.daemon is None else self.output_job[1:]

    def tick(self):
        if not self.is_running:
            return
        if len(self.screen_stack) > 1:
            return
        result = self.monitor.poll()
        if result is not None:
            daemon, job, data = result
            if daemon == self.daemon and job == self.requested_job():
                self.data = data
                self.render_snapshot()
                self.next_refresh = self.clock() + self.refresh_seconds
            else:
                self.next_refresh = 0
        if (not self.paused or self.next_refresh == 0) and self.clock() >= self.next_refresh:
            if self.monitor.refresh(self.daemon, self.requested_job()):
                self.next_refresh = float('inf')
        self.update_notice()

    def update_notice(self):
        errors = self.data.get('errors', {})
        if errors:
            state, text = 'warning', 'Partial/unavailable: ' + '; '.join('%s: %s' % pair for pair in errors.items())
        elif self.data.get('partial'):
            state, text = 'running', 'Waiting for remaining daemons; coverage is incomplete'
        elif not self.data:
            state, text = 'running', 'Reading daemon observations...'
        else:
            state, text = 'info', 'Observations loaded'
        text += ' | %s | state=%s' % (self.daemon or 'ALL DAEMONS', self.state or 'all')
        if self.paused:
            text += ' | PAUSED — displayed observations are not refreshed'
        if self.demo:
            text = 'DEMO — synthetic data | ' + text
        self.set_notice(state, text)

    def selected_item(self):
        table = self.query_one(DataTable)
        if table.cursor_row < len(self._visible_keys):
            return self.items.get(self._visible_keys[table.cursor_row])
        return None

    def render_snapshot(self):
        rows, self.items = [], {}
        if self.view == 'daemons':
            maintenance = self.data.get('health', {}).get('maintenance', {})
            source = self.data.get('daemons', [dict(name=self.daemon, state=(
                'error' if self.data.get('errors') else 'maintenance' if maintenance.get('enabled')
                else 'ok' if self.data else 'pending'), jobs=len(self.data.get('jobs', [])),
                error=maintenance.get('reason', ''))])
        elif self.view == 'endpoints':
            source = self.data.get('endpoints', [])
        elif self.view == 'output':
            source = []
            detail = self.data.get('detail')
            if detail:
                source = [detail]
        else:
            source = [j for j in self.data.get('jobs', [])
                      if (self.state is None or j['status'] == self.state)
                      and (self.endpoint is None or (j.get('daemon', self.daemon), j['endpoint']) == self.endpoint)]
        for item in source:
            daemon = item.get('daemon', self.daemon)
            if self.view == 'daemons':
                key = identity(('daemon', item['name']))
                cells = (item['name'], item['name'], item['state'], item.get('jobs', '?'))
                title = item['name']
            elif self.view == 'endpoints':
                key = identity(('endpoint', daemon, item['name']))
                cells = (item['name'], daemon, 'ENDPOINT', '')
                title = item['name']
            else:
                key = identity(('job', daemon, item['endpoint'], item['uid']))
                cells = (item['uid'], daemon, status_label(item),
                         '-' if item.get('return_code') is None else item['return_code'])
                title = item['uid']
            self.items[key] = item
            rows.append(TableRow(key, cells, title, json.dumps(item, indent=2, ensure_ascii=False)))
        self.display_rows(rows)
        if self.view == 'output':
            self.show_output()
        self.set_activity('%s entries | e prepares an operation with explicit confirmation.' % len(rows) if self.preparation is not None else 'READ ONLY | %s entries' % len(rows))

    def _show_row(self, key):
        if self.view == 'output':
            self.show_output()
        else:
            super()._show_row(key)

    def show_output(self):
        detail = self.data.get('detail')
        title = 'stderr / diagnostics' if self.stderr else 'stdout'
        if not detail:
            text = self.data.get('errors', {}).get('detail', 'Select a job and press Enter to read its output.')
        else:
            title = '%s | %s' % (detail['uid'], title)
            text = ''.join(detail.get('errors' if self.stderr else 'stream', [])) or '(empty)'
        display = (title, text)
        if display != self._output_display:
            panel = self.query_one(DetailPanel)
            offset = panel.scroll_offset
            same_job = self._output_display is not None and self._output_display[0] == title
            panel.show_details(title, text)
            if same_job:
                panel.scroll_to(x=offset.x, y=offset.y, animate=False)
            self._output_display = display

    def change_view(self, view):
        if view == 'output' and self.view == 'jobs':
            item = self.selected_item()
            self.output_job = ((item.get('daemon', self.daemon), item['endpoint'], item['uid']) if item else None)
        elif view != 'output':
            self.output_job = None
        self.view = view
        self._output_display = None
        self.select_navigation(view)
        self.action_clear_search()
        # A previous job's output is never shown while a new read is pending.
        self.data.pop('detail', None)
        self.render_snapshot()
        self.next_refresh = 0

    def on_dashboard_app_navigation_requested(self, message):
        self.change_view(message.key)

    def on_data_table_row_selected(self, event):
        item = self.selected_item()
        if item is None:
            return
        if self.view == 'jobs':
            self.change_view('output')
        elif self.view == 'endpoints':
            self.endpoint = (item.get('daemon', self.daemon), item['name'])
            self.change_view('jobs')
        elif self.view == 'daemons':
            self.switch_daemon(item['name'])

    def switch_daemon(self, name):
        self.daemon, self.data, self.endpoint, self.output_job = name, {}, None, None
        self.change_view('jobs')

    def action_next_daemon(self):
        self._cycle_daemon(1)

    def action_previous_daemon(self):
        self._cycle_daemon(-1)

    def _cycle_daemon(self, step):
        names = self.names + ([None] if len(self.names) > 1 else [])
        self.switch_daemon(names[(names.index(self.daemon) + step) % len(names)])

    def action_all_daemons(self):
        self.switch_daemon(None if len(self.names) > 1 and self.daemon is not None else self.names[0])

    def action_refresh(self):
        self.next_refresh = 0

    def action_pause(self):
        self.paused = not self.paused
        if not self.paused:
            self.next_refresh = 0
        self.update_notice()

    def action_state(self):
        states = (None,) + JOB_STATES
        self.state = states[(states.index(self.state) + 1) % len(states)]
        self.render_snapshot()

    def action_clear_filters(self):
        self.state, self.endpoint = None, None
        self.action_clear_search()
        self.render_snapshot()

    def check_action(self, action, parameters):
        if len(self.screen_stack) > 1:
            return False
        return True

    def action_prepare(self):
        if self.preparation is None:
            self.notify('Execution is not configured in this observation session.')
            return
        from auton_client.textual_operations import OperationsScreen
        snapshots = getattr(self.monitor, 'snapshots', {}) or {self.daemon: self.data}
        self.preparation.update(snapshots)
        self.push_screen(OperationsScreen(self.preparation, demo=self.demo))

    def action_quit(self):
        if self.preparation is not None and self.preparation.session.running:
            self.notify('Stop observation first. Remote jobs are not cancelled.', severity='warning')
            return
        self.exit()

    def action_stream(self):
        self.stderr = not self.stderr
        if self.view == 'output':
            self.show_output()


def run(specs, uris, auth=None, http_timeout=30, refresh=DEFAULT_REFRESH, configured=None, selected=None,
        groups=None, scenarios=None, scenario_groups=None, transport=None):
    from dwho.cli import require_terminal
    from auton_client.connections import target_origins, monitoring_origins
    from auton_client.credentials import bind_credentials
    require_terminal('TUI requires an interactive terminal')
    if not math.isfinite(refresh) or refresh < 0.2:
        raise ValueError('refresh must be at least 0.2 seconds')
    connections = daemon_specs(specs, uris, configured) if selected is None else selected
    if not connections:
        raise ValueError('selection contains no targets')
    auth = bind_credentials(auth, [uri for target in connections.values() for uri in target_origins(target)])
    clients = {name: DaemonClient(uri, auth, http_timeout, transport=transport)
               for name, uri in monitoring_origins(connections).items()}
    from auton_client.preparation_model import PreparationModel
    from auton_client.session import ExecutionSession
    monitor = FleetMonitor(clients)
    session = ExecutionSession()
    preparation = PreparationModel(connections, groups or {}, scenarios or {}, scenario_groups or {},
                                   session, {'auth': auth, 'http_timeout': http_timeout, 'transport': transport})
    try:
        OperatorApp(monitor, refresh, preparation=preparation).run()
    finally:
        monitor.close()
        session.close()
    return 0

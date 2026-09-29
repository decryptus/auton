# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Curses presentation only; all daemon reads go through the monitor service."""

import curses
import math
import sys
import textwrap
import time

from auton_client import __version__
from auton_client.monitor import Monitor
from auton_client.visibility import DaemonClient, JOB_STATES

VIEWS = ('Jobs', 'Endpoints', 'Output')
MIN_ROWS = 12
MIN_COLS = 64
MAX_SEARCH = 128
DEFAULT_REFRESH = 2.0
STATUS_LABELS = {'new': 'queued', 'processing': 'running', 'complete': 'completed'}


def safe_text(value):
    """Do not interpret terminal controls supplied by commands or remote metadata."""
    return ''.join(char if char.isprintable() else ' ' for char in str(value))


def daemon_specs(specs, uris):
    if specs and uris:
        raise ValueError('use --daemon or a single --uri for TUI, not both')
    if not specs:
        if len(uris) != 1:
            raise ValueError('TUI needs --daemon NAME=URI or one --uri; failover URIs are not targets')
        return {'default': uris[0]}
    result = {}
    for spec in specs:
        name, separator, uri = spec.partition('=')
        if (not separator or not name or len(name) > 32 or not uri
                or not all(char.isalnum() or char in '._-' for char in name)):
            raise ValueError('daemon must use NAME=URI (name: letters, digits, dot, underscore, hyphen)')
        if name in result:
            raise ValueError('duplicate daemon name: ' + name)
        result[name] = uri
    return result


class OperatorView:
    def __init__(self, monitor, refresh=DEFAULT_REFRESH, clock=time.monotonic):
        self.monitor = monitor
        self.names = list(monitor.clients)
        self.daemon_index = 0
        self.view = 0
        self.index = 0
        self.output_line = 0
        self.stderr = False
        self.search = ''
        self.editing = False
        self.state = None
        self.endpoint = None
        self.paused = False
        self.refresh = refresh
        self.clock = clock
        self.next_refresh = 0
        self.cache = {}

    @property
    def daemon(self):
        return self.names[self.daemon_index]

    @property
    def data(self):
        return self.cache.get(self.daemon, {})

    def rows(self):
        if self.view == 1:
            return [item for item in self.data.get('endpoints', [])
                    if self.search.casefold() in item['name'].casefold()]
        return [job for job in self.data.get('jobs', [])
                if (self.state is None or job['status'] == self.state)
                and (self.endpoint is None or job['endpoint'] == self.endpoint)
                and self.search.casefold() in job['uid'].casefold()]

    def selected_job(self):
        if self.view != 2:
            return None
        rows = self.rows()
        if not rows:
            return None
        self.index = min(self.index, len(rows) - 1)
        job = rows[self.index]
        return job['endpoint'], job['uid']

    def tick(self):
        result = self.monitor.poll()
        if result is not None:
            daemon, job, data = result
            if daemon == self.daemon and job == self.selected_job():
                old_rows = self.rows()
                selected = old_rows[min(self.index, len(old_rows) - 1)] if old_rows else None
                self.cache[daemon] = data
                rows = self.rows()
                key = 'name' if self.view == 1 else 'uid'
                if selected is not None:
                    self.index = next((i for i, row in enumerate(rows)
                                       if row[key] == selected[key]), 0)
                self.index = min(self.index, max(0, len(rows) - 1))
                self.next_refresh = self.clock() + self.refresh
            else:
                # A response to an old selection must never replace the current output.
                self.next_refresh = 0
        if self.clock() >= self.next_refresh and (not self.paused or self.next_refresh == 0):
            if self.monitor.refresh(self.daemon, self.selected_job()):
                self.next_refresh = float('inf')

    def handle(self, key):
        if self.editing:
            if key in (10, 13, 27):
                self.editing = False
            elif key in (curses.KEY_BACKSPACE, 127, 8):
                self.search = self.search[:-1]
            elif 32 <= key <= 126 and len(self.search) < MAX_SEARCH:
                self.search += chr(key)
            self.index = self.output_line = 0
            return True
        if key == ord('q'):
            return False
        if key in (ord(']'), ord('[')):
            self.daemon_index = (self.daemon_index + (1 if key == ord(']') else -1)) % len(self.names)
            self.index = self.output_line = 0
            self.endpoint = None
            self.next_refresh = 0
        elif key == 9:
            self.view = (self.view + 1) % len(VIEWS)
            self.index = self.output_line = 0
            self.next_refresh = 0
        elif key in (10, 13):
            rows = self.rows()
            if rows and self.view == 1:
                self.endpoint = rows[self.index]['name']
                self.search = ''
                self.index = 0
                self.view = 0
            elif rows and self.view == 0:
                self.view = 2
            self.output_line = 0
            self.next_refresh = 0
        elif key in (curses.KEY_DOWN, ord('j'), curses.KEY_UP, ord('k'),
                     curses.KEY_NPAGE, curses.KEY_PPAGE):
            step = -1 if key in (curses.KEY_UP, ord('k'), curses.KEY_PPAGE) else 1
            if key in (curses.KEY_NPAGE, curses.KEY_PPAGE):
                step *= 10
            if self.view == 2:
                self.output_line = max(0, self.output_line + step)
            else:
                self.index = min(max(0, len(self.rows()) - 1), max(0, self.index + step))
        elif key == ord('v'):
            self.stderr = not self.stderr
            self.output_line = 0
        elif key == ord('/'):
            self.editing = True
        elif key == ord('s'):
            states = (None,) + JOB_STATES
            self.state = states[(states.index(self.state) + 1) % len(states)]
            self.index = self.output_line = 0
            self.next_refresh = 0
        elif key == ord('c'):
            self.search, self.state, self.endpoint = '', None, None
            self.index = self.output_line = 0
            self.next_refresh = 0
        elif key == ord('r'):
            self.next_refresh = 0
        elif key == ord('p'):
            self.paused = not self.paused
            if not self.paused:
                self.next_refresh = 0
        elif key == 27:
            self.view = 0
            self.next_refresh = 0
        return True

    def draw(self, screen):
        screen.erase()
        height, width = screen.getmaxyx()
        def line(y, value, attr=0):
            if 0 <= y < height:
                try:
                    screen.addnstr(y, 0, safe_text(value), max(0, width - 1), attr)
                except curses.error:
                    pass  # Resize races and terminal wide-character boundaries.
        if height < MIN_ROWS or width < MIN_COLS:
            line(0, 'Auton: enlarge terminal to 64x12; q quits')
            screen.refresh()
            return
        line(0, 'AUTON %s | READ ONLY | %s' % (__version__, 'PAUSED' if self.paused else 'LIVE'), curses.A_BOLD)
        health = 'unchecked' if not self.data else ('error' if self.data.get('errors') else 'ok')
        line(1, 'Daemon %s (%s/%s) [%s]   [ / ] switch' %
             (self.daemon, self.daemon_index + 1, len(self.names), health))
        counts = self.data.get('stats', {}).get('jobs_by_status', {})
        line(2, 'Queued: %s  Running: %s  Completed: %s | %s' %
             (counts.get('new', 0), counts.get('processing', 0), counts.get('complete', 0),
              '  '.join(('[' + name + ']') if i == self.view else name for i, name in enumerate(VIEWS))))
        line(3, 'Filter: %s%s | state=%s endpoint=%s' %
             (self.search, '_' if self.editing else '', self.state or 'all', self.endpoint or 'all'))
        errors = self.data.get('errors', {})
        line(4, ' | '.join('%s: %s' % item for item in errors.items()) if errors else
             ('Refreshing...' if self.monitor.worker is not None else 'Last refresh completed'))
        available = height - 8
        if self.view == 2:
            job = self.selected_job()
            detail = self.data.get('detail', {})
            if job is None or detail.get('uid') != job[1]:
                line(5, 'No current job detail (select a job and press Enter).')
            else:
                line(5, '%s | %s | exit=%s | %s' % (detail['uid'], detail['status'],
                     detail.get('return_code'), 'stderr / diagnostics' if self.stderr else 'stdout'), curses.A_BOLD)
                line(6, 'Started: %s  Ended: %s' % (detail.get('started_at'), detail.get('ended_at')))
                raw = ''.join(detail.get('errors' if self.stderr else 'stream', []))
                lines = [part for value in raw.splitlines() for part in
                         (textwrap.wrap(safe_text(value), max(1, width - 1)) or [''])] or ['(empty)']
                self.output_line = min(self.output_line, max(0, len(lines) - available + 1))
                for y, value in enumerate(lines[self.output_line:self.output_line + available - 1], 7):
                    line(y, value)
        else:
            rows = self.rows()
            line(5, 'ENDPOINT' if self.view == 1 else 'STATE       EXIT  JOB', curses.A_BOLD)
            start = max(0, self.index - available + 1)
            if not rows:
                line(6, '(no matching entries)')
            for i, item in enumerate(rows[start:start + available], start):
                value = item['name'] if self.view == 1 else '%-11s %-5s %s' % (
                    STATUS_LABELS[item['status']], item.get('return_code'), item['uid'])
                line(6 + i - start, value, curses.A_REVERSE if i == self.index else 0)
        line(height - 2, 'Tab view | Enter open | j/k move | / search | s state | c clear')
        line(height - 1, 'r refresh | p pause | v stdout/stderr | PgUp/PgDn scroll | q quit')
        screen.refresh()


def run(specs, uris, auth=None, http_timeout=30, refresh=DEFAULT_REFRESH):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise ValueError('TUI requires an interactive terminal')
    if not math.isfinite(refresh) or refresh < 0.2:
        raise ValueError('refresh must be at least 0.2 seconds')
    clients = {name: DaemonClient(uri, auth, http_timeout)
               for name, uri in daemon_specs(specs, uris).items()}
    monitor = Monitor(clients)
    view = OperatorView(monitor, refresh)
    def display(screen):
        screen.keypad(True)
        screen.timeout(100)
        try:
            curses.curs_set(0)
        except curses.error:
            pass
        while True:
            view.tick()
            view.draw(screen)
            if not view.handle(screen.getch()):
                break
    try:
        curses.wrapper(display)
    except curses.error:
        raise ValueError('unable to initialize or draw TUI; check TERM and terminal capabilities') from None
    finally:
        monitor.close()
    return 0

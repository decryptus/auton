# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Terminal-independent daemon snapshots and bounded background refresh."""

import queue
import threading
import time
from collections import deque

from auton_client.visibility import VisibilityError

SNAPSHOT_READS = ('health', 'endpoints', 'jobs', 'stats')
DEFAULT_READ_CONCURRENCY = 4


def aggregate_snapshots(names, snapshots, selected_job=None):
    """Combine reads, preserving daemon identity and incomplete coverage."""
    result = {'jobs': [], 'endpoints': [], 'daemons': [], 'errors': {}}
    counts = {}
    responding = 0
    for name in names:
        data = snapshots.get(name)
        if data is None:
            result['daemons'].append({'name': name, 'state': 'pending', 'jobs': None,
                                      'error': ''})
            continue
        errors = data.get('errors', {})
        for operation, error in errors.items():
            result['errors']['%s/%s' % (name, operation)] = error
        maintenance = data.get('health', {}).get('maintenance', {})
        state = 'error' if errors else 'maintenance' if maintenance.get('enabled') else 'ok'
        result['daemons'].append({'name': name, 'state': state,
                                  'jobs': len(data['jobs']) if 'jobs' in data else None,
                                  'error': '; '.join('%s: %s' % item for item in errors.items()) or maintenance.get('reason', '')})
        if 'jobs' in data:
            responding += 1
        for job in data.get('jobs', []):
            result['jobs'].append(dict(job, daemon=name))
            counts[job['status']] = counts.get(job['status'], 0) + 1
        result['endpoints'].extend(dict(endpoint, daemon=name)
                                   for endpoint in data.get('endpoints', []))
        if selected_job is not None and selected_job[0] == name and 'detail' in data:
            result['detail'] = dict(data['detail'], daemon=name)
    result['stats'] = {'jobs': len(result['jobs']), 'endpoints': len(result['endpoints']),
                       'jobs_by_status': counts, 'responding': responding, 'total': len(names)}
    result['partial'] = any(item['state'] not in ('ok', 'maintenance') for item in result['daemons'])
    return result


def snapshot(client, job=None, stopped=None):
    """Collect independently useful reads; preserve partial errors explicitly."""
    data = {'errors': {}, 'checked_at': time.time()}
    for name in SNAPSHOT_READS:
        if stopped is not None and stopped.is_set():
            return data
        try:
            data[name] = getattr(client, name)()
        except VisibilityError as error:
            data['errors'][name] = str(error)
    if job is not None and not (stopped is not None and stopped.is_set()):
        try:
            data['detail'] = client.detail(*job)
        except VisibilityError as error:
            data['errors']['detail'] = str(error)
    return data


class Monitor:
    """At most one active refresh; the caller tags results to reject stale selections."""
    def __init__(self, clients):
        self.clients = dict(clients)
        self.results = queue.Queue(maxsize=1)
        self.stopped = threading.Event()
        self.worker = None

    def refresh(self, daemon, job=None):
        if self.stopped.is_set() or self.worker is not None or not self.results.empty():
            return False
        client = self.clients[daemon]
        def collect():
            try:
                data = snapshot(client, job, self.stopped)
            except Exception:
                # Never send exception reprs containing credentials to a terminal.
                data = {'errors': {'refresh': 'unable to read daemon response'}}
            if not self.stopped.is_set():
                self.results.put_nowait((daemon, job, data))
        self.worker = threading.Thread(target=collect, name='auton-monitor', daemon=True)
        self.worker.start()
        return True

    def poll(self):
        try:
            result = self.results.get_nowait()
        except queue.Empty:
            return None
        self.worker.join()
        self.worker = None
        return result

    def close(self):
        # In-flight GET is bounded by its HTTP timeout. Do not block terminal exit.
        self.stopped.set()


class FleetMonitor:
    """Bounded parallel GETs with incremental results; no execution or failover."""
    def __init__(self, clients, max_workers=DEFAULT_READ_CONCURRENCY):
        if not clients or not isinstance(max_workers, int) or max_workers < 1:
            raise ValueError('clients and positive read concurrency are required')
        self.clients = dict(clients)
        self.monitors = {name: Monitor({name: client}) for name, client in self.clients.items()}
        self.max_workers = max_workers
        self.pending = deque()
        self.active = set()
        self.snapshots = {}
        self.selection = None
        self.job = None
        self.closed = False

    @property
    def worker(self):
        return next((self.monitors[name].worker for name in self.active), None)

    def refresh(self, daemon, job=None):
        if self.closed or self.pending or self.active:
            return False
        names = list(self.clients) if daemon is None else [daemon]
        if any(name not in self.clients for name in names):
            raise ValueError('unknown daemon')
        if daemon is None and job is not None and (len(job) != 3 or job[0] not in self.clients):
            raise ValueError('aggregate detail requires daemon, endpoint and uid')
        self.selection, self.job = daemon, job
        self.snapshots = {}
        self.pending.extend(names)
        self._start_pending()
        return True

    def _start_pending(self):
        while self.pending and len(self.active) < self.max_workers and not self.closed:
            name = self.pending.popleft()
            job = self.job
            if self.selection is None:
                job = job[1:] if job is not None and job[0] == name else None
            self.monitors[name].refresh(name, job)
            self.active.add(name)

    def poll(self):
        for name in list(self.active):
            result = self.monitors[name].poll()
            if result is None:
                continue
            self.active.remove(name)
            self.snapshots[name] = result[2]
            self._start_pending()
            data = (aggregate_snapshots(list(self.clients), self.snapshots, self.job)
                    if self.selection is None else result[2])
            return self.selection, self.job, data
        return None

    def close(self):
        self.closed = True
        self.pending.clear()
        for monitor in self.monitors.values():
            monitor.close()

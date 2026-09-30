# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit Unix worker lifecycle for client-hosted unattended operations.

The worker owns the already validated service. It never starts the CLI, stores
credentials in reports, or replays work after a host/worker crash.
"""
import json
import fcntl
import os
import resource
import signal
import stat
import threading
from pathlib import Path

from auton_client.operations import operation_identity
from auton_client.progress import OperationProgress

REPORT_NAME = 'observation.json'
TEMP_REPORT_NAME = '.observation.tmp'
PRIVATE_DIRECTORY_MODE = 0o700
PRIVATE_FILE_MODE = 0o600


class ObservationWriter:
    def __init__(self, directory):
        self.directory = Path(directory).absolute()
        os.mkdir(self.directory, PRIVATE_DIRECTORY_MODE)
        info = self.directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
            raise ValueError('observation directory must be private and owned by this user')
        self.directory_fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        # Launchers may have closed stdin/stdout/stderr. Keep the directory out
        # of that range before the worker redirects its standard descriptors.
        if self.directory_fd < 3:
            original = self.directory_fd
            try:
                self.directory_fd = fcntl.fcntl(original, fcntl.F_DUPFD_CLOEXEC, 3)
            finally:
                os.close(original)
        self.lock = threading.Lock()

    def write(self, result):
        encoded = (json.dumps(result, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
        with self.lock:
            fd = os.open(TEMP_REPORT_NAME, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         PRIVATE_FILE_MODE, dir_fd=self.directory_fd)
            try:
                with os.fdopen(fd, 'wb') as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.rename(TEMP_REPORT_NAME, REPORT_NAME, src_dir_fd=self.directory_fd,
                          dst_dir_fd=self.directory_fd)
                os.fsync(self.directory_fd)
            except BaseException:
                try:
                    os.unlink(TEMP_REPORT_NAME, dir_fd=self.directory_fd)
                except FileNotFoundError:
                    pass
                raise

    def close(self):
        os.close(self.directory_fd)


class DurableProgress(OperationProgress):
    def __init__(self, writer, stopped):
        super().__init__()
        self.writer, self.stopped = writer, stopped
        self.write_lock = threading.Lock()

    def _save(self):
        data = self.snapshot()
        targets = {}
        scenario_mode = any(row['scenario'] for row in data['rows'])
        for row in data['rows']:
            state = {'pending': 'not_submitted', 'submitting': 'unknown'}.get(row['status'], row['status'])
            item = dict(target=row['target'], uri=row['uri'], job_id=row['job_id'],
                        uid=row['endpoint'] + ':' + row['job_id'] if row['job_id'] else None,
                        endpoint=row['endpoint'], status=state, return_code=row['return_code'],
                        stdout=[], stderr=[], error=None, output_truncated=False,
                        duration_ms=row['duration_ms'])
            if scenario_mode:
                target = targets.setdefault(row['target'], dict(target=row['target'], uri=row['uri'],
                    status='unknown', duration_ms=0, scenarios=[]))
                scenario = next((s for s in target['scenarios'] if s['name'] == row['scenario']), None)
                if scenario is None:
                    scenario = dict(name=row['scenario'], status='unknown', steps=[])
                    target['scenarios'].append(scenario)
                item['step'] = row['step']
                scenario['steps'].append(item)
            else:
                targets[row['target']] = item
        report = dict(operation_id=data['operation_id'], status='incomplete', targets=list(targets.values()),
                      worker_pid=os.getpid(), checkpoint=True)
        if scenario_mode:
            report['kind'] = 'scenario'
        elif data['rows']:
            report['endpoint'] = data['rows'][0]['endpoint']
        try:
            self.writer.write(report)
        except BaseException:
            self.stopped.set()
            raise

    def begin(self, operation_id, rows):
        with self.write_lock:
            super().begin(operation_id, rows)
            self._save()

    def update(self, key, result):
        with self.write_lock:
            super().update(key, result)
            self._save()

    def finish(self, status):
        # The worker writes the full final result immediately after run returns.
        super().finish(status)


def start_detached(service, directory, operation_id=None):
    """Start an explicit detached worker, with a new private observation directory."""
    if not hasattr(os, 'fork') or threading.active_count() != 1:
        raise ValueError('detached execution requires a single-threaded Unix launcher')
    operation_id = operation_identity(operation_id)
    writer = ObservationWriter(directory)
    try:
        writer.write(dict(operation_id=operation_id, status='incomplete', targets=[],
                          checkpoint=True, error='worker starting; no jobs admitted by this checkpoint'))
        first = os.fork()
    except BaseException:
        writer.close()
        raise
    if first:
        _, status = os.waitpid(first, 0)
        writer.close()
        if status != 0:
            raise RuntimeError('unable to detach worker; inspect observation directory')
        return dict(operation_id=operation_id, status='detached',
                    report=str(writer.directory / REPORT_NAME),
                    message='Worker started on this host; inspect the report for outcomes.')
    try:
        os.setsid()
        if os.fork():
            os._exit(0)
        # Keep only the observation directory descriptor; no inherited terminal/log.
        maximum = resource.getrlimit(resource.RLIMIT_NOFILE)[0]
        maximum = 65536 if maximum == resource.RLIM_INFINITY else int(maximum)
        os.closerange(3, writer.directory_fd)
        os.closerange(writer.directory_fd + 1, maximum)
        null = os.open(os.devnull, os.O_RDWR)
        for fd in (0, 1, 2):
            os.dup2(null, fd)
        if null > 2:
            os.close(null)
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        stopped = threading.Event()
        signal.signal(signal.SIGTERM, lambda *_: stopped.set())
        signal.signal(signal.SIGINT, lambda *_: stopped.set())
        progress = DurableProgress(writer, stopped)
        result = service.run(operation_id, stopped=stopped, progress=progress)
        result.update(worker_pid=os.getpid(), checkpoint=False)
        writer.write(result)
        writer.close()
        os._exit(0)
    except BaseException:
        # Preserve the last durable evidence; no automatic retry/resume.
        os._exit(1)

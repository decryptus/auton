"""Local durable job snapshots using Sonicprobe AnySQL; never an execution queue."""
import copy
import fcntl
import json
import math
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager

from sonicprobe.libs import anysql
from sonicprobe.libs.urisup import uri_help_unsplit
from auton.classes.job import JobObject, STATUS_NEW, STATUS_PROCESSING, STATUS_COMPLETE
from auton.classes.exceptions import AutonConfigurationError

SCHEMA_VERSION = 1
APPLICATION_ID = 1096111182
MAX_STORED_JOBS = 10000
MAX_STORED_OUTPUT_BYTES = 16777216
MAX_METADATA_BYTES = 131072
MAX_DIAGNOSTIC_BYTES = 16896
HISTORY_FIELDS = frozenset(('backend', 'path', 'timeout'))
SNAPSHOT_FIELDS = frozenset(('uid', 'endpoint', 'owner', 'execution_id', 'status', 'return_code',
                            'started_at', 'ended_at', 'admitted_at', 'result', 'errors',
                            'outcome', 'outcome_reason', 'execution_uncertain'))
SCHEMA_SQL = '''CREATE TABLE jobs (
    uid TEXT PRIMARY KEY NOT NULL,
    status TEXT NOT NULL,
    ended_at REAL,
    record TEXT NOT NULL
)'''
EXPECTED_COLUMNS = (('uid', 'TEXT', 1, None, 1), ('status', 'TEXT', 1, None, 0),
                    ('ended_at', 'REAL', 0, None, 0), ('record', 'TEXT', 1, None, 0))


class JobStoreUnavailable(Exception):
    pass


def history_config(general, directory=None):
    if 'job_storage' not in general:
        return None
    cfg = general['job_storage']
    if not isinstance(cfg, dict) or set(cfg) - HISTORY_FIELDS or cfg.get('backend') != 'sqlite':
        raise AutonConfigurationError('job_storage requires backend: sqlite and only path/timeout options')
    path = cfg.get('path')
    if not isinstance(path, str) or not path or '\x00' in path or path == ':memory:' or path.startswith('file:'):
        raise AutonConfigurationError('job_storage.path must name a local database file')
    if not os.path.isabs(path):
        if not directory:
            raise AutonConfigurationError('relative job_storage.path requires a configuration file directory')
        path = os.path.join(directory, path)
    path = os.path.abspath(path)
    auth_path = general.get('authentication', {}).get('path')
    if auth_path and os.path.realpath(auth_path) == os.path.realpath(path):
        raise AutonConfigurationError('job and authentication databases must be separate files')
    timeout = cfg.get('timeout', 5)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 30):
        raise AutonConfigurationError('job_storage.timeout must be > 0 and <= 30 seconds')
    return dict(backend='sqlite', path=path, timeout=timeout)


def snapshot(obj):
    with obj.output_lock:
        return copy.deepcopy({field: getattr(obj, field) for field in SNAPSHOT_FIELDS})


def restore(record, max_output_bytes):
    obj = JobObject(record['endpoint'], record['uid'], record['endpoint'], 'run', principal=record['owner'])
    for name, value in record.items():
        setattr(obj, name, value)
    obj.max_output_bytes = max_output_bytes
    obj.output_size = sum(len(part.encode('utf-8')) for part in obj.result + obj.errors)
    obj.clear_input()
    return obj


class SQLiteJobStore:
    """One daemon per file, thread-local transaction connections, no automatic retry.

    The lifetime flock prevents another daemon from treating live jobs as crash
    remnants. It is independent of SQLite's own transaction locks. Never unlink
    or replace the database while the store is open.
    """
    def __init__(self, path, timeout=5, max_output_bytes=1048576):
        self.path = os.path.abspath(path)
        self.pid = os.getpid()
        self.lock = threading.RLock()
        self.descriptor = None
        self.failed = False
        self.max_output_bytes = max_output_bytes
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or not 0 < timeout <= 30
                or type(max_output_bytes) is not int or not 0 < max_output_bytes <= MAX_STORED_OUTPUT_BYTES):
            raise ValueError('invalid job storage limits')
        self.max_record_bytes = max_output_bytes * 6 + MAX_METADATA_BYTES
        self.uri = uri_help_unsplit(('sqlite3', None, self.path, (('timeout_ms', str(timeout * 1000)),), None))
        try:
            self._check_parent()
            self.descriptor = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            info = os.fstat(self.descriptor)
            self._check_file(info)
            self.identity = (info.st_dev, info.st_ino)
            fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self._transaction() as cursor:
                version = self._query(cursor, 'PRAGMA user_version').fetchone(raw=True)[0]
                app_id = self._query(cursor, 'PRAGMA application_id').fetchone(raw=True)[0]
                tables = self._query(cursor, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall(raw=True)
                if version == 0 and app_id == 0 and not tables:
                    self._query(cursor, SCHEMA_SQL)
                    self._query(cursor, 'CREATE INDEX jobs_retention ON jobs(status, ended_at)')
                    self._query(cursor, 'PRAGMA application_id=%d' % APPLICATION_ID)
                    self._query(cursor, 'PRAGMA user_version=%d' % SCHEMA_VERSION)
                elif version != SCHEMA_VERSION or app_id != APPLICATION_ID:
                    raise ValueError('incompatible job database')
                columns = tuple(tuple(row[1:]) for row in self._query(cursor, 'PRAGMA table_info(jobs)').fetchall(raw=True))
                if columns != EXPECTED_COLUMNS:
                    raise ValueError('incompatible job schema')
        except Exception:
            self.close()
            raise JobStoreUnavailable('cannot open private job storage (permissions, ownership, lock or schema)') from None

    def _check_parent(self):
        info = os.stat(os.path.dirname(self.path))
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o022:
            raise ValueError('unsafe job storage directory')

    @staticmethod
    def _check_file(info):
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o077 or info.st_nlink != 1):
            raise ValueError('unsafe job storage file')

    @staticmethod
    def _query(cursor, query, parameters=None):
        cursor.query(query, parameters=parameters)
        return cursor

    @contextmanager
    def _transaction(self):
        if self.pid != os.getpid():
            raise JobStoreUnavailable('job storage inherited across fork')
        with self.lock:
            connection = None
            try:
                if self.descriptor is None or self.failed:
                    raise ValueError('closed or failed storage')
                self._check_parent()
                info = os.lstat(self.path)
                self._check_file(info)
                if self.identity != (info.st_dev, info.st_ino):
                    raise ValueError('job storage replaced')
                connection = anysql.connect_by_uri(self.uri, auto_reconnect=False)
                cursor = connection.cursor()
                self._query(cursor, 'PRAGMA synchronous=FULL')
                self._query(cursor, 'PRAGMA secure_delete=ON')
                if self._query(cursor, 'PRAGMA journal_mode=DELETE').fetchone(raw=True)[0].lower() != 'delete':
                    raise ValueError('unsupported journal mode')
                self._query(cursor, 'BEGIN IMMEDIATE')
                yield cursor
                connection.commit()
            except Exception:
                self.failed = True
                raise JobStoreUnavailable('job storage unavailable; recovery requires restart') from None
            finally:
                if connection is not None:
                    try:
                        connection.close()  # Rolls back uncommitted work, including BaseException.
                    except sqlite3.Error:
                        self.failed = True
                        raise JobStoreUnavailable('job storage close failed') from None

    def _validate(self, record):
        if not isinstance(record, dict) or set(record) != SNAPSHOT_FIELDS:
            raise ValueError('invalid job snapshot fields')
        for name in ('uid', 'endpoint', 'execution_id'):
            if not isinstance(record[name], str) or not record[name] or len(record[name]) > 1024:
                raise ValueError('invalid job identity')
        if not record['uid'].startswith(record['endpoint'] + ':'):
            raise ValueError('inconsistent job identity')
        if record['owner'] is not None and (not isinstance(record['owner'], str) or len(record['owner']) > 1024):
            raise ValueError('invalid job owner')
        if record['status'] not in (STATUS_NEW, STATUS_PROCESSING, STATUS_COMPLETE):
            raise ValueError('invalid job status')
        if record['return_code'] is not None and type(record['return_code']) is not int:
            raise ValueError('invalid return code')
        for name in ('admitted_at', 'started_at', 'ended_at'):
            value = record[name]
            if value is not None and (type(value) not in (float, int) or not math.isfinite(value)):
                raise ValueError('invalid job timestamp')
        if record['status'] == STATUS_COMPLETE and record['ended_at'] is None:
            raise ValueError('missing completion time')
        for name in ('outcome', 'outcome_reason'):
            if record[name] is not None and (not isinstance(record[name], str) or len(record[name]) > 1024):
                raise ValueError('invalid outcome')
        if type(record['execution_uncertain']) is not bool:
            raise ValueError('invalid uncertainty marker')
        for name in ('result', 'errors'):
            if not isinstance(record[name], list) or any(not isinstance(part, str) for part in record[name]):
                raise ValueError('invalid output')
        # The worker may add a bounded diagnostic after exhausting its output budget.
        if sum(len(part.encode('utf-8')) for part in record['result'] + record['errors']) > self.max_output_bytes + MAX_DIAGNOSTIC_BYTES:
            raise ValueError('stored output exceeds configured limit')

    def _encode(self, record):
        self._validate(record)
        data = json.dumps(record, ensure_ascii=True, allow_nan=False, separators=(',', ':'))
        if len(data.encode('utf-8')) > self.max_record_bytes:
            raise ValueError('job snapshot too large')
        return data

    def save(self, record):
        with self._transaction() as cursor:
            data = self._encode(record)
            self._query(cursor, 'INSERT OR REPLACE INTO jobs(uid, status, ended_at, record) VALUES (?, ?, ?, ?)',
                        (record['uid'], record['status'], record['ended_at'], data))

    def delete(self, uid):
        with self._transaction() as cursor:
            self._query(cursor, 'DELETE FROM jobs WHERE uid=?', (uid,))

    def recover(self, now, ttl, capacity):
        if type(capacity) is not int or not 0 < capacity <= MAX_STORED_JOBS:
            raise ValueError('persistent max_jobs must be between 1 and %d' % MAX_STORED_JOBS)
        result = []
        with self._transaction() as cursor:
            self._query(cursor, 'DELETE FROM jobs WHERE status=? AND ended_at<=?', (STATUS_COMPLETE, now - ttl))
            # Every retained unfinished job becomes terminal below; old history may
            # therefore be evicted when capacity is reduced at restart.
            self._query(cursor, 'DELETE FROM jobs WHERE uid NOT IN (SELECT uid FROM jobs ORDER BY rowid DESC LIMIT ?)', (capacity,))
            rows = self._query(cursor, '''SELECT uid, status, ended_at,
                CASE WHEN length(CAST(record AS BLOB)) <= ? THEN record ELSE NULL END
                FROM jobs ORDER BY rowid''', (self.max_record_bytes,)).fetchall(raw=True)
            for uid, status, ended_at, data in rows:
                record = json.loads(data)
                self._validate(record)
                if (uid, status, ended_at) != (record['uid'], record['status'], record['ended_at']):
                    raise ValueError('inconsistent stored job')
                if status != STATUS_COMPLETE:
                    uncertain = status == STATUS_PROCESSING
                    record.update(status=STATUS_COMPLETE, return_code=130, ended_at=now,
                                  outcome='job.interrupted', execution_uncertain=uncertain,
                                  outcome_reason='daemon_restart' if uncertain else 'restart_before_launch')
                    record['errors'].append('ERROR: daemon restarted; execution outcome unknown; command will not be replayed.\n'
                                            if uncertain else 'ERROR: daemon restarted before launch; command was not executed and will not be replayed.\n')
                    self._query(cursor, 'UPDATE jobs SET status=?, ended_at=?, record=? WHERE uid=?',
                                (STATUS_COMPLETE, now, self._encode(record), uid))
                result.append(record)
        return result

    def close(self):
        if self.pid != os.getpid():
            raise JobStoreUnavailable('cannot close inherited job storage')
        with self.lock:
            if self.descriptor is not None:
                os.close(self.descriptor)
                self.descriptor = None

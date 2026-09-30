# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional job snapshots using DWho's Redis connection adapter.

A fenced single-daemon namespace protects history writes. Losing the lease disables
writes/admission; it cannot prove that an already running external command stopped.
"""
import json
import os
import re
import threading
import time
import uuid
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

from auton.classes.job_store import (JobStoreUnavailable, MAX_METADATA_BYTES,
    MAX_STORED_JOBS, MAX_STORED_OUTPUT_BYTES, encode_snapshot, validate_snapshot)

REDIS_NAME = 'auton-job-history'
NAMESPACE_PATTERN = re.compile(r'[a-z0-9][a-z0-9-]{0,63}')
LEASE_SECONDS = 30
HEARTBEAT_SECONDS = 5
REDIS_FIELDS = frozenset(('backend', 'url', 'namespace', 'timeout'))
LEASE_CHECK = "if redis.call('GET',KEYS[1]) ~= ARGV[1] then return redis.error_reply('lease lost') end; "
RENEW_SCRIPT = LEASE_CHECK + "return redis.call('EXPIRE',KEYS[1],ARGV[2])"
RELEASE_SCRIPT = "if redis.call('GET',KEYS[1]) == ARGV[1] then return redis.call('DEL',KEYS[1]) end; return 0"
SAVE_SCRIPT = LEASE_CHECK + """
if redis.call('HEXISTS',KEYS[2],ARGV[2]) == 0 and redis.call('HLEN',KEYS[2]) >= tonumber(ARGV[5]) then
 return redis.error_reply('history capacity exceeded') end
redis.call('HSET',KEYS[2],ARGV[2],ARGV[3]); redis.call('ZADD',KEYS[3],'NX',ARGV[4],ARGV[2]); return 1
"""
DELETE_SCRIPT = LEASE_CHECK + "redis.call('HDEL',KEYS[2],ARGV[2]); return redis.call('ZREM',KEYS[3],ARGV[2])"
IDS_SCRIPT = LEASE_CHECK + """
if redis.call('HLEN',KEYS[2]) ~= redis.call('ZCARD',KEYS[3]) or redis.call('HLEN',KEYS[2]) > tonumber(ARGV[2]) then
 return redis.error_reply('inconsistent history index') end
return redis.call('ZRANGE',KEYS[3],0,-1)
"""
READ_SCRIPT = LEASE_CHECK + """
if redis.call('HSTRLEN',KEYS[2],ARGV[2]) > tonumber(ARGV[3]) then return redis.error_reply('oversized snapshot') end
return redis.call('HGET',KEYS[2],ARGV[2])
"""


def redis_settings(settings):
    if not isinstance(settings, dict) or set(settings) - REDIS_FIELDS:
        raise ValueError('invalid Redis history settings')
    namespace, url = settings.get('namespace'), settings.get('url')
    timeout = settings.get('timeout', 3)
    if not isinstance(namespace, str) or NAMESPACE_PATTERN.fullmatch(namespace) is None:
        raise ValueError('Redis history requires an explicit namespace (1-64 lowercase letters/digits/hyphens)')
    if type(timeout) not in (float, int) or not 0 < timeout <= 5:
        raise ValueError('Redis timeout must be > 0 and <= 5 seconds')
    if not isinstance(url, str) or len(url) > 4096:
        raise ValueError('invalid Redis URL')
    try:
        parts = urlsplit(url)
        if parts.scheme not in ('redis', 'rediss', 'unix') or parts.fragment:
            raise ValueError()
        if parts.scheme != 'unix' and not parts.hostname or parts.scheme == 'unix' and not parts.path:
            raise ValueError()
        query = dict(parse_qsl(parts.query))
        query.update(socket_timeout=str(timeout), socket_connect_timeout=str(timeout),
                     decode_responses='True', retry_on_timeout='False')
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
    except ValueError:
        raise ValueError('invalid Redis URL') from None
    return dict(backend='redis', url=url, namespace=namespace, timeout=timeout)


class RedisJobStore:
    def __init__(self, url, namespace, timeout=3, max_output_bytes=1048576, adapter_factory=None):
        settings = redis_settings(dict(url=url, namespace=namespace, timeout=timeout))
        if type(max_output_bytes) is not int or not 0 < max_output_bytes <= MAX_STORED_OUTPUT_BYTES:
            raise ValueError('invalid output limit')
        if adapter_factory is None:
            from dwho.adapters.redis import DWhoAdapterRedis
            adapter_factory = DWhoAdapterRedis
        self.adapter = None
        self.failed, self.closed = False, False
        self.pid = os.getpid()
        self.owner = uuid.uuid4().hex
        self.keys = ['{auton-%s}:%s' % (namespace, suffix) for suffix in ('lease', 'jobs', 'index')]
        self.max_output_bytes = max_output_bytes
        self.max_record_bytes = max_output_bytes * 6 + MAX_METADATA_BYTES
        self.stopped = threading.Event()
        self.worker = None
        try:
            self.adapter = adapter_factory({'general': {'redis': {REDIS_NAME: {'url': settings['url']}}}})
            self.conn = self.adapter.servers[REDIS_NAME]['conn']
            from redis.backoff import NoBackoff
            from redis.retry import Retry
            self.conn.set_retry(Retry(NoBackoff(), 0))
            if not self.conn.set(self.keys[0], self.owner, nx=True, ex=LEASE_SECONDS):
                raise ValueError('history namespace is already in use')
            self.worker = threading.Thread(target=self._heartbeat, name='auton-redis-lease', daemon=True)
            self.worker.start()
        except Exception:
            self.close()
            raise JobStoreUnavailable('cannot acquire Redis history namespace') from None

    def _call(self, script, *args):
        if self.closed or self.failed or self.pid != os.getpid():
            raise JobStoreUnavailable('Redis history is unavailable')
        try:
            return self.conn.eval(script, len(self.keys), *self.keys, self.owner, *args)
        except Exception:
            self.failed = True
            raise JobStoreUnavailable('Redis history operation failed; no automatic retry') from None

    def _heartbeat(self):
        while not self.stopped.wait(HEARTBEAT_SECONDS):
            try:
                self._call(RENEW_SCRIPT, LEASE_SECONDS)
            except JobStoreUnavailable:
                return

    def save(self, record):
        data = encode_snapshot(record, self.max_output_bytes, self.max_record_bytes)
        self._call(SAVE_SCRIPT, record['uid'], data, record['admitted_at'] or time.time(), MAX_STORED_JOBS)

    def delete(self, uid):
        self._call(DELETE_SCRIPT, uid)

    def recover(self, now, ttl, capacity):
        if type(capacity) is not int or not 0 < capacity <= MAX_STORED_JOBS:
            raise ValueError('invalid persistent job capacity')
        ids = self._call(IDS_SCRIPT, MAX_STORED_JOBS)
        result = []
        for index, uid in enumerate(ids):
            if index < len(ids) - capacity:
                self.delete(uid)
                continue
            data = self._call(READ_SCRIPT, uid, self.max_record_bytes)
            try:
                record = json.loads(data)
                validate_snapshot(record, self.max_output_bytes)
                if record['uid'] != uid:
                    raise ValueError('inconsistent job identity')
                if record['status'] == 'complete' and record['ended_at'] <= now - ttl:
                    self.delete(uid)
                    continue
                if record['status'] != 'complete':
                    uncertain = record['status'] == 'processing'
                    record.update(status='complete', return_code=130, ended_at=now,
                        outcome='job.interrupted', execution_uncertain=uncertain,
                        outcome_reason='daemon_restart' if uncertain else 'restart_before_launch')
                    record['errors'].append('ERROR: daemon restarted; command will not be replayed.\n')
                    self.save(record)
                result.append(record)
            except Exception:
                self.failed = True
                raise JobStoreUnavailable('invalid Redis job history; evidence retained') from None
        return result

    def close(self):
        if self.pid != os.getpid():
            raise JobStoreUnavailable('cannot close inherited Redis history')
        if self.closed:
            return
        self.stopped.set()
        if self.worker is not None:
            self.worker.join(timeout=6)
        try:
            if hasattr(self, 'conn'):
                self.conn.eval(RELEASE_SCRIPT, 1, self.keys[0], self.owner)
        except Exception:
            pass  # Lease expiry releases abandoned ownership; never delete another owner.
        self.closed = True
        if self.adapter is not None:
            self.adapter.disconnect(name=REDIS_NAME)

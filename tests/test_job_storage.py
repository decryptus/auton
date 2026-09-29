"""Durable lifecycle, restart recovery and storage failures without command replay."""
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from auton.classes.exceptions import AutonConfigurationError
from auton.classes.job import JobObject, STATUS_COMPLETE
from auton.classes.job_store import SQLiteJobStore, JobStoreUnavailable, history_config, snapshot
from auton.classes.jobs import JobService, JobUnavailable, AccessDenied, UnknownJob, UnknownEndpoint
from auton.classes.plugins import AutonEPTSync, EPTS_SYNC
from auton.classes.target import AutonTarget
from auton.plugins.subproc import AutonSubProcPlugin


class JobStorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name) / 'jobs.sqlite3')

    def store(self, **kwargs):
        store = SQLiteJobStore(self.path, **kwargs)
        self.addCleanup(store.close)
        return store

    def service(self, store, **kwargs):
        return JobService({'test': SimpleNamespace(users={'alice': True, 'bob': True})},
                          {'test': AutonEPTSync('test')}, store=store, **kwargs)

    def complete(self, obj, ended=None):
        with obj.output_lock:
            obj.set_return_code(0)
            obj.set_ended_at()
            if ended is not None:
                obj.ended_at = ended
            obj.set_status(STATUS_COMPLETE)
            obj.clear_input()

    def test_config_and_independent_authentication_file(self):
        self.assertIsNone(history_config({}))
        cfg = history_config({'job_storage': {'backend': 'sqlite', 'path': 'jobs.db'}}, self.tmp.name)
        self.assertEqual(cfg['path'], str(Path(self.tmp.name) / 'jobs.db'))
        for cfg in (None, {}, {'backend': 'redis', 'path': self.path},
                    {'backend': 'sqlite', 'path': ':memory:'},
                    {'backend': 'sqlite', 'path': self.path, 'timeout': True},
                    {'backend': 'sqlite', 'path': self.path, 'timeout': float('nan')},
                    {'backend': 'sqlite', 'path': self.path, 'extra': 1}):
            with self.subTest(cfg=cfg), self.assertRaises(AutonConfigurationError):
                history_config({'job_storage': cfg})
        with self.assertRaises(AutonConfigurationError):
            history_config({'job_storage': {'backend': 'sqlite', 'path': self.path},
                            'authentication': {'path': self.path}})

    def test_completed_output_restart_offsets_owner_and_current_acl(self):
        store = self.store()
        service = self.service(store)
        service.submit('test', 'one', {'env': {'SECRET': 'not-stored'}}, 'alice', 0)
        obj = service.objs['test:one']
        with service.availability.launch(obj):
            obj.add_result('first\n')
            obj.add_result('second é\n')
            obj.add_error('stderr\n')
        self.complete(obj)
        self.assertNotIn(b'not-stored', Path(self.path).read_bytes())
        store.close()
        restored = self.service(self.store())
        detail = restored.detail('test', 'one', 'alice', 1)
        self.assertEqual(detail['stream'], ['second é\n'])
        self.assertEqual(detail['errors'], ['stderr\n'])
        self.assertEqual(detail['return_code'], 0)
        self.assertEqual(restored.objs['test:one'].execution_id, obj.execution_id)
        self.assertIsNone(restored.objs['test:one'].payload)
        self.assertEqual(restored.objs['test:one'].vars, {})
        self.assertTrue(restored.queues['test'].queue.empty())
        with self.assertRaises(AccessDenied):
            restored.detail('test', 'one', 'bob')
        restored.endpoints['test'].users = {'alice': False}
        self.assertEqual(restored.list_jobs('alice'), [])
        with self.assertRaises(AccessDenied):
            restored.detail('test', 'one', 'alice')
        restored.endpoints.clear()
        with self.assertRaises(UnknownEndpoint):
            restored.detail('test', 'one', 'alice')

    def test_restart_marks_unfinished_jobs_terminal_and_never_enqueues(self):
        store = self.store()
        service = self.service(store)
        for xid in ('queued', 'running'):
            service.submit('test', xid, {}, 'alice', 0)
        with service.availability.launch(service.objs['test:running']):
            service.objs['test:running'].add_result('not checkpointed')
        store.close()
        store = self.store()
        restored = self.service(store)
        for xid, uncertain in (('queued', False), ('running', True)):
            result = restored.status('test', xid, 'alice', 0)
            self.assertEqual(result['status'], 'complete')
            self.assertEqual(result['outcome'], 'job.interrupted')
            self.assertEqual(result['execution_uncertain'], uncertain)
            self.assertEqual(result['return_code'], 130)
            self.assertEqual(result['stream'], [])
        self.assertTrue(restored.queues['test'].queue.empty())
        before = restored.detail('test', 'running', 'alice')
        store.close()
        self.assertEqual(self.service(self.store()).detail('test', 'running', 'alice'), before)

    def test_ttl_eviction_and_reduced_capacity_survive_restart(self):
        store = self.store()
        service = self.service(store, clock=lambda: 100, result_ttl=10, max_jobs=2)
        service.submit('test', 'old', {}, 'alice')
        self.complete(service.objs['test:old'], 89)
        service.submit('test', 'one', {}, 'alice')
        self.complete(service.objs['test:one'], 95)
        service.submit('test', 'two', {}, 'alice')
        self.complete(service.objs['test:two'], 96)
        service.submit('test', 'three', {}, 'alice')
        self.assertEqual(set(service.objs), {'test:two', 'test:three'})
        store.close()
        restored = self.service(self.store(), clock=lambda: 100, result_ttl=10, max_jobs=1)
        self.assertEqual(set(restored.objs), {'test:three'})

    def test_delete_waits_for_final_write_and_cannot_resurrect_history(self):
        store = self.store()
        service = self.service(store, clock=lambda: 100, result_ttl=10)
        service.submit('test', 'one', {}, 'alice')
        obj = service.objs['test:one']
        entered, release, deleted = threading.Event(), threading.Event(), threading.Event()
        save = store.save
        def paused(record):
            if record['status'] == 'complete':
                entered.set()
                if not release.wait(2):
                    raise RuntimeError('test timeout')
            save(record)
        with patch.object(store, 'save', paused):
            writer = threading.Thread(target=lambda: self.complete(obj, 80))
            writer.start()
            self.assertTrue(entered.wait(1))
            def expire():
                service.list_jobs('alice')
                deleted.set()
            remover = threading.Thread(target=expire)
            remover.start()
            try:
                self.assertFalse(deleted.wait(0.05))
            finally:
                release.set()
                writer.join(2)
                remover.join(2)
            self.assertTrue(deleted.is_set())
        store.close()
        self.assertEqual(self.service(self.store(), clock=lambda: 100).objs, {})

    def test_private_file_lock_schema_corruption_and_replacement(self):
        store = self.store()
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        with self.assertRaises(JobStoreUnavailable):
            SQLiteJobStore(self.path)
        service = self.service(store)
        service.submit('test', 'one', {}, 'alice')
        store.close()
        with sqlite3.connect(self.path) as conn:
            conn.execute("UPDATE jobs SET record='{}'")
        with self.assertRaises(JobUnavailable):
            self.service(self.store())
        # Failed recovery remains rolled back; never silently start with empty history.
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
        # A separate path lets the corrupt store keep its lifetime lock until cleanup.
        other = str(Path(self.tmp.name) / 'other.db')
        safe = SQLiteJobStore(other)
        self.addCleanup(safe.close)
        os.rename(other, other + '.old')
        Path(other).touch(mode=0o600)
        with self.assertRaises(JobStoreUnavailable):
            safe.recover(time.time(), 3600, 128)
        link = Path(self.tmp.name) / 'link.db'
        link.symlink_to(other)
        with self.assertRaises(JobStoreUnavailable):
            SQLiteJobStore(str(link))
        os.chmod(other, 0o644)
        with self.assertRaises(JobStoreUnavailable):
            SQLiteJobStore(other)

    def test_failed_transaction_rolls_back_and_is_not_retried(self):
        store = self.store()
        service = self.service(store)
        service.submit('test', 'one', {}, 'alice')
        original = store._query
        def fail(cursor, sql, parameters=None):
            result = original(cursor, sql, parameters)
            if sql.startswith('DELETE'):
                raise sqlite3.OperationalError('simulated disk error')
            return result
        with patch.object(store, '_query', side_effect=fail), self.assertRaises(JobStoreUnavailable):
            store.delete('test:one')
        with sqlite3.connect(self.path) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM jobs').fetchone()[0], 1)
        with self.assertRaises(JobStoreUnavailable):
            store.delete('test:one')

    def test_real_worker_writes_three_snapshots_and_survives_storage_failure(self):
        for failure in (None, 'new', 'processing', 'complete'):
            with self.subTest(failure=failure):
                path = str(Path(self.tmp.name) / ('%s.db' % failure))
                store = SQLiteJobStore(path)
                plugin = AutonSubProcPlugin('test')
                plugin.users = {'alice': True}
                plugin.target = AutonTarget('test', {'prog': sys.executable, 'timeout': 2})
                queue = AutonEPTSync('test')
                EPTS_SYNC['test'] = queue
                service = JobService({'test': plugin}, {'test': queue}, store=store)
                marker = Path(self.tmp.name) / ('%s.executed' % failure)
                states, done = [], threading.Event()
                save = store.save
                def injected(record):
                    states.append(record['status'])
                    if record['status'] in ('new', 'processing'):
                        self.assertFalse(marker.exists())
                    if record['status'] == failure:
                        raise JobStoreUnavailable('injected')
                    save(record)
                try:
                    with patch.object(store, 'save', injected):
                        payload = {'args': ['-c', 'from pathlib import Path; Path(%r).touch(); print("done")' % str(marker)]}
                        if failure == 'new':
                            with self.assertRaises(JobUnavailable):
                                service.submit('test', 'one', payload, 'alice', 0)
                            self.assertTrue(queue.queue.empty())
                        else:
                            service.submit('test', 'one', payload, 'alice', 0)
                            obj = service.objs['test:one']
                            obj.callback = lambda _: done.set()
                            plugin.start()
                            self.assertTrue(done.wait(3))
                            self.assertTrue(plugin.is_alive())
                            if failure is None:
                                self.assertEqual(service.status('test', 'one', 'alice', 0)['stream'], ['done\n'])
                            else:
                                with self.assertRaises(JobUnavailable):
                                    service.status('test', 'one', 'alice', 0)
                        self.assertEqual(marker.exists(), failure in (None, 'complete'))
                        if failure is not None:
                            self.assertEqual(service.health()['status'], 'degraded')
                            with self.assertRaises(JobUnavailable):
                                service.submit('test', 'two', payload, 'alice', 0)
                        if failure != 'new':
                            self.assertEqual(states, ['new', 'processing', 'complete'])
                finally:
                    if plugin.is_alive():
                        plugin.at_stop()
                    EPTS_SYNC.pop('test', None)
                    store.close()

    def test_snapshot_detaches_output_and_never_serializes_inputs(self):
        obj = JobObject('test', 'test:one', 'test', 'run', payload={'secret': 'input'})
        obj.add_result('one')
        record = snapshot(obj)
        obj.add_result('two')
        self.assertEqual(record['result'], ['one'])
        self.assertNotIn('payload', record)
        self.assertNotIn('vars', record)


class DurableDaemonTests(unittest.TestCase):
    def test_real_daemon_abrupt_restart_preserves_results_and_does_not_replay(self):
        import base64
        import hashlib
        import signal
        import socket
        import subprocess
        import requests
        import yaml
        root = Path(__file__).resolve().parents[1]
        proc, child_pid = None, None
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            with socket.socket() as sock:
                sock.bind(('127.0.0.1', 0))
                port = sock.getsockname()[1]
            passwd = tmp / 'htpasswd'
            passwd.write_text('alice:{SHA}' + base64.b64encode(hashlib.sha1(b'secret').digest()).decode() + '\n')
            cfg = yaml.safe_load((root / 'etc/auton/auton.yml.example').read_text())
            cfg['general'].update(listen_addr='127.0.0.1', listen_port=port,
                                  max_life_time=0, max_requests=0, auth_basic='Test',
                                  auth_basic_file=str(passwd),
                                  job_storage={'backend': 'sqlite', 'path': 'jobs.db'})
            cfg.pop('import_modules', None)
            cfg['modules'] = yaml.safe_load((root / 'etc/auton/modules/job.yml').read_text())
            cfg['endpoints'] = {'test': {'plugin': 'subproc', 'users': {'alice': True},
                                       'config': {'prog': sys.executable, 'timeout': 40}}}
            conf = tmp / 'auton.yml'
            conf.write_text(yaml.safe_dump(cfg))
            uri, auth = 'http://127.0.0.1:%s' % port, ('alice', 'secret')
            def launch():
                process = subprocess.Popen([sys.executable, str(root / 'bin/autond'), '-f', '-c', str(conf),
                    '-p', str(tmp / 'daemon.pid'), '--logfile', str(tmp / 'daemon.log')],
                    env=dict(os.environ, PYTHONPATH=str(root)), cwd=root,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                for _ in range(150):
                    try:
                        response = requests.get(uri + '/health', auth=auth, timeout=0.2)
                        if response.status_code == 200:
                            self.assertTrue(response.json()['storage']['available'])
                            return process
                    except requests.RequestException:
                        pass
                    if process.poll() is not None:
                        break
                    time.sleep(0.02)
                process.terminate()
                process.wait(timeout=5)
                self.fail('daemon did not start: ' + (tmp / 'daemon.log').read_text())
            def run(xid, code):
                response = requests.post(uri + '/run/test/' + xid + '-job', auth=auth,
                                         json={'args': ['-c', code]}, timeout=3)
                self.assertEqual(response.status_code, 200, response.text)
            def detail(xid):
                response = requests.get(uri + '/jobs/test/' + xid + '-job', auth=auth, timeout=2)
                self.assertEqual(response.status_code, 200, response.text)
                return response.json()
            try:
                proc = launch()
                run('finished', 'import sys; print("durable stdout"); print("durable stderr", file=sys.stderr)')
                for _ in range(100):
                    before = detail('finished')
                    if before['status'] == 'complete':
                        break
                    time.sleep(0.02)
                self.assertEqual(before['status'], 'complete')
                marker = tmp / 'running.pid'
                queued = tmp / 'queued.executed'
                run('running', 'import os,time; from pathlib import Path; Path(%r).write_text(str(os.getpid())); time.sleep(30)' % str(marker))
                for _ in range(100):
                    if marker.exists() and marker.read_text():
                        break
                    time.sleep(0.02)
                child_pid = int(marker.read_text())
                run('queued', 'from pathlib import Path; Path(%r).touch()' % str(queued))
                self.assertEqual(detail('queued')['status'], 'new')
                proc.kill()
                proc.wait(timeout=5)
                # Abrupt daemon death need not kill an already launched process.
                # Reap its process group explicitly so this integration test leaks none.
                os.killpg(child_pid, signal.SIGKILL)
                child_pid = None
                proc = launch()
                self.assertEqual(detail('finished'), before)
                for xid, uncertain in (('queued', False), ('running', True)):
                    recovered = detail(xid)
                    self.assertEqual(recovered['status'], 'complete')
                    self.assertEqual(recovered['outcome'], 'job.interrupted')
                    self.assertEqual(recovered['execution_uncertain'], uncertain)
                    self.assertEqual(recovered['return_code'], 130)
                run('after', 'print("after restart")')
                for _ in range(100):
                    after = detail('after')
                    if after['status'] == 'complete':
                        break
                    time.sleep(0.02)
                self.assertEqual(after['stream'], ['after restart\n'])
                self.assertFalse(queued.exists())
            finally:
                if proc is not None and proc.poll() is None:
                    proc.terminate()
                    proc.wait(timeout=5)
                if child_pid is not None:
                    try:
                        os.killpg(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass

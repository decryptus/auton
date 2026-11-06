"""Admission and execution regression tests for explicit client policies."""
import base64
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from auton.classes.client_policy import ClientPolicy, SNAPSHOT_KEY, migration_categories
from auton.classes.exceptions import AutonConfigurationError
from auton.classes.job_schema import validate_input, InvalidArguments
from auton.classes.jobs import JobService
from auton.classes.parameters import InvalidParameters
from auton.classes.plugins import AutonEPTSync, EPTS_SYNC
from auton.classes.target import AutonTarget
from auton.plugins.subproc import AutonSubProcPlugin


class ClientPolicyTests(unittest.TestCase):
    def setUp(self):
        self.default = patch('auton.classes.client_policy.DEFAULT_CLIENT_ALLOW', False)
        self.default.start()
        self.addCleanup(self.default.stop)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.file = self.root / 'vars.env'
        self.file.write_text('MONTH=2026-10\n')

    def policy(self, **overrides):
        config = {'allow-envfiles': [str(self.file)], 'allow-env': {'MONTH': r'2026-\d{2}'}}
        config.update(overrides)
        return ClientPolicy(config)

    def service(self, config):
        plugin = AutonSubProcPlugin('policy')
        plugin.target = AutonTarget('policy', dict(prog=sys.executable, timeout=2, **config))
        self.addCleanup(plugin.do_terminate)
        queue = AutonEPTSync('policy')
        return JobService({'policy': plugin}, {'policy': queue}), plugin, queue

    def test_default_refuses_all_four_categories(self):
        inputs = ({'args': ['--month', 'x']}, {'env': {'MONTH': 'x'}},
                  {'envfiles': [str(self.file)]},
                  {'argfiles': [{'arg': '-f', 'filename': 'f', 'content': 'eA=='}]})
        for payload in inputs:
            with self.subTest(field=next(iter(payload))), self.assertRaises(InvalidParameters):
                ClientPolicy({}).prepare(payload)
        ClientPolicy({}).prepare({})

    def test_transition_keeps_implicit_inputs_and_reports_categories(self):
        with patch('auton.classes.client_policy.DEFAULT_CLIENT_ALLOW', True):
            ClientPolicy({}).prepare({'args': ['positional'], 'env': {'ANY': 'value'}})
        self.assertEqual(migration_categories({}), ['args', 'env', 'argfiles', 'envfiles'])
        self.assertEqual(migration_categories({'allow-args': True, 'allow-env': [],
                                             'disallow-argfiles': True, 'disallow-envfiles': True}), [])
        self.assertIn('args', migration_categories({'disallow-args': False}))

    def test_regex_and_repeated_arguments_at_admission(self):
        config = {'allow-args': {'month': {'regex': r'2026-(0[1-9]|1[0-2])', 'repeatable': True}}}
        service, _, queue = self.service(config)
        with self.assertRaises(InvalidParameters):
            service.submit('policy', 'bad', {'args': ['--month', '2026-13']}, None)
        self.assertEqual(queue.queue.qsize(), 0)
        args = ['--month=2026-09', '--month', '2026-10']
        service.submit('policy', 'good', {'args': args}, None)
        self.assertEqual(queue.qget().get_payload()['args'], args)

    def test_envfile_contents_checked_before_admission(self):
        self.file.write_text('MONTH=invalid-private-value\n')
        service, _, queue = self.service({'allow-envfiles': [str(self.file)],
                                          'allow-env': {'MONTH': r'2026-\d{2}'}})
        with self.assertRaises(InvalidParameters) as error:
            service.submit('policy', 'bad', {'envfiles': [str(self.file)]}, None)
        self.assertNotIn('invalid-private-value', str(error.exception))
        self.assertEqual(queue.queue.qsize(), 0)

    def test_envfile_read_once_and_snapshot_executed(self):
        service, plugin, queue = self.service({'allow-envfiles': [str(self.file)], 'allow-env': ['MONTH'],
            'args': ['-c', 'import os; print(os.environ["MONTH"])']})
        service.submit('policy', 'one', {'envfiles': [str(self.file)]}, None)
        self.file.write_text('MONTH=changed\n')
        obj = queue.qget()
        plugin.do_run(obj)
        self.assertEqual(''.join(obj.result).strip(), '2026-10')

    def test_client_cannot_supply_internal_snapshot(self):
        with self.assertRaises(InvalidArguments):
            validate_input({'endpoint': 'x', 'id': 'one'}, {SNAPSHOT_KEY: {'PRIVATE': 'x'}})

    def test_files_cannot_bypass_env_deny_even_if_overridden(self):
        self.file.write_text('BLOCKED=secret\n')
        policy = self.policy(**{'allow-env': True, 'disallow-env': ['BLOCKED']})
        with self.assertRaises(InvalidParameters):
            policy.prepare({'envfiles': [str(self.file)], 'env': {'BLOCKED': 'other'}})

    def test_exact_paths_and_deny_priority(self):
        forbidden = self.root / 'other.env'
        forbidden.write_text('MONTH=2026-11')
        with self.assertRaises(InvalidParameters):
            self.policy().prepare({'envfiles': [str(forbidden)]})
        with self.assertRaises(InvalidParameters):
            self.policy(**{'allow-envfiles': True, 'disallow-envfiles': [str(self.file)]}).prepare(
                {'envfiles': [str(self.file)]})
        alias = self.root / 'alias.env'
        alias.symlink_to(self.file)
        with self.assertRaises(InvalidParameters):
            self.policy(**{'allow-envfiles': True, 'disallow-envfiles': [str(self.file)]}).prepare(
                {'envfiles': [str(alias)]})

    def test_no_process_env_interpolation_in_explicit_files(self):
        self.file.write_text('MONTH=${HOST_SECRET}\n')
        with patch.dict(os.environ, HOST_SECRET='secret'):
            prepared = self.policy(**{'allow-env': True}).prepare({'envfiles': [str(self.file)]})
        self.assertEqual(prepared[SNAPSHOT_KEY]['MONTH'], '${HOST_SECRET}')

    def test_bad_files_fail_closed_without_opening_devices_or_waiting_on_fifo(self):
        fifo = self.root / 'fifo'
        os.mkfifo(fifo)
        for path in (str(fifo), str(self.root), str(self.root/'missing')):
            with self.subTest(path=path), self.assertRaises(InvalidParameters):
                self.policy(**{'allow-envfiles': True}).prepare({'envfiles': [path]})
        self.file.write_text('x' * 1048577)
        with self.assertRaises(InvalidParameters):
            self.policy().prepare({'envfiles': [str(self.file)]})

    def test_upload_option_and_filename_validation(self):
        policy = ClientPolicy({'allow-argfiles': ['-f']})
        payload = {'argfiles': [{'arg': '-f', 'filename': 'data.json', 'content': 'eA=='}]}
        policy.prepare(payload)
        payload['argfiles'][0]['arg'] = '--evil'
        with self.assertRaises(InvalidParameters):
            policy.prepare(payload)
        payload['argfiles'][0].update(arg='-f', filename='../escape')
        with self.assertRaises(InvalidParameters):
            policy.prepare(payload)

    def test_uploaded_file_contents_are_opaque_and_preserved(self):
        data = b'--not-an-auton-option\n'
        service, plugin, queue = self.service({'allow-argfiles': ['-f'], 'args': ['-c',
            'import sys; print(open(sys.argv[2]).read(), end="")']})
        service.submit('policy', 'file', {'argfiles': [{'arg': '-f', 'filename': 'data',
            'content': base64.b64encode(data).decode()}]}, None)
        obj = queue.qget()
        plugin.do_run(obj)
        self.assertEqual(''.join(obj.result).encode(), data)

    def test_server_defined_args_and_env_remain_available(self):
        service, plugin, queue = self.service({'args': ['-c', 'import os; print(os.environ["FIXED"])'],
                                               'env': {'FIXED': 'server'}})
        service.submit('policy', 'fixed', {}, None)
        obj = queue.qget()
        plugin.do_run(obj)
        self.assertEqual(''.join(obj.result).strip(), 'server')

    def test_invalid_configuration_fails_during_startup(self):
        plugin = AutonSubProcPlugin('policy-startup')
        plugin.config = {'config': {'prog': 'echo', 'allow-args': {'month': {'regex': '['}}}}
        self.addCleanup(EPTS_SYNC.pop, 'policy-startup', None)
        with self.assertRaises(AutonConfigurationError):
            plugin.safe_init()

    def test_startup_warning_is_informative_and_contains_no_values(self):
        plugin = AutonSubProcPlugin('policy-startup')
        plugin.config = {'config': {'prog': 'echo', 'env': {'PRIVATE': 'secret-value'}}}
        self.addCleanup(EPTS_SYNC.pop, 'policy-startup', None)
        with patch('auton.plugins.subproc.DEFAULT_CLIENT_ALLOW', True):
            with self.assertLogs('auton.plugins.subproc', level='WARNING') as captured:
                plugin.safe_init()
        self.assertEqual(len(captured.output), 4)
        self.assertNotIn('secret-value', '\n'.join(captured.output))
        self.assertIn('allow-args: true', captured.output[0])

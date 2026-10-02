"""Unexpected service failures must not disclose details or promise safe replay."""
import unittest
from unittest.mock import Mock

from auton.modules.job import JobModule
from auton.classes.availability import MaintenanceActive
from auton.classes.jobs import AccessDenied
from httpdis.ext.httpdis_json import HttpReqErrJson


class JobHTTPErrorTests(unittest.TestCase):
    def setUp(self):
        self.module = JobModule()
        self.module.service = Mock()
        self.request = Mock()
        self.request.query_params.return_value = {'endpoint': 'test', 'id': 'example'}
        self.request.payload_params.return_value = {}
        self.request.get_headers.return_value = {}
        self.request.get_server_vars.return_value = {'HTTP_AUTH_USER': 'alice'}

    def test_unknown_failures_hide_details_and_never_allow_replay(self):
        for method, service in ((self.module.job_run, self.module.service.submit),
                                (self.module.job_status, self.module.service.status)):
            with self.subTest(method=method.__name__):
                service.side_effect = RuntimeError('SYNTHETIC-INTERNAL-CREDENTIAL')
                with self.assertLogs('auton.modules.job', level='ERROR') as logs:
                    with self.assertRaises(HttpReqErrJson) as caught:
                        method(self.request)
                error = caught.exception
                self.assertEqual(error.code, 503)
                self.assertEqual(error.text, 'job_service_unavailable')
                self.assertNotIn('X-Auton-Admission', error.headers or {})
                self.assertNotIn('SYNTHETIC-INTERNAL-CREDENTIAL', '\n'.join(logs.output))
                service.assert_called_once()

    def test_expected_errors_keep_their_contract(self):
        self.module.service.submit.side_effect = AccessDenied('access denied')
        with self.assertRaises(HttpReqErrJson) as caught:
            self.module.job_run(self.request)
        self.assertEqual(caught.exception.code, 403)
        self.assertEqual(caught.exception.text, 'access denied')
        self.module.service.submit.side_effect = MaintenanceActive()
        with self.assertRaises(HttpReqErrJson) as caught:
            self.module.job_run(self.request)
        self.assertEqual(caught.exception.code, 503)
        self.assertEqual(caught.exception.text, 'daemon_maintenance')
        self.assertEqual(caught.exception.headers['X-Auton-Admission'], 'not-admitted')

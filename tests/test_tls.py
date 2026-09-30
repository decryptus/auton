"""Real native mTLS: certificate trust and application permissions are independent."""
import os
import subprocess
import sys
import unittest
from unittest.mock import patch
import requests
import yaml
from auton.classes.tls import tls_config
from auton.classes.exceptions import AutonConfigurationError
from auton_client.transport import validate_transport
from tls_fixture import certificates
from web_fixture import WebDaemon, ROOT


class MutualTLSTests(unittest.TestCase):
    def test_configuration_cannot_disable_verification_or_application_auth(self):
        for transport, origins in [({'verify': False}, ['https://host']), ({'cert': ['one']}, ['https://host']),
                                   ({'verify': '/ca'}, ['http://host'])]:
            with self.assertRaises(ValueError):
                validate_transport(transport, origins)
        with self.assertRaises(AutonConfigurationError):
            tls_config({'auth_mode': 'anonymous', 'tls': {'certificate': 'cert', 'private_key': 'key', 'client_ca': 'ca'}}, '/tmp')

    def test_real_daemon_requires_both_certificate_and_application_identity(self):
        daemon = WebDaemon()
        self.addCleanup(daemon.close)
        transport = certificates(daemon.path)
        daemon.uri = daemon.uri.replace('http:', 'https:')
        confpath = daemon.path / 'auton.yml'
        conf = yaml.safe_load(confpath.read_text())
        conf['general'].update(web_origin=daemon.uri, tls={'certificate': str(daemon.path / 'server.pem'),
            'private_key': str(daemon.path / 'server.key'), 'client_ca': transport['verify']})
        confpath.write_text(yaml.safe_dump(conf))
        original_get = requests.get
        with patch('web_fixture.requests.get', side_effect=lambda *a, **kw: original_get(*a, **kw, **transport)):
            daemon.start()
        with self.assertRaises(requests.exceptions.SSLError):
            requests.get(daemon.uri + '/health', verify=transport['verify'], timeout=2)
        with self.assertRaises(requests.exceptions.SSLError):
            requests.get(daemon.uri + '/health', cert=transport['cert'], timeout=2)
        self.assertEqual(requests.get(daemon.uri + '/health', timeout=2, **transport).status_code, 401)
        headers = {'Authorization': 'Bearer ' + daemon.token}
        self.assertEqual(requests.get(daemon.uri + '/health', headers=headers, timeout=2, **transport).status_code, 200)
        tokenpath = daemon.path / 'token'
        tokenpath.write_text(daemon.token)
        tokenpath.chmod(0o600)
        command = [sys.executable, str(ROOT / 'bin/auton'), '--uri', daemon.uri, '--endpoint', 'diagnostic',
                   '--token-file', str(tokenpath), '--ca-file', transport['verify'], '--client-cert', transport['cert'][0],
                   '--client-key', transport['cert'][1], '-a=-c', '-a=print("mtls")']
        result = subprocess.run(command, capture_output=True, text=True, timeout=15, env=dict(os.environ, PYTHONPATH=str(ROOT)))
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        self.assertIn('mtls', result.stdout + result.stderr)

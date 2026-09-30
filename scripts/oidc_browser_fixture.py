#!/usr/bin/env python3
"""Disposable HTTPS daemon and fake signing IdP for real-browser acceptance."""
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch
import requests
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tests'))
from web_fixture import WebDaemon
from tls_fixture import certificates
from oidc_fixture import IdentityProvider

daemon = WebDaemon()
provider = None
try:
    transport = certificates(daemon.path)
    provider = IdentityProvider(daemon.path)
    provider.uri = provider.uri.replace('127.0.0.1', 'localhost')
    daemon.uri = daemon.uri.replace('http:', 'https:')
    provider.redirect_uri = daemon.uri + '/oidc/callback'
    path = daemon.path / 'auton.yml'
    conf = yaml.safe_load(path.read_text())
    conf['general'].update(web_origin=daemon.uri,
        tls=dict(certificate=str(daemon.path / 'server.pem'), private_key=str(daemon.path / 'server.key'), client_ca=transport['verify']),
        oidc=dict(issuer=provider.uri, authorization_endpoint=provider.uri + '/authorize', token_endpoint=provider.uri + '/token',
                  client_id='auton-browser-test', jwks_file=str(provider.jwks), subjects={'subject-1': 'reader'}))
    path.write_text(yaml.safe_dump(conf))
    original = requests.get
    with patch.dict(os.environ, REQUESTS_CA_BUNDLE=transport['verify']):
        with patch('web_fixture.requests.get', side_effect=lambda *a, **kw: original(*a, **kw, **transport)):
            daemon.start()
    print(json.dumps(dict(uri=daemon.uri, cert=transport['cert'][0], key=transport['cert'][1])), flush=True)
    sys.stdin.read()
finally:
    daemon.close()
    if provider is not None:
        provider.close()

"""Explicit client TLS settings, independent of credentials and interfaces."""
import os
from urllib.parse import urlsplit

TRANSPORT_FIELDS = frozenset(('verify', 'cert'))


def validate_transport(transport, origins):
    if transport is None:
        return {}
    if not isinstance(transport, dict) or set(transport) - TRANSPORT_FIELDS:
        raise ValueError('invalid client TLS settings')
    verify = transport.get('verify', True)
    cert = transport.get('cert')
    if verify is not True and (not isinstance(verify, str) or not verify):
        raise ValueError('server certificate verification cannot be disabled')
    if cert is not None and (not isinstance(cert, (list, tuple)) or len(cert) != 2
                            or any(not isinstance(path, str) or not path for path in cert)):
        raise ValueError('client TLS requires certificate and key paths')
    if transport and any(urlsplit(origin).scheme != 'https' for origin in origins):
        raise ValueError('explicit TLS settings require HTTPS origins')
    return dict(transport)


def transport_from_options(options):
    result = {}
    if bool(options.client_cert) != bool(options.client_key):
        raise ValueError('client-cert and client-key must be provided together')
    if options.ca_file:
        result['verify'] = os.path.abspath(options.ca_file)
    if options.client_cert:
        result['cert'] = (os.path.abspath(options.client_cert), os.path.abspath(options.client_key))
    return result

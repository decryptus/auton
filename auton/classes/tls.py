"""Explicit native mTLS transport; application authentication remains mandatory."""
import os
import ssl

from auton.classes.exceptions import AutonConfigurationError
from sonicprobe.libs.threading_tcp_server import KillableThreadingHTTPServer

TLS_FIELDS = frozenset(('certificate', 'private_key', 'client_ca'))
TLS_HANDSHAKE_TIMEOUT = 5


def tls_config(general, directory=None):
    if 'tls' not in general:
        return None
    settings = general['tls']
    if (general.get('auth_mode') != 'required' or not isinstance(settings, dict)
            or set(settings) != TLS_FIELDS):
        raise AutonConfigurationError('tls requires certificate, private_key, client_ca and auth_mode: required')
    result = {}
    for name, value in settings.items():
        if not isinstance(value, str) or not value or '\x00' in value:
            raise AutonConfigurationError('invalid TLS file path')
        if not os.path.isabs(value):
            if not directory:
                raise AutonConfigurationError('relative TLS paths require a configuration directory')
            value = os.path.join(directory, value)
        result[name] = os.path.abspath(value)
    return result


def tls_server_class(settings, base=KillableThreadingHTTPServer):
    try:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.load_cert_chain(settings['certificate'], settings['private_key'])
        context.load_verify_locations(cafile=settings['client_ca'])
        context.verify_mode = ssl.CERT_REQUIRED
    except (OSError, ssl.SSLError, ValueError):
        raise AutonConfigurationError('cannot initialize mutual TLS credentials') from None

    class MutualTLSServer(base):
        def get_request(self):
            connection, address = super().get_request()
            try:
                connection.settimeout(TLS_HANDSHAKE_TIMEOUT)
                secured = context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False)
                return secured, address
            except BaseException:
                connection.close()
                raise
        def finish_request(self, request, address):
            # Handshakes consume a bounded worker, never the listener thread.
            request.do_handshake()
            request.settimeout(None)
            return super().finish_request(request, address)
    return MutualTLSServer

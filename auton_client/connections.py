"""Explicit named daemon connection parsing shared by client interfaces."""

import re
import ipaddress
from urllib.parse import urlsplit, urlunsplit

from urllib3.util import parse_url

MAX_CONNECTION_NAME = 32
CONNECTION_NAME_PATTERN = re.compile(r'[a-z0-9-]+')
HOST_LABEL_PATTERN = re.compile(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', re.IGNORECASE)
MAX_ORIGIN_LENGTH = 2048
MAX_HOST_LENGTH = 253
MAX_PORT = 65535
HTTP_SCHEMES = ('http', 'https')


def validate_connection_name(name):
    if (not isinstance(name, str) or len(name) > MAX_CONNECTION_NAME
            or CONNECTION_NAME_PATTERN.fullmatch(name) is None):
        raise ValueError('connection name must match [a-z0-9-]+ (1-32 characters)')
    return name


def named_connections(specs):
    """Parse explicit named connections, shared by execution and visibility."""
    result = {}
    for spec in specs:
        name, separator, uri = spec.partition('=')
        if not separator or not uri:
            raise ValueError('connection must use NAME=URI')
        validate_connection_name(name)
        if name in result:
            raise ValueError('duplicate connection name: ' + name)
        result[name] = uri
    return result



def normalize_origin(uri):
    """Validate the entire HTTP(S) origin before any connection is attempted."""
    try:
        if (not isinstance(uri, str) or not uri or len(uri) > MAX_ORIGIN_LENGTH
                or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in uri)
                or any(char in uri for char in ('\\', '?', '#'))):
            raise ValueError()
        parsed = urlsplit(uri)
        if (parsed.scheme not in HTTP_SCHEMES or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.path not in ('', '/') or parsed.netloc.endswith(':')
                or (parsed.port is not None and not 1 <= parsed.port <= MAX_PORT)):
            raise ValueError()
        # Use the same IDNA normalization as the requests/urllib3 transport.
        host = parse_url(uri).host
        if host.startswith('['):
            host = '[' + ipaddress.IPv6Address(host[1:-1]).compressed + ']'
        else:
            checked_host = host[:-1] if host.endswith('.') else host
            if (len(checked_host) > MAX_HOST_LENGTH or not all(
                    HOST_LABEL_PATTERN.fullmatch(label) for label in checked_host.split('.'))):
                raise ValueError()
            if all(char in '0123456789.' for char in checked_host) and '.' in checked_host:
                ipaddress.IPv4Address(checked_host)
        authority = host + (':' + str(parsed.port) if parsed.port is not None else '')
        return urlunsplit((parsed.scheme, authority, '', '', ''))
    except (ValueError, TypeError, AttributeError):
        raise ValueError('daemon URI must be a valid HTTP(S) origin: host and optional port, without credentials, path, query or fragment') from None

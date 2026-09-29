"""Explicit named daemon connection parsing shared by client interfaces."""

import re

MAX_CONNECTION_NAME = 32
CONNECTION_NAME_PATTERN = re.compile(r'[a-z0-9-]+')


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


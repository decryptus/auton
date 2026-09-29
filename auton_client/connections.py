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
MAX_TARGET_ORIGINS = 16
MAX_TARGET_REFERENCE_DEPTH = 16
TARGET_FIELDS = frozenset(('uris',))
TARGET_REFERENCE_FIELDS = frozenset(('target',))


def validate_connection_name(name):
    if (not isinstance(name, str) or len(name) > MAX_CONNECTION_NAME
            or CONNECTION_NAME_PATTERN.fullmatch(name) is None):
        raise ValueError('connection name must match [a-z0-9-]+ (1-32 characters)')
    return name


def named_connections(specs, configured=None):
    """Parse explicit named connections, shared by execution and visibility."""
    configured = {} if configured is None else configured
    result = {}
    for spec in specs:
        name, separator, uri = spec.partition('=')
        validate_connection_name(name)
        if not separator:
            if name not in configured:
                raise ValueError('unknown connection name; declare it in --config or use NAME=URI')
            uri = configured[name]
        elif name in configured:
            raise ValueError('inline connection shadows a configured name; select its name or use another alias')
        if not uri:
            raise ValueError('connection must use NAME=URI')
        if name in result:
            raise ValueError('duplicate connection name: ' + name)
        result[name] = normalize_target(uri)
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


def select_connections(specs, group_names=(), configured=None, groups=None):
    """Resolve exact/glob/~regex selections as a stable union without requests."""
    from auton_client.selectors import NameSelector, is_pattern, MAX_SELECTORS
    configured = {} if configured is None else configured
    groups = {} if groups is None else groups
    if len(specs) + len(group_names) > MAX_SELECTORS:
        raise ValueError('too many selectors')
    selector = NameSelector()
    result = {}
    explicit = set()
    for spec in specs:
        if not isinstance(spec, str):
            raise ValueError('selectors must be strings')
        if not spec.startswith('~') and '=' in spec:
            connections = named_connections([spec], configured)
            names = list(connections)
            exact = True
        elif not is_pattern(spec):
            connections = named_connections([spec], configured)
            names = list(connections)
            exact = True
        else:
            names = selector.select(spec, configured)
            connections = {name: normalize_target(configured[name]) for name in names}
            exact = False
        for name in names:
            if exact and name in explicit:
                raise ValueError('duplicate connection name: ' + name)
            if exact:
                explicit.add(name)
            result.setdefault(name, connections[name])
    for pattern in group_names:
        for name in selector.select(pattern, groups):
            for member in groups[name]:
                validate_connection_name(member)
                if member not in configured:
                    raise ValueError('group references an unknown target: ' + member)
                uri = normalize_target(configured[member])
                if member in result and result[member] != uri:
                    raise ValueError('conflicting target selection: ' + member)
                result.setdefault(member, uri)
    return result


def origin_key(uri):
    parsed = urlsplit(normalize_origin(uri))
    return parsed.scheme, parsed.hostname.lower().rstrip('.'), parsed.port or (443 if parsed.scheme == 'https' else 80)


def target_origins(value):
    if isinstance(value, str):
        return [normalize_origin(value)]
    if not isinstance(value, dict) or set(value) != TARGET_FIELDS:
        raise ValueError('target must be an origin or a mapping with uris')
    entries = value['uris']
    if not isinstance(entries, list) or not 1 <= len(entries) <= MAX_TARGET_ORIGINS:
        raise ValueError('target uris must contain 1-16 origins')
    origins, seen = [], set()
    for uri in entries:
        origin = normalize_origin(uri)
        key = origin_key(origin)
        if key not in seen:
            origins.append(origin)
            seen.add(key)
    return origins


def normalize_target(value):
    origins = target_origins(value)
    return origins[0] if isinstance(value, str) else {'uris': origins}


def resolve_targets(entries):
    """Expand explicit target references offline; groups are never replacement chains."""
    resolved = {}
    depths = {}
    def resolve(name, path):
        validate_connection_name(name)
        if name in path or len(path) >= MAX_TARGET_REFERENCE_DEPTH:
            raise ValueError('cyclic or excessively deep target reference')
        if name not in entries:
            raise ValueError('unknown target reference: ' + name)
        if name in resolved:
            if depths[name] + len(path) >= MAX_TARGET_REFERENCE_DEPTH:
                raise ValueError('excessively deep target reference')
            return resolved[name]
        value = entries[name]
        if isinstance(value, str):
            resolved[name] = normalize_origin(value)
            depths[name] = 0
        else:
            if not isinstance(value, dict) or set(value) != TARGET_FIELDS:
                raise ValueError('target mappings require only uris; group references are not supported')
            members = value['uris']
            if not isinstance(members, list) or not 1 <= len(members) <= MAX_TARGET_ORIGINS:
                raise ValueError('target uris must contain 1-16 origins or target references')
            origins, seen = [], set()
            depth = 0
            for member in members:
                if isinstance(member, str):
                    additions = [normalize_origin(member)]
                elif isinstance(member, dict) and set(member) == TARGET_REFERENCE_FIELDS:
                    additions = target_origins(resolve(member['target'], path + (name,)))
                    depth = max(depth, depths[member['target']] + 1)
                else:
                    raise ValueError('uris entries must be origins or {target: name} references')
                for origin in additions:
                    key = origin_key(origin)
                    if key not in seen:
                        origins.append(origin)
                        seen.add(key)
            resolved[name] = normalize_target({'uris': origins})
            depths[name] = depth
        return resolved[name]
    return {name: resolve(name, ()) for name in entries}

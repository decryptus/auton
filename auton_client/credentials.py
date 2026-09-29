"""Explicit bearer credentials usable by all Requests-based Auton clients."""
import ipaddress
import os
import re
import stat
from urllib.parse import urlsplit
from pathlib import Path

from auton_client.connections import normalize_origin, origin_key, target_origins, validate_connection_name

from requests.auth import AuthBase

TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}')
MAX_TOKEN_FILE_BYTES = 256
PROFILE_FIELDS = frozenset(('origins', 'token_file'))
MAX_CREDENTIAL_PROFILES = 128
MAX_PROFILE_ORIGINS = 16


class BearerCredentials(AuthBase):
    def __init__(self, token):
        if not isinstance(token, str) or not TOKEN_PATTERN.fullmatch(token):
            raise ValueError('invalid bearer token')
        self._token = token

    def __repr__(self):
        return 'BearerCredentials(<redacted>)'

    def __call__(self, request):
        parsed = urlsplit(request.url)
        local = False
        try:
            local = ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:
            pass
        if (parsed.username is not None or parsed.password is not None
                or parsed.scheme not in ('http', 'https')
                or (parsed.scheme == 'http' and not local)):
            raise ValueError('bearer authentication requires HTTPS or an explicit loopback address')
        request.headers['Authorization'] = 'Bearer ' + self._token
        return request

    @classmethod
    def from_file(cls, filename):
        descriptor = None
        try:
            descriptor = os.open(filename, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
            info = os.fstat(descriptor)
            if (not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077
                    or info.st_uid != os.geteuid() or info.st_nlink != 1):
                raise ValueError()
            with os.fdopen(descriptor, 'rb') as stream:
                descriptor = None
                data = stream.read(MAX_TOKEN_FILE_BYTES + 1)
            if len(data) > MAX_TOKEN_FILE_BYTES:
                raise ValueError()
            return cls(data.decode('ascii').strip())
        except (OSError, ValueError, UnicodeError):
            raise ValueError('token file must be a private regular file owned by the current user and contain one valid token') from None
        finally:
            if descriptor is not None:
                os.close(descriptor)


def credential_origin(uri):
    origin = normalize_origin(uri)
    parsed = urlsplit(origin)
    if parsed.scheme == 'http':
        try:
            local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            local = False
        if not local:
            raise ValueError('bearer authentication requires HTTPS or an explicit loopback address')
    return origin_key(origin)


def validate_profiles(profiles, directories):
    """Validate public profile declarations without reading any token files."""
    if not isinstance(profiles, dict) or not 1 <= len(profiles) <= MAX_CREDENTIAL_PROFILES:
        raise ValueError('credentials must contain 1-128 named profiles')
    result, seen = {}, set()
    for name, profile in profiles.items():
        validate_connection_name(name)
        if not isinstance(profile, dict) or set(profile) != PROFILE_FIELDS:
            raise ValueError('credential profile requires only origins and token_file')
        origins = profile['origins']
        if not isinstance(origins, list) or not 1 <= len(origins) <= MAX_PROFILE_ORIGINS:
            raise ValueError('credential profile requires 1-16 explicit origins')
        for uri in origins:
            key = credential_origin(uri)
            if key in seen:
                raise ValueError('duplicate credential origin')
            seen.add(key)
        filename = profile['token_file']
        if not isinstance(filename, str) or not filename or '\x00' in filename or '://' in filename:
            raise ValueError('credential token_file must be a local filename')
        # Do not resolve the file itself: the private-file reader rejects symlinks.
        filename = str(Path(directories[name]) / filename)
        result[name] = {'origins': list(origins), 'token_file': filename}
    return result


class OriginCredentials(AuthBase):
    """A closed origin-to-token map; never falls back to another credential."""
    def __init__(self, bindings):
        self._bindings = dict(bindings)

    def __repr__(self):
        return 'OriginCredentials(<redacted>)'

    def restricted(self, origins):
        keys = {credential_origin(uri) for uri in origins}
        if not keys <= self._bindings.keys():
            raise ValueError('missing credentials for a selected daemon origin')
        return OriginCredentials({key: self._bindings[key] for key in keys})

    def __call__(self, request):
        parsed = urlsplit(request.url)
        key = credential_origin(parsed.scheme + '://' + parsed.netloc)
        credential = self._bindings.get(key)
        if credential is None:
            raise ValueError('no credentials bound to the requested origin')
        return credential(request)


def bind_credentials(auth, origins):
    """Bind bearer auth before any I/O; keep explicit legacy Basic unchanged."""
    origins = list(origins)
    if isinstance(auth, OriginCredentials):
        return auth.restricted(origins)
    if isinstance(auth, BearerCredentials):
        return OriginCredentials({credential_origin(uri): auth for uri in origins})
    return auth


def credentials_from_options(options):
    filename = getattr(options, 'token_file', None)
    user = getattr(options, 'auth_user', None)
    password = getattr(options, 'auth_passwd', None)
    profiles = getattr(options, 'configured_credentials', {})
    if profiles and (filename or user or password):
        raise ValueError('credential profiles and token-file/Basic credentials are mutually exclusive')
    if profiles:
        selected = getattr(options, 'selected_targets', None) or {}
        origins = [uri for target in selected.values() for uri in target_origins(target)]
        needed = {credential_origin(uri) for uri in origins}
        declarations = {credential_origin(uri): profile for profile in profiles.values() for uri in profile['origins']}
        if not needed or not needed <= declarations.keys():
            raise ValueError('missing credentials for a selected daemon origin')
        bindings, loaded = {}, {}
        for key in needed:
            path = declarations[key]['token_file']
            if path not in loaded:
                loaded[path] = BearerCredentials.from_file(path)
            bindings[key] = loaded[path]
        return OriginCredentials(bindings)
    if filename:
        if user or password:
            raise ValueError('token-file and Basic credentials are mutually exclusive')
        return BearerCredentials.from_file(filename)
    return (user, password or '') if user else None

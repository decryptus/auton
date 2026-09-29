"""Explicit bearer credentials usable by all Requests-based Auton clients."""
import ipaddress
import os
import re
import stat
from urllib.parse import urlsplit

from requests.auth import AuthBase

TOKEN_PATTERN = re.compile(r'[A-Za-z0-9_-]{43}')
MAX_TOKEN_FILE_BYTES = 256


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


def credentials_from_options(options):
    filename = getattr(options, 'token_file', None)
    user = getattr(options, 'auth_user', None)
    password = getattr(options, 'auth_passwd', None)
    if filename:
        if user or password:
            raise ValueError('token-file and Basic credentials are mutually exclusive')
        return BearerCredentials.from_file(filename)
    return (user, password or '') if user else None

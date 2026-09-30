"""Local credential verification and explicit daemon authentication policy."""
import base64
import binascii
try:
    import crypt
except ImportError:
    import legacycrypt as crypt
import hashlib
import hmac
import ipaddress
import logging
from pathlib import Path
from types import MappingProxyType

from auton.classes.exceptions import AutonConfigurationError

AUTH_MODES = frozenset(('required', 'anonymous', 'legacy'))
MAX_PASSWORD_FILE_BYTES = 1048576
MAX_AUTHORIZATION_BYTES = 8192
LOG = logging.getLogger(__name__)


def apply_auth_policy(conf):
    from auton.classes.auth_store import authentication_config
    general = conf['general']
    settings = authentication_config(general, conf.get('_config_directory'))
    if settings is not None:
        general['authentication'] = settings
    mode = general.get('auth_mode', 'legacy')
    if not isinstance(mode, str) or mode not in AUTH_MODES:
        raise AutonConfigurationError('auth_mode must be required, anonymous or legacy')
    if mode == 'legacy':
        LOG.warning('Legacy route-based authentication: review every route; migrate to auth_mode: required before 1.0')
        return
    if mode == 'anonymous':
        try:
            local = ipaddress.ip_address(general.get('listen_addr', '')).is_loopback
        except ValueError:
            local = False
        if not local or general.get('auth_basic_file'):
            raise AutonConfigurationError('anonymous mode requires a loopback listen_addr and no auth_basic_file')
        LOG.warning('Explicit anonymous local mode: local callers share access and job ownership')
        return
    if not general.get('auth_basic_file') and settings is None:
        raise AutonConfigurationError('required authentication needs auth_basic_file; use anonymous only for local development')
    # Preserve explicit route user allowlists while protecting every declared route.
    for module in conf.get('modules', {}).values():
        for route in module.get('routes', {}).values():
            if not isinstance(route.get('auth'), (list, tuple)):
                route['auth'] = True


class PasswordAuthenticator:
    """Immutable credential snapshot; request identity is returned, never retained."""
    def __init__(self, users=None):
        self.users = MappingProxyType(dict(users or {}))

    @classmethod
    def from_file(cls, filename=None, required=False):
        if not filename:
            if required:
                raise AutonConfigurationError('authentication password file is required')
            return cls()
        try:
            with Path(filename).open('rb') as stream:
                data = stream.read(MAX_PASSWORD_FILE_BYTES + 1)
            if len(data) > MAX_PASSWORD_FILE_BYTES:
                raise ValueError()
            users = {}
            for line in data.decode('utf-8').splitlines():
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                user, secret = line.split(':', 1)
                if not user or not secret or user in users or any(ord(c) < 32 for c in user + secret):
                    raise ValueError()
                users[user] = secret
            if required and not users:
                raise ValueError()
            return cls(users)
        except (OSError, ValueError, UnicodeError):
            raise AutonConfigurationError('cannot load authentication file (missing, malformed, duplicate users, empty or too large)') from None

    def authenticate(self, authorization, allowed_users=None):
        if not isinstance(authorization, str) or len(authorization) > MAX_AUTHORIZATION_BYTES:
            return None
        try:
            scheme, encoded = authorization.split(' ', 1)
            if scheme.lower() != 'basic':
                return None
            user, password = base64.b64decode(encoded, validate=True).decode('utf-8').split(':', 1)
            if '\x00' in password:
                return None
            secret = self.users.get(user)
            if not secret:
                return None
            if secret.startswith('{SHA}'):
                candidate = '{SHA}' + base64.b64encode(hashlib.sha1(password.encode('utf-8')).digest()).decode('ascii')
            else:
                candidate = crypt.crypt(password, secret)
            if not candidate or not hmac.compare_digest(candidate.encode('utf-8'), secret.encode('utf-8')):
                return None
            if allowed_users and user not in allowed_users:
                return None
            return user
        except (ValueError, UnicodeError, binascii.Error, OSError):
            return None

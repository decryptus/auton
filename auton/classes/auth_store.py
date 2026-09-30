"""Daemon-owned configuration and lifecycle for HTTPdis authentication services."""
import math
import os
from pathlib import Path

from auton.classes.exceptions import AutonConfigurationError

AUTH_FIELDS = frozenset(('backend', 'path', 'timeout'))
AUTH_SCOPES = frozenset(('read', 'run', 'maintenance', 'cancel'))
DEFAULT_AUTH_TIMEOUT = 5


def authentication_config(general, directory=None):
    if 'authentication' not in general:
        return None
    settings = general['authentication']
    if (not isinstance(settings, dict) or set(settings) - AUTH_FIELDS
            or settings.get('backend') != 'sqlite'):
        raise AutonConfigurationError('authentication requires backend: sqlite and only path/timeout options')
    path = settings.get('path')
    if not isinstance(path, str) or not path or '\x00' in path or path == ':memory:' or path.startswith('file:'):
        raise AutonConfigurationError('authentication.path must name a local database file')
    if not os.path.isabs(path):
        if not directory:
            raise AutonConfigurationError('relative authentication.path requires a configuration file directory')
        path = os.path.join(directory, path)
    timeout = settings.get('timeout', DEFAULT_AUTH_TIMEOUT)
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout) or not 0 < timeout <= 30):
        raise AutonConfigurationError('authentication.timeout must be > 0 and <= 30 seconds')
    if general.get('auth_mode') != 'required' or general.get('auth_basic_file'):
        raise AutonConfigurationError('SQLite authentication requires auth_mode: required and no auth_basic_file')
    if general.get('auth_provider') is not None:
        raise AutonConfigurationError('auth_provider is supplied by the daemon, not YAML')
    return dict(backend='sqlite', path=os.path.abspath(path), timeout=timeout)


def validate_scopes(scopes):
    if isinstance(scopes, str):
        raise ValueError('scopes must be a collection')
    scopes = frozenset(scopes)
    if not scopes or not scopes <= AUTH_SCOPES:
        raise ValueError('scopes must contain only read, run, maintenance or cancel')
    return scopes


def authorized_principal(principal, identity, scope):
    """Transport-independent action policy, in addition to endpoint/owner ACLs."""
    from auton.classes.jobs import AccessDenied
    if identity is not None:
        if scope not in identity.scopes or identity.principal != principal:
            raise AccessDenied('token scope does not permit this action')
    return principal


class PersistentAuthentication:
    """Compose existing services after privilege drop and fork, with no default path."""
    def __init__(self, settings, passwords=None, audit=None):
        from httpdis.auth_backend import Argon2Passwords, BearerAuthProvider, LocalAuthService
        from httpdis.auth_sqlite import SQLiteAuthStore
        # Resolve hashing support before opening/creating a database.
        passwords = passwords or Argon2Passwords()
        self.store = SQLiteAuthStore(settings['path'], timeout=settings['timeout'])
        try:
            self.service = LocalAuthService(self.store, passwords, audit=audit)
            self.provider = BearerAuthProvider(self.service)
        except BaseException:
            self.store.close()
            raise

    def account_identity(self, principal):
        from httpdis.authentication import AuthenticationDenied
        with self.store.transaction() as tx:
            account = tx.get('accounts', principal)
            if not account or not account['enabled']:
                raise AuthenticationDenied()
            return account['revision'], frozenset(account['scopes'])

    def close(self):
        self.store.close()

    def provision(self, principal, password, scopes):
        self.service.provision(principal, password, validate_scopes(scopes))

    def disable(self, principal):
        self.service.disable(principal)

    def issue_token(self, principal, scopes, ttl):
        return self.service.issue_token(principal, validate_scopes(scopes), ttl)

    def revoke_token(self, credential_id):
        self.service.revoke_token(credential_id)

    def list_accounts(self):
        with self.store.transaction() as tx:
            return [dict(principal=name, enabled=record['enabled'], scopes=sorted(record['scopes']))
                    for name, record in tx.items('accounts')]


def load_authentication_config(filename):
    """Read only auth settings; never initialize modules, endpoints or commands."""
    from sonicprobe.helpers import load_yaml
    path = Path(filename)
    with path.open() as stream:
        conf = load_yaml(stream)
    if not isinstance(conf, dict) or not isinstance(conf.get('general'), dict):
        raise AutonConfigurationError('configuration requires a general mapping')
    settings = authentication_config(conf['general'], str(path.absolute().parent))
    if settings is None:
        raise AutonConfigurationError('no SQLite authentication configured')
    return settings

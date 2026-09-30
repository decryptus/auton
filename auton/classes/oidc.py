"""Opt-in OIDC code flow and bounded sessions, independent of HTTP handlers.

Issuer endpoints and public signing keys are trusted operator configuration.
There is no discovery from tokens, implicit flow, refresh token or role import.
"""
import base64
import hashlib
import hmac
import json
import math
import os
from pathlib import Path
import secrets
import threading
import time
from urllib.parse import urlencode, urlsplit

from httpdis.authentication import AuthenticationDenied, AuthenticationUnavailable, Identity
from httpdis.auth_backend import SessionGrant
from auton.classes.exceptions import AutonConfigurationError

OIDC_FIELDS = frozenset(('issuer', 'authorization_endpoint', 'token_endpoint', 'client_id',
                         'client_secret_file', 'jwks_file', 'subjects'))
OIDC_REQUIRED = OIDC_FIELDS - {'client_secret_file'}
MAX_DOCUMENT_BYTES = 65536
MAX_TOKEN_BYTES = 16384
MAX_PENDING = 128
MAX_SESSIONS = 1024
FLOW_TTL = 300
SESSION_TTL = 3600
IDLE_TTL = 900
REQUIRED_CLAIMS = ('iss', 'aud', 'sub', 'exp', 'iat', 'nonce')


def _text(value, limit=2048):
    return isinstance(value, str) and 0 < len(value) <= limit and not any(ord(c) < 32 or ord(c) == 127 for c in value)


def _url(value):
    if not _text(value) or any(c.isspace() for c in value):
        raise ValueError()
    parsed = urlsplit(value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query:
        raise ValueError()
    if parsed.port is not None and not 1 <= parsed.port <= 65535:
        raise ValueError()
    return value


def oidc_config(general, directory=None):
    if 'oidc' not in general:
        return None
    conf = general['oidc']
    try:
        if (not general.get('web_enabled') or general.get('auth_mode') != 'required'
                or not general.get('authentication') or not general.get('web_origin', '').startswith('https://')
                or not isinstance(conf, dict) or set(conf) - OIDC_FIELDS or not OIDC_REQUIRED <= set(conf)):
            raise ValueError()
        result = dict(conf)
        for name in ('issuer', 'authorization_endpoint', 'token_endpoint'):
            result[name] = _url(conf[name])
        if not _text(conf['client_id'], 256):
            raise ValueError()
        subjects = conf['subjects']
        if (not isinstance(subjects, dict) or not 0 < len(subjects) <= MAX_SESSIONS
                or any(not _text(subject, 256) or not _text(principal, 256) for subject, principal in subjects.items())):
            raise ValueError()
        result['subjects'] = dict(subjects)
        for name in ('jwks_file', 'client_secret_file'):
            if name not in conf:
                continue
            path = conf[name]
            if not _text(path) or '\x00' in path or (not os.path.isabs(path) and not directory):
                raise ValueError()
            result[name] = os.path.abspath(os.path.join(directory or '', path))
        return result
    except (ValueError, TypeError):
        raise AutonConfigurationError('invalid OIDC settings: explicit HTTPS endpoints, client, keys, subject mapping and authenticated HTTPS web console required') from None


def _digest(secret):
    return hashlib.sha256(secret.encode('ascii')).hexdigest()


def _document(path):
    with Path(path).open('rb') as stream:
        data = stream.read(MAX_DOCUMENT_BYTES + 1)
    if len(data) > MAX_DOCUMENT_BYTES:
        raise ValueError()
    return data


class OIDCService:
    """Compose local authentication with externally verified browser identities.

    account_lookup returns (revision, scopes) for an enabled local account. Every
    request rechecks it; issuer claims never provision accounts or grant scopes.
    """
    def __init__(self, settings, origin, local, account_lookup, clock=time.time, exchange=None):
        try:
            import jwt
            self.jwt = jwt
            keys = json.loads(_document(settings['jwks_file']))['keys']
            if not isinstance(keys, list) or not 1 <= len(keys) <= 16:
                raise ValueError()
            self.keys = {}
            for key in keys:
                if (not isinstance(key, dict) or not _text(key.get('kid'), 256) or key['kid'] in self.keys
                        or key.get('kty') != 'RSA' or key.get('use', 'sig') != 'sig'
                        or key.get('alg', 'RS256') != 'RS256' or 'd' in key
                        or key.get('key_ops', ['verify']) != ['verify']):
                    raise ValueError()
                parsed = jwt.PyJWK.from_dict(key, algorithm='RS256').key
                if parsed.key_size < 2048:
                    raise ValueError()
                self.keys[key['kid']] = parsed
            self.secret = None
            if settings.get('client_secret_file'):
                path = Path(settings['client_secret_file'])
                if path.stat().st_mode & 0o077:
                    raise ValueError()
                self.secret = _document(path).decode('utf-8').strip()
                if not _text(self.secret, 4096):
                    raise ValueError()
        except (ImportError, OSError, ValueError, KeyError, TypeError):
            raise AutonConfigurationError('cannot initialize OIDC: install autond[oidc], check public RSA keys and private client-secret file') from None
        self.settings = settings
        self.redirect_uri = origin + '/oidc/callback'
        self.local, self.account_lookup, self.clock = local, account_lookup, clock
        if exchange is None:
            from auton.classes.oidc_transport import OIDCTokenExchange
            exchange = OIDCTokenExchange(settings['token_endpoint'], settings['client_id'], self.secret, self.redirect_uri)
        self.exchange = exchange
        self.session_ttl = local.session_ttl
        self.exchange_slots = threading.BoundedSemaphore(4)
        self.lock = threading.RLock()
        self.pending, self.sessions = {}, {}

    def _purge(self):
        now = self.clock()
        for collection in (self.pending, self.sessions):
            for key, value in list(collection.items()):
                if min(value['expires'], value.get('idle', value['expires'])) <= now:
                    del collection[key]

    def begin(self):
        with self.lock:
            self._purge()
            if len(self.pending) >= MAX_PENDING:
                raise AuthenticationUnavailable()
            state, binding, nonce, verifier = [secrets.token_urlsafe(32) for _ in range(4)]
            self.pending[_digest(state)] = dict(binding=_digest(binding), nonce=nonce,
                verifier=verifier, expires=self.clock() + FLOW_TTL)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).rstrip(b'=').decode('ascii')
        params = dict(response_type='code', client_id=self.settings['client_id'], redirect_uri=self.redirect_uri,
                      scope='openid', state=state, nonce=nonce, code_challenge=challenge, code_challenge_method='S256')
        return self.settings['authorization_endpoint'] + '?' + urlencode(params), binding

    def finish(self, state, binding, code):
        if not all(_text(value, limit) for value, limit in ((state, 43), (binding, 43), (code, 2048))):
            raise AuthenticationDenied()
        try:
            with self.lock:
                self._purge()
                pending = self.pending.get(_digest(state))
                if pending is None or not hmac.compare_digest(pending['binding'], _digest(binding)):
                    raise AuthenticationDenied()
                del self.pending[_digest(state)]  # one attempt, including provider failure
            if not self.exchange_slots.acquire(False):
                raise AuthenticationUnavailable()
            try:
                token = self.exchange(code, pending['verifier'])
            finally:
                self.exchange_slots.release()
            if not _text(token, MAX_TOKEN_BYTES):
                raise AuthenticationDenied()
            header = self.jwt.get_unverified_header(token)
            if header.get('alg') != 'RS256' or header.get('crit') or header.get('kid') not in self.keys:
                raise AuthenticationDenied()
            claims = self.jwt.decode(token, self.keys[header['kid']], algorithms=['RS256'],
                audience=self.settings['client_id'], issuer=self.settings['issuer'],
                options={'require': list(REQUIRED_CLAIMS)})
            if (not isinstance(claims['nonce'], str) or not hmac.compare_digest(claims['nonce'], pending['nonce'])
                    or claims.get('azp', self.settings['client_id']) != self.settings['client_id']
                    or (isinstance(claims['aud'], list) and len(claims['aud']) > 1 and 'azp' not in claims)
                    or type(claims['exp']) not in (int, float) or type(claims['iat']) not in (int, float)
                    or not math.isfinite(claims['exp']) or not math.isfinite(claims['iat'])
                    or claims['exp'] <= self.clock() or claims['iat'] > self.clock() or claims['iat'] < self.clock() - FLOW_TTL):
                raise AuthenticationDenied()
            principal = self.settings['subjects'].get(claims['sub'])
            if principal is None:
                raise AuthenticationDenied()
            revision, scopes = self.account_lookup(principal)
            now, secret, csrf = self.clock(), secrets.token_urlsafe(32), secrets.token_urlsafe(32)
            expires = min(now + SESSION_TTL, claims['exp'])
            with self.lock:
                self._purge()
                if len(self.sessions) >= MAX_SESSIONS:
                    raise AuthenticationUnavailable()
                self.sessions[_digest(secret)] = dict(principal=principal, revision=revision,
                    scopes=frozenset(scopes), csrf=csrf, expires=expires, idle=min(now + IDLE_TTL, expires))
            return SessionGrant(secret, csrf, expires, Identity(principal, 'session', scopes))
        except (ValueError, TypeError, KeyError, UnicodeError, self.jwt.PyJWTError):
            raise AuthenticationDenied() from None

    def authenticate_session(self, secret, csrf=None, mutation=False):
        with self.lock:
            self._purge()
            record = self.sessions.get(_digest(secret))
            if record is None:
                return self.local.authenticate_session(secret, csrf, mutation)
            revision, scopes = self.account_lookup(record['principal'])
            if revision != record['revision'] or (mutation and (not isinstance(csrf, str) or not hmac.compare_digest(csrf, record['csrf']))):
                raise AuthenticationDenied()
            record['idle'] = min(self.clock() + IDLE_TTL, record['expires'])
            return Identity(record['principal'], 'session', set(scopes).intersection(record['scopes']))

    def csrf_for(self, secret):
        with self.lock:
            record = self.sessions.get(_digest(secret))
            return record['csrf'] if record else None

    def logout(self, secret):
        with self.lock:
            self.sessions.pop(_digest(secret), None)
        return self.local.logout(secret)

    def login(self, *args, **kwargs):
        return self.local.login(*args, **kwargs)

    def authenticate_token(self, secret):
        return self.local.authenticate_token(secret)

    def close(self):
        with self.lock:
            self.sessions.clear()
            self.pending.clear()

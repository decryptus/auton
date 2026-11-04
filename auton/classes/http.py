"""Request-local Basic authentication for the httpdis transport."""
import io
import time

from httpdis.ext.httpdis_json import HttpReqHandler, HttpReqErrJson, HttpReqError
from auton.classes.authentication import PasswordAuthenticator
from auton.classes.exceptions import AutonConfigurationError
from auton.classes.web import authentication_request, SECURITY_HEADERS, MAX_LOGIN_BYTES
from httpdis.auth_browser import BrowserAuthProvider
from httpdis.authentication import AuthenticationDenied


HEADER_READ_SECONDS = 10
BODY_GRACE_SECONDS = 10
BODY_BYTES_PER_SECOND = 32 * 1024
BODY_MAX_SECONDS = 120


class _DeadlineInput(io.RawIOBase):
    """Limit each socket read to the time left for the current request phase."""

    def __init__(self, raw, connection):
        super().__init__()
        self.raw = raw
        self.connection = connection
        self.deadline = None

    def readable(self):
        return True

    def readinto(self, buffer):
        if self.deadline is None:
            return self.raw.readinto(buffer)
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('request read deadline exceeded')
        previous = self.connection.gettimeout()
        self.connection.settimeout(remaining if previous is None else min(remaining, previous))
        try:
            return self.raw.readinto(buffer)
        finally:
            self.connection.settimeout(previous)

    def close(self):
        try:
            self.raw.close()
        finally:
            super().close()


class _RequestReader(io.BufferedReader):
    """HTTPdis reads an admitted body with read(length), headers with readline()."""

    def read(self, size=-1):
        if size <= 0:
            return super().read(size)
        budget = min(BODY_MAX_SECONDS, BODY_GRACE_SECONDS + size / BODY_BYTES_PER_SECOND)
        self.raw.deadline = time.monotonic() + budget
        try:
            return super().read(size)
        except TimeoutError:
            raise HttpReqErrJson(408, 'Request body read deadline exceeded') from None
        finally:
            self.raw.deadline = None


AUDIT_STATUS_EVENTS = {401: 'auth.denied', 403: 'auth.forbidden'}
BROWSER_AUTH_PATHS = frozenset(('/ui/auth/login', '/ui/auth/logout', '/ui/auth/oidc', '/oidc/callback'))


class AutonHttpReqHandler(HttpReqHandler):
    # Immutable process-start credential snapshot, never request identity.
    authenticator = PasswordAuthenticator()
    realm = 'Restricted'
    auth_audit = None

    # Request a raw socket stream from StreamRequestHandler.setup, then buffer
    # above the deadline check so even trickled bytes within one line are bounded.
    rbufsize = 0

    def setup(self):
        super().setup()
        self._input = _DeadlineInput(self.rfile, self.connection)
        self.rfile = _RequestReader(self._input)

    def handle_one_request(self):
        self._input.deadline = time.monotonic() + HEADER_READ_SECONDS
        return super().handle_one_request()

    def parse_request(self):
        try:
            return super().parse_request()
        finally:
            self._input.deadline = None

    @classmethod
    def configure_auth(cls, filename=None, realm=None, required=False):
        realm = realm or 'Restricted'
        if not isinstance(realm, str) or any(ord(c) < 32 or c in '\\"' for c in realm):
            raise AutonConfigurationError('invalid authentication realm')
        cls.authenticator = PasswordAuthenticator.from_file(filename, required=required)
        cls.realm = realm

    def authenticate(self, auth_users=None):
        self._auth_unavailable = False
        if self.get_context().auth_provider is not None:
            try:
                return super().authenticate(auth_users)
            except HttpReqError as error:
                self._auth_unavailable = error.code == 503
                raise
        self._SERVER.pop('HTTP_AUTH_IDENTITY', None)
        self._SERVER.pop('HTTP_AUTH_USER', None)
        self._SERVER.pop('HTTP_AUTH_PASSWD', None)
        principal = self.authenticator.authenticate(self.headers.get('Authorization', ''), auth_users)
        if principal is None:
            raise HttpReqErrJson(401, 'authentication required', headers={
                'WWW-Authenticate': 'Basic realm="%s"' % self.realm})
        self._SERVER['HTTP_AUTH_USER'] = principal


    def _browser(self):
        provider = self.get_context().auth_provider
        return provider if isinstance(provider, BrowserAuthProvider) else None

    def _public_browser_policy(self, mutation=False):
        try:
            self._browser().require_browser(authentication_request(self), mutation)
        except AuthenticationDenied:
            raise HttpReqErrJson(403, 'Browser request rejected') from None

    def data_from_query(self, cmd):
        if self._browser() and (cmd == 'ui' or cmd.startswith('ui/')):
            self._public_browser_policy()
        return super().data_from_query(cmd)

    def data_from_payload(self, cmd):
        if self._browser() and cmd.startswith('ui/'):
            self._public_browser_policy(mutation=True)
            lengths = self.headers.get_all('Content-Length', [])
            try:
                if len(lengths) != 1 or not 0 <= int(lengths[0]) <= MAX_LOGIN_BYTES:
                    raise ValueError()
            except ValueError:
                raise HttpReqErrJson(413, 'Browser request body too large or invalid') from None
        return super().data_from_payload(cmd)

    def end_response(self, response):
        if self.auth_audit is not None:
            code = response.get_code()
            event = AUDIT_STATUS_EVENTS.get(code)
            if code == 503 and (getattr(self, '_auth_unavailable', False)
                    or getattr(self, '_path', None) in BROWSER_AUTH_PATHS):
                event = 'auth.unavailable'
            if event:
                self.auth_audit.record(event)
        if self._browser():
            for name, value in SECURITY_HEADERS.items():
                response.add_header(name, value)
            if self._browser().secure:
                response.add_header('Strict-Transport-Security', 'max-age=31536000')
        return super().end_response(response)

    def do_OPTIONS(self):
        if self._browser():
            # Generic HTTPdis keeps historical CORS behavior; this console does not.
            self.end_response(self.build_response(code=405))
            return
        return super().do_OPTIONS()

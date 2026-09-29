"""Request-local Basic authentication for the httpdis transport."""
from httpdis.ext.httpdis_json import HttpReqHandler, HttpReqErrJson, HttpReqError
from auton.classes.authentication import PasswordAuthenticator
from auton.classes.exceptions import AutonConfigurationError
from auton.classes.web import authentication_request, SECURITY_HEADERS, MAX_LOGIN_BYTES
from httpdis.auth_browser import BrowserAuthProvider
from httpdis.authentication import AuthenticationDenied


AUDIT_STATUS_EVENTS = {401: 'auth.denied', 403: 'auth.forbidden'}
BROWSER_AUTH_PATHS = frozenset(('/ui/auth/login', '/ui/auth/logout'))


class AutonHttpReqHandler(HttpReqHandler):
    # Immutable process-start credential snapshot, never request identity.
    authenticator = PasswordAuthenticator()
    realm = 'Restricted'
    auth_audit = None

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

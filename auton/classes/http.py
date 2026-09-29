"""Request-local Basic authentication for the httpdis transport."""
from httpdis.ext.httpdis_json import HttpReqHandler, HttpReqErrJson
from auton.classes.authentication import PasswordAuthenticator
from auton.classes.exceptions import AutonConfigurationError


class AutonHttpReqHandler(HttpReqHandler):
    # Immutable process-start credential snapshot, never request identity.
    authenticator = PasswordAuthenticator()
    realm = 'Restricted'

    @classmethod
    def configure_auth(cls, filename=None, realm=None, required=False):
        realm = realm or 'Restricted'
        if not isinstance(realm, str) or any(ord(c) < 32 or c in '\\"' for c in realm):
            raise AutonConfigurationError('invalid authentication realm')
        cls.authenticator = PasswordAuthenticator.from_file(filename, required=required)
        cls.realm = realm

    def authenticate(self, auth_users=None):
        self._SERVER.pop('HTTP_AUTH_USER', None)
        self._SERVER.pop('HTTP_AUTH_PASSWD', None)
        principal = self.authenticator.authenticate(self.headers.get('Authorization', ''), auth_users)
        if principal is None:
            raise HttpReqErrJson(401, 'authentication required', headers={
                'WWW-Authenticate': 'Basic realm="%s"' % self.realm})
        self._SERVER['HTTP_AUTH_USER'] = principal

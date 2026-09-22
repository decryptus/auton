"""Request-local Basic authentication for the legacy httpdis transport."""
import base64
import binascii
import crypt
import hashlib
import hmac

from httpdis.ext.httpdis_json import HttpReqHandler, HttpReqErrJson


class AutonHttpReqHandler(HttpReqHandler):
    passwords = {}
    realm = 'Restricted'

    @classmethod
    def configure_auth(cls, filename=None, realm=None):
        cls.passwords = {}
        cls.realm = realm or 'Restricted'
        if filename:
            with open(filename, encoding='utf-8') as stream:
                for line in stream:
                    line = line.strip()
                    if line and not line.startswith('#') and ':' in line:
                        user, secret = line.split(':', 1)
                        if user and secret:
                            cls.passwords[user] = secret

    def authenticate(self, auth_users=None):
        # Do not mutate httpdis's shared authentication object: concurrent
        # requests must never exchange identities or retain a previous login.
        self._SERVER.pop('HTTP_AUTH_USER', None)
        self._SERVER.pop('HTTP_AUTH_PASSWD', None)
        denied = HttpReqErrJson(401, 'authentication required', headers={
            'WWW-Authenticate': 'Basic realm="%s"' % self.realm})
        try:
            scheme, encoded = self.headers.get('Authorization', '').split(' ', 1)
            if scheme.lower() != 'basic':
                raise ValueError('unsupported authentication scheme')
            raw = base64.b64decode(encoded, validate=True).decode('utf-8')
            user, password = raw.split(':', 1)
            if '\x00' in password:
                raise ValueError('invalid password')
        except (ValueError, UnicodeError, binascii.Error):
            raise denied
        secret = self.passwords.get(user)
        if not secret:
            raise denied
        if secret.startswith('{SHA}'):
            candidate = '{SHA}' + base64.b64encode(hashlib.sha1(password.encode('utf-8')).digest()).decode('ascii')
        else:
            candidate = crypt.crypt(password, secret)
        if not candidate or not hmac.compare_digest(candidate, secret):
            raise denied
        if auth_users and user not in auth_users:
            raise denied
        self._SERVER['HTTP_AUTH_USER'] = user

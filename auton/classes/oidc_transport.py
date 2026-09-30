"""Bounded OIDC token HTTP exchange; no flow/session/authorization policy."""
import json
import time
from urllib.parse import quote
import requests
from httpdis.authentication import AuthenticationDenied, AuthenticationUnavailable

MAX_DOCUMENT_BYTES = 65536
OIDC_TIMEOUT = 5


class OIDCTokenExchange:
    def __init__(self, endpoint, client_id, secret, redirect_uri):
        self.endpoint, self.client_id, self.secret = endpoint, client_id, secret
        self.redirect_uri = redirect_uri

    def __call__(self, code, verifier):
        payload = dict(grant_type='authorization_code', code=code, redirect_uri=self.redirect_uri,
                       client_id=self.client_id, code_verifier=verifier)
        auth = (quote(self.client_id, safe=''), quote(self.secret, safe='')) if self.secret is not None else None
        try:
            # No redirects/retries; a code exchange is not safely replayable.
            with requests.post(self.endpoint, data=payload, auth=auth,
                               timeout=OIDC_TIMEOUT, allow_redirects=False, stream=True) as response:
                if response.status_code != 200:
                    raise AuthenticationDenied()
                data = bytearray()
                deadline = time.monotonic() + OIDC_TIMEOUT
                for chunk in response.iter_content(4096):
                    data.extend(chunk)
                    if len(data) > MAX_DOCUMENT_BYTES or time.monotonic() > deadline:
                        raise AuthenticationUnavailable()
                body = json.loads(data)
                return body['id_token']
        except (requests.RequestException, ValueError, KeyError, TypeError):
            raise AuthenticationUnavailable() from None

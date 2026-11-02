"""Bounded OIDC token HTTP exchange; no flow/session/authorization policy."""
import json
import subprocess
import sys
import time
from urllib.parse import quote
import requests
from httpdis.authentication import AuthenticationDenied, AuthenticationUnavailable

MAX_DOCUMENT_BYTES = 65536
OIDC_TIMEOUT = 5
WORKER_MODULE = 'auton.classes.oidc_transport'
MAX_REQUEST_BYTES = 262144


class OIDCTokenExchange:
    def __init__(self, endpoint, client_id, secret, redirect_uri):
        self.endpoint, self.client_id, self.secret = endpoint, client_id, secret
        self.redirect_uri = redirect_uri

    def __call__(self, code, verifier):
        started = time.monotonic()
        process = None
        try:
            document = json.dumps(dict(endpoint=self.endpoint, client_id=self.client_id,
                                       secret=self.secret, redirect_uri=self.redirect_uri,
                                       code=code, verifier=verifier)).encode('utf-8')
            if len(document) > MAX_REQUEST_BYTES:
                raise AuthenticationUnavailable()
            # Private stdin carries credentials; never place them in argv or logs.
            # The parent deadline also bounds DNS, headers and slow buffered reads.
            process = subprocess.Popen([sys.executable, '-m', WORKER_MODULE],
                                       stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.DEVNULL, close_fds=True)
            remaining = OIDC_TIMEOUT - (time.monotonic() - started)
            if remaining <= 0:
                raise AuthenticationUnavailable()
            output, _ = process.communicate(document, timeout=remaining)
            if process.returncode or time.monotonic() - started > OIDC_TIMEOUT:
                raise AuthenticationUnavailable()
            result = json.loads(output)
            if result['status'] == 'denied':
                raise AuthenticationDenied()
            if result['status'] != 'success':
                raise AuthenticationUnavailable()
            return result['token']
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            raise AuthenticationUnavailable() from None
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.communicate()

    def _exchange(self, code, verifier):
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


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise AuthenticationUnavailable()
        document = json.loads(raw)
        exchange = OIDCTokenExchange(document['endpoint'], document['client_id'],
                                     document['secret'], document['redirect_uri'])
        token = exchange._exchange(document['code'], document['verifier'])
        result = dict(status='success', token=token)
    except AuthenticationDenied:
        result = dict(status='denied')
    except Exception:
        result = dict(status='unavailable')
    sys.stdout.write(json.dumps(result))


if __name__ == '__main__':
    main()

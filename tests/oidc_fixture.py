"""Local HTTPS identity provider with signed tokens and PKCE for integration tests."""
import base64
import hashlib
import json
import ssl
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class IdentityProvider:
    def __init__(self, path):
        self.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
        jwk.update(kid='integration-key', use='sig', alg='RS256')
        self.jwks = path / 'jwks.json'
        self.jwks.write_text(json.dumps({'keys': [jwk]}))
        self.flow = None
        self.calls = 0
        provider = self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_POST(self):
                provider.calls += 1
                data = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
                verifier = data.get('code_verifier', [''])[0]
                challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
                flow = provider.flow
                if not flow or challenge != flow['code_challenge'] or data.get('code') != ['test-code']:
                    self.send_error(400)
                    return
                provider.flow = None
                claims = dict(iss=provider.uri, aud=flow['client_id'], sub='subject-1', nonce=flow['nonce'],
                              iat=int(time.time()), exp=int(time.time()) + 120)
                token = jwt.encode(claims, provider.key, algorithm='RS256', headers={'kid': 'integration-key'})
                body = json.dumps({'id_token': token, 'token_type': 'Bearer', 'access_token': 'unused'}).encode()
                self.send_response(200)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(str(path / 'server.pem'), str(path / 'server.key'))
        self.server.socket = ctx.wrap_socket(self.server.socket, server_side=True)
        self.uri = 'https://127.0.0.1:%d' % self.server.server_port
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

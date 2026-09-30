"""Optional browser transport and fixed assets; application behavior stays in services."""
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlsplit

from httpdis.auth_browser import BrowserAuthProvider, browser_origin
from httpdis.authentication import AuthenticationRequest, AuthenticationDenied, AUTH_HEADER_NAMES
from httpdis.ext.httpdis_json import HttpReqErrJson, HttpResponse, HttpResponseJson
from auton.classes.exceptions import AutonConfigurationError

MAX_LOGIN_BYTES = 8192
LOGIN_FIELDS = frozenset(('principal', 'password'))
OIDC_COOKIE = '__Host-autond-oidc'
CALLBACK_FIELDS = frozenset(('code', 'state', 'iss', 'session_state'))
WEB_ROUTES = {
    'web_auth_options': ('^ui/auth/options$', 'web_auth_options', 'GET', False),
    'web_oidc_start': ('^ui/auth/oidc$', 'web_oidc_start', 'POST', False),
    'web_oidc_callback': ('^oidc/callback$', 'web_oidc_callback', 'GET', False),
    'web_console': ('^ui/?$', 'web_console', 'GET', False),
    'web_script': ('^ui/app\\.js$', 'web_script', 'GET', False),
    'web_style': ('^ui/style\\.css$', 'web_style', 'GET', False),
    'web_logo': ('^ui/logo\\.svg$', 'web_logo', 'GET', False),
    'web_login': ('^ui/auth/login$', 'web_login', 'POST', False),
    'web_session': ('^ui/auth/session$', 'web_session', 'GET', True),
    'web_logout': ('^ui/auth/logout$', 'web_logout', 'POST', True),
}
WEB_PATHS = ('ui', 'ui/', 'ui/app.js', 'ui/style.css', 'ui/logo.svg',
             'ui/auth/login', 'ui/auth/session', 'ui/auth/logout',
             'ui/auth/options', 'ui/auth/oidc', 'oidc/callback')
SECURITY_HEADERS = {
    'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
    'X-Content-Type-Options': 'nosniff', 'X-Frame-Options': 'DENY',
    'Referrer-Policy': 'no-referrer', 'Cache-Control': 'no-store',
    'Permissions-Policy': 'camera=(), microphone=(), geolocation=()',
}


def configure_web(conf):
    """Called after required-auth policy; only fixed UI assets/login become public."""
    general = conf['general']
    enabled = general.get('web_enabled', False)
    if type(enabled) is not bool:
        raise AutonConfigurationError('web_enabled must be a boolean')
    if not enabled:
        return
    if general.get('auth_mode') != 'required' or not general.get('authentication'):
        raise AutonConfigurationError('web console requires auth_mode: required and SQLite authentication')
    try:
        general['web_origin'] = browser_origin(general.get('web_origin'))
    except ValueError as error:
        raise AutonConfigurationError(str(error)) from None
    routes = conf.get('modules', {}).get('job', {}).get('routes')
    if not isinstance(routes, dict) or not any(r.get('handler') == 'job_run' and r.get('safe_init') is True for r in routes.values()):
        raise AutonConfigurationError('web console requires initialized job module routes')
    for module in conf.get('modules', {}).values():
        for name, route in module.get('routes', {}).items():
            if name in WEB_ROUTES or route.get('name', '').lstrip('/') in WEB_PATHS:
                raise AutonConfigurationError('reserved web route')
            if 'regexp' in route and any(re.match(route['regexp'], path) for path in WEB_PATHS):
                raise AutonConfigurationError('configured route overlaps reserved web paths')
    for name, (pattern, handler, method, auth) in WEB_ROUTES.items():
        routes[name] = dict(regexp=pattern, handler=handler, op=method, auth=auth, log=False)


def authentication_request(request):
    return AuthenticationRequest(request.command, request._path,
        tuple((name.lower(), value) for name, value in request.headers.items() if name.lower() in AUTH_HEADER_NAMES),
        request.client_address[0])


def auth_call(callback, *args, **kwargs):
    try:
        return callback(*args, **kwargs)
    except AuthenticationDenied:
        raise HttpReqErrJson(401, 'Authentication required') from None
    except Exception:
        # Do not disclose exception text from credential/storage adapters.
        raise HttpReqErrJson(503, 'Authentication unavailable') from None


class WebConsole:
    def __init__(self, provider, jobs):
        if not isinstance(provider, BrowserAuthProvider):
            raise ValueError('browser authentication is not configured')
        self.provider = provider
        self.jobs = jobs

    @staticmethod
    def asset(filename, content_type):
        data = (Path(__file__).resolve().parents[1] / 'web' / filename).read_text(encoding='utf-8')
        return HttpResponse(200, data, headers={'Content-type': content_type + '; charset=utf-8'})

    def identity(self, identity):
        try:
            release = version('autond')
        except PackageNotFoundError:
            release = 'development'
        return {'principal': identity.principal, 'scopes': sorted(identity.scopes),
                'maintenance_operator': identity.principal in self.jobs.maintenance_operators,
                'version': release}

    def login(self, request):
        payload = request.payload_params()
        if not isinstance(payload, dict) or set(payload) != LOGIN_FIELDS:
            raise HttpReqErrJson(400, 'principal and password are required')
        grant = auth_call(self.provider.login, authentication_request(request), **payload)
        result = self.identity(grant.identity)
        result['csrf'] = grant.csrf
        return HttpResponseJson(200, result, headers={
            'Set-Cookie': self.provider.cookie(grant.secret, int(self.provider.service.session_ttl))})

    def session(self, request):
        identity = request.get_server_vars().get('HTTP_AUTH_IDENTITY')
        if identity is None or identity.method != 'session':
            raise HttpReqErrJson(401, 'Browser session required')
        result = self.identity(identity)
        if hasattr(self.provider.service, 'csrf_for'):
            secret = self.provider.session_secret(authentication_request(request))
            csrf = self.provider.service.csrf_for(secret)
            if csrf:
                result['csrf'] = csrf
        return result

    def logout(self, request):
        auth_call(self.provider.logout, authentication_request(request))
        return HttpResponseJson(200, {'logged_out': True}, headers={'Set-Cookie': self.provider.expired_cookie()})

    def auth_options(self, request):
        return {'oidc': hasattr(self.provider.service, 'begin')}

    def oidc_start(self, request):
        if not hasattr(self.provider.service, 'begin'):
            raise HttpReqErrJson(404, 'SSO is not configured')
        auth_call(self.provider.require_browser, authentication_request(request), True)
        if request.payload_params() != {} or request.headers.get('Authorization') is not None:
            raise HttpReqErrJson(400, 'SSO requires an empty browser request')
        url, binding = auth_call(self.provider.service.begin)
        return HttpResponseJson(200, {'authorization_url': url}, headers={
            'Set-Cookie': OIDC_COOKIE + '=' + binding + '; Path=/; Max-Age=300; HttpOnly; Secure; SameSite=Lax'})

    def oidc_callback(self, request):
        if not hasattr(self.provider.service, 'finish'):
            raise HttpReqErrJson(404, 'SSO is not configured')
        # This is an intentional cross-site top-level GET from the trusted IdP.
        # Do not apply same-origin fetch policy; bind it to a one-use state cookie.
        values = authentication_request(request)
        for name in AUTH_HEADER_NAMES:
            values.header(name)
        if values.header('host', '').lower() != self.provider.authority or values.header('authorization') is not None:
            raise HttpReqErrJson(401, 'SSO callback rejected')
        try:
            if len(request.path) > 8192:
                raise ValueError()
            pairs = parse_qsl(urlsplit(request.path).query, keep_blank_values=True, max_num_fields=8)
            params = dict(pairs)
            if len(params) != len(pairs) or not {'code', 'state'} <= set(params) or set(params) - CALLBACK_FIELDS:
                raise ValueError()
            if params.get('iss', self.provider.service.settings['issuer']) != self.provider.service.settings['issuer']:
                raise ValueError()
            cookie = values.header('cookie', '')
            if len(cookie) > 8192:
                raise ValueError()
            bindings = [part.strip().partition('=')[2] for part in cookie.split(';')
                        if part.strip().partition('=')[0] == OIDC_COOKIE]
            if len(bindings) != 1:
                raise ValueError()
        except ValueError:
            raise HttpReqErrJson(401, 'SSO callback rejected') from None
        grant = auth_call(self.provider.service.finish, params['state'], bindings[0], params['code'])
        # No token or CSRF value in the URL. Session GET supplies CSRF same-origin.
        return HttpResponse(303, '', headers={'Location': '/ui/',
            'Set-Cookie': self.provider.cookie(grant.secret, max(0, int(grant.expires_at - time.time())))})

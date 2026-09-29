"""Optional browser transport and fixed assets; application behavior stays in services."""
from importlib.metadata import version, PackageNotFoundError
from pathlib import Path
import re

from httpdis.auth_browser import BrowserAuthProvider, browser_origin
from httpdis.authentication import AuthenticationRequest, AuthenticationDenied, AUTH_HEADER_NAMES
from httpdis.ext.httpdis_json import HttpReqErrJson, HttpResponse, HttpResponseJson
from auton.classes.exceptions import AutonConfigurationError

MAX_LOGIN_BYTES = 8192
LOGIN_FIELDS = frozenset(('principal', 'password'))
WEB_ROUTES = {
    'web_console': ('^ui/?$', 'web_console', 'GET', False),
    'web_script': ('^ui/app\\.js$', 'web_script', 'GET', False),
    'web_style': ('^ui/style\\.css$', 'web_style', 'GET', False),
    'web_logo': ('^ui/logo\\.svg$', 'web_logo', 'GET', False),
    'web_login': ('^ui/auth/login$', 'web_login', 'POST', False),
    'web_session': ('^ui/auth/session$', 'web_session', 'GET', True),
    'web_logout': ('^ui/auth/logout$', 'web_logout', 'POST', True),
}
WEB_PATHS = ('ui', 'ui/', 'ui/app.js', 'ui/style.css', 'ui/logo.svg',
             'ui/auth/login', 'ui/auth/session', 'ui/auth/logout')
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
        return self.identity(identity)

    def logout(self, request):
        auth_call(self.provider.logout, authentication_request(request))
        return HttpResponseJson(200, {'logged_out': True}, headers={'Set-Cookie': self.provider.expired_cookie()})

"""Local administration interface for the shared authentication service."""
import argparse
import getpass
import json
import os
import sys

from auton.classes.auth_store import PersistentAuthentication, load_authentication_config, AUTH_SCOPES

DEFAULT_CONFIG = '/etc/auton/auton.yml'
DEFAULT_TOKEN_TTL = 3600


def parser():
    result = argparse.ArgumentParser(description='Administer local autond authentication as the daemon OS user')
    result.add_argument('-c', '--config', default=DEFAULT_CONFIG)
    commands = result.add_subparsers(dest='kind', required=True)
    users = commands.add_parser('user').add_subparsers(dest='action', required=True)
    users.add_parser('list')
    create = users.add_parser('set', help='Create an account or rotate its password; revokes existing credentials')
    create.add_argument('-u', '--user', required=True)
    create.add_argument('-s', '--scope', action='append', default=None, choices=sorted(AUTH_SCOPES))
    disable = users.add_parser('disable')
    disable.add_argument('-u', '--user', required=True)
    tokens = commands.add_parser('token').add_subparsers(dest='action', required=True)
    issue = tokens.add_parser('create', help='Write a new token to an exclusive private file')
    issue.add_argument('-u', '--user', required=True)
    issue.add_argument('-s', '--scope', action='append', default=None, choices=sorted(AUTH_SCOPES))
    issue.add_argument('-t', '--ttl', type=int, default=DEFAULT_TOKEN_TTL, help='Lifetime in seconds (maximum 30 days)')
    issue.add_argument('-o', '--output', required=True, help='New file; never overwrite an existing token')
    revoke = tokens.add_parser('revoke')
    revoke.add_argument('-i', '--id', required=True, help='Credential ID returned by token create')
    return result


def run(options, authentication, password_reader=getpass.getpass):
    """Render/administer; authentication operations remain in the neutral service."""
    if options.kind == 'user':
        if options.action == 'list':
            return {'accounts': authentication.list_accounts()}
        if options.action == 'disable':
            authentication.disable(options.user)
            return {'disabled': options.user}
        password = password_reader('New password: ')
        if password != password_reader('Repeat password: '):
            raise ValueError('passwords do not match')
        authentication.provision(options.user, password, options.scope or ['read'])
        return {'updated': options.user}
    if options.action == 'revoke':
        authentication.revoke_token(options.id)
        return {'revoked': options.id}
    # Reserve output before creating credentials. O_EXCL rejects symlinks too.
    descriptor = os.open(options.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    grant = None
    try:
        with os.fdopen(descriptor, 'w') as stream:
            grant = authentication.issue_token(options.user, options.scope or ['read'], options.ttl)
            stream.write(grant.secret + '\n')
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            if grant is not None:
                authentication.revoke_token(grant.credential_id)
        finally:
            os.unlink(options.output)
        raise
    return {'credential_id': grant.credential_id, 'expires': grant.expires_at, 'file': options.output}


def main(argv=None):
    options = parser().parse_args(argv)
    authentication = None
    try:
        authentication = PersistentAuthentication(load_authentication_config(options.config))
        print(json.dumps(run(options, authentication)))
        return 0
    except KeyboardInterrupt:
        return 130
    except Exception:
        # Never display adapter exception text, passwords or bearer tokens.
        print('Authentication administration failed; check configuration, permissions and arguments.', file=sys.stderr)
        return 1
    finally:
        if authentication is not None:
            authentication.close()

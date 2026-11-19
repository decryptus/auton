# SPDX-License-Identifier: GPL-3.0-or-later
"""Endpoint client-input policies; independent of HTTP, CLI and execution."""
import copy
import io
import os
import stat

from dotenv.main import dotenv_values
from sonicprobe.libs.argpolicy import ArgsPolicy, EnvPolicy, PolicyError, ValidationError

from auton.classes.exceptions import AutonConfigurationError
from auton.classes.parameters import InvalidParameters

CLIENT_FIELDS = ('args', 'env', 'argfiles', 'envfiles')
DEFAULT_CLIENT_ALLOW = False
MAX_ENVFILE_BYTES = 1048576
SNAPSHOT_KEY = '_prepared_envfiles'


def migration_categories(config):
    return [field for field in CLIENT_FIELDS
            if 'allow-' + field not in config and config.get('disallow-' + field) is not True
            and not (field == 'envfiles' and 'allow-client-envfiles' in config)]


def _paths(spec):
    if type(spec) is bool:
        return spec
    if not isinstance(spec, list) or any(not isinstance(p, str) or not p
            or not os.path.isabs(p) or '\x00' in p for p in spec):
        raise PolicyError('envfile policy requires a boolean or absolute path list')
    # Keep configured paths, resolve both policy and selection just before opening.
    return tuple(spec)


class ClientPolicy:
    def __init__(self, config):
        self.config = copy.deepcopy(config)
        if 'allow-client-envfiles' in config:
            if 'allow-envfiles' in config:
                raise AutonConfigurationError('use only allow-envfiles, not both envfile policy names')
            self.config['allow-envfiles'] = config['allow-client-envfiles']
        try:
            self.args = ArgsPolicy(*self.settings('args'))
            self.env = EnvPolicy(*self.settings('env'))
            self.uploads = ArgsPolicy(*self.settings('argfiles'))
            # Files are opaque uploads, not a command language. Rules concern
            # their option name and the generated pathname, never file contents.
            self.files_allow, self.files_deny = map(_paths, self.settings('envfiles'))
        except PolicyError as error:
            raise AutonConfigurationError(str(error)) from None
        self.legacy_files = (DEFAULT_CLIENT_ALLOW and not any(
            key in self.config for key in ('allow-envfiles', 'allow-env', 'disallow-env')))

    def settings(self, field):
        return (self.config.get('allow-' + field, DEFAULT_CLIENT_ALLOW),
                self.config.get('disallow-' + field, False))

    def check_path(self, path):
        if not isinstance(path, str) or not path or '\x00' in path:
            raise InvalidParameters('invalid client envfile path')
        resolved = os.path.realpath(path)
        allowed = self.files_allow is True or isinstance(self.files_allow, tuple) and resolved in {
            os.path.realpath(p) for p in self.files_allow}
        denied = self.files_deny is True or isinstance(self.files_deny, tuple) and resolved in {
            os.path.realpath(p) for p in self.files_deny}
        if not allowed or denied:
            raise InvalidParameters('endpoint forbids the selected client envfile')
        return resolved

    def validate(self, payload):
        try:
            self.args.validate(payload.get('args', []))
            self.env.validate(payload.get('env', {}))
            files = payload.get('envfiles', [])
            if not isinstance(files, list):
                raise InvalidParameters('envfiles must be a list')
            for path in files:
                self.check_path(path)
            uploads = payload.get('argfiles', [])
            if not isinstance(uploads, list):
                raise InvalidParameters('argfiles must be a list')
            # Before files are materialized, validate the option and original
            # filename. Contents are opaque program data, not shell arguments.
            upload_args = []
            for upload in uploads:
                if not isinstance(upload, dict) or set(upload) != {'arg', 'filename', 'content'}:
                    raise InvalidParameters('invalid uploaded argfile')
                arg, filename = upload['arg'], upload['filename']
                if (not isinstance(arg, str) or not isinstance(filename, str)
                        or filename in ('.', '..') or any(c in filename for c in ('/', '\\', '\x00'))):
                    raise InvalidParameters('invalid uploaded argfile name')
                if arg.endswith('@'):
                    arg = arg[:-1]
                upload_args.extend((arg, filename))
            self.uploads.validate(upload_args)
        except ValidationError as error:
            raise InvalidParameters(str(error)) from None

    def prepare(self, payload):
        self.validate(payload)
        result = copy.deepcopy(payload)
        snapshot = {}
        for path in payload.get('envfiles', []):
            resolved = self.check_path(path)
            try:
                fd = os.open(resolved, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW)
                with os.fdopen(fd, 'rb') as stream:
                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise InvalidParameters('client envfile must be a regular file')
                    data = stream.read(MAX_ENVFILE_BYTES + 1)
                if len(data) > MAX_ENVFILE_BYTES:
                    raise InvalidParameters('client envfile is too large')
                values = dict(dotenv_values(stream=io.StringIO(data.decode('utf-8')),
                                            interpolate=self.legacy_files))
            except (OSError, UnicodeError):
                raise InvalidParameters('unable to read client envfile') from None
            try:
                self.env.validate(values)
            except ValidationError as error:
                raise InvalidParameters(str(error)) from None
            snapshot.update(values)
        result[SNAPSHOT_KEY] = snapshot
        return result

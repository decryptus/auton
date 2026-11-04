"""Read-only checks for configurations migrating from 1.x to 2.0."""
from pathlib import Path

from auton.classes.authentication import AUTH_MODES
from auton.classes.configuration_schema import validate_configuration
from auton.classes.endpoint_imports import load_endpoint_imports, read_mapping
from auton.classes.exceptions import AutonConfigurationError


def migration_findings(filename):
    path = Path(filename).absolute()
    conf = validate_configuration(read_mapping(path))
    conf['_config_directory'] = str(path.parent)
    conf['endpoints'], _ = load_endpoint_imports(conf)
    validate_configuration(conf)
    if not conf['endpoints']:
        raise AutonConfigurationError('Missing endpoints')
    findings = []
    general = conf['general']
    if 'auth_mode' not in general:
        findings.append('AUTON_MIGRATION_AUTH_DEFAULT: configure credentials and test auth_mode: required.')
    elif not isinstance(general['auth_mode'], str) or general['auth_mode'] not in AUTH_MODES:
        raise AutonConfigurationError('Invalid authentication mode')
    for name, endpoint in conf['endpoints'].items():
        if endpoint['plugin'] != 'subproc':
            continue
        config = endpoint.get('config', {})
        flag = 'disallow-envfiles'
        if 'allow-client-envfiles' in config:
            allowed = config['allow-client-envfiles']
            if not isinstance(allowed, list) or any(
                    not isinstance(path, str) or not Path(path).is_absolute() or '\x00' in path
                    for path in allowed):
                findings.append('REVIEW %r: allow-client-envfiles requires absolute server paths.' % name)
            elif allowed and config.get(flag):
                findings.append('REVIEW %r: conflicting client envfile policies.' % name)
            continue
        if flag not in config and endpoint.get('import_config'):
            findings.append('REVIEW %r: imported component determines client envfile policy. '
                            'Inspect the imported config for envfiles used by clients.' % name)
        elif flag in config and not isinstance(config[flag], bool):
            findings.append('REVIEW %r: use a YAML boolean for disallow-envfiles.' % name)
        elif not config.get(flag):
            findings.append('AUTON_MIGRATION_ENVFILES_ALLOWED %r: check actual client use in daemon logs '
                            'before migrating from 1.x. Add any required paths to allow-client-envfiles.' % name)
    return findings

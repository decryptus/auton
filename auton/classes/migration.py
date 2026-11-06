"""Read-only checks for the two planned 2.0 default changes."""
from pathlib import Path

from auton.classes.client_policy import ClientPolicy, migration_categories

from auton.classes.authentication import AUTH_MODES
from auton.classes.configuration_schema import validate_configuration
from auton.classes.endpoint_imports import BLOCK_IMPORTS, load_endpoint_imports, read_mapping
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
        ClientPolicy(config)  # Compile explicit rules without opening files or running templates.
        categories = migration_categories(config)
        if categories and (endpoint.get('import_config') or any(
                key in endpoint or key in config for key in BLOCK_IMPORTS)):
            findings.append('REVIEW %r: imported component may determine client policies. '
                            'Check the effective startup warnings.' % name)
        for field in categories:
            findings.append('AUTON_MIGRATION_CLIENT_POLICY %r: %s will be denied by default in 2.0; '
                            'set allow-%s: true or a selective policy for required inputs.' %
                            (name, field, field))
        if 'allow-client-envfiles' in config:
            findings.append('REVIEW %r: rename allow-client-envfiles to allow-envfiles.' % name)
    return findings

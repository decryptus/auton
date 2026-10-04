"""XYS configuration structure checks, without loading or initializing services."""
from sonicprobe.libs import xys
from auton.classes.exceptions import AutonConfigurationError


xys.add_callback('auton.config.mapping', lambda value: isinstance(value, dict))
MAPPING = '!~~callback(auton.config.mapping) null'
MAPPING_SCHEMA = xys.load(MAPPING)


def validate_fields(data, schema):
    # Unknown fields belong to extensions. Never use a wildcard that could consume
    # known optional fields before their XYS validators run.
    if not isinstance(data, dict) or not xys.validate(
            {key: value for key, value in data.items() if key in schema}, schema):
        raise AutonConfigurationError('Invalid configuration structure')
    return data


def validate_mapping(data):
    if not xys.validate(data, MAPPING_SCHEMA):
        raise AutonConfigurationError('Invalid configuration mapping')
    return data


CONFIG_SCHEMA = xys.load('''
general: %s
endpoints*: %s
modules*: %s
''' % (MAPPING, MAPPING, MAPPING))
ENDPOINT_SCHEMA = xys.load('''
plugin![1,]: !!str
config?: %s
users?: %s
vars?: %s
discovery?: %s
import_config*: !!str
import_users*: !!str
import_vars*: !!str
''' % (MAPPING, MAPPING, MAPPING, MAPPING))


def validate_configuration(conf):
    validate_fields(conf, CONFIG_SCHEMA)
    for definition in (conf.get('endpoints') or {}).values():
        validate_fields(definition, ENDPOINT_SCHEMA)
    return conf

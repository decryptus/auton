# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve endpoint catalogues without initializing plugins or HTTP modules."""
from pathlib import Path
import os

import yaml
from mako.template import Template

from auton.classes.exceptions import AutonConfigurationError

MAX_ENDPOINT_IMPORT_FILES = 32
MAX_ENDPOINT_IMPORT_BYTES = 1048576
MAX_IMPORTED_ENDPOINTS = 1024
COMPONENT_SECTIONS = ('vars', 'config', 'users')
IMPORT_FIELDS = frozenset(('import_endpoints', 'import_modules', 'import_config', 'import_vars', 'import_users'))
TEMPLATE_IMPORTS = ('from os import environ as ENV', 'from sonicprobe.helpers import to_yaml as my')


class EndpointLoader(yaml.SafeLoader):
    pass


def strict_mapping(loader, node):
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str) or key in result:
            raise ValueError('mapping keys must be distinct strings')
        result[key] = loader.construct_object(value_node)
    return result


EndpointLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, strict_mapping)


def local_path(value, directory):
    if not isinstance(value, str) or not value or '://' in value or '\x00' in value:
        raise AutonConfigurationError('imports require a local filename')
    return (Path(directory or os.getcwd()) / value).resolve()


def read_mapping(path, budget=None, context=None):
    try:
        limit = MAX_ENDPOINT_IMPORT_BYTES if budget is None else budget[0]
        with path.open('rb') as stream:
            source = stream.read(limit + 1)
        if len(source) > limit:
            raise ValueError('import byte limit exceeded')
        if budget is not None:
            budget[0] -= len(source)
        source = source.decode('utf-8')
        if context is not None:
            source = Template(source, imports=TEMPLATE_IMPORTS).render(**context)
            if len(source.encode('utf-8')) > MAX_ENDPOINT_IMPORT_BYTES:
                raise ValueError('rendered component exceeds byte limit')
        data = yaml.load(source, Loader=EndpointLoader)
        if not isinstance(data, dict):
            raise ValueError('expected a mapping')
        return data
    except Exception:
        # Do not print YAML values or template exceptions: these may contain secrets.
        raise AutonConfigurationError('unable to load mapping from %s (missing, invalid, duplicate keys or too large)' % path) from None


def load_endpoint_imports(conf):
    """Return combined definitions and the declaring file of imported entries."""
    inline = conf.get('endpoints') or {}
    if not isinstance(inline, dict):
        raise AutonConfigurationError('endpoints must be a mapping')
    definitions, sources = dict(inline), {}
    if 'import_endpoints' not in conf:
        return definitions, sources
    files = conf['import_endpoints']
    files = [files] if isinstance(files, str) else files
    if not isinstance(files, list) or not 1 <= len(files) <= MAX_ENDPOINT_IMPORT_FILES:
        raise AutonConfigurationError('import_endpoints requires 1-32 local filenames')
    seen, budget = set(), [MAX_ENDPOINT_IMPORT_BYTES]
    for filename in files:
        path = local_path(filename, conf.get('_config_directory'))
        if path in seen:
            raise AutonConfigurationError('duplicate endpoint import file: %s' % path)
        seen.add(path)
        entries = read_mapping(path, budget)
        if set(entries) & IMPORT_FIELDS:
            raise AutonConfigurationError('nested section imports are forbidden in endpoint catalogue: %s' % path)
        for name, definition in entries.items():
            if not name or not isinstance(definition, dict):
                raise AutonConfigurationError('invalid endpoint definition in %s' % path)
            if name in definitions:
                raise AutonConfigurationError('duplicate endpoint %r in %s' % (name, path))
            if not isinstance(definition.get('plugin'), str) or not definition['plugin']:
                raise AutonConfigurationError('missing/invalid plugin in %s, endpoint %r' % (path, name))
            for section in COMPONENT_SECTIONS:
                if section in definition and not isinstance(definition[section], dict):
                    raise AutonConfigurationError('invalid %s mapping in %s, endpoint %r' % (section, path, name))
            if set(definition) & (IMPORT_FIELDS - {'import_config', 'import_vars', 'import_users'}):
                raise AutonConfigurationError('nested endpoint/module import in %s, endpoint %r' % (path, name))
            definitions[name] = definition
            sources[name] = str(path)
            if len(sources) > MAX_IMPORTED_ENDPOINTS:
                raise AutonConfigurationError('endpoint imports exceed 1024 definitions')
    return definitions, sources


def load_component(filename, source, context):
    """A terminal component relative to the endpoint catalogue declaring it."""
    path = local_path(filename, Path(source).parent)
    data = read_mapping(path, context=context)
    if set(data) & IMPORT_FIELDS:
        raise AutonConfigurationError('component cannot import another section: %s (endpoint catalogue %s)' % (path, source))
    return data

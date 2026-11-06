# SPDX-License-Identifier: GPL-3.0-or-later
"""Resolve configuration and terminal data blocks before plugin initialization."""
from pathlib import Path

from sonicprobe.libs.argpolicy import ArgsPolicy, EnvPolicy, PolicyError

from auton.classes.endpoint_imports import (
    BLOCK_IMPORTS, IMPORT_FIELDS, load_component, local_path, read_document,
)
from auton.classes.exceptions import AutonConfigurationError

MAX_BLOCK_DEPTH = 32


def _terminal(value, path, ancestors=(), depth=0, seen=None):
    if not isinstance(value, (dict, list)):
        return
    if depth >= MAX_BLOCK_DEPTH or id(value) in ancestors:
        raise AutonConfigurationError('recursive or too deeply nested block in %s' % path)
    seen = set() if seen is None else seen
    if id(value) in seen:
        return
    seen.add(id(value))
    if isinstance(value, dict) and set(value) & IMPORT_FIELDS:
        raise AutonConfigurationError('block cannot import another section: %s' % path)
    children = value.values() if isinstance(value, dict) else value
    for child in children:
        _terminal(child, path, ancestors + (id(value),), depth + 1, seen)


def _block(filename, field, source):
    path = local_path(filename, Path(source).parent)
    value = read_document(path)  # Plain data: no Mako evaluation in terminal blocks.
    _terminal(value, path)
    try:
        if field == 'args':
            if not isinstance(value, list) or any(not isinstance(v, str) or '\x00' in v for v in value):
                raise ValueError()
        elif field == 'env':
            if not isinstance(value, dict):
                raise ValueError()
            EnvPolicy(True, False).validate(value)
        else:
            policy = ArgsPolicy if field.endswith('args') else EnvPolicy
            policy(value, False) if field.startswith('allow-') else policy(True, value)
    except (ValueError, PolicyError):
        raise AutonConfigurationError('invalid %s block in %s' % (field, path)) from None
    return value


def _resolve_blocks(data, source):
    forbidden = set(data) & (IMPORT_FIELDS - BLOCK_IMPORTS.keys())
    if forbidden:
        raise AutonConfigurationError('configuration cannot import another section: %s' % source)
    result = {}
    for key, field in BLOCK_IMPORTS.items():
        if key in data:
            result[field] = _block(data[key], field, source)
    # Replace whole blocks: combining allowlists could broaden permissions.
    result.update((key, value) for key, value in data.items() if key not in BLOCK_IMPORTS)
    return result


def load_config(endpoint, source, context):
    """Imported configuration < endpoint block imports < inline configuration."""
    result = {}
    if 'import_config' in endpoint:
        filename = endpoint['import_config']
        data = load_component(filename, source, context, allowed_imports=BLOCK_IMPORTS)
        result.update(_resolve_blocks(data, local_path(filename, Path(source).parent)))
    direct = {key: endpoint[key] for key in BLOCK_IMPORTS if key in endpoint}
    result.update(_resolve_blocks(direct, source))
    result.update(_resolve_blocks(endpoint.get('config', {}), source))
    return result

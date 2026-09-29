# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit client inventory loading; never selects targets or makes requests."""
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode

from auton_client.connections import normalize_origin, validate_connection_name

MAX_CONFIG_BYTES = 65536
MAX_CONFIG_TARGETS = 128
CONFIG_FIELDS = frozenset(('targets',))
STRING_TAG = 'tag:yaml.org,2002:str'
MAPPING_TAG = 'tag:yaml.org,2002:map'


def _mapping(node):
    if not isinstance(node, MappingNode) or node.tag != MAPPING_TAG:
        raise ValueError('client config requires mappings')
    result = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.tag != STRING_TAG:
            raise ValueError('client config keys must be strings (quote numeric names)')
        if key.value in result:
            raise ValueError('duplicate client config key: ' + key.value)
        result[key.value] = value
    return result


def load_targets(path):
    """Read and validate the complete flat inventory, including unselected entries."""
    try:
        with Path(path).open('rb') as stream:
            source = stream.read(MAX_CONFIG_BYTES + 1)
        if len(source) > MAX_CONFIG_BYTES:
            raise ValueError('client config exceeds 64 KiB')
        source = source.decode('utf-8')
        # Inspect safe nodes without constructing custom objects or expanding merges.
        # The schema has a fixed depth: root mapping, targets mapping, URI scalars.
        root = _mapping(yaml.compose(source, Loader=yaml.SafeLoader))
        if set(root) != CONFIG_FIELDS:
            raise ValueError('client config must contain only a targets mapping')
        entries = _mapping(root['targets'])
        if not 1 <= len(entries) <= MAX_CONFIG_TARGETS:
            raise ValueError('client config must declare 1-128 targets')
        result = {}
        for name, value in entries.items():
            validate_connection_name(name)
            if not isinstance(value, ScalarNode) or value.tag != STRING_TAG:
                raise ValueError('target URI must be a string')
            result[name] = normalize_origin(value.value)
        return result
    except (OSError, UnicodeError):
        raise ValueError('unable to read UTF-8 client config') from None
    except (yaml.YAMLError, RecursionError):
        # Parser diagnostics can echo arbitrary file contents; do not expose them.
        raise ValueError('invalid client YAML config') from None

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit client inventory loading; never selects targets or makes requests."""
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from auton_client.connections import normalize_origin, validate_connection_name

MAX_CONFIG_BYTES = 65536
MAX_CONFIG_TARGETS = 128
MAX_CONFIG_GROUPS = 128
MAX_IMPORT_FILES = 16
CONFIG_SECTIONS = ('targets', 'groups')
CONFIG_FIELDS = frozenset(('targets', 'groups', 'import_targets', 'import_groups'))
STRING_TAG = 'tag:yaml.org,2002:str'
MAPPING_TAG = 'tag:yaml.org,2002:map'
SEQUENCE_TAG = 'tag:yaml.org,2002:seq'


def _mapping(node):
    if not isinstance(node, MappingNode) or node.tag != MAPPING_TAG:
        raise ValueError('client config requires mappings')
    result = {}
    for key, value in node.value:
        if not isinstance(key, ScalarNode) or key.tag != STRING_TAG:
            raise ValueError('client config keys must be strings (quote numeric names)')
        if key.value in result:
            raise ValueError('duplicate client config key')
        result[key.value] = value
    return result


def _read_node(path, budget):
    with path.open('rb') as stream:
        source = stream.read(budget[0] + 1)
    budget[0] -= len(source)
    if budget[0] < 0:
        raise ValueError('client config and imports exceed 64 KiB')
    return yaml.compose(source.decode('utf-8'), Loader=yaml.SafeLoader)


def _import_paths(node):
    nodes = node.value if isinstance(node, SequenceNode) and node.tag == SEQUENCE_TAG else [node]
    if not 1 <= len(nodes) <= MAX_IMPORT_FILES:
        raise ValueError('imports require 1-16 local file paths')
    paths = []
    for item in nodes:
        if (not isinstance(item, ScalarNode) or item.tag != STRING_TAG or not item.value
                or '://' in item.value or '\x00' in item.value):
            raise ValueError('imports require local file paths')
        paths.append(item.value)
    return paths


def load_inventory(path):
    """Validate inline/imported sections completely, including unselected entries.

    Section imports follow DWho's import_<section> convention, using local files
    containing the section mapping itself. Imports are flat and bounded; the
    client loader does not import the daemon framework or construct YAML objects.
    """
    try:
        path = Path(path).resolve()
        budget = [MAX_CONFIG_BYTES]
        root = _mapping(_read_node(path, budget))
        if not set(root) <= CONFIG_FIELDS or not ('targets' in root or 'import_targets' in root):
            raise ValueError('client config requires targets or import_targets; unknown fields are rejected')
        sections = {}
        seen = {path}
        for section in CONFIG_SECTIONS:
            merged = {}
            import_key = 'import_' + section
            if import_key in root:
                for name in _import_paths(root[import_key]):
                    imported_path = (path.parent / name).resolve()
                    if imported_path in seen:
                        raise ValueError('duplicate or self-referencing import file')
                    if len(seen) > MAX_IMPORT_FILES:
                        raise ValueError('client config exceeds 16 imported files')
                    seen.add(imported_path)
                    values = _mapping(_read_node(imported_path, budget))
                    if any(key.startswith('import_') for key in values):
                        raise ValueError('nested imports are not supported; import only from the main file')
                    if merged.keys() & values.keys():
                        raise ValueError('duplicate entry across imported files')
                    merged.update(values)
            if section in root:
                values = _mapping(root[section])
                if merged.keys() & values.keys():
                    raise ValueError('inline entry duplicates an imported entry')
                merged.update(values)
            sections[section] = merged
        entries = sections['targets']
        if not 1 <= len(entries) <= MAX_CONFIG_TARGETS:
            raise ValueError('client config must declare 1-128 targets')
        targets = {}
        for name, value in entries.items():
            validate_connection_name(name)
            if not isinstance(value, ScalarNode) or value.tag != STRING_TAG:
                raise ValueError('target URI must be a string')
            targets[name] = normalize_origin(value.value)
        groups = {}
        entries = sections['groups']
        if len(entries) > MAX_CONFIG_GROUPS:
            raise ValueError('client config exceeds 128 groups')
        for name, value in entries.items():
            validate_connection_name(name)
            if (not isinstance(value, SequenceNode) or value.tag != SEQUENCE_TAG
                    or not 1 <= len(value.value) <= MAX_CONFIG_TARGETS):
                raise ValueError('group must contain 1-128 target names')
            members = []
            for member in value.value:
                if not isinstance(member, ScalarNode) or member.tag != STRING_TAG:
                    raise ValueError('group members must be target name strings')
                validate_connection_name(member.value)
                if member.value not in targets:
                    raise ValueError('group references an unknown target: ' + member.value)
                if member.value not in members:
                    members.append(member.value)
            groups[name] = members
        return {'targets': targets, 'groups': groups}
    except (OSError, UnicodeError):
        raise ValueError('unable to read UTF-8 client config or import') from None
    except (yaml.YAMLError, RecursionError, RuntimeError):
        raise ValueError('invalid client YAML config or import') from None


def load_targets(path):
    """Compatibility accessor; still validate the entire inventory."""
    return load_inventory(path)['targets']

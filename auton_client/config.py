# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit client inventory loading; never selects targets or makes requests."""
from pathlib import Path

import yaml
from yaml.nodes import MappingNode, ScalarNode, SequenceNode

from auton_client.connections import resolve_targets, validate_connection_name

MAX_CONFIG_BYTES = 65536
MAX_CONFIG_TARGETS = 128
MAX_CONFIG_GROUPS = 128
MAX_IMPORT_FILES = 16
CONFIG_SECTIONS = ('targets', 'groups', 'scenarios', 'scenario_groups', 'credentials')
CONFIG_FIELDS = frozenset(CONFIG_SECTIONS + tuple('import_' + section for section in CONFIG_SECTIONS))
MAX_SCENARIO_NODES = 8192
MAX_SCENARIO_DEPTH = 8
INTEGER_TAG = 'tag:yaml.org,2002:int'
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


def _plain_scenario(node, budget, depth=0):
    budget[0] -= 1
    if budget[0] < 0 or depth > MAX_SCENARIO_DEPTH:
        raise ValueError('scenario YAML is too large or deeply nested')
    if isinstance(node, ScalarNode):
        if node.tag == STRING_TAG:
            return node.value
        if node.tag == INTEGER_TAG:
            try:
                return int(node.value)
            except ValueError:
                pass
    elif isinstance(node, SequenceNode) and node.tag == SEQUENCE_TAG:
        return [_plain_scenario(value, budget, depth + 1) for value in node.value]
    elif isinstance(node, MappingNode) and node.tag == MAPPING_TAG:
        return {key: _plain_scenario(value, budget, depth + 1) for key, value in _mapping(node).items()}
    raise ValueError('scenario YAML supports only mappings, lists, strings and integer versions')


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
        credential_directories = {}
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
                    if section == 'credentials':
                        credential_directories.update({name: imported_path.parent for name in values})
            if section in root:
                values = _mapping(root[section])
                if merged.keys() & values.keys():
                    raise ValueError('inline entry duplicates an imported entry')
                merged.update(values)
                if section == 'credentials':
                    credential_directories.update({name: path.parent for name in values})
            sections[section] = merged
        entries = sections['targets']
        if not 1 <= len(entries) <= MAX_CONFIG_TARGETS:
            raise ValueError('client config must declare 1-128 targets')
        node_budget = [MAX_SCENARIO_NODES]
        targets = resolve_targets({name: _plain_scenario(value, node_budget) for name, value in entries.items()})
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
        from auton_client.scenarios import validate_scenarios, validate_scenario_groups
        scenarios = validate_scenarios({name: _plain_scenario(node, node_budget)
                                       for name, node in sections['scenarios'].items()})
        scenario_groups = validate_scenario_groups({name: _plain_scenario(node, node_budget)
                                                   for name, node in sections['scenario_groups'].items()}, scenarios)
        from auton_client.credentials import validate_profiles
        credentials = {}
        if 'credentials' in root or 'import_credentials' in root:
            credentials = validate_profiles(
                {name: _plain_scenario(node, node_budget)
                 for name, node in sections['credentials'].items()}, credential_directories)
        return {'targets': targets, 'groups': groups, 'credentials': credentials,
                'scenarios': scenarios, 'scenario_groups': scenario_groups}
    except (OSError, UnicodeError):
        raise ValueError('unable to read UTF-8 client config or import') from None
    except (yaml.YAMLError, RecursionError, RuntimeError):
        raise ValueError('invalid client YAML config or import') from None


def load_targets(path):
    """Compatibility accessor; still validate the entire inventory."""
    return load_inventory(path)['targets']

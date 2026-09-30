# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit public positional-argument contracts; no shell interpretation."""
import copy
import re

MAX_PARAMETERS = 64
MAX_PARAMETER_TEXT = 512
MAX_VALUE_LENGTH = 4096
MAX_CHOICES = 64
SCHEMA_FIELDS = frozenset(('version', 'args'))
PARAMETER_FIELDS = frozenset(('name', 'type', 'required', 'description', 'choices', 'max_length'))
PARAMETER_TYPES = frozenset(('string', 'integer', 'boolean'))
NAME_PATTERN = re.compile(r'[a-zA-Z][a-zA-Z0-9_-]{0,63}')
INTEGER_PATTERN = re.compile(r'-?(?:0|[1-9][0-9]*)')
BOOLEAN_VALUES = ('true', 'false')


class InvalidParameters(ValueError):
    pass


def validate_parameters(schema):
    if (not isinstance(schema, dict) or set(schema) != SCHEMA_FIELDS
            or type(schema['version']) is not int or schema['version'] != 1
            or not isinstance(schema['args'], list) or len(schema['args']) > MAX_PARAMETERS):
        raise ValueError('parameters require version: 1 and an args list of at most 64 fields')
    names, optional = set(), False
    for field in schema['args']:
        if not isinstance(field, dict) or set(field) - PARAMETER_FIELDS:
            raise ValueError('invalid parameter fields')
        name, kind = field.get('name'), field.get('type', 'string')
        if not isinstance(name, str) or NAME_PATTERN.fullmatch(name) is None or name in names:
            raise ValueError('parameter names must be unique identifiers')
        names.add(name)
        if not isinstance(kind, str) or kind not in PARAMETER_TYPES:
            raise ValueError('parameter type must be string, integer or boolean')
        required = field.get('required', True)
        if type(required) is not bool or optional and required:
            raise ValueError('optional positional parameters must follow required parameters')
        optional |= not required
        description = field.get('description', '')
        if (not isinstance(description, str) or len(description) > MAX_PARAMETER_TEXT
                or any(not char.isprintable() for char in description)):
            raise ValueError('invalid parameter description')
        length = field.get('max_length', MAX_VALUE_LENGTH)
        if type(length) is not int or not 1 <= length <= MAX_VALUE_LENGTH:
            raise ValueError('invalid parameter length limit')
        if 'choices' in field:
            choices = field['choices']
            if (not isinstance(choices, list) or not 1 <= len(choices) <= MAX_CHOICES
                    or any(not isinstance(value, str) for value in choices)
                    or len(set(choices)) != len(choices)):
                raise ValueError('invalid parameter choices')
            for value in choices:
                validate_value(field, value, check_choices=False)
    return copy.deepcopy(schema)


def validate_value(field, value, check_choices=True):
    kind = field.get('type', 'string')
    if (not isinstance(value, str) or '\x00' in value
            or len(value) > field.get('max_length', MAX_VALUE_LENGTH)):
        raise InvalidParameters('invalid value for parameter ' + field['name'])
    if kind == 'integer' and INTEGER_PATTERN.fullmatch(value) is None:
        raise InvalidParameters('parameter ' + field['name'] + ' requires an integer')
    if kind == 'boolean' and value not in BOOLEAN_VALUES:
        raise InvalidParameters('parameter ' + field['name'] + ' requires true or false')
    if check_choices and 'choices' in field and value not in field['choices']:
        raise InvalidParameters('parameter ' + field['name'] + ' is outside the allowed choices')


def validate_arguments(schema, payload):
    if schema is None:
        return
    args = (payload or {}).get('args', [])
    fields = schema['args']
    required = sum(field.get('required', True) for field in fields)
    if not isinstance(args, list) or not required <= len(args) <= len(fields):
        raise InvalidParameters('argument count does not match endpoint parameters')
    for field, value in zip(fields, args):
        validate_value(field, value)

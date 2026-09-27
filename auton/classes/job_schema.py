# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared input contract for HTTP and direct job callers."""

import re
from sonicprobe.libs import xys

ENV_NAME_PATTERN = re.compile(r'[a-zA-Z_][a-zA-Z0-9_]{0,63}')
xys.add_regex('job.envname', ENV_NAME_PATTERN.fullmatch)


class InvalidArgumentsType(Exception):
    pass


class InvalidArguments(Exception):
    pass


RUN_QSCHEMA = xys.load("""
endpoint: !!str
id: !!str
""")

RUN_PSCHEMA = xys.load("""
env*:
  !~~regex? (0,64) job.envname: !!str
envfiles*: !~~seqlen(0,64) [ !!str ]
args*: !~~seqlen(0,64) [ !!str ]
argfiles*: !~~seqlen(0,64)
  - arg: !!str
    content: !!str
    filename: !!str
""")


def validate_input(params, payload=None):
    if not isinstance(params, dict):
        raise InvalidArgumentsType('invalid arguments type')
    if not xys.validate(params, RUN_QSCHEMA):
        raise InvalidArguments('invalid arguments for command')
    if payload is not None:
        if not isinstance(payload, dict):
            raise InvalidArgumentsType('invalid arguments type')
        if not xys.validate(payload, RUN_PSCHEMA):
            raise InvalidArguments('invalid arguments for command')

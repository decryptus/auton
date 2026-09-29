# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded name selectors shared by CLI/TUI and future scenario catalogues."""
import fnmatch
import re
import time

import regex

MAX_PATTERN_LENGTH = 4096
MAX_SELECTORS = 128
MAX_MATCH_SECONDS = 0.05
MAX_SELECTION_SECONDS = 1.0
GLOB_MARKERS = frozenset('*?[')


def is_pattern(value):
    return value.startswith('~') or any(char in value for char in GLOB_MARKERS)


class NameSelector:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.deadline = clock() + MAX_SELECTION_SECONDS
        self.count = 0

    def select(self, expression, names):
        self.count += 1
        if (self.count > MAX_SELECTORS or not isinstance(expression, str)
                or not expression or len(expression) > MAX_PATTERN_LENGTH):
            raise ValueError('invalid or oversized selection')
        try:
            source = expression[1:] if expression.startswith('~') else fnmatch.translate(expression)
            re.compile(source)
            matcher = regex.compile(source, regex.VERSION0)
        except (re.error, regex.error, RecursionError, OverflowError):
            raise ValueError('invalid selection pattern') from None
        matches = []
        for name in names:
            remaining = self.deadline - self.clock()
            if remaining <= 0:
                raise ValueError('selection time budget exceeded')
            try:
                if matcher.match(name, timeout=min(MAX_MATCH_SECONDS, remaining)):
                    matches.append(name)
            except TimeoutError:
                raise ValueError('selection pattern timed out') from None
        if not matches:
            raise ValueError('selection matched no declared names')
        return matches

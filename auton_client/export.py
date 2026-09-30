# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit local export of an operation result, independent of interfaces."""
import json
import os

EXPORT_MODE = 0o600


def export_result(result, path):
    """Create a private JSON file; never overwrite files or follow a final symlink."""
    if not isinstance(result, dict) or not result.get('operation_id'):
        raise ValueError('no completed observation to export')
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, EXPORT_MODE)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            json.dump(result, stream, ensure_ascii=False, allow_nan=False, indent=2)
            stream.write('\n')
    except Exception:
        os.unlink(path)
        raise

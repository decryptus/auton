# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""auton.classes.exceptions"""

import logging

LOG = logging.getLogger('auton.exceptions')


class AutonConfigurationError(Exception):
    pass

class AutonTargetFailed(Exception):
    def __init__(self, message = None, args = None, code = None):
        if isinstance(message, Exception):
            Exception.__init__(self, message.message, message.args)
        else:
            Exception.__init__(self, message, args)

        self.code = code

class AutonTargetTimeout(AutonTargetFailed):
    pass

class AutonTargetUnauthorized(AutonTargetFailed):
    pass

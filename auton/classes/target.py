# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""auton.classes.target"""

import copy
import logging

from dwho.config import load_credentials

LOG             = logging.getLogger('auton.target')

DEFAULT_TIMEOUT = 60


class AutonTarget(object): # pylint: disable=useless-object-inheritance
    def __init__(self,
                 name,
                 config,
                 credentials = None):
        self.name                = name or ''
        self.__config            = copy.copy(config)
        self.__credentials       = copy.copy(credentials)

        self.__config['timeout'] = self.__config.get('timeout') or DEFAULT_TIMEOUT

        if 'credentials' in self.config:
            if self.__config['credentials'] is None:
                self.__credentials = None
            else:
                self.__credentials = load_credentials(self.config['credentials'])
            del self.__config['credentials']

    @property
    def config(self):
        return copy.copy(self._AutonTarget__config) # pylint: disable=no-member

    @config.setter
    def config(self, config): # pylint: disable=unused-argument
        return self

    @property
    def credentials(self):
        return copy.copy(self._AutonTarget__credentials) # pylint: disable=no-member

    @credentials.setter
    def credentials(self, credentials): # pylint: disable=unused-argument
        return self

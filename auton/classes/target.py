# -*- coding: utf-8 -*-
"""auton target"""

__author__  = "Adrien DELLE CAVE <adc@doowan.net>"
__license__ = """
    Copyright (C) 2018  fjord-technologies

    This program is free software; you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation; either version 2 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License along
    with this program; if not, write to the Free Software Foundation, Inc.,
    51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA..
"""

import copy
import logging

from dwho.config import load_credentials

LOG             = logging.getLogger('auton.target')

DEFAULT_TIMEOUT = 60


class AutonTarget(object):
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
        return copy.copy(self._AutonTarget__config)

    @config.setter
    def config(self, config):
        return self

    @property
    def credentials(self):
        return copy.copy(self._AutonTarget__credentials)

    @credentials.setter
    def credentials(self, credentials):
        return self

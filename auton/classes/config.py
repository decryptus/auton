# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""auton.classes.config"""

import logging
import os
import signal

import six
try:
    from six.moves import cStringIO as StringIO
except ImportError:
    from six import StringIO

from dwho.config import import_conf_files, init_modules, parse_conf, stop, DWHO_THREADS
from dwho.classes.libloader import DwhoLibLoader
from dwho.classes.modules import MODULES
from httpdis.httpdis import get_default_options
from mako.template import Template
from sonicprobe.helpers import load_yaml

from auton.classes.exceptions import AutonConfigurationError
from auton.classes.plugins import ENDPOINTS, PLUGINS

_TPL_IMPORTS = ('from os import environ as ENV',
                'from sonicprobe.helpers import to_yaml as my')
LOG          = logging.getLogger('auton.config')


def import_file(filepath, config_dir = None, xvars = None):
    if not xvars:
        xvars = {}

    if config_dir and not filepath.startswith(os.path.sep):
        filepath = os.path.join(config_dir, filepath)

    with open(filepath, 'r') as f:
        return load_yaml(Template(f.read(),
                                  imports = _TPL_IMPORTS).render(**xvars))

def load_conf(xfile, options = None, envvar = None):
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    conf = {'_config_directory': None}

    if os.path.exists(xfile):
        with open(xfile, 'r') as f:
            conf = parse_conf(load_yaml(f))

        conf['_config_directory'] = os.path.dirname(os.path.abspath(xfile))
    elif envvar and os.environ.get(envvar):
        c = StringIO(os.environ[envvar])
        conf = parse_conf(load_yaml(c.getvalue()))
        c.close()
        conf['_config_directory'] = None

    conf = import_conf_files('modules', conf)

    init_modules(conf)

    for x in ('module', 'plugin'):
        path = conf['general'].get('%ss_path' % x)
        if path and os.path.isdir(path):
            DwhoLibLoader.load_dir(x, path)

    if not conf.get('endpoints'):
        raise AutonConfigurationError("Missing 'endpoints' section in configuration")

    for name, ept_cfg in six.iteritems(conf['endpoints']):
        cfg     = {'general':  dict(conf['general']),
                   'auton':    {'endpoint_name': name,
                                'config_dir':    conf['_config_directory']},
                   'config':   {},
                   'users' :   {},
                   'vars':     {}}

        if 'plugin' not in ept_cfg:
            raise AutonConfigurationError("Missing 'plugin' option in endpoint: %r" % name)

        if ept_cfg['plugin'] not in PLUGINS:
            raise AutonConfigurationError("Invalid plugin %r in endpoint: %r"
                                          % (ept_cfg['plugin'],
                                             name))
        cfg['auton']['plugin_name'] = ept_cfg['plugin']

        for x in ('vars', 'config', 'users'):
            if ept_cfg.get("import_%s" % x):
                cfg[x].update(import_file(ept_cfg["import_%s" % x], conf['_config_directory'], cfg))

            if x in ept_cfg:
                cfg[x].update(dict(ept_cfg[x]))

        cfg['credentials'] = None
        if ept_cfg.get('credentials'):
            cfg['credentials'] = ept_cfg['credentials']

        endpoint = PLUGINS[ept_cfg['plugin']](name)
        ENDPOINTS.register(endpoint)
        LOG.info("endpoint init: %r", name)
        endpoint.init(cfg)
        LOG.info("endpoint safe_init: %r", name)
        endpoint.safe_init()
        DWHO_THREADS.append(endpoint.at_stop)

    if not options or not isinstance(options, object):
        return conf

    for def_option in six.iterkeys(get_default_options()):
        if getattr(options, def_option, None) is None \
           and def_option in conf['general']:
            setattr(options, def_option, conf['general'][def_option])

    setattr(options, 'configuration', conf)

    return options


def start_endpoints():
    for name, endpoint in six.iteritems(ENDPOINTS):
        if endpoint.enabled and endpoint.autostart:
            LOG.info("endpoint at_start: %r", name)
            endpoint.at_start()

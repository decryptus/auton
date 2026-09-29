# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""auton.classes.config"""

import logging
import importlib.util
import os
import signal
import sys

import six
try:
    from six.moves import cStringIO as StringIO
except ImportError:
    from six import StringIO

from dwho.config import import_conf_files, init_modules, parse_conf, stop, DWHO_THREADS
from dwho.classes.modules import MODULES
from httpdis.httpdis import get_default_options
from mako.template import Template
from sonicprobe.helpers import load_yaml

from auton.classes.exceptions import AutonConfigurationError
from auton.classes.authentication import apply_auth_policy, PasswordAuthenticator
from auton.classes.job_store import history_config
from auton.classes.web import configure_web
from auton.classes.plugins import ENDPOINTS, PLUGINS
from auton.classes.endpoint_imports import load_endpoint_imports, load_component, COMPONENT_SECTIONS

_TPL_IMPORTS = ('from os import environ as ENV',
                'from sonicprobe.helpers import to_yaml as my')
LOG          = logging.getLogger('auton.config')
DISCOVERY_FIELDS = frozenset(('description',))
MAX_ENDPOINT_DESCRIPTION = 512


def load_extensions(kind, path):
    """Load configured extensions without the removed Python imp module."""
    for filename in sorted(os.listdir(path)):
        if filename.startswith('.') or filename == '__init__.py' or not filename.endswith('.py'):
            continue
        name = '%s.%s' % (kind, filename[:-3])
        if name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(name, os.path.join(path, filename))
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            del sys.modules[name]
            raise


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

    conf['endpoints'], endpoint_sources = load_endpoint_imports(conf)
    conf = import_conf_files('modules', conf)

    apply_auth_policy(conf)
    configure_web(conf)
    storage = history_config(conf['general'], conf.get('_config_directory'))
    if storage is not None:
        conf['general']['job_storage'] = storage
    PasswordAuthenticator.from_file(conf['general'].get('auth_basic_file'),
                                    required=(conf['general'].get('auth_mode') == 'required'
                                              and not conf['general'].get('authentication')))
    init_modules(conf)

    for x in ('module', 'plugin'):
        path = conf['general'].get('%ss_path' % x)
        if path and os.path.isdir(path):
            load_extensions(x, path)

    if not conf.get('endpoints'):
        raise AutonConfigurationError("Missing 'endpoints' section in configuration")

    prepared = []
    for name, ept_cfg in six.iteritems(conf['endpoints']):
        source = endpoint_sources.get(name)
        config_dir = os.path.dirname(source) if source else conf['_config_directory']
        cfg     = {'general':  dict(conf['general']),
                   'auton':    {'endpoint_name': name,
                                'config_dir':    config_dir},
                   'config':   {},
                   'users' :   {},
                   'vars':     {}}

        if 'plugin' not in ept_cfg:
            raise AutonConfigurationError("Missing 'plugin' option in endpoint: %r" % name)

        if ept_cfg['plugin'] not in PLUGINS:
            raise AutonConfigurationError("Invalid plugin %r in endpoint: %r%s"
                                          % (ept_cfg['plugin'],
                                             name, ' in ' + source if source else ''))
        cfg['auton']['plugin_name'] = ept_cfg['plugin']

        for x in COMPONENT_SECTIONS:
            key = 'import_' + x
            if source and key in ept_cfg:
                cfg[x].update(load_component(ept_cfg[key], source, cfg))
            elif ept_cfg.get(key):
                cfg[x].update(import_file(ept_cfg[key], config_dir, cfg))

            if x in ept_cfg:
                cfg[x].update(dict(ept_cfg[x]))

        if 'discovery' in ept_cfg:
            discovery = ept_cfg['discovery']
            if not isinstance(discovery, dict) or set(discovery) - DISCOVERY_FIELDS:
                raise AutonConfigurationError('endpoint discovery accepts only description')
            description = discovery.get('description', '')
            if (not isinstance(description, str) or len(description) > MAX_ENDPOINT_DESCRIPTION
                    or any(not char.isprintable() for char in description)):
                raise AutonConfigurationError('endpoint discovery.description must be printable text of at most 512 characters')
            cfg['discovery'] = {'description': description} if description else {}

        cfg['credentials'] = None
        if ept_cfg.get('credentials'):
            cfg['credentials'] = ept_cfg['credentials']

        prepared.append((name, ept_cfg['plugin'], cfg))

    for name, plugin, cfg in prepared:
        endpoint = PLUGINS[plugin](name)
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

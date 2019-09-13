#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import os
import yaml
from setuptools import find_packages, setup

current_dir              = os.path.abspath(os.path.dirname(__file__))
requirements_auton       = None
requirements_autond      = None
requirements_auton_file  = os.path.join(current_dir, 'requirements-auton.txt')
requirements_autond_file = os.path.join(current_dir, 'requirements-autond.txt')
setup_config             = os.path.join(current_dir, 'setup.yml')
readme_file              = os.path.join(current_dir, 'README.md')
long_desc                = None
long_desc_content_type   = None

if os.path.isfile(requirements_auton_file):
    requirements_auton = [line.strip() for line in open(requirements_auton_file, 'r').readlines()]

if os.path.isfile(requirements_autond_file):
    requirements_autond = [line.strip() for line in open(requirements_autond_file, 'r').readlines()]

if os.path.isfile(setup_config):
    setup_cfg = yaml.safe_load(open(setup_config, 'r').read())

if os.path.isfile(readme_file):
    long_desc = open(readme_file, 'r').read()
    long_desc_content_type = 'text/markdown'

if requirements_auton:
    setup(
        name                          = setup_cfg['auton']['name'],
        version                       = setup_cfg['version'],
        description                   = setup_cfg['auton']['description'],
        author                        = setup_cfg['author'],
        author_email                  = setup_cfg['author_email'],
        license                       = setup_cfg['license'],
        url                           = setup_cfg['url'],
        scripts                       = ['bin/auton'],
        install_requires              = requirements_auton,
        python_requires               = ', '.join(setup_cfg['python_requires']),
        classifiers                   = setup_cfg['common']['classifiers'] + setup_cfg['auton'].get('classifiers', []),
        long_description              = long_desc,
        long_description_content_type = long_desc_content_type
    )

if requirements_autond:
    setup(
        name                          = setup_cfg['autond']['name'],
        version                       = setup_cfg['version'],
        description                   = setup_cfg['autond']['description'],
        author                        = setup_cfg['author'],
        author_email                  = setup_cfg['author_email'],
        license                       = setup_cfg['license'],
        url                           = setup_cfg['url'],
        scripts                       = ['bin/autond'],
        packages                      = find_packages(),
        install_requires              = requirements_autond,
        python_requires               = ', '.join(setup_cfg['python_requires']),
        classifiers                   = setup_cfg['common']['classifiers'] + setup_cfg['autond'].get('classifiers', []),
        long_description              = long_desc,
        long_description_content_type = long_desc_content_type
    )

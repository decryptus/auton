#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import os
from setuptools import find_packages, setup

version                = '0.1.40'

current_dir            = os.path.abspath(os.path.dirname(__file__))

requirements_auton       = None
requirements_autond      = None
version_file             = os.path.join(current_dir, 'VERSION')
readme_file              = os.path.join(current_dir, 'README.md')
requirements_auton_file  = os.path.join(current_dir, 'requirements-auton.txt')
requirements_autond_file = os.path.join(current_dir, 'requirements-autond.txt')
long_desc                = None
long_desc_content_type   = None

if os.path.isfile(requirements_auton_file):
    requirements_auton = [line.strip() for line in open(requirements_auton_file, 'r').readlines()]

if os.path.isfile(requirements_autond_file):
    requirements_autond = [line.strip() for line in open(requirements_autond_file, 'r').readlines()]

if os.path.isfile(version_file):
    version = open(version_file, 'r').readline().strip() or version

if os.path.isfile(readme_file):
    long_desc = open(readme_file, 'r').read()
    long_desc_content_type = 'text/markdown'

if requirements_auton:
    setup(
        name                          = 'auton',
        version                       = version,
        description                   = 'auton-client',
        author                        = 'Adrien Delle Cave',
        author_email                  = 'pypi@doowan.net',
        license                       = 'License GPL-2',
        url                           = 'https://github.com/decryptus/auton',
        scripts                       = ['bin/auton'],
        install_requires              = requirements_auton,
        long_description              = long_desc,
        long_description_content_type = long_desc_content_type
    )

if requirements_autond:
    setup(
        name                          = 'autond',
        version                       = version,
        description                   = 'autond',
        author                        = 'Adrien Delle Cave',
        author_email                  = 'pypi@doowan.net',
        license                       = 'License GPL-2',
        url                           = 'https://github.com/decryptus/auton',
        scripts                       = ['bin/autond'],
        packages                      = find_packages(),
        install_requires              = requirements_autond,
        long_description              = long_desc,
        long_description_content_type = long_desc_content_type
    )

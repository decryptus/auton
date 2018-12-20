# -*- coding: utf-8 -*-
"""auton plugin subproc"""

__author__  = "Adrien DELLE CAVE"
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
import os
import subprocess
import threading
import time
import uuid

from datetime import datetime
from dotenv.main import dotenv_values
from auton.classes.exceptions import AutonConfigurationError
from auton.classes.plugins import AutonPlugBase, AutonTargetFailed, AutonTargetTimeout, PLUGINS

LOG = logging.getLogger('auton.plugins.subproc')

DEFAULT_BECOME_METHOD = 'sudo'
DEFAULT_BECOME_USER   = 'root'
DEFAULT_BECOME_OPTS   = {'sudo': ['-H', '-E']}


class AutonSubProcPlugin(AutonPlugBase):
    PLUGIN_NAME = 'subproc'

    def _proc_stdout(self, obj, proc, texit):
        while True:
            try:
                for x in iter(proc.stdout.readline, b''):
                    if x is not "":
                        obj.add_result(x.rstrip())
            except Exception, e:
                obj.add_error(repr(e))
                LOG.exception(e)
                break
            finally:
                if texit.is_set():
                    break

    def _proc_stderr(self, obj, proc, texit):
        while True:
            try:
                for x in iter(proc.stderr.readline, b''):
                    if x is not "":
                        obj.add_error(x.rstrip())
            except Exception, e:
                obj.add_error(repr(e))
                LOG.exception(e)
                break
            finally:
                if texit.is_set():
                    break

    def _mk_args(self, prog, cargs, pargs, ovars):
        r = [prog]

        if cargs and not isinstance(cargs, list):
            LOG.error("invalid configuration args for target: %r", self.target.name)
            return

        if pargs and not isinstance(pargs, list):
            LOG.error("invalid payload args for target: %r", self.target.name)
            return

        if cargs:
            for x in cargs:
                if isinstance(x, basestring) and '%' in x:
                    x.format(**ovars)
                r.append(x)

        if pargs:
            for x in pargs:
                if isinstance(x, basestring) and '%' in x:
                    x.format(**ovars)
                r.append(x)

        return r

    def _load_envfile(self, envfiles):
        r = {}

        if not isinstance(envfiles, list):
            LOG.error("invalid payload envfiles for target: %r", self.target.name)
            return r

        for envfile in envfiles:
            try:
                r.update(dotenv_values(envfile))
            except Exception, e:
                LOG.warning("unable to load envfile: %r, error: %r", envfile, e)

        return r

    def _mk_env(self, cenv, fenv, penv, ovars):
        r   = {}
        env = []

        if fenv:
            if not isinstance(fenv, list):
                LOG.warning("invalid configuration envfiles for target: %r", self.target.name)
                return r

            for key, val in self._load_envfile(fenv).iteritems():
                env.append({key: val})

        if cenv:
            if isinstance(cenv, dict):
                for key, val in cenv.iteritems():
                    env.append({key: val})
            elif not isinstance(cenv, list):
                LOG.warning("invalid configuration env for target: %r", self.target.name)
                return r
            else:
                env.extend(cenv)

        if penv:
            if not isinstance(penv, dict):
                LOG.warning("invalid payload env for target: %r", self.target.name)
                return r

            r = penv.copy()

        self._build_params_dict('env', env, penv, ovars, r)

        return r

    def _get_become(self, cfg):
        if not isinstance(cfg, dict) or not cfg.get('enabled'):
            return []

        method = cfg.get('method') or DEFAULT_BECOME_METHOD
        become = [method]

        if method in DEFAULT_BECOME_OPTS:
            become += DEFAULT_BECOME_OPTS[method]

        if method == 'sudo':
            become += ['-u', cfg.get('user') or DEFAULT_BECOME_USER]

        return become

    def safe_init(self):
        AutonPlugBase.safe_init(self)

        if not self.target.config.get('prog'):
            raise AutonConfigurationError("missing prog option in target: %r" % self.target.name)

    def do_run(self, obj):
        cfg     = self.target.config
        payload = obj.get_request().payload_params()
        ovars   = obj.get_vars()
        pargs   = None
        fenv    = []
        penv    = {}

        if isinstance(payload, dict) and payload.get('args'):
            if cfg.get('disallow-args'):
                LOG.warning("args from payload isn't allowed for target: %r", self.target.name)
            else:
                pargs = copy.copy(payload['args'])

        args    = self._mk_args(cfg['prog'], cfg.get('args'), pargs, ovars)
        if not args:
            raise AutonTargetFailed("missing args for command on target: %r" % self.target.name)

        if isinstance(payload, dict) and payload.get('envfiles'):
            if cfg.get('disallow-env'):
                LOG.warning("envfile from payload isn't allowed for target: %r", self.target.name)
            else:
                fenv = payload['envfiles']

        if isinstance(payload, dict) and payload.get('env'):
            if cfg.get('disallow-env'):
                LOG.warning("env from payload isn't allowed for target: %r", self.target.name)
            else:
                penv.update(copy.copy(payload['env']))

        env     = self._mk_env(cfg.get('env'), fenv, penv, ovars)
        if not env:
            env = None

        bargs   = self._get_become(cfg.get('become'))

        texit   = threading.Event()
        proc    = None

        try:
            proc  = subprocess.Popen(bargs + args,
                                     stdout = subprocess.PIPE,
                                     stderr = subprocess.PIPE,
                                     env    = env,
                                     cwd    = cfg.get('workdir'))

            to    = threading.Thread(target=self._proc_stdout,
                                     args=(obj, proc, texit))
            to.daemon = True
            to.start()

            te    = threading.Thread(target=self._proc_stderr,
                                     args=(obj, proc, texit))
            te.daemon = True
            te.start()

            start = time.time()

            while True:
                if proc.poll() is not None:
                    break

                if start + cfg['timeout'] <= time.time():
                    raise AutonTargetTimeout("timeout on target: %r" % self.target.name)

            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, args[0])
        except (AutonTargetFailed, AutonTargetTimeout):
            raise
        except Exception, e:
            raise AutonTargetFailed("error on target: %r. exception: %r"
                                    % (self.target.name, e))
        finally:
            texit.set()

        try:
            if proc and proc.returncode is None:
                proc.terminate()
        except OSError:
            pass


if __name__ != "__main__":
    def _start():
        PLUGINS.register(AutonSubProcPlugin)
    _start()

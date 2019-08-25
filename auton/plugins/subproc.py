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
import shutil
import subprocess
import threading
import time
import tempfile

from dotenv.main import dotenv_values

from sonicprobe import helpers
from auton.classes.exceptions import (AutonConfigurationError,
                                      AutonTargetFailed,
                                      AutonTargetTimeout)
from auton.classes.plugins import AutonPlugBase, PLUGINS

try:
    from StringIO import CStringIO as StringIO
except ImportError:
    from six import StringIO

LOG = logging.getLogger('auton.plugins.subproc')


class AutonSubProcPlugin(AutonPlugBase):
    PLUGIN_NAME = 'subproc'

    def __init__(self, name):
        AutonPlugBase.__init__(self, name)
        self._dirs_to_delete = []
        self._killed = False

    def at_stop(self):
        self._killed = True

    def _proc_stdout(self, obj, proc, texit):
        stopped = False
        while not self._killed and not stopped:
            try:
                for x in iter(proc.stdout.readline, b''):
                    if x != '':
                        obj.add_result(x.rstrip())
            except Exception, e:
                obj.add_error(repr(e))
                LOG.exception(e)
                break
            finally:
                if texit.is_set():
                    stopped = True

    def _proc_stderr(self, obj, proc, texit):
        stopped = False
        while not self._killed and not stopped:
            try:
                for x in iter(proc.stderr.readline, b''):
                    if x != '':
                        obj.add_error(x.rstrip())
            except Exception, e:
                obj.add_error(repr(e))
                LOG.exception(e)
                break
            finally:
                if texit.is_set():
                    stopped = True

    def _mk_args(self, args, cargs, pargs, ovars):
        r = copy.copy(args)

        if cargs:
            if not isinstance(cargs, list):
                LOG.error("invalid configuration args for target: %r", self.target.name)
                return None

            for x in cargs:
                if isinstance(x, basestring) and '%' in x:
                    x.format(**ovars)
                r.append(x)

        if pargs:
            if not isinstance(pargs, list):
                LOG.error("invalid payload args for target: %r", self.target.name)
                return None

            for x in pargs:
                if isinstance(x, basestring) and '%' in x:
                    x.format(**ovars)
                r.append(x)

        return r

    def _mk_argfiles(self, args, cargfiles, pargfiles):
        r = copy.copy(args)

        if cargfiles:
            if not isinstance(cargfiles, list):
                LOG.error("invalid configuration argfiles for target: %r", self.target.name)
                return None

            for cargfile in cargfiles:
                if not isinstance(cargfile, dict):
                    LOG.error("invalid type in configuration argfiles for target: %r", self.target.name)
                    return None

                if not cargfile.get('arg'):
                    LOG.error("missing arg in configuration argfiles for target: %r", self.target.name)
                    return None

                if not cargfile.get('filepath'):
                    LOG.error("missing filepath in configuration argfiles for target: %r", self.target.name)
                    return None

                if not os.path.isfile(cargfile['filepath']):
                    LOG.error("invalid filepath in configuration argfiles for target: %r", self.target.name)
                    return None

                if cargfile['arg'].startswith('@'):
                    if len(cargfile['arg']) == 1:
                        LOG.error("invalid arg %r in configuration argfiles for target: %r",
                                  cargfile['arg'],
                                  self.target.name)
                        return None
                    r.extend([cargfile['arg'][1:], "@%s" % cargfile['filepath']])
                else:
                    r.extend([cargfile['arg'], cargfile['filepath']])

        if pargfiles:
            if not isinstance(pargfiles, list):
                LOG.error("invalid payload argfiles for target: %r", self.target.name)
                return None

            tmpdir = tempfile.mkdtemp(prefix = '.auton.')
            self._dirs_to_delete.append(tmpdir)

            for pargfile in pargfiles:
                if pargfile['filename'] == '':
                    with tempfile.NamedTemporaryFile(dir = tmpdir, delete = False) as tmpfile:
                        tmpfile.close()
                    filepath = tmpfile.name
                else:
                    filepath = os.path.join(tmpdir, pargfile['filename'])
                helpers.base64_decode_file(StringIO(pargfile['content']),
                                           filepath)
                if pargfile['arg'].startswith('@'):
                    if len(pargfile['arg']) == 1:
                        LOG.error("invalid arg %r in payload argfiles for target: %r",
                                  pargfile['arg'],
                                  self.target.name)
                        return None
                    r.extend([pargfile['arg'][1:], "@%s" % filepath])
                else:
                    r.extend([pargfile['arg'], filepath])

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

    def safe_init(self):
        AutonPlugBase.safe_init(self)

        if not self.target.config.get('prog'):
            raise AutonConfigurationError("missing prog option in target: %r" % self.target.name)

    def do_run(self, obj):
        cfg       = self.target.config
        payload   = obj.get_request().payload_params()
        ovars     = obj.get_vars()
        pargs     = None
        pargfiles = None
        args      = [cfg['prog']]
        fenv      = []
        penv      = {}

        if isinstance(payload, dict) and payload.get('args'):
            if cfg.get('disallow-args'):
                LOG.warning("args from payload isn't allowed for target: %r", self.target.name)
            else:
                pargs = copy.copy(payload['args'])

        args    = self._mk_args(args, cfg.get('args'), pargs, ovars)

        if isinstance(payload, dict) and payload.get('argfiles'):
            if cfg.get('disallow-argfiles'):
                LOG.warning("argfiles from payload isn't allowed for target: %r", self.target.name)
            else:
                pargfiles = copy.copy(payload['argfiles'])

        args    = self._mk_argfiles(args, cfg.get('argfiles'), pargfiles)

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
            env = {}

        env     = self._set_default_env(env, ovars)

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
        except subprocess.CalledProcessError, e:
            raise AutonTargetFailed("error on target: %r. exception: %s"
                                    % (self.target.name, e), code = e.returncode)
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

    def do_terminate(self):
        while self._dirs_to_delete:
            tdir = self._dirs_to_delete.pop()
            if os.path.isdir(tdir):
                shutil.rmtree(tdir, True)


if __name__ != "__main__":
    def _start():
        PLUGINS.register(AutonSubProcPlugin)
    _start()

# -*- coding: utf-8 -*-
# Copyright (C) 2018-2022 fjord-technologies
# SPDX-License-Identifier: GPL-3.0-or-later
"""auton.plugins.subproc"""

import copy
import logging
import math
import os
import signal
import selectors
import codecs
import shutil
import subprocess
import time
import tempfile
import threading

import six
try:
    from six.moves import cStringIO as StringIO
except ImportError:
    from six import StringIO

from dotenv.main import dotenv_values

from sonicprobe import helpers
from auton.classes.exceptions import (AutonConfigurationError,
                                      AutonTargetFailed,
                                      AutonTargetTimeout)
from auton.classes.plugins import AutonPlugBase, PLUGINS

LOG = logging.getLogger('auton.plugins.subproc')


class AutonSubProcPlugin(AutonPlugBase):
    PLUGIN_NAME = 'subproc'

    def __init__(self, name):
        AutonPlugBase.__init__(self, name)
        self._dirs_to_delete = []
        self._killed = False

    def at_stop(self):
        self._killed = True
        if self.is_alive() and threading.current_thread() is not self:
            self.join(timeout=3)

    @staticmethod
    def _signal_process_group(proc, sig):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            pass

    def _collect(self, obj, proc, timeout):
        """Drain both pipes, including partial lines, within a monotonic deadline."""
        deadline = time.monotonic() + timeout
        with selectors.DefaultSelector() as selector:
            for pipe, callback in ((proc.stdout, obj.add_result),
                                   (proc.stderr, obj.add_error)):
                os.set_blocking(pipe.fileno(), False)
                decoder = codecs.getincrementaldecoder('utf-8')(errors='replace')
                selector.register(pipe, selectors.EVENT_READ, (callback, decoder))
            while selector.get_map() or proc.poll() is None:
                if self._killed:
                    raise AutonTargetFailed('daemon stopping', code=130)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AutonTargetTimeout("timeout on target: %r" % self.target.name,
                                            code=124)
                for key, _ in selector.select(min(remaining, 0.1)):
                    data = os.read(key.fd, 65536)
                    callback, decoder = key.data
                    value = decoder.decode(data, final=not data)
                    if value:
                        callback(value)
                    if not data:
                        selector.unregister(key.fileobj)

    def _mk_args(self, args, cargs, pargs, ovars):
        r = copy.copy(args)

        if cargs:
            if not isinstance(cargs, list):
                LOG.error("invalid configuration args for target: %r", self.target.name)
                return None

            for x in cargs:
                if not isinstance(x, six.string_types):
                    LOG.error("invalid configuration argument %r for target: %r", x, self.target.name)
                    return None

                if '{' in x and '}' in x:
                    x = x.format(**ovars)
                r.append(x)

        if pargs:
            if not isinstance(pargs, list):
                LOG.error("invalid payload args for target: %r", self.target.name)
                return None

            for x in pargs:
                if not isinstance(x, six.string_types):
                    LOG.error("invalid payload argument %r for target: %r", x, self.target.name)
                    return None

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

                if cargfile['arg'].endswith('@'):
                    if len(cargfile['arg']) == 1:
                        LOG.error("invalid arg %r in configuration argfiles for target: %r",
                                  cargfile['arg'],
                                  self.target.name)
                        return None
                    r.extend([cargfile['arg'][:-1], "@%s" % cargfile['filepath']])
                else:
                    r.extend([cargfile['arg'], cargfile['filepath']])

        if pargfiles:
            if not isinstance(pargfiles, list):
                LOG.error("invalid payload argfiles for target: %r", self.target.name)
                return None

            tmpdir = tempfile.mkdtemp(prefix = '.auton.')
            self._dirs_to_delete.append(tmpdir)

            for pargfile in pargfiles:
                filename = pargfile['filename']
                if (not isinstance(filename, six.string_types)
                        or filename in ('.', '..')
                        or '/' in filename or '\\' in filename or '\x00' in filename):
                    raise AutonTargetFailed('invalid upload filename', code=1)
                if pargfile['filename'] == '':
                    with tempfile.NamedTemporaryFile(dir = tmpdir, delete = False) as tmpfile:
                        tmpfile.close()
                    filepath = tmpfile.name
                else:
                    filepath = os.path.join(tmpdir, pargfile['filename'])
                helpers.base64_decode_file(StringIO(pargfile['content']),
                                           filepath)
                if pargfile['arg'].endswith('@'):
                    if len(pargfile['arg']) == 1:
                        LOG.error("invalid arg %r in payload argfiles for target: %r",
                                  pargfile['arg'],
                                  self.target.name)
                        return None
                    r.extend([pargfile['arg'][:-1], "@%s" % filepath])
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
            except Exception as e:
                LOG.warning("unable to load envfile: %r, error: %r", envfile, e)

        return r

    def _mk_env(self, cenvfiles, penvfiles, cenv, penv, ovars):
        r   = {}
        env = []

        if penvfiles:
            if not isinstance(penvfiles, list):
                LOG.warning("invalid payload envfiles for target: %r", self.target.name)
                return r

            for key, val in six.iteritems(self._load_envfile(penvfiles)):
                env.append({key: val})

        if cenvfiles:
            if not isinstance(cenvfiles, list):
                LOG.warning("invalid configuration envfiles for target: %r", self.target.name)
                return r

            for key, val in six.iteritems(self._load_envfile(cenvfiles)):
                env.append({key: val})

        if cenv:
            if isinstance(cenv, dict):
                for key, val in six.iteritems(cenv):
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

        timeout = self.target.config['timeout']
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise AutonConfigurationError('timeout must be a finite positive number')

        if not self.target.config.get('prog'):
            raise AutonConfigurationError("missing prog keyword for target: %r" % self.target.name)

    def do_run(self, obj):
        cfg       = self.target.config
        payload   = obj.get_request().payload_params()
        ovars     = obj.get_vars()
        pargs     = None
        pargfiles = None
        args      = [cfg['prog']]
        penvfiles = []
        penv      = {}

        if isinstance(payload, dict) and payload.get('args'):
            if cfg.get('disallow-args'):
                LOG.warning("args from payload isn't allowed for target: %r", self.target.name)
            else:
                pargs = copy.copy(payload['args'])

        args    = self._mk_args(args, cfg.get('args'), pargs, ovars)
        if not args:
            raise AutonTargetFailed("invalid args for command on target: %r" % self.target.name)

        if isinstance(payload, dict) and payload.get('argfiles'):
            if cfg.get('disallow-argfiles'):
                LOG.warning("argfiles from payload isn't allowed for target: %r", self.target.name)
            else:
                pargfiles = copy.copy(payload['argfiles'])

        args    = self._mk_argfiles(args, cfg.get('argfiles'), pargfiles)
        if not args:
            raise AutonTargetFailed("invalid argfiles for command on target: %r" % self.target.name)

        if isinstance(payload, dict) and payload.get('envfiles'):
            if cfg.get('disallow-envfiles'):
                LOG.warning("envfile from payload isn't allowed for target: %r", self.target.name)
            else:
                penvfiles = payload['envfiles']

        if isinstance(payload, dict) and payload.get('env'):
            if cfg.get('disallow-env'):
                LOG.warning("env from payload isn't allowed for target: %r", self.target.name)
            else:
                penv.update(copy.copy(payload['env']))

        env     = self._mk_env(cfg.get('envfiles'), penvfiles, cfg.get('env'), penv, ovars)
        if not env:
            env = {}

        if cfg.get('search_paths'):
            if not isinstance(cfg['search_paths'], list):
                LOG.warning("invalid search_paths for target: %r", self.target.name)
            else:
                env['PATH'] = os.path.pathsep.join(cfg['search_paths'])

        env     = self._set_default_env(env, ovars)

        bargs   = self._get_become(cfg.get('become'))

        proc    = None

        LOG.debug("cmd line: %r", bargs + args)

        try:
            proc  = subprocess.Popen(bargs + args,
                                     stdout = subprocess.PIPE,
                                     stderr = subprocess.PIPE,
                                     env    = env,
                                     cwd    = cfg.get('workdir'),
                                     start_new_session = True)

            self._collect(obj, proc, cfg['timeout'])

            if proc.returncode:
                raise subprocess.CalledProcessError(proc.returncode, args[0])
        except (AutonTargetFailed, AutonTargetTimeout):
            raise
        except subprocess.CalledProcessError as e:
            raise AutonTargetFailed("error on target: %r. exception: %s"
                                    % (self.target.name, e), code = e.returncode)
        except Exception as e:
            raise AutonTargetFailed("error on target: %r. exception: %r"
                                    % (self.target.name, e))
        finally:
            if proc is not None:
                # Kill the session's remaining children too, even if the leader exited.
                try:
                    self._signal_process_group(proc, signal.SIGTERM)
                    try:
                        proc.wait(timeout=1)
                    except subprocess.TimeoutExpired:
                        pass
                finally:
                    self._signal_process_group(proc, signal.SIGKILL)
                    proc.wait()
                    proc.stdout.close()
                    proc.stderr.close()

    def do_terminate(self):
        while self._dirs_to_delete:
            tdir = self._dirs_to_delete.pop()
            if os.path.isdir(tdir):
                shutil.rmtree(tdir, True)


if __name__ != "__main__":
    def _start():
        PLUGINS.register(AutonSubProcPlugin)
    _start()

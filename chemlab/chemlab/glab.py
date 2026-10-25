"""The generic lab, assembled: lab backend (1-203 instruments) + generic commands + the pilot's socket server, client and
sandbox (reused unchanged).

    lab = GenericLab(run_dir='build/labs/demo', config='configs/all203.json').start()
    r = lab.sh('chemlab list')                       # inside the sandbox, through the client and the socket
    r = lab.chemlab('describe', 'ika_plate_rctdigital')   # in-process, same core (tests, trusted host)
    lab.backend.evaluator_read('ika_plate_rctdigital')    # evaluator view: host side only, never through the socket
    lab.stop()

The shell runs in the agent sandbox (sandbox.py: Landlock + seccomp; Linux x86_64).  ``sandbox=False`` is an explicit opt-in for
local, trusted use on a machine without the sandbox: the same client and socket, but the shell runs as the calling user with
no isolation (it can read the source, the contracts and the evaluator data, and use the network).  A warning is printed.
"""
import os
import secrets
import signal
import subprocess
import sys
import time
from pathlib import Path

from .generic import GenericCore
from .lab import ShellResult, build_client
from .labbackend import LabBackend, load_config
from .sandbox import require as require_sandbox, sandbox_argv, sandbox_env
from .server import LabServer

NO_SANDBOX_WARNING = ('WARNING: chemlab is running WITHOUT the agent sandbox (--no-sandbox). Shell commands run as you, with no '
                      'isolation: they can read the source, the contracts and the evaluator data, change files and use the '
                      'network. Use this only for local, trusted use; never for evaluating an agent.')


class GenericLab:
    def __init__(self, run_dir, config=None, *, callers=('agent',), setup=True, step_s=None, adapters=None, max_wait_s=86400.0,
                 sandbox=True):
        self.run_dir = Path(run_dir).resolve()
        self.work = self.run_dir / 'work'
        self.work.mkdir(parents=True, exist_ok=True)
        cfg = load_config(config)
        self.backend = LabBackend(cfg, run_dir=self.run_dir, callers=callers, step_s=step_s, setup=setup)
        self.core = GenericCore(self.backend, work_dir=self.work, log_path=str(self.run_dir / 'core_log.jsonl'), adapters=adapters,
                                max_wait_s=max_wait_s)
        self.tokens = {c: secrets.token_hex(16) for c in callers}
        self.server = LabServer(self.core, self.run_dir, {t: c for c, t in self.tokens.items()})
        self.sandbox = bool(sandbox)
        self._bindir = None
        self.transcript = []
        if not self.sandbox:
            print(NO_SANDBOX_WARNING, file=sys.stderr)

    @property
    def bindir(self):
        """build/bin with the chemlab client (compiled on first use; the MCP and in-process paths do not need it)."""
        if self._bindir is None:
            self._bindir = str(build_client())
        return self._bindir

    def start(self):
        self.server.start()
        return self

    def stop(self):
        self.server.stop()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def env(self, caller='agent'):
        return sandbox_env(str(self.work), self.bindir, {'CHEMLAB_SOCKET': self.server.path, 'CHEMLAB_TOKEN': self.tokens[caller]})

    def shell_argv(self, command):
        """argv that runs ``command`` with bash: inside the sandbox, or (opt-in, sandbox=False) as a plain bash in the work dir."""
        if self.sandbox:
            require_sandbox()
            return sandbox_argv(str(self.work), self.bindir, command)
        return ['/usr/bin/env', 'bash', '-c', 'cd "$HOME" && ' + command]

    def sh(self, command, caller='agent', timeout=600):
        """Run a bash command inside the sandbox, as an agent would."""
        t0 = time.monotonic()
        p = subprocess.Popen(self.shell_argv(command), env=self.env(caller), stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, start_new_session=True)
        timed_out = False
        try:
            out, err = p.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(p.pid, signal.SIGKILL)                                # our own child's process group
            out, err = p.communicate()
        r = ShellResult(command, p.returncode, out.decode('utf-8', 'replace'), err.decode('utf-8', 'replace'), time.monotonic() - t0, timed_out)
        self.transcript.append({'caller': caller, 'command': command, 'exit': r.exit_code, 'stdout': r.out, 'stderr': r.err,
                                'wall_s': round(r.wall_s, 3), 'timed_out': timed_out, 'sim_time_s': self.backend.now()})
        return r

    def chemlab(self, *args, caller='agent'):
        """The same core in-process (no sandbox, no socket): for tests and the trusted host."""
        return self.core.handle(['chemlab'] + list(args), caller)


def single(instrument_ids, config=None):
    """A lab config with only some instruments of a config (default: all203)."""
    cfg = load_config(config)
    want = [instrument_ids] if isinstance(instrument_ids, str) else list(instrument_ids)
    cfg['instruments'] = [e for e in cfg['instruments'] if e['id'] in want]
    missing = set(want) - {e['id'] for e in cfg['instruments']}
    if missing:
        raise KeyError('not in the config: %s' % ', '.join(sorted(missing)))
    return cfg

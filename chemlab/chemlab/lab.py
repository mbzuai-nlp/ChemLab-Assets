"""Putting the layers together for one instrument session: backend + core service + socket server + sandboxed shell.

    lab = Lab(deck, run_dir, speed=20).start()
    r = lab.sh('chemlab run flex power on')      # runs inside the sandbox, through the client and the socket
    state = lab.backend.evaluator_state()        # the evaluator view (checkers only; never reachable from the shell)
    lab.stop()
"""
import os
import secrets
import shutil
import signal
import subprocess
import time
from pathlib import Path

from .backend import FastSimFlexBackend
from .core import CoreService
from .flex_verbs import FlexInstrument
from .sandbox import require as require_sandbox, sandbox_argv, sandbox_env
from .server import LabServer

CHEMLAB_ROOT = Path(__file__).resolve().parents[1]
CLIENT_SRC = CHEMLAB_ROOT / 'client' / 'chemlab_client.c'
BIN_DIR = CHEMLAB_ROOT / 'build' / 'bin'


def build_client(force=False):
    """Compile the thin client into build/bin/chemlab (static if possible).  Returns the bin directory."""
    out = BIN_DIR / 'chemlab'
    if not force and out.exists() and out.stat().st_mtime >= CLIENT_SRC.stat().st_mtime:
        return BIN_DIR
    BIN_DIR.mkdir(parents=True, exist_ok=True)
    cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('gcc') or shutil.which('clang')
    if cc is None:
        raise RuntimeError('a C compiler (cc, gcc or clang; or set CC) is needed once to build the chemlab command-line client '
                           '%s into %s. Install one (e.g. "apt install gcc"), or build it elsewhere and copy the binary there.'
                           % (CLIENT_SRC, out))
    tmp = out.with_suffix('.tmp')
    r = subprocess.run([cc, '-O2', '-Wall', '-static', '-o', str(tmp), str(CLIENT_SRC)], capture_output=True, text=True)
    if r.returncode != 0:
        r = subprocess.run([cc, '-O2', '-Wall', '-o', str(tmp), str(CLIENT_SRC)], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError('building the chemlab client with %s failed:\n%s' % (cc, r.stderr))
    os.replace(tmp, out)
    return BIN_DIR


class ShellResult:
    def __init__(self, command, exit_code, out, err, wall_s, timed_out=False):
        self.command, self.exit_code, self.out, self.err, self.wall_s, self.timed_out = command, exit_code, out, err, wall_s, timed_out

    def __repr__(self):
        return 'ShellResult(exit=%s, out=%r, err=%r)' % (self.exit_code, self.out[-200:], self.err[-200:])


class Lab:
    def __init__(self, deck, run_dir, *, faults=None, protocols=(), speed=None, callers=('agent',), lease_s=120.0):
        self.run_dir = Path(run_dir).resolve()
        self.work = self.run_dir / 'work'
        self.work.mkdir(parents=True, exist_ok=True)
        self.backend = FastSimFlexBackend(deck, faults=faults, protocols=protocols)
        self.core = CoreService(self.backend, FlexInstrument, log_path=str(self.run_dir / 'core_log.jsonl'), lease_s=lease_s)
        self.tokens = {c: secrets.token_hex(16) for c in callers}
        self.server = LabServer(self.core, self.run_dir, {t: c for c, t in self.tokens.items()})
        self.speed = speed
        self.bindir = str(build_client())
        self.transcript = []

    def start(self):
        self.server.start()
        if self.speed:
            self.backend.start_clock(self.speed)
        return self

    def stop(self):
        self.backend.stop_clock()
        self.server.stop()

    def __enter__(self):
        return self.start()

    def __exit__(self, *exc):
        self.stop()

    def env(self, caller='agent'):
        return sandbox_env(str(self.work), self.bindir, {'CHEMLAB_SOCKET': self.server.path, 'CHEMLAB_TOKEN': self.tokens[caller]})

    def sh(self, command, caller='agent', timeout=600):
        """Run a bash command inside the sandbox (as an agent would)."""
        require_sandbox()
        t0 = time.monotonic()
        p = subprocess.Popen(sandbox_argv(str(self.work), self.bindir, command), env=self.env(caller), stdout=subprocess.PIPE,
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
        """Call the core directly (in-process, no sandbox) - for tests and the trusted host."""
        return self.core.handle(['chemlab'] + list(args), caller)

import json
import os
import shlex
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

RUNS = os.path.join(ROOT, 'build', 'test_runs_generic')


def host_paths():
    """Paths of this machine that must never appear in agent-visible output: the project folder, the home directory and the
    folder holding the home directories (when it is not '/'), plus the temp dir prefix and the runtime source folder name."""
    from pathlib import Path
    from chemlab.hosts import CHEMSIM_ROOT
    roots = {str(CHEMSIM_ROOT), str(Path.home()), str(Path.home().parent)}
    return tuple(sorted(p for p in roots if p.strip('/'))) + ('/tmp/', 'Chemistry-Sim/scripts')


def host_path_re():
    import re
    return '|'.join(re.escape(p) for p in host_paths())


def tmpdir():
    os.makedirs(RUNS, exist_ok=True)
    return tempfile.mkdtemp(dir=RUNS)


def run(lab, cmd, caller='agent'):
    """In-process call of the generic core (same core the socket serves)."""
    return lab.core.handle(['chemlab'] + shlex.split(cmd), caller)


def run_json(lab, cmd, caller='agent'):
    r = run(lab, cmd + ' --json', caller)
    return r.exit_code, json.loads(r.out)

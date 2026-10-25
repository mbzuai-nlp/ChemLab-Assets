"""Lowering check: per class, per verb, per member instrument.

For every step of a member's case list, a class verb runs through chemlab (lab A, adapters on) and

1. the native calls it made (capability + arguments in SI, in order) must equal the expected calls written by hand in the class's
   lowering table (tasks/<class>_lowering.py) - not derived from the mapping;
2. the same native calls (same request ids) and clock advances are then made directly on a twin lab B (LabBackend, same config,
   no chemlab), and the instrument's full evaluator view must be equal on A and B after every step.

    python -m chemlab.compile.lowering --class balance [--only mt_xpr204e]

Writes compile_results/<class>/lowering.json.  A case list: {'prep': fn(backend) | None, 'steps': [(command, expected)]},
command without "chemlab run INST"; expected = [(capability, {arguments})], [] for "no native call", None (not checked), a
callable(got calls) -> bool, or (capability, callable(arguments) -> bool) items for arguments that are measured arrays.  Inside
expected arguments, matchers stand for measured values: ``Measured()`` (any finite number, or a non-empty list / object),
``Obs(n, fixed, measured)`` (a list of n objects with fixed fields and measured fields).  Special steps: ``('#wait N', None)``
moves lab time on both labs; ``('#host', fn)`` (or ``('#host what', fn)``) runs ``fn(backend)`` on the lab and on the twin (the
lab operator swaps a sample, refills a reservoir ...) and compares their states.
"""
import argparse
import copy
import importlib
import json
import math
import shlex
import shutil
import sys
import time
from pathlib import Path

from ..glab import GenericLab, single
from ..labbackend import LabBackend, load_config

CHEMLAB_ROOT = Path(__file__).resolve().parents[2]
RESULTS = CHEMLAB_ROOT / 'compile_results'
RUNS = CHEMLAB_ROOT / 'build' / 'lowering_runs'
TABLE_MODULE = 'chemlab.compile.tasks.%s_lowering'      # a class's lowering table, found by convention (no shared registry)


def norm(o):
    """JSON-normal form with floats rounded to 12 significant digits (the same arithmetic gives the same digits)."""
    if isinstance(o, float):
        return float('%.12g' % o) if math.isfinite(o) else str(o)
    if isinstance(o, dict):
        return {str(k): norm(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [norm(v) for v in o]
    return o


class Matcher:
    """An expected value that matches a family of values (subclasses define match(value))."""

    def match(self, value):
        raise NotImplementedError

    def __eq__(self, other):
        return self.match(other)

    def __ne__(self, other):
        return not self.match(other)

    __hash__ = None


def _measured(v):
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return math.isfinite(v)
    return isinstance(v, (list, dict)) and bool(v)


class Measured(Matcher):
    """Any measured value: a finite number, or a non-empty list / object (``Measured(list)``: a non-empty list only)."""

    def __init__(self, kind=None):
        self.kind = kind

    def match(self, value):
        return _measured(value) and (self.kind is None or isinstance(value, self.kind))

    def __repr__(self):
        return 'Measured(%s)' % (self.kind.__name__ if self.kind else '')


class Obs(Matcher):
    """A list of observations matched by shape: ``n`` items; ``fixed`` fields equal per item (a dict for all, or a list of dicts,
    compared with same_args); ``measured`` fields present and measured (numbers finite, lists non-empty lists of objects).
    (Used by the quant_analyzer lowering table.)"""

    def __init__(self, n, fixed=None, measured=()):
        self.n, self.fixed, self.measured = n, fixed or {}, list(measured)

    def match(self, other):
        if not isinstance(other, list) or len(other) != self.n:
            return False
        for i, o in enumerate(other):
            if not isinstance(o, dict):
                return False
            fx = self.fixed[i] if isinstance(self.fixed, list) else self.fixed
            if any(not same_args(o.get(k), v) for k, v in fx.items()):
                return False
            for k in self.measured:
                v = o.get(k)
                if isinstance(v, list):
                    if not v or not all(isinstance(x, dict) for x in v):
                        return False
                elif not _measured(v):
                    return False
        return True

    def __repr__(self):
        return 'Obs(%d x %s + measured %s)' % (self.n, self.fixed, self.measured)

    __str__ = __repr__


def same_args(a, b, rel=1e-9):
    if isinstance(b, Matcher):
        return b.match(a)
    if isinstance(a, Matcher):
        return a.match(b)
    if isinstance(a, dict) and isinstance(b, dict):
        return set(a) == set(b) and all(same_args(a[k], b[k], rel) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(same_args(x, y, rel) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(a - b) <= rel * max(1.0, abs(a), abs(b))
    return a == b


def calls_match(got, expected):
    """expected: None (not checked), a callable(got) -> bool, or a list of (capability, arguments) where arguments may be a
    dict (compared with same_args) or a callable(arguments) -> bool (e.g. for measured arrays in analysis steps)."""
    if expected is None:
        return True
    if callable(expected):
        return bool(expected(got))
    if len(got) != len(expected):
        return False
    for g, e in zip(got, expected):
        if g[0] != e[0]:
            return False
        if callable(e[1]):
            if not e[1](g[1]):
                return False
        elif not same_args(g[1], e[1]):
            return False
    return True


def ev_obs(backend, iid):
    r = backend.evaluator_read(iid)
    return norm({k: (o.get('value') if isinstance(o, dict) else o) for k, o in (r.get('observations') or {}).items()})


def check_member(iid, case, config=None, log=print, run_root=None):
    cfg = single([iid], config or load_config())
    run = Path(run_root or RUNS) / iid
    if run.exists():
        shutil.rmtree(run)
    lab = GenericLab(run / 'chemlab', cfg)
    twin = LabBackend(cfg, run_dir=run / 'twin')
    if case.get('prep'):
        case['prep'](lab.backend)
        case['prep'](twin)
    rows = []
    base = ev_obs(lab.backend, iid) == ev_obs(twin, iid)
    for cmd, expected in case['steps']:
        if cmd.startswith('#host'):                                         # the lab operator changes the world (both labs)
            expected(lab.backend)
            expected(twin)
            a, b = ev_obs(lab.backend, iid), ev_obs(twin, iid)
            diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            rows.append({'command': cmd, 'exit': 0, 'outcome': 'host', 'native_calls': [], 'expected': None, 'calls_ok': True,
                         'state_equal': not diff, 'diff': diff[:10], 'time_s': lab.backend.now()})
            continue
        if cmd.startswith('#wait '):                                        # lab time passes (both labs), no command
            s = float(cmd.split()[1])
            lab.backend.advance(s)
            twin.advance(s)
            a, b = ev_obs(lab.backend, iid), ev_obs(twin, iid)
            diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
            rows.append({'command': cmd, 'exit': 0, 'outcome': 'wait', 'native_calls': [], 'expected': None, 'calls_ok': True,
                         'state_equal': not diff, 'diff': diff[:10], 'time_s': lab.backend.now()})
            continue
        n = len(lab.core.log.entries)
        r = lab.core.handle(['chemlab', 'run', iid] + shlex.split(cmd), 'agent')
        entry = lab.core.log.entries[n]
        ops = [c for c in entry['backend_calls'] if c.get('op') in ('invoke', 'write', 'advance')]
        got = [(c['capability'], c['arguments']) for c in ops if c['op'] == 'invoke']
        calls_ok = calls_match(got, expected)
        for c in ops:                                                       # the same native calls, directly, on the twin
            if c['op'] == 'invoke':
                twin.invoke('agent', iid, c['capability'], copy.deepcopy(c['arguments']), c['request_id'])
            elif c['op'] == 'write':
                twin.write('agent', iid, c['state_id'], copy.deepcopy(c['value']), c['request_id'])
            else:
                twin.advance(c['seconds'])
        a, b = ev_obs(lab.backend, iid), ev_obs(twin, iid)
        diff = sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))
        shown = repr(expected) if callable(expected) else (None if expected is None else
                                                            [(e[0], repr(e[1]) if callable(e[1]) else e[1]) for e in expected])
        row = {'command': cmd, 'exit': r.exit_code, 'outcome': entry['outcome'], 'native_calls': got,
               'expected': shown, 'calls_ok': calls_ok, 'state_equal': not diff, 'diff': diff[:10],
               'time_s': lab.backend.now()}
        rows.append(row)
        log('  %-40s exit %d %-22s calls %s  state %s%s' % (cmd[:40], r.exit_code, entry['outcome'][:22], 'ok' if calls_ok else 'DIFF %s' % got,
                                                     'equal' if not diff else 'DIFF', ('  ' + ', '.join(diff[:4])) if diff else ''))
    path_problems = path_check(lab, iid, rows)
    lab.stop()
    return {'instrument': iid, 'start_equal': base, 'steps': rows, 'path_problems': path_problems,
            'ok': base and not path_problems and all(x['calls_ok'] and x['state_equal'] for x in rows)}


def path_check(lab, iid, rows):
    """One native path per verb group, seen in the calls actually made: every capability used by the verbs of a PATH_GROUPS group
    shares the member's path namespace (or is a listed exception)."""
    ad = lab.core.adapters.get(iid)
    parts = getattr(ad, 'parts', [ad]) if ad is not None else []
    out = []
    for part in parts:
        for group in getattr(part, 'PATH_GROUPS', ()):
            # only the rows whose verb this part owns (on a composite instrument a class part may own result, the shared part start)
            used = [(r['command'].split()[0], c[0]) for r in rows for c in r['native_calls'] if r['command'].split()[0] in group
                    and (ad.owner(r['command'].split()[0]) if hasattr(ad, 'owner') else ad) is part]
            if not used:
                continue
            path = part.map.get('path') or used[0][1].split('.', 1)[0]
            allowed = set(part.map.get('path_exceptions') or [])
            out += ['%s used %s outside the %s path' % (v, c, path) for v, c in used if c.split('.', 1)[0] != path and c not in allowed]
    return out


def run_class(cls, only=None, log=print, write=True):
    mod = importlib.import_module(TABLE_MODULE % cls)
    out = {'class': cls, 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'members': []}
    for iid, case in mod.CASES.items():
        if only and iid not in only:
            continue
        log('%s (%d steps)' % (iid, len(case['steps'])))
        out['members'].append(check_member(iid, case, config=getattr(mod, 'config', lambda: None)(), log=log,
                                           run_root=RUNS / cls))           # per class: classes sharing a member never collide
    out['passed'] = bool(out['members']) and all(m['ok'] for m in out['members'])
    out['steps'] = sum(len(m['steps']) for m in out['members'])
    out['native_calls'] = sum(len(s['native_calls']) for m in out['members'] for s in m['steps'])
    if write and not only:
        (RESULTS / cls).mkdir(parents=True, exist_ok=True)
        (RESULTS / cls / 'lowering.json').write_text(json.dumps(out, ensure_ascii=False, indent=1, default=str) + '\n')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--class', dest='cls', required=True, help='a class name (tasks/<class>_lowering.py)')
    ap.add_argument('--only', nargs='*')
    a = ap.parse_args(argv)
    out = run_class(a.cls, a.only)
    print('%s: %s (%d members, %d steps, %d native calls)' % (a.cls, 'PASS' if out['passed'] else 'FAIL', len(out['members']), out['steps'],
                                                              out['native_calls']))
    return 0 if out['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())

"""Task harness for the class acceptance suites (the evaluator side; never reachable from the sandbox).

A task is a dict:

    {'id': 'b01_tare_beaker', 'kind': 'single step', 'class': 'balance',
     'instruments': ['mt_xpr204e'],                  # lab = these entries of all203.json (+ 'config': fn(cfg) to change them)
     'world': fn(lab),                               # trusted host set-up after the lab is ready (loads, faults, time)
     'instruction': '...',                           # what the agent is told
     'gold': 'chemlab run mt_xpr204e tare --wait 30s',   # bash, run inside the sandbox with `chemlab run`
     'check': fn(c)}                                 # c: Check (evaluator view + the agent's work files)

run_task() runs a solution (the gold, or '' for NoAct) through the sandboxed client and judges it; run_suite() runs gold and
NoAct for every task and writes compile_results/<class>/ (transcripts, core logs, checks, summary.md / summary.json).
"""
import json
import math
import shutil
import time
from pathlib import Path

from ...glab import GenericLab, single
from ...labbackend import load_config

CHEMLAB_ROOT = Path(__file__).resolve().parents[3]
RESULTS = CHEMLAB_ROOT / 'compile_results'


class Check:
    def __init__(self, task, lab):
        self.task, self.lab = task, lab
        self.items = []
        self._ev = {}

    def ev(self, iid):
        """Evaluator view of one instrument: {slot: value} (includes evaluator-only and truth slots)."""
        if iid not in self._ev:
            r = self.lab.backend.evaluator_read(iid)
            self._ev[iid] = {k: (o.get('value') if isinstance(o, dict) else o) for k, o in (r.get('observations') or {}).items()}
        return self._ev[iid]

    def add(self, name, ok, detail=''):
        self.items.append({'check': name, 'ok': bool(ok), 'detail': str(detail)[:300]})
        return ok

    def near(self, name, got, want, tol):
        ok = isinstance(got, (int, float)) and not isinstance(got, bool) and math.isfinite(got) and abs(got - want) <= tol
        return self.add(name, ok, 'got %r, want %r ± %r' % (got, want, tol))

    def equal(self, name, got, want):
        return self.add(name, got == want, 'got %r, want %r' % (got, want))

    def work_file(self, name):
        p = self.lab.work / name
        return p.read_text(encoding='utf-8', errors='replace').strip() if p.is_file() else None

    def number_file(self, name):
        t = self.work_file(name)
        try:
            return float(t.split()[0]) if t else None
        except ValueError:
            return None

    def native(self, iid, cap, ok=('succeeded', 'accepted')):
        """The agent's native calls of ``cap`` on ``iid`` from the host-side request log, oldest first: [(log entry, call)].
        The entry carries the lab time (``sim_time_s``, after the request) and the reply chemlab printed (``stdout``): evidence
        that an action really ran, which a report file alone is not."""
        out = []
        for e in self.lab.core.log.entries:
            for x in e.get('backend_calls') or []:
                if (x.get('op') == 'invoke' and x.get('instrument') == iid and x.get('capability') == cap
                        and (ok is None or x.get('status') in ok)):
                    out.append((e, x))
        return out

    def looked(self, iid, after_seq=None):
        """Log entries in which the agent successfully read or queried ``iid`` (a request that read its state or called it),
        after log entry ``after_seq`` when given: evidence that a reported value came from the instrument."""
        return [e for e in self.lab.core.log.entries
                if e.get('instrument') == iid and e['exit'] == 0 and (after_seq is None or e['seq'] > after_seq)
                and any(x.get('op') in ('read', 'invoke') for x in e.get('backend_calls') or [])]

    def verbs_used(self):
        """(verb, exit) of the agent's class-verb requests (host-side log)."""
        out = []
        for e in self.lab.core.log.entries:
            a = e['argv'][1:]
            if len(a) >= 3 and a[0] == 'run':
                out.append((a[1], a[2], e['exit']))
        return out

    def result(self):
        return {'task': self.task['id'], 'passed': bool(self.items) and all(i['ok'] for i in self.items), 'checks': self.items}


def lab_config(task):
    if task.get('lab_config'):                                              # the task builds its whole lab config (e.g. a Flex with a deck)
        return task['lab_config']()
    cfg = single(task['instruments'], load_config())
    if task.get('config'):
        cfg = task['config'](cfg) or cfg
    return cfg


def make_lab(task, run_dir, sandbox=True):
    """The task's lab (its instruments of the default lab + the task world), started.  sandbox=False: see glab.GenericLab."""
    lab = GenericLab(run_dir, lab_config(task), callers=tuple(task.get('callers', ('agent',))), sandbox=sandbox).start()
    if task.get('world'):
        task['world'](lab)
    return lab


def run_task(task, out_dir, solution=None, timeout=900, sandbox=True):
    """Run ``solution`` (bash, in the agent sandbox; default: the task's gold; '' = do nothing) in a fresh task world and judge it.
    Writes transcript.json, check.json and the lab's core_log.jsonl / work/ into ``out_dir``; returns the check result."""
    out_dir = Path(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    t0 = time.monotonic()
    lab = make_lab(task, out_dir, sandbox=sandbox)
    script = task['gold'] if solution is None else solution
    r = None
    try:
        r = lab.sh(script, timeout=timeout) if script else None
        if task.get('after'):
            task['after'](lab)
    finally:
        lab.stop()
    return judge(task, lab, out_dir, t0, r, script)


def judge(task, lab, out_dir, t0, r=None, script=None):
    """Run the task's checker on a finished lab (stopped), write transcript.json and check.json, return the check result."""
    out_dir = Path(out_dir)
    c = Check(task, lab)
    try:
        task['check'](c)
    except Exception as exc:                                                 # noqa: BLE001 - a checker crash fails the task
        c.add('checker ran', False, '%s: %s' % (type(exc).__name__, exc))
    res = c.result()
    entries = lab.core.log.entries
    res.update({'wall_s': round(time.monotonic() - t0, 2), 'sim_time_s': round(lab.backend.now(), 1),
                'exit_code': None if r is None else r.exit_code, 'timed_out': bool(r and r.timed_out), 'requests': len(entries),
                'refused': sum(1 for e in entries if e['exit'] != 0),
                'native_calls': sum(1 for e in entries for x in e['backend_calls'] if x.get('op') == 'invoke'),
                'class_verbs': sum(1 for e in entries if e['argv'][1:2] == ['run'])})
    (out_dir / 'transcript.json').write_text(json.dumps({'task': task['id'], 'instruction': task['instruction'], 'solution': script,
                                                         'shell': lab.transcript}, ensure_ascii=False, indent=1))
    (out_dir / 'check.json').write_text(json.dumps(res, ensure_ascii=False, indent=1))
    return res


def run_suite(cls, tasks, out=None, only=None, noact=True, log=print):
    out = Path(out) if out else RESULTS / cls
    rows = []
    t0 = time.monotonic()
    for task in tasks:
        if only and task['id'] not in only:
            continue
        gold = run_task(task, out / task['id'] / 'gold')
        na = run_task(task, out / task['id'] / 'noact', solution='') if noact else None
        row = {'task': task['id'], 'kind': task['kind'], 'instruments': task['instruments'], 'gold_passed': gold['passed'],
               'noact_failed': None if na is None else not na['passed'], 'requests': gold['requests'], 'refused': gold['refused'],
               'class_verbs': gold['class_verbs'], 'native_calls': gold['native_calls'], 'wall_s': gold['wall_s'],
               'sim_s': gold['sim_time_s'], 'failed_checks': [x['check'] + ': ' + x['detail'] for x in gold['checks'] if not x['ok']]}
        rows.append(row)
        ok = row['gold_passed'] and row['noact_failed'] is not False
        log('%-34s %s  gold=%s noact_failed=%s  %d requests (%d refused), %d native calls, %.1f s wall%s' % (
            task['id'], 'PASS' if ok else 'FAIL', gold['passed'], row['noact_failed'], row['requests'], row['refused'], row['native_calls'],
            row['wall_s'], ('  failed: ' + '; '.join(row['failed_checks'])) if row['failed_checks'] else ''))
    passed = bool(rows) and all(r['gold_passed'] and r['noact_failed'] is not False for r in rows)
    summary = {'class': cls, 'passed': passed, 'tasks': rows, 'wall_s': round(time.monotonic() - t0, 1),
               'time': time.strftime('%Y-%m-%d %H:%M:%S')}
    out.mkdir(parents=True, exist_ok=True)
    if not only:
        (out / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=1))
        lines = ['| task | kind | instruments | gold passes | NoAct fails | requests | refused (expected) | class verbs | native calls | sim s |',
                 '|---|---|---|---|---|---|---|---|---|---|']
        for r in rows:
            lines.append('| %s | %s | %s | %s | %s | %d | %d | %d | %d | %.0f |' % (
                r['task'], r['kind'], ', '.join(r['instruments']), 'yes' if r['gold_passed'] else 'NO',
                {True: 'yes', False: 'NO', None: '-'}[r['noact_failed']], r['requests'], r['refused'], r['class_verbs'], r['native_calls'], r['sim_s']))
        (out / 'summary.md').write_text('\n'.join(lines) + '\n')
    return summary

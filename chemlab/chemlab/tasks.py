"""Run the acceptance tasks with your own agent, a script, or by hand.

Every instrument class of the compile layer comes with acceptance tasks: a task world (the instruments of the default lab,
sometimes with a fault or an unusual state to recover from), an instruction for the agent, a reference solution and an
automatic checker.  The checker reads the evaluator view (true instrument state, never visible to the agent), the lab's
request log and the report files the instruction asks for.

    python -m chemlab.tasks list [--class CLASS] [--json]      # every task: id, class, kind, instruments
    python -m chemlab.tasks show TASK_ID [--json]               # the instruction (what the agent is told) and the instruments
    python -m chemlab.tasks run TASK_ID --solution FILE         # run a bash script in the task's sandbox, then check it
    python -m chemlab.tasks run TASK_ID --gold                  # the same with the reference solution
    python -m chemlab.tasks shell TASK_ID                       # interactive sandboxed shell in the task world; checked on exit

A solution is a bash script that runs inside the agent sandbox, where `chemlab` is the only way to reach the lab.  It starts
in the agent's work directory; report files the instruction asks for are written there.  To put an agent on a task, show it
the instruction, let it produce the commands (or drive `shell`), and run them as the solution.

Results go to --out (default build/tasks/<TASK_ID>/<time>): transcript.json, core_log.jsonl (every request and reply),
check.json and work/.  Exit code: 0 when the checker passes, 1 when it fails, 2 on bad usage.  --no-sandbox (opt-in, with a
warning) runs without the agent sandbox for local, trusted use on machines without Landlock.
"""
import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

from .labbackend import CHEMLAB_ROOT


def suites():
    """{class: [task, ...]} over every class suite (chemlab/compile/tasks/<class>_tasks.py)."""
    from .compile.acceptance import classes, suite
    out = {}
    for cls in classes():
        try:
            out[cls] = list(suite(cls).TASKS)
        except ImportError:
            continue
    return out


def _usage(msg):
    print('chemlab.tasks: %s' % msg, file=sys.stderr)
    raise SystemExit(2)


def find(task_id):
    for cls, tasks in suites().items():
        for t in tasks:
            if t['id'] == task_id:
                return cls, t
    _usage('no task %r (see: python -m chemlab.tasks list)' % task_id)


def _out_dir(a, task_id):
    return Path(a.out) if a.out else CHEMLAB_ROOT / 'build' / 'tasks' / task_id / time.strftime('%Y%m%d-%H%M%S')


def _print_check(res, out):
    for x in res['checks']:
        print('  %s  %s%s' % ('ok  ' if x['ok'] else 'FAIL', x['check'], ('  (%s)' % x['detail']) if x['detail'] and not x['ok'] else ''))
    print('%s: %s  (%d requests, %d refused, %.0f s simulated, %.1f s wall)' % (
        res['task'], 'PASSED' if res['passed'] else 'FAILED', res['requests'], res['refused'], res['sim_time_s'], res['wall_s']))
    print('results: %s' % out)


def cmd_list(a):
    rows = [{'id': t['id'], 'class': cls, 'kind': t.get('kind', ''), 'instruments': t.get('instruments', [])}
            for cls, tasks in suites().items() if not a.cls or cls == a.cls for t in tasks]
    if a.cls and not rows:
        _usage('no class %r (classes: %s)' % (a.cls, ', '.join(suites())))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=1))
        return 0
    for r in rows:
        print('%-36s %-18s %-48s %s' % (r['id'], r['class'], r['kind'][:48], ', '.join(r['instruments'])))
    print('%d tasks' % len(rows))
    return 0


def cmd_show(a):
    cls, t = find(a.task)
    info = {'id': t['id'], 'class': cls, 'kind': t.get('kind', ''), 'instruments': t.get('instruments', []),
            'instruction': t['instruction']}
    if a.json:
        print(json.dumps(info, ensure_ascii=False, indent=1))
    else:
        print('%s (%s; %s)\ninstruments: %s\n\n%s' % (t['id'], cls, info['kind'], ', '.join(info['instruments']), t['instruction']))
    return 0


def cmd_run(a):
    from .compile.tasks import common
    _, t = find(a.task)
    if a.gold == bool(a.solution):
        _usage('give either --solution FILE or --gold')
    script = Path(a.solution).read_text(encoding='utf-8') if a.solution else t['gold']
    out = _out_dir(a, t['id'])
    res = common.run_task(t, out, solution=script, timeout=a.timeout, sandbox=not a.no_sandbox)
    _print_check(res, out)
    return 0 if res['passed'] else 1


def cmd_shell(a):
    from .compile.tasks import common
    _, t = find(a.task)
    out = _out_dir(a, t['id'])
    out.mkdir(parents=True, exist_ok=True)
    t0 = time.monotonic()
    lab = common.make_lab(t, out, sandbox=not a.no_sandbox)
    try:
        print('task %s\n\n%s\n\n(a sandboxed shell in the task world: use `chemlab ...`; type exit to finish and run the checker)\n'
              % (t['id'], t['instruction']), file=sys.stderr)
        subprocess.call(lab.shell_argv('exec bash --norc --noprofile -i'), env=lab.env())
        if t.get('after'):
            t['after'](lab)
    finally:
        lab.stop()
    res = common.judge(t, lab, out, t0, None, '(interactive shell)')
    _print_check(res, out)
    return 0 if res['passed'] else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog='python -m chemlab.tasks', description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='command', required=True)
    p = sub.add_parser('list', help='list the tasks')
    p.add_argument('--class', dest='cls', help='only this class (e.g. balance, thermal, property_meter)')
    p.add_argument('--json', action='store_true')
    p = sub.add_parser('show', help="show a task's instruction")
    p.add_argument('task')
    p.add_argument('--json', action='store_true')
    for name, hlp in (('run', 'run a solution script in the task world and check it'),
                      ('shell', 'interactive sandboxed shell in the task world; the checker runs when it exits')):
        p = sub.add_parser(name, help=hlp)
        p.add_argument('task')
        p.add_argument('--out', help='results directory (default build/tasks/TASK_ID/TIME)')
        p.add_argument('--no-sandbox', action='store_true', help='no agent sandbox (no isolation; local, trusted use only)')
        if name == 'run':
            p.add_argument('--solution', help='bash script to run inside the sandbox')
            p.add_argument('--gold', action='store_true', help="run the task's reference solution")
            p.add_argument('--timeout', type=float, default=900.0, help='wall-clock limit in seconds (default 900)')
    a = ap.parse_args(argv)
    try:
        return {'list': cmd_list, 'show': cmd_show, 'run': cmd_run, 'shell': cmd_shell}[a.command](a)
    except RuntimeError as e:                                               # no sandbox here, no C compiler for the client
        print('chemlab: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())

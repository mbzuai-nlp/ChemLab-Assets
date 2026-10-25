"""Acceptance suite: every task's gold solution through the sandboxed chemlab client must pass its checker, and an empty
solution (NoAct) must fail it.

    python -m chemlab.acceptance [--only t02_single_transfer] [--speed 20] [--out pilot_results/acceptance]
"""
import argparse
import json
import sys
import time
from pathlib import Path

from .flex_tasks import BY_ID, TASKS, run_task

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--only', action='append')
    ap.add_argument('--speed', type=float, default=20.0)
    ap.add_argument('--out', default=str(ROOT / 'pilot_results' / 'acceptance'))
    ap.add_argument('--no-noact', action='store_true')
    a = ap.parse_args(argv)
    tasks = [BY_ID[t] for t in a.only] if a.only else TASKS
    out = Path(a.out)
    rows = []
    t0 = time.monotonic()
    for task in tasks:
        gold = run_task(task, out / task['id'] / 'gold', speed=a.speed)
        noact = None if a.no_noact else run_task(task, out / task['id'] / 'noact', solution='', speed=a.speed)
        rows.append({'task': task['id'], 'kind': task['kind'], 'gold_passed': gold['passed'], 'noact_failed': None if noact is None else not noact['passed'],
                     'gold_wall_s': gold['wall_s'], 'gold_sim_s': gold['sim_time_s'], 'requests': gold['requests'], 'rejected': gold['rejected'],
                     'backend_calls': gold['backend_calls'], 'failed_checks': [c['check'] for c in gold['checks'] if not c['ok']]})
        flag = 'PASS' if gold['passed'] and (noact is None or not noact['passed']) else 'FAIL'
        print('%-30s %s  gold=%s noact_failed=%s  %d requests (%d refused), %.1f s wall, %.0f s sim%s' % (
            task['id'], flag, gold['passed'], rows[-1]['noact_failed'], gold['requests'], gold['rejected'], gold['wall_s'], gold['sim_time_s'],
            ('  failed: ' + '; '.join(rows[-1]['failed_checks'])) if rows[-1]['failed_checks'] else ''), flush=True)
    ok = all(r['gold_passed'] and r['noact_failed'] is not False for r in rows)
    summary = {'passed': ok, 'tasks': rows, 'wall_s': round(time.monotonic() - t0, 1), 'speed': a.speed}
    out.mkdir(parents=True, exist_ok=True)
    (out / 'summary.json').write_text(json.dumps(summary, indent=1))
    lines = ['| task | kind | gold passes | NoAct fails | requests | refused (expected) | backend calls | wall s | sim s |', '|---|---|---|---|---|---|---|---|---|']
    for r in rows:
        lines.append('| %s | %s | %s | %s | %d | %d | %d | %.1f | %.0f |' % (r['task'], r['kind'], 'yes' if r['gold_passed'] else 'NO',
                     {True: 'yes', False: 'NO', None: '-'}[r['noact_failed']], r['requests'], r['rejected'], r['backend_calls'], r['gold_wall_s'], r['gold_sim_s']))
    (out / 'summary.md').write_text('\n'.join(lines) + '\n')
    print('ALL PASS' if ok else 'SOME FAILED')
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

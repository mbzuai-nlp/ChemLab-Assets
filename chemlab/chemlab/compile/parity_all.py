"""The generic sweeps on all 203 instruments with every adapter loaded (shared layer + classes): the shared-layer parity check.

    python -m chemlab.compile.parity_all [--parts equivalence ready panels clock] [--out build/compile_parity_all]

Writes <out>/<part>.json (the sweep results) and <out>/summary.json; generic_results/ is not touched.  Takes about an hour.
"""
import argparse
import json
import sys
import time
from pathlib import Path

from .. import sweep

ROOT = Path(__file__).resolve().parents[2]


def summarize(part, res):
    rows = res.get('rows') or []
    if part == 'equivalence':
        rows = [r for r in rows if 'cli' in r]
        return {'calls': len(rows), 'same': sum(1 for r in rows if r['same'] and r['args_sent_equal']),
                'instruments': len({r['instrument'] for r in rows}),
                'bad': [(r['instrument'], r['capability']) for r in rows if not (r['same'] and r['args_sent_equal'])][:50]}
    if part == 'ready':
        return {'calls': len(rows), 'same': sum(1 for r in rows if r['same']), 'describe_read_ok': sum(1 for r in res['describe_read'] if r['ok']),
                'describe_read': len(res['describe_read']), 'visibility': res['visibility'][:20],
                'bad': [(r['instrument'], r['capability']) for r in rows if not r['same']][:50]}
    if part == 'panels':
        return {'instruments': len(rows), 'ok': sum(1 for r in rows if r.get('ok')), 'screen_equals_capture': sum(1 for r in rows if r.get('screen_equals_capture')),
                'gestures': sum(len(r.get('steps') or []) for r in rows),
                'gestures_equal': sum(1 for r in rows for s in r.get('steps') or [] if s['same_outcome'] and s['frames_equal']),
                'bad': [r['instrument'] for r in rows if not r.get('ok')]}
    return {'rows': len(rows), 'clock_ok': sum(1 for r in rows if r['clock_ok']), 'twin_rows': sum(1 for r in rows if 'twin_equal' in r),
            'twin_equal': sum(1 for r in rows if r.get('twin_equal')), 'bad': [r['instrument'] for r in rows if not r['clock_ok'] or r.get('twin_equal') is False]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--parts', nargs='*', default=['equivalence', 'ready', 'panels', 'clock'])
    ap.add_argument('--out', default=str(ROOT / 'build' / 'compile_parity_all'))
    a = ap.parse_args(argv)
    out = Path(a.out)
    sweep.RESULTS, sweep.RUNS = out, out / 'runs'
    summary = {}
    for part in a.parts:
        t0 = time.time()
        res = sweep.PARTS[part](log=lambda *x: None)
        summary[part] = dict(summarize(part, res), wall_s=round(time.time() - t0))
        print(part, json.dumps(summary[part])[:600], flush=True)
        (out / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())

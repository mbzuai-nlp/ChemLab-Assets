"""Class acceptance suites: every task's gold solution (written with `chemlab run`) through the sandboxed client must pass its
checker, and NoAct (an empty solution) must fail it.

    python -m chemlab.compile.acceptance --class balance [--only b01_tare_beaker] [--no-noact]
    python -m chemlab.compile.acceptance --class all

Results: compile_results/<class>/<task>/{gold,noact}/ (transcript.json, core_log.jsonl, check.json) and summary.md / summary.json.
"""
import argparse
import importlib
import sys

def classes():
    """Class names with a fragment (configs/adapters/<class>.json); a class's suite is chemlab.compile.tasks.<class>_tasks
    (found by convention, so adding a class edits no shared file)."""
    from .fragments import load_fragments
    return sorted({f['class'] for f in load_fragments() if f.get('layer') != 'shared'})


def suite(name):
    return importlib.import_module('chemlab.compile.tasks.%s_tasks' % name)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--class', dest='cls', required=True, help='a class name (configs/adapters/<class>.json) or all')
    ap.add_argument('--only', action='append')
    ap.add_argument('--no-noact', action='store_true')
    a = ap.parse_args(argv)
    ok = True
    for name in (classes() if a.cls == 'all' else [a.cls]):
        try:
            mod = suite(name)
        except ImportError as exc:
            print('%s: no suite (%s)' % (name, exc))
            ok = False
            continue
        print('== %s' % name, flush=True)
        s = mod.main(only=a.only, noact=not a.no_noact)
        print('%s: %s' % (name, 'ALL PASS' if s['passed'] else 'SOME FAILED'), flush=True)
        ok = ok and s['passed']
    return 0 if ok else 1


if __name__ == '__main__':
    sys.exit(main())

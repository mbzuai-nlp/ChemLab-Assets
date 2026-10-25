"""Start one Flex lab (a task's deck) and use it by hand.

    python3 -m chemlab.serve --task t02_single_transfer --run 'chemlab describe flex'   # one sandboxed command
    python3 -m chemlab.serve --task t10_protocol_then_transfer --shell                   # interactive sandboxed bash
    python3 -m chemlab.serve --task t09_busy_wait                                        # serve; prints the client env

The run directory (socket, core_log.jsonl, work/) goes to --dir (default: build/serve/<task>).  --check prints the task's
checker result (evaluator view) when the session ends.
"""
import argparse
import json
import os
import subprocess
import sys
import time

from .flex_tasks import BY_ID, check, make_lab
from .lab import CHEMLAB_ROOT
from .sandbox import sandbox_argv


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--task', default='t02_single_transfer', choices=sorted(BY_ID))
    ap.add_argument('--dir')
    ap.add_argument('--speed', type=float, default=10.0, help='simulated seconds per wall second')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--run', help='run one bash command in the sandbox')
    g.add_argument('--shell', action='store_true', help='interactive bash in the sandbox')
    ap.add_argument('--check', action='store_true', help="print the task checker's result at the end")
    a = ap.parse_args(argv)
    task = BY_ID[a.task]
    run_dir = a.dir or str(CHEMLAB_ROOT / 'build' / 'serve' / a.task)
    lab = make_lab(task, run_dir, speed=a.speed).start()
    print('task %s: %s' % (task['id'], task['instruction']), file=sys.stderr)
    code = 0
    try:
        if a.run:
            r = lab.sh(a.run)
            sys.stdout.write(r.out)
            sys.stderr.write(r.err)
            code = r.exit_code
        elif a.shell:
            code = subprocess.call(sandbox_argv(str(lab.work), lab.bindir, 'exec bash --norc --noprofile -i'), env=lab.env())
        else:
            env = lab.env()
            print('serving; in another shell (no sandbox) use:\n  export CHEMLAB_SOCKET=%s CHEMLAB_TOKEN=%s\n  %s/chemlab describe flex\n'
                  'Ctrl-C to stop.' % (env['CHEMLAB_SOCKET'], env['CHEMLAB_TOKEN'], lab.bindir), file=sys.stderr)
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        lab.stop()
        if a.check:
            print(json.dumps(check(task, lab), indent=1))
    return code


if __name__ == '__main__':
    sys.exit(main())

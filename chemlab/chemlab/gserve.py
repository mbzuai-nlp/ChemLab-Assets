"""Start a generic lab (default: all 203 instruments) and use it by hand.

    python -m chemlab.gserve --run 'chemlab list'                          # one sandboxed command
    python -m chemlab.gserve --only ika_plate_rctdigital mt_xpr204e --shell   # interactive sandboxed bash
    python -m chemlab.gserve --config configs/all203.json                   # serve; prints the client environment
    python -m chemlab.gserve --mcp                                          # MCP server on stdio (same commands as tools)

The run directory (socket, core_log.jsonl, captures/, work/) goes to --dir (default build/glab/<name>).  --no-setup starts every
instrument as its runtime creates it (no world set-up, no bring-up).  --no-sandbox (opt-in, prints a warning) runs --run and
--shell commands without the agent sandbox, for local, trusted use on machines without Landlock.
"""
import argparse
import subprocess
import sys
import time

from .glab import GenericLab, single
from .labbackend import CHEMLAB_ROOT, load_config
from .sandbox import SandboxUnavailable


def make_lab(a):
    cfg = load_config(a.config)
    if a.only:
        cfg = single(a.only, cfg)
    name = a.name or ('all' if not a.only else '_'.join(a.only)[:60])
    run_dir = a.dir or str(CHEMLAB_ROOT / 'build' / 'glab' / name)
    t0 = time.monotonic()
    lab = GenericLab(run_dir, cfg, setup=not a.no_setup, step_s=a.step, sandbox=not a.no_sandbox).start()
    print('lab: %d instruments, t = %.1f s simulated, ready in %.1f s (run dir %s)' % (len(lab.backend.instruments), lab.backend.now(),
                                                                                  time.monotonic() - t0, run_dir), file=sys.stderr)
    return lab


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--config')
    ap.add_argument('--only', nargs='*', help='instrument ids (default: every instrument of the config)')
    ap.add_argument('--name')
    ap.add_argument('--dir')
    ap.add_argument('--step', type=float, help='lab clock step in simulated seconds (default: the config, 1.0)')
    ap.add_argument('--no-setup', action='store_true')
    ap.add_argument('--no-sandbox', action='store_true',
                    help='run --run / --shell commands WITHOUT the agent sandbox (no isolation; local, trusted use only)')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--run', help='run one bash command in the sandbox')
    g.add_argument('--shell', action='store_true', help='interactive bash in the sandbox')
    g.add_argument('--mcp', action='store_true', help='serve the generic commands as MCP tools on stdio')
    a = ap.parse_args(argv)
    lab = make_lab(a)
    code = 0
    try:
        if a.run:
            r = lab.sh(a.run)
            sys.stdout.write(r.out)
            sys.stderr.write(r.err)
            code = r.exit_code
        elif a.shell:
            code = subprocess.call(lab.shell_argv('exec bash --norc --noprofile -i'), env=lab.env())
        elif a.mcp:
            from .mcp_shell import serve_stdio
            serve_stdio(lab.core, caller='agent')
        else:
            env = lab.env()
            print('serving; in another shell (no sandbox) use:\n  export CHEMLAB_SOCKET=%s CHEMLAB_TOKEN=%s\n  %s/chemlab list\n'
                  'Ctrl-C to stop.' % (env['CHEMLAB_SOCKET'], env['CHEMLAB_TOKEN'], lab.bindir), file=sys.stderr)
            while True:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    except (SandboxUnavailable, RuntimeError) as e:                     # no sandbox here, or no C compiler for the client
        print('chemlab: %s' % e, file=sys.stderr)
        code = 1
    finally:
        lab.stop()
    return code


if __name__ == '__main__':
    sys.exit(main())

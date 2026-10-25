"""Checks of the generic layer over all 203 instruments (used by tests_generic/ and runnable by hand).

    python -m chemlab.sweep equivalence   # every agent capability with contract example arguments: chemlab call (sandbox) vs the
                                          # runtime called directly, each on a fresh copy -> same status and code
    python -m chemlab.sweep ready         # the default lab after set-up: describe / read / every example call through the CLI,
                                          # twin lab called directly in the same order -> same status and code; visibility scan
    python -m chemlab.sweep panels        # 106 front panels: chemlab screen == panel.capture; press / turn / touch frames equal
                                          # to the runtime driven directly
    python -m chemlab.sweep clock         # chemlab wait: every runtime clock equals the lab clock and equals a twin stepped directly
    python -m chemlab.sweep all

Results: generic_results/<part>.json and a line per part in generic_results/summary.md.  The runtimes are driven through
their own sessions for the "direct" side; chemlab is not involved there.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shlex
import shutil
import sys
import time
from pathlib import Path

from .contract_text import example_call
from .glab import GenericLab, single
from .hosts import RuntimeHost
from .labbackend import EVALUATOR, OPERATOR, LabBackend, agent_principal, load_config

CHEMLAB_ROOT = Path(__file__).resolve().parents[1]
RESULTS = CHEMLAB_ROOT / 'generic_results'
SWEEP_ROOT = CHEMLAB_ROOT / 'build' / 'sweep_runs'
# one directory per process, so two sweeps running at once never delete each other's labs (they used to share build/sweep_runs/<part>)
RUNS = SWEEP_ROOT / ('%d-%s' % (os.getpid(), time.strftime('%Y%m%d-%H%M%S')))
KEEP_RUNS_S = 2 * 86400                          # older per-run directories are removed when a new sweep part starts


def _prune_old_runs():
    if RUNS.parent != SWEEP_ROOT or not SWEEP_ROOT.is_dir():
        return
    for d in SWEEP_ROOT.iterdir():
        try:
            if d.is_dir() and d != RUNS and time.time() - d.stat().st_mtime > KEEP_RUNS_S:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            pass


def _fresh_dir(name):
    _prune_old_runs()
    d = RUNS / name
    if d.exists():
        shutil.rmtree(d)
    d.mkdir(parents=True)
    return d


def direct_outcome(fn):
    """(status, code, raised) of a direct runtime call; exceptions are kept as they are."""
    try:
        r = fn()
    except Exception as exc:                                                 # noqa: BLE001
        text = str(exc)
        code = text if re.match(r'^[A-Z][A-Z0-9_]{1,63}$', text or '') else None
        return {'status': 'raised', 'code': code, 'raised': type(exc).__name__}
    if not isinstance(r, dict):
        return {'status': 'non-dict', 'code': None, 'raised': None}
    return {'status': r.get('status'), 'code': (r.get('error') or {}).get('code'), 'raised': None}


def cli_outcome(stdout):
    """(status, code, raised) from `chemlab ... --json` output."""
    try:
        p = json.loads(stdout)
    except ValueError:
        return {'status': 'unparsable', 'code': None, 'raised': None}
    if p.get('ok'):
        return {'status': p.get('status'), 'code': None, 'raised': None}
    e = p.get('error') or {}
    rt = e.get('runtime') or {}
    if e.get('class') == 'usage':
        return {'status': 'usage', 'code': e.get('code'), 'raised': None}
    if rt.get('raised'):
        return {'status': 'raised', 'code': None if e.get('code') == 'RUNTIME_EXCEPTION' else e.get('code'), 'raised': rt['raised']}
    return {'status': rt.get('status', p.get('status')), 'code': rt.get('code', e.get('code')), 'raised': None}


def same(a, b):
    return a['status'] == b['status'] and a['code'] == b['code'] and a['raised'] == b['raised']


def call_command(iid, cap, example):
    """The chemlab command for a contract example: flag form when every value is a scalar, else --args JSON."""
    lines = example_call(iid, cap, example)
    use_flags = len(lines) == 2 and isinstance(example, dict) and example
    return (lines[0] if use_flags else lines[-1]) + ' --json', bool(use_flags)


def direct_host(inst, callers=('agent',)):
    """A fresh copy of the instrument's runtime host, sessions opened in the lab's order, nothing else."""
    h = RuntimeHost(inst.asset_type_id, inst.recipe, inst.entry.get('instance_id') or inst.id)
    for c in callers:
        h.open_session(agent_principal(c), 'agent')
    h.open_session(OPERATOR, 'agent')
    h.open_session(EVALUATOR, 'evaluator')
    return h


def _hidden_hits(view, text, global_view=None):
    out = set()
    for v in (view, global_view):
        if v is not None and v._hidden_re is not None:
            out |= set(m.group(1) for m in v._hidden_re.finditer(text))
    return sorted(out)


# ------------------------------------------------------------------------------------------------ part 1: fresh copies
def equivalence(only=None, log=print):
    run = _fresh_dir('equivalence')
    cfg = load_config()
    if only:
        cfg['instruments'] = [e for e in cfg['instruments'] if e['id'] in only]
    lab = GenericLab(run / 'lab', cfg, setup=False, max_wait_s=10).start()
    rows, t0 = [], time.time()
    try:
        for iid, inst in lab.backend.instruments.items():
            n_ok = 0
            for cap, c in sorted(inst.view.capabilities.items()):
                ex = c.get('example_arguments')
                if not isinstance(ex, dict):
                    rows.append({'instrument': iid, 'capability': cap, 'skipped': 'no example arguments'})
                    continue
                lab.backend.reset_instrument(iid)
                cmd, flags = call_command(iid, cap, ex)
                n_log = len(lab.core.log.entries)
                r = lab.sh(cmd, timeout=600)
                cli = cli_outcome(r.out)
                sent = None
                new = lab.core.log.entries[n_log:]
                if new and new[-1]['backend_calls']:
                    sent = new[-1]['backend_calls'][0].get('arguments')
                args_ok = sent is not None and json.dumps(sent, sort_keys=True) == json.dumps(ex, sort_keys=True)
                h = direct_host(inst)
                d = direct_outcome(lambda: h.sessions[agent_principal('agent')].invoke(
                    instance_id=h.instance_id, capability_id=cap, arguments=copy.deepcopy(ex), request_id='direct-1'))
                eq = same(cli, d)
                n_ok += eq and args_ok
                rows.append({'instrument': iid, 'capability': cap, 'cli': cli, 'direct': d, 'same': eq, 'flag_form': flags,
                             'args_sent_equal': args_ok, 'exit': r.exit_code})
            log('%-40s %3d/%3d  (%.0f s)' % (iid, n_ok, len(inst.view.capabilities), time.time() - t0))
    finally:
        lab.stop()
    return _report('equivalence', rows)


# ------------------------------------------------------------------------------------------------ part 2: the ready lab
def ready(only=None, log=print):
    run = _fresh_dir('ready')
    cfg = load_config()
    if only:
        cfg['instruments'] = [e for e in cfg['instruments'] if e['id'] in only]
    t0 = time.time()
    lab = GenericLab(run / 'lab', cfg).start()
    twin = LabBackend(cfg, run_dir=run / 'twin')
    log('labs built in %.0f s, T0 = %.0f s' % (time.time() - t0, lab.backend.now()))
    out = {'describe_read': [], 'calls': [], 'visibility': [], 'setup': {}, 'leaks_removed': []}
    try:
        ids = list(lab.backend.instruments)
        script = 'mkdir -p sweep; for i in %s; do ' \
                 'chemlab describe $i --json > sweep/$i.describe.json; echo "$i describe_json $?"; ' \
                 'chemlab describe $i > sweep/$i.describe.txt; echo "$i describe $?"; ' \
                 'chemlab read $i --json > sweep/$i.read.json 2>&1; echo "$i read_json $?"; ' \
                 'chemlab read $i > sweep/$i.read.txt 2>&1; echo "$i read $?"; done' % ' '.join(ids)
        r = lab.sh(script, timeout=3600)
        codes = {}
        for line in r.out.splitlines():
            iid, what, code = line.split()
            codes.setdefault(iid, {})[what] = int(code)
        work = lab.work / 'sweep'
        for iid in ids:
            inst = lab.backend.instruments[iid]
            v = inst.view
            row = {'instrument': iid, 'exit': codes.get(iid)}
            dj = json.loads((work / ('%s.describe.json' % iid)).read_text())
            rj = json.loads((work / ('%s.read.json' % iid)).read_text())
            row['n_caps'] = len(dj.get('capabilities') or [])
            row['n_caps_view'] = len(v.capabilities)
            row['n_slots'] = len(dj.get('state_slots') or [])
            row['n_obs'] = len(rj.get('observations') or {})
            row['obs_not_visible'] = sorted(k for k in (rj.get('observations') or {}) if k not in v.slots)
            row['visible_not_read'] = sorted(sid for sid in v.slots if sid not in (rj.get('observations') or {}))
            row['ok'] = all(c == 0 for c in (codes.get(iid) or {'x': 1}).values()) and row['n_caps'] == row['n_caps_view'] \
                and not row['obs_not_visible']
            out['describe_read'].append(row)
            out['setup'][iid] = inst.setup_report
            for f in work.glob('%s.*' % iid):
                hits = _hidden_hits(v, f.read_text(), lab.backend.global_view)
                if hits:
                    out['visibility'].append({'instrument': iid, 'file': f.name, 'hidden_ids': hits})
        for iid in ids:                                                      # the twin sees the same reads
            th = twin.instruments[iid].host
            for _ in range(2):
                direct_outcome(lambda: th.describe(agent_principal('agent')))
                direct_outcome(lambda: th.describe(agent_principal('agent')))
                direct_outcome(lambda: th.read(agent_principal('agent')))
        log('describe/read done (%.0f s)' % (time.time() - t0))
        # every example call, in order, from the set-up state: chemlab (sandbox) vs the twin's runtime directly
        for iid in ids:
            inst = lab.backend.instruments[iid]
            th = twin.instruments[iid].host
            lines, meta = [], []
            for cap, c in sorted(inst.view.capabilities.items()):
                ex = c.get('example_arguments')
                if not isinstance(ex, dict):
                    continue
                cmd, _ = call_command(iid, cap, ex)
                lines.append('%s > sweep/call_%s_%s.json 2>&1' % (cmd, iid, cap))
                meta.append((cap, ex))
            n_log = len(lab.core.log.entries)
            r = lab.sh('; '.join(lines), timeout=3600)
            for k, (cap, ex) in enumerate(meta):
                f = lab.work / 'sweep' / ('call_%s_%s.json' % (iid, cap))
                cli = cli_outcome(f.read_text())
                d = direct_outcome(lambda: th.sessions[agent_principal('agent')].invoke(
                    instance_id=th.instance_id, capability_id=cap, arguments=copy.deepcopy(ex), request_id='r-%04d' % (n_log + k + 1)))
                hits = _hidden_hits(inst.view, f.read_text(), lab.backend.global_view)
                if hits:
                    out['visibility'].append({'instrument': iid, 'file': f.name, 'hidden_ids': hits})
                out['calls'].append({'instrument': iid, 'capability': cap, 'cli': cli, 'direct': d, 'same': same(cli, d)})
            log('%-40s calls done (%.0f s)' % (iid, time.time() - t0))
        # the log as the agent sees it
        r = lab.sh('chemlab log --last 100000 --json > sweep/log.json; chemlab log --last 100000 --full > sweep/log.txt')
        logtext = (lab.work / 'sweep' / 'log.json').read_text() + (lab.work / 'sweep' / 'log.txt').read_text()
        tokens = set(re.findall(r'(?<![A-Za-z0-9_.])[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)+', logtext))
        visible_anywhere = set()
        for o in lab.backend.instruments.values():
            visible_anywhere |= o.view.callable | set(o.view.slots)
        for iid, inst in lab.backend.instruments.items():
            # an id hidden on one instrument can be visible on another; only ids visible nowhere count in the shared log
            hits = sorted((tokens & inst.view.hidden_set) - visible_anywhere)
            if hits:
                out['visibility'].append({'instrument': iid, 'file': 'log', 'hidden_ids': hits})
        out['leaks_removed'] = lab.core.leaks
        out['clock_errors'] = lab.backend.clock_errors()
    finally:
        lab.stop()
    res = _report('ready', out['calls'], extra=out)
    return res


# ------------------------------------------------------------------------------------------------ part 3: front panels
def _png_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _pixels_equal(a, b):
    from PIL import Image
    ia, ib = Image.open(a).convert('RGBA'), Image.open(b).convert('RGBA')
    return ia.size == ib.size and ia.tobytes() == ib.tobytes()


def _gestures(lab, iid):
    """Candidate gestures per kind: every key (press), every knob (turn +1), touch points on the main touch display (centre, then a
    3 x 3 grid).  panels() tries each kind in order until one gesture of that kind changes the frame."""
    p = lab.core._panel_view(iid)
    out = []
    if p is None:
        return out
    for k in p['keys']:
        out.append(('press', [k['id']], {'input_id': k['id']}, 'panel.press'))
    for k in p['knobs']:
        out.append(('turn', [k['id'], '1'], {'input_id': k['id'], 'detents': 1}, 'panel.turn'))
    if p['touch_displays']:
        d = p['touch_displays'][0]
        size = next((x['size'] for x in p['displays'] if x['id'] == d and x.get('size')), None) or [320, 240]
        w, h = int(size[0]), int(size[1])
        pts = [(w // 2, h // 2)] + [(w * i // 6, h * j // 6) for j in (1, 3, 5) for i in (1, 3, 5) if (i, j) != (3, 3)]
        pts += [(w * i // 8, y) for y in (h - max(4, h // 16), max(4, h // 24)) for i in (1, 3, 5, 7)]   # soft-key rows
        pts += [(max(4, w // 24), h * j // 8) for j in (1, 3, 5, 7)]                                      # side bar
        for x, y in pts:
            out.append(('touch', [d, str(x), str(y)], {'display_id': d, 'x': x, 'y': y}, 'panel.touch'))
    return out


def panels(only=None, log=print):
    run = _fresh_dir('panels')
    cfg = load_config()
    ids = [e['id'] for e in cfg['instruments'] if 'panel' in e or e['id'] in ('cem_libertyblue', 'cytiva_ktaoligosynt')]
    rows, t0 = [], time.time()
    for iid in ids:
        if only and iid not in only:
            continue
        one = single(iid, cfg)
        lab = GenericLab(run / iid / 'cli', one).start()
        if lab.backend.instruments[iid].panel is None:
            lab.stop()
            continue
        twin = LabBackend(one, run_dir=run / iid / 'direct')
        th = twin.instruments[iid].host
        p_agent = agent_principal('agent')
        main = lab.backend.instruments[iid].panel['main_display']
        row = {'instrument': iid, 'main_display': main, 'steps': []}
        try:
            def cli_screen(name):
                r = lab.sh('chemlab screen %s %s -o shots/%s.png --json' % (iid, main, name))
                return r, lab.work / 'shots' / ('%s.png' % name)

            ncap = [0]

            def direct_capture():
                ncap[0] += 1
                resp = th.invoke(p_agent, 'panel.capture', {'display_id': main}, 'direct-cap-%d' % ncap[0])
                return Path(resp['result']['image']['path']) if resp.get('status') == 'succeeded' else None

            r, f0 = cli_screen('00_home')
            # screen == panel.capture of the same runtime at the same moment
            same_file = lab.backend.invoke('agent', iid, 'panel.capture', {'display_id': main}, 'check-cap-0')
            cap0 = lab.backend.capture_file(same_file)
            row['screen_exit'] = r.exit_code
            row['screen_equals_capture'] = r.exit_code == 0 and cap0 is not None and f0.read_bytes() == cap0.read_bytes()
            d0 = direct_capture()
            row['home_equals_twin'] = d0 is not None and f0.exists() and _pixels_equal(f0, d0)
            prev_cli, prev_dir = f0, d0
            changed_kind = set()
            for k, (g, pos, args, cap) in enumerate(_gestures(lab, iid), 1):
                if g in changed_kind:
                    continue
                rc = lab.sh('chemlab panel %s %s %s --json' % (iid, g, ' '.join(shlex.quote(x) for x in pos)))
                cli = cli_outcome(rc.out)
                lab.sh('chemlab wait 1')
                rd = direct_outcome(lambda: th.invoke(p_agent, cap, dict(args), 'direct-g-%d' % k))
                twin.advance(1)
                r, fc = cli_screen('%02d_%s' % (k, g))
                fd = direct_capture()
                step = {'gesture': g, 'args': args, 'cli': cli, 'direct': rd, 'same_outcome': same(cli, rd),
                        'frames_equal': fc.exists() and fd is not None and _pixels_equal(fc, fd),
                        'cli_changed': fc.exists() and not _pixels_equal(fc, prev_cli),
                        'direct_changed': fd is not None and prev_dir is not None and not _pixels_equal(fd, prev_dir)}
                row['steps'].append(step)
                if step['cli_changed']:
                    changed_kind.add(g)
                prev_cli, prev_dir = fc, fd
            row['ok'] = row['screen_equals_capture'] and row['home_equals_twin'] and all(s['same_outcome'] and s['frames_equal']
                                                                                          for s in row['steps'])
            row['any_change'] = any(s['cli_changed'] for s in row['steps'])
        except Exception as exc:                                             # noqa: BLE001
            row['ok'] = False
            row['error'] = '%s: %s' % (type(exc).__name__, exc)
        finally:
            lab.stop()
        rows.append(row)
        log('%-40s ok=%s changes=%s (%.0f s)' % (iid, row.get('ok'), [s['cli_changed'] for s in row['steps']], time.time() - t0))
    return _report('panels', rows)


# ------------------------------------------------------------------------------------------------ part 4: the clock
def _direct_advance(h, mode, dt, t_new, n):
    """The recipe's clock call, written out here (independent of hosts.RuntimeHost.advance)."""
    x = h.host
    if mode == 'step_dt':
        x.step(dt)
    elif mode == 'host_step_dt':
        x.host_step(dt)
    elif mode == 'host_step_dt_world':
        x.host_step(dt, world_time_s=t_new)
    elif mode == 'step_dt_world':
        x.step(dt, world_time_s=t_new)
    elif mode == 'host_step_abs':
        x.host_step(t_new)
    elif mode == 'host_advance_abs':
        x.host_advance(t_new)
    elif mode == 'host_set_clock_abs':
        x.host_set_clock(t_new)
    elif mode == 'vici_set_time':
        x.host_set_time(world_time_s=t_new, request_id='twin-clock-%d' % n)
    elif mode == 'clock_host_update':
        h.local_s = t_new                                                    # the clock callable first, then the event update
        x.host_update()
    h.local_s = t_new                                                        # clock-callable runtimes read local_s


def _scrub_obs(obs):
    return json.loads(json.dumps(obs, sort_keys=True, default=str))


def clock(only=None, log=print, waits=(7.5, 30.0)):
    run = _fresh_dir('clock')
    cfg = load_config()
    if only:
        cfg['instruments'] = [e for e in cfg['instruments'] if e['id'] in only]
    rows = []
    from .make_config import build
    bare = {e['id']: e for e in build(default_lab=False)['instruments']}
    fresh = copy.deepcopy(cfg)                  # no set-up: also without the default lab's entry keys (scenes, decks, chillers ...)
    for e in fresh['instruments']:
        for k in [k for k in e if k != 'setup' and k not in (bare.get(e['id']) or e)]:
            e.pop(k)
    for setup in (False, True):
        lab = GenericLab(run / ('lab_setup' if setup else 'lab_fresh'), cfg if setup else fresh, setup=setup).start()
        twins = {}
        if not setup:
            for iid, inst in lab.backend.instruments.items():
                h = direct_host(inst)
                twins[iid] = h
        try:
            t_start = lab.backend.now()
            steps_done = []
            for w in waits:
                r = lab.sh('chemlab wait %s --json' % w)
                assert r.exit_code == 0, r.err
                n_full = int(w // lab.backend.step_s)
                rest = w - n_full * lab.backend.step_s
                steps_done += [lab.backend.step_s] * n_full + ([rest] if rest > 1e-9 else [])
            for iid, inst in lab.backend.instruments.items():
                rt = lab.backend.runtime_clock(iid)
                row = {'instrument': iid, 'setup': setup, 'mode': inst.recipe.get('advance'), 'lab_time_s': lab.backend.now(),
                       'runtime_time_s': rt, 'clock_ok': rt is not None and abs(rt - lab.backend.now()) <= 1e-6 * max(1.0, lab.backend.now())}
                if not setup:
                    h = twins[iid]
                    t, n = 0.0, 0
                    for dt in steps_done:
                        n += 1
                        t += dt
                        _direct_advance(h, inst.recipe.get('advance'), dt, t, n)
                    a = lab.backend.evaluator_read(iid)
                    b = h.sessions[EVALUATOR].read(instance_id=h.instance_id, state_ids=None)
                    oa, ob = _scrub_obs(a.get('observations')), _scrub_obs(b.get('observations'))
                    row['twin_equal'] = oa == ob
                    if oa != ob:
                        row['twin_diff'] = sorted(k for k in set(oa) | set(ob) if oa.get(k) != ob.get(k))[:10]
                rows.append(row)
            log('setup=%s: %d instruments, lab t=%.1f, clock ok %d, twin equal %d' % (
                setup, len(lab.backend.instruments), lab.backend.now(), sum(r['clock_ok'] for r in rows if r['setup'] == setup),
                sum(bool(r.get('twin_equal')) for r in rows if r['setup'] == setup)))
        finally:
            lab.stop()
    return _report('clock', rows)


# ------------------------------------------------------------------------------------------------ report
def _report(part, rows, extra=None):
    RESULTS.mkdir(parents=True, exist_ok=True)
    data = {'part': part, 'time': time.strftime('%Y-%m-%d %H:%M:%S'), 'rows': rows}
    if extra:
        data.update({k: v for k, v in extra.items() if k != 'calls'})
    out = RESULTS / ('%s.json' % part)
    tmp = out.with_name('.%s.%d.tmp' % (out.name, os.getpid()))           # atomic: concurrent sweeps never leave half a file
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str) + '\n')
    os.replace(tmp, out)
    return data


PARTS = {'equivalence': equivalence, 'ready': ready, 'panels': panels, 'clock': clock}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('part', choices=sorted(PARTS) + ['all'])
    ap.add_argument('--only', nargs='*', help='instrument ids')
    a = ap.parse_args(argv)
    for name in (sorted(PARTS) if a.part == 'all' else [a.part]):
        t0 = time.time()
        PARTS[name](only=a.only)
        print('%s done in %.0f s' % (name, time.time() - t0), file=sys.stderr)


if __name__ == '__main__':
    main()

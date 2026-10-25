"""Generator of chemlab/default_world.py (the default lab's per-instrument set-up), from the class packages' worlds.

    <matterix python> -m chemlab.default_world_gen [--out FILE]

Two sources, both diffed against the bring-up-only lab (make_config.build(default_lab=False)):
  * class worlds written as lab config: each lowering module's ``config()`` (one class world per instrument: the entry whose id is
    the asset slug, else the first one) - the set-up steps it adds (with "at": "start" when they go before the bring-up) and the
    entry keys it sets (scene, deck, chiller, samples ...);
  * class worlds written as functions on a LabBackend (PREPS below), recorded call by call as set-up steps.
Each step gets a "default" group (utility | accessory | consumable | standard | state) from its capability / host name.  Task-only
steps (unknown samples) are dropped (DROP).  Re-run after a class world changes, then `python -m chemlab.make_config`.
"""
import argparse
import copy
import importlib
import json
import pkgutil
import pprint
import shutil
import sys
import tempfile
from pathlib import Path

from .labbackend import LabBackend
from . import labbackend as _lb

OUT = Path(__file__).resolve().parent / 'default_world.py'
_BARE = None


def bare_config():
    global _BARE
    if _BARE is None:
        from .make_config import build
        _BARE = build(default_lab=False)
        _BARE.pop('inventory', None)
    return copy.deepcopy(_BARE)


def _patch_load_config():
    """Class worlds call load_config() for their base: give them the bring-up-only lab."""
    real = _lb.load_config

    def bare_loader(path_or_dict=None):
        return real(bare_config()) if path_or_dict is None else real(path_or_dict)
    from .compile import tasks as T
    for m in pkgutil.iter_modules(T.__path__):
        mod = importlib.import_module('chemlab.compile.tasks.' + m.name)
        if hasattr(mod, 'load_config'):
            mod.load_config = bare_loader
    _lb.load_config = bare_loader                       # also for functions that import it when called
    return real


# ------------------------------------------------------------------ 1. lab-config class worlds
def world_rows():
    real = _patch_load_config()
    base = {e['id']: e for e in bare_config()['instruments']}
    out = {}
    from .compile import tasks as T
    for m in sorted(pkgutil.iter_modules(T.__path__), key=lambda m: m.name):
        if not m.name.endswith('_lowering'):
            continue
        mod = importlib.import_module('chemlab.compile.tasks.' + m.name)
        if not hasattr(mod, 'config'):
            continue
        cls = m.name[:-len('_lowering')]
        for e in mod.config()['instruments']:
            e['setup'] = [x for x in e.get('setup') or [] if 'default' not in x]     # only what the class world itself adds
            slug = e['asset_type_id'].split('.', 1)[1]
            b = base[slug]
            bs = [json.dumps(x, sort_keys=True) for x in b.get('setup') or []]
            ws = [json.dumps(x, sort_keys=True) for x in e.get('setup') or []]
            first = ws.index(bs[0]) if bs and bs[0] in ws else len(ws)
            added = [dict(x, **({'at': 'start'} if i < first else {})) for i, x in enumerate(e.get('setup') or []) if ws[i] not in bs]
            removed = [json.loads(x) for x in bs if x not in ws]
            extra = {k: e[k] for k in e if k not in ('setup', 'setup_source', 'default_world') and e.get(k) != b.get(k)}
            if added or removed or extra:
                out.setdefault(slug, []).append({'class': cls, 'id': e['id'], 'added': added, 'removed': removed, 'extra': extra})
    _lb.load_config = real
    return out


# ------------------------------------------------------------------ 2. function class worlds, recorded
from .compile.tasks import particle_surface_world as PS, quant_analyzer_world as QA   # noqa: E402


def qa(iid, std=None):
    def f(b):
        QA.bring_up(b, iid, 'blank')
        if std is not None:
            QA.load(b, iid, 'standard', **std)
    return f


def qa_loader(iid):
    def f(b):
        QA.bring_up(b, iid, 'blank')
        QA.SAMPLE_LOADERS[iid](QA.World(b, iid))
    return f


def seal_default(b):
    """AA500: kits, channel reagents, power, the calibrants std0-3 and wash water in the sampler, and the automatic start-up program
    started (it runs about 2060 s, so it finishes shortly after T0 = 1800 s; the default lab does not wait for it).  The lab's
    release of the set-up-loaded cups (host release_to_lab, seal_aa500 1.0.1) is in default_world_overrides.py."""
    w = QA.World(b, QA.SEAL)
    QA._seal(w, 'on', 'cal1')
    QA.seal_standards(b)
    w.ag('system.startup')


PREPS = {
    # particle_surface: the class worlds of the acceptance tasks (the operator filled liquids / gases and put the contract's
    # example material in as the check sample)
    'antonpaar_litesizerdls': (lambda b: PS.dls(b), 'particle_surface world dls(): check dispersion in the omega cell'),
    'antonpaar_psa1190': (lambda b: PS.psa(b), 'particle_surface world psa(): tank filled, background measured, check sample added'),
    'antonpaar_litesizerdia': (lambda b: PS.dia(b), 'particle_surface world dia(): tank filled, background captured, check sample added'),
    'microtrac_turbiscanlab': (lambda b: PS.turbiscan(b), 'particle_surface world turbiscan(): check dispersion vial in the holder'),
    'antonpaar_autosorb6100': (lambda b: PS.autosorb(b), 'particle_surface world autosorb(): degassed check sample tube on station 1, '
                                                          'LN2 dewar filled'),
    'antonpaar_poremaster60': (lambda b: PS.poremaster(b), 'particle_surface world poremaster(): mercury filled, penetrometer in port 0'),
    'porometer_poroluxrevo': (lambda b: PS.porolux(b), 'particle_surface world porolux(): membrane disc, wetting liquid, N2'),
    'antonpaar_ultrapyc5000': (lambda b: PS.ultrapyc(b), 'particle_surface world ultrapyc(): cell insert chosen, filled cup in'),
    'surface_dvsresolution': (lambda b: PS.dvs(b), 'particle_surface world dvs(): water reservoir, dry gas, sample pan in'),
    'antonpaar_surpass3': (lambda b: PS.surpass(b), 'particle_surface world surpass(): sample in the cell, electrolyte filled'),
    'biolin_sigma700': (lambda b: PS.sigma(b), 'particle_surface world sigma(): liquid vessel on the stage'),
    'biolin_thetaflex': (lambda b: PS.theta(b), 'particle_surface world theta(): substrate on the stage, syringe filled'),
    'biolin_mediuml': (lambda b: PS.mediuml(b), 'particle_surface world mediuml(): subphase filled, spreading solution in the syringe'),
    'ametek_oxtran': (lambda b: PS.oxtran(b), 'particle_surface world oxtran(): test gases connected, film mounted in cell A'),
    # quant_analyzer: warmed up and the day's blank measured (stage "blank"), with a calibration standard in as the check standard
    QA.ICPE: (qa(QA.ICPE, {'dilution_factor': 10.0}), 'quant_analyzer world bring_up(blank) + the mid calibration standard'),
    QA.ICPMS: (qa(QA.ICPMS, {'dilution_factor': 10.0}), 'quant_analyzer world bring_up(blank) + the mid calibration standard'),
    QA.TOC: (qa(QA.TOC, {}), 'quant_analyzer world bring_up(blank) + KHP CRM standard'),
    QA.AA: (qa(QA.AA, {'dilution_factor': 2.0}), 'quant_analyzer world bring_up(blank) + the mid Cu standard'),
    QA.EDX: (qa(QA.EDX, {'reference_fraction': 0.5}), 'quant_analyzer world bring_up(blank) + the black-mass reference'),
    QA.MX: (qa(QA.MX, {'template_id': 'aox_linear_100_r1_c1'}), 'quant_analyzer world bring_up(blank) + the 100 AOX standard'),
    QA.DMA: (qa(QA.DMA), 'quant_analyzer world bring_up(blank): carousel with blank, 3 SRM 2448 standards and an SRM 2448 check'),
    QA.VARIO: (qa(QA.VARIO), 'quant_analyzer world bring_up(blank): blank, 3 sulfanilic acid standards, wheat-flour reference'),
    QA.RGA: (qa(QA.RGA), 'quant_analyzer world bring_up(blank)'),
    QA.ORBI: (qa_loader(QA.ORBI), 'quant_analyzer world bring_up(blank) + its sample loader'),
    QA.LECO: (qa_loader(QA.LECO), 'quant_analyzer world bring_up(blank) + its sample loader'),
    QA.IRMS: (qa(QA.IRMS), 'quant_analyzer world bring_up(blank)'),
    QA.SEAL: (seal_default, 'quant_analyzer world _seal(on) + seal_standards(): stock and reagent kits, channel reagents, power on, '
                            'calibrants std0-3 and wash water in the sampler, start-up program started (ready about 260 s after T0)'),
}



def record(slug, fn):
    cfg = bare_config()
    e = copy.deepcopy([x for x in cfg['instruments'] if x['id'] == slug][0])
    c = dict(cfg, instruments=[e])
    runs = Path(__file__).resolve().parents[1] / 'build' / 'default_world_gen'
    runs.mkdir(parents=True, exist_ok=True)
    t = tempfile.mkdtemp(dir=runs)
    b = LabBackend(c, run_dir=t + '/b')
    steps = []
    depth = [0]
    o_inv, o_ev, o_adv, o_host, o_write = b.invoke, b.evaluator_invoke, b.advance, b.host_call, b.write

    def wrap(kind, orig):
        def w(*a, **kw):
            if depth[0] == 0:
                if kind == 'invoke':
                    caller, iid, cap, args = a[:4]
                    steps.append({'capability': cap, 'arguments': copy.deepcopy(args)})
                elif kind == 'ev':
                    iid, cap, args = a[:3]
                    steps.append({'capability': cap, 'arguments': copy.deepcopy(args), 'evaluator': True})
                elif kind == 'adv':
                    s = float(a[0] if a else kw['seconds'])
                    if steps and 'advance_s' in steps[-1]:
                        steps[-1]['advance_s'] = round(steps[-1]['advance_s'] + s, 6)
                    else:
                        steps.append({'advance_s': s})
                elif kind == 'host':
                    iid, method = a[:2]
                    steps.append({'host': method, 'kwargs': dict(kw, instance_id='$instance')})
                elif kind == 'write':
                    raise RuntimeError('write is not a set-up step')
            depth[0] += 1
            try:
                r = orig(*a, **kw)
            finally:
                depth[0] -= 1
            if kind in ('invoke', 'ev') and depth[0] == 0 and r.get('status') not in ('succeeded', 'accepted', 'running'):
                steps[-1]['_status'] = r.get('status')
            return r
        return w
    b.invoke, b.evaluator_invoke, b.advance, b.host_call, b.write = (wrap('invoke', o_inv), wrap('ev', o_ev), wrap('adv', o_adv),
                                                                     wrap('host', o_host), wrap('write', o_write))
    t0 = b.now()
    try:
        fn(b)
        err = None
    except Exception as exc:  # noqa: BLE001
        err = '%s: %s' % (type(exc).__name__, str(exc)[:300])
    total = b.now() - t0
    shutil.rmtree(t, ignore_errors=True)
    return steps, total, err



def recorded():
    out = {}
    for slug, (fn, why) in PREPS.items():
        steps, total, err = record(slug, fn)
        if err:
            raise RuntimeError('%s: %s' % (slug, err))
        out[slug] = {'steps': [{k: x for k, x in st.items() if k != '_status'} for st in steps], 'why': why, 'advance_total_s': total}
    return out


# ------------------------------------------------------------------ 3. groups, task-only steps, the module
UTIL = ('supply', 'gas', 'water', 'vacuum', 'air', 'ventilation', 'cooling', 'coolant', 'boundary', 'connect', 'ambient', 'plant',
        'register_gc', 'gc.ready', 'utilities', 'dynamics', 'kinetics', 'process', 'thermal_rate', 'motion', 'geometry', 'water_model',
        'bath_fluid', 'configure_bath', 'laser', 'lasers', 'source.arm', 'wash.power', 'valves', 'heating.configure', 'thermal.configure',
        'electrochem.configure', 'pulse.charge', 'set_temperature', 'set_readings', 'install_consumables', 'chamber', 'cooling_source',
        'host_configure_dosing', 'host_configure_geometry')
ACCESS = ('column', 'fixture', 'cartridge', 'die', 'probe', 'membrane', 'cassette', 'crystal', 'rotor', 'stylus', 'pin.', 'tubes.install',
          'set_installed', 'deck', 'rack.configure', 'mount', 'separator', 'head.', 'grip', 'set_clamp', 'host_load_head', 'cell')
CONSUM = ('film', 'plate', 'tube', 'bottle', 'kit', 'ink', 'substrate', 'liquid', 'syringe', 'solution.prepare', 'buffer', 'gel.',
          'charge', 'powder', 'outlet', 'rack.place', 'consumable', 'reservoir', 'well.dispense', 'initialize_liquids', 'bottle.place',
          'vial.place', 'host_load_container', 'receiver', 'register_consumable', 'present_sample', 'load_rack')
STD = ('sample', 'specimen', 'register', 'template', 'solution.register', 'reference', 'vessel', 'queue', 'method.configure', 'charge')


def name_of(st):
    return st.get('host') or st.get('capability') or ('advance' if 'advance_s' in st else '?')


def group(st):
    n = name_of(st)
    if 'advance_s' in st:
        return 'state'
    for g, keys in (('standard', STD), ('consumable', CONSUM), ('accessory', ACCESS), ('utility', UTIL)):
        if any(k in n for k in keys):
            return g
    if n in ('power.set', 'power.press_switch'):
        return 'utility'
    return 'state'


# task-only steps (unknown samples, odd states) per instrument: predicate on the step
DROP = {
    'bd_facsariaiii': lambda st: name_of(st) in ('sample.provide', 'tube.prepare'),          # PBMC: a task sample
    'biorad_miniprotean': lambda st: name_of(st) == 'sample.prepare',                         # lysate_1: a task sample
    'agilent_hs8697': lambda st: False,
}
NO_SCENE = {'agilent_hs8697'}                       # the lab clock hook moves the 8697 without its scene
SKIP = {'genevac_mivac2'}                           # the class world only removes the default sample (an empty-stage variant)
DROP_ENTRY_KEYS = {'protocols', 'faults', '_removed'}



def generate(out_path=OUT):
    ws = world_rows()
    draft = {}
    for slug, rows in sorted(ws.items()):
        r = ([x for x in rows if x['id'] == slug] or rows[:1])[0]   # one class world per instrument (the first), no merging
        entry = {k: v for k, v in r['extra'].items() if k not in ('id', 'name')}
        draft[slug] = {'setup': [dict(st) for st in r['added']], 'entry': entry,
                       'from': '%s world%s' % (r['class'], '' if r['id'] == slug else ' (%s)' % r['id'])}
    for slug, pv in sorted(recorded().items()):
        if slug in draft:
            draft[slug]['setup'] += pv['steps']
            draft[slug]['from'] += ' + ' + pv['why']
        else:
            draft[slug] = {'setup': pv['steps'], 'entry': {}, 'from': pv['why']}
    out = {}
    for slug, v in sorted(draft.items()):
        if slug in SKIP:
            continue
        steps = [dict(st) for st in v['setup'] if not DROP.get(slug, lambda s: False)(st)]
        groups = [group(st) for st in steps]
        for _ in range(3):                  # a state step next to a standard / consumable load belongs to that load's group
            for i, g in enumerate(groups):
                if g == 'state':
                    for j in (i - 1, i + 1):
                        if 0 <= j < len(groups) and groups[j] in ('standard', 'consumable'):
                            groups[i] = groups[j]
                            break
        for st, g in zip(steps, groups):
            st['default'] = g
        entry = {k: x for k, x in v['entry'].items() if k not in DROP_ENTRY_KEYS}
        if slug in NO_SCENE:
            entry.pop('scene', None)
        out[slug] = {'setup': steps, 'entry': entry, 'from': v['from']}
    hdr = '''"""The default lab: what a well-run lab has ready at the start of a working day, per instrument - utilities
connected, standard accessories installed, consumables stocked, check standards and reference materials in place - so that
every instrument can run its main workflow from the default lab (configs/all203.json, built by chemlab/make_config.py).

GENERATED by chemlab/default_world_gen.py from the class worlds (lab-config worlds and, recorded as steps, the worlds
written as functions); the hand-written additions are in chemlab/default_world_overrides.py.  Each instrument:
    {"setup": [steps], "entry": {extra lab-config keys: scene, deck, ...}, "from": the class world it comes from}
Steps are lab-config set-up steps tagged "default": GROUP (utility | accessory | consumable | standard | state); a task world can
drop a group with {"replace_default": [GROUP]} or all of them with "default_world": false (labbackend._setup_steps).  Values come
from the asset contracts' example arguments (the documented typical set-up of each asset), the asset template libraries the
runtimes cite, the class packages' lab set-ups (their evidence strings are kept), and the manual pages named in "why".
Unknown samples, faults and odd states are not here: they stay in the task worlds.
"""

DEFAULTS = '''
    Path(out_path).write_text(hdr + pprint.pformat(out, width=150, sort_dicts=False) + '\n', encoding='utf-8')
    return out


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(OUT))
    a = ap.parse_args(argv)
    out = generate(a.out)
    print('%s: %d instruments' % (a.out, len(out)))


if __name__ == '__main__':
    main()

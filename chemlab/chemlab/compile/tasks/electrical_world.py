"""Host-side world for the electrical class: what the lab operator has done before the agent starts, as lab-config set-up steps
(capability calls as the operator, or "evaluator": true for host-only ones; "advance_s" lets that instrument's time run).

* M91 FastHall: the default set-up already mounts the sample card and switches the instrument on; the operator closes the sample
  clamp (sample.set_clamp is host-only).  The 30 min warm-up runs from power-on.
* QuadPro2: wafer size declared, the 1.27 mm tungsten-carbide probe head fitted (with the hood open), the wafer placed, the dark
  hood closed (the hood is host-only).
* 8604 VSM: the sample mounted on the rod (switched off, rod up).
* Alpha-T: cell geometry and temperature stage declared, the water cell mounted and clamped (switched off).
* LSR-3: specimen geometry declared, the specimen mounted and clamped, the furnace lowered (switched off).
"""
import copy

from ...labbackend import load_config


def step(cap, args='contract_example', evaluator=True):
    return {'capability': cap, 'arguments': args, 'evaluator': evaluator}


def wait(s):
    return {'advance_s': s}


WORLD = {
    'lakeshore_measurereadym91': [step('sample.set_clamp', {'closed': True}), wait(5.0)],
    'signatone_quadpro2': [step('wafer.configure'), step('probe.configure'), step('sample.load'), step('enclosure.set_open', {'opened': False}), wait(30.0)],
    'lakeshore_8604vsm': [step('sample.load')],
    'novocontrol_alphat': [step('geometry.configure'), step('thermal.configure'), step('sample.load'), step('sample.set_clamp', {'closed': True}), wait(30.0)],
    'linseis_lsr3': [step('geometry.configure'), step('sample.load'), step('sample.set_clamp', {'closed': True}), wait(30.0),
                     step('furnace.set_open', {'opened': False}), wait(60.0)],
}


def config(base=None, only=None):
    """all203.json (or ``base``) with the electrical world steps appended to the members' set-ups."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in WORLD and (only is None or slug in only):
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(WORLD[slug])
    return cfg


def setup_problems(lab):
    out = []
    for iid, inst in lab.backend.instruments.items():
        for r in getattr(inst, 'setup_report', []):
            if r.get('status') not in ('succeeded', 'accepted', 'ok'):
                out.append((iid, r))
    return out

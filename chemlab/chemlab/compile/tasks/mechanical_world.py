"""Host-side world for the mechanical class (what the lab operator has done before the agent starts), as lab-config set-up steps
appended after each member's default bring-up (all203.json).  Specimens, pins, styli, grips and charges are placed by people, so
these are evaluator / host calls, never class verbs:

* TRB3 tribometer: a disk specimen and a pin are loaded (contract examples); the lid stays open.
* RST 300 scratch tester: a coated plate specimen and a diamond stylus are loaded.
* HIT 300 nanoindenter: a specimen is loaded.
* AGS-10kNXD: the fixture (5 kN grips, no extensometer) is configured, a tensile specimen is put into the open grips and both grips
  are closed (the grips are an operator action on this frame).
* HIT5P pendulum: the default bring-up (pendulum raised, shield closed, no specimen) - enough for the friction calibration swing.
  ``HIT5P_READY`` is the extra world of a test: calibration swing done, pendulum re-raised, a notched bar on the supports.
* Mflow: the barrel heated to 250 °C, then 6 g of PBT granulate (Ultradur B6550, the slowest-flowing grade of the library, so
  that the piston is still above the measuring window after the preheat) charged, the 2.16 kg weight and the piston placed.
* MCR 503: 0.49 mL of water on the lower plate (1 mm fill gap with the PP25 system).
* FT4: 51 g of lactose powder in the vessel (door open, vessel unclamped).
"""
import copy
import json

from ...hosts import contract_index
from ...labbackend import load_config

EX = 'contract_example'


def ev(cap, args=EX):
    return {'capability': cap, 'arguments': args, 'evaluator': True}


WORLD = {
    'antonpaar_trb': [ev('specimen.load'), ev('pin.load')],
    'antonpaar_rst300': [ev('specimen.load'), ev('stylus.load')],
    'antonpaar_hit300': [ev('specimen.load')],
    'shimadzu_ags10knxd': [ev('fixture.configure'), ev('sample.load'),
                           ev('grip.set_closed', {'which': 'upper', 'closed': True}),
                           ev('grip.set_closed', {'which': 'lower', 'closed': True}), {'advance_s': 10.0}],
    'zwick_hit5p': [],
    'zwick_mflow': [{'capability': 'thermal.set_temperature', 'arguments': {'target_k': 523.15}}, {'advance_s': 1500.0},
                    ev('sample.load', {'_example': 'sample.load', 'reference_id': 'basf_ultradur_b6550'}),
                    ev('weight.configure', {'total_load_kg': 2.16}), ev('piston.insert'), {'advance_s': 10.0}],
    'antonpaar_mcr503': [ev('sample.load')],
    'freeman_ft4powder': [ev('sample.load')],
}

# HIT5P ready for an impact test: the friction calibration swing done by the operator, pendulum re-raised, shield opened,
# a notched bar placed, shield closed (the pendulum raise and the shield are operator actions on this tester).
HIT5P_READY = [ev('calibration.start', {}), {'advance_s': 20.0}, ev('pendulum.catch', {}), {'advance_s': 5.0}, ev('pendulum.raise', {}),
               {'advance_s': 5.0}, ev('shield.set_open', {'opened': True}), {'advance_s': 3.0}, ev('sample.load'),
               ev('shield.set_open', {'opened': False}), {'advance_s': 3.0}]


def example(slug, cap, **over):
    c = json.loads(contract_index()['instrument.' + slug].read_text(encoding='utf-8'))
    a = copy.deepcopy(c['capabilities'][cap]['example_arguments'])
    a.update(over)
    return a


def _resolve(slug, steps):
    out = []
    for st in copy.deepcopy(steps):
        a = st.get('arguments')
        if isinstance(a, dict) and '_example' in a:
            cap = a.pop('_example')
            st['arguments'] = example(slug, cap, **a)
        out.append(st)
    return out


def config(base=None, extra=None):
    """all203.json (or ``base``) with the mechanical world steps added; ``extra``: {slug: [more steps]}."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        steps = list(WORLD.get(slug, [])) + list((extra or {}).get(slug, []))
        if slug == 'zwick_hit5p':
            e['default_world'] = False        # this world brings the pendulum up itself (the default lab's calibrated state differs)
        if steps:
            e['setup'] = list(e.get('setup') or []) + _resolve(slug, steps)
    return cfg
READY = {'zwick_hit5p': HIT5P_READY}

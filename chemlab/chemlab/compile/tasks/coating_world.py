"""Host-side world for the coating class (what the lab operator has done before the agent starts), as lab-config set-up steps.

* Spin coater: a 25 mm substrate with solution on the chuck (evaluator sample registration, lid opened for it, then closed).
* Dip coater: the solution reservoir in place and a 1 mm slide clamped at the top (the clamp opened to 12 mm to insert it);
  the clamp is left open, so the agent closes it with `gap`.
* Doctor-blade and slot-die coaters: the solution (charge / ink syringe) and a 100 mm wide substrate in place.
* Sputter coater, CVD furnace: their release bring-up already installs the source, samples, gases and the sample boat; the CVD
  gas supply is enlarged (2 mol per bottle instead of 0.05) so a full furnace process does not run its bottles dry.
* ALD reactor: gas bottles (TMA / water), the deposition chemistry and a substrate (door open while loading).
* Inkjet printer: switched on, a filled cartridge, a substrate held by the platen vacuum.
Every step is a contract capability called as the operator or the evaluator with the contract's example arguments unless noted.
"""
import copy

from ...hosts import contract_index
from ...labbackend import load_config

EX = 'contract_example'


def ev(cap, args=EX):
    return {'capability': cap, 'arguments': args, 'evaluator': True}


def op(cap, args=EX):
    return {'capability': cap, 'arguments': args}


WORLD = {
    'ossila_spincoater': [op('lid.set_angle', {'angle_rad': 1.5}), {'advance_s': 2.0}, ev('sample.configure'),
                          op('lid.set_angle', {'angle_rad': 0.0}), {'advance_s': 2.0}],
    'ossila_dipcoater': [ev('bath.configure'), op('clamp.set_gap', {'gap_m': 0.012}), {'advance_s': 1.0}, ev('substrate.configure')],
    'ossila_doctorblade': [ev('charge.configure'), ev('substrate.configure')],
    'ossila_slotdie': [ev('ink.configure'), ev('substrate.configure')],
    'arradiance_gemstarxt': [ev('gas.install'), ev('kinetics.configure'), ev('sample.install')],
    'fujifilm_dmp2850': [op('power.set', {'enabled': True}), ev('ink.configure'), ev('substrate.configure'), ev('vacuum.set', {'enabled': True})],
}


def _cvd_gas():
    import json
    c = json.loads(contract_index()['instrument.laboao_cvdltf12p50'].read_text(encoding='utf-8'))
    a = copy.deepcopy(c['capabilities']['gas.install']['example_arguments'])
    a['initial_mol'] = [2.0 for _ in a['initial_mol']]
    return a


def config(base=None):
    """all203.json (or ``base``) with the coating world steps added after each member's own bring-up."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in WORLD:
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(WORLD[slug])
        elif slug == 'laboao_cvdltf12p50':
            steps = []
            for st in e.get('setup') or []:
                if st.get('capability') == 'gas.install':
                    st = dict(st, arguments=_cvd_gas())
                steps.append(st)
            e['setup'] = steps
    return cfg

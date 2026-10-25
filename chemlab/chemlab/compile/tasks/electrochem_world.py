"""Host-side world for the electrochem class (what the lab operator has done before the agent starts), as lab-config set-up steps.
Nothing here is reachable from the sandbox.

* Gamry Reference 620: the lab's 1 mM Fe(III)/Fe(II) template cell (SHE reference) is in place (host_load_template_cell); the
  electrodes are not yet registered in the potentiostat's slots (the agent connects we_01, ref_01, ce_01, cell_01).
* CHI440C: the quartz crystal declared (evaluator crystal.configure, before power on), the lid opened, the copper plating bath
  (contract example) filled, the lid closed.
* CHI920D: the SECM sample (contract example surface) mounted.
* WaveVortex 10: the enclosure opened, the oxygen-saturated bath (contract example) under the electrode, the enclosure closed, the
  external bipotentiostat connected (evaluator electrochem.configure).
"""
import copy

from ...labbackend import load_config

GAMRY_CELL = {'couple_id': 'redox_fep3_to_fep2', 'oxidant_concentration_mol_m3': 1.0, 'reductant_concentration_mol_m3': 0.0,
              'electrode_area_m2': 7.07e-06, 'reference_electrode_id': 'refel_she'}
CHI440C = [{'capability': 'crystal.configure', 'arguments': 'contract_example', 'evaluator': True},
           {'capability': 'lid.set', 'arguments': {'lift_m': 0.05}},
           {'capability': 'sample.load', 'arguments': 'contract_example', 'evaluator': True},
           {'capability': 'lid.set', 'arguments': {'lift_m': 0.0}}]
CHI920D = [{'capability': 'sample.load', 'arguments': 'contract_example', 'evaluator': True}]
PINE = [{'capability': 'enclosure.set', 'arguments': {'lift_m': 0.285}},
        {'capability': 'sample.load', 'arguments': 'contract_example', 'evaluator': True},
        {'capability': 'enclosure.set', 'arguments': {'lift_m': 0.0}},
        {'capability': 'electrochem.configure', 'arguments': 'contract_example', 'evaluator': True}]


def config(base=None):
    """all203.json (or ``base``) with the electrochem world steps added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        iid = e.get('instance_id') or e['id']
        if slug == 'gamry_reference620':
            e['setup'] = list(e.get('setup') or []) + [{'host': 'host_load_template_cell', 'kwargs': {'instance_id': iid, 'cell': dict(GAMRY_CELL)}}]
        elif slug == 'ch_chi440c':
            e['setup'] = CHI440C + list(e.get('setup') or [])
        elif slug == 'ch_chi920d':
            e['setup'] = list(e.get('setup') or []) + CHI920D
        elif slug == 'pine_wavevortex10':
            e['setup'] = list(e.get('setup') or []) + PINE
    return cfg

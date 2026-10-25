"""Host-side world for the sample_intro class (what the lab operator has done before the agent starts), as lab-config set-up
steps and scenes.  Nothing here is reachable from the sandbox.

* Agilent 8697: the room temperature is known (host set_ambient_temperature), four sealed 20 mL vials v1..v4 are prepared
  (host register_sample), the GC is connected (host register_gc), and the event-clock scene (scenes/hs8697.py).
* Atomx XYZ: an aqueous benzene vial at tray position 1 (contract example, vial_index 0) and an aqueous toluene vial at 3.
* Analyte Excite+: the guard and the drawer opened, the glass sample (contract example) loaded, drawer and guard closed.
* EGA/PY-3030D: the sample cup (contract example) loaded, the carrier gas on, the GC ready.
* UNITY-xr: the lid and the sample lever opened, the sorbent tube (contract example) loaded, lever and lid closed, the GC ready.
"""
import copy

from ...labbackend import load_config

HS_SCENE = 'chemlab.compile.scenes.hs8697:HS8697Scene'


def hs_setup(iid, vials=('v1', 'v2', 'v3', 'v4')):
    steps = [{'host': 'set_ambient_temperature', 'kwargs': {'temperature_K': 293.15}}]
    steps += [{'host': 'register_sample', 'kwargs': {'sample_id': v, 'volume_m3': 2e-05}} for v in vials]
    steps.append({'host': 'register_gc', 'kwargs': {'gc_instance_id': 'gc_01'}})
    return steps


ATOMX = [{'capability': 'sample.load', 'arguments': 'contract_example', 'evaluator': True},
         {'capability': 'sample.load', 'evaluator': True, 'arguments': {
             'sample_id': 'aqueous_toluene', 'vial_index': 2, 'matrix': 'water', 'liquid_volume_m3': 2e-05, 'soil_mass_kg': 0.0,
             'components': [{'substance_id': 'toluene', 'moles': 1e-08, 'soil_kd_m3_kg': 0.0}]}}]
ANALYTE = [{'capability': 'guard.set', 'arguments': {'angle_rad': 1.0}},
           {'capability': 'drawer.set', 'arguments': {'open_m': 0.2}},
           {'capability': 'sample.load', 'arguments': 'contract_example', 'evaluator': True},
           {'capability': 'drawer.set', 'arguments': {'open_m': 0.0}},
           {'capability': 'guard.set', 'arguments': {'angle_rad': 0.0}}]
EGAPY = [{'capability': 'sample.load', 'arguments': 'contract_example', 'evaluator': True},
         {'capability': 'gas.set', 'arguments': {'enabled': True}},
         {'capability': 'gc.ready', 'arguments': {'ready': True}, 'evaluator': True}]
UNITY = [{'capability': 'lid.set', 'arguments': {'lift_m': 0.18}},
         {'capability': 'sample_lever.set', 'arguments': {'angle_rad': 1.0}},
         {'capability': 'sample.load', 'arguments': 'contract_example', 'evaluator': True},
         {'capability': 'sample_lever.set', 'arguments': {'angle_rad': 0.0}},
         {'capability': 'lid.set', 'arguments': {'lift_m': 0.0}},
         {'capability': 'gc.ready', 'arguments': {'ready': True}, 'evaluator': True}]


def config(base=None):
    """all203.json (or ``base``) with the sample_intro world steps added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug == 'agilent_hs8697':
            e['scene'] = HS_SCENE
            e['setup'] = list(e.get('setup') or []) + hs_setup(e['id'])
        elif slug == 'teledyne_atomxxyz':
            e['setup'] = list(e.get('setup') or []) + ATOMX
        elif slug == 'teledyne_analyteexcite':
            e['setup'] = list(e.get('setup') or []) + ANALYTE
        elif slug == 'frontier_egapy':
            e['setup'] = list(e.get('setup') or []) + EGAPY
        elif slug == 'markes_unityxr':
            e['setup'] = list(e.get('setup') or []) + UNITY
    return cfg

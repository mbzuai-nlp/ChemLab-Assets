"""Host-side world for the digestion class (what the lab operator has done before the agent starts), as lab-config set-up steps.

* DigiPREP MS: the tubes are in the block (the evaluator's sample registration, contract example: one gibbsite tube with acid).
  The twin accepts tubes only after a programme exists, so the operator first stores a placeholder programme (the contract
  example, a constant 95 °C); the agent sets its own programme afterwards.
* Metrohm 909: the sample tubes are in the holder (shield open while loading, the contract example: 12 tubes, the sensor at
  position 6); the operator leaves the shield open, as after loading.
* BUCHI HydrolEx: the fume extraction runs, the hot-water reservoir is filled (5 L) and two beakers stand at positions 1 and 3
  (contract example sample: milk powder with acid).
* Multiwave 7101: the default bring-up (mains on, auto login) is the world; vessels and contents are the agent's `load`.
"""
import copy

from ...labbackend import load_config

HYDROLEX_SAMPLE = {'substance_id': 'buchi_milk_powder', 'sample_mass_kg': 0.002, 'sample_liquid_m3': 0.0, 'acid_volume_m3': 0.0001,
                   'acid_mol_m3': 4000.0, 'bound_fat_fraction': 0.8, 'release_rate_s_inverse': 0.003, 'fat_capture_fraction': 0.995,
                   'accept_reference_not_hydrolysis_calibration': True}
HYDROLEX_POSITIONS = (1, 3)

DIGIPREP = [{'capability': 'method.configure', 'arguments': 'contract_example'},
            {'capability': 'sample.configure', 'arguments': 'contract_example', 'evaluator': True}]
METROHM = [{'capability': 'shield.set_open', 'arguments': {'open': True}}, {'advance_s': 2.0},
           {'capability': 'sample.configure', 'arguments': 'contract_example', 'evaluator': True}]
HYDROLEX = ([{'capability': 'ventilation.set', 'arguments': {'enabled': True}, 'evaluator': True},
             {'capability': 'water.configure', 'arguments': {'volume_m3': 0.005}, 'evaluator': True}] +
            [{'capability': 'sample.configure', 'arguments': dict(HYDROLEX_SAMPLE, position=p), 'evaluator': True} for p in HYDROLEX_POSITIONS])

WORLD = {'scp_digiprepms': DIGIPREP, 'metrohm_909uv': METROHM, 'buchi_hydrolexh': HYDROLEX}


def config(base=None):
    """all203.json (or ``base``) with the digestion world steps added after each member's own bring-up."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in WORLD:
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(WORLD[slug])
    return cfg


"""Host-side world for the reactor class (what the lab operator has done before the agent starts), as lab-config set-up steps.

* Monowave 400: cooling air connected (gas.configure, evaluator) and a sealed G30 vial with 8 g ethanol loaded (lid opened,
  sample.configure as the evaluator, lid closed). The lid is closed but not locked: locking is the agent's step (`close lid`).
* Mya 4: the cooling water (15 °C, 3 L/min) is connected and confirmed - the host call configure_cooling_source; without it the
  zones refuse to heat or cool (the default lab has no cooling source).
* The other members are ready in the default lab (their set-up steps load the sample, chip, cartridge or charge).
"""
import copy

from ...labbackend import load_config

MONOWAVE = [{'capability': 'gas.configure', 'arguments': 'contract_example', 'evaluator': True},
            {'capability': 'lid.set_lift', 'arguments': {'lift_m': 0.15}},
            {'capability': 'sample.configure', 'arguments': 'contract_example', 'evaluator': True},
            {'capability': 'lid.set_lift', 'arguments': {'lift_m': 0.0}}]
MYA4_COOLING = {'host': 'configure_cooling_source', 'kwargs': {'available': True, 'temperature_K': 288.15, 'flow_m3_s': 5e-5}}


def config(base=None):
    """all203.json (or ``base``) with the reactor world steps added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug == 'antonpaar_monowave400':
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(MONOWAVE)
        elif slug == 'radleys_mya4':
            e['setup'] = [copy.deepcopy(MYA4_COOLING)] + list(e.get('setup') or [])
    return cfg

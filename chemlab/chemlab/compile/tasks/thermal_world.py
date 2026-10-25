"""Host-side world for the thermal class (what the lab operator has done before the agent starts), as lab-config set-up steps.

* JULABO CORIO: the bath is filled (4.5 L water) and its thermal model declared - configure_bath_fluid, a trusted host call
  that must come before the power is switched on (the default lab has no bath, so its temperature is unknown).
* Elma S60H: the tank is filled with 3.8 L of water (evaluator bath.configure, lid open while filling, then closed).
"""
import copy

from ...labbackend import load_config

JULABO_BATH = {'host': 'configure_bath_fluid',
               'kwargs': {'instance_id': 'julabo_corio_cd200f', 'mass_kg': 4.5, 'specific_heat_J_kg_K': 4186.0, 'heat_loss_W_K': 2.0,
                          'lid_open_loss_W_K': 6.0, 'initial_temperature_K': 293.15, 'minimum_temperature_K': 278.15,
                          'maximum_temperature_K': 368.15, 'evidence': 'task world: bath filled with 4.5 L of water'}}
ELMA_FILL = [{'capability': 'cover.set_lift', 'arguments': {'lift_m': 0.3}},
             {'capability': 'bath.configure', 'arguments': 'contract_example', 'evaluator': True},
             {'capability': 'cover.set_lift', 'arguments': {'lift_m': 0.0}}]


def config(base=None):
    """all203.json (or ``base``) with the thermal world steps added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug == 'julabo_corio_cd200f':
            kw = dict(JULABO_BATH['kwargs'], instance_id=e.get('instance_id') or e['id'])
            e['setup'] = [dict(JULABO_BATH, kwargs=kw)] + list(e.get('setup') or [])
        elif slug == 'elma_elmasonics60h':
            e['setup'] = ELMA_FILL + list(e.get('setup') or [])
        elif slug == 'ika_ks4000':
            e['default_world'] = False        # th10: the shaker without its default fixture (the task's odd state: NO_FIXTURE)
    return cfg

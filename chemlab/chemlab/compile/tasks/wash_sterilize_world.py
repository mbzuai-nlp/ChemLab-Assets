"""Host-side world for the wash_sterilize class (what the lab operator has done before the agent starts), as lab-config set-up steps.

* Miele PLW 6111: the wash water / heater model is declared (configure_water_model, a trusted host call that must come before the
  washer is switched on; without it every wet programme is refused): an effective heat capacity of 25 L of water in the sump and
  circulation, 8 W/K losses, mains water at 15 C, liquid-water envelope up to 95 C.
* Tuttnauer 3870ELV-D: the room and utilities declaration is already in all203.json (plant.configure); nothing else is needed.
"""
import copy

from ...labbackend import load_config

MIELE_WATER = {'host': 'configure_water_model',
               'kwargs': {'instance_id': None, 'effective_heat_capacity_J_K': 104650.0, 'heat_loss_W_K': 8.0, 'initial_temperature_K': 293.15,
                          'maximum_temperature_K': 368.15, 'evidence': 'task world: 25 L wash water, mains water, liquid-water envelope'}}


def config(base=None):
    """all203.json (or ``base``) with the wash_sterilize world steps added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug == 'miele_plw6111':
            st = copy.deepcopy(MIELE_WATER)
            st['kwargs']['instance_id'] = e.get('instance_id') or e['id']
            e['setup'] = [st] + list(e.get('setup') or [])
    return cfg

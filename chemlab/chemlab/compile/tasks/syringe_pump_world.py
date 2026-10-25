"""Host-side world for the syringe-pump member of the liquid class (New Era NE-1000): what the lab operator has done before the
agent starts.  The default lab already declares the environment and switches the pump on; here the operator also loads a
B-D 10 mL syringe with 8 mL of water and connects the outlet line (0.5 m PTFE 0.8 mm into a reservoir holding 20 mL), as
operator calls of the contract's robot skills.  The syringe diameter is not set: that is the agent's job (manual 7.1 p.12).
"""
import copy

from ...labbackend import load_config

N = 'newera_ne1000'


def world_steps(syringe='bd_10ml', fill_m3=8e-06, reservoir_m3=2e-05):
    return [{'capability': 'syringe.load', 'arguments': {'syringe': syringe, 'liquid_id': 'water', 'fill_volume_m3': fill_m3}},
            {'capability': 'outlet.connect', 'arguments': {'tubing': 'ptfe_0p8mm', 'tubing_length_m': 0.5, 'needle': 'none',
                                                           'backpressure_pa': 0.0, 'reservoir_liquid_m3': reservoir_m3}}]


def config(base=None, **kw):
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        if e['asset_type_id'].split('.', 1)[1] == N:
            e['setup'] = list(e.get('setup') or []) + world_steps(**kw)
    return cfg

"""Host-side world for the formulation class (what the lab operator has done before the agent starts), as lab-config set-up steps.

* BUCHI Encapsulator: the feed bottle filled with 15 mL alginate solution, the gelling bath placed and grounded, compressed air
  connected - sample.configure, bath.configure, air.configure (evaluator, contract examples; the air needs the sample first).
* NanoAssemblr Ignite: a dilution cartridge, the collection tubes, three loaded syringes (diluent, aqueous reagent, ethanol lipids)
  and the syringe heater fitted (evaluator).
* Mini-Glatt: 80 g of powder and 30 mL binder loaded (evaluator powder.load / liquid.load, contract examples); the unit stays
  switched off (the agent switches it on).
* Process 11 and Linari are ready in the default lab (material, coolant, collector, solution).
"""
import copy

from ...labbackend import load_config

ENCAPSULATOR = [{'capability': 'sample.configure', 'arguments': 'contract_example', 'evaluator': True},
                {'capability': 'bath.configure', 'arguments': 'contract_example', 'evaluator': True},
                {'capability': 'air.configure', 'arguments': 'contract_example', 'evaluator': True}]
SYRINGES = [dict(channel=i, source_tag=['diluent', 'aqueous_reagent', 'ethanol_lipids'][i], volume_m3=[10e-6, 3e-6, 1e-6][i],
                 capacity_m3=[10e-6, 3e-6, 1e-6][i], inner_diameter_m=[.0145, .009, .0047][i], density_kg_m3=[1000., 1000., 789.][i],
                 solute_kg_m3=[0., .1, 10.][i], accept_host_compatibility=True) for i in range(3)]
IGNITE = ([{'capability': 'cartridge.install', 'arguments': 'contract_example', 'evaluator': True},
           {'capability': 'tubes.install', 'arguments': 'contract_example', 'evaluator': True}] +
          [{'capability': 'syringe.load', 'arguments': s, 'evaluator': True} for s in SYRINGES] +
          [{'capability': 'heating.configure', 'arguments': 'contract_example', 'evaluator': True}])
GLATT = [{'capability': 'powder.load', 'arguments': 'contract_example', 'evaluator': True},
         {'capability': 'liquid.load', 'arguments': 'contract_example', 'evaluator': True}]


def config(base=None):
    """all203.json (or ``base``) with the formulation world steps added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        add = {'buchi_encapsulatorb': ENCAPSULATOR, 'cytiva_nanoassemblrignite': IGNITE, 'glatt_miniglatt': GLATT}.get(slug)
        if add:
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(add)
    return cfg

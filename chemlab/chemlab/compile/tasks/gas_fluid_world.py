"""Host-side world for the gas_fluid class (what the lab operator has done before the agent starts), as lab-config set-up steps.

* VACUUBRAND PC 3001: a 2 L flask at room pressure is connected to the pump (create_chamber + connect_source; the default lab
  has no vessel, so the pump shows no pressure and refuses to start).
* ELGA PURELAB flex 3: feed water and a purified-water reservoir connected, a 2 L receiving bottle under the dispenser, the
  purification pack installed, and the water quality readings (18.2 MOhm.cm, 25 C) declared.
* INFICON UL1000 Fab: a 10 L stainless test chamber with one weld leak (the contract example) on the inlet (part.connect as
  the evaluator: the leak is truth the agent must not set).
* VICI EUTA: the trusted valve-drive scene (scenes/vici.py) that confirms the rotor's arrival.
* Bronkhorst CEM: nitrogen (4 bar) and water (3 bar) plumbed to the inlets (supply.connect_gas / connect_liquid as the operator).
* Alicat MC: the controller sits in a nitrogen line (3 bar abs cylinder side, 1 atm downstream, 25 C): response time 0.5 s
  declared, ports connected, the line at rest; the gas-line scene (scenes/alicat.py) keeps "no flow" true exactly while the set
  point is 0.
* Inert glovebox (secondary member): the enclosure world (tasks/enclosure_world.py: the glovebox scene).
"""
import copy

from ...labbackend import load_config
from .enclosure_world import config as enclosure_config

VAC_CHAMBER = [{'host': 'create_chamber', 'kwargs': {'chamber_id': 'flask_2l', 'volume_m3': 0.002, 'initial_pressure_pa': 101325.0,
                                                     'leak_throughput_pa_m3_s': 0.0,
                                                     'evidence': 'task world: 2 L round-bottom flask with a cold trap on the pump inlet'}},
               {'host': 'connect_source', 'kwargs': {'instance_id': None, 'chamber_id': 'flask_2l'}}]
ELGA_WORLD = [{'host': 'configure_supply', 'kwargs': {'instance_id': None, 'feed_volume_m3': 0.05, 'purified_volume_m3': 0.02,
                                                      'buffer_capacity_m3': 0.03, 'qualified_feed': True,
                                                      'evidence': 'task world: RO feed connected, 20 L reservoir filled'}},
              {'host': 'connect_receiver', 'kwargs': {'instance_id': None, 'receiver_id': 'bottle_2l', 'volume_m3': 0.0, 'capacity_m3': 0.002,
                                                      'evidence': 'task world: empty 2 L bottle under the dispenser'}},
              {'host': 'install_consumables', 'kwargs': {'instance_id': None, 'evidence': 'task world: purification pack installed'}},
              {'host': 'set_readings', 'kwargs': {'instance_id': None, 'resistivity_ohm_m': 182000.0, 'temperature_k': 298.15,
                                                  'source_kind': 'synthetic', 'evidence': 'task world: 18.2 MOhm.cm at 25 C'}}]
INFICON_PART = {'capability': 'part.connect', 'arguments': 'contract_example', 'evaluator': True}
ALICAT_LINE = [{'host': 'host_configure_dynamics', 'kwargs': {'instance_id': None, 'time_constant_s': 0.5,
                                                             'evidence': 'task world: engineering response time of the MC control loop'}},
               {'host': 'host_connect_ports', 'kwargs': {'instance_id': None, 'upstream_node_id': 'n2_cylinder_regulator',
                                                         'downstream_node_id': 'reactor_inlet'}},
               {'host': 'host_set_boundary', 'kwargs': {'instance_id': None, 'upstream_pressure_Pa': 300000.0, 'downstream_pressure_Pa': 101325.0,
                                                        'temperature_K': 298.15, 'equilibrium': True, 'no_flow': True}}]
CEM_SUPPLY = [{'capability': 'supply.connect_gas', 'arguments': {'gas': 'nitrogen', 'pressure_pa': 400000.0}},
              {'capability': 'supply.connect_liquid', 'arguments': {'composition': {'water': 1.0}, 'pressure_pa': 300000.0}}]


def _with_instance(steps, iid):
    out = []
    for st in steps:
        st = copy.deepcopy(st)
        if 'kwargs' in st and 'instance_id' in st['kwargs']:
            st['kwargs']['instance_id'] = iid
        out.append(st)
    return out


def config(base=None):
    """all203.json (or ``base``) with the gas_fluid world steps added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        iid = e.get('instance_id') or e['id']
        setup = list(e.get('setup') or [])
        if slug == 'vacuubrand_pc3001_vario_select':
            e['setup'] = _with_instance(VAC_CHAMBER, iid) + setup
        elif slug == 'elga_purelab_flex3':
            e['setup'] = _with_instance(ELGA_WORLD, iid) + setup
        elif slug == 'inficon_ul1000fab':
            i = next((n for n, st in enumerate(setup) if st.get('capability') == 'plant.configure'), -1)
            e['setup'] = setup[:i + 1] + [dict(INFICON_PART)] + setup[i + 1:]
        elif slug == 'vici_euta_2sd6mwe':
            e['scene'] = 'chemlab.compile.scenes.vici:ViciScene'
        elif slug == 'alicat_mc_1slpm_rev6':
            e['setup'] = _with_instance(ALICAT_LINE, iid) + setup
            e['scene'] = 'chemlab.compile.scenes.alicat:AlicatScene'
        elif slug == 'bronkhorst_cemw':
            e['setup'] = setup + copy.deepcopy(CEM_SUPPLY)
    return enclosure_config(cfg)

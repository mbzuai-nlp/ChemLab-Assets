"""Host-side world for the bioassay class (what the lab staff have done before the agent starts), as lab-config set-up steps.

The default lab only declares each instrument's plant; nothing is on the bench.  This world adds, as trusted calls (evaluator) or
operator calls (bench work the agent would otherwise do with `chemlab call`):

* CLARIOstar: the fluorescein standard and water kits, a black 96-well plate "wp" with fluorescein in A1:A3 and water in B1:B3;
* 405 TS: an ELISA plate "p1" on the bench (the default set-up already filled the four buffer bottles and switched it on);
* CFX Opus: a GAPDH standard sample and a prepared plate "plate_1" (A1 standard, B1 NTC);
* Biacore T200: switched on and connected, running buffer and water bottles, waste bottle emptied, the sample and reagent rack with
  running buffer in R1B1 (no chip: loading the chip is the agent's job);
* PEAQ-ITC: switched on with the wash module, EDTA in MES (cell) and CaCl2 in the same buffer (syringe) prepared and degassed;
* FACSAria III: switched on, a stained PBMC sample and a tube "t1" of it on the bench; ``ready=True`` also starts the fluidics and
  the stream (for tasks that begin with a running cytometer).
"""
import copy

from ...labbackend import load_config

EV = True

BMG = [{'capability': 'kit.provide', 'arguments': {'kit': 'fluorescein_standard'}, 'evaluator': EV},
       {'capability': 'kit.provide', 'arguments': {'kit': 'water'}, 'evaluator': EV},
       {'capability': 'plate.prepare', 'arguments': {'plate_id': 'wp', 'format': '96', 'type': 'black'}, 'evaluator': EV},
       {'capability': 'well.dispense', 'arguments': {'plate_id': 'wp', 'wells': ['A1:A3'], 'source_id': 'fluorescein_standard', 'volume_m3': 1e-7},
        'evaluator': EV},
       {'capability': 'well.dispense', 'arguments': {'plate_id': 'wp', 'wells': ['B1:B3'], 'source_id': 'water', 'volume_m3': 1e-7}, 'evaluator': EV}]

BIOTEK = [{'capability': 'plate.provide', 'arguments': {'plate_id': 'p1', 'plate_type': 'costar_3590', 'fill_volume_m3': 1e-07, 'liquid_id': 'pbs_1x',
                                                         'unbound_mol': 1e-12, 'nonspecific_mol': 2e-14, 'specific_mol': 5e-14, 'cells_per_well': 0.0,
                                                         'cell_adhesion': 'none', 'wells': None}, 'evaluator': EV}]

CFX_WELLS = [{'well': 'A1', 'sample_type': 'standard', 'sample_id': 'gapdh_std', 'dilution': 1.0, 'template_volume_m3': 2e-09,
              'reaction_volume_m3': 2e-08, 'assays': ['hs_gapdh'], 'chemistry': 'sybr', 'quantity_copies': 1000000.0},
             {'well': 'B1', 'sample_type': 'ntc', 'reaction_volume_m3': 2e-08, 'assays': ['hs_gapdh'], 'chemistry': 'sybr'}]
CFX = [{'capability': 'sample.provide', 'arguments': {'sample_id': 'gapdh_std', 'kind': 'standard', 'copies_per_m3': {'hs_gapdh': 5e14},
                                                      'declared_copies_per_m3': {'hs_gapdh': 5e14}}, 'evaluator': EV},
       {'capability': 'plate.prepare', 'arguments': {'plate_id': 'plate_1', 'plate_type': 'white_lowprofile_96', 'seal': 'optical_film',
                                                     'wells': CFX_WELLS}, 'evaluator': EV}]

BIACORE = [{'capability': 'power.set', 'arguments': {'enabled': True}, 'evaluator': EV}, {'advance_s': 40.0},
           {'capability': 'solution.prepare', 'arguments': {'solution_id': 'run_buffer', 'volume_m3': 1e-3, 'base': 'hbs_ep_plus'}, 'evaluator': EV},
           {'capability': 'solution.prepare', 'arguments': {'solution_id': 'dist_water', 'volume_m3': 5e-4, 'base': 'water'}, 'evaluator': EV},
           {'capability': 'bottle.place', 'arguments': {'slot': 'buffer_a', 'solution_id': 'run_buffer', 'volume_m3': 9.5e-4}, 'evaluator': EV},
           {'capability': 'bottle.place', 'arguments': {'slot': 'water', 'solution_id': 'dist_water', 'volume_m3': 4.5e-4}, 'evaluator': EV},
           {'capability': 'bottle.empty_waste', 'arguments': {}, 'evaluator': EV},
           {'capability': 'rack.eject', 'arguments': {}, 'evaluator': EV}, {'advance_s': 10.0},
           {'capability': 'rack.configure', 'arguments': {'config': 'sample_and_reagent_rack', 'plate': 'none'}, 'evaluator': EV},
           {'capability': 'solution.prepare', 'arguments': {'solution_id': 'buf_s', 'volume_m3': 6e-7, 'base': 'hbs_ep_plus'}, 'evaluator': EV},
           {'capability': 'rack.place', 'arguments': {'position': 'R1B1', 'solution_id': 'buf_s', 'volume_m3': 4e-7}, 'evaluator': EV},
           {'capability': 'rack.retract', 'arguments': {}, 'evaluator': EV}, {'advance_s': 10.0}]

ITC_BUF = {'buffer_id': 'mes', 'concentration_mol_m3': 10.0, 'ph': 5.6}
ITC = [{'capability': 'power.set', 'arguments': {'enabled': True}, 'evaluator': EV},
       {'capability': 'wash.power_set', 'arguments': {'enabled': True}, 'evaluator': EV},
       {'capability': 'solution.prepare', 'arguments': {'solution_id': 'edta', 'volume_m3': 3e-6, 'buffer': ITC_BUF,
                                                        'solutes': [{'species_id': 'edta', 'concentration_mol_m3': 0.1}]}, 'evaluator': EV},
       {'capability': 'solution.prepare', 'arguments': {'solution_id': 'ca', 'volume_m3': 1e-6, 'buffer_from': 'edta',
                                                        'solutes': [{'species_id': 'ca2+', 'concentration_mol_m3': 1.0}]}, 'evaluator': EV},
       {'capability': 'solution.degas', 'arguments': {'solution_id': 'edta'}, 'evaluator': EV},
       {'capability': 'solution.degas', 'arguments': {'solution_id': 'ca'}, 'evaluator': EV}]

FACS_POPS = [{'population_id': 'cd4_t', 'particle_id': 'human_t_cell_cd4', 'concentration_per_m3': 4e12,
              'stains': [{'dye': 'fluorescein_fitc', 'molecules': 45000.0, 'cv': 0.35}]},
             {'population_id': 'b_cells', 'particle_id': 'human_b_cell', 'concentration_per_m3': 1.5e12,
              'stains': [{'dye': 'fluorescein_fitc', 'molecules': 300.0, 'cv': 0.5}]}]
FACS = [{'capability': 'power.set', 'arguments': {'enabled': True}, 'evaluator': EV},
        {'capability': 'lasers.set', 'arguments': {'laser': 'blue', 'enabled': True}, 'evaluator': EV},
        {'capability': 'sample.provide', 'arguments': {'sample_id': 'pbmc', 'volume_m3': 3e-6, 'populations': FACS_POPS}, 'evaluator': EV},
        {'capability': 'tube.prepare', 'arguments': {'tube_id': 't1', 'tube_type': '12x75', 'sources': [{'sample_id': 'pbmc', 'volume_m3': 5e-7}],
                                                     'buffer_volume_m3': 5e-7}, 'evaluator': EV}]
FACS_READY = [{'capability': 'fluidics.startup', 'arguments': {}, 'evaluator': EV}, {'advance_s': 500.0},
              {'capability': 'stream.set', 'arguments': {'on': True}, 'evaluator': EV}, {'advance_s': 30.0}]

WORLD = {'bmg_clariostarplus': BMG, 'biotek_405ts': BIOTEK, 'biorad_cfxopus': CFX, 'cytiva_biacoret200': BIACORE,
         'malvern_microcalpeaq': ITC, 'bd_facsariaiii': FACS}


def config(base=None, ready=False):
    """all203.json (or ``base``) with the bioassay world steps added after each member's own set-up."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in WORLD:
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(WORLD[slug]) + (copy.deepcopy(FACS_READY) if ready and slug == 'bd_facsariaiii' else [])
    return cfg


def ready_config(base=None):
    return config(base, ready=True)

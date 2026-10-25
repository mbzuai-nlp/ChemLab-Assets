"""Host-side world for the imaging class: what the lab operator has done before the agent starts (lab-config set-up steps).

* specimens / samples on each instrument's bench (the contracts' own example specimens, provided by the trusted host or the
  operator: specimen.provide / sample.provide);
* instruments switched on and started up where the default lab leaves them off (Keyence, BX53-P, NX10, NaioSTM);
* the NX10 prepared for contact mode by the operator: a contact cantilever (CONTAL-G) mounted, head re-attached and configured
  (probe exchange is set-up work, not a class verb);
* the Keyence head coarse-focused near the sample plate (the operator's coarse focus; the 2.5x viewing objective stays in).

Nothing here is reachable from the sandbox.
"""
import copy

from ...labbackend import load_config


def _example(cfg_entry_contract_cap):
    return 'contract_example'


SEM_SPECIMENS = [
    {'specimen_id': 'fe_si_polished', 'phases': [{'substance_id': 'iron', 'area_fraction': 0.7, 'grain_size_m': 2e-05},
                                                 {'substance_id': 'silicon', 'area_fraction': 0.3, 'grain_size_m': 2e-05}],
     'diameter_m': 0.012, 'height_m': 0.005, 'topography': 'flat_polished', 'coating': 'none', 'coating_thickness_m': 0.0, 'wet': False},
]
CT_SAMPLE = {'sample_id': 'alumina_pellet', 'kind': 'cylinder', 'diameter_m': 0.006, 'height_m': 0.006,
             'layers': [{'thickness_m': 0.006, 'materials': [{'substance_id': 'alumina', 'volume_fraction': 1.0}]}],
             'pores': {'count': 120, 'median_radius_m': 0.00012, 'sigma_ln': 0.35, 'layer': 0, 'seed': 7},
             'cracks': [{'center_m': [0.0, 0.0, 0.003], 'normal': [1.0, 0.0, 0.0], 'radius_m': 0.0018, 'aperture_m': 3e-05}],
             'inclusions': [], 'container': None, 'pin': None}
GEL = {'sample_id': 'gel1', 'support': 'agarose_gel', 'width_m': 0.1, 'height_m': 0.08,
       'lanes': [{'x_m': 0.02, 'width_m': 0.005, 'bands': []}, {'x_m': 0.04, 'width_m': 0.005, 'bands': []}]}
SLIDE = {'sample_id': 'quartz_section', 'kind': 'thin_section', 'substance_id': 'main_sio2', 'thickness_m': 3e-05, 'slide_thickness_m': 0.001,
         'grains': 40, 'defects': 0, 'orientation': None, 'immersion_oil': False}

KEYENCE_COARSE_Z_M = 0.0322          # head Z with the sample-plate surface (+0.5 mm wafer) near the focal plane (operator's coarse focus)


def ev(cap, args):
    return {'capability': cap, 'arguments': args, 'evaluator': True}


def op(cap, args):
    return {'capability': cap, 'arguments': args}


STEPS = {
    'jeol_jsmit200': [ev('specimen.provide', 'contract_example')],
    'jeol_jem1400flash': [ev('specimen.provide', 'contract_example')],
    'keyence_vkx3000': [ev('sample.provide', 'contract_example'), op('power.set', {'enabled': True}), {'advance_s': 90.0},
                        op('focus.move', {'z_m': KEYENCE_COARSE_Z_M}), {'advance_s': 10.0}],
    'evident_bx53p': [op('power.set', {'enabled': True}), op('sample.provide', SLIDE)],
    'park_nx10': [ev('sample.provide', 'contract_example'), ev('probe.provide', {'probe_id': 'c1', 'type_id': 'contal_g'}),
                  op('power.set', {'enabled': True}), {'advance_s': 60.0}, op('head.remove', {}), {'advance_s': 20.0},
                  op('probe.mount', {'probe_id': 'c1'}), {'advance_s': 25.0}, op('head.attach', {}), {'advance_s': 20.0},
                  op('head.configure', {'mode': 'contact', 'cantilever_db': 'contal_g'})],
    'nanosurf_naiostm': [ev('sample.provide', 'contract_example'), op('power.set', {'enabled': True}), {'advance_s': 30.0}],
    'bruker_x4poseidon': [ev('sample.provide', CT_SAMPLE)],
    'thermo_ibrightfl1500': [op('sample.provide', GEL)],
}


def config(base=None):
    """all203.json (or ``base``) with the imaging world steps appended to each member's set-up."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in STEPS:
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(STEPS[slug])
    return cfg


_ = _example, SEM_SPECIMENS

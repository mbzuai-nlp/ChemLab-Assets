"""Host-side world of the property_meter class: what the lab operator has done before the agent starts, as lab-config set-up
steps (evaluator capability calls for samples, operator calls for lids, advances for time).  Nothing here is a class verb.

* DMA 5002, Abbemat, AQUALAB, TL2300, HX204, MP70: a sample in the cell (``sample.configure`` / ``capillary`` as the evaluator,
  lids opened and closed by the operator around it).
* 914: the beaker (``"solution"``) fed by the trusted scene ``chemlab.compile.scenes.metrohm914``.
* P-4200 and K-7400S need cells or tubes exchanged between steps (blank -> quartz plate -> sample; water -> standard ->
  sample).  The lab cannot swap them while the agent works, so the world offers one entry per stage: the same instrument type
  under another lab name (``jasco_p4200_quartz``, ``knauer_k7400s_ready`` ...), each set up to the point where the agent's
  step begins (earlier steps done by the operator with the same native calls).
"""
import copy

from ...hosts import contract_index
from ...labbackend import load_config

K0 = 273.15


def ev(cap, args):
    return {'capability': cap, 'arguments': copy.deepcopy(args), 'evaluator': True}


def op(cap, args):
    return {'capability': cap, 'arguments': copy.deepcopy(args)}


def wait(s):
    return {'advance_s': float(s)}


_EX = {}


def example(slug, cap):
    if slug not in _EX:
        import json
        _EX[slug] = json.loads(contract_index()['instrument.' + slug].read_text(encoding='utf-8'))
    return copy.deepcopy(_EX[slug]['capabilities'][cap].get('example_arguments') or {})


# ------------------------------------------------------------------ samples per member
DMA_ETHANOL = ev('sample.configure', {'substance_id': 'ethanol', 'volume_m3': 2e-06, 'bubble_fraction': 0.0, 'contamination_fraction': 0.0})
ABM_ETHANOL = [op('lid.set_angle', {'angle_rad': 1.5707963267948966}),
               ev('sample.configure', {'volume_fractions': {'ethanol': 1.0}, 'volume_m3': 1e-07, 'matrix_id': 'ethanol', 'contamination_fraction': 0.0}),
               op('lid.set_angle', {'angle_rad': 0.0})]


def aqualab_sample():
    # the 0.884 aw verification standard (sodium chloride 3.209 mol/kg, template record 0: aw 0.8838 at 25 °C), set explicitly: the
    # contract example is now the template's headline record (aw 0.9002, aqualab 1.3.1), which is not the certified standard
    std = dict(example('aqualab_4te', 'sample.configure'), substance_id='sodium_chloride', record_index=0)
    return [op('lid.set_angle', {'angle_rad': 1.5707963267948966}), ev('sample.configure', std),
            op('lid.set_angle', {'angle_rad': 0.0}), op('latch.set_angle', {'angle_rad': 1.5707963267948966})]


def hach_standard():
    return [op('lid.set_angle', {'angle_rad': 1.7}), ev('sample.configure', example('hach_tl2300epa', 'sample.configure')),
            op('lid.set_angle', {'angle_rad': 0.0})]


HX_SAMPLE = [op('lid.set_position', {'position_m': 0.1085}), ev('sample.configure', {'species_kg': {'water': 0.0005}, 'residue_kg': 0.0045}),
             op('lid.set_position', {'position_m': 0.0})]
MP_BENZOIC = [ev('sample.configure', {'channel': 0, 'substance_id': 'benzoic_acid', 'mass_kg': 2e-06, 'purity': 1.0, 'particle_size_m': 5e-05,
                                      'decomposition_onset_k': None, 'decomposition_source': ''}),
              ev('sample.configure', {'channel': 1, 'substance_id': 'benzoic_acid', 'mass_kg': 2e-06, 'purity': 1.0, 'particle_size_m': 5e-05,
                                      'decomposition_onset_k': None, 'decomposition_source': ''})]
PH_BUFFER = {'kind': 'ph_buffer', 'substance_id': 'nist_srm_186g_phosphate_equimolal_buffer', 'temperature_k': 298.15, 'standard_channel': 'ph'}
SCENE_914 = 'chemlab.compile.scenes.metrohm914:Metrohm914Scene'

# ------------------------------------------------------------------ P-4200 stages
JASCO_METHOD = {'lamp': 'Na', 'wavelength_m': 5.89e-07, 'integration_s': 1.0, 'repeats': 3, 'declared_concentration_kg_m3': 100.0, 'declared_path_m': 0.1}
QUARTZ_RAD = 0.17453292519943295        # a 10.000 deg control plate


def jasco_cells():
    s = example('jasco_p4200', 'sample.register')
    blank = dict(s, sample_id='blank_water', kind='blank', template_id='', concentration_kg_m3=0.0, accept_missing_source_concentration=False)
    quartz = dict(s, sample_id='quartz_10deg', kind='quartz', template_id='', solvent='', volume_m3=0.0, concentration_kg_m3=0.0,
                  quartz_angle_rad=QUARTZ_RAD, accept_missing_source_concentration=False, accept_ideal_quartz=True)
    return [ev('sample.register', blank), ev('sample.register', quartz), ev('sample.register', s)]


def jasco_swap(new_id, lamp=True):
    out = [op('lid.set', {'opened': True}), ev('sample.unload', {'recipient_id': 'external_rack'}), ev('sample.load', {'sample_id': new_id}),
           op('lid.set', {'opened': False})]
    if lamp:
        out.append(op('lamp.set', {'enabled': True}))
    return out


def jasco_stage(stage):
    """'blank': blank cell in, nothing else; 'quartz': method, blank read, quartz plate in; 'sample': + quartz calibration, sample in."""
    st = jasco_cells() + [op('lid.set', {'opened': True}), ev('sample.load', {'sample_id': 'blank_water'}), op('lid.set', {'opened': False})]
    if stage == 'blank':
        return st
    st += [op('method.configure', JASCO_METHOD), op('lamp.set', {'enabled': True}), op('acquisition.start', {'kind': 'blank'}), wait(120)]
    st += jasco_swap('quartz_10deg')
    if stage == 'quartz':
        return st
    st += [op('acquisition.start', {'kind': 'quartz'}), wait(120),
           op('calibration.quartz', {'certified_angle_rad': QUARTZ_RAD, 'accept_simulated_standard': True})]
    st += jasco_swap('sample_01')
    return st


# ------------------------------------------------------------------ K-7400S stages
KNAUER_METHOD = {'sample_volume_m3': 1.5e-07, 'freeze_k': 267.15, 'cooling_limit_k': 258.15, 'sample_interval_s': 1.0}


def knauer_tube(sample_id, molality):
    s = example('knauer_k7400s', 'sample.load')
    s['sample_id'] = sample_id
    if molality is None:                        # pure water reference
        s.update(kind='reference', components=[])
    else:
        s['components'] = [{'substance_id': 'NaCl', 'molality_mol_kg': molality}]
    return s


def knauer_load(sample_id, molality, first=False):
    out = [] if first else [op('head.position', {'position': 'raised'}), ev('sample.unload', {})]
    if first:
        out.append(op('head.position', {'position': 'raised'}))
    out += [ev('sample.load', knauer_tube(sample_id, molality)), op('head.position', {'position': 'measuring'})]
    return out


def knauer_stage(stage):
    """'water': method set, water tube on the head; 'ready': water + 0.2 mol/kg NaCl calibration done, the sample tube on."""
    st = [wait(1), op('method.configure', KNAUER_METHOD)] + knauer_load('water', None, first=True)
    if stage == 'water':
        return st
    st += [op('acquisition.start', {'purpose': 'calibration'}), wait(400), op('calibration.accept', {'known_osmolality_mol_kg': 0.0})]
    st += knauer_load('std_400', 0.2166) + [wait(120), op('acquisition.start', {'purpose': 'calibration'}), wait(400),
                                         op('calibration.accept', {'known_osmolality_mol_kg': 0.4})]
    st += knauer_load('sample_nacl', 0.1) + [wait(120)]
    return st


# ------------------------------------------------------------------ the lab config
EXTRA_ENTRIES = {          # lab name -> (asset slug, stage)
    'jasco_p4200_quartz': ('jasco_p4200', 'quartz'),
    'jasco_p4200_sample': ('jasco_p4200', 'sample'),
    'knauer_k7400s_water': ('knauer_k7400s', 'water'),
    'knauer_k7400s_ready': ('knauer_k7400s', 'ready'),
    'mt_hx204_empty': ('mt_hx204', 'empty'),
}


def world_steps(slug, stage=None):
    if slug == 'antonpaar_dma5002':
        return [DMA_ETHANOL]
    if slug == 'antonpaar_abbematadvanced':
        return copy.deepcopy(ABM_ETHANOL)
    if slug == 'aqualab_4te':
        return aqualab_sample()
    if slug == 'hach_tl2300epa':
        return hach_standard()
    if slug == 'mt_hx204':
        return [] if stage == 'empty' else copy.deepcopy(HX_SAMPLE)
    if slug == 'mt_mp70':
        return copy.deepcopy(MP_BENZOIC)
    if slug == 'jasco_p4200':
        return jasco_stage(stage or 'blank')
    if slug == 'knauer_k7400s':
        return knauer_stage(stage) if stage else []
    return []


def add_world(e, stage=None):
    slug = e['asset_type_id'].split('.', 1)[1]
    e['setup'] = list(e.get('setup') or []) + world_steps(slug, stage)
    if (slug == 'knauer_k7400s' and stage is None) or stage == 'empty':
        e['default_world'] = False            # bare entries: no tube / no sample (the default lab has its reference in)
    if slug == 'metrohm_914':
        e['scene'] = SCENE_914
        e['solution'] = copy.deepcopy(PH_BUFFER)
    return e


def config(base=None, extra=True):
    """all203.json (or ``base``) with the property_meter world; plus the staged entries (P-4200, K-7400S) when ``extra``."""
    cfg = copy.deepcopy(base or load_config())
    by_id = {e['id']: e for e in cfg['instruments']}
    for e in cfg['instruments']:
        add_world(e)
    if extra:
        for name, (slug, stage) in EXTRA_ENTRIES.items():
            src = next((x for x in cfg['instruments'] if x['asset_type_id'] == 'instrument.' + slug), None)
            if src is None or name in by_id:
                continue
            e = copy.deepcopy(next(x for x in load_config()['instruments'] if x['asset_type_id'] == 'instrument.' + slug))
            e['id'] = name
            e['name'] = '%s (%s stage)' % (e.get('name', slug), stage)
            cfg['instruments'].append(add_world(e, stage))
    return cfg

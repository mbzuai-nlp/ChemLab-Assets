"""Host-side world for the structure class: what the lab operator has done before the agent starts (lab-config set-up steps and,
for the MiniFlex, a trusted host scene).

* MiniFlex 600: the external chiller connected and running, and two prepared powder holders whose diffraction comes from the
  public RRUFF / COD templates (scene chemlab.compile.scenes.miniflex: "chiller", "samples"); switched on.
* XtaLAB Synergy-R: an aspirin crystal on its loop on the bench (contract example); switched on.
* SAXSpace: a lysozyme solution on the bench (contract example); switched on (SAXSdrive start-up and chiller flow).
* AXIS Supra+: the lab declared (plant.configure, the default lab has none), switched on and started up; an SiO2-on-Si film sample
  on the bench (contract example).
* alpha-SE: a PMMA-on-Si wafer on the bench (contract example); switched on and the lamp warmed up.

Nothing here is reachable from the sandbox.
"""
import copy

from ...labbackend import load_config

MINIFLEX_SAMPLES = {'quartz_holder': [{'phase_id': 'quartz', 'weight_fraction': 1.0}],
                    'calcite_holder': [{'phase_id': 'calcite', 'weight_fraction': 1.0}]}


def ev(cap, args):
    return {'capability': cap, 'arguments': args, 'evaluator': True}


def op(cap, args):
    return {'capability': cap, 'arguments': args}


STEPS = {
    'rigaku_miniflex600_external_cooling': [op('power.set', {'enabled': True})],
    'rigaku_xtalabsynergy': [ev('sample.provide', 'contract_example'), op('power.set', {'enabled': True}), {'advance_s': 30.0}],
    'antonpaar_saxspace': [ev('sample.provide', 'contract_example'), op('power.set', {'enabled': True}), {'advance_s': 90.0}],
    'kratos_axissupra': [op('plant.configure', {'room_k': 295.15, 'mains_voltage_v': 200.0, 'noise_seed': 1}), op('power.set', {'enabled': True}),
                         {'advance_s': 700.0}, op('sample.provide', 'contract_example')],
    'ja_alphase': [ev('sample.provide', 'contract_example'), op('power.set', {'enabled': True}), {'advance_s': 620.0}],
}
EXTRA = {
    'rigaku_miniflex600_external_cooling': {'scene': 'chemlab.compile.scenes.miniflex:MiniFlexScene', 'chiller': 'chiller_01',
                                            'samples': MINIFLEX_SAMPLES},
}


def config(base=None):
    """all203.json (or ``base``) with the structure world steps appended to each member's set-up."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in STEPS:
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(STEPS[slug])
        if slug in EXTRA:
            e.update(copy.deepcopy(EXTRA[slug]))
    return cfg

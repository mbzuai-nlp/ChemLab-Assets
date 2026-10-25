"""Host-side world for the thin class: the synthesis twins (CEM Liberty Blue, ÄKTA oligosynt) need their run set up by the lab
before the agent can start it - the resin queue / oligo method and the explicit kinetics (evaluator world calls with the
contract's example arguments, as plant.configure already is in the default lab)."""
import copy

from ...labbackend import load_config

WORLD = {
    'cem_libertyblue': [{'capability': 'kinetics.configure', 'arguments': 'contract_example', 'evaluator': True},
                        {'capability': 'queue.configure', 'arguments': 'contract_example', 'evaluator': True}],
    'cytiva_ktaoligosynt': [{'capability': 'kinetics.configure', 'arguments': 'contract_example', 'evaluator': True},
                            {'capability': 'method.configure', 'arguments': 'contract_example', 'evaluator': True}],
}


def config(base=None):
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in WORLD:
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(WORLD[slug])
    return cfg

"""Host-side world for the solid_prep class (what the lab operator has done before the agent starts), as lab-config set-up steps
appended after each member's default bring-up (all203.json).  Loading jars, sieves, bottles, dies and crucibles is done by people,
so these are evaluator calls, never class verbs:

* PM 100 planetary mill, AS 200 sieve shaker, LeNeo fusion: the default bring-up already loads the jar / the sieve stack / the
  crucible and the sample (contract examples).
* PT 100 rotary divider: 0.5 kg of quartz then aragonite in the hopper (contract example) and all ten bottles attached.
* Atlas 15T manual press: the 13 mm die (contract example, 10 t maximum) and 200 mg of KBr in it, front guard still raised.
"""
import copy

from ...labbackend import load_config


def ev(cap, args='contract_example'):
    return {'capability': cap, 'arguments': args, 'evaluator': True}


WORLD = {
    'retsch_pt100': [ev('sample.configure'), ev('mount.configure', {'all_bottles_attached': True})],
    'specac_atlasmanual': [ev('die.configure'), ev('sample.configure')],
}


def config(base=None, extra=None):
    """all203.json (or ``base``) with the solid_prep world steps added; ``extra``: {slug: [more steps]}."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        steps = list(WORLD.get(slug, [])) + list((extra or {}).get(slug, []))
        if steps:
            e['setup'] = list(e.get('setup') or []) + copy.deepcopy(steps)
    return cfg

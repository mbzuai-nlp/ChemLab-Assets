"""Write the default lab config (configs/all203.json): every instrument with a host recipe (recipes.py), in one lab.

    python3 -m chemlab.make_config [--out configs/all203.json]

Per instrument: id (= asset_type_id without "instrument."), English name / brand / model / domain (the release catalog,
catalog/catalog.json), and the host set-up:

* the 106 front-panel instruments: the release bring-up (examples/panel_instruments.json), which brings each one to its
  home screen - world set-up calls as the evaluator, operator gestures (power switch, keys) as the lab operator;
* the others: the world set-up ``plant.configure`` with the contract's example arguments (as the evaluator), when the
  contract has that capability and it is not agent-visible; otherwise nothing.

Only data is read (recipes are parsed, not imported).  The config can be edited by hand afterwards.
"""
import argparse
import json
from pathlib import Path

from .hosts import CHEMSIM_ROOT, contract_index
from .labbackend import CONFIG_DIR, DEFAULT_CONFIG
from .recipes import all_recipes

PROJECT = Path(__file__).resolve().parents[3]
# names and the panel bring-up: the release builder's tables when they are next to this project, else the release's own copies
NAMES_EN = PROJECT / 'release' / '_build' / 'names_en.json'
CATALOG = CHEMSIM_ROOT / 'catalog' / 'catalog.json'
PANEL_TABLE = next((p for p in (PROJECT / 'release' / '_build' / 'examples' / 'panel_instruments.json',
                                CHEMSIM_ROOT / 'examples' / 'panel_instruments.json') if p.exists()), CHEMSIM_ROOT / 'examples' / 'panel_instruments.json')


def _names():
    """{'assets': {slug: [name, brand, model, domain]}, '_domains': {key: [name, ...]}} from names_en.json or catalog.json."""
    if NAMES_EN.exists():
        return json.loads(NAMES_EN.read_text(encoding='utf-8'))
    if CATALOG.exists():
        cat = json.loads(CATALOG.read_text(encoding='utf-8'))
        return {'assets': {a['slug']: [a['name'], a['maker'], a['model'], a['domain']] for a in cat['assets']},
                '_domains': {d['key']: [d['name']] for d in cat['domains']}}
    return {'assets': {}, '_domains': {}}


# Set-ups that need more than the default.  Each entry cites the contract precondition that asks for it.
SETUP_OVERRIDES = {
    # plant.configure precondition (contract): "首次打开罩和夹具" - cover and clamp open first (runtime: cover >= 0.079 m, clamp >= 0.039 m)
    'cem_libertyblue': [{'capability': 'cover.set_lift', 'arguments': 'contract_example'},
                        {'capability': 'clamp.set_lift', 'arguments': 'contract_example'},
                        {'capability': 'plant.configure', 'arguments': 'contract_example', 'evaluator': True}],
    # plant.configure precondition (contract): "首次开夹" - clamp open first (runtime: clamp >= 0.039 m)
    'cytiva_ktaoligosynt': [{'capability': 'clamp.set_lift', 'arguments': 'contract_example'},
                            {'capability': 'plant.configure', 'arguments': 'contract_example', 'evaluator': True}],
}


def _place(bringup, steps):
    """Put the default-lab steps into the bring-up: "at": "start" (before it, e.g. configure while still off), "before:CAP" /
    "after:CAP" (next to the first bring-up step with that capability), or at the end (the default)."""
    out = list(bringup)
    tail = []
    groups = {}
    for st in steps:
        at = st.pop('at', None) or 'end'
        groups.setdefault(at, []).append(st)
    for at, sts in groups.items():
        if at == 'end':
            tail += sts
            continue
        if at == 'start':
            out = sts + out
            continue
        where, _, cap = at.partition(':')
        idx = next((i for i, x in enumerate(out) if x.get('capability') == cap), None)
        if idx is None:
            tail += sts
        else:
            out[idx + (1 if where == 'after' else 0):idx + (1 if where == 'after' else 0)] = sts
    return out + tail


def build(default_lab=True):
    """The all-203 lab config; ``default_lab=False``: the bring-up only (what chemlab/default_world_gen.py diffs the class
    worlds against)."""
    from .default_world import DEFAULTS
    from .default_world_overrides import OVERRIDES
    recipes = all_recipes()
    index = contract_index()
    names = _names()
    panels = json.loads(PANEL_TABLE.read_text(encoding='utf-8'))['instruments'] if PANEL_TABLE.exists() else {}
    out = []
    for atid in sorted(recipes, key=lambda a: a.split('.', 1)[1]):
        slug = atid.split('.', 1)[1]
        contract = json.loads(index[atid].read_text(encoding='utf-8'))
        n = names['assets'].get(slug) or [contract.get('name_zh') or slug, None, None, None]
        e = {'id': slug, 'asset_type_id': atid, 'name': n[0], 'brand': n[1], 'model': n[2], 'domain': n[3]}
        if slug in SETUP_OVERRIDES:
            e['setup'] = SETUP_OVERRIDES[slug]
            e['setup_source'] = 'world set-up in the order the contract preconditions ask for'
            if slug in panels:
                e['panel'] = {'main_display': panels[slug].get('main_display')}
        elif slug in panels:
            e['panel'] = {'main_display': panels[slug].get('main_display')}
            e['setup'] = panels[slug]['bringup']
            e['setup_source'] = 'release panel bring-up (home screen)'
        else:
            pc = contract['capabilities'].get('plant.configure')
            if pc and pc.get('visibility') != 'agent' and pc.get('availability') == 'implemented':
                e['setup'] = [{'capability': 'plant.configure', 'arguments': 'contract_example', 'evaluator': True}]
                e['setup_source'] = 'world set-up: plant.configure with the contract example'
            else:
                e['setup'] = []
        dw, ov = (DEFAULTS.get(slug) or {}, OVERRIDES.get(slug) or {}) if default_lab else ({}, {})
        if dw or ov:
            drop_bring = set(ov.get('replace_bringup') or [])
            e['setup'] = [st for st in e.get('setup') or [] if st.get('capability') not in drop_bring]
            drop = ov.get('drop') or []
            steps = [] if drop == '*' else [dict(st) for st in dw.get('setup') or [] if (st.get('capability') or st.get('host')) not in set(drop)]
            steps += [dict(st) for st in ov.get('add') or []]
            for st in steps:
                st.setdefault('default', 'state')
            e['setup'] = _place(list(e['setup']), steps)
            e['setup_source'] = (e.get('setup_source') or 'none') + ' + the default lab (chemlab/default_world.py)'
            for k, v in dict(dw.get('entry') or {}, **(ov.get('entry') or {})).items():
                e[k] = v
        out.append(e)
    return {'name': 'all203', 'about': 'Every Chemistry-Sim instrument with a Matterix host recipe, in one simulated lab with one clock, '
                                      'set up as a well-run lab at the start of a working day (the default lab: chemlab/default_world.py). '
                                      'Generated by chemlab/make_config.py; set-up steps use the release bring-up format.',
            'step_s': 1.0, 'domains': {k: v[0] for k, v in names.get('_domains', {}).items()}, 'instruments': out,
            'inventory': INVENTORY_FILE}


INVENTORY_FILE = 'inventory_default.json'


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=str(DEFAULT_CONFIG))
    a = ap.parse_args(argv)
    cfg = build()
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(cfg, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('%s: %d instruments (%d with a front panel)' % (a.out, len(cfg['instruments']), sum(1 for e in cfg['instruments'] if 'panel' in e)))
    from . import inventory
    inv = inventory.build(cfg)                                      # the default lab's containers (one record each), next to it
    bad = inventory.check(inv)
    if bad:
        raise SystemExit('inventory problems: %s' % bad)
    ip = Path(a.out).with_name(INVENTORY_FILE)
    ip.write_text(json.dumps(inv, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    print('%s: %d containers at %d places' % (ip, len(inv['containers']), len(inv['places'])))
    import copy
    for name, v in inventory.VARIANTS.items():                      # task-world variants: lab config + inventory, one pair each
        vinv = inventory.variant(inv, name)
        bad = inventory.check(vinv)
        if bad:
            raise SystemExit('inventory variant %s problems: %s' % (name, bad))
        vfile = 'inventory_task_%s.json' % name
        vcfg = copy.deepcopy(cfg)
        vcfg['name'] = 'all203_%s' % name
        vcfg['about'] = cfg['about'] + ' Task-world variant %s: %s' % (name, v['about'])
        vcfg['inventory'] = vfile
        for e in vcfg['instruments']:
            if e['id'] in (v.get('entry_steps') or {}):
                e['setup'] = list(e.get('setup') or []) + copy.deepcopy(v['entry_steps'][e['id']])
        Path(a.out).with_name(vfile).write_text(json.dumps(vinv, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        cp = Path(a.out).with_name('%s_%s.json' % (Path(a.out).stem, name))
        cp.write_text(json.dumps(vcfg, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
        print('%s + %s: variant %s' % (cp, vfile, name))


if __name__ == '__main__':
    main()

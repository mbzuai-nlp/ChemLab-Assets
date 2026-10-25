"""Host recipes for every instrument runtime: how each Chemistry-Sim runtime host is built and how its clock is advanced.

A recipe records, per asset_type_id, the runtime module and class, constructor arguments, instance creation, sessions, host
set-up calls and the clock-advance mode.  There are two tables, ``RECIPES`` and ``PANEL_RECIPES``.

Where the recipes come from:

* ``configs/host_recipes.json`` (shipped): both tables as plain JSON.  This is what a release checkout uses.
* The simulator bridge ``runtime_bridge.py`` of the Matterix (Isaac Lab) integration, when that checkout is present
  (``$MATTERIX_PATH``, default: a ``Matterix`` folder next to this project).  It is the original of the JSON file and wins
  when it exists.  The module imports torch and Isaac Lab, so it is never imported here: its source is parsed with ``ast``
  and only the recipe literals (dicts, strings, numbers, booleans and the module-level constants they name) are evaluated.

Regenerate the JSON from the bridge (needs the Matterix checkout) and check that the two agree:

    python -m chemlab.recipes --export          # write configs/host_recipes.json
    python -m chemlab.recipes --check           # exit 1 if the JSON differs from the bridge
    python -m chemlab.recipes                   # print where the recipes are read from and how many there are
"""
import argparse
import ast
import json
import os
import sys
from pathlib import Path

CHEMLAB_ROOT = Path(__file__).resolve().parents[1]
HOST_RECIPES_JSON = CHEMLAB_ROOT / 'configs' / 'host_recipes.json'
PROJECT = Path(__file__).resolve().parents[3]                      # the folder that holds this project
MATTERIX_ROOT = Path(os.environ.get('MATTERIX_PATH') or PROJECT / 'Matterix')
BRIDGE_REL = 'source/matterix/matterix/managers/semantics/primitive_semantics/chemsim/runtime_bridge.py'

# advance modes (the same strings as runtime_bridge.py)
STEP_DT, HOST_STEP_DT, HOST_STEP_DT_WORLD, STEP_DT_WORLD = 'step_dt', 'host_step_dt', 'host_step_dt_world', 'step_dt_world'
HOST_STEP_ABS, HOST_ADVANCE_ABS, HOST_SET_CLOCK_ABS, VICI_SET_TIME, NO_CLOCK = ('host_step_abs', 'host_advance_abs', 'host_set_clock_abs',
                                                                               'vici_set_time', None)
# the runtime reads a clock callable (passed as clock=) and processes its own timed events in host_update(), called once after
# every clock step (Agilent 8697)
CLOCK_HOST_UPDATE = 'clock_host_update'
CLOCK_MODES = (NO_CLOCK, CLOCK_HOST_UPDATE)                 # modes whose runtime reads the lab clock through a callable
ADVANCE_MODES = (STEP_DT, HOST_STEP_DT, HOST_STEP_DT_WORLD, STEP_DT_WORLD, HOST_STEP_ABS, HOST_ADVANCE_ABS, HOST_SET_CLOCK_ABS,
                 VICI_SET_TIME, CLOCK_HOST_UPDATE, NO_CLOCK)


class RecipeError(Exception):
    pass


def _literal(node, names):
    """Evaluate a recipe expression: literals, dict(...) calls with keywords, names of module constants."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        if node.id in names:
            return names[node.id]
        if node.id in ('True', 'False', 'None'):
            return {'True': True, 'False': False, 'None': None}[node.id]
        raise RecipeError('unknown name %r at line %d' % (node.id, node.lineno))
    if isinstance(node, ast.Dict):
        return {_literal(k, names): _literal(v, names) for k, v in zip(node.keys, node.values)}
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_literal(e, names) for e in node.elts]
        return items if isinstance(node, ast.List) else tuple(items)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'dict' and not node.args:
        return {kw.arg: _literal(kw.value, names) for kw in node.keywords}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_literal(node.operand, names)
    raise RecipeError('unsupported expression %s at line %d' % (type(node).__name__, getattr(node, 'lineno', 0)))


def bridge_path():
    """The Matterix bridge file, or None when it is not there."""
    p = MATTERIX_ROOT / BRIDGE_REL
    return p if p.is_file() else None


def parse_bridge(path):
    """{'RECIPES': {...}, 'PANEL_RECIPES': {...}} parsed from a runtime_bridge.py source (nothing is imported or run)."""
    path = Path(path)
    tree = ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
    names, tables = {}, {}
    for stmt in tree.body:
        targets = []
        if isinstance(stmt, ast.Assign):
            targets, value = stmt.targets, stmt.value
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            targets, value = [stmt.target], stmt.value
        else:
            continue
        for tgt in targets:
            if isinstance(tgt, ast.Tuple) and isinstance(value, ast.Tuple) and len(tgt.elts) == len(value.elts):
                for t, v in zip(tgt.elts, value.elts):
                    if isinstance(t, ast.Name):
                        try:
                            names[t.id] = _literal(v, names)
                        except RecipeError:
                            pass
            elif isinstance(tgt, ast.Name):
                if tgt.id in ('RECIPES', 'PANEL_RECIPES'):
                    tables[tgt.id] = _literal(value, names)
                else:
                    try:
                        names[tgt.id] = _literal(value, names)
                    except RecipeError:
                        pass
    if 'RECIPES' not in tables:
        raise RecipeError('RECIPES not found in %s' % path)
    return {'RECIPES': tables['RECIPES'], 'PANEL_RECIPES': tables.get('PANEL_RECIPES', {})}


def _plain(tables):
    """The tables as JSON values (tuples become lists), keys sorted: the form stored in host_recipes.json."""
    return json.loads(json.dumps(tables, sort_keys=True))


def read_json(path=None):
    path = Path(path or HOST_RECIPES_JSON)
    data = json.loads(path.read_text(encoding='utf-8'))
    return {'RECIPES': data['RECIPES'], 'PANEL_RECIPES': data.get('PANEL_RECIPES', {})}


def load_recipes(path=None):
    """-> {'recipes': {asset_type_id: recipe}, 'panel_recipes': {...}, 'source': path}.  Every recipe gets 'table'.

    ``path``: a runtime_bridge.py or a host_recipes.json; default: the Matterix bridge when it is there, else the JSON."""
    if path is None:
        path = bridge_path() or HOST_RECIPES_JSON
        if not Path(path).is_file():
            raise RecipeError('no host recipes: %s is missing and no Matterix bridge was found (set MATTERIX_PATH)' % HOST_RECIPES_JSON)
    path = Path(path)
    tables = read_json(path) if path.suffix == '.json' else parse_bridge(path)
    out = {'recipes': {}, 'panel_recipes': {}, 'source': str(path)}
    for key, table in (('recipes', 'RECIPES'), ('panel_recipes', 'PANEL_RECIPES')):
        for atid, r in tables.get(table, {}).items():
            r = dict(r)
            if r.get('advance', NO_CLOCK) not in ADVANCE_MODES:
                raise RecipeError('%s: unknown advance mode %r' % (atid, r.get('advance')))
            r['table'] = table
            out[key][atid] = r
    return out


def all_recipes(path=None):
    """{asset_type_id: recipe} over RECIPES and PANEL_RECIPES (RECIPES wins, as in the bridge's own lookup)."""
    data = load_recipes(path)
    merged = dict(data['panel_recipes'])
    merged.update(data['recipes'])
    return merged


def export(out=None, bridge=None):
    """Write host_recipes.json from the Matterix bridge.  Returns the path written."""
    bridge = Path(bridge) if bridge else bridge_path()
    if bridge is None:
        raise RecipeError('the Matterix bridge is needed to export the recipes (set MATTERIX_PATH)')
    tables = _plain(parse_bridge(bridge))
    doc = {'about': 'Host recipes of the Chemistry-Sim runtimes (how each runtime host is built and how its clock is advanced), '
                    'exported from the Matterix simulator bridge (runtime_bridge.py: RECIPES and PANEL_RECIPES) by '
                    '"python -m chemlab.recipes --export". chemlab reads this file when the bridge is not present.',
           'n_recipes': len(tables['RECIPES']), 'n_panel_recipes': len(tables['PANEL_RECIPES']), **tables}
    out = Path(out or HOST_RECIPES_JSON)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False, indent=1, sort_keys=False) + '\n', encoding='utf-8')
    return out


def diff(bridge=None, json_path=None):
    """Differences between the bridge and the JSON file: [] when they agree."""
    bridge = Path(bridge) if bridge else bridge_path()
    if bridge is None:
        raise RecipeError('no Matterix bridge to compare with (set MATTERIX_PATH)')
    a, b = _plain(parse_bridge(bridge)), _plain(read_json(json_path))
    out = []
    for table in ('RECIPES', 'PANEL_RECIPES'):
        for atid in sorted(set(a[table]) | set(b[table])):
            if a[table].get(atid) != b[table].get(atid):
                out.append('%s %s: %s' % (table, atid, 'only in the bridge' if atid not in b[table] else
                                          'only in the JSON' if atid not in a[table] else 'differs'))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description='Host recipes: show the source, export the JSON from the Matterix bridge, or check it.')
    g = ap.add_mutually_exclusive_group()
    g.add_argument('--export', action='store_true', help='write configs/host_recipes.json from the Matterix bridge')
    g.add_argument('--check', action='store_true', help='compare configs/host_recipes.json with the Matterix bridge')
    ap.add_argument('--out', help='output file for --export (default: configs/host_recipes.json)')
    a = ap.parse_args(argv)
    if a.export:
        p = export(a.out)
        print('wrote %s from %s' % (p, bridge_path()))
        return 0
    if a.check:
        d = diff()
        print('\n'.join(d) if d else 'host_recipes.json equals the Matterix bridge')
        return 1 if d else 0
    data = load_recipes()
    print('%s: %d recipes, %d panel recipes' % (data['source'], len(data['recipes']), len(data['panel_recipes'])))
    return 0


if __name__ == '__main__':
    sys.exit(main())

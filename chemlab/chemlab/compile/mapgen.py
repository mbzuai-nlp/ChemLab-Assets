"""Draft per-instrument mappings from the contracts (the "generate what you can" step of a new class).

    python -m chemlab.compile.mapgen thermal                   # draft for the class's members (from its fragment)
    python -m chemlab.compile.mapgen shared --all              # draft shared-layer mappings for all 203
    python -m chemlab.compile.mapgen balance --compare         # how much of the hand-checked fragment the rules find

A rule set per class says, for each verb, which agent-callable capabilities qualify (id pattern, argument by unit or name) and,
for each slot role, which state slots qualify.  Ranges, enums and units are never copied into a mapping: the adapter reads them
from the contract at run time.  The draft goes to build/mapgen/<class>.json; a person reviews it, writes
reasons for the verbs a member lacks, and puts the result into configs/adapters/<class>.json.  One native path per verb group:
a stop / result candidate in the same namespace as the chosen start wins.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from ..hosts import contract_index
from .fragments import load_fragments, write_json_atomic

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build' / 'mapgen'
TEMP_UNITS = ('K', 'degC', '°C')

# rule kinds: {'cap': regex, 'arg': {'unit': [...], 'name': regex, 'type': json type} -> "arg", 'fixed': {...}, 'toggle': True,
#              'same_path_as': verb}
RULES = {
    'shared': {
        'verbs': {
            'power': [{'cap': r'^power\.set$', 'arg': {'type': 'boolean', 'name': r'^(enabled|on)$'}},
                      {'cap': r'^power\.(toggle|press_switch|press_button|key_toggle)$', 'toggle': True}],
            'method': [{'cap': r'^method\.(configure|set)$'}, {'cap': r'^(acquisition|chromatography)\.(configure|set_method)$'}],
            'start': [{'cap': r'^acquisition\.start$'}, {'cap': r'^(measurement|run|test|scan|sequence|cycle|analysis|program)\.start$'}],
            'stop': [{'cap': r'^(acquisition|measurement|run|test|scan|sequence|cycle|analysis|program)\.(stop|abort|cancel)$', 'same_path_as': 'start'}],
            'result': [{'cap': r'^(acquisition|measurement|run|analysis)\.(result|read|measure)$', 'same_path_as': 'start'},
                       {'cap': r'\.(result|measure)$', 'same_path_as': 'start'}],
            'ack': [{'cap': r'^(alarm|alarms|message|messages|fault|faults)\.(acknowledge|ack|clear|mute)$'}],
            'estop': [{'cap': r'^(emergency|estop)\.(stop|press)$'}],
        },
        'slots': {'power': [r'^power\.(enabled|on)$']},
    },
    'balance': {
        'verbs': {
            'power': [{'cap': r'^power\.set$', 'arg': {'type': 'boolean', 'name': r'^(enabled|on)$'}}],
            'zero': [{'cap': r'^(balance|weighing)\.zero$'}],
            'tare': [{'cap': r'^(balance|weighing)\.tare$'}],
            'weigh': [{'cap': r'^balance\.reading$'}],
            'unit': [{'cap': r'^display\.set_unit$', 'arg': {'name': r'^unit$'}}, {'cap': r'^(weighing|balance)\.set_(display_)?unit$', 'arg': {'name': r'^unit$'}}],
            'door': [{'cap': r'^door\.operate$'}, {'cap': r'^doors\.set$', 'arg': {'name': r'open_fraction'}}],
        },
        'slots': {'power': [r'^power\.(enabled|on)$'], 'stable': [r'^(balance|weighing)\.stable$'], 'net': [r'^(balance|weighing)\.net_kg$'],
                  'gross': [r'^(balance|weighing)\.gross_kg$'], 'tare': [r'^(balance|weighing)\.tare_kg$']},
    },
    'thermal': {
        'verbs': {
            'power': [{'cap': r'^power\.set$', 'arg': {'type': 'boolean', 'name': r'^(enabled|on)$'}}],
            'set-temp': [{'cap': r'^(thermal\.set_temperature|heating\.set_temperature|temperature\.set|setpoint\.set|controller\.set_sv)$',
                          'arg': {'unit': TEMP_UNITS, 'name': r'temp|set_?point|target'}},
                         {'cap': r'^settings\.configure$', 'arg': {'unit': TEMP_UNITS, 'name': r'^set_?point'}}],
            'heat': [{'cap': r'^(heating\.set|thermal\.set_enabled|thermal\.set_control|circulation\.set_running)$', 'arg': {'type': 'boolean'}}],
            'safety-limit': [{'cap': r'^safety\.(set_limit|set_cutoff|configure)$', 'arg': {'unit': TEMP_UNITS, 'name': r'limit|cutoff|temperature|value'}}],
            'stir': [{'cap': r'^(stirring|shaking)\.set_speed$', 'arg': {'unit': ('rad/s', 'rpm')}}],
            'remote': [{'cap': r'^interface\.set_remote$', 'arg': {'type': 'boolean'}}],
        },
        'slots': {'power': [r'^power\.(enabled|on)$'],
                  'temp': [r'indicated_temperature_k$', r'^temperature\.(chamber|interior)_k$', r'^sensor\.temperature_k$', r'^display\.temperature(_c)?$'],
                  'set': [r'^thermal\.target_k$', r'^heating\.set_k$', r'^temperature\.set_k$', r'^setpoint\.temperature_k$'],
                  'limit': [r'^safety\.(limit|cutoff)_k$', r'temperature_limit_k$'],
                  'heat': [r'^heating\.on$', r'^heater\.enabled$', r'^thermal\.control_enabled$', r'^circulation\.running$']},
    },
}


def _types(s):
    t = s.get('type')
    return set(t if isinstance(t, list) else [t] if t else [])


def pick_arg(cap_spec, want):
    props = ((cap_spec.get('input_schema') or {}).get('properties') or {})
    for name, s in props.items():
        if not isinstance(s, dict):
            continue
        if want.get('unit') and s.get('x-unit') not in want['unit']:
            continue
        if want.get('type') and want['type'] not in _types(s):
            continue
        if want.get('name') and not re.search(want['name'], name):
            continue
        return name
    return None


def draft(contract, rules):
    caps = {cid: c for cid, c in contract['capabilities'].items() if c.get('visibility') == 'agent' and c.get('availability') == 'implemented'}
    slots = {sid for sid, s in contract['state_slots'].items() if s.get('visibility') == 'agent'}
    verbs, missing = {}, []
    for verb, cands in rules['verbs'].items():
        found = None
        for rule in cands:
            ns = None
            if rule.get('same_path_as') and verbs.get(rule['same_path_as']):
                ns = verbs[rule['same_path_as']]['cap'].split('.', 1)[0]
            matches = sorted(c for c in caps if re.search(rule['cap'], c))
            if ns:
                matches = sorted(matches, key=lambda c: c.split('.', 1)[0] != ns)
            for cid in matches:
                m = {'cap': cid}
                if rule.get('toggle'):
                    m['toggle'] = True
                if rule.get('arg'):
                    a = pick_arg(caps[cid], rule['arg'])
                    if a is None:
                        continue
                    m['arg'] = a
                found = m
                break
            if found:
                break
        if found:
            verbs[verb] = found
        else:
            missing.append(verb)
    roles = {}
    for role, pats in rules.get('slots', {}).items():
        for pat in pats:
            hit = sorted(s for s in slots if re.search(pat, s))
            if hit:
                roles[role] = hit[0]
                break
    return {'verbs': verbs, 'slots': roles, 'missing': missing}


def compare(drafted, hand):
    """(verbs where the draft equals the hand-checked mapping, verbs the hand mapping has)."""
    same, total = 0, 0
    for verb, m in (hand.get('verbs') or {}).items():
        total += 1
        d = drafted['verbs'].get(verb)
        if d and d.get('cap') == m.get('cap') and d.get('arg') == m.get('arg'):
            same += 1
    return same, total


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('cls', choices=sorted(RULES))
    ap.add_argument('--all', action='store_true', help='every instrument with a contract, not just the class members')
    ap.add_argument('--slugs', nargs='*')
    ap.add_argument('--compare', action='store_true')
    a = ap.parse_args(argv)
    idx = contract_index()
    frag = next((f for f in load_fragments() if f['class'] == a.cls), {'members': {}})
    slugs = a.slugs or (sorted(k.split('.', 1)[1] for k in idx) if a.all else sorted(frag['members']))
    out = {'class': a.cls, 'members': {}}
    same = total = 0
    for slug in slugs:
        contract = json.loads(idx['instrument.' + slug].read_text(encoding='utf-8'))
        d = draft(contract, RULES[a.cls])
        out['members'][slug] = d
        if a.compare and slug in frag['members']:
            s, t = compare(d, frag['members'][slug])
            same, total = same + s, total + t
            print('%-34s draft verbs %-2d missing %-40s matches the fragment %d/%d' % (slug, len(d['verbs']), ','.join(d['missing'])[:40], s, t))
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / ('%s.json' % a.cls)
    write_json_atomic(p, out)
    print('%s: %d instruments drafted -> %s%s' % (a.cls, len(slugs), p, ('; %d/%d hand-mapped verbs found by the rules' % (same, total)) if a.compare else ''))
    return 0


if __name__ == '__main__':
    sys.exit(main())

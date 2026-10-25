"""Lowering table of the shared layer, built for every member of configs/adapters/shared.json.

Expected native calls come from the contracts, not from the shared mapping's values: method = the method capability with the
contract's example arguments (typed as KEY=VALUE, bare numbers in the declared unit); open / close = the part capability with the
open / close value the argument's schema gives (range maximum / minimum, true / false, open / closed enum); start / stop / result /
ack / estop = the capability with no arguments.  power is state dependent (no call when already in that state): its calls are
not fixed here; test_shared_all203 checks the power mapping against the contract's write_via of the power slot instead.
Verbs a class fragment owns on that instrument are left to the class's own lowering table.
"""
import json
import shlex

from ...glab import single
from ...hosts import contract_index
from ..fragments import adapters_for, load_fragments
from ..shared_gen import part_value

ORDER = ('method', 'start', 'result', 'stop')


def _contract(slug):
    return json.loads(contract_index()['instrument.' + slug].read_text(encoding='utf-8'))


def _text(v):
    return v if isinstance(v, str) else json.dumps(v)


def steps_for(slug, m, contract, owned):
    caps = contract['capabilities']
    verbs = m.get('verbs') or {}
    out = [('status', [])]
    if 'method' in verbs and 'method' not in owned:
        cap = verbs['method']['cap']
        ex = caps[cap].get('example_arguments') or {}
        if ex:
            out.append(('method ' + ' '.join(shlex.quote('%s=%s' % (k, _text(v))) for k, v in ex.items()), [(cap, ex)]))
    for v in ('start', 'result', 'stop'):
        if v in verbs and v not in owned:
            out.append((v, [(verbs[v]['cap'], {})]))
    if 'open' in verbs and 'open' not in owned:
        for name, spec in verbs['open']['parts'].items():
            for action in ('open', 'close'):
                if spec.get('%s_cap' % action):
                    out.append(('%s %s' % (action, name), [(spec['%s_cap' % action], dict(spec.get('args') or {}))]))
                    continue
                schema = ((caps[spec['cap']].get('input_schema') or {}).get('properties') or {})[spec['arg']]
                ov, cv = part_value(spec['arg'], schema)
                args = dict(spec.get('args') or {})
                args[spec['arg']] = ov if action == 'open' else cv
                out.append(('%s %s' % (action, name), [(spec['cap'], args)]))
    for v in ('ack', 'estop'):
        if v in verbs and v not in owned:
            out.append((v, [(verbs[v]['cap'], {})]))
    if 'power' in verbs and 'power' not in owned:
        out += [('power off', None), ('power on', None)]
    return out


def build():
    frags = load_fragments()
    shared = next(f for f in frags if f.get('layer') == 'shared')
    classes = [f for f in frags if f.get('layer') != 'shared']
    cases = {}
    for slug, m in sorted(shared['members'].items()):
        owned = set()
        for f in classes:
            if slug in f['members']:
                cad = adapters_for(single([slug]), [f])[slug]
                owned |= set(cad.verbs()) | {v for v in cad.table if cad.blocks(v)} | set(cad.hidden_verbs())   # class beats shared
        cases[slug] = {'steps': steps_for(slug, m, _contract(slug), owned)}
    return cases


CASES = build()

"""Generate the shared layer: configs/adapters/shared.json for all 203 instruments.

    python -m chemlab.compile.shared_gen            # draft into build/mapgen/shared_fragment.json and print counts
    python -m chemlab.compile.shared_gen --write    # write configs/adapters/shared.json (after validating every member)

Every member mapping is generated from its contract's agent-visible, implemented capabilities, then checked with
SharedAdapter.check_mapping against the contract and the agent view; a verb that does not fit is dropped with a reason.  Rules:

* power: power.set-like capability with one boolean argument (enabled / on / power); other required arguments take the
  contract's example values (e.g. the DMT152 supply voltage); toggles only when there is a power state slot.
* open / close PART: a door, lid, cover, sash, drawer, hood, shield, guard, hatch, cap, latch, furnace or flange capability that
  opens with one setting: an open/close pair, a boolean (open = true, unless the argument says closed/locked), a number with a
  range (open = maximum, close = minimum) or an enum with open/closed values; and any other capability whose one argument is a
  boolean open / opened / closed (valves, shutters, chambers, holders).  scene.* capabilities are left to the classes.
* ack: an alarm / message / fault / error / warning acknowledge (no arguments); reset / clear only for alarms, faults, errors.
* estop: emergency.stop / trigger / engage, safety.emergency_stop / emo / trip, generator.emergency_stop (no arguments).
* method: method.configure / method.set / acquisition.configure / measurement.configure / chromatography.set_method.
* start: acquisition.start first, then measurement / run / test / scan / sequence / cycle / analysis / program / experiment /
  process .start, with no required arguments; an asynchronous one returns an operation job.
* stop: the start's namespace .stop / .abort / .cancel.  result: the start's namespace .result / .read / .measure, else a
  query in a namespace that has no start of its own (not a second path; listed in path_exceptions).
* status: the power slot and the state / phase / status slots the start changes.

The thin-class members (configs/adapters/thin.json) are left out: their reviewed thin fragment is their shared layer.
"""
import argparse
import json
import re
import sys
from pathlib import Path

from ..labbackend import LabBackend, load_config
from .fragments import FRAGMENT_DIR, load_fragments, write_json_atomic
from .shared import SharedAdapter

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'build' / 'mapgen'
PART_WORDS = ('door', 'doors', 'lid', 'cover', 'sash', 'drawer', 'hood', 'shield', 'guard', 'hatch', 'cap', 'latch', 'furnace', 'flange',
              'enclosure')
PART_ACTIONS = ('set', 'set_angle', 'set_lift', 'set_open', 'set_position', 'move', 'set_extension', 'set_raised', 'set_door', 'set_opening')
NOT_PARTS = ('scene.', 'monitor.')       # boolean "closed" there is a mode (fume-hood night setback), not a part
ACK_NS = ('alarm', 'alarms', 'message', 'messages', 'fault', 'faults', 'error', 'errors', 'warning', 'warnings', 'notification',
          'notifications', 'alert', 'alerts', 'generator', 'pressure_alarm', 'system')
ACK_ACTIONS = ('acknowledge', 'ack', 'confirm', 'dismiss', 'mute', 'silence', 'clear', 'reset')
ESTOP = ('emergency.stop', 'emergency.trigger', 'emergency.engage', 'safety.emergency_stop', 'generator.emergency_stop', 'safety.emo',
         'safety.trip')
METHOD = ('method.configure', 'method.set', 'acquisition.configure', 'measurement.configure', 'chromatography.set_method')
START_NS = ('acquisition', 'measurement', 'run', 'test', 'scan', 'sequence', 'cycle', 'analysis', 'program', 'experiment', 'process')
REASONS = {
    'power': 'its contract gives agents no power switch capability',
    'open': 'no door, lid or cover that opens with a single setting (see `chemlab describe INST` for its own capabilities)',
    'close': 'no door, lid or cover that closes with a single setting (see `chemlab describe INST`)',
    'ack': 'no alarm or message acknowledge capability',
    'estop': 'no emergency stop capability',
    'method': 'no method capability (its settings are separate capabilities: `chemlab describe INST`)',
    'start': 'no run or measurement start that needs no arguments (see `chemlab describe INST`)',
    'stop': 'no stop for its start capability',
    'result': 'no result query on the same path as its start',
}


def _props(c):
    return (c.get('input_schema') or {}).get('properties') or {}


def _required(c):
    return list((c.get('input_schema') or {}).get('required') or [])


def _types(s):
    t = s.get('type')
    return set(t if isinstance(t, list) else [t] if t else [])


def power(caps, slots, contract):
    cands = sorted(c for c in caps if re.fullmatch(r'(power|platform|controller|main)\.set(_power)?', c) or c.endswith('.set_power'))
    cands.sort(key=lambda c: c != 'power.set')
    for cid in cands:
        props = _props(caps[cid])
        b = [n for n, s in props.items() if 'boolean' in _types(s) and n in ('enabled', 'on', 'power', 'powered')]
        if not b:
            continue
        m = {'cap': cid, 'arg': b[0]}
        ex = caps[cid].get('example_arguments') or {}
        fixed = {n: ex[n] for n in _required(caps[cid]) if n != b[0]}
        if any(n not in ex for n in _required(caps[cid]) if n != b[0]):
            continue
        if fixed:
            m['args'] = fixed
        return m, power_slot(cid, b[0], slots, contract)
    for cid in sorted(caps):
        if re.fullmatch(r'power\.(toggle|press_switch|press_button|key_toggle)', cid) and not _required(caps[cid]):
            s = power_slot(cid, None, slots, contract)
            if s:
                return {'cap': cid, 'toggle': True}, s
    return None, None


def power_slot(cid, arg, slots, contract):
    for sid in sorted(slots):
        wv = contract['state_slots'][sid].get('write_via') or {}
        if isinstance(wv, dict) and wv.get('capability_id') == cid and (arg is None or wv.get('argument') == arg):
            return sid
    for sid in sorted(slots):
        if re.fullmatch(r'power\.(enabled|on)', sid) and 'boolean' in _types(contract['state_slots'][sid].get('value_schema') or {}):
            return sid
    return None


def _is_part(prefix, action):
    words = re.split(r'[._]', prefix)
    return any(w in PART_WORDS for w in words) or (action in ('set_door',) and prefix)


def part_value(arg, s):
    """(open value, close value) for one argument, or None."""
    t = _types(s)
    inv = bool(re.search(r'closed|lock', arg))
    if 'boolean' in t:
        return (False, True) if inv else (True, False)
    if t & {'number', 'integer'} and 'minimum' in s and 'maximum' in s:
        lo, hi = s['minimum'], s['maximum']
        if lo == hi:
            return None
        return (lo, hi) if inv else (hi, lo)
    enum = [str(e) for e in s.get('enum') or []]
    o = next((e for e in enum if e in ('open', 'opened', 'up', 'raised')), None)
    c = next((e for e in enum if e in ('closed', 'close', 'down', 'lowered')), None)
    if o and c:
        return (o, c)
    return None


def parts(caps):
    found = {}
    rank = {}
    for cid in sorted(caps):
        if cid.startswith('scene.') or '.' not in cid:
            continue
        prefix, _, action = cid.rpartition('.')
        if action in ('open', 'close'):
            other = '%s.%s' % (prefix, 'close' if action == 'open' else 'open')
            if other in caps and not _required(caps[cid]) and not _required(caps[other]) and _is_part(prefix, action):
                name = prefix.replace('.', '_')
                if rank.get(name, 9) > 0:
                    found[name], rank[name] = {'open_cap': '%s.open' % prefix, 'close_cap': '%s.close' % prefix}, 0
            continue
        if action not in PART_ACTIONS or not _is_part(prefix, action):
            continue
        props, req = _props(caps[cid]), _required(caps[cid])
        if len(props) != 1 or len(req) > 1:
            continue
        arg, s = next(iter(props.items()))
        v = part_value(arg, s)
        if v is None:
            continue
        name = prefix.replace('.', '_') + ('_door' if action == 'set_door' and 'door' not in prefix else '')
        r = 1 if action != 'set' and action != 'move' else 2
        if rank.get(name, 9) > r:
            found[name], rank[name] = {'cap': cid, 'arg': arg, 'open': v[0], 'close': v[1]}, r
    # any other capability whose one argument is a boolean open / opened / closed: a valve, shutter, chamber, holder ... that
    # opens and closes (quorum chamber.set(opened), TA gas.set_open(opened), CellXpert door.inner(open), ...)
    used = {x.get('cap') for x in found.values()}
    for cid in sorted(caps):
        if cid in used or cid.startswith(NOT_PARTS) or '.' not in cid:
            continue
        props = _props(caps[cid])
        if len(props) != 1:
            continue
        arg, s = next(iter(props.items()))
        if 'boolean' not in _types(s) or not re.fullmatch(r'(open|opened|closed)', arg):
            continue
        prefix, _, action = cid.rpartition('.')
        tail = action[4:] if action.startswith('set_') else ('' if action in ('set', 'open', 'close') else action.replace('_set', ''))
        name = prefix.replace('.', '_') + ('_' + tail if tail and tail not in ('open', 'opened') and tail not in prefix else '')
        if name in found:
            continue
        v = part_value(arg, s)
        found[name], rank[name] = {'cap': cid, 'arg': arg, 'open': v[0], 'close': v[1]}, 1
    return found


def ack(caps):
    best = None
    for cid in sorted(caps):
        if '.' not in cid or _required(caps[cid]):
            continue
        ns, _, action = cid.partition('.')
        if ns not in ACK_NS or action not in ACK_ACTIONS:
            continue
        if action in ('reset', 'clear') and ns not in ('alarm', 'alarms', 'fault', 'faults', 'error', 'errors'):
            continue
        if ns == 'system' and action != 'acknowledge':
            continue
        r = ACK_ACTIONS.index(action)
        if best is None or r < best[0]:
            best = (r, cid)
    return {'cap': best[1]} if best else None


def run_set(caps, contract):
    out, notes = {}, {}
    starts = [c for c in caps if c.endswith('.start') and c.split('.', 1)[0] in START_NS and not _required(caps[c])]
    starts.sort(key=lambda c: START_NS.index(c.split('.', 1)[0]))
    m = next((c for c in METHOD if c in caps and _props(caps[c])), None)
    if m:
        out['method'] = {'cap': m}
    if not starts:
        return out, None
    start = starts[0]
    ns = start.split('.', 1)[0]
    mode = ((caps[start].get('execution') or {}).get('mode') or 'synchronous')
    out['start'] = {'cap': start, 'job': 'operation' if mode != 'synchronous' else 'none'}
    stop = next(('%s.%s' % (ns, a) for a in ('stop', 'abort', 'cancel') if '%s.%s' % (ns, a) in caps and not _required(caps['%s.%s' % (ns, a)])), None)
    if stop:
        out['stop'] = {'cap': stop}
    res = next(('%s.%s' % (ns, a) for a in ('result', 'read', 'measure', 'get_result', 'read_result', 'report')
                if '%s.%s' % (ns, a) in caps and not _required(caps['%s.%s' % (ns, a)])), None)
    exc = []
    if not res:
        start_ns = {c.split('.', 1)[0] for c in caps if c.endswith('.start')}
        for cid in sorted(caps):
            pre, _, action = cid.rpartition('.')
            if action in ('measure', 'result') and pre.split('.', 1)[0] not in start_ns and not _required(caps[cid]) and \
                    caps[cid].get('kind') == 'query':
                res = cid
                exc.append(cid)
                break
    if res:
        out['result'] = {'cap': res}
    return out, (exc or None)


def status_slots(pslot, start, caps, contract, slots):
    out = [pslot] if pslot else []
    eff = [e.get('state_id') for e in (caps.get(start) or {}).get('state_effects') or []] if start else []
    for sid in eff + sorted(slots):
        if sid in out or sid not in slots or len(out) >= 4:
            continue
        s = contract['state_slots'][sid].get('value_schema') or {}
        if re.search(r'(^|\.)(phase|state|status|mode)$', sid) and ('string' in _types(s) or s.get('enum')):
            out.append(sid)
    return out


def member(contract, view):
    caps = {c: contract['capabilities'][c] for c in view.capabilities}
    slots = set(view.slots)
    verbs, m = {}, {}
    p, pslot = power(caps, slots, contract)
    if p:
        verbs['power'] = p
    ps = parts(caps)
    if ps:
        verbs['open'] = {'parts': ps}
    a = ack(caps)
    if a:
        verbs['ack'] = a
    e = next((c for c in ESTOP if c in caps and not _required(caps[c])), None)
    if e:
        verbs['estop'] = {'cap': e}
    rs, exc = run_set(caps, contract)
    verbs.update(rs)
    m['verbs'] = verbs
    if pslot:
        m['slots'] = {'power': pslot}
    if exc:
        m['path_exceptions'] = exc
    m['status_slots'] = status_slots(pslot, (rs.get('start') or {}).get('cap'), caps, contract, slots)
    return m


def finish(slug, m, lab_inst):
    """Validate against the contract; drop verbs that do not fit; reasons for the missing ones."""
    entry = {'id': slug, 'asset_type_id': 'instrument.' + slug, 'class_map': m}
    for _ in range(10):
        problems = SharedAdapter(slug, entry).check_mapping(lab_inst)
        if not problems:
            break
        for pr in problems:
            for v in list(m['verbs']):
                if pr.startswith('verbs.%s' % v) or ('path: %s ' % v) in pr:
                    m['verbs'].pop(v, None)
                    m.setdefault('dropped', {})[v] = pr
    un = {}
    for v in ('power', 'open', 'close', 'ack', 'estop', 'method', 'start', 'stop', 'result'):
        key = 'open' if v == 'close' else v
        if key not in m['verbs']:
            un[v] = REASONS[v]
    if un:
        m['unsupported'] = un
    return m, SharedAdapter(slug, entry).check_mapping(lab_inst)


def build(log=print):
    cfg = load_config()
    cfg['class_adapters'] = False
    thin = next((f for f in load_fragments() if f['class'] == 'thin'), {'members': {}})
    lab = LabBackend(cfg, run_dir=ROOT / 'build' / 'shared_gen', setup=False)
    members, bad = {}, {}
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in thin['members']:
            continue
        inst = lab.instruments[e['id']]
        m, problems = finish(slug, member(inst.contract, inst.view), inst)
        if problems:
            bad[slug] = problems
        members[slug] = m
    return members, bad, sorted(thin['members'])


def fragment(members, thin):
    return {'class': 'shared', 'layer': 'shared', 'adapter': 'chemlab.compile.shared:SharedAdapter',
            'about': 'The shared layer, generated from the contracts by chemlab/compile/shared_gen.py: power, status, '
                     'open/close of named parts, ack, estop and method / start / stop / result / wait wherever the contract supports '
                     'them.  Class fragments override it verb by verb (fragments.py precedence).  Regenerate, do not hand-edit.',
            'generated_by': 'python -m chemlab.compile.shared_gen --write',
            'membership': {'members': len(members), 'thin_class_members_left_out': thin},
            'members': members}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--write', action='store_true')
    a = ap.parse_args(argv)
    members, bad, thin = build()
    frag = fragment(members, thin)
    from collections import Counter
    c = Counter(v for m in members.values() for v in m['verbs'])
    nparts = sum(len(m['verbs'].get('open', {}).get('parts', {})) for m in members.values())
    print('%d members; verbs: %s; parts: %d; dropped: %d; still bad: %d' % (
        len(members), dict(c), nparts, sum(len(m.get('dropped', {})) for m in members.values()), len(bad)))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'shared_fragment.json').write_text(json.dumps(frag, ensure_ascii=False, indent=1) + '\n')
    if bad:
        print('NOT written: problems remain: %s' % json.dumps(bad)[:2000])
        return 1
    if a.write:
        write_json_atomic(FRAGMENT_DIR / 'shared.json', frag)
        print('wrote %s' % (FRAGMENT_DIR / 'shared.json'))
    return 0


if __name__ == '__main__':
    sys.exit(main())

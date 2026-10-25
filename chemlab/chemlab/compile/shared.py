"""Shared verbs: the vocabulary most of the 203 contracts already have in common.

* device base: ``power on|off``, ``status`` (every ClassAdapter has it), ``open PART`` / ``close PART`` (doors, lids, covers,
  sashes, hoods, ...), ``ack`` (acknowledge an alarm / message), ``estop`` (emergency stop);
* run set: ``method KEY=VALUE ...``, ``start`` (a job when the action is long), ``stop``, ``result [--save-to FILE]``,
  ``wait [--max T]`` (the caller's latest job on this instrument).

Classes take the factories they need (``self.table.update(shared.base_verbs(self))``); the generated shared / thin layer is ``SharedAdapter`` with a fragment marked ``"layer": "shared"``.  A class fragment overrides it verb by verb
(fragments.py precedence).  Member mapping keys (data in the fragment):

    "power":  {"cap": "power.set", "arg": "enabled"}                 or {"cap": "power.toggle", "toggle": true}
    "open":   {"parts": {"door": {"cap": "door.set", "arg": "angle_rad", "open": 1.57, "close": 0.0},
                         "lid":  {"open_cap": "lid.open", "close_cap": "lid.close"}}}      (close uses the same table)
    "ack":    {"cap": "alarm.acknowledge"}           "estop": {"cap": "emergency.stop"}
    "method": {"cap": "method.configure", "args": {fixed}, "keys": {"temp": {"arg": "temperature_k", "kind": "temperature"}},
               "passthrough": true}               (canonical key dictionary of the class + native argument names)
    "start":  {"cap": "acquisition.start", "args": {}, "job": "operation" | "state" | "none",
               "state": {"slot": "cycle.phase", "done": ["succeeded"], "failed": ["failed", "cancelled"]}, "stop_cap": "..."}
    "stop":   {"cap": "acquisition.stop"}
    "result": {"cap": "acquisition.result"}          or {"reads": ["measurement.value", ...]}

One native path per verb group: start / stop / result of a member must use one path (PATH_GROUPS; base.check_paths).
"""
import copy
import json
import re

from ..contract import validate
from ..generic import coerce
from . import units
from .base import ClassAdapter, short
from .errors import invalid, refused
from .jobs import RUNNING, OperationJob, StateJob, latest
from .verbs import Arg, Opt, Verb, save_to_opt, target_opt

ON_OFF = ['on', 'off']


# ------------------------------------------------------------------ device base
def power_verb(ad):
    def h(ctx, p):
        m = ad.vmap('power')
        want = p.pos[0] == 'on'
        slot = ad.slot('power')
        cur = ctx.v(slot) if slot else None
        if slot and bool(cur) == want:
            return {'text': 'ok: %s is already %s\n' % (ad.instrument_id, p.pos[0]), 'json': {'power': want, 'changed': False}}
        if m.get('toggle'):
            if not slot:
                ctx.fail(refused('STATE_UNKNOWN', '%s only has a power toggle and no power state to check.' % ad.instrument_id,
                                 'use `chemlab call %s %s` if you are sure.' % (ad.instrument_id, m['cap'])))
            args = dict(m.get('args') or {})
        else:
            args = dict(m.get('args') or {})
            args[m['arg']] = want
        ctx.call(m['cap'], args, 'switch %s' % p.pos[0],
                 readback=(lambda v: None if bool(ctx.v(slot)) == want else 'power %s' % ctx.v(slot)) if slot else None)
        return {'text': 'ok: %s switched %s\n' % (ad.instrument_id, p.pos[0]), 'json': {'power': want, 'changed': True}}
    return Verb('power', 'switch the instrument on or off', h, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                example='chemlab run INST power on')


def _part(ad, ctx, name):
    parts = (ad.vmap('open') or {}).get('parts') or {}
    if name is None:
        if len(parts) == 1:
            return next(iter(parts.items()))
        ctx.fail(invalid('WHICH_PART', '%s has several parts: %s.' % (ad.instrument_id, ', '.join(parts)), 'name one, e.g. `open %s`.' % next(iter(parts))))
    if name not in parts:
        ctx.fail(invalid('UNKNOWN_PART', '%s has no part %r to open or close.' % (ad.instrument_id, name), 'parts: %s.' % ', '.join(parts)))
    return name, parts[name]


def open_close_verbs(ad):
    def make(action):
        def h(ctx, p):
            name, spec = _part(ad, ctx, p.pos[0] if p.pos else None)
            if spec.get('%s_cap' % action):
                cap, args = spec['%s_cap' % action], dict(spec.get('args') or {})
            else:
                cap, args = spec['cap'], dict(spec.get('args') or {})
                args[spec['arg']] = spec[action]
            ctx.call(cap, args, '%s the %s' % (action, name))
            return {'text': 'ok: %s %s %s\n' % (ad.instrument_id, name, 'opened' if action == 'open' else 'closed'),
                    'json': {'part': name, 'action': action}}
        return h
    pos = [Arg('PART', 'which part (door, lid, cover, sash, ...); optional when there is only one', required=False)]
    return {'open': Verb('open', 'open a door, lid, cover, sash or hood', make('open'), positionals=pos, example='chemlab run INST open door'),
            'close': Verb('close', 'close a door, lid, cover, sash or hood', make('close'), positionals=pos, example='chemlab run INST close door')}


def simple_verb(ad, name, summary, done_text):
    def h(ctx, p):
        m = ad.vmap(name)
        ctx.call(m['cap'], dict(m.get('args') or {}), done_text)
        return {'text': 'ok: %s %s\n' % (ad.instrument_id, done_text), 'json': {'done': done_text}}
    return Verb(name, summary, h, example='chemlab run INST %s' % name)


def base_verbs(ad):
    out = {'power': power_verb(ad)}
    out.update(open_close_verbs(ad))
    out['ack'] = simple_verb(ad, 'ack', 'acknowledge the current alarm or message', 'acknowledged')
    out['estop'] = simple_verb(ad, 'estop', 'emergency stop', 'emergency stop sent')
    return out


# ------------------------------------------------------------------ run set
OK_CALL = ('succeeded', 'accepted', 'running')
SOURCE_TEXT = {'instrument': 'kept from what the instrument reports', 'last': 'kept from the last method chemlab sent',
               'example': "from the contract's example method (give them to change)", 'fixed': 'fixed by the mapping'}


def reported_method(ad, ctx, cap, props):
    """{arg: (value, where)} for arguments of a method capability that the instrument itself reports (agent-visible): a slot
    '<namespace>.<arg>' of the capability's namespace (method.mode for method.configure), or a key of a visible object slot that
    holds several of the arguments (method.current, measurement.settings, ...).  Values are converted to the argument's
    declared unit and dropped when they do not fit its schema (e.g. null before the first method)."""
    ns = cap.split('.', 1)[0]
    slots = ad.lab.contract['state_slots']
    view = ctx.view or {}
    out = {}
    for sid, v in view.items():
        if sid.startswith('_') or v is None:
            continue
        head, _, last = sid.rpartition('.')
        if head == ns and last in props:
            out.setdefault(last, (_to_arg(v, (slots.get(sid) or {}).get('unit'), props[last]), sid))
    for sid, v in view.items():
        if isinstance(v, dict) and not sid.startswith('_'):
            common = [k for k in v if k in props]
            if len(common) >= 2:
                for k in common:
                    out.setdefault(k, (v[k], '%s#%s' % (sid, k)))
    return {k: (v, w) for k, (v, w) in out.items() if v is not None and not validate(props[k], v, k)}


def _to_arg(v, slot_unit, schema):
    """A slot value in the unit the argument declares (same kind, different spelling or scale), else unchanged."""
    au, su = units.declared(schema), units.norm_unit(slot_unit) if slot_unit else None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not au or not su or au == su:
        return v
    ka, ks = units.unit_kind(au), units.unit_kind(su)
    if ka and ka == ks:
        out = units.sig(units.to_declared(units.from_declared(v, su), au))
        return int(out) if schema.get('type') == 'integer' and float(out).is_integer() else out
    return v


def last_sent(core, instrument, cap):
    """The arguments of the last successful call of ``cap`` on an instrument sent through chemlab (any verb, `chemlab call`
    included), from the request log; None when there was none."""
    log = getattr(core, 'log', None)
    for e in reversed(getattr(log, 'entries', None) or []):
        for c in reversed(e.get('backend_calls') or []):
            if c.get('op') == 'invoke' and c.get('instrument') == instrument and c.get('capability') == cap and c.get('status') in OK_CALL:
                return copy.deepcopy(c.get('arguments') or {})
    return None


def full_method_args(ad, ctx, cap, given, fixed=None):
    """The complete argument set of a method capability: ``given`` (the agent's keys), then the mapping's fixed ``args``, then
    for every other required argument - and every argument chemlab sent last time - in this order: what the instrument reports
    (if visible), the last method chemlab sent, the contract's example.  Returns (args, {arg: source}); a required argument
    with no source is a usage error naming it."""
    spec = ctx.cap_spec(cap)
    schema = spec.get('input_schema') or {}
    props = schema.get('properties') or {}
    required = list(schema.get('required') or [])
    args, src = {}, {}
    for k, v in (fixed or {}).items():
        args[k], src[k] = v, 'fixed'
    for k, v in given.items():
        args[k], src[k] = v, 'given'
    last = last_sent(ctx.core, ad.instrument_id, cap) or {}
    wanted = [a for a in required if a not in args] + [a for a in last if a not in args and a not in required and a in props]
    if not wanted:
        return args, src
    reported = reported_method(ad, ctx, cap, props)
    example = spec.get('example_arguments') or {}
    missing = []
    for a in wanted:
        if a in reported:
            args[a], src[a] = copy.deepcopy(reported[a][0]), 'instrument:' + reported[a][1]
        elif a in last and not validate(props.get(a) or {}, last[a], a):
            args[a], src[a] = copy.deepcopy(last[a]), 'last'
        elif a in example and a in required:
            args[a], src[a] = copy.deepcopy(example[a]), 'example'
        elif a in required:
            missing.append(a)
    if missing:
        ctx.fail(invalid('MISSING_SETTING', '%s needs %s, and %s reports no current value for %s.' % (
            cap, ', '.join(missing), ad.instrument_id, 'it' if len(missing) == 1 else 'them'),
            'give %s (see `chemlab help %s method`).' % (' '.join('%s=...' % a for a in missing), ad.instrument_id)))
    return args, src


def _show(v, schema):
    """A native value for the reply: in friendly units when the argument declares one ('1570.8 rad/s' -> '15000 rpm')."""
    u = units.declared(schema or {})
    if u and units.unit_kind(u) and isinstance(v, (int, float)) and not isinstance(v, bool):
        return units.fmt_declared(v, u)
    return short(v, 40)


def method_sources_text(args, src, props=None):
    """Reply lines naming where each value not given by the agent came from."""
    groups = {}
    for k, s in src.items():
        if s == 'given':
            continue
        key = 'instrument' if s.startswith('instrument:') else s
        groups.setdefault(key, []).append('%s=%s' % (k, _show(args[k], (props or {}).get(k))) +
                                          (' (%s)' % s.split(':', 1)[1] if key == 'instrument' else ''))
    return ''.join('  %s: %s\n' % (SOURCE_TEXT[g], ', '.join(groups[g])) for g in ('instrument', 'last', 'example', 'fixed') if g in groups)


def method_verb(ad):
    def h(ctx, p):
        m = ad.vmap('method')
        cap = m['cap']
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        given = {}
        keys = m.get('keys') or {}
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'see `chemlab help %s method`.' % ad.instrument_id))
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write method KEY=VALUE ... (see `chemlab help %s method`).' % ad.instrument_id))
            if k in keys:
                spec = keys[k]
                native, kind = spec['arg'], spec.get('kind')
            elif m.get('passthrough', True) and k in props:
                native, kind = k, None
            else:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (ad.instrument_id, k),
                                 'settings: %s.' % ', '.join(sorted(set(keys) | (set(props) if m.get('passthrough', True) else set())))))
            schema = props.get(native) or {}
            u = units.declared(schema)
            bare = re.fullmatch(r'\s*[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?\s*', v) is not None
            numeric = re.match(r'\s*[+-]?(\d|\.\d)', v) is not None          # null / true / [..] / {..} / text: no unit parsing
            if u and units.unit_kind(u) and (kind is not None or (numeric and not bare)):   # native name + bare number: declared unit
                given[native] = units.parse_for(v, schema, kind, what=k)
                if schema.get('type') == 'integer' and float(given[native]).is_integer():
                    given[native] = int(given[native])
            else:
                given[native] = coerce(v, schema, k)
        given.update(ad.target_args(ctx, p))
        args, src = full_method_args(ad, ctx, cap, given, m.get('args'))
        bad = validate(ctx.cap_spec(cap).get('input_schema') or {'type': 'object', 'properties': {}}, args, cap)
        if bad:
            ctx.fail(invalid('BAD_SETTING', 'the method does not fit %s: %s.' % (ad.instrument_id, '; '.join(bad[:6])),
                             'change the value(s) (ranges: `chemlab describe %s`); values not given come from the sources the '
                             'reply names and can be overridden by giving them.' % ad.instrument_id))
        ctx.call(cap, args, 'set the method (%s)' % ', '.join('%s=%s' % (k, short(v, 30)) for k, v in given.items()))
        text = 'ok: method of %s set: %s\n' % (ad.instrument_id, ', '.join('%s=%s' % (k, short(v, 40)) for k, v in given.items()))
        text += method_sources_text(args, src, props)
        return {'text': text, 'json': {'arguments': args, 'given': sorted(given), 'sources': src}}
    return Verb('method', 'set method parameters: KEY=VALUE ... (class keys take units like 80C; a native argument name with a bare '
                'number takes the unit the contract declares for it; settings not given are kept from what the instrument reports, '
                'else from the last method chemlab sent, else from the contract example - the reply says which)', h,
                options=target_opts(ad), variadic=('KEY=VALUE', 'a setting'), example='chemlab run INST method temp=80C time=5min')


def target_opts(ad):
    """--zone / --channel / --cell / --head for the targets this member's mapping has."""
    return {k: target_opt(k, sorted((t.get('values') or {}).keys()) or None) for k, t in (ad.map.get('targets') or {}).items()}


def _start_job(ad, ctx, m, r, cap):
    mode = ((ctx.cap_spec(cap).get('execution') or {}).get('mode'))
    job = m.get('job') or ('operation' if r.get('operation_id') else 'none')
    if job == 'operation' and r.get('operation_id'):
        return OperationJob(ctx.core, ad, ctx.req, '%s %s' % (ad.instrument_id, cap), cap, r['operation_id'])
    if job == 'state':
        st = m['state']

        def state(c):
            v = c.v(st['slot'])
            v = v.get(st['key']) if st.get('key') and isinstance(v, dict) else v
            if v in (st.get('done') or []):
                return 'succeeded', '%s = %s' % (st['slot'], v), {'state': v}
            if v in (st.get('failed') or []):
                return 'failed', '%s = %s' % (st['slot'], v), {'state': v}
            return 'running', '%s = %s' % (st['slot'], v), {'state': v}
        stop = None
        if m.get('stop_cap') or ad.vmap('stop'):
            sm = {'cap': m['stop_cap']} if m.get('stop_cap') else ad.vmap('stop')

            def stop(c):
                c.call(sm['cap'], dict(sm.get('args') or {}), 'stop')
                return 'stopped with %s' % sm['cap']
        return StateJob(ctx.core, ad, ctx.req, '%s %s' % (ad.instrument_id, cap), state, stop=stop, max_s=float(m.get('max_s', 7200)))
    _ = mode
    return None


def run_verbs(ad):
    def h_start(ctx, p):
        m = ad.vmap('start')
        args = dict(m.get('args') or {})
        args.update(ad.target_args(ctx, p))
        r = ctx.call(m['cap'], args, 'start (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = _start_job(ad, ctx, m, r, m['cap'])
        if job is None:
            return {'text': 'ok: %s started (%s)\n' % (ad.instrument_id, r.get('status')), 'json': {'status': r.get('status'), 'result': r.get('result')}}
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}

    def h_stop(ctx, p):
        m = ad.vmap('stop')
        args = dict(m.get('args') or {})
        args.update(ad.target_args(ctx, p))
        ctx.call(m['cap'], args, 'stop (%s)' % m['cap'])
        return {'text': 'ok: %s stopped\n' % ad.instrument_id, 'json': {}}

    def h_result(ctx, p):
        m = ad.vmap('result')
        if m.get('cap'):
            args = dict(m.get('args') or {})
            args.update(ad.target_args(ctx, p))
            r = ctx.call(m['cap'], args, 'read the result (%s)' % m['cap'], query=True)
            res = r.get('result')
        else:
            res = {s: ctx.v(s) for s in m.get('reads') or []}
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        lines = ['%s result:' % ad.instrument_id]
        if isinstance(res, dict):
            for k, v in list(res.items())[:30]:
                lines.append('  %s = %s' % (k, short(v, 160)))
        else:
            lines.append('  %s' % short(res, 400))
        return {'text': '\n'.join(lines) + '\n', 'json': {'result': res}}

    def h_wait(ctx, p):
        job = latest(ctx.core, ctx.caller, ad.instrument_id)
        if job is None:
            ctx.fail(invalid('NO_JOB', 'you have no job on %s.' % ad.instrument_id, 'start one first (`chemlab run %s start`).' % ad.instrument_id))
        r = job.wait_reply(ctx.core, ctx.req, p.get('max'))
        return {'text': r['text'], 'json': r['json']}

    t = target_opts(ad)
    return {'start': Verb('start', 'start the run / measurement (long ones return job-N)', h_start, long_action=True, options=dict(t),
                          example='chemlab run INST start'),
            'stop': Verb('stop', 'stop the run / measurement', h_stop, options=dict(t), example='chemlab run INST stop'),
            'result': Verb('result', 'the result of the last run / measurement', h_result, readonly=True,
                           options=dict(t, **{'save-to': save_to_opt()}), example='chemlab run INST result --save-to result.json'),
            'wait': Verb('wait', 'wait for your latest job on this instrument', h_wait, readonly=True,
                         options={'max': Opt('longest wait', kind='duration', metavar='TIME')}, example='chemlab run INST wait --max 30min')}


class SharedAdapter(ClassAdapter):
    """The shared / thin layer for any instrument (shared_gen.py generates its fragment, "layer": "shared")."""
    CLASS = 'shared'
    TITLE = 'shared verbs'
    ABOUT = 'power, status, open/close named parts, ack, estop, and the run set method / start / stop / result / wait.'
    STANDARDS = 'SiLA 2 core (power, abort), OPC UA LADS (FunctionalUnit Start/Stop, controller set points)'
    PATH_GROUPS = (('start', 'stop', 'result'),)
    ALWAYS = ('status', 'wait')

    def build_verbs(self):
        out = base_verbs(self)
        out['method'] = method_verb(self)
        out.update(run_verbs(self))
        return out

    def summary(self, values):
        keys = self.map.get('status_slots') or []
        lines = ['%s = %s' % (k, short(values.get(k), 60)) for k in keys if k in values]
        return (lines or ['(no status slots mapped; see `chemlab read %s`)' % self.instrument_id]), {k: values.get(k) for k in keys}


def dumps(o):
    return json.dumps(o, ensure_ascii=False, default=str)


_ = RUNNING

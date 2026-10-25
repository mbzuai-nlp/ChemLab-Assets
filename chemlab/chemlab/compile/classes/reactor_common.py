"""Common machinery of the reactor and formulation classes: member mappings that are data, not code.

``SetpointAdapter`` (a ClassAdapter) lowers a verb through the member's step lists in the fragment:

    "stir": {"kind": "rotation",
             "set": [{"cap": "stirring.set_speed", "arg": "angular_speed_rad_s", "target": true},
                     {"cap": "stirring.set_enabled", "args": {"enabled": true}, "target": true,
                      "skip_if": {"slot": "zone_1.stirring.enabled", "per": "zone_{zone}.stirring.enabled", "value": true}}],
             "off": [{"cap": "stirring.set_enabled", "args": {"enabled": false}, "target": true}]}

A step is one native call: ``cap``; ``arg`` gets the agent's value converted into the unit the argument declares (``unit``
overrides an undeclared one such as 1/min; ``value`` replaces it with a constant, e.g. 0 for off); ``args`` are fixed arguments;
``keep`` = {ARG: slot | [slots] | {"slot", "per"}} are arguments the capability needs that stay as the instrument has them (read
from its own state, never invented); ``array`` = {"arg", "slots", "by": "channel"} sets one element of an array argument and keeps
the others; ``target`` adds the --zone / --channel / --head argument; ``skip_if`` skips the call when the state already is so;
``toggle`` marks a toggle capability (called only when ``state`` differs from the wanted value).  A ``per`` template names the
slot of the chosen target ({zone} = the label the agent typed, {native} = the native index), so a four-zone station needs one
mapping.  Member preconditions: ``"requires": {verb: [{"slot", "per", "value" | "not" | "min" | "max" | "truthy" | "falsy",
"code", "message", "fix"}]}`` (``verb:off`` for the off form).

Verb families built here: knobs (VALUE | on | off -> "set" / "on" / "off" steps), temperature (set-temp with --zone / --channel /
--ramp / --jacket / --wait, temp, wait-temp with a job, heat), method with class keys (missing native arguments kept from slots or
asked for by name), start / stop / result with jobs (operation, state, none), wait, named parts (open / close), and a class view
built from slot roles.  Instrument facts stay in configs/adapters/{reactor,formulation}.json.
"""
import copy
import difflib
import math
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import CompileError, invalid, refused, unsupported
from ..jobs import RUNNING, ConditionJob, OperationJob, StateJob, latest
from ..verbs import Arg, Opt, Verb, save_to_opt, target_opt
from ...generic import coerce                     # its usage errors stay exit 2

ON_WORDS = ('on', 'start', 'open', 'enable')
OFF_WORDS = ('off', 'stop', 'close', 'disable')
TARGETS = ('zone', 'channel', 'head')
_NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


def _types(schema):
    t = (schema or {}).get('type')
    return set(t if isinstance(t, list) else [t] if t else [])


class SetpointAdapter(ClassAdapter):
    KEYS = {}                         # method dictionary of the class: key -> (kind or None, help)
    VIEW = ()                         # class view rows: (role, label, kind) ; kind 'bool', 'text' or a quantity kind
    KNOB_KIND = {}                    # verb -> default quantity kind of its VALUE

    # ------------------------------------------------------------------ targets (--zone / --channel / --head)
    def target_opts(self, only=None, verb=None):
        """--zone / --channel / --head options of this member (only those that apply to ``verb``, when the target lists its verbs)."""
        out = {}
        for k, t in (self.map.get('targets') or {}).items():
            if (only is None or k in only) and (verb is None or t.get('verbs') is None or verb in t['verbs']):
                out[k] = target_opt(k, sorted((t.get('values') or {}).keys()) or None)
        return out

    def targets(self, ctx, p, verb=None):
        """{kind: (label, native)} of the --zone / --channel / --head the agent gave (required ones checked)."""
        out = {}
        verb = verb or (ctx.verb.name if ctx.verb else '')
        for k, t in (self.map.get('targets') or {}).items():
            v = p.get(k) if p is not None else None
            if t.get('verbs') is not None and verb not in t['verbs']:
                if v is not None:
                    ctx.fail(invalid('NOT_PER_' + k.upper(), '%s on %s applies to the whole instrument, not to one %s.' % (verb, self.instrument_id, k),
                                     'leave out --%s.' % k))
                continue
            req = t.get('required')
            if isinstance(req, list):
                req = verb in req
            if v is None:
                if t.get('default') is not None:
                    v = str(t['default'])
                elif req and verb not in (t.get('optional_for') or []):
                    ctx.fail(invalid('MISSING_' + k.upper(), '%s needs --%s on %s.' % (verb, k, self.instrument_id),
                                     'give --%s %s.' % (k, '|'.join(map(str, t.get('values') or {})))))
                else:
                    continue
            vals = t.get('values') or {}
            if str(v) not in vals:
                ctx.fail(invalid('UNKNOWN_' + k.upper(), '%s has no %s %r.' % (self.instrument_id, k, v),
                                 '%ss of %s: %s.' % (k, self.instrument_id, ', '.join(map(str, vals)))))
            out[k] = (str(v), vals[str(v)])
        for k in TARGETS:
            if p is not None and p.get(k) is not None and k not in (self.map.get('targets') or {}):
                ctx.fail(unsupported('NO_' + k.upper(), '%s has no %ss.' % (self.instrument_id, k), 'leave out --%s.' % k))
        return out

    def all_targets(self, kind):
        t = (self.map.get('targets') or {}).get(kind) or {}
        return [(lab, nat) for lab, nat in (t.get('values') or {}).items()]

    @staticmethod
    def fill(template, tg):
        """A 'per' template with the chosen target: {zone} {channel} {head} = label, {native} = native value."""
        kw = {}
        for k, (lab, nat) in (tg or {}).items():
            kw[k] = lab
            kw['native'] = nat
        try:
            return template.format(**kw)
        except (KeyError, IndexError):
            return None

    # ------------------------------------------------------------------ slots
    def resolve(self, spec, tg=None):
        """A slot spec (role name, slot id, or {"slot", "per", "unit", "key"}) -> a concrete spec for slot_si / raw."""
        if spec is None:
            return None
        if isinstance(spec, str) and spec in (self.map.get('slots') or {}):
            spec = self.map['slots'][spec]
        if isinstance(spec, dict):
            out = dict(spec)
            labels = [lab for (lab, _) in (tg or {}).values()]
            if out.get('per_map') and labels and labels[0] in out['per_map']:     # a target with its own slot (the extruder die)
                out['slot'] = out['per_map'][labels[0]]
                out.pop('index', None)
            else:
                if out.get('index') is True:                                       # an element of an array slot, by the native index
                    nats = [nat for (_, nat) in (tg or {}).values()]
                    out['index'] = nats[0] if nats else None
                if out.get('per') and tg:
                    s = self.fill(out['per'], tg)
                    if s:
                        out['slot'] = s
            out.pop('per', None)
            out.pop('per_map', None)
            return out
        return spec

    def has_role(self, role):
        return role in (self.map.get('slots') or {})

    def per_kind(self, role):
        """The target kind a role's slot depends on ('zone', 'channel', ...) or None."""
        s = (self.map.get('slots') or {}).get(role)
        targets = self.map.get('targets') or {}
        if isinstance(s, dict) and (s.get('per') or s.get('per_map') or s.get('index')):
            for k in TARGETS:
                if '{%s}' % k in (s.get('per') or ''):
                    return k
            for k in TARGETS:
                if k in targets:
                    return k
        return None

    def raw(self, values, spec, tg=None):
        spec = self.resolve(spec, tg)
        if spec is None:
            return None
        sid = spec['slot'] if isinstance(spec, dict) else spec
        sid, _, key = sid.partition('#')
        get = values.v if hasattr(values, 'v') else (lambda k: values.get(k))
        v = get(sid)
        if key and isinstance(v, dict):
            v = v.get(key)
        if isinstance(spec, dict) and 'index' in spec and spec['index'] is not True:
            i = spec['index']
            v = v[int(i)] if isinstance(v, list) and isinstance(i, int) and 0 <= i < len(v) else None
        return v

    def si(self, values, spec, tg=None):
        spec = self.resolve(spec, tg)
        if isinstance(spec, dict) and 'index' in spec:
            v = self.raw(values, spec, tg)
            if not finite(v):
                return None
            u = spec.get('unit') or units.declared(self.lab.contract['state_slots'].get(spec['slot']) or {})
            return units.from_declared(v, u) if u else v
        return self.slot_si(values, spec) if spec is not None else None

    # ------------------------------------------------------------------ member preconditions
    def need(self, ctx, key, tg=None, only=None):
        for r in (self.map.get('requires') or {}).get(key, []):
            if only is not None and r.get('code') not in only:
                continue
            got = self.raw(ctx, r, tg)
            if 'value' in r:
                ok = got == r['value']
            elif 'in' in r:
                ok = got in r['in']
            elif 'not' in r:
                ok = got not in r['not']
            elif r.get('truthy'):
                ok = bool(got)
            elif r.get('falsy'):
                ok = not got
            elif got is None and r.get('none_ok'):
                ok = True
            else:
                ok = finite(got) and ('max' not in r or got <= r['max'] + 1e-12) and ('min' not in r or got >= r['min'] - 1e-12)
            shown = short(got, 40)
            if r.get('kind') and finite(got):
                shown = units.fmt(self.si(ctx, r, tg), r['kind'])
            zone = ' '.join('%s %s' % (k, lab) for k, (lab, _) in (tg or {}).items())
            ctx.check(ok, r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=shown, target=zone),
                      r['fix'].format(inst=self.instrument_id, value=shown, target=zone), label=r.get('label', r.get('code', 'ready').lower()))

    def need_power(self, ctx):
        s = self.slot('power')
        if s:
            ctx.check(bool(ctx.v(s)), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                      'switch it on: `chemlab run %s power on`.' % self.instrument_id, label='power on')

    # ------------------------------------------------------------------ arguments
    def arg_schema(self, cap, arg):
        s = self.cap_schema(cap, arg)
        if 'anyOf' in s:
            s = next((x for x in s['anyOf'] if x.get('type') in ('number', 'integer', 'array')), s)
        return s

    def item_schema(self, cap, arg):
        s = self.arg_schema(cap, arg)
        return s.get('items') if s.get('type') == 'array' and isinstance(s.get('items'), dict) else s

    def unit_of(self, cap, arg, override=None):
        return units.norm_unit(override) if override else units.declared(self.item_schema(cap, arg))

    def to_arg(self, cap, arg, si, override_unit=None):
        """SI -> the value in the argument's declared unit (integers stay integers)."""
        if not finite(si):
            return si
        u = self.unit_of(cap, arg, override_unit)
        if u == 'rpm' and override_unit:
            v = si * 60.0 / (2 * math.pi)
        else:
            v = units.to_declared(si, u) if u else si
        sch = self.item_schema(cap, arg)
        if 'integer' in _types(sch):
            return int(round(v))
        return round(v, 12) if abs(v) >= 1e-6 else v

    def range_si(self, cap, arg, override_unit=None):
        sch = self.item_schema(cap, arg)
        lo, hi = sch.get('minimum'), sch.get('maximum')
        u = self.unit_of(cap, arg, override_unit)

        def conv(x):
            if x is None or not u:
                return x
            if u == 'rpm' and override_unit:
                return x * 2 * math.pi / 60.0
            return units.from_declared(x, u)
        enum = sch.get('enum')
        return conv(lo), conv(hi), ([conv(e) for e in enum] if enum else None)

    def check_value(self, ctx, step, si, kind, what, m=None):
        """The value inside the contract range of the step's argument and the mapping's own limits (usage error, nothing sent)."""
        m = m or {}
        lo, hi, enum = self.range_si(step['cap'], step['arg'], step.get('unit'))
        if m.get('min') is not None:
            v = units.parse(m['min'], kind) if isinstance(m['min'], str) else m['min']
            lo = v if lo is None else max(lo, v)
        if m.get('max') is not None:
            v = units.parse(m['max'], kind) if isinstance(m['max'], str) else m['max']
            hi = v if hi is None else min(hi, v)
        if m.get('max_slot'):
            v = self.si(ctx, m['max_slot'])
            if finite(v):
                hi = v if hi is None else min(hi, v)
        f = (lambda x: units.fmt(x, kind)) if kind in units.KINDS else (lambda x: '%g' % x)
        zero_ok = m.get('zero_ok') and abs(si) < 1e-12
        if enum and not any(abs(si - e) <= 1e-9 * max(1.0, abs(e)) for e in enum):
            ctx.fail(invalid('NOT_ALLOWED', '%s %s is not one of the values %s takes (%s).' % (what, f(si), self.instrument_id,
                                                                                           ', '.join(f(e) for e in enum)),
                             'give one of: %s.' % ', '.join(f(e) for e in enum)))
        if not zero_ok and ((lo is not None and si < lo - 1e-9 * max(1.0, abs(lo))) or (hi is not None and si > hi + 1e-9 * max(1.0, abs(hi)))):
            rng = '%s to %s' % (f(lo) if lo is not None else '-', f(hi) if hi is not None else '-')
            ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of %s (%s%s).' % (what, f(si), self.instrument_id, rng,
                                                                                         ', or 0' if m.get('zero_ok') else ''),
                             'give a %s from %s%s.' % (what, rng, ' (or 0 to stop)' if m.get('zero_ok') else '')))
        ctx.checks.append('%s within the range' % what)

    def kept(self, ctx, cap, arg, spec, tg):
        """An argument kept as the instrument has it: from its slot(s), converted from the slot unit to the argument unit."""
        if isinstance(spec, list):
            return [self.kept(ctx, cap, arg, s, tg) for s in spec]
        rs = self.resolve(spec, tg)
        v = self.raw(ctx, rs, tg)
        if finite(v) and isinstance(rs, (str, dict)):
            sid = rs['slot'] if isinstance(rs, dict) else rs
            su = units.declared((self.lab.contract['state_slots'].get(sid.partition('#')[0]) or {})) if '#' not in sid else None
            au = self.unit_of(cap, arg)
            if su and au and su != au and units.unit_kind(su) and units.unit_kind(su) == units.unit_kind(au):
                v = units.to_declared(units.from_declared(v, su), au)
        if v is None and 'null' in {x.get('type') for x in (self.cap_schema(cap, arg).get('anyOf') or []) if isinstance(x, dict)}:
            return None
        if v is None:
            ctx.fail(refused('STATE_UNKNOWN', '%s needs %s, and %s does not show it now (%s is empty).' % (
                cap, arg, self.instrument_id, rs['slot'] if isinstance(rs, dict) else rs),
                'set it explicitly (`chemlab run %s method %s=...`) or see `chemlab describe %s`.' % (self.instrument_id, arg, self.instrument_id)))
        return copy.deepcopy(v)

    def build_args(self, ctx, step, si, tg):
        args = copy.deepcopy(step.get('args') or {})
        for a, spec in (step.get('keep') or {}).items():
            args[a] = self.kept(ctx, step['cap'], a, spec, tg)
        arr = step.get('array')
        if arr:
            vals = [self.kept(ctx, step['cap'], arr['arg'], s, tg) for s in arr['slots']]
            if 'arg' not in step:
                lab_nat = tg.get(arr.get('by'))
                idx = lab_nat[1] if lab_nat else None
                v = step['value'] if 'value' in step else self.to_arg(step['cap'], arr['arg'], si, step.get('unit'))
                if idx is None:
                    vals = [v for _ in vals]                                  # no --channel: every element
                else:
                    vals[int(idx)] = v
            args[arr['arg']] = vals
        if 'arg' in step:
            args[step['arg']] = step['value'] if 'value' in step else self.to_arg(step['cap'], step['arg'], si, step.get('unit'))
        if step.get('target'):
            for k, (lab, nat) in tg.items():
                t = (self.map.get('targets') or {}).get(k) or {}
                if t.get('arg'):
                    args[t['arg']] = nat
        return args

    def run_steps(self, ctx, steps, si, tg, what, want=None):
        """Send the step list; returns the number of calls made."""
        n = 0
        for st in steps:
            cond = st.get('skip_if')
            if cond is not None:
                got = self.raw(ctx, cond, tg)
                if ('value' in cond and got == cond['value']) or (cond.get('truthy') and bool(got)) or (cond.get('falsy') and not got):
                    continue
            if st.get('toggle'):
                cur = self.raw(ctx, st['state'], tg)
                if bool(cur) == bool(st.get('want', want)):
                    continue
            args = self.build_args(ctx, st, si, tg)
            ctx.call(st['cap'], args, st.get('step', what).format(inst=self.instrument_id))
            n += 1
        return n

    @staticmethod
    def first_arg_step(steps):
        for st in steps or []:
            if 'arg' in st or ((st.get('array') or {}).get('arg') and 'value' not in st):
                return st
        return None

    # ------------------------------------------------------------------ knobs: VALUE | on | off
    def knob_verb(self, name, summary, kind, value_help, *, on_off=True, example=None, details=None, extra_opts=None,
                  preconditions=(), errors=(), reference=None):
        opts = dict(self.target_opts(verb=name))
        opts.update(extra_opts or {})
        what = 'VALUE|on|off' if on_off else 'VALUE'
        return Verb(name, summary, lambda ctx, p: self.h_knob(ctx, p, name, kind), positionals=[Arg(what, value_help)],
                    options=opts, example=example or 'chemlab run INST %s on' % name, details=details,
                    preconditions=list(preconditions), errors=list(errors), reference=reference)

    def switch_verb(self, name, summary, words=('on', 'off'), example=None, details=None, reference=None):
        return Verb(name, summary, lambda ctx, p: self.h_knob(ctx, p, name, None),
                    positionals=[Arg('|'.join(words), ' or '.join(words), choices=list(words))], options=dict(self.target_opts(verb=name)),
                    example=example or 'chemlab run INST %s %s' % (name, words[0]), details=details, reference=reference)

    def h_knob(self, ctx, p, name, kind):
        m = self.vmap(name)
        inst = self.instrument_id
        text = (p.pos[0] if p.pos else '').strip()
        low = text.lower()
        tg = self.targets(ctx, p)
        where = ''.join(' (%s %s)' % (k, lab) for k, (lab, _) in tg.items())
        word = m.get('word', name)
        if low == '0' and 'off' in m and not m.get('zero_is_value'):
            low = 'off'                                                   # stir 0, light 0, flow 0: the off form
        if low in ON_WORDS or low in OFF_WORDS:
            want = low in ON_WORDS
            on_word, off_word = m.get('words', ['on', 'off'])
            shown = on_word if want else off_word
            steps = m.get('on' if want else 'off')
            if steps is None:
                ctx.fail(invalid('NEEDS_VALUE' if 'set' in m else 'NO_SWITCH', '%s cannot be switched %s on %s.' % (word, shown, inst),
                                 m.get('switch_fix', 'give a value, e.g. `chemlab run INST %s %s`.' % (name, units.example(kind) if kind in units.KINDS else '1')).replace('INST', inst)))
            self.need_power_if(ctx, m, want)
            self.need(ctx, name if want else name + ':off', tg)
            st = m.get('state')
            if st is not None:
                cur = self.raw(ctx, st, tg)
                if bool(cur) == want and not m.get('always'):
                    return {'text': 'ok: %s %s already %s%s\n' % (inst, word, shown, where),
                            'json': {'verb': name, 'on': want, 'changed': False}}
            self.run_steps(ctx, steps, None, tg, '%s %s%s' % (word, shown, where), want=want)
            return {'text': 'ok: %s %s %s%s\n' % (inst, word, shown, where), 'json': {'verb': name, 'on': want, 'changed': True}}
        if 'set' not in m:
            ctx.fail(invalid('NO_VALUE', '%s takes on or off on %s, not a value.' % (name, inst),
                             m.get('value_fix', 'use `chemlab run INST %s on|off`.' % name).replace('INST', inst)))
        k = m.get('kind') or kind
        si = units.parse(text, k, what=word) if k in units.KINDS else self.plain(text, word)
        step = self.first_arg_step(m['set'])
        if step is not None:
            self.check_value(ctx, dict(step, arg=step.get('arg') or step['array']['arg']), si, k, word, m)
        self.need_power_if(ctx, m, True)
        self.need(ctx, name, tg)
        self.run_steps(ctx, m['set'], si, tg, 'set the %s to %s%s' % (word, self.fmt(si, k), where), want=True)
        if m.get('auto_on') and m.get('on'):
            st = m.get('state')
            if st is None or not self.raw(ctx, st, tg):
                self.run_steps(ctx, m['on'], None, tg, '%s on%s' % (word, where), want=True)
        notes = []
        st = m.get('state')
        if st is not None and not m.get('auto_on') and not self.raw(ctx, st, tg) and m.get('on'):
            notes.append('%s is off - switch it on with `chemlab run %s %s on%s`.' % (word, inst, name,
                                                                                    ''.join(' --%s %s' % (kk, lab) for kk, (lab, _) in tg.items())))
        return {'text': 'ok: %s %s %s%s\n' % (inst, word, self.fmt(si, k), where), 'notes': notes,
                'json': {'verb': name, 'value_si': si, 'value': self.fmt(si, k), 'target': {kk: lab for kk, (lab, _) in tg.items()}}}

    def need_power_if(self, ctx, m, want):
        if want and m.get('power', True):
            self.need_power(ctx)

    @staticmethod
    def plain(text, what):
        try:
            return float(text)
        except ValueError:
            raise invalid('BAD_NUMBER', '%s %r is not a number.' % (what, text), 'give a number, e.g. 0.5.')

    @staticmethod
    def fmt(si, kind):
        if not finite(si):
            return 'n/a'
        if kind == 'flow' and abs(si) >= 1e-3 / 60:                         # air flows: L/min, not thousands of mL/min
            return '%g L/min' % round(si * 60000.0, 3)
        return units.fmt(si, kind) if kind in units.KINDS else '%g' % si

    # ------------------------------------------------------------------ temperature verbs
    def tol(self):
        return float(self.param('tolerance_k', 1.0))

    def temp_verbs(self, reference=None, heat_summary='start or stop temperature control (heating / cooling)'):
        temp = Arg('TEMP', 'set point, e.g. 80C, 353.15K or 176F (a bare number is °C)', kind='temperature')
        so = dict(self.target_opts(verb='set-temp'))
        so.update({'wait': Opt('then wait until the temperature is within the tolerance', kind='flag'),
                   'max': Opt('with --wait: the longest wait (default 1 h)', kind='duration', metavar='TIME'),
                   'ramp': Opt('reach the set point over this time (instruments with a ramp)', kind='duration', metavar='TIME'),
                   'jacket': Opt('set the jacket temperature (Tj) instead of the reaction temperature (calorimeters)', kind='flag'),
                   'controller': Opt('the temperature controller to use (RC1: aqueous, organic, aqueous_pi, organic_pi)', metavar='NAME')})
        wo = dict(self.target_opts(verb='wait-temp'))
        wo.update({'within': Opt('tolerance (default: the member\'s, about 1 K)', kind='temperature_delta', metavar='DELTA'),
                   'max': Opt('longest wait (default 1 h)', kind='duration', metavar='TIME'),
                   'hold': Opt('stay within the tolerance this long (default 0)', kind='duration', metavar='TIME'),
                   'to': Opt('wait for this temperature instead of the set point', kind='temperature', metavar='TEMP'),
                   'job': Opt('return a job id at once instead of waiting (chemlab wait|status|cancel job-N)', kind='flag')})
        return {
            'set-temp': Verb('set-temp', 'set the temperature set point', self.h_set_temp, positionals=[temp], options=so,
                             preconditions=['set point within the instrument range (contract)', 'power on', 'the member\'s own conditions',
                                            'not above the safety limit where the instrument has one'],
                             errors=['OUT_OF_RANGE (usage)', 'POWER_OFF, ABOVE_SAFETY_LIMIT and member codes (refused)'],
                             example='chemlab run INST set-temp 60C', reference=reference),
            'heat': Verb('heat', heat_summary, lambda ctx, p: self.h_knob(ctx, p, 'heat', None),
                         positionals=[Arg('on|off', 'on or off', choices=['on', 'off'])], options=dict(self.target_opts(verb='heat')),
                         preconditions=['power on', 'the member\'s own conditions'], example='chemlab run INST heat on', reference=reference),
            'temp': Verb('temp', 'the current temperature(s) and set point(s), in °C', self.h_temp, readonly=True,
                         options=dict(self.target_opts(verb='temp'), **{'save-to': save_to_opt()}), example='chemlab run INST temp'),
            'wait-temp': Verb('wait-temp', 'wait until the temperature is at the set point (or --to T), moving lab time', self.h_wait_temp,
                              readonly=True, long_action=True, options=wo,
                              preconditions=['power on', 'temperature control on (where the instrument has a switch)'],
                              errors=['HEATING_OFF (refused)', 'NOT_REACHED (refused: still outside the tolerance after --max)'],
                              example='chemlab run INST wait-temp --within 1K --max 30min'),
        }

    def c(self, k):
        return units.fmt(k, 'temperature') if finite(k) else 'n/a'

    def h_set_temp(self, ctx, p):
        inst = self.instrument_id
        m = self.vmap('set-temp')
        k = p.pos[0]
        tg = self.targets(ctx, p)
        where = ''.join(' (%s %s)' % (kk, lab) for kk, (lab, _) in tg.items())
        if p.get('jacket'):
            if not m.get('jacket'):
                ctx.fail(invalid('NO_JACKET', '%s has no separate jacket temperature control.' % inst, 'leave out --jacket.'))
            m = dict(m['jacket'], **{'set_slot': m['jacket'].get('set_slot')})
        label = next((lab for kk, (lab, _) in tg.items()), None)
        steps = copy.deepcopy((m.get('by_target') or {}).get(label) or m['set'])
        step = self.first_arg_step(steps)
        self.check_value(ctx, dict(step, arg=step.get('arg') or step['array']['arg']), k, 'temperature', 'set point', m)
        step_c = self.param('step_c')
        if step_c:
            n = (k - 273.15) / float(step_c)
            if abs(n - round(n)) > 1e-6:
                ctx.fail(invalid('BAD_STEP', '%s takes set points in %g °C steps, not %s.' % (inst, step_c, p.raw['TEMP']),
                                 'round it, e.g. %.0fC.' % (round(n) * float(step_c))))
        ramp = p.get('ramp')
        if ramp is not None and not m.get('ramp_arg'):
            ctx.fail(invalid('NO_RAMP', '%s has no set-point ramp.' % inst, m.get('ramp_fix', 'leave out --ramp (or use a program where the '
                                                                             'instrument has one: `chemlab run %s program ...`).' % inst)))
        ctrl = p.get('controller')
        if ctrl is not None and not m.get('controller_arg'):
            ctx.fail(invalid('NO_CONTROLLER', '%s has no controller choice.' % inst, 'leave out --controller.'))
        self.need_power(ctx)
        self.need(ctx, 'set-temp:jacket' if p.get('jacket') else 'set-temp', tg)
        lim = self.si(ctx, 'limit', tg) if not p.get('jacket') else self.si(ctx, 'jacket_limit', tg)
        if finite(lim):
            ctx.check(k <= lim + 1e-9, 'ABOVE_SAFETY_LIMIT', 'the set point %s is above the safety limit of %s (%s).' % (self.c(k), inst, self.c(lim)),
                      'set a lower temperature, or raise the limit first (`chemlab run %s safety-limit T`) if the reaction and the vessel '
                      'allow it.' % inst, label='below the safety limit')
        if m.get('ramp_arg'):
            step.setdefault('args', {})[m['ramp_arg']] = self.to_arg(step['cap'], m['ramp_arg'], ramp if ramp is not None else
                                                                     float(m.get('ramp_default', 0.0)))
        if m.get('controller_arg'):
            allowed = self.arg_enum(step['cap'], m['controller_arg'])
            c = ctrl or self.param('controller', allowed[0] if allowed else None)
            if allowed and c not in allowed:
                ctx.fail(invalid('BAD_CHOICE', '--controller %s is not one of %s.' % (c, ', '.join(allowed)), 'use one of: %s.' % ', '.join(allowed)))
            step.setdefault('args', {})[m['controller_arg']] = c
        before = self.si(ctx, m.get('set_slot') or 'set', tg)
        self.run_steps(ctx, steps, k, tg, 'set the set point to %s%s' % (self.c(k), where))
        set_now = self.si(ctx, m.get('set_slot') or 'set', tg)
        if ramp is None and m.get('readback', True) and finite(set_now) and abs(set_now - k) > 0.051 and not ctx.dry_run:
            ctx.fail(refused('NOT_TAKEN', '%s still shows the set point %s, not %s.' % (inst, self.c(set_now), self.c(k)),
                             'check `chemlab run %s status`.' % inst, changed='unknown'))
        notes = []
        hs = self.has_role('heat') and self.raw(ctx, 'heat', tg)
        if self.has_role('heat') and not hs and self.supported('heat') and not p.get('jacket'):
            tgt = (self.map.get('targets') or {})
            opts = ''.join(' --%s %s' % (kk, lab) for kk, (lab, _) in tg.items() if tgt.get(kk, {}).get('verbs') is None or 'heat' in tgt[kk]['verbs'])
            notes.append('temperature control is off - start it with `chemlab run %s heat on%s`.' % (inst, opts))
        text = 'ok: set point %s%s%s (was %s)%s\n' % (self.c(k), where, (', ramping over %s' % units.fmt_s(ramp)) if ramp else '',
                                                    self.c(before), ('; now %s' % self.c(self.si(ctx, 'temp', tg))) if self.has_role('temp') else '')
        js = {'set_point_k': k, 'set_point_c': k - 273.15, 'was_k': before, 'temperature_k': self.si(ctx, 'temp', tg),
              'target': {kk: lab for kk, (lab, _) in tg.items()}}
        if p.get('wait') and not ctx.dry_run:
            r = self.wait_for(ctx, k, self.tol(), p.get('max') or 3600.0, 0.0, tg, dynamic=ramp is not None)
            text += r['text']
            js.update(r['json'])
        return {'text': text, 'json': js, 'notes': notes}

    def temp_rows(self, values, tg=None):
        """[(label, temperature K, set point K, extra)] for one target or for every zone / channel."""
        kind = self.per_kind('temp')
        rows = []
        if kind and not tg:
            for lab, nat in self.all_targets(kind):
                t = {kind: (lab, nat)}
                rows.append(('%s %s' % (kind, lab), self.si(values, 'temp', t), self.si(values, 'set', t), t))
        else:
            lab = ' '.join('%s %s' % (k, l) for k, (l, _) in (tg or {}).items())
            rows.append((lab, self.si(values, 'temp', tg), self.si(values, 'set', tg), tg))
        return rows

    def h_temp(self, ctx, p):
        tg = self.targets(ctx, p, 'temp')
        rows = self.temp_rows(ctx, tg)
        lines, js = [], []
        for lab, tk, sk, t in rows:
            extra = ''
            if self.has_role('jacket'):
                extra = ', jacket %s' % self.c(self.si(ctx, 'jacket', t))
            if self.has_role('temp2'):
                extra += ', %s %s' % (self.param('temp2_word', 'liquid'), self.c(self.si(ctx, 'temp2', t)))
            lines.append('%s%s (set point %s)%s' % (('%s: ' % lab) if lab else '', self.c(tk), self.c(sk), extra))
            js.append({'target': lab or None, 'temperature_k': tk, 'temperature_c': tk - 273.15 if finite(tk) else None,
                       'set_point_k': sk, 'set_point_c': sk - 273.15 if finite(sk) else None})
        out = {'temperatures': js}
        if len(js) == 1:
            out.update(js[0])
        return {'text': '%s: %s\n' % (self.instrument_id, '; '.join(lines)), 'json': out}

    def busy(self, values, tg):
        b = self.map.get('wait_while')                                     # e.g. a set-point ramp still running
        return bool(b and self.raw(values, b, tg))

    def wait_for(self, ctx, target, within, max_s, hold_s, tg, dynamic=False):
        """Advance lab time until |T - target| <= within (held for hold_s).  ``dynamic``: the target is the set point as it is at each
        poll (a ramping set point reaches its end value when the ramp is over)."""
        state = {'since': None, 'done': False}

        def tgt():
            return self.si(ctx, 'set', tg) if dynamic else target

        def inside(view):
            tk = self.si(ctx, 'temp', tg)
            t = tgt()
            ok = finite(tk) and finite(t) and abs(tk - t) <= within + 1e-9 and not self.busy(ctx, tg)
            if not ok:
                state['since'] = None
                return False
            if state['since'] is None:
                state['since'] = ctx.now()
            state['done'] = ctx.now() - state['since'] >= hold_s - 1e-9
            return state['done']
        t0 = ctx.now()
        if not inside(ctx.view):
            ctx.advance(max_s, until=inside, step='wait until %s ± %s' % (self.c(target), units.fmt(within, 'temperature_delta')),
                        poll_s=float(self.param('poll_s', 5.0)))
        tk = self.si(ctx, 'temp', tg)
        target = tgt() if finite(tgt()) else target
        if not state['done']:
            ctx.fail(refused('NOT_REACHED', '%s is at %s after %s; the target %s ± %s%s was not reached.' % (
                self.instrument_id, self.c(tk), units.fmt_s(ctx.now() - t0), self.c(target), units.fmt(within, 'temperature_delta'),
                ' (held for %s)' % units.fmt_s(hold_s) if hold_s else ''),
                'wait longer (`chemlab run %s wait-temp --max 2h`), or check that temperature control is on (`chemlab run %s status`).'
                % (self.instrument_id, self.instrument_id), detail={'temperature_k': tk, 'target_k': target}))
        return {'text': 'reached %s (target %s ± %s) after %s of waiting (t = %.0f s)\n' % (
            self.c(tk), self.c(target), units.fmt(within, 'temperature_delta'), units.fmt_s(ctx.now() - t0), ctx.now()),
            'json': {'reached': True, 'temperature_k': tk, 'target_k': target, 'waited_s': ctx.now() - t0}}

    def h_wait_temp(self, ctx, p):
        inst = self.instrument_id
        self.need_power(ctx)
        tg = self.targets(ctx, p, 'wait-temp')
        if self.per_kind('temp') and not tg:
            k = self.per_kind('temp')
            if self.param('wait_all') and p.get('to') is None:
                return self.wait_all(ctx, p, k)
            ctx.fail(invalid('MISSING_' + k.upper(), 'wait-temp needs --%s on %s.' % (k, inst),
                             'give --%s %s.' % (k, '|'.join(lab for lab, _ in self.all_targets(k)))))
        target = p.get('to') if p.get('to') is not None else self.si(ctx, 'set', tg)
        if not finite(target):
            ctx.fail(refused('NO_SET_POINT', '%s has no set point to wait for.' % inst, 'set one (`set-temp`) or give --to T.'))
        within = p.get('within') if p.get('within') is not None else self.tol()
        if self.has_role('heat') and self.supported('heat') and not self.raw(ctx, 'heat', tg):
            tk = self.si(ctx, 'temp', tg)
            if not (finite(tk) and tk >= target - within):                     # cooling down with the heater off is fine
                ctx.fail(refused('HEATING_OFF', 'temperature control of %s%s is off, so it will not reach %s (now %s).' % (
                    inst, ''.join(' %s %s' % (kk, lab) for kk, (lab, _) in tg.items()), self.c(target), self.c(tk)),
                    'start it: `chemlab run %s heat on%s`, then wait.' % (inst, ''.join(' --%s %s' % (kk, lab) for kk, (lab, _) in tg.items()))))
        max_s = p.get('max') or 3600.0
        hold = p.get('hold') or 0.0
        if ctx.dry_run:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': max_s},
                             'step': 'wait until %s ± %s' % (self.c(target), units.fmt(within, 'temperature_delta'))})
            return {'text': '', 'json': {}}
        if p.get('job'):
            ad = self

            dyn = p.get('to') is None

            def test(c):
                tk = ad.si(c, 'temp', tg)
                t = ad.si(c, 'set', tg) if dyn else target
                ok = finite(tk) and finite(t) and abs(tk - t) <= within + 1e-9 and not ad.busy(c, tg)
                return ok, '%s now, target %s ± %s' % (ad.c(tk), ad.c(t), units.fmt(within, 'temperature_delta'))
            job = ConditionJob(ctx.core, self, ctx.req, 'reach %s on %s' % (self.c(target), inst), test, max_s=max_s,
                               poll_s=float(self.param('poll_s', 5.0)))
            return {'text': 'started %s: wait until %s ± %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
                job.handle, self.c(target), units.fmt(within, 'temperature_delta'), job.handle, job.handle, job.handle),
                'json': {'job': job.handle, 'target_k': target}}
        return self.wait_for(ctx, target, within, max_s, hold, tg, dynamic=p.get('to') is None)

    def wait_all(self, ctx, p, k):
        """wait-temp without --zone / --channel on a member whose zones all heat together: every one within the tolerance of
        its own set point."""
        inst = self.instrument_id
        within = p.get('within') if p.get('within') is not None else self.tol()
        max_s = p.get('max') or 3600.0
        tgs = [{k: (lab, nat)} for lab, nat in self.all_targets(k)]

        def off(c):
            out = []
            for t in tgs:
                tk, sk = self.si(c, 'temp', t), self.si(c, 'set', t)
                if not (finite(tk) and finite(sk) and abs(tk - sk) <= within + 1e-9):
                    out.append('%s %s %s (set %s)' % (k, t[k][0], self.c(tk), self.c(sk)))
            return out
        if self.has_role('heat') and self.supported('heat') and not self.raw(ctx, 'heat') and off(ctx):
            ctx.fail(refused('HEATING_OFF', 'temperature control of %s is off, so the %ss will not reach their set points.' % (inst, k),
                             'start it: `chemlab run %s heat on`, then wait.' % inst))
        if ctx.dry_run:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': max_s}, 'step': 'wait until every %s is at its set point' % k})
            return {'text': '', 'json': {}}
        if p.get('job'):
            job = ConditionJob(ctx.core, self, ctx.req, 'every %s of %s at its set point' % (k, inst),
                               lambda c: (not off(c), '; '.join(off(c)) or 'all at their set points'), max_s=max_s,
                               poll_s=float(self.param('poll_s', 5.0)))
            return {'text': 'started %s: wait until every %s is at its set point\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
                job.handle, k, job.handle, job.handle, job.handle), 'json': {'job': job.handle}}
        t0 = ctx.now()
        if off(ctx):
            ctx.advance(max_s, until=lambda v: not off(ctx), step='wait until every %s is within %s of its set point' % (
                k, units.fmt(within, 'temperature_delta')), poll_s=float(self.param('poll_s', 5.0)))
        left = off(ctx)
        if left:
            ctx.fail(refused('NOT_REACHED', 'after %s not every %s of %s is at its set point: %s.' % (units.fmt_s(ctx.now() - t0), k, inst, '; '.join(left)),
                             'wait longer (`chemlab run %s wait-temp --max 2h`) or check `chemlab run %s status`.' % (inst, inst)))
        return {'text': 'every %s of %s is within %s of its set point after %s of waiting (t = %.0f s)\n' % (
            k, inst, units.fmt(within, 'temperature_delta'), units.fmt_s(ctx.now() - t0), ctx.now()),
            'json': {'reached': True, 'waited_s': ctx.now() - t0}}

    # ------------------------------------------------------------------ one-call actions (prime, clean, ...)
    def action_verb(self, name, summary, example=None, details=None, reference=None):
        return Verb(name, summary, lambda ctx, p: self.h_action(ctx, p, name), options=dict(self.target_opts(verb=name)), long_action=True,
                    example=example or 'chemlab run INST %s' % name, details=details, reference=reference)

    def h_action(self, ctx, p, name):
        inst = self.instrument_id
        m = self.vmap(name)
        tg = self.targets(ctx, p)
        self.need_power(ctx)
        self.need(ctx, name, tg)
        steps = m.get('steps') or [dict((k, v) for k, v in m.items() if k in ('cap', 'args', 'target', 'keep'))]
        r = None
        for st in steps:
            r = ctx.call(st['cap'], self.build_args(ctx, st, None, tg), st.get('step', m.get('what', name)))
        where = ''.join(' (%s %s)' % (kk, lab) for kk, (lab, _) in tg.items())
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.make_job(ctx, m, r, tg, cap=steps[-1]['cap'])
        if job is None:
            return {'text': 'ok: %s %s%s\n' % (inst, m.get('done', name + ' done'), where), 'json': {'result': (r or {}).get('result')}}
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}

    # ------------------------------------------------------------------ method with class keys
    def method_verb(self, reference=None):
        keys = ', '.join('%s (%s)' % (k, v[1]) for k, v in sorted(self.KEYS.items()))
        return Verb('method', 'set the run parameters: KEY=VALUE ... (class keys take units; native argument names pass through)',
                    self.h_method, options=dict(self.target_opts(verb='method')), variadic=('KEY=VALUE', 'a setting'),
                    details='class keys: %s. Settings you leave out keep the value the instrument shows now where it shows one; '
                            'otherwise they are asked for by name. A native argument name with a bare number takes the unit the contract '
                            'declares for it; lists are comma-separated (flow=0.5mL/min,0.5mL/min).' % keys,
                    preconditions=['every required setting given or kept from the instrument', 'each value inside the contract range'],
                    errors=['UNKNOWN_SETTING, MISSING_SETTING, OUT_OF_RANGE (usage)'], example='chemlab run INST method temp=80C time=10min',
                    reference=reference)

    def parse_setting(self, ctx, cap, native, text, kind, what, unit=None):
        sch = self.cap_schema(cap, native)
        if 'anyOf' in sch and text.strip().lower() in ('null', 'none'):
            return None
        base = self.arg_schema(cap, native)
        if base.get('type') == 'array' and not text.strip().startswith('['):
            parts = [x for x in re.split(r'[,;]', text) if x.strip()]
            return [self.parse_one(ctx, cap, native, x.strip(), kind, what, unit) for x in parts]
        if base.get('type') == 'array' or base.get('type') == 'object' or text.strip().startswith(('[', '{')):
            return coerce(text, sch, what)
        return self.parse_one(ctx, cap, native, text, kind, what, unit)

    def parse_one(self, ctx, cap, native, text, kind, what, unit=None):
        sch = self.item_schema(cap, native)
        types = _types(sch)
        u = self.unit_of(cap, native, unit)
        bare = re.fullmatch(r'\s*%s\s*' % _NUM, text) is not None
        numeric = re.match(r'\s*[+-]?(\d|\.\d)', text) is not None
        k = kind or (units.unit_kind(u) if u else None)
        if k in units.KINDS and numeric and (kind is not None or not bare):
            si = units.parse(text, k, what=what)
            lo, hi, enum = self.range_si(cap, native, unit)
            f = lambda x: units.fmt(x, k)                                  # noqa: E731
            if (lo is not None and si < lo - 1e-9 * max(1.0, abs(lo))) or (hi is not None and si > hi + 1e-9 * max(1.0, abs(hi))):
                ctx.fail(invalid('OUT_OF_RANGE', '%s=%s is outside the range of %s (%s to %s).' % (what, text, self.instrument_id,
                                                                                               f(lo) if lo is not None else '-', f(hi) if hi is not None else '-'),
                                 'give %s from %s to %s.' % (what, f(lo) if lo is not None else '-', f(hi) if hi is not None else '-')))
            return self.to_arg(cap, native, si, unit)
        if types & {'number', 'integer'} and bare:
            v = float(text)
            if 'integer' in types and v.is_integer():
                v = int(v)
            return v
        return coerce(text, sch, what)

    def h_method(self, ctx, p):
        inst = self.instrument_id
        m = self.vmap('method')
        cap = m['cap']
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        required = list((ctx.cap_spec(cap).get('input_schema') or {}).get('required') or [])
        keys = m.get('keys') or {}
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'see `chemlab help %s method`.' % inst))
        tg = self.targets(ctx, p)
        args = copy.deepcopy(m.get('args') or {})
        given = {}
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write method KEY=VALUE ... (see `chemlab help %s method`).' % inst))
            if k in keys:
                spec = keys[k]
                native, kind, unit = spec['arg'], spec.get('kind', (self.KEYS.get(k) or (None,))[0]), spec.get('unit')
            elif m.get('passthrough', True) and k in props:
                native, kind, unit = k, None, None
            else:
                close = difflib.get_close_matches(k, list(keys) + list(props), 2)
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (inst, k),
                                 ('did you mean %s? ' % ' or '.join(close) if close else '') +
                                 'settings: %s (native: %s).' % (', '.join(sorted(keys)), ', '.join(sorted(props)))))
            if native in given:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice (%s and %s).' % (native, given[native], k), 'give it once.'))
            given[native] = k
            args[native] = self.parse_setting(ctx, cap, native, v, kind, k, unit)
        missing = []
        for a in required:
            if a in args:
                continue
            spec = (m.get('keep') or {}).get(a)
            if spec is not None:
                args[a] = self.kept(ctx, cap, a, spec, tg)
            elif a in (m.get('defaults') or {}):
                args[a] = copy.deepcopy(m['defaults'][a])
            else:
                missing.append(a)
        if missing:
            names = []
            for a in missing:
                ks = [k for k, s in keys.items() if s['arg'] == a]
                names.append('%s (%s)' % (ks[0], a) if ks else a)
            ctx.fail(invalid('MISSING_SETTING', '%s needs %s in its method; %s does not show the current value, so give it.' % (
                inst, ', '.join(names), inst), 'add %s.' % ' '.join('%s=...' % ((([k for k, s in keys.items() if s['arg'] == a] or [a])[0])) for a in missing)))
        for a in list(args):
            if a not in props:
                del args[a]
        if m.get('target'):
            for kk, (lab, nat) in tg.items():
                t = (self.map.get('targets') or {}).get(kk) or {}
                if t.get('arg'):
                    args[t['arg']] = nat
        self.need(ctx, 'method', tg)
        ctx.call(cap, args, 'set the method (%s)' % ', '.join('%s=%s' % (k, short(v, 30)) for k, v in args.items()))
        return {'text': 'ok: method of %s set: %s\n' % (inst, short(args, 400)), 'json': {'arguments': args}}

    # ------------------------------------------------------------------ run: start / stop / result / wait
    def run_verbs(self, reference=None):
        t = self.target_opts(verb='start')
        start_opts = dict(t, name=Opt('a name for the run record (instruments that keep named experiments)', metavar='NAME'))
        stop_opts = dict(self.target_opts(verb='stop'), **{'cool-to': Opt('after stopping, cool to this temperature (instruments that can)', kind='temperature',
                                              metavar='TEMP')})
        res_opts = dict(self.target_opts(verb='result'), **{'save-to': save_to_opt(), 'experiment': Opt('which experiment record (default: the latest)', metavar='ID')})
        return {
            'start': Verb('start', 'start the run (long runs return job-N: chemlab status|wait|cancel job-N)', self.h_start,
                          long_action=True, options=start_opts, preconditions=['the member\'s own conditions (lid, sample, ...)'],
                          errors=['member codes (refused)'], example='chemlab run INST start', reference=reference),
            'stop': Verb('stop', 'stop the run', self.h_stop, options=stop_opts, example='chemlab run INST stop', reference=reference),
            'result': Verb('result', 'the result / record of the last run', self.h_result, readonly=True, options=res_opts,
                           example='chemlab run INST result --save-to run.json', reference=reference),
            'wait': Verb('wait', 'wait for your latest job on this instrument', self.h_wait_job, readonly=True,
                         options={'max': Opt('longest wait', kind='duration', metavar='TIME')}, example='chemlab run INST wait --max 30min'),
        }

    def h_start(self, ctx, p):
        inst = self.instrument_id
        m = self.vmap('start')
        tg = self.targets(ctx, p)
        self.need_power(ctx)
        self.need(ctx, 'start', tg)
        if p.get('name') is not None and not m.get('name_arg'):
            ctx.fail(invalid('NO_NAME', '%s does not keep named runs.' % inst, 'leave out --name.'))
        steps = copy.deepcopy(m.get('steps') or [dict((k, v) for k, v in m.items() if k in ('cap', 'args', 'target', 'keep', 'skip_if'))])
        main = steps[-1]
        if m.get('name_arg'):
            main.setdefault('args', {})[m['name_arg']] = p.get('name') or self.param('run_name', 'chemlab run')
        r = None
        for st in steps:
            cond = st.get('skip_if')
            if cond is not None and self.raw(ctx, cond, tg) == cond.get('value'):
                continue
            args = self.build_args(ctx, st, None, tg)
            r = ctx.call(st['cap'], args, st.get('step', 'start (%s)' % st['cap']))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = (r or {}).get('result') if isinstance((r or {}).get('result'), dict) else {}
        if m.get('id_key') and res.get(m['id_key']):
            self.last_id = res[m['id_key']]
        job = self.make_job(ctx, m, r, tg)
        where = ''.join(' (%s %s)' % (kk, lab) for kk, (lab, _) in tg.items())
        if job is None:
            return {'text': 'ok: %s started%s%s\n' % (inst, where, (' (%s %s)' % (m['id_key'], res.get(m['id_key']))) if m.get('id_key') else ''),
                    'json': {'status': r.get('status'), 'result': r.get('result')}}
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}

    def make_job(self, ctx, m, r, tg, cap=None):
        cap = cap or (m.get('steps') or [m])[-1]['cap']
        kind = m.get('job') or ('operation' if (r or {}).get('operation_id') else 'none')
        where = ''.join(' %s %s' % (kk, lab) for kk, (lab, _) in tg.items())
        if kind == 'operation' and (r or {}).get('operation_id'):
            return OperationJob(ctx.core, self, ctx.req, '%s %s%s' % (self.instrument_id, m.get('what', cap), where), cap, r['operation_id'],
                                max_s=m.get('max_s'))
        if kind == 'state':
            st = m['state']
            ad = self

            def state(c):
                v = ad.raw(c, st, tg)
                if v in (st.get('done') or []):
                    return 'succeeded', '%s = %s' % (ad.resolve(st, tg)['slot'], v), {'state': v}
                if v in (st.get('failed') or []):
                    return 'failed', '%s = %s' % (ad.resolve(st, tg)['slot'], v), {'state': v}
                return 'running', '%s = %s' % (ad.resolve(st, tg)['slot'], v), {'state': v}
            sm = self.vmap('stop')
            stop = None
            if sm:
                def stop(c):
                    for s in sm.get('steps') or [sm]:
                        c.call(s['cap'], ad.build_args(c, s, None, tg), 'stop (%s)' % s['cap'])
                    return 'stopped'
            return StateJob(ctx.core, self, ctx.req, '%s %s%s' % (self.instrument_id, m.get('what', cap), where), state, stop=stop,
                            max_s=float(m.get('max_s', 7200)), poll_s=float(m.get('poll_s', 5.0)))
        return None

    def h_stop(self, ctx, p):
        inst = self.instrument_id
        m = self.vmap('stop')
        tg = self.targets(ctx, p)
        self.need(ctx, 'stop', tg)
        cool = p.get('cool-to')
        if cool is not None and not m.get('cool_arg'):
            ctx.fail(invalid('NO_COOL_TO', '%s cannot cool to a temperature when stopping.' % inst, 'leave out --cool-to; set a temperature afterwards.'))
        steps = copy.deepcopy(m.get('steps') or [dict((k, v) for k, v in m.items() if k in ('cap', 'args', 'target', 'keep', 'skip_if'))])
        if m.get('cool_arg'):
            main = steps[-1]
            main.setdefault('args', {})
            if cool is not None:
                self.check_value(ctx, {'cap': main['cap'], 'arg': m['cool_arg']}, cool, 'temperature', 'end temperature')
                main['args'][m['cool_arg']] = self.to_arg(main['cap'], m['cool_arg'], cool)
                main['args'].update(m.get('cool_args') or {})
            else:
                main['args'][m['cool_arg']] = None
        n = 0
        for st in steps:
            cond = st.get('skip_if')
            if cond is not None:
                got = self.raw(ctx, cond, tg)
                if ('value' in cond and got == cond['value']) or (cond.get('falsy') and not got) or (cond.get('truthy') and bool(got)):
                    continue
            ctx.call(st['cap'], self.build_args(ctx, st, None, tg), st.get('step', 'stop (%s)' % st['cap']))
            n += 1
        where = ''.join(' (%s %s)' % (kk, lab) for kk, (lab, _) in tg.items())
        if n == 0:
            return {'text': 'ok: %s has nothing running%s\n' % (inst, where), 'json': {'changed': False}}
        return {'text': 'ok: %s stopped%s%s\n' % (inst, where, (', cooling to %s' % self.c(cool)) if cool is not None else ''),
                'json': {'changed': True}}

    last_id = None

    def h_result(self, ctx, p):
        inst = self.instrument_id
        m = self.vmap('result')
        tg = self.targets(ctx, p)
        if p.get('experiment') is not None and not m.get('id_arg'):
            ctx.fail(invalid('NO_EXPERIMENTS', '%s keeps no experiment records.' % inst, 'leave out --experiment.'))
        if m.get('cap'):
            args = copy.deepcopy(m.get('args') or {})
            if m.get('id_arg'):
                rid = p.get('experiment') or (self.raw(ctx, m['id_slot']) if m.get('id_slot') else None) or self.last_id
                if not rid:
                    ctx.fail(refused('NO_RECORD', '%s has no experiment record yet.' % inst, 'start one first (`chemlab run %s start`).' % inst))
                args[m['id_arg']] = rid
            if m.get('target'):
                args.update({(self.map['targets'][kk]).get('arg'): nat for kk, (lab, nat) in tg.items() if self.map['targets'][kk].get('arg')})
            r = ctx.call(m['cap'], args, 'read the result (%s)' % m['cap'], query=True)
            res = r.get('result')
        else:
            res = {}
            for s in m.get('reads') or []:
                res[s] = ctx.v(s)
            for role in m.get('roles') or []:
                rs = self.resolve(role, tg)
                sid = rs['slot'] if isinstance(rs, dict) else rs
                res[sid] = self.raw(ctx, rs, tg)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        lines = ['%s result%s:' % (inst, ''.join(' (%s %s)' % (kk, lab) for kk, (lab, _) in tg.items()))]
        if isinstance(res, dict):
            for k, v in list(res.items())[:30]:
                if isinstance(v, list) and len(v) > 6:
                    lines.append('  %s = [%d entries] (--save-to FILE for all)' % (k, len(v)))
                else:
                    lines.append('  %s = %s' % (k, short(v, 160)))
        else:
            lines.append('  %s' % short(res, 400))
        return {'text': '\n'.join(lines) + '\n', 'json': {'result': res}}

    def h_wait_job(self, ctx, p):
        job = latest(ctx.core, ctx.caller, self.instrument_id)
        if job is None:
            ctx.fail(invalid('NO_JOB', 'you have no job on %s.' % self.instrument_id, 'start one first (`chemlab run %s start`).' % self.instrument_id))
        r = job.wait_reply(ctx.core, ctx.req, p.get('max'))
        return {'text': r['text'], 'json': r['json']}

    # ------------------------------------------------------------------ named parts
    def part_verbs(self):
        def make(action):
            def h(ctx, p):
                parts = (self.vmap('open') or {}).get('parts') or {}
                name = p.pos[0] if p.pos else None
                if name is None:
                    if len(parts) != 1:
                        ctx.fail(invalid('WHICH_PART', '%s has several parts: %s.' % (self.instrument_id, ', '.join(parts)),
                                         'name one, e.g. `%s %s`.' % (action, next(iter(parts)))))
                    name = next(iter(parts))
                if name not in parts:
                    ctx.fail(invalid('UNKNOWN_PART', '%s has no part %r to open or close.' % (self.instrument_id, name), 'parts: %s.' % ', '.join(parts)))
                spec = parts[name]
                so, sc = spec.get('state_open'), spec.get('state_closed')
                is_open = bool(self.raw(ctx, so)) if so else (not self.raw(ctx, sc) if sc else None)
                is_closed = bool(self.raw(ctx, sc)) if sc else (not self.raw(ctx, so) if so else None)
                if (action == 'open' and is_open) or (action == 'close' and is_closed):
                    return {'text': 'ok: %s %s is already %s\n' % (self.instrument_id, name, 'open' if action == 'open' else 'closed'),
                            'json': {'part': name, 'action': action, 'changed': False}}
                self.need(ctx, '%s:%s' % (action, name))
                self.run_steps(ctx, spec[action], None, {}, '%s the %s' % (action, name), want=(action == 'open'))
                return {'text': 'ok: %s %s %s\n' % (self.instrument_id, name, 'opened' if action == 'open' else 'closed'),
                        'json': {'part': name, 'action': action, 'changed': True}}
            return h
        pos = [Arg('PART', 'which part (lid, vent, ...); optional when there is only one', required=False)]
        return {'open': Verb('open', 'open a lid, vent or cover (with the class safety checks)', make('open'), positionals=pos,
                             example='chemlab run INST open lid'),
                'close': Verb('close', 'close (and lock) a lid, vent or cover', make('close'), positionals=pos, example='chemlab run INST close lid')}

    # ------------------------------------------------------------------ class view
    def summary(self, values):
        p = self.slot('power')
        if p and not self.raw(values, 'power'):
            return ['power off'], {'power': False}
        lines, js = [], {'power': True}
        main, more = [], []
        kinds = {}
        for role, label, kind in self.VIEW:
            if not self.has_role(role):
                continue
            pk = self.per_kind(role)
            if pk:
                kinds.setdefault(pk, []).append((role, label, kind))
                continue
            v = self.view_value(values, role, kind, None)
            if v is None:
                continue
            js[role] = v[1]
            (main if role in ('temp', 'set') else more).append('%s %s' % (label, v[0]))
        if main or more:
            lines.append(', '.join(main + more))
        for pk, roles in kinds.items():
            for lab, nat in self.all_targets(pk):
                tg = {pk: (lab, nat)}
                bits = []
                for role, label, kind in roles:
                    v = self.view_value(values, role, kind, tg)
                    if v is None:
                        continue
                    js.setdefault('%s_%s' % (pk, lab), {})[role] = v[1]
                    bits.append('%s %s' % (label, v[0]))
                if bits:
                    lines.append('%s %s: %s' % (pk, lab, ', '.join(bits)))
        return (lines or ['(no class view for this instrument; see `chemlab read %s`)' % self.instrument_id]), js

    def view_value(self, values, role, kind, tg):
        if kind == 'bool':
            v = self.raw(values, role, tg)
            return None if v is None else ('on' if v else 'off', bool(v))
        if kind == 'text':
            v = self.raw(values, role, tg)
            return None if v in (None, '') else (short(v, 40).strip('"'), v)
        v = self.si(values, role, tg)
        rv = self.raw(values, role, tg)
        if isinstance(rv, list):
            spec = self.resolve(role, tg)
            sid = (spec['slot'] if isinstance(spec, dict) else spec).partition('#')[0]
            u = (spec.get('unit') if isinstance(spec, dict) else None) or units.declared(self.lab.contract['state_slots'].get(sid) or {})
            v = [units.from_declared(x, u) if u and finite(x) else x for x in rv]
            shown = [self.fmt(x, kind) for x in v]
            return ((shown[0] + (' (all %d)' % len(shown) if len(shown) > 1 else '')) if len(set(shown)) == 1 else ', '.join(shown), v)
        if not finite(v):
            return None
        return (self.fmt(v, kind), v)

    # ------------------------------------------------------------------ mapping check: templates, keep slots, keys
    def check_mapping(self, lab):
        problems = ClassAdapter.check_mapping(self, lab)
        visible = {s.lower() for s in lab.view.slots}
        caps = lab.contract['capabilities']
        targets = self.map.get('targets') or {}

        def tvals(template, skip=()):
            ks = [k for k in TARGETS if '{%s}' % k in template] or [k for k in targets if '{native}' in template]
            if not ks:
                return [template]
            k = ks[0]
            return [self.fill(template, {k: (lab_, nat)}) for lab_, nat in (targets.get(k) or {}).get('values', {}).items() if lab_ not in skip]

        def slot_ok(s, where):
            if isinstance(s, list):
                for x in s:
                    slot_ok(x, where)
                return
            if isinstance(s, dict):
                pm = s.get('per_map') or {}
                for t in tvals(s['per'], skip=pm) if s.get('per') else []:
                    if t is None or t.split('#', 1)[0].lower() not in visible:
                        problems.append('%s: no visible state slot %s' % (where, t))
                for t in pm.values():
                    if t.split('#', 1)[0].lower() not in visible:
                        problems.append('%s: no visible state slot %s' % (where, t))
                s = s.get('slot')
            if isinstance(s, str):
                if s in (self.map.get('slots') or {}):
                    return
                if s.split('#', 1)[0].lower() not in visible:
                    problems.append('%s: no visible state slot %s' % (where, s))

        def walk(o, where, cap=None):
            if isinstance(o, dict):
                cap = o.get('cap') if isinstance(o.get('cap'), str) else cap
                props = ((caps.get(cap) or {}).get('input_schema') or {}).get('properties') or {} if cap else {}
                for a, spec in (o.get('keep') or {}).items():
                    if cap and a not in props:
                        problems.append('%s: %s has no argument %s (keep)' % (where, cap, a))
                    slot_ok(spec, where + '.keep.' + a)
                if isinstance(o.get('array'), dict):
                    if cap and o['array'].get('arg') not in props:
                        problems.append('%s: %s has no argument %s (array)' % (where, cap, o['array'].get('arg')))
                    slot_ok(o['array'].get('slots') or [], where + '.array')
                for key in ('skip_if', 'state'):
                    if isinstance(o.get(key), (dict, str)):
                        slot_ok(o[key], where + '.' + key)
                for k in ('ramp_arg', 'controller_arg', 'cool_arg', 'name_arg', 'id_arg'):
                    if o.get(k):
                        c2 = ((self.first_arg_step(o.get('set')) or {}).get('cap') if k in ('ramp_arg', 'controller_arg')
                              else cap or ((o.get('steps') or [{}])[-1]).get('cap'))
                        p2 = ((caps.get(c2) or {}).get('input_schema') or {}).get('properties') or {}
                        if c2 and o[k] not in p2:
                            problems.append('%s: %s has no argument %s (%s)' % (where, c2, o[k], k))
                if isinstance(o.get('keys'), dict) and cap:
                    for kk, spec in o['keys'].items():
                        if spec.get('arg') not in props:
                            problems.append('%s: method key %s -> %s, which %s does not take' % (where, kk, spec.get('arg'), cap))
                for k, x in o.items():
                    if k not in ('keep', 'array', 'skip_if', 'state', 'keys') and isinstance(x, (dict, list)):
                        walk(x, '%s.%s' % (where, k), cap)
            elif isinstance(o, list):
                for x in o:
                    walk(x, where, cap)
        walk(self.map.get('verbs') or {}, 'verbs')
        for role, s in (self.map.get('slots') or {}).items():
            slot_ok(s, 'slots.' + role)
        for verb, reqs in (self.map.get('requires') or {}).items():
            for r in reqs:
                slot_ok(r, 'requires.' + verb)
        if self.map.get('wait_while'):
            slot_ok(self.map['wait_while'], 'wait_while')
        for k, t in targets.items():
            if t.get('arg'):
                for v in self.verb_caps_with_target():
                    props = ((caps.get(v) or {}).get('input_schema') or {}).get('properties') or {}
                    if t['arg'] not in props:
                        problems.append('targets.%s: %s has no argument %s' % (k, v, t['arg']))
        return problems

    def verb_caps_with_target(self):
        out = []

        def walk(o):
            if isinstance(o, dict):
                if o.get('target') is True and isinstance(o.get('cap'), str):
                    out.append(o['cap'])
                for x in o.values():
                    walk(x)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(self.map.get('verbs') or {})
        return sorted(set(out))


_ = (RUNNING, unsupported)

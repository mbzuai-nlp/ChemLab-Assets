"""Common machinery of the evaporation and extraction classes: process instruments that take set points
(temperature, vacuum / pressure, rotation, flow), a method of class keys, and a run with start / stop / result.

``SeparationAdapter`` (a ClassAdapter) keeps every instrument fact in the fragment (configs/adapters/evaporation.json,
extraction.json) and gives the two classes these mapping forms:

* setter verbs (set-temp, vacuum, pressure, flow, condenser, feed): ``{"cap", "arg", "args": {fixed}, "unit": U (only when the
  native argument declares no unit; the value is given in U), "range_from": {"cap", "arg"} (where the real range is declared),
  "step_si": S (the instrument takes whole multiples of S), "heat_arg": A | "heat_cap": C (--heat), "off": {"calls": [...]}}``;
* call lists: ``"calls": [{"cap", "args", "step", "skip_if": {"slot", "value"}}]`` (composite verbs, e.g. the rotavap start
  = bath heating on + rotation on);
* the method dictionary: ``{"cap": DEFAULT_CAP, "keys": {KEY: {"cap", "arg", "key" (an object argument's field), "kind"}},
  "passthrough": true, "keep": {CAP: {ARG: {"slot", "key"}}}}`` - keys of one capability go into one call; a required argument the
  agent leaves out is kept from the instrument's own state slot (never invented), else it is a usage error naming it;
* member preconditions with comparisons: ``"requires": {VERB: [{"slot", "key", "value" | "not" | "in" | "truthy" | "falsy" |
  "min" | "max", "code", "message", "fix"}]}`` - checked before anything is sent;
* jobs: an operation_id gives an OperationJob; ``"state": {"slot", "done", "failed"}`` a StateJob; ``"until": {"slot", "value",
  "when": {...}}`` a ConditionJob (e.g. the rotavap timer).

The class modules (evaporation.py, extraction.py) hold the verb tables and the class views; no member data lives here.
"""
import difflib
import json
import math
import re

from ...generic import CmdError, coerce
from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused
from ..jobs import ConditionJob, OperationJob, StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt, target_opt

ON_OFF = ['on', 'off']
_NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


def _types(schema):
    t = (schema or {}).get('type')
    return set(t if isinstance(t, list) else [t] if t else [])


def _numeric(schema):
    """The numeric branch of a schema (anyOf [number, null] -> the number part), or None."""
    if not isinstance(schema, dict):
        return None
    if _types(schema) & {'number', 'integer'}:
        return schema
    for x in schema.get('anyOf') or []:
        if isinstance(x, dict) and _types(x) & {'number', 'integer'}:
            return x
    return None


class SeparationAdapter(ClassAdapter):
    ROLES = ()                         # (role, label, kind) of the class view; kind 'bool', 'text', 'plain' or a units kind
    KEYS = {}                          # method dictionary: key -> (kind or None, help) - documented in help INST method
    PATH_GROUPS = (('start', 'stop', 'result'),)

    # ------------------------------------------------------------------ contract helpers
    def schema_of(self, cap, arg, key=None):
        s = self.cap_schema(cap, arg)
        if key is not None:
            s = ((s or {}).get('properties') or {}).get(key) or {}
        return s

    def bounds(self, schema):
        """(lo, hi, lo_exclusive) of a numeric schema in its declared unit."""
        n = _numeric(schema) or {}
        lo, hi, excl = n.get('minimum'), n.get('maximum'), False
        if lo is None and n.get('exclusiveMinimum') is not None:
            lo, excl = n['exclusiveMinimum'], True
        return lo, hi, excl

    def fmt_si(self, si, kind):
        if kind in (None, 'plain', 'count'):
            return '%g' % si if finite(si) else str(si)
        return fmtq(si, kind)

    def check_range(self, ctx, schema, value, kind, what, unit=None):
        """A value in the declared unit (or ``unit``) against the schema's range: usage error with friendly units."""
        lo, hi, excl = self.bounds(schema)
        u = unit or units.declared(schema)
        to_si = (lambda x: units.from_declared(x, u)) if u and units.unit_kind(u) else (lambda x: x)
        bad = (lo is not None and (value <= lo if excl else value < lo - 1e-12 * max(1.0, abs(lo)))) or \
              (hi is not None and value > hi + 1e-12 * max(1.0, abs(hi)))
        if bad:
            rng = '%s%s to %s' % ('above ' if excl else '', self.fmt_si(to_si(lo), kind) if lo is not None else 'any',
                                  self.fmt_si(to_si(hi), kind) if hi is not None else 'any')
            ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of %s (%s).' % (what, self.fmt_si(to_si(value), kind),
                                                                                   self.instrument_id, rng),
                             'give a value from %s.' % rng))
        if 'integer' in _types(_numeric(schema) or {}):
            if abs(value - round(value)) > 1e-9:
                ctx.fail(invalid('NOT_WHOLE', '%s must be a whole number on %s (got %g).' % (what, self.instrument_id, value),
                                 'give a whole number.'))
            return int(round(value))
        return value

    def to_arg(self, ctx, cap, arg, si, kind, what, key=None, unit=None, range_from=None):
        """SI -> the native argument value (declared unit, or ``unit`` when the argument declares none), range-checked against
        the argument's schema or the ``range_from`` capability argument."""
        schema = self.schema_of(cap, arg, key)
        u = unit or units.declared(schema)
        value = units.to_declared(si, u) if u and units.unit_kind(u) else si
        if range_from:
            rs = self.schema_of(range_from['cap'], range_from['arg'], range_from.get('key'))
            ru = units.declared(rs)
            v2 = units.to_declared(si, ru) if ru and units.unit_kind(ru) else si
            self.check_range(ctx, rs, v2, kind, what)
            if 'integer' in _types(_numeric(schema) or {}):
                value = int(round(value))
            return value
        return self.check_range(ctx, schema, value, kind, what, unit=u)

    def targets(self, ctx, p):
        """--channel / --zone / --cell / --head -> native arguments; a target with a "default" is used when the option is left out."""
        out = {}
        for k, t in (self.map.get('targets') or {}).items():
            v = p.get(k)
            if v is None and t.get('default') is not None:
                v = t['default']
            if v is None:
                if t.get('required'):
                    ctx.fail(invalid('MISSING_' + k.upper(), '%s needs --%s on %s.' % (ctx.verb.name, k, self.instrument_id),
                                     'give --%s %s.' % (k, '|'.join(map(str, t.get('values') or {})))))
                continue
            vals = t.get('values')
            if vals is not None and str(v) not in vals:
                ctx.fail(invalid('UNKNOWN_' + k.upper(), '%s has no %s %r.' % (self.instrument_id, k, v),
                                 '%ss of %s: %s.' % (k, self.instrument_id, ', '.join(map(str, vals)))))
            out[t['arg']] = vals[str(v)] if vals is not None else v
        return out

    # ------------------------------------------------------------------ member preconditions
    def slot_value(self, ctx, slot, key=None):
        v = ctx.v(slot)
        if key is not None:
            v = v.get(key) if isinstance(v, dict) else None
        return v

    def need(self, ctx, verb, only=None, skip=()):
        """The member's "requires" entries for a verb (value / not / in / truthy / falsy / min / max), checked before sending."""
        for r in (self.map.get('requires') or {}).get(verb, []):
            if (only is not None and r.get('code') not in only) or r.get('code') in skip:
                continue
            got = self.slot_value(ctx, r['slot'], r.get('key'))
            if 'value' in r:
                ok = got == r['value']
            elif 'in' in r:
                ok = got in r['in']
            elif 'not' in r:
                ok = got not in (r['not'] if isinstance(r['not'], list) else [r['not']])
            elif r.get('any'):
                ok = isinstance(got, list) and any(got)
            elif r.get('truthy'):
                ok = bool(got)
            elif r.get('falsy'):
                ok = not got
            elif 'near' in r:
                ref = self.slot_value(ctx, r['near']['slot'], r['near'].get('key'))
                ok = finite(got) and finite(ref) and abs(got - ref) <= float(r.get('tol', 0))
                got = '%s (set point %s)' % (short(got, 30), short(ref, 30))
            elif 'min' in r:
                ok = finite(got) and got >= r['min']
            elif 'max' in r:
                ok = finite(got) and got <= r['max']
            else:
                ok = True
            shown = got if ('near' in r and isinstance(got, str)) else short(got, 60)
            ctx.check(ok, r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=shown),
                      r['fix'].format(inst=self.instrument_id, value=shown), label=r.get('label', r['slot']))

    # ------------------------------------------------------------------ calls
    def run_calls(self, ctx, calls, fill=None):
        """A list of {"cap", "args", "step", "skip_if": {"slot", "value"}}; ``fill``: {ARG: value} added where the capability
        has that argument.  Returns the last response."""
        r = None
        for c in calls:
            sk = c.get('skip_if')
            if sk and not ctx.dry_run and self.slot_value(ctx, sk['slot'], sk.get('key')) == sk['value']:
                ctx.notes.append('%s skipped: %s is already %s' % (c['cap'], sk['slot'], json.dumps(sk['value'])))
                continue
            args = dict(c.get('args') or {})
            props = (ctx.cap_spec(c['cap']).get('input_schema') or {}).get('properties') or {}
            for k, v in (fill or {}).items():
                if k in props:
                    args[k] = v
            r = ctx.call(c['cap'], args, c.get('step') or c['cap'])
        return r

    # ------------------------------------------------------------------ jobs
    def make_job(self, ctx, m, r, cap, what):
        if ctx.dry_run:
            return None
        desc = '%s %s' % (self.instrument_id, what)
        if r is not None and r.get('operation_id') and m.get('job', 'operation') == 'operation':
            return OperationJob(ctx.core, self, ctx.req, desc, cap, r['operation_id'], max_s=m.get('max_s'))
        st = m.get('state')
        if st:
            def state(c):
                v = self.slot_value(c, st['slot'], st.get('key'))
                fault = self.slot_value(c, st['slot'], st['fault_key']) if st.get('fault_key') else None
                if fault not in (None, ''):
                    return 'failed', '%s: %s' % (st['slot'], fault), {'state': v, 'fault': fault}
                if v in (st.get('done') or []):
                    return 'succeeded', '%s = %s' % (st['slot'], v), {'state': v}
                if v in (st.get('failed') or []):
                    return 'failed', '%s = %s' % (st['slot'], v), {'state': v}
                return 'running', '%s = %s' % (st['slot'], v), {'state': v}
            stop = None
            if self.vmap('stop'):
                def stop(c):
                    sm = self.vmap('stop')
                    self.run_calls(c, sm.get('calls') or [{'cap': sm['cap'], 'args': sm.get('args'), 'step': 'stop'}])
                    return 'stopped (%s)' % ', '.join(self.maps_to('stop'))
            return StateJob(ctx.core, self, ctx.req, desc, state, stop=stop, max_s=float(m.get('max_s', 7 * 86400)), poll_s=float(m.get('poll_s', 5)))
        un = m.get('until')
        if un:
            when = un.get('when')
            if when and not self._holds(ctx, when):
                return None

            def test(c):
                v = self.slot_value(c, un['slot'], un.get('key'))
                return v == un['value'], '%s = %s' % (un['slot'], json.dumps(v))
            return ConditionJob(ctx.core, self, ctx.req, desc, test, max_s=float(m.get('max_s', 86400)), poll_s=float(m.get('poll_s', 1)))
        return None

    def _holds(self, ctx, cond):
        v = self.slot_value(ctx, cond['slot'], cond.get('key'))
        if 'value' in cond:
            return v == cond['value']
        if 'min' in cond:
            return finite(v) and v >= cond['min']
        return bool(v)

    # ------------------------------------------------------------------ class view
    def summary(self, values):
        lines, js = [], {}
        labels = self.param('labels') or {}
        for role, label, kind in self.ROLES:
            spec = self.slot(role)
            if spec is None:
                continue
            label = labels.get(role, label)
            if kind in ('bool', 'text', 'plain'):
                sid, _, key = (spec if isinstance(spec, str) else spec['slot']).partition('#')
                get = values.v if hasattr(values, 'v') else values.get
                v = get(sid)
                if key and isinstance(v, dict):
                    v = v.get(key)
                if v is None or v == '':
                    continue
                js[role] = v
                lines.append('%s: %s' % (label, ('yes' if v else 'no') if kind == 'bool' else v if isinstance(v, str) else
                                         fmtq(v, 'plain') if finite(v) else short(v, 60)))
            else:
                v = self.slot_si(values, spec)
                if v is None:
                    continue
                js[role] = v
                lines.append('%s: %s' % (label, fmtq(v, kind)))
        return (lines or ['(nothing to show yet; see `chemlab read %s`)' % self.instrument_id]), js

    # ------------------------------------------------------------------ verb factories
    def setter_verb(self, name, summary, kind, argname, example, *, off=False, heat=False, targets=(), details=None,
                    preconditions=(), errors=(), reference=None):
        opts = {}
        if heat:
            opts['heat'] = Opt('also switch the heater on (members with a heater switch)', kind='flag')
        for t in targets:
            if t in (self.map.get('targets') or {}):
                vals = sorted(((self.map.get('targets') or {}).get(t) or {}).get('values') or {})
                opts[t] = target_opt(t, vals or None)
        return Verb(name, summary, self.h_setter, positionals=[Arg(argname, 'e.g. %s%s' % (units.example(kind), ' or off' if off else ''))],
                    options=opts, example=example, details=details, preconditions=list(preconditions) or ['value within the instrument range'],
                    errors=list(errors) or ['OUT_OF_RANGE (usage)', 'NOT_READY (refused)'], reference=reference)

    SETTER_KINDS = {}                 # verb -> quantity kind (filled by the class)

    def h_setter(self, ctx, p):
        name = ctx.verb.name
        m = self.vmap(name)
        kind = self.SETTER_KINDS[name]
        text = p.pos[0]
        if str(text).strip().lower() == 'off':
            off = m.get('off')
            if not off:
                ctx.fail(invalid('NO_OFF', '%s on %s takes a value, not off.' % (name, self.instrument_id),
                                 'give a value, e.g. `chemlab run %s %s %s`.' % (self.instrument_id, name, units.example(kind))))
            self.need(ctx, name + ':off')
            self.run_calls(ctx, off.get('calls') or [dict(off, step='%s off' % name)], fill=self.targets(ctx, p))
            return {'text': 'ok: %s %s off\n' % (self.instrument_id, name), 'json': {'off': True}}
        si = units.parse(text, kind, what=name)
        cap, arg = m['cap'], m['arg']
        props = (ctx.cap_spec(m['cap']).get('input_schema') or {}).get('properties') or {}
        tgt = {k: v for k, v in self.targets(ctx, p).items() if k in props}      # --channel etc. only where the capability takes it
        value = self.to_arg(ctx, cap, arg, si, kind, name, unit=m.get('unit'), range_from=m.get('range_from'))
        step = m.get('step_si')
        if isinstance(step, dict):
            step = step.get(str(next(iter(tgt.values()), '*')), step.get('*'))
        if step and abs(si / step - round(si / step)) > 1e-6:
            ctx.fail(invalid('NOT_ON_STEP', '%s %s is not a whole multiple of %s on %s.' % (name, units.fmt(si, kind), units.fmt(step, kind),
                                                                                            self.instrument_id),
                             'give a multiple of %s, e.g. %s.' % (units.fmt(step, kind), units.fmt(round(si / step) * step, kind))))
        if p.get('heat') and not (m.get('heat_arg') or m.get('heat_cap')):
            ctx.fail(invalid('NO_HEATER_SWITCH', '%s has no separate heater switch for --heat.' % self.instrument_id,
                             'leave out --heat (see `chemlab help %s %s`).' % (self.instrument_id, name)))
        self.need(ctx, name)
        if p.get('heat'):
            self.need(ctx, name + ':heat')
        args = dict(m.get('args') or {})
        if m.get('keep_from'):                                      # a method setting: the rest of the method is kept as it is
            cur = self.slot_value(ctx, m['keep_from']['slot'], m['keep_from'].get('key')) or {}
            props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
            for k in props:
                if k != arg and k not in (m.get('keep_skip') or []) and isinstance(cur, dict) and cur.get(k) is not None:
                    args[k] = cur[k]
        args.update(tgt)
        args[arg] = value
        if p.get('heat') and m.get('heat_arg'):
            args[m['heat_arg']] = True
        shown = fmtq(si, kind)
        ctx.call(cap, args, 'set %s to %s' % (name if name != 'set-temp' else 'the temperature', shown))
        if p.get('heat') and m.get('heat_cap'):
            self.run_calls(ctx, [{'cap': m['heat_cap'], 'args': m.get('heat_args') or {}, 'step': 'switch the heater on',
                                  'skip_if': m.get('heat_skip_if')}])
        out = {'set_si': si, 'unit_si': units.KINDS[kind].si, 'native': {arg: value}}
        return {'text': 'ok: %s %s %s%s\n' % (self.instrument_id, name, shown, ' (heater on)' if p.get('heat') else ''), 'json': out}

    # ------------------------------------------------------------------ method
    def method_verb(self, summary, example, details=None, reference=None):
        return Verb('method', summary, self.h_method, variadic=('KEY=VALUE', 'a method setting'), example=example, details=details,
                    preconditions=['every key known (class keys or native argument names)', 'values in range',
                                   'the method can still be changed (see the member notes)'],
                    errors=['UNKNOWN_SETTING (usage)', 'MISSING_SETTING (usage)', 'OUT_OF_RANGE (usage)', 'METHOD_LOCKED (refused)'],
                    reference=reference)

    def parse_value(self, ctx, text, schema, kind, what):
        """One KEY=VALUE value -> the native value (units converted into the declared unit, JSON for the rest)."""
        if text.strip().lower() in ('null', 'none') and ('null' in _types(schema) or any(
                isinstance(x, dict) and x.get('type') == 'null' for x in (schema or {}).get('anyOf') or [])):
            return None
        if kind == 'fractions':
            parts = [x for x in re.split(r'[,:/ ]+', text.strip()) if x]
            try:
                vals = [float(x[:-1]) / 100 if x.endswith('%') else float(x) for x in parts]
            except ValueError:
                ctx.fail(invalid('BAD_VALUE', '%s=%s is not a list of fractions.' % (what, text), 'write e.g. %s=1,0,0,0 or 50%%,50%%,0,0.' % what))
            n = (schema.get('minItems') or schema.get('maxItems')) if isinstance(schema, dict) else None
            if n and len(vals) != n:
                ctx.fail(invalid('BAD_VALUE', '%s needs %d fractions (got %d).' % (what, n, len(vals)), 'write %d numbers, e.g. %s.' % (n, ','.join(['1'] + ['0'] * (n - 1)))))
            if abs(sum(vals) - 1) > 1e-6:
                ctx.fail(invalid('BAD_VALUE', 'the fractions of %s add up to %g, not 1.' % (what, sum(vals)), 'make them add up to 1 (or 100%).'))
            return vals
        num = _numeric(schema)
        if num is not None:
            u = units.declared(schema)
            ukind = units.unit_kind(u) if u else None
            bare = re.fullmatch(r'\s*' + _NUM + r'\s*', text) is not None
            if kind in units.KINDS:
                value = units.parse_for(text, schema, kind, what=what) if ukind else units.parse(text, kind, what=what)
            elif ukind and not bare:
                value = units.parse_for(text, schema, what=what)
            elif bare or u:
                value = units.parse_for(text, None if bare else u, what=what)
            else:
                value = units.parse_for(text, None, what=what)
            return self.check_range(ctx, schema, value, ukind or 'plain', what)
        enum = (schema or {}).get('enum')
        if enum and text not in [str(x) for x in enum]:
            ctx.fail(invalid('BAD_CHOICE', '%s=%s is not allowed on %s.' % (what, text, self.instrument_id), 'use one of: %s.' % ', '.join(map(str, enum))))
        try:
            return coerce(text, schema, what)
        except CmdError as exc:
            if hasattr(exc, 'done'):
                raise
            ctx.fail(invalid(exc.code, exc.message, exc.fix))

    def h_method(self, ctx, p):
        m = self.vmap('method')
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'see `chemlab help %s method`.' % self.instrument_id))
        keys = m.get('keys') or {}
        dflt = m.get('cap')
        given = {}                                                     # cap -> {arg: value | {key: value}}
        order = [dflt] if dflt else []
        seen = set()
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write method KEY=VALUE ... (see `chemlab help %s method`).' % self.instrument_id))
            if k in seen:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % k, 'give each setting once.'))
            seen.add(k)
            if k in keys:
                spec = keys[k]
                cap, arg, key, kind = spec.get('cap', dflt), spec['arg'], spec.get('key'), spec.get('kind')
            elif m.get('passthrough') and dflt and k in ((ctx.cap_spec(dflt).get('input_schema') or {}).get('properties') or {}):
                cap, arg, key, kind = dflt, k, None, None
            else:
                native = sorted(((ctx.cap_spec(dflt).get('input_schema') or {}).get('properties') or {})) if (dflt and m.get('passthrough')) else []
                close = difflib.get_close_matches(k, list(keys) + native, 2)
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (self.instrument_id, k),
                                 ('did you mean %s? ' % ' or '.join(close) if close else '') + 'settings: %s.' % ', '.join(list(keys) + [n for n in native if n not in [s.get('arg') for s in keys.values()]])))
            schema = self.schema_of(cap, arg, key)
            if v.strip().lower() in ('on', 'yes') and 'boolean' in _types(schema):
                v = 'true'
            elif v.strip().lower() in ('off', 'no') and 'boolean' in _types(schema):
                v = 'false'
            value = self.parse_value(ctx, v, schema, kind, k)
            if cap not in order:
                order.append(cap)
            if key is not None:
                given.setdefault(cap, {}).setdefault(arg, {})[key] = value
            else:
                given.setdefault(cap, {})[arg] = value
        self.need(ctx, 'method')
        calls = []
        for cap in order:
            if cap not in given:
                continue
            spec = ctx.cap_spec(cap).get('input_schema') or {}
            args = dict((m.get('args') or {}) if cap == dflt else {})
            args.update(given[cap])
            missing = []
            keep = (m.get('keep') or {}).get(cap) or {}
            kw = m.get('keep_when')
            if kw and not self._holds(ctx, kw):
                keep = {}
            props = spec.get('properties') or {}
            for arg, kept in keep.items():                          # settings the agent left out keep their stored value
                if arg not in props:
                    continue
                old = self.slot_value(ctx, kept['slot'], kept.get('key')) if kept.get('slot') else None
                if arg in args and isinstance(args[arg], dict) and (props[arg] or {}).get('type') == 'object':
                    if isinstance(old, dict):
                        for k2, v2 in old.items():
                            args[arg].setdefault(k2, v2)
                elif arg not in args and old is not None:
                    args[arg] = old
            for req in spec.get('required') or []:
                sub = props.get(req) or {}
                if req in args and isinstance(args[req], dict) and sub.get('type') == 'object':
                    for sreq in sub.get('required') or []:
                        if sreq not in args[req]:
                            missing.append(self._key_for(keys, cap, req, sreq))
                elif req not in args:
                    missing.append(self._key_for(keys, cap, req))
            if missing:
                ctx.fail(invalid('MISSING_SETTING', 'the method of %s also needs %s (it has no stored value to keep).' % (
                    self.instrument_id, ', '.join(missing)), 'give them too, e.g. `chemlab run %s method %s` (see `chemlab help %s method`).' % (
                    self.instrument_id, ' '.join('%s=...' % x for x in missing), self.instrument_id)))
            calls.append((cap, args))
        for cap, args in calls:
            ctx.call(cap, args, 'set the method (%s: %s)' % (cap, short(args, 160)))
        return {'text': 'ok: method of %s set: %s\n' % (self.instrument_id, '; '.join('%s %s' % (c, short(a, 300)) for c, a in calls)),
                'json': {'calls': [{'capability': c, 'arguments': a} for c, a in calls]}}

    @staticmethod
    def _key_for(keys, cap, arg, key=None):
        for k, s in keys.items():
            if s.get('arg') == arg and s.get('key') == key and s.get('cap', cap) == cap:
                return k
        return '%s.%s' % (arg, key) if key else arg

    # ------------------------------------------------------------------ run set
    def run_verbs(self):
        t = {}
        return {'start': Verb('start', 'start the run (a long run returns job-N: chemlab status|wait|cancel job-N)', self.h_start,
                              long_action=True, options=dict(t), example='chemlab run INST start',
                              preconditions=['the member\'s own start conditions (power, loaded sample, lid / door closed, ...)'],
                              errors=['NOT_READY / POWER_OFF / NO_SAMPLE ... (refused)']),
                'stop': Verb('stop', 'stop the run (what was done so far is kept)', self.h_stop, example='chemlab run INST stop'),
                'result': Verb('result', 'the run record: readings and products so far', self.h_result, readonly=True,
                               options={'save-to': save_to_opt()}, example='chemlab run INST result --save-to run.json')}

    def h_start(self, ctx, p):
        m = self.vmap('start')
        self.need(ctx, 'start')
        calls = m.get('calls') or [{'cap': m['cap'], 'args': m.get('args'), 'step': 'start (%s)' % m['cap']}]
        r = self.run_calls(ctx, calls)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        cap = m.get('job_cap') or calls[-1]['cap']
        job = self.make_job(ctx, m, r, cap, 'run')
        if job is None:
            return {'text': 'ok: %s started (%s); it keeps running until `chemlab run %s stop`\n' % (
                self.instrument_id, ', '.join(c['cap'] for c in calls), self.instrument_id), 'json': {'job': None}}
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        self.need(ctx, 'stop')
        self.run_calls(ctx, m.get('calls') or [{'cap': m['cap'], 'args': m.get('args'), 'step': 'stop (%s)' % m['cap']}])
        return {'text': 'ok: %s stopped\n' % self.instrument_id, 'json': {}}

    def h_result(self, ctx, p):
        m = self.vmap('result')
        if m.get('cap'):
            r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the result (%s)' % m['cap'], query=True)
            res = r.get('result')
        else:
            res = {s: ctx.v(s) for s in m.get('reads') or []}
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        lines = ['%s result:' % self.instrument_id]
        if isinstance(res, dict):
            for k, v in list(res.items())[:40]:
                f = self._friendly_field(k, v)
                lines.append('  %s = %s%s' % (k, short(v, 160), ('  (= %s)' % f) if f else ''))
        else:
            lines.append('  %s' % short(res, 400))
        return {'text': '\n'.join(lines) + '\n', 'json': {'result': res}}

    def _friendly_field(self, name, v):
        if not finite(v):
            return None
        s = self.lab.contract['state_slots'].get(name)
        u = units.declared(s) if s else None
        if u is None:
            for suf, unit in (('_k', 'K'), ('_pa', 'Pa'), ('_m3_s', 'm^3/s'), ('_m3', 'm^3'), ('_kg', 'kg'), ('_rad_s', 'rad/s'),
                              ('_kg_s', 'kg/s'), ('_s', 's')):
                if name.endswith(suf):
                    u = unit
                    break
        return units.friendly(v, u) if u else None

    # ------------------------------------------------------------------ small helpers for class verbs
    def wait_until(self, ctx, test, max_s, what, poll_s=1.0):
        """Let lab time pass until ``test(view)`` holds (verbs that say they wait)."""
        if ctx.dry_run:
            ctx.advance(max_s, step=what)
            return 0.0
        if test(ctx.view):
            return 0.0
        waited = ctx.advance(max_s, until=test, step=what, poll_s=poll_s)
        if not test(ctx.view):
            ctx.fail(refused('NOT_REACHED', '%s did not finish %s within %s.' % (self.instrument_id, what, units.fmt_s(max_s)),
                             'check `chemlab run %s status`; wait longer with `chemlab wait`.' % self.instrument_id))
        return waited


def fmtq(si, kind):
    """units.fmt (fine vacuum keeps 3 significant digits there), and small plain numbers."""
    if kind in (None, 'plain'):
        return ('%.4g' % si) if finite(si) else str(si)
    return units.fmt(si, kind)


def frac_text(v):
    return '%.1f %%' % (100 * v) if finite(v) else str(v)


_ = (math, ON_OFF)

"""Common machinery of the property_meter and agitation classes: member mappings that are data, not code.

``StepAdapter`` extends ClassAdapter with

* quantities the unit table has no kind for (osmolality, NTU, water activity, nD, pH, conductance) - ``parse_quantity``;
* member requirements with comparisons (``"requires": {verb: [{"slot", "min" | "max" | "value" | "not" | "truthy" | "falsy"}]}``);
* calls whose capability needs every argument (centrifuge set_method, sonicator method.configure, KS 4000 timer.configure): the
  arguments the agent did not give are kept from the instrument's own state slots (``"keep": {ARG: {"from_slot": S, "key": K,
  "or_slot": S2, "or_key": K2, "default": D, "scale": F, "is_set": true}}``) - never invented; a required argument with no slot is a usage error that names it;
* step lists for composite verbs (``"steps": [{"cap", "args", "with": {ARG: "$token"}, "skip_if_slot": S}, {"wait": {"slot", "done",
  "failed"}, "max_s"}, {"report_slot": S}]``): a start, the wait for its run (lab time moves), then the accept / decide / quartz call;
* slot-based jobs (``"state": {"slot", "done", "failed"}`` -> jobs.StateJob, ``"until": {"slot", "value"}`` -> jobs.ConditionJob)
  that work whether or not the runtime answers operation polls.

Instrument facts stay in the fragments (configs/adapters/property_meter.json, agitation.json); this module holds no member data.
"""
import difflib
import math

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused
from ..jobs import ConditionJob, StateJob
from ...generic import coerce

# quantities outside units.KINDS: kind -> (base unit, {typed unit: factor to the base unit}, default typed unit, example)
EXTRA = {
    'osmolality': ('mol/kg', {'mosmol/kg': 1e-3, 'mosm/kg': 1e-3, 'mmol/kg': 1e-3, 'mol/kg': 1.0, 'osmol/kg': 1.0, 'osm/kg': 1.0},
                   'mosmol/kg', '400mOsmol/kg'),
    'ntu': ('NTU', {'ntu': 1.0, 'fnu': 1.0}, 'ntu', '20NTU'),
    'aw': ('1', {'aw': 1.0}, 'aw', '0.760'),
    'nd': ('1', {'nd': 1.0}, 'nd', '1.33299'),
    'ph': ('1', {'ph': 1.0}, 'ph', '6.865'),
    'conductance': ('S', {'s': 1.0, 'ms': 1e-3, 'us': 1e-6, 'µs': 1e-6}, 'ms', '1.413mS'),
    'plain': ('1', {}, '', '1.0'),
}
_NUM = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'
FAILED = ('failed', 'cancelled', 'canceled', 'aborted', 'error', 'fault', 'stopped')


def with_items(step):
    """A step's ``with`` as (argument, token) pairs: a dict, or a list of pairs when an argument is called ``slot`` (a dict key
    ``slot`` would be read as a state slot by the base mapping check)."""
    w = step.get('with') or {}
    return list(w.items()) if isinstance(w, dict) else [tuple(x) for x in w]


def kind_example(kind):
    if kind in EXTRA:
        return EXTRA[kind][3]
    return units.example(kind) if kind in units.KINDS else '1'


def parse_quantity(text, kind, what):
    """Agent text -> value in SI (units.KINDS) or in the EXTRA kind's base unit."""
    if kind in units.KINDS:
        return units.parse(text, kind, what=what)
    import re
    base, table, default, ex = EXTRA.get(kind, EXTRA['plain'])
    t = (text or '').strip().replace(' ', '')
    m = re.fullmatch(_NUM + r'(.*)', t)
    if not m:
        raise invalid('BAD_NUMBER', '%r is not a number%s.' % (text, ' with a unit' if table else ''),
                      'write the %s like %s.' % (what, ex))
    v = float(m.group(1))
    u = (m.group(2) or default).lower()
    if u and u not in table:
        raise invalid('UNIT_NOT_ALLOWED', '%r is not a unit for the %s.' % (m.group(2), what),
                      'write it like %s%s.' % (ex, (' (units: %s)' % ', '.join(sorted(table))) if table else ''))
    if not math.isfinite(v):
        raise invalid('BAD_NUMBER', '%r is not a finite number.' % text, 'write a plain decimal number.')
    return v * table.get(u, 1.0)


def fmt_value(value, unit, decimals=None):
    """A value in a declared unit -> friendly text."""
    if value is None:
        return 'n/a'
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return short(value, 80)
    k = units.unit_kind(unit) if unit else None
    if k:
        return units.fmt_declared(value, unit, decimals)
    if decimals is not None:
        return ('%.*f %s' % (decimals, value, unit or '')).strip()
    return ('%.6g %s' % (value, unit if unit not in (None, '1') else '')).strip()


class StepAdapter(ClassAdapter):
    """ClassAdapter + the step mapping vocabulary (see the module docstring)."""

    # ------------------------------------------------------------------ slots
    def sval(self, values, spec):
        """Raw value of 'slot' or 'slot#key' from a Ctx or a {slot: value} dict."""
        if spec is None:
            return None
        if isinstance(spec, dict):
            spec = spec.get('slot')
        sid, _, key = spec.partition('#')
        if hasattr(values, 'v'):
            v = values.v(sid)
        else:
            v = values.get(sid)
            if v is None:
                low = {k.lower(): k for k in values}
                v = values.get(low.get(sid.lower()))
        if key and isinstance(v, dict):
            v = v.get(key)
        return v

    def role(self, values, role):
        return self.sval(values, self.slot(role))

    # ------------------------------------------------------------------ requirements with comparisons
    def requirements(self, ctx, verb):
        for r in (self.map.get('requires') or {}).get(verb, []):
            got = self.sval(ctx, r['slot'])
            if 'value' in r:
                ok = got == r['value']
            elif 'not' in r:
                ok = got not in r['not']
            elif 'min' in r:
                ok = finite(got) and got >= r['min'] - 1e-12
            elif 'max' in r:
                ok = finite(got) and got <= r['max'] + 1e-12
            elif r.get('falsy'):
                ok = not got
            else:
                ok = bool(got)
            fill = {'inst': self.instrument_id, 'value': short(got, 60)}
            ctx.check(ok, r.get('code', 'NOT_READY'), r['message'].format(**fill), r['fix'].format(**fill), label=r.get('label', r['slot']))

    # ------------------------------------------------------------------ quantities and arguments
    def native(self, cap, arg, base_value, kind):
        """A parsed value (SI, or the EXTRA kind's base unit) -> the unit the argument declares."""
        if kind in units.KINDS:
            return self.arg_value(cap, arg, base_value)
        u = self.arg_unit(cap, arg)
        base = EXTRA.get(kind, EXTRA['plain'])[0]
        if u is None or u == base or kind not in EXTRA:
            return base_value
        f = EXTRA[kind][1].get(u.lower())
        return base_value / f if f else base_value

    def check_range(self, ctx, cap, arg, native_value, label, kind=None):
        """Range of a numeric argument from the contract (in its declared unit); a usage error outside it."""
        lo, hi = self.arg_range(cap, arg)
        u = self.arg_unit(cap, arg)
        if not finite(native_value):
            return
        if (lo is not None and native_value < lo - 1e-9 * max(1.0, abs(lo))) or (hi is not None and native_value > hi + 1e-9 * max(1.0, abs(hi))):
            ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of %s (%s to %s).' % (
                label, fmt_value(native_value, u), self.instrument_id, fmt_value(lo, u), fmt_value(hi, u)),
                'give a %s from %s to %s.' % (label, fmt_value(lo, u), fmt_value(hi, u))))
        ctx.checks.append('%s within %s..%s' % (label, fmt_value(lo, u), fmt_value(hi, u)))

    def keep_value(self, ctx, spec):
        """One kept argument from the instrument's state: {"from_slot", "key", "default", "scale", "is_set"}."""
        v = self.sval(ctx, spec['from_slot'] + ('#' + spec['key'] if spec.get('key') else ''))
        if v is None and spec.get('or_slot'):                         # e.g. the touchscreen set points before any method exists
            v = self.sval(ctx, spec['or_slot'] + ('#' + spec['or_key'] if spec.get('or_key') else ''))
        if spec.get('is_set'):
            return v is not None
        if v is None:
            return spec.get('default')
        if spec.get('scale') is not None and finite(v):
            v = v * spec['scale']
        if spec.get('int') and finite(v):
            v = int(round(v))
        return v

    def build_args(self, ctx, m, given, what):
        """Arguments of one call: fixed ``args``, then ``keep`` (from state slots, for arguments not given), then ``given``.
        A required argument still missing is a usage error naming the class key (``m["names"]``) that sets it."""
        cap = m['cap']
        args = dict(m.get('args') or {})
        for a, spec in (m.get('keep') or {}).items():
            if a in given:
                continue
            v = self.keep_value(ctx, spec)
            if v is not None:
                args[a] = v
        args.update(given)
        req = ((ctx.cap_spec(cap).get('input_schema') or {}).get('required') or [])
        missing = [a for a in req if a not in args]
        if missing:
            names = m.get('names') or {}
            ex = (ctx.cap_spec(cap).get('example_arguments') or {})
            ctx.fail(invalid('MISSING_SETTING', '%s on %s also needs %s (the instrument does not report a current value to keep).' % (
                what, self.instrument_id, ', '.join(names.get(a, a) for a in missing)),
                'give %s, e.g. %s (see `chemlab help %s %s`).' % (
                    ', '.join(names.get(a, a) for a in missing),
                    ' '.join('%s=%s' % (names.get(a, a), short(ex.get(a, '...'), 30)) for a in missing), self.instrument_id,
                    what.split()[0])))
        return args

    # ------------------------------------------------------------------ waiting on a run in state slots
    def run_state(self, values, st):
        v = self.sval(values, st['slot'])
        if 'value' in st:
            return ('succeeded' if v == st['value'] else 'running'), v
        if v in (st.get('done') or []):
            return 'succeeded', v
        if v in (st.get('failed') or FAILED):
            return 'failed', v
        if st.get('running') and v not in st['running']:
            return 'failed', v
        return 'running', v

    def wait_run(self, ctx, st, max_s, what):
        """Move lab time until the run in ``st`` ends; refuse when it failed or did not end within ``max_s``."""
        if ctx.dry_run:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': max_s}, 'step': 'wait until %s ends (up to %s of lab time)' % (
                what, units.fmt_s(max_s))})
            return None
        t0 = ctx.now()
        status, v = self.run_state(ctx, st)
        if status == 'running':
            ctx.advance(max_s, until=lambda view: self.run_state(ctx, st)[0] != 'running', step='wait until %s ends' % what,
                        poll_s=float(st.get('poll_s', self.param('poll_s', 1.0))))
            status, v = self.run_state(ctx, st)
        if status == 'failed':
            ctx.fail(refused('RUN_FAILED', '%s on %s ended as %s=%s after %s.' % (what, self.instrument_id, st['slot'], short(v, 40),
                                                                                  units.fmt_s(ctx.now() - t0)),
                             'read the reason with `chemlab run %s status` / `chemlab read %s`, fix it and run again.' % (
                                 self.instrument_id, self.instrument_id), changed='yes'))
        if status != 'succeeded':
            ctx.fail(refused('NOT_FINISHED', '%s on %s is still running after %s (%s=%s).' % (
                what, self.instrument_id, units.fmt_s(ctx.now() - t0), st['slot'], short(v, 40)),
                'wait longer (--max), or check it with `chemlab run %s status`.' % self.instrument_id, changed='yes'))
        return ctx.now() - t0

    def state_job(self, ctx, m, description):
        """A job for a run whose progress is in state slots (``"state"``) or a condition (``"until"``)."""
        ad = self
        if m.get('until'):
            st = m['until']

            def test(c):
                status, v = ad.run_state(c, st)
                return status == 'succeeded', '%s = %s' % (st['slot'], short(v, 40))
            return ConditionJob(ctx.core, self, ctx.req, description, test, max_s=float(m.get('max_s', 3600)),
                                poll_s=float(st.get('poll_s', 1.0)))
        st = m['state']

        def state(c):
            status, v = ad.run_state(c, st)
            return status, '%s = %s' % (st['slot'], short(v, 40)), {'state': v}
        stop = None
        if m.get('stop_cap'):
            def stop(c):
                c.call(m['stop_cap'], dict(m.get('stop_args') or {}), 'stop')
                return 'stopped with %s' % m['stop_cap']
        return StateJob(ctx.core, self, ctx.req, description, state, stop=stop, max_s=float(m.get('max_s', 7200)),
                        poll_s=float(st.get('poll_s', 1.0)))

    def follow_job(self, ctx, job, max_s):
        """Block on a job (lab time moves) - the --wait form of a long verb."""
        r = job.wait_reply(ctx.core, ctx.req, max_s)
        ctx.waited_s += r['json'].get('waited_s') or 0.0
        ctx.refresh()
        return r

    # ------------------------------------------------------------------ composite steps
    def run_steps(self, ctx, steps, tokens, what, max_s):
        """Run a member's step list.  Returns the results of the calls (cap -> result) and the reports of report steps."""
        results, reports = [], []
        for st in steps:
            if 'wait' in st:
                self.wait_run(ctx, st['wait'], float(st.get('max_s', max_s)), st.get('step') or what)
            elif 'cap' in st:
                if st.get('skip_if_slot') and self.sval(ctx, st['skip_if_slot']):
                    continue                                              # e.g. the lamp is already on
                args = dict(st.get('args') or {})
                for a, tok in with_items(st):
                    args[a] = self.token(ctx, tok, st['cap'], a, tokens)
                r = ctx.call(st['cap'], args, st.get('step') or '%s (%s)' % (what, st['cap']), query=bool(st.get('query')))
                results.append((st['cap'], r.get('result')))
            elif 'report_slot' in st:
                v = self.sval(ctx, st['report_slot'])
                if st.get('last') and isinstance(v, list):
                    v = v[-1] if v else None
                if st.get('key') and isinstance(v, dict):
                    v = v.get(st['key'])
                reports.append((st['report_slot'], v))
        return results, reports

    def token(self, ctx, tok, cap, arg, tokens):
        """$standard / $reference / $volume (parsed by the verb), $name, $set_point, $probe, $source, and class tokens."""
        if not isinstance(tok, str) or not tok.startswith('$'):
            return tok
        name = tok[1:]
        if name in ('standard', 'reference', 'volume'):
            if name not in tokens:
                ctx.fail(invalid('MISSING_%s' % name.upper(), '%s needs --%s on %s.' % (ctx.verb.name, name, self.instrument_id),
                                 'give --%s VALUE (see `chemlab help %s %s`).' % (name, self.instrument_id, ctx.verb.name)))
            value, kind = tokens[name]
            v = self.native(cap, arg, value, kind)
            self.check_range(ctx, cap, arg, v, '--%s' % name)
            return v
        if name == 'name':
            return tokens.get('name')
        if name == 'set_point':
            k = self.slot_si(ctx, self.slot('set'))
            if not finite(k):
                ctx.fail(refused('NO_SET_POINT', '%s reports no temperature set point.' % self.instrument_id, 'set one with `set-temp`.'))
            return self.arg_value(cap, arg, k)
        if name == 'probe':
            return self.sval(ctx, self.slot('probe'))
        if name == 'source':
            return 'chemlab %s on %s (standard %s)' % (ctx.verb.name, self.instrument_id, ctx.args.raw.get('standard', '?'))
        return self.class_token(ctx, name, cap, arg, tokens)

    def class_token(self, ctx, name, cap, arg, tokens):
        raise KeyError('unknown token $%s' % name)

    # ------------------------------------------------------------------ names (checks, test kinds)
    def resolve_name(self, ctx, spec, given):
        """A NAME argument: from a fixed choice table ("choices": {name: native}) or a list slot ("list_slot", "field")."""
        if given is None:
            if spec.get('default') is not None:
                given = spec['default']
            elif spec.get('required'):
                ctx.fail(invalid('MISSING_NAME', '%s on %s needs a NAME.' % (ctx.verb.name, self.instrument_id),
                                 'names: %s.' % ', '.join(self.name_choices(ctx, spec)) or 'see `chemlab help %s %s`.' % (self.instrument_id, ctx.verb.name)))
            else:
                return None
        choices = self.name_choices(ctx, spec)
        table = spec.get('choices') or {}
        if table:
            if given not in table:
                close = difflib.get_close_matches(given, list(table), 2)
                ctx.fail(invalid('UNKNOWN_NAME', '%s on %s has no %r.' % (ctx.verb.name, self.instrument_id, given),
                                 ('did you mean %s? ' % ' or '.join(close) if close else '') + 'one of: %s.' % ', '.join(table)))
            return table[given]
        if choices and given not in choices:
            close = difflib.get_close_matches(given, choices, 2)
            ctx.fail(invalid('UNKNOWN_NAME', '%s has no %s named %r.' % (self.instrument_id, spec.get('what', 'item'), given),
                             ('did you mean %s? ' % ' or '.join(close) if close else '') + 'names: %s.' % ', '.join(choices)))
        return given

    def name_choices(self, ctx, spec):
        if spec.get('choices'):
            return list(spec['choices'])
        if spec.get('list_slot'):
            v = self.sval(ctx, spec['list_slot']) or []
            return [x.get(spec.get('field', 'name')) for x in v if isinstance(x, dict)]
        return []

    # ------------------------------------------------------------------ the mapping check, extended
    def check_mapping(self, lab):
        problems = ClassAdapter.check_mapping(self, lab)
        caps = lab.contract['capabilities']
        visible = {s.lower() for s in lab.view.slots}

        def props(cap):
            return ((caps.get(cap) or {}).get('input_schema') or {}).get('properties') or {}

        def walk(o, where):
            if isinstance(o, dict):
                cap = o.get('cap')
                if isinstance(cap, str):
                    for key in ('with', 'keep'):
                        for a in ([x[0] for x in with_items(o)] if key == 'with' else (o.get(key) or {})):
                            if a not in props(cap):
                                problems.append('%s.%s: %s has no argument %s' % (where, key, cap, a))
                    for a, spec in (o.get('keep') or {}).items():
                        s = (spec or {}).get('from_slot') if isinstance(spec, dict) else None
                        if s and s.lower() not in visible:
                            problems.append('%s.keep.%s: no visible state slot %s' % (where, a, s))
                for k, x in o.items():
                    if k == 'report_slot' and isinstance(x, str) and x.split('#', 1)[0].lower() not in visible:
                        problems.append('%s: no visible state slot %s' % (where, x))
                    if isinstance(x, (dict, list)):
                        walk(x, '%s.%s' % (where, k))
            elif isinstance(o, list):
                for x in o:
                    walk(x, where)
        walk(self.map.get('verbs') or {}, 'verbs')
        return problems

    # ------------------------------------------------------------------ value of one argument typed as KEY=VALUE
    def parse_setting(self, ctx, cap, arg, text, spec, key):
        schema = ((ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}).get(arg) or {}
        kind = spec.get('kind')
        if kind and kind not in units.KINDS:
            v = self.native(cap, arg, parse_quantity(text, kind, key), kind)
        elif kind and not (units.declared(schema) and units.unit_kind(units.declared(schema))):
            v = units.parse(text, kind, what=key)                         # a unitless argument with a typed kind (fraction: 0.1%)
        elif kind or (units.declared(schema) and units.unit_kind(units.declared(schema))):
            v = units.parse_for(text, schema, kind, what=key)
        elif spec.get('bool'):
            t = text.strip().lower()
            if t not in ('on', 'off', 'true', 'false', 'yes', 'no', '1', '0'):
                ctx.fail(invalid('BAD_VALUE', '%s=%s is not on or off.' % (key, text), 'write %s=on or %s=off.' % (key, key)))
            v = t in ('on', 'true', 'yes', '1')
        else:
            v = coerce(text, schema, key)
            if isinstance(v, str) and schema.get('type') in ('number', 'integer'):
                v = units.parse_for(text, schema, what=key)
        if schema.get('type') == 'integer' and finite(v) and float(v).is_integer():
            v = int(v)
        enum = schema.get('enum')
        if enum and v not in enum:
            ctx.fail(invalid('BAD_CHOICE', '%s=%s is not allowed on %s.' % (key, text, self.instrument_id),
                             'one of: %s.' % ', '.join(str(e) for e in enum)))
        if finite(v) and not isinstance(v, bool):
            self.check_range(ctx, cap, arg, v, key)
        return v

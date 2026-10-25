"""Helpers shared by the process classes digestion and coating: instruments that run a programme on a loaded
sample - a method with class keys and units, a start that gives a job, stop, a result record, and short mechanical waits.

ProcessAdapter (a ClassAdapter) adds:

* ``need(ctx, verb)``: the member's own preconditions from the mapping's "requires", with more tests than the base
  ("value", "not", "in", "max", "min"); message / fix may use {inst} and {value};
* ``kv(ctx, p)``: KEY=VALUE pairs of a method verb (each key once);
* ``parse_key(ctx, text, schema, kind, what)``: one value into the unit the native argument declares (integers stay integers);
* ``check_args(ctx, cap, args)``: every numeric argument (and array item) inside the contract range, as a usage error with
  friendly units before anything is sent;
* ``program(ctx, text, kind)``: a step list ``VALUE@TIME,VALUE@TIME,...`` (e.g. ``95C@10min,95C@30min`` or ``500rpm@5s``);
* ``example(cap)``: the contract's documented example arguments (defaults for method settings the agent leaves out);
* ``start_job(ctx, r, cap, m)``: an OperationJob (operation_id) or the member's state job;
* ``wait_op(ctx, r, cap, what, max_s)``: let lab time pass until a short operation (a stage move, a homing) has ended;
* ``settle(ctx, test, what, max_s)``: let lab time pass until a slot test holds (a lid or a gap still moving);
* ``h_start / h_stop / h_result`` and a summary built from slot roles.
"""
import copy
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid
from ..jobs import RUNNING, OperationJob, StateJob

_NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


def _types(schema):
    t = (schema or {}).get('type')
    return set(t if isinstance(t, list) else [t] if t else [])


class ProcessAdapter(ClassAdapter):
    KEYS = {}                         # class dictionary: key -> (kind or None, help)
    ALIASES = {}                      # other spellings of a key -> the key

    # ------------------------------------------------------------------ member preconditions
    def need(self, ctx, verb, only=None):
        for r in (self.map.get('requires') or {}).get(verb, []):
            if only is not None and r.get('code') not in only:
                continue
            got = self.slot_value(ctx, r['slot'])
            if 'value' in r:
                ok = got == r['value']
            elif 'in' in r:
                ok = got in r['in']
            elif 'not' in r:
                ok = got not in r['not']
            else:
                ok = finite(got) and ('max' not in r or got <= r['max'] + 1e-12) and ('min' not in r or got >= r['min'] - 1e-12)
            shown = units.fmt_s(got) if r.get('show') == 'duration' and finite(got) else short(got, 40)
            ctx.check(ok, r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=shown),
                      r['fix'].format(inst=self.instrument_id, value=shown), label=r.get('label', r['slot']))

    def slot_value(self, ctx, spec):
        sid, _, key = spec.partition('#')
        v = ctx.v(sid)
        if key and isinstance(v, dict):
            v = v.get(key)
        return v

    def role(self, values, role, default=None):
        s = self.slot(role)
        if not s:
            return default
        sid, _, key = s.partition('#') if isinstance(s, str) else (s.get('slot'), None, None)
        v = values.v(sid) if hasattr(values, 'v') else values.get(sid)
        if key and isinstance(v, dict):
            v = v.get(key)
        return v

    # ------------------------------------------------------------------ values
    def kv(self, ctx, p, verb='method'):
        out = {}
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', '%s needs at least one KEY=VALUE.' % verb, 'see `chemlab help %s %s`.' % (self.instrument_id, verb)))
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write %s KEY=VALUE ... (see `chemlab help %s %s`).' % (verb, self.instrument_id, verb)))
            k = self.ALIASES.get(k, k)
            if k in out:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % k, 'give each setting once.'))
            out[k] = v
        return out

    def parse_key(self, ctx, text, schema, kind=None, what=None):
        """Agent text -> the value in the unit the argument declares; integers stay integers; booleans and text pass."""
        t = _types(schema)
        if 'boolean' in t:
            low = text.strip().lower()
            if low in ('true', 'on', 'yes', '1'):
                return True
            if low in ('false', 'off', 'no', '0'):
                return False
            ctx.fail(invalid('BAD_VALUE', '%s=%s is not on/off.' % (what, text), 'write %s=on or %s=off.' % (what, what)))
        if t == {'string'}:
            enum = schema.get('enum')
            if enum and text not in enum:
                ctx.fail(invalid('BAD_CHOICE', '%s=%s is not allowed.' % (what, text), 'use one of: %s.' % ', '.join(map(str, enum))))
            return text
        if t & {'number', 'integer'}:
            u = units.declared(schema)
            if u and units.unit_kind(u):
                v = units.parse_for(text, schema, 'temperature_delta' if kind == 'temperature_delta' else None, what=what)
            else:
                v = self._number(ctx, text, what)
            if 'integer' in t and 'number' not in t:
                if abs(v - round(v)) > 1e-9:
                    ctx.fail(invalid('NOT_WHOLE', '%s=%s must be a whole number.' % (what, text), 'give a whole number.'))
                v = int(round(v))
            return v
        return text

    def _number(self, ctx, text, what):
        m = re.fullmatch(_NUM, (text or '').strip())
        if not m:
            ctx.fail(invalid('BAD_NUMBER', '%s=%s is not a number.' % (what, text), 'write a plain number.'))
        return float(text)

    def program(self, ctx, text, kind, what='program'):
        """'95C@10min,95C@30min' -> [(value_si, seconds), ...]."""
        steps = []
        for part in [x for x in text.split(',') if x.strip()]:
            v, at, t = part.partition('@')
            if not at:
                ctx.fail(invalid('BAD_PROGRAM', '%s step %r is not VALUE@TIME.' % (what, part),
                                 'write steps like %s (each: value @ time).' % ('95C@10min,95C@30min' if kind == 'temperature' else '500rpm@5s,3000rpm@30s')))
            steps.append((units.parse(v.strip(), kind, what=what), units.parse(t.strip(), 'duration', what=what)))
        if not steps:
            ctx.fail(invalid('BAD_PROGRAM', '%s is empty.' % what, 'give at least one step VALUE@TIME.'))
        return steps

    @staticmethod
    def fmt_val(v, u):
        """A value in a declared unit as friendly text (small pressures in Pa, short times in µs / ms)."""
        if not (u and units.unit_kind(u) and finite(v)):
            return short(v, 40)
        si = units.from_declared(v, u)
        kind = units.unit_kind(u)
        if kind == 'pressure' and abs(si) < 100:
            return '%g Pa' % si
        if kind == 'duration' and 0 < abs(si) < 0.01:
            return '%g µs' % round(si * 1e6, 6) if abs(si) < 1e-3 else '%g ms' % round(si * 1e3, 6)
        return units.fmt_declared(v, u)

    def example(self, cap):
        return copy.deepcopy((self.lab.contract['capabilities'].get(cap) or {}).get('example_arguments') or {})

    def check_args(self, ctx, cap, args, label=None):
        """Numeric arguments (and array items) inside the contract range: a usage error with friendly units, nothing sent."""
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}

        def one(name, v, s, where):
            if not finite(v) or not isinstance(s, dict):
                return
            lo, hi = s.get('minimum'), s.get('maximum')
            if (lo is not None and v < lo - 1e-12 * max(1, abs(lo))) or (hi is not None and v > hi + 1e-12 * max(1, abs(hi))):
                u = units.declared(s)
                f = (lambda x: units.fmt_declared(x, u)) if u and units.unit_kind(u) else (lambda x: '%g' % x)
                what = (label or {}).get(name, name)
                ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of %s (%s to %s).' % (what + where, f(v), self.instrument_id,
                                                                                                   f(lo) if lo is not None else '-',
                                                                                                   f(hi) if hi is not None else '-'),
                                 'give a value from %s to %s.' % (f(lo) if lo is not None else '-', f(hi) if hi is not None else '-')))
        for name, v in args.items():
            s = props.get(name) or {}
            if isinstance(v, list):
                items = s.get('items') or {}
                for i, x in enumerate(v):
                    if isinstance(x, dict):
                        for k2, y in x.items():
                            one(k2, y, (items.get('properties') or {}).get(k2), ' (step %d)' % (i + 1))
                    else:
                        one(name, x, items, ' (item %d)' % (i + 1))
                n = len(v)
                if s.get('minItems') is not None and n < s['minItems'] or s.get('maxItems') is not None and n > s['maxItems']:
                    ctx.fail(invalid('OUT_OF_RANGE', '%s takes %s to %s items on %s, not %d.' % ((label or {}).get(name, name), s.get('minItems', 0),
                                                                                                s.get('maxItems', '-'), self.instrument_id, n),
                                     'give between %s and %s items.' % (s.get('minItems', 0), s.get('maxItems', '-'))))
            else:
                one(name, v, s, '')
        word = 'values within the %s ranges' % cap
        if word not in ctx.checks:
            ctx.checks.append(word)

    # ------------------------------------------------------------------ waiting on short actions
    def op_status(self, ctx, op):
        from ...generic import GenericCore
        r = ctx.core.backend.poll(ctx.caller, self.instrument_id, op)
        return None if r is None else GenericCore._op_status(r)

    def wait_op(self, ctx, r, what, max_s, done=None):
        """After an operation started (r has operation_id): advance lab time until it ends (or ``done(view)``). Returns status."""
        if ctx.dry_run:
            ctx.advance(max_s, step='wait until %s' % what)
            return 'succeeded'
        op = r.get('operation_id')
        state = {'st': 'running'}

        def ended(view):
            if done is not None and done(view):
                state['st'] = 'succeeded'
                return True
            if op:
                st = self.op_status(ctx, op)
                state['st'] = st or 'running'
                return st is not None and st not in RUNNING
            return False
        if not ended(ctx.view):
            ctx.advance(max_s, until=ended, step='wait until %s' % what, poll_s=float(self.param('poll_s', 0.5)))
        return state['st']

    def settle(self, ctx, test, what, max_s=30.0):
        """Advance lab time (at most max_s) until test(view) holds; True when it does."""
        if ctx.dry_run:
            ctx.advance(max_s, step='wait until %s' % what)
            return True
        if test(ctx.view):
            return True
        ctx.advance(max_s, until=test, step='wait until %s' % what, poll_s=0.25)
        return bool(test(ctx.view))

    # ------------------------------------------------------------------ run set
    def state_job(self, ctx, m, cap):
        """A member whose run lives in slots ("job": "state", "state": {"slot", "done", "failed", "running"})."""
        st = m['state']
        ad = self

        def state(c):
            v = c.v(st['slot'])
            if v in (st.get('done') or []):
                return 'succeeded', '%s = %s' % (st['slot'], v), {'state': v}
            if v in (st.get('failed') or []):
                return 'failed', '%s = %s' % (st['slot'], v), {'state': v}
            return ('running' if v in (st.get('running') or [v]) else 'failed'), '%s = %s' % (st['slot'], v), {'state': v}
        sm = ad.vmap('stop') or {}

        def stop(c):
            c.call(sm['cap'], dict(sm.get('args') or {}), 'stop')
            return 'stopped with %s' % sm['cap']
        return StateJob(ctx.core, self, ctx.req, '%s %s' % (self.instrument_id, cap), state, stop=stop if sm.get('cap') else None,
                        max_s=float(m.get('max_s', 7200)))

    def start_job(self, ctx, r, cap, m):
        if m.get('job') == 'state':
            return self.state_job(ctx, m, cap)
        if r.get('operation_id'):
            return OperationJob(ctx.core, self, ctx.req, '%s %s' % (self.instrument_id, cap), cap, r['operation_id'],
                                max_s=m.get('max_s'))
        return None

    def h_start(self, ctx, p):
        m = self.vmap('start')
        self.start_checks(ctx, p)
        self.need(ctx, 'start')
        args = dict(m.get('args') or {})
        args.update(self.start_args(ctx, p, m))
        r = ctx.call(m['cap'], args, 'start the %s (%s)' % (self.param('run_word', 'run'), m['cap']))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.start_job(ctx, r, m['cap'], m)
        if job is None:
            return {'text': 'ok: %s started\n' % self.instrument_id, 'json': {'status': r.get('status')}}
        return {'text': 'started %s: %s %s\nfollow it: chemlab status %s | chemlab wait %s [--max 2h] | chemlab cancel %s\n' % (
            job.handle, self.instrument_id, self.param('run_word', 'run'), job.handle, job.handle, job.handle),
            'json': {'job': job.handle, 'kind': job.kind}}

    def start_checks(self, ctx, p):
        """Class checks before start (subclasses)."""

    def start_args(self, ctx, p, m):
        return {}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        ph = self.role(ctx, 'phase')
        running = self.param('running_phases', ['running'])
        if self.slot('phase'):
            ctx.check(ph in running, 'NOT_RUNNING', '%s is not running (%s).' % (self.instrument_id, ph),
                      'nothing to stop; see `chemlab run %s status`.' % self.instrument_id, label='running')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop the %s' % self.param('run_word', 'run'))
        return {'text': 'ok: %s stopped (%s)\n' % (self.instrument_id, self.role(ctx, 'phase')), 'json': {'phase': self.role(ctx, 'phase')}}

    def result_args(self, ctx, p, m):
        return dict(m.get('args') or {})

    def h_result(self, ctx, p):
        m = self.vmap('result')
        args = self.result_args(ctx, p, m)
        r = ctx.call(m['cap'], args, 'read the record (%s)' % m['cap'], query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') or {}
        lines = ['%s %s:' % (self.instrument_id, self.param('result_word', 'record'))]
        for k, v in list(res.items())[:int(self.param('result_lines', 14))]:
            lines.append('  %s = %s' % (k, self.show(k, v)))
        if len(res) > int(self.param('result_lines', 14)):
            lines.append('  ... (%d more fields; --json or --save-to FILE for all)' % (len(res) - int(self.param('result_lines', 14))))
        return {'text': '\n'.join(lines) + '\n', 'json': {'result': res}}

    SHOW = (('_m3_kg', None), ('_kg_m3', None), ('_m3_s', 'flow'), ('_rad_s', 'rotation'), ('_m_s', 'speed'), ('_k', 'temperature'),
            ('_K', 'temperature'), ('_s', 'duration'), ('_m3', 'volume'), ('_kg', 'mass'), ('_m', 'length'), ('_pa', 'pressure'),
            ('_w', 'power'), ('_W', 'power'), ('_a', 'current'))

    def show(self, key, v):
        if finite(v):
            for suf, kind in self.SHOW:
                if key.endswith(suf):
                    if kind == 'pressure' and abs(v) < 100:
                        return '%g Pa' % v
                    return units.fmt(v, kind) if kind else short(v, 40)
        return short(v, 140)

    # ------------------------------------------------------------------ summary
    def summary(self, values):
        get = (lambda r: self.role(values, r))
        p = self.slot('power')
        if p and get('power') is False:
            return ['power off'], {'power': False}
        parts = []
        ph, stage = get('phase'), get('stage')
        if ph is not None:
            parts.append('%s%s' % (ph, (' / %s' % stage) if stage not in (None, '', ph) else ''))
        t, s = self.slot_si(values, self.slot('temp')), self.slot_si(values, self.slot('set'))
        if t is not None or s is not None:
            parts.append('%s (set %s)' % (units.fmt(t, 'temperature') if t is not None else 'n/a', units.fmt(s, 'temperature') if s is not None else 'n/a'))
        line2 = []
        for role, word in self.SUMMARY_FLAGS:
            v = get(role)
            if v is not None:
                line2.append('%s %s' % (word, self.flag_word(role, v)))
        el = get('elapsed')
        if finite(el):
            line2.append('elapsed %s' % units.fmt_s(el))
        rem = get('remaining')
        if finite(rem):
            line2.append('remaining %s' % units.fmt_s(rem))
        js = {'power': True if p is None else get('power'), 'phase': ph, 'stage': stage, 'temperature_k': t, 'set_point_k': s}
        for role, _ in self.SUMMARY_FLAGS:
            js[role] = get(role)
        return ['; '.join(parts) or '(see `chemlab read %s`)' % self.instrument_id] + (['; '.join(line2)] if line2 else []), js

    SUMMARY_FLAGS = ()

    def flag_word(self, role, v):
        if isinstance(v, bool):
            return {True: 'yes', False: 'no'}[v]
        if finite(v):
            return '%g' % v
        return short(v, 40)


def positions(ctx, text, n, inst, what='position'):
    """'1,3,5' or '1-4' or 'all' -> sorted list of 1-based positions within 1..n."""
    out = set()
    t = (text or '').strip().lower()
    if t == 'all':
        return list(range(1, n + 1))
    for part in [x for x in t.split(',') if x.strip()]:
        a, dash, b = part.partition('-')
        try:
            lo = int(a)
            hi = int(b) if dash else lo
        except ValueError:
            ctx.fail(invalid('BAD_POSITION', '%r is not a %s list.' % (text, what), 'write positions like 1,3,5 or 1-4 (1 to %d).' % n))
        if lo > hi:
            lo, hi = hi, lo
        out.update(range(lo, hi + 1))
    bad = [x for x in sorted(out) if not 1 <= x <= n]
    if not out or bad:
        ctx.fail(invalid('BAD_POSITION', '%s has %ss 1 to %d%s.' % (inst, what, n, (', not %s' % ','.join(map(str, bad))) if bad else ''),
                         'give %ss from 1 to %d, e.g. 1,3,5 or 1-4.' % (what, n)))
    return sorted(out)


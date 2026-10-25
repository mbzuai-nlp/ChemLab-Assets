"""Helpers shared by the bioassay and labware_automation classes: instruments whose class verbs are short
sequences of native calls with mechanical waits in between (open the drawer, wait until it is out, load the plate, close it), runs
that return a job, and KEY=VALUE settings with units.

StepAdapter (a ClassAdapter) adds, with every instrument fact kept in the fragment:

* ``sv(values, spec)``: a slot value, ``slot#key`` for a key of an object slot;
* ``cond(values, c)``: a condition on a slot: {"slot", "value" | "not" | "in" | "min" | "max" | "truthy" | "falsy" | "null" | "set"};
* ``need(ctx, verb)``: the member's own preconditions ("requires": {verb: [{condition + "code", "message", "fix"}]});
* ``run_steps(ctx, steps, env)``: a step list from the fragment, run in order:
      {"cap": C, "args": {fixed}, "with": {ARG: "$name"}, "when": condition(s), "step": "plain text"}    one native call
      {"wait": condition, "max_s": S, "step": "...", "code"/"message"/"fix"}                                let lab time pass
      {"check": condition, "code", "message", "fix"}                                                         a state check
  ``$name`` takes a value from ``env`` (SI numbers are converted into the unit the argument declares);
* ``pairs(ctx, p)`` / ``settings(ctx, cap, pairs, keys)``: KEY=VALUE settings: class keys with units (``"keys": {"temp":
  {"arg": "temperature_k", "kind": "temperature"}}``), native argument names (passthrough; a bare number takes the declared unit),
  ``KEY.SUB=VALUE`` for an object argument, comma lists for arrays, JSON for anything else and ``@FILE`` to read a JSON value
  from a file in the caller's work dir;
* ``check_args(ctx, cap, args)``: numbers inside the contract ranges before anything is sent (a usage error, friendly units);
* ``start_job(ctx, m, r, cap, what)``: a job for a long action (the runtime's operation, or a state slot);
* ``wait_until(ctx, test, max_s, step, fail)``: let lab time pass until a state test holds.

Never copies ranges or units into the fragment: they come from the contract.
"""
import copy
import difflib
import json
import math
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused
from ..jobs import RUNNING, OperationJob, StateJob

_NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
def _types(schema):
    s = schema or {}
    out = set()
    for x in ([s] + [y for y in s.get('anyOf') or [] if isinstance(y, dict)]):
        t = x.get('type')
        out |= set(t if isinstance(t, list) else [t] if t else [])
    return out


def _num_schema(schema):
    s = schema or {}
    if 'anyOf' in s:
        return next((x for x in s['anyOf'] if isinstance(x, dict) and x.get('type') in ('number', 'integer')), s)
    return s


def plain(v):
    return v if isinstance(v, str) else short(v, 40)


def parse_si(text, kind, what):
    """Agent text -> SI (units.parse: µM, nM, pM, µcal/s, µW are framework units); 'rotation_hz': a speed typed in rpm (or Hz)
    given back in revolutions per second (ITC stirrer)."""
    t = (text or '').strip().replace(' ', '')
    if kind == 'rotation_hz':
        return units.parse(t, 'rotation', what=what) / (2 * math.pi)
    return units.parse(t, kind, what=what)


class StepAdapter(ClassAdapter):
    KEY_HELP = {}                      # class key -> help (documented in help INST method)

    # ------------------------------------------------------------------ slots and conditions
    def sv(self, values, spec, default=None):
        if spec is None:
            return default
        sid, _, key = spec.partition('#')
        v = values.v(sid) if hasattr(values, 'v') else values.get(sid)
        if key and isinstance(v, dict):
            v = v.get(key)
        elif key:
            v = None
        return default if v is None else v

    def cond(self, values, c):
        if c is None:
            return True
        if isinstance(c, list):
            return all(self.cond(values, x) for x in c)
        if 'any' in c:
            return any(self.cond(values, x) for x in c['any'])
        v = self.sv(values, c['slot'] + ('#' + c['key'] if c.get('key') else ''))
        if 'value' in c:
            return v == c['value']
        if 'in' in c:
            return v in c['in']
        if 'not' in c:
            return v not in c['not']
        if 'truthy' in c:
            return bool(v) == bool(c['truthy'])
        if 'null' in c:
            return (v is None) == bool(c['null'])
        if 'min' in c or 'max' in c:
            return finite(v) and ('min' not in c or v >= c['min'] - 1e-12) and ('max' not in c or v <= c['max'] + 1e-12)
        return v is not None

    def need(self, ctx, verb, only=None):
        for r in (self.map.get('requires') or {}).get(verb, []):
            if only is not None and r.get('code') not in only:
                continue
            got = self.sv(ctx, r['slot'] + ('#' + r['key'] if r.get('key') else ''))
            ctx.check(self.cond(ctx, r), r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=plain(got)),
                      r['fix'].format(inst=self.instrument_id, value=plain(got)), label=r.get('label', r['slot']))

    # ------------------------------------------------------------------ waiting
    def wait_until(self, ctx, test, max_s, step, fail=None, poll_s=0.5):
        """Let lab time pass until test(view) holds (at most max_s).  Returns the seconds waited; refuses with ``fail`` (code,
        message, fix) or NOT_REACHED when the state is not reached."""
        if ctx.dry_run:
            ctx.advance(max_s, step=step)
            return 0.0
        if test(ctx.view):
            return 0.0
        waited = ctx.advance(max_s, until=test, step=step, poll_s=poll_s)
        if not test(ctx.refresh()):
            code, msg, fix = fail or ('NOT_REACHED', '%s did not finish: %s (waited %s).' % (self.instrument_id, step, units.fmt_s(waited)),
                                      'check `chemlab run %s status`, then retry.' % self.instrument_id)
            ctx.fail(refused(code, msg.format(inst=self.instrument_id), fix.format(inst=self.instrument_id)))
        return waited

    # ------------------------------------------------------------------ step lists
    def resolve(self, ctx, cap, arg, value):
        """An env value for a native argument: SI numbers into the declared unit; integers stay integers."""
        if isinstance(value, float) or (isinstance(value, int) and not isinstance(value, bool)):
            s = _num_schema(self.cap_schema(cap, arg))
            u = units.declared(s)
            if u and units.unit_kind(u):
                value = units.to_declared(value, u)
            if s.get('type') == 'integer' and float(value).is_integer():
                value = int(value)
        return value

    def build_call(self, ctx, st, env):
        cap = st['cap']
        args = copy.deepcopy(st.get('args') or {})
        for arg, ref in (st.get('with') or {}).items():
            name = ref[1:] if isinstance(ref, str) and ref.startswith('$') else None
            if name is None:
                args[arg] = ref
                continue
            if name not in env or env[name] is None:
                if arg in (st.get('optional') or []):
                    continue
                ctx.fail(invalid('MISSING_VALUE', '%s needs %s on %s.' % (ctx.verb.name, name, self.instrument_id),
                                 'see `chemlab help %s %s`.' % (self.instrument_id, ctx.verb.name)))
            args[arg] = self.resolve(ctx, cap, arg, env[name])
        return cap, args

    def run_steps(self, ctx, steps, env, results=None):
        """Run a fragment step list (see the module docstring).  Returns the responses of the native calls, by step index."""
        out = {} if results is None else results
        for i, st in enumerate(steps):
            if st.get('when') is not None and not self.cond(ctx, st['when']):
                continue
            if 'wait' in st:
                c = st['wait']
                fail = (st['code'], st['message'], st['fix']) if st.get('code') else None
                self.wait_until(ctx, lambda v, c=c: self.cond(ctx, c), float(st.get('max_s', 60.0)), st.get('step', 'wait'), fail,
                                poll_s=float(st.get('poll_s', 0.5)))
                continue
            if 'check' in st:
                c = st['check']
                got = self.sv(ctx, c['slot'] + ('#' + c['key'] if c.get('key') else '')) if isinstance(c, dict) and c.get('slot') else None
                ctx.check(self.cond(ctx, st['check']), st.get('code', 'NOT_READY'),
                          st['message'].format(inst=self.instrument_id, value=plain(got), **{k: v for k, v in env.items() if isinstance(v, str)}),
                          st['fix'].format(inst=self.instrument_id, value=plain(got), **{k: v for k, v in env.items() if isinstance(v, str)}))
                continue
            cap, args = self.build_call(ctx, st, env)
            self.check_args(ctx, cap, args)
            text = st.get('step', cap)
            text = text.format(**{k: (v if isinstance(v, str) else short(v, 30)) for k, v in env.items()})
            out[i] = ctx.call(cap, args, text, query=bool(st.get('query')))
        return out

    # ------------------------------------------------------------------ KEY=VALUE settings
    def pairs(self, ctx, p, verb, required=True):
        out = []
        if required and not p.extra:
            ctx.fail(invalid('MISSING_SETTING', '%s needs at least one KEY=VALUE.' % verb,
                             'see `chemlab help %s %s`.' % (self.instrument_id, verb)))
        seen = set()
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item,
                                 'write %s KEY=VALUE ... (see `chemlab help %s %s`).' % (verb, self.instrument_id, verb)))
            if k in seen:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % k, 'give each setting once.'))
            seen.add(k)
            out.append((k, v))
        return out

    def read_file(self, ctx, name, what):
        try:
            path = ctx.core._work_path(name)
        except Exception:                                                  # noqa: BLE001 - the generic layer's own usage error text
            ctx.fail(invalid('BAD_FILE', '%s=@%s is not a file in your work dir.' % (what, name), 'write the JSON into a file in your work dir.'))
        if not path.is_file():
            ctx.fail(invalid('NO_FILE', 'there is no file %s in your work dir (for %s).' % (name, what), 'write the JSON value into it first.'))
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except ValueError as exc:
            ctx.fail(invalid('BAD_JSON', '%s is not valid JSON (%s).' % (name, exc), 'fix the file and retry.'))

    def value(self, ctx, text, schema, kind=None, what=None):
        """Agent text -> a JSON value for an argument: @FILE (JSON from the work dir), JSON arrays / objects, comma lists for
        arrays, on/off for booleans, numbers with units (into the declared unit; ``kind`` for a class key), enums, null."""
        what = what or 'value'
        t = (text or '').strip()
        types = _types(schema)
        if t.startswith('@'):
            return self.read_file(ctx, t[1:], what)
        if t == 'null' and ('null' in types or not types):
            return None
        if t[:1] in '[{':
            try:
                return json.loads(t)
            except ValueError as exc:
                ctx.fail(invalid('BAD_JSON', '%s=%s is not valid JSON (%s).' % (what, t, exc), 'write it as JSON, or as a comma list.'))
        if 'array' in types and not (types & {'number', 'integer', 'string', 'boolean'} and ',' not in t):
            items = (schema or {}).get('items') or next((x.get('items') for x in (schema or {}).get('anyOf') or [] if isinstance(x, dict) and x.get('items')), {}) or {}
            return [self.value(ctx, x, items, kind, what) for x in [y for y in t.split(',') if y.strip()]]
        if 'boolean' in types and not types & {'number', 'integer'}:
            low = t.lower()
            if low in ('true', 'on', 'yes', '1'):
                return True
            if low in ('false', 'off', 'no', '0'):
                return False
            ctx.fail(invalid('BAD_VALUE', '%s=%s is not on/off.' % (what, t), 'write %s=on or %s=off.' % (what, what)))
        if (types & {'number', 'integer'} or (kind and not types)) and re.match(r'\s*[+-]?(\d|\.\d)', t):
            s = _num_schema(schema)
            u = units.declared(s)
            if kind:
                si = parse_si(t, kind, what)
                v = units.to_declared(si, u) if u and units.unit_kind(u) else si
            elif u and units.unit_kind(u):
                v = units.parse_for(t, s, what=what)
            else:
                m = re.fullmatch(_NUM, t)
                if not m:
                    ctx.fail(invalid('BAD_NUMBER', '%s=%s is not a number.' % (what, t), 'write a plain number.'))
                v = float(t)
            if 'integer' in types and 'number' not in types:
                if abs(v - round(v)) > 1e-9:
                    ctx.fail(invalid('NOT_WHOLE', '%s=%s must be a whole number.' % (what, t), 'give a whole number.'))
                v = int(round(v))
            return v
        enum = (schema or {}).get('enum')
        if enum and t not in [str(x) for x in enum]:
            close = difflib.get_close_matches(t, [str(x) for x in enum], 2)
            ctx.fail(invalid('BAD_CHOICE', '%s=%s is not allowed on %s.' % (what, t, self.instrument_id),
                             ('did you mean %s? ' % ' or '.join(close) if close else '') + 'use one of: %s.' % ', '.join(map(str, enum))))
        if enum:
            return next(x for x in enum if str(x) == t)
        return t

    def settings(self, ctx, cap, pairs, keys, passthrough=True, args=None, parsers=None):
        """KEY=VALUE pairs -> native arguments of ``cap``.  keys: class key -> {"arg", "kind", "parse"}; ``parsers``: parse name ->
        fn(ctx, text, schema) for structured class syntaxes (qPCR steps, ITC injections)."""
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        args = dict(args or {})
        for k, text in pairs:
            base, dot, sub = k.partition('.')
            spec = keys.get(k) or (keys.get(base) if dot else None)
            if spec is not None:
                native, kind = spec['arg'], spec.get('kind')
            elif passthrough and (k in props or (dot and base in props)):
                native, kind = (base if dot else k), None
            else:
                names = sorted(set(keys) | (set(props) if passthrough else set()))
                close = difflib.get_close_matches(k, names, 2)
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no setting %r here.' % (self.instrument_id, k),
                                 ('did you mean %s? ' % ' or '.join(close) if close else '') + 'settings: %s.' % ', '.join(names)))
            if native not in props:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s is not a setting of %s on %s.' % (k, cap, self.instrument_id),
                                 'settings here: %s.' % ', '.join(sorted(set(x for x, s in keys.items() if s.get('arg') in props) | (set(props) if passthrough else set())))))
            schema = props.get(native) or {}
            if spec is not None and spec.get('parse') and parsers:
                v = parsers[spec['parse']](ctx, text, schema)
            elif dot:
                sub_schema = ((schema.get('properties') or {}).get(sub)) or (schema.get('additionalProperties') if isinstance(schema.get('additionalProperties'), dict) else {}) or {}
                v = self.value(ctx, text, sub_schema, kind, k)
                obj = args.setdefault(native, {})
                if not isinstance(obj, dict):
                    ctx.fail(invalid('BAD_SETTING', '%s is given both as a whole and by key.' % native, 'give it one way.'))
                obj[sub] = v
                continue
            else:
                v = self.value(ctx, text, schema, kind, k)
            if spec is not None and spec.get('scale') is not None and finite(v):
                v = v * spec['scale']
            if native in args and not (spec or {}).get('merge'):
                ctx.fail(invalid('REPEATED_SETTING', '%s is set twice (%s).' % (native, k), 'give it once.'))
            args[native] = v
        return args

    def check_args(self, ctx, cap, args):
        """Numbers (and array items, and numbers inside array objects) inside the contract ranges: a usage error before anything
        is sent, in friendly units."""
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}

        def one(name, v, s, where):
            s = _num_schema(s)
            if not finite(v) or not isinstance(s, dict):
                return
            enum = s.get('enum')
            u = units.declared(s)
            f = (lambda x: units.fmt_declared(x, u)) if u and units.unit_kind(u) else (lambda x: '%g' % x)
            if enum and all(finite(e) for e in enum) and not any(abs(v - e) <= 1e-9 * max(1.0, abs(e)) for e in enum):
                ctx.fail(invalid('NOT_ALLOWED', '%s%s %s is not a setting of %s.' % (name, where, f(v), self.instrument_id),
                                 'use one of: %s.' % ', '.join(f(e) for e in enum)))
            lo, hi = s.get('minimum'), s.get('maximum')
            if (lo is not None and v < lo - 1e-9 * max(1, abs(lo))) or (hi is not None and v > hi + 1e-9 * max(1, abs(hi))):
                ctx.fail(invalid('OUT_OF_RANGE', '%s%s %s is outside the range of %s (%s to %s).' % (
                    name, where, f(v), self.instrument_id, f(lo) if lo is not None else '-', f(hi) if hi is not None else '-'),
                    'give a value from %s to %s.' % (f(lo) if lo is not None else '-', f(hi) if hi is not None else '-')))

        def walk(name, v, s, where):
            if isinstance(v, list):
                arr = s if isinstance(s, dict) and s.get('type') == 'array' else next((x for x in (s or {}).get('anyOf') or [] if isinstance(x, dict) and x.get('type') == 'array'), {})
                lo, hi = arr.get('minItems'), arr.get('maxItems')
                if (lo is not None and len(v) < lo) or (hi is not None and len(v) > hi):
                    ctx.fail(invalid('WRONG_COUNT', '%s%s takes %s to %s items on %s, not %d.' % (name, where, lo if lo is not None else 0,
                                                                                               hi if hi is not None else 'any', self.instrument_id, len(v)),
                                     'give between %s and %s.' % (lo if lo is not None else 0, hi if hi is not None else 'any number')))
                items = (s or {}).get('items') or next((x.get('items') for x in (s or {}).get('anyOf') or [] if isinstance(x, dict) and x.get('items')), None) or {}
                for i, x in enumerate(v):
                    walk(name, x, items, '%s[%d]' % (where, i + 1))
            elif isinstance(v, dict):
                sp = (s or {}).get('properties') or {}
                if not sp and isinstance(s, dict) and 'anyOf' in s:
                    sp = {}
                    for x in s['anyOf']:
                        if isinstance(x, dict):
                            sp.update(x.get('properties') or {})
                for k2, y in v.items():
                    walk(k2, y, sp.get(k2), where)
            else:
                one(name, v, s, where)
        for name, v in args.items():
            walk(name, v, props.get(name), '')

    # ------------------------------------------------------------------ jobs
    def start_job(self, ctx, m, r, cap, what):
        """A job for a started long action: the runtime's operation (operation_id) or a run whose progress is in a state slot
        ({"state": {"slot", "done", "failed"}}); None when the action is not long."""
        if ctx.dry_run:
            return None
        if m.get('state'):
            st = m['state']
            ad = self

            def state(c):
                v = ad.sv(c, st['slot'] + ('#' + st['key'] if st.get('key') else ''))
                if v in (st.get('done') or []):
                    return 'succeeded', '%s = %s' % (st['slot'], v), {'state': v}
                if v in (st.get('failed') or []):
                    return 'failed', '%s = %s' % (st['slot'], v), {'state': v}
                return 'running', '%s = %s' % (st['slot'], v), {'state': v}
            stop = None
            sm = self.vmap('stop')
            if sm and sm.get('cap'):
                def stop(c):
                    c.call(sm['cap'], dict(sm.get('args') or {}), 'stop')
                    return 'stopped with %s' % sm['cap']
            return StateJob(ctx.core, self, ctx.req, what, state, stop=stop, max_s=float(m.get('max_s', 86400)), poll_s=float(m.get('poll_s', 1.0)))
        if r.get('operation_id'):
            return OperationJob(ctx.core, self, ctx.req, what, cap, r['operation_id'], max_s=m.get('max_s'))
        return None

    def job_text(self, job, what):
        return 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, what, job.handle, job.handle, job.handle)

    # ------------------------------------------------------------------ names checked against a list slot
    def known(self, ctx, name, spec, what):
        """Check ``name`` against a list slot ({"slot", "key"}): a usage error with close matches when it is not there."""
        if not spec:
            return
        items = self.sv(ctx, spec['slot'], []) or []
        names = []
        for x in items:
            if isinstance(x, dict):
                x = x.get(spec.get('key', 'id'))
            if x is not None:
                names.append(str(x))
        if name in names:
            ctx.checks.append('%s %s is there' % (what, name))
            return
        close = difflib.get_close_matches(name, names, 2)
        ctx.fail(invalid(spec.get('code', 'UNKNOWN_ITEM'), '%s has no %s %r%s.' % (self.instrument_id, what, name, spec.get('where', '')),
                         ('did you mean %s? ' % ' or '.join(close) if close else '') +
                         ('%ss there: %s.' % (what, ', '.join(names)) if names else spec.get('none', 'there is none yet.'))))

    def status_lines(self, values):
        """status: one line per entry of the fragment's "view": [{"label", "slot", "key", "kind"}] (kind: a unit kind for a number)."""
        lines, js = [], {}
        for e in self.map.get('view') or []:
            v = self.sv(values, e['slot'] + ('#' + e['key'] if e.get('key') else ''))
            kind = e.get('kind')
            if kind and finite(v):
                u = None
                if self.lab is not None and not e.get('key'):
                    u = units.declared(self.lab.contract['state_slots'].get(e['slot']) or {})
                u = e.get('unit', u)
                txt = units.fmt(units.from_declared(v, u) if u else v, kind)
            elif v is None:
                txt = '-'
            elif isinstance(v, bool):
                txt = e.get('yes', 'yes') if v else e.get('no', 'no')
            elif isinstance(v, str):
                txt = v
            elif isinstance(v, float):
                txt = '%.3g' % v
            else:
                txt = short(v, 80)
            lines.append('%s: %s' % (e['label'], txt))
            js[e['label']] = v
        return lines, js

    def verb_help(self, name):
        """help INST VERB with this member's own manual references ("manual": {verb | "*": "doc id pp. ..."}) and example
        ("examples": {verb: "load edta --into cell"}) in place of the class-wide ones."""
        r = super().verb_help(name)
        man = self.map.get('manual') or {}
        ref = man.get(name) or man.get('*')
        ex = (self.map.get('examples') or {}).get(name)
        lines = r['text'].split('\n')
        for i, line in enumerate(lines):
            if ref and line.startswith('manual: '):
                lines[i] = 'manual: ' + ref
            if ex and line == 'example:' and i + 1 < len(lines):
                lines[i + 1] = '  chemlab run %s %s' % (self.instrument_id, ex)
        r['text'] = '\n'.join(lines)
        return r

    def summary(self, values):
        lines, js = self.status_lines(values)
        return (['; '.join(lines[i:i + 3]) for i in range(0, len(lines), 3)] or ['(no status slots mapped)']), js


_ = RUNNING

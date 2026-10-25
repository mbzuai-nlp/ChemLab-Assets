"""Microscopy and imaging class ("imaging"): SEM, TEM, laser-scanning profilometer, polarizing microscope, AFM, STM, micro-CT and
the gel / blot imager.  Members and mappings: configs/adapters/imaging.json.  The structure class (structure.py: diffraction,
SAXS, XPS, ellipsometry) is built on the same step engine (SampleAdapter, below).

Verbs (every one also takes --dry-run; `chemlab run INST` lists what a member supports, with reasons for the rest):

    load SAMPLE [--at X,Y] [--holder H]     put a specimen from the bench into the instrument (composite: door / airlock / holder)
    unload [SAMPLE]                         take it out again
    move X Y                                stage position (lengths; a bare number is mm)
    focus auto | Z                          autofocus, or the member's focus coordinate (SEM working distance, TEM defocus,
                                            profilometer head Z, microscope stage Z, AFM optical focus)
    zoom MAG | --objective N                magnification (SEM / TEM magnification, optical zoom) or the objective
    source on|off [--kv V] [--current I] [--level F]   beam / high tension / X-rays / illumination / laser
    acquire [--pixels P] [--dwell T] [--exposure T] [--binning N] [--size L] [--rate F] [--line-time T] [--points N] [--hdr]
                                            take an image / scan / tomogram: job-N (chemlab status|wait|cancel job-N), or --wait
    result [ID] [--save-to FILE]            the record (default: the last one)
    vacuum pump|vent                        chamber vacuum (SEM)
    approach / retract [--distance L]       scanning-probe tip approach and retract (AFM, STM)
    status                                  the class view

Long actions: a verb whose native action takes time returns job-N at once (an image or scan, a pump-down, a warm-up, a
transfer, an autofocus run); --wait blocks until it is done instead (lab time moves; the reply says how long), --max bounds it.
When a sequence needs another native call after a wait (TEM: high tension ramp -> filament, airlock pump-down -> holder in;
SEM: the drawer must be fully out before the specimen goes on), the verb waits for those intermediate steps itself (bounded by
--max) and says so.

Instrument facts live in the fragment as step lists per verb (the step engine below): native calls with arguments taken from
the verb's inputs ($sample, $x, $value, $opt:NAME), the current state ($slot:ID) or literals; checks (class preconditions with
a code, message and fix); waits for a state; and the job that follows.  Quantities are parsed into SI by the verb and converted
into the unit each argument declares, with the range checked from the contract before anything is sent.

Vendor-neutral models followed: Micro-Manager Core (setXYPosition / setPosition / snapImage / setExposure), the OME data model
for image records, ISO 16700 / ASTM E766 (SEM magnification), ISO 25178 (areal roughness) and the terms of the members' manuals
(page references in the fragment notes; no manual text is copied).
"""
import copy
import json
import math
import re

from ...generic import coerce
from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import CompileError, invalid, refused, unexpected
from ..jobs import RUNNING, ConditionJob, OperationJob, StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt

ON_OFF = ['on', 'off']


EXAMPLES = {'duration': '20us', 'force': '2nN', 'length': '20nm', 'current': '150uA', 'voltage': '60kV', 'frequency': '1Hz', 'angle': '0.5deg'}


def parse_q(text, kinds, what):
    """Agent text -> Q in SI for the first of ``kinds`` whose framework units fit (20us, 2nN, 50pA, 20nm ...).  A usage error
    names the kinds and an example."""
    t = str(text).strip().replace(' ', '')
    for kind in kinds:
        try:
            return Q(units.parse(t, kind, what=what), kind)
        except CompileError:
            pass
    raise invalid('BAD_VALUE', '%s %s is not a %s.' % (what, text, ' or a '.join(k.replace('_', ' ') for k in kinds)),
                  'write it like %s.' % ' or '.join(EXAMPLES.get(k, units.example(k)) for k in kinds))


class Q:
    """A parsed quantity: SI value and its kind."""

    def __init__(self, si, kind):
        self.si, self.kind = si, kind

    def __repr__(self):
        return 'Q(%r, %r)' % (self.si, self.kind)


def wait_opts(default_max='10min'):
    return {'wait': Opt('block until it is done instead of returning a job (lab time moves)', kind='flag'),
            'max': Opt('the longest wait (default %s)' % default_max, kind='duration', metavar='TIME')}


class SampleAdapter(ClassAdapter):
    """Step engine shared by the imaging and structure classes.  A member's verb mapping:

        {"steps": [STEP, ...], "job": JOB, "after": ["slot", ...], "next": "hint shown after success",
         "defaults": {"OPTION": "agent text"}, "options": ["OPTION", ...] (extra options the member takes)}

    STEP is one of
        {"cap": CAP, "args": {ARG: VALUE}, "say": "plain text", "if": COND, "unless": COND, "query": false}
        {"check": COND, "code": "CODE", "message": "...", "fix": "...", "label": "...", "if": COND}
        {"wait": COND, "max_s": 60, "say": "the drawer is fully out", "poll_s": 1.0, "if": COND, "unless": COND}
        {"fail": "CODE", "message": "...", "fix": "...", "kind": "usage|refused|unsupported", "if": COND}
        {"remember": NAME, "value": VALUE}  (e.g. a slot value now, used as $NAME by a later step)
    VALUE: a JSON literal, or "$name" (a verb input: $sample, $value, $x, $y, $id, $opt:NAME), "$slot:ID[#key]" (now),
        "$param:NAME" (member params).  Quantities are converted to the argument's declared unit and range-checked.
    COND: {"slot": ID, "eq"|"ne"|"in"|"not_in"|"lt"|"le"|"gt"|"ge": V} | {"slot": ID, "truthy": true|false}
          | {"slot": ID, "near": VALUE, "rel": r, "abs": a} | {"input": NAME, "eq"|"in"|"given": ...}
          | {"all": [...]} | {"any": [...]} | {"not": COND}
    JOB: "operation" (the last call's operation_id), {"until": COND, "what": "...", "max_s": S} (a pure wait),
         {"state": {"slot": ID, "done": [...], "failed": [...]}, "stop_cap": CAP, "what": "..."}
    Messages and fixes may use {inst} {sample} {value} {slot:ID}.
    """
    VIEW = ()                        # default class view rows when the member has no "view"
    MADE_BY = 'acquire'              # the verb that makes records (for hints)
    QOPTS = {}                       # option -> quantity kinds parsed here (SI prefixes the framework lacks: 20us, 2nN)

    # ------------------------------------------------------------------ mapping check
    def check_mapping(self, lab):
        """The base check plus the step engine's own references: $slot:ID values, view rows and job progress slots."""
        problems = ClassAdapter.check_mapping(self, lab)
        visible = {s.lower() for s in lab.view.slots}

        def walk(o, where):
            if isinstance(o, str) and o.startswith(('$slot:', '$slotkey:')):
                sid = o.split(':', 1)[1].split('#')[0]
                if sid.lower() not in visible:
                    problems.append('%s: no visible state slot %s' % (where, sid))
            elif isinstance(o, dict):
                for k, x in o.items():
                    if k == 'progress' and isinstance(x, list):
                        for sid in x:
                            if sid.lower() not in visible:
                                problems.append('%s.progress: no visible state slot %s' % (where, sid))
                    walk(x, '%s.%s' % (where, k))
            elif isinstance(o, list):
                for x in o:
                    walk(x, where)
        walk(self.map.get('verbs') or {}, 'verbs')
        for row in self.map.get('view') or []:
            if row.get('slot', '').lower() not in visible:
                problems.append('view: no visible state slot %s' % row.get('slot'))
        return problems

    # ------------------------------------------------------------------ inputs and values
    def env_from(self, ctx, p, extra=None):
        env = {}
        for k, v in (p.opts or {}).items():
            if k in ('wait', 'max', 'save-to'):
                continue
            o = ctx.verb.options.get(k)
            if k in self.QOPTS:
                env['opt:' + k] = parse_q(v, self.QOPTS[k], '--' + k)
                continue
            env['opt:' + k] = Q(v, o.kind) if o is not None and o.quantity else v
        env.update(extra or {})
        return env

    def apply_defaults(self, ctx, m, env):
        for k, text in (m.get('defaults') or {}).items():
            if 'opt:' + k in env:
                continue
            if isinstance(text, list):                              # conditional default: [{"if": COND, "value": V}, {"value": V}]
                text = next((x['value'] for x in text if self.cond(ctx, x.get('if'), env)), None)
                if text is None:
                    continue
            o = ctx.verb.options.get(k)
            if k in self.QOPTS:
                env['opt:' + k] = parse_q(text, self.QOPTS[k], '--' + k)
            elif o is not None and o.quantity:
                env['opt:' + k] = Q(units.parse(str(text), o.kind, what='--' + k), o.kind)
            else:
                env['opt:' + k] = text

    @staticmethod
    def options_of(m):
        """The options a mapping (or sub-mapping) uses (others are a usage error)."""
        m = m or {}
        found = set(m.get('options') or [])

        def walk(o):
            if isinstance(o, str) and o.startswith('$opt:'):
                found.add(o[5:].split('#')[0])
            elif isinstance(o, dict):
                for x in o.values():
                    walk(x)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(m)
        return found

    def check_options(self, ctx, p, verb, m):
        allowed = self.options_of(m) | {'wait', 'max', 'save-to'}
        if 'at_x' in allowed or 'at_y' in allowed:
            allowed.add('at')
        for k in p.opts:
            if k not in allowed and k in ctx.verb.options:
                usable = sorted(x for x in allowed if x in ctx.verb.options and x not in ('wait', 'max', 'save-to'))
                ctx.fail(invalid('OPTION_NOT_USED', '%s does not take --%s on %s.' % (verb, k, self.instrument_id),
                                 'options of %s on %s: %s (see `chemlab help %s %s`).' % (
                                     verb, self.instrument_id, ', '.join('--' + x for x in usable) or 'none', self.instrument_id, verb)))

    def slot_value(self, ctx, spec):
        sid, _, key = spec.partition('#')
        v = ctx.v(sid)
        if key:
            for part in key.split('.'):
                v = v.get(part) if isinstance(v, dict) else None
        return v

    def raw(self, ctx, expr, env):
        """A VALUE expression -> Python value (Q for quantities); literals unchanged."""
        if isinstance(expr, str) and expr.startswith('$'):
            key = expr[1:]
            if key.startswith('slot:'):
                return self.slot_value(ctx, key[5:])
            if key.startswith('param:'):
                return self.param(key[6:])
            if key.startswith('slotkey:'):                       # the first key of an object slot whose value is set
                v = self.slot_value(ctx, key[8:])
                return next((k for k, x in v.items() if x), None) if isinstance(v, dict) else None
            if key in env:
                return env[key]
            name = key[4:] if key.startswith('opt:') else key
            ctx.fail(invalid('MISSING_' + name.upper().replace('-', '_'), '%s on %s needs %s.' % (
                ctx.verb.name, self.instrument_id, ('--' + name) if key.startswith('opt:') else name.upper()),
                'see `chemlab help %s %s`.' % (self.instrument_id, ctx.verb.name)))
        if isinstance(expr, dict) and '$or' in expr:              # {"$or": [A, B, ...]}: the first one that is given
            for x in expr['$or']:
                if isinstance(x, str) and x.startswith('$') and not x.startswith(('$slot:', '$param:', '$slotkey:')) and x[1:] not in env:
                    continue
                return self.raw(ctx, x, env)
            return None
        if isinstance(expr, dict) and '$list' in expr:            # {"$list": EXPR}: "a,b,c" -> ["a", "b", "c"]
            v = self.raw(ctx, expr['$list'], env)
            return [x.strip() for x in v.split(',') if x.strip()] if isinstance(v, str) else v
        if isinstance(expr, dict) and '$records' in expr:         # {"$records": EXPR, "fields": [...], "presets": {name: [...]}}
            v = self.raw(ctx, expr['$records'], env)
            return self.records(ctx, v, expr.get('fields') or [], expr.get('presets') or {}, expr.get('ints') or [])
        if isinstance(expr, dict) and '$mul' in expr:            # {"$mul": [A, B, ...]}: a product (plain number)
            out = 1.0
            for x in expr['$mul']:
                x = self.raw(ctx, x, env)
                x = x.si if isinstance(x, Q) else x
                if not finite(x):
                    return None
                out *= x
            return out
        if isinstance(expr, dict) and '$div' in expr:            # {"$div": [A, B], "kind": K}: A / B as a quantity of kind K
            a, b = (self.raw(ctx, x, env) for x in expr['$div'])
            a, b = (x.si if isinstance(x, Q) else x for x in (a, b))
            if not (finite(a) and finite(b)) or b == 0:
                ctx.fail(invalid('BAD_VALUE', 'cannot compute that setting on %s.' % self.instrument_id, 'check the value you gave.'))
            return Q(a / b, expr.get('kind', 'length'))
        if isinstance(expr, dict):
            return {k: self.raw(ctx, v, env) for k, v in expr.items()}
        if isinstance(expr, list):
            return [self.raw(ctx, v, env) for v in expr]
        return expr

    def records(self, ctx, text, fields, presets, ints):
        """'survey,C 1s:280:296:0.1:0.1:2' -> a list of objects: preset names, or colon-separated values in the order of ``fields``
        (the first field is the name; missing trailing numbers take the last preset's values)."""
        if not isinstance(text, str):
            return text
        out = []
        for item in [x.strip() for x in text.split(',') if x.strip()]:
            if item in presets:
                out.append(dict(zip(fields, presets[item])))
                continue
            parts = item.split(':')
            if len(parts) < 2 or len(parts) > len(fields):
                ctx.fail(invalid('BAD_SETTING', '%r is neither a preset (%s) nor NAME:%s.' % (item, ', '.join(presets) or 'none', ':'.join(f.upper() for f in fields[1:])),
                                 'write it like %s.' % (next(iter(presets), 'survey'))))
            rec = {fields[0]: parts[0]}
            for f, x in zip(fields[1:], parts[1:]):
                try:
                    rec[f] = int(x) if f in ints else float(x)
                except ValueError:
                    ctx.fail(invalid('BAD_NUMBER', '%s=%r in %r is not a number.' % (f, x, item), 'write numbers only between the colons.'))
            base = next(iter(presets.values()), None)
            for f, d in zip(fields, base or []):
                rec.setdefault(f, d)
            out.append(rec)
        return out

    @staticmethod
    def plain(val):
        """Nested quantities (in objects / lists) -> SI numbers."""
        if isinstance(val, Q):
            return val.si
        if isinstance(val, dict):
            return {k: SampleAdapter.plain(v) for k, v in val.items()}
        if isinstance(val, list):
            return [SampleAdapter.plain(v) for v in val]
        return val

    def to_arg(self, ctx, cap, arg, val, what=None):
        """A raw value -> the native argument value (declared unit, integer, enum and range checked)."""
        schema = self.cap_schema(cap, arg)
        s = schema
        if 'anyOf' in s:
            s = next((x for x in s['anyOf'] if x.get('type') in ('number', 'integer', 'string')), s)
        what = what or arg
        if isinstance(val, Q):
            want = units.unit_kind(self.arg_unit(cap, arg))
            fam = {'wavelength': 'length'}
            if want and fam.get(want, want) != fam.get(val.kind, val.kind):
                ctx.fail(invalid('WRONG_QUANTITY', '%s on %s takes a %s here, not a %s.' % (what, self.instrument_id, want, val.kind),
                                 'write it like %s.' % EXAMPLES.get(want, units.example(want))))
            lo, hi = self.arg_range_si(cap, arg)
            if (lo is not None and val.si < lo - 1e-12 * max(1.0, abs(lo))) or (hi is not None and val.si > hi + 1e-12 * max(1.0, abs(hi))):
                ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of %s (%s to %s).' % (
                    what, units.fmt(val.si, val.kind), self.instrument_id, units.fmt(lo, val.kind), units.fmt(hi, val.kind)),
                    'give a value from %s to %s.' % (units.fmt(lo, val.kind), units.fmt(hi, val.kind))))
            v = self.arg_value(cap, arg, val.si)
            v = float('%.15g' % v) if finite(v) else v                 # 15 significant digits: no binary residue, no lost precision
            if s.get('type') == 'integer':
                if abs(v - round(v)) > 1e-9:
                    ctx.fail(invalid('NOT_WHOLE', '%s must be a whole number on %s.' % (what, self.instrument_id), 'give a whole number.'))
                v = int(round(v))
            return v
        if isinstance(val, (dict, list)):
            val = self.plain(val)
        if isinstance(val, str) and s.get('type') in ('number', 'integer', 'boolean', 'array', 'object'):
            if s.get('type') in ('boolean', 'array', 'object'):
                val = coerce(val, s, what)
        if isinstance(val, str) and s.get('type') in ('number', 'integer'):
            val = units.parse_for(val, s, what=what) if units.declared(s) and units.unit_kind(units.declared(s)) else coerce(val, s, what)
        if s.get('type') == 'integer' and isinstance(val, float) and val.is_integer():
            val = int(val)
        if s.get('type') in ('number', 'integer') and finite(val):
            lo, hi = s.get('minimum'), s.get('maximum')
            if (lo is not None and val < lo) or (hi is not None and val > hi):
                ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of %s (%s to %s).' % (what, val, self.instrument_id, lo, hi),
                                 'give a value from %s to %s.' % (lo, hi)))
        enum = s.get('enum')
        if enum and isinstance(val, str) and val not in enum:
            ctx.fail(invalid('BAD_CHOICE', '%s %r is not one of the choices on %s.' % (what, val, self.instrument_id),
                             'choose one of: %s.' % ', '.join(map(str, enum))))
        return val

    def build_args(self, ctx, cap, spec, env):
        out = {}
        for arg, expr in (spec or {}).items():
            val = self.raw(ctx, expr, env)
            out[arg] = self.to_arg(ctx, cap, arg, val, what=self.label_of(expr, arg))
        return out

    @staticmethod
    def label_of(expr, arg):
        if isinstance(expr, str) and expr.startswith('$opt:'):
            return '--' + expr[5:]
        if isinstance(expr, str) and expr.startswith('$') and not expr.startswith(('$slot:', '$param:', '$slotkey:')):
            return expr[1:].upper()
        return arg

    # ------------------------------------------------------------------ conditions
    def cond(self, ctx, c, env=None):
        if c is None:
            return True
        env = env or {}
        if 'all' in c:
            return all(self.cond(ctx, x, env) for x in c['all'])
        if 'any' in c:
            return any(self.cond(ctx, x, env) for x in c['any'])
        if 'not' in c:
            return not self.cond(ctx, c['not'], env)
        if 'input' in c:
            v = env.get(c['input'])
            v = v.si if isinstance(v, Q) else v
            if 'given' in c:
                return (v is not None) == bool(c['given'])
            if 'eq' in c:
                return v == c['eq']
            if 'in' in c:
                return v in c['in']
            return bool(v)
        v = self.slot_value(ctx, c['slot'] + ('#' + c['key'] if c.get('key') else ''))
        if 'eq' in c:
            return v == self.raw(ctx, c['eq'], env)
        if 'ne' in c:
            return v != self.raw(ctx, c['ne'], env)
        if 'in' in c:
            return v in c['in']
        if 'has' in c:
            ref = self.raw(ctx, c['has'], env)
            return isinstance(v, (list, dict, str)) and ref in v
        if 'not_in' in c:
            return v not in c['not_in']
        if 'truthy' in c:
            return bool(v) == bool(c['truthy'])
        if 'near' in c:
            ref = self.raw(ctx, c['near'], env)
            ref = ref.si if isinstance(ref, Q) else ref
            if not (finite(v) and finite(ref)):
                return False
            return abs(v - ref) <= max(float(c.get('abs', 0.0)), float(c.get('rel', 0.0)) * abs(ref))
        for op, fn in (('lt', lambda a, b: a < b), ('le', lambda a, b: a <= b), ('gt', lambda a, b: a > b), ('ge', lambda a, b: a >= b)):
            if op in c:
                ref = self.raw(ctx, c[op], env)
                ref = ref.si if isinstance(ref, Q) else ref
                return finite(v) and finite(ref) and fn(v, ref)
        return bool(v)

    def fill(self, ctx, text, env):
        """Message templates: {inst} {sample} {value} {slot:ID}."""
        if not text:
            return text
        out = text.replace('{inst}', self.instrument_id)
        for k, v in env.items():
            if '{%s}' % k in out:
                out = out.replace('{%s}' % k, units.fmt(v.si, v.kind) if isinstance(v, Q) else str(v))
        while '{slot:' in out:
            a = out.index('{slot:')
            b = out.index('}', a)
            out = out[:a] + short(self.slot_value(ctx, out[a + 6:b]), 60) + out[b + 1:]
        return out

    # ------------------------------------------------------------------ running a mapping
    def prevalidate(self, ctx, steps, env):
        """Usage checks of every agent-given value before anything is sent (range, unit, enum), and every verb input the steps
        that will run need is there (so a missing SAMPLE is a usage error before the first call, not halfway)."""
        later = set()

        def need(o):
            if isinstance(o, str) and o.startswith('$') and not o.startswith(('$slot:', '$param:', '$slotkey:', '$opt:')):
                if o[1:] not in env and o[1:] not in later:
                    self.raw(ctx, o, env)                       # raises MISSING_<NAME> (usage)
            elif isinstance(o, dict):
                if '$or' in o:
                    return
                for x in o.values():
                    need(x)
            elif isinstance(o, list):
                for x in o:
                    need(x)
        for st in steps:
            if 'remember' in st:
                later.add(st['remember'])
            if 'cap' not in st:
                continue
            if ('if' in st and not self.cond(ctx, st['if'], env)) or ('unless' in st and self.cond(ctx, st['unless'], env)):
                continue
            need(st.get('args') or {})
        for st in steps:
            if 'cap' not in st:
                continue
            if ('if' in st and not self.cond(ctx, st['if'], env)) or ('unless' in st and self.cond(ctx, st['unless'], env)):
                continue                                        # a step that will not run (judged on the state now)
            for arg, expr in (st.get('args') or {}).items():
                if isinstance(expr, str) and expr.startswith('$') and not expr.startswith(('$slot:', '$param:')) and expr[1:] in env:
                    self.to_arg(ctx, st['cap'], arg, env[expr[1:]], what=self.label_of(expr, arg))

    def run_steps(self, ctx, steps, env, max_s=None):
        last = None
        self.prevalidate(ctx, steps, env)
        for st in steps:
            if 'if' in st and not self.cond(ctx, st['if'], env):
                continue
            if 'unless' in st and self.cond(ctx, st['unless'], env):
                continue
            if 'check' in st:
                ctx.check(self.cond(ctx, st['check'], env), st.get('code', 'NOT_READY'), self.fill(ctx, st['message'], env),
                          self.fill(ctx, st.get('fix', ''), env), kind=st.get('kind', 'refused'), label=st.get('label'))
            elif 'fail' in st:
                kind = st.get('kind', 'refused')
                err = {'usage': invalid, 'refused': refused}.get(kind)
                if err is None:
                    from ..errors import unsupported
                    err = unsupported
                ctx.fail(err(st['fail'], self.fill(ctx, st['message'], env), self.fill(ctx, st.get('fix', ''), env)))
            elif 'cap' in st:
                args = self.build_args(ctx, st['cap'], st.get('args'), env)
                last = ctx.call(st['cap'], args, self.fill(ctx, st.get('say') or st['cap'], env), query=bool(st.get('query')))
                if st.get('save_as'):
                    env[st['save_as']] = (last or {}).get('result')
            elif 'remember' in st:                              # {"remember": NAME, "value": VALUE}: keep a value for later steps
                env[st['remember']] = self.raw(ctx, st['value'], env)
            elif 'wait' in st:
                limit = float(max_s) if max_s is not None else float(st.get('max_s', 600.0))
                self.wait_until(ctx, st['wait'], env, limit, self.fill(ctx, st.get('say', 'the instrument is ready'), env), float(st.get('poll_s', 1.0)))
        return last

    def wait_until(self, ctx, c, env, max_s, what, poll_s=1.0):
        if self.cond(ctx, c, env):
            return 0.0
        if ctx.dry_run:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': max_s}, 'step': 'wait until %s (up to %s)' % (what, units.fmt_s(max_s))})
            return 0.0
        t0 = ctx.now()
        ctx.advance(max_s, until=lambda v: self.cond(ctx, c, env), step='wait until %s' % what, poll_s=poll_s)
        if not self.cond(ctx, c, env):
            ctx.fail(refused('NOT_REACHED', '%s: waited %s, but not yet %s.' % (self.instrument_id, units.fmt_s(ctx.now() - t0), what),
                             'check `chemlab run %s status`; retry with a longer --max if it is still on its way.' % self.instrument_id))
        ctx.done.append('waited %s until %s' % (units.fmt_s(ctx.now() - t0), what))
        return ctx.now() - t0

    def make_job(self, ctx, m, last, env, what):
        """The job that follows the steps (or None when there is nothing left to wait for)."""
        spec = m.get('job')
        if spec is None or ctx.dry_run:
            return None
        if spec == 'operation' or (isinstance(spec, dict) and spec.get('operation')):
            op = (last or {}).get('operation_id')
            if not op:
                return None
            cap = (ctx.req.calls[-1] if ctx.req.calls else {}).get('capability') or ''
            for c in reversed(ctx.req.calls):
                if c.get('op') == 'invoke':
                    cap = c.get('capability')
                    break
            return OperationJob(ctx.core, self, ctx.req, '%s on %s' % (what, self.instrument_id), cap, op)
        if 'until' in spec:
            if self.cond(ctx, spec['until'], env):
                return None
            ad = self

            def test(c):
                ok = ad.cond(c, spec['until'], env)
                return ok, ad.progress(c, spec)
            return ConditionJob(ctx.core, self, ctx.req, '%s on %s' % (what, self.instrument_id), test,
                                max_s=float(spec.get('max_s', 3600.0)), poll_s=float(spec.get('poll_s', 1.0)))
        if 'state' in spec:
            st = spec['state']
            ad = self

            def state(c):
                v = ad.slot_value(c, st['slot'])
                if v in (st.get('done') or []):
                    return 'succeeded', '%s = %s' % (st['slot'], v), {'state': v}
                if v in (st.get('failed') or []):
                    return 'failed', '%s = %s' % (st['slot'], v), {'state': v}
                return 'running', ad.progress(c, spec) or '%s = %s' % (st['slot'], v), {'state': v}
            stop = None
            if spec.get('stop_cap'):
                def stop(c):
                    c.call(spec['stop_cap'], {}, 'stop')
                    return 'stopped with %s' % spec['stop_cap']
            return StateJob(ctx.core, self, ctx.req, '%s on %s' % (what, self.instrument_id), state, stop=stop,
                            max_s=float(spec.get('max_s', 7200.0)), poll_s=float(spec.get('poll_s', 1.0)))
        return None

    def progress(self, ctx, spec):
        rows = spec.get('progress') or []
        return ', '.join('%s %s' % (s.split('.')[-1], short(self.slot_value(ctx, s), 40)) for s in rows)

    def run_mapping(self, ctx, p, verb, env, what, m=None):
        """Run a member's mapping of ``verb`` (or the sub-mapping ``m``) and follow it with its job (or block with --wait)."""
        m = self.vmap(verb) if m is None else m
        what = self.fill(ctx, m.get('what') or what, env)
        self.check_options(ctx, p, verb, m)
        self.apply_defaults(ctx, m, env)
        max_s = p.get('max')
        last = self.run_steps(ctx, m.get('steps') or [], env, max_s=max_s)
        lines = []
        js = {'verb': verb}
        if last is not None and isinstance(last.get('result'), dict):
            js['result'] = last.get('result')
        job = self.make_job(ctx, m, last, env, what)
        if job is not None:
            if p.get('wait'):
                r = job.wait_reply(ctx.core, ctx.req, max_s if max_s is not None else float(m.get('max_s', 3600.0)))
                ctx.waited_s += r['json'].get('waited_s') or 0.0
                ctx.refresh()
                st = r['json'].get('status')
                js.update({'job': job.handle, 'job_status': st, 'job_result': r['json'].get('result')})
                if st not in ('succeeded', 'completed'):
                    if st in RUNNING:
                        ctx.fail(refused('NOT_DONE', '%s is still running after %s (%s).' % (job.handle, units.fmt_s(r['json'].get('waited_s') or 0), what),
                                         'wait more: `chemlab wait %s --max 30min`, or check `chemlab status %s`.' % (job.handle, job.handle)))
                    res = r['json'].get('result')
                    err = r['json'].get('error') or (res.get('error_code') if isinstance(res, dict) else None)
                    ctx.fail(refused('JOB_' + str(st).upper(), '%s %s ended %s%s.' % (what, job.handle, st, (' (%s)' % err) if err else ''),
                                     'see `chemlab run %s status` and `chemlab run %s result`.' % (self.instrument_id, self.instrument_id)))
                lines.append('%s done after %s of waiting (t = %.1f s)' % (what, units.fmt_s(r['json'].get('waited_s') or 0), ctx.now()))
            else:
                js.update({'job': job.handle, 'kind': job.kind})
                lines.append('started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s' % (
                    job.handle, job.description, job.handle, job.handle, job.handle))
        return lines, js, m

    # ------------------------------------------------------------------ view
    def view_rows(self):
        return self.map.get('view') or [{'label': k, 'slot': s} for k, s in self.VIEW]

    def show(self, values, row):
        get = values.v if hasattr(values, 'v') else values.get
        v = get(row['slot'])
        for part in (row.get('key') or '').split('.'):
            if part:
                v = v.get(part) if isinstance(v, dict) else None
        if v is None:
            return None
        if row.get('kind') and finite(v):
            si = units.from_declared(v, row['unit']) if row.get('unit') else v
            return units.fmt(si, row['kind'])
        if row.get('fmt') == 'x' and finite(v):
            return '%gx' % v
        if row.get('fmt') == 'deg' and finite(v):
            return '%.2f°' % math.degrees(v)
        if row.get('fmt') == 'kv' and finite(v):
            return '%g kV' % (v / 1000.0)
        if row.get('fmt') == 'ma' and finite(v):
            return '%g mA' % (v * 1000.0)
        if row.get('fmt') == 'ua' and finite(v):
            return '%g µA' % (v * 1e6)
        if row.get('fmt') == 'pa' and finite(v):
            return '%.3g Pa' % v
        if isinstance(v, bool):
            return 'yes' if v else 'no'
        if isinstance(v, str):
            return v if len(v) <= 50 else v[:50] + '...'
        return short(v, 50)

    def summary(self, values):
        parts, js = [], {}
        for row in self.view_rows():
            s = self.show(values, row)
            js[row['label']] = s
            if s is not None:
                parts.append('%s %s' % (row['label'], s))
        lines = []
        while parts:
            lines.append('; '.join(parts[:5]))
            parts = parts[5:]
        return lines or ['(see `chemlab read %s`)' % self.instrument_id], js

    def head_text(self, ctx, m, js, what, env):
        """The first line: what was done, or what was started (a job) and is still running."""
        what = m.get('what') or what
        if js.get('job') and not js.get('job_status'):
            return self.fill(ctx, m.get('started') or ('%s started' % (m.get('what') or what)), env)
        if js.get('job_status'):
            return self.fill(ctx, m.get('done') or ('%s done' % (m.get('what') or what)), env)
        return self.fill(ctx, m.get('done') or what, env)

    @staticmethod
    def waited_lines(ctx):
        waits = [d for d in ctx.done if d.startswith('waited ')]
        return ['(lab time: %s)' % '; '.join(waits)] if waits else []

    def now_line(self, ctx):
        lines, _ = self.summary(ctx)
        return 'now: ' + ' | '.join(lines)

    # ------------------------------------------------------------------ shared verb handlers
    def h_generic(self, verb, what, env_fn=None):
        """A handler that runs the member's mapping of ``verb``; env_fn(ctx, p) -> extra inputs."""
        def h(ctx, p):
            env = self.env_from(ctx, p, env_fn(ctx, p) if env_fn else None)
            lines, js, m = self.run_mapping(ctx, p, verb, env, self.fill(ctx, what, env))
            if ctx.dry_run:
                return {'text': '', 'json': js}
            head = 'ok: %s %s' % (self.instrument_id, self.head_text(ctx, m, js, what, env))
            text = '\n'.join([head] + self.waited_lines(ctx) + lines + [self.now_line(ctx)])
            if m.get('next'):
                text += '\nnext: ' + self.fill(ctx, m['next'], env)
            return {'text': text + '\n', 'json': js}
        return h

    def h_onoff(self, verb, what):
        """on|off verbs: the mapping has "on" and "off" sub-mappings (each a step list mapping)."""
        def h(ctx, p):
            want = p.pos[0]
            m = self.vmap(verb) or {}
            sub = m.get(want)
            if sub is None:
                ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s %s is not available on %s.' % (verb, want, self.instrument_id),
                                 (m.get('why_' + want) or 'see `chemlab help %s %s`.' % (self.instrument_id, verb)).replace('{inst}', self.instrument_id)))
            env = self.env_from(ctx, p, {'value': want})
            lines, js, mm = self.run_mapping(ctx, p, verb, env, self.fill(ctx, sub.get('what') or '%s %s' % (what, want), env), m=sub)
            if ctx.dry_run:
                return {'text': '', 'json': js}
            head = 'ok: %s %s' % (self.instrument_id, self.head_text(ctx, sub, js, '%s %s' % (what, want), env))
            text = '\n'.join([head] + self.waited_lines(ctx) + lines + [self.now_line(ctx)])
            if sub.get('next'):
                text += '\nnext: ' + self.fill(ctx, sub['next'], env)
            js['state'] = want
            return {'text': text + '\n', 'json': js}
        return h

    def h_result(self, ctx, p):
        m = self.vmap('result')
        rid = p.pos[0] if p.pos else None
        if m.get('arg') is None:                                    # a member whose record is the current pattern (no record ids)
            if rid is not None:
                ctx.fail(invalid('NO_RECORD_IDS', '%s keeps only its current pattern (no record ids).' % self.instrument_id,
                                 'leave out the ID: `chemlab run %s result`.' % self.instrument_id))
            rid = 'current'
        elif rid is None:
            rid = self.slot_value(ctx, m['last_slot'])
            if not rid:
                ctx.fail(refused('NO_RECORD', '%s has no record of yours yet.' % self.instrument_id,
                                 'take one first (`chemlab run %s %s`).' % (self.instrument_id, m.get('made_by', self.MADE_BY))))
        args = dict(m.get('args') or {})
        if m.get('arg') is not None:
            args[m['arg']] = [rid] if m.get('list') else rid
        r = ctx.call(m['cap'], args, 'read record %s' % rid, query=True)
        res = r.get('result')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        lines = ['%s record %s:' % (self.instrument_id, rid)]
        keys = m.get('show') or (list(res)[:24] if isinstance(res, dict) else [])
        if isinstance(res, dict):
            for k in keys:
                if k in res:
                    lines.append('  %-24s %s' % (k, short(res[k], 150)))
            more = [k for k in res if k not in keys]
            if more:
                lines.append('  (%d more fields: %s; all of them with --json or --save-to FILE)' % (len(more), ', '.join(more[:12]) + (' ...' if len(more) > 12 else '')))
        else:
            lines.append('  ' + short(res, 400))
        return {'text': '\n'.join(lines) + '\n', 'json': {'record_id': rid, 'result': res}}


class ImagingAdapter(SampleAdapter):
    CLASS = 'imaging'
    TITLE = 'microscopy and imaging'
    ABOUT = ('Microscopes and imagers (SEM, TEM, laser profilometer, polarizing microscope, AFM, STM, micro-CT, gel imager): load a '
             'specimen, move the stage, focus, set the magnification, switch the beam / X-rays / light, take an image or scan '
             '(job-N) and read the record; the same words on every member, lowered to its own capabilities.')
    STANDARDS = ('Micro-Manager Core API (setXYPosition / setPosition / snapImage / setExposure); OME data model for image records; '
                 'ISO 16700 and ASTM E766 (SEM magnification); ISO 25178 (areal roughness)')
    UNIT_KINDS = ('length', 'duration', 'voltage', 'current', 'frequency', 'force')
    QOPTS = {'dwell': ('duration',), 'setpoint': ('force', 'length')}
    PATH_GROUPS = (('acquire', 'result'),)
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on (`chemlab call {inst} power.set --enabled true`, or `chemlab run {inst} power on`).'),
        'NOT_READY': ('NOT_READY', '{inst} is not ready (still starting up, or busy).', 'wait a little (`chemlab wait 30`) and retry; see `chemlab run {inst} status`.'),
        'ERROR_PENDING': ('ERROR_PENDING', '{inst} has an error waiting to be acknowledged.', 'read it (`chemlab run {inst} status`), then acknowledge it (`chemlab describe {inst}`: error.acknowledge / system.acknowledge).'),
        'ACQUISITION_RUNNING': ('BUSY', '{inst} is acquiring now.', 'wait for it (`chemlab status`) or cancel your job first.'),
        'VACUUM_NOT_READY': ('VACUUM_NOT_READY', 'the chamber of {inst} is not under vacuum yet.', 'pump it down: `chemlab run {inst} vacuum pump --wait`.'),
        'BEAM_NOT_READY': ('BEAM_NOT_READY', 'the beam of {inst} is not ready (not switched on, or the filament is still heating).', '`chemlab run {inst} source on --wait`.'),
        'DOOR_NOT_OPEN': ('DOOR_NOT_OPEN', 'the specimen drawer / door of {inst} is not open.', '`chemlab run {inst} load SAMPLE` opens it as part of loading.'),
        'DOOR_OPEN': ('DOOR_OPEN', 'the door of {inst} is open.', 'close it (load / unload close it when they are done).'),
        'CHAMBER_UNDER_VACUUM': ('NOT_VENTED', 'the chamber of {inst} is under vacuum, so it cannot be opened.', 'vent it first: `chemlab run {inst} vacuum vent --wait` (switch the beam off before: `source off`).'),
        'SPECIMEN_NOT_ON_BENCH': ('NO_SUCH_SAMPLE', 'that specimen is not on the bench next to {inst}.', 'see the specimens on the bench: `chemlab read {inst} specimen.bench_ids sample.bench_ids`.'),
        'UNKNOWN_SPECIMEN': ('NO_SUCH_SAMPLE', '{inst} has no specimen of that name on its bench.', 'see `chemlab read {inst}` (bench ids) for the specimens there.'),
        'UNKNOWN_SAMPLE': ('NO_SUCH_SAMPLE', '{inst} has no sample of that name on its bench.', 'see `chemlab read {inst}` (bench ids) for the samples there.'),
        'SAMPLE_NOT_ON_BENCH': ('NO_SUCH_SAMPLE', 'that sample is not on the bench next to {inst}.', 'see `chemlab read {inst} sample.bench_ids`.'),
        'HOLDER_OCCUPIED': ('OCCUPIED', 'the specimen holder of {inst} already holds a specimen.', 'unload it first: `chemlab run {inst} unload`.'),
        'STAGE_OCCUPIED': ('OCCUPIED', 'the stage of {inst} already holds a sample.', 'unload it first: `chemlab run {inst} unload`.'),
        'NO_SPECIMEN': ('NO_SAMPLE', 'there is no specimen in {inst}.', 'load one: `chemlab run {inst} load SAMPLE`.'),
        'NO_SAMPLE': ('NO_SAMPLE', 'there is no sample in {inst}.', 'load one: `chemlab run {inst} load SAMPLE`.'),
        'RECORDS_FULL': ('RECORDS_FULL', 'the record store of {inst} is full.', 'delete old records (`chemlab describe {inst}`: records.delete).'),
        'UNKNOWN_RECORD': ('NO_SUCH_RECORD', '{inst} has no record of that id for you.', 'list them: `chemlab call {inst} records.list`.'),
        'STAGE_COLLISION_RISK': ('COLLISION_RISK', 'that move could bring the specimen too close to the lens of {inst}.', 'lower the stage first or choose another position.'),
        'INTERLOCK_OPEN': ('INTERLOCK_OPEN', 'the safety interlock of {inst} is open (door open or light curtain).', 'close the door; then retry.'),
        'MEASUREMENT_RUNNING': ('BUSY', '{inst} is measuring or running an automatic operation now.', 'wait for it (`chemlab status`, `chemlab run {inst} status`), then retry.'),
        'MOTION_IN_PROGRESS': ('BUSY', 'a motor of {inst} is still moving.', 'wait a few seconds (`chemlab wait 5`) and retry.'),
        'XRAY_ON': ('SOURCE_ON', 'the X-rays of {inst} are on, so the door stays locked.', 'switch them off first: `chemlab run {inst} source off`.'),
        'SAMPLE_NOT_IN_FIELD': ('NOT_IN_FIELD', 'the sample is not inside the detector field of view of {inst} at this magnification.', 'lower the magnification (e.g. `chemlab run {inst} zoom 2x`) and retry.'),
        'FLAT_FIELD_REQUIRED': ('NOT_CALIBRATED', '{inst} has no flat-field references for the current settings.', '`chemlab run {inst} acquire` takes them first; retry it.'),
        'NO_SURFACE_FOUND': ('NO_SURFACE', '{inst} found no surface within its search range around the current focus.', 'bring the surface near focus first (`chemlab run {inst} focus Z`), then retry.'),
        'LENS_NOT_SUPPORTED': ('LENS_NOT_FOR_MEASURING', 'the objective in use on {inst} cannot do this measurement.', 'choose a measuring objective: `chemlab run {inst} zoom --objective 10x`.'),
        'NOT_ENGAGED': ('NOT_ENGAGED', 'the probe tip of {inst} is not on the surface.', 'approach first: `chemlab run {inst} approach --wait`.'),
        'NO_PROBE': ('NO_PROBE', 'there is no probe (cantilever) on {inst}.', 'ask the lab operator to mount one (set-up work).'),
        'DRIVE_NOT_SET': ('NOT_SET_UP', 'the cantilever drive of {inst} is not set up for its dynamic mode.', 'set it up with the operator, or use contact mode.'),
        'HOLDER_NOT_IN_HEAD': ('NO_SAMPLE', 'the sample holder is not in the scan head of {inst}.', 'load a sample: `chemlab run {inst} load SAMPLE`.'),
        'COVER_ON': ('COVER_ON', 'the magnifier cover of {inst} is on.', '`chemlab run {inst} load` / `unload` lift it as needed.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab (no environment declared by the lab).', 'ask the lab operator.'),
    }

    def build_verbs(self):
        R = None                               # manual pages are per member: the 'manual:' notes of help INST VERB
        length = lambda name, h: Arg(name, h, kind='length')   # noqa: E731
        return {
            'load': Verb('load', 'put a specimen from the bench into the instrument (door / airlock / holder as needed)',
                         self.h_generic('load', 'loaded {sample}', lambda c, p: {'sample': p.pos[0]}),
                         positionals=[Arg('SAMPLE', 'the specimen id on the bench (see `chemlab read INST` bench ids)')],
                         options=dict({'at': Opt('where on the stage / holder, X,Y (e.g. 2mm,-1mm)', metavar='X,Y'),
                                       'holder': Opt('which holder / support (member specific)', metavar='H')}, **wait_opts()),
                         preconditions=['the specimen is on the bench', 'the holder / stage is free', 'the chamber is vented (SEM)'],
                         errors=['NO_SUCH_SAMPLE, OCCUPIED, NOT_VENTED (refused)', 'NOT_REACHED (refused: a door or airlock did not get there in time)'],
                         example='chemlab run INST load fe_si_polished', reference=R),
            'unload': Verb('unload', 'take the specimen out and put it back on the bench', self.h_generic('unload', 'unloaded {sample}', self.env_unload),
                           positionals=[Arg('SAMPLE', 'the specimen id (default: the one in the instrument)', required=False)],
                           options=wait_opts(), example='chemlab run INST unload', reference=R),
            'move': Verb('move', 'move the stage to X Y and / or the height --z (lengths; a bare number is mm); waits until it stops',
                         self.h_generic('move', 'stage move', self.env_move),
                         positionals=[Arg('X', 'x position, e.g. 2mm or 150um', required=False, kind='length'),
                                      Arg('Y', 'y position', required=False, kind='length')],
                         options=dict({'z': Opt('stage height (member specific: SEM Z lift, TEM Z, CT chuck height)', kind='length', metavar='Z')},
                                      **wait_opts('5min')),
                         preconditions=['positions within the stage travel (contract)'],
                         errors=['OUT_OF_RANGE, MISSING_ARGUMENT (usage)'], example='chemlab run INST move 2mm -1mm', reference=R),
            'focus': Verb('focus', 'autofocus (`focus auto`) or set the focus coordinate Z (working distance, defocus, head / stage Z)',
                          self.h_focus, positionals=[Arg('auto|Z', 'auto, or a length (e.g. 10mm, -2um)')], options=wait_opts('5min'),
                          errors=['OUT_OF_RANGE (usage)', 'BEAM_NOT_READY / NO_SAMPLE (refused)'], example='chemlab run INST focus auto', reference=R),
            'zoom': Verb('zoom', 'set the magnification (e.g. 2000x) or choose an objective (--objective N)', self.h_zoom,
                         positionals=[Arg('MAG', 'magnification, e.g. 2000x or 2000 (omit with --objective)', required=False)],
                         options={'objective': Opt('objective / lens position, or its magnification as 10x', metavar='N')},
                         errors=['OUT_OF_RANGE (usage)'], example='chemlab run INST zoom 2000x', reference=R),
            'source': Verb('source', 'switch the beam / high tension / X-rays / illumination / laser on or off', self.h_onoff('source', 'source'),
                           positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                           options=dict({'kv': Opt('accelerating / tube voltage, e.g. 15kV', kind='voltage', metavar='V'),
                                         'current': Opt('beam / tube current, e.g. 100uA', kind='current', metavar='I'),
                                         'level': Opt('illumination level, e.g. 60%', kind='fraction', metavar='F')}, **wait_opts()),
                           preconditions=['vacuum ready (electron microscopes)', 'door closed (X-ray)'],
                           errors=['VACUUM_NOT_READY, DOOR_OPEN (refused)', 'OUT_OF_RANGE (usage)'], example='chemlab run INST source on --wait', reference=R),
            'acquire': Verb('acquire', 'take an image / scan / tomogram: job-N, or --wait', self.h_generic('acquire', 'acquisition'),
                            options=dict({'pixels': Opt('frame size, e.g. 640x480 (SEM) or 512 (scan lines)', metavar='P'),
                                          'dwell': Opt('dwell time per pixel, e.g. 20us (us, ms or s)', metavar='T'),
                                          'exposure': Opt('exposure time, e.g. 1s', kind='duration', metavar='T'),
                                          'binning': Opt('camera binning, e.g. 2', metavar='N'),
                                          'size': Opt('scan size (field of view), e.g. 5um', kind='length', metavar='L'),
                                          'rate': Opt('line rate, e.g. 1Hz', kind='frequency', metavar='F'),
                                          'line-time': Opt('time per line, e.g. 0.5s', kind='duration', metavar='T'),
                                          'points': Opt('points per line', metavar='N'),
                                          'hdr': Opt('high dynamic range capture', kind='flag'),
                                          'mode': Opt('imaging mode / preset (member specific, e.g. nucleic_acid_gels on the gel imager)', metavar='M')},
                                         **wait_opts('1h')),
                            long_action=True, preconditions=['a specimen in place', 'source on and ready', 'vacuum ready (electron microscopes)'],
                            errors=['BEAM_NOT_READY, VACUUM_NOT_READY, NO_SAMPLE, NOT_ENGAGED (refused)', 'OPTION_NOT_USED (usage)'],
                            example='chemlab run INST acquire --wait', reference=R),
            'result': Verb('result', 'the image / scan record (default: your last one)', self.h_result, readonly=True,
                           positionals=[Arg('ID', 'record id (default: the last one)', required=False)], options={'save-to': save_to_opt()},
                           example='chemlab run INST result --save-to image.json'),
            'vacuum': Verb('vacuum', 'pump the chamber down or vent it', self.h_onoff('vacuum', 'chamber'),
                           positionals=[Arg('pump|vent', 'pump or vent', choices=['pump', 'vent'])], options=wait_opts(),
                           preconditions=['the beam off before venting', 'the door closed before pumping'], example='chemlab run INST vacuum pump --wait', reference=R),
            'approach': Verb('approach', 'bring the probe tip into contact / tunnelling range (scanning-probe microscopes): job-N, or --wait',
                             self.h_generic('approach', 'tip approach'),
                             options=dict({'setpoint': Opt('feedback set point: a force in contact mode (e.g. 2nN), an amplitude in tapping mode (e.g. 20nm)',
                                                           metavar='S')}, **wait_opts()),
                             long_action=True, example='chemlab run INST approach --wait', reference=R),
            'retract': Verb('retract', 'pull the probe tip away from the sample', self.h_generic('retract', 'tip retracted'),
                            options=dict({'distance': Opt('how far, e.g. 1mm', kind='length', metavar='L')}, **wait_opts()),
                            example='chemlab run INST retract', reference=R),
        }

    # ------------------------------------------------------------------ input helpers
    def env_unload(self, ctx, p):
        if p.pos:
            return {'sample': p.pos[0]}
        s = self.slot('sample')
        v = self.slot_value(ctx, s) if s else None
        return {'sample': v} if v else {}

    def env_move(self, ctx, p):
        if len(p.pos) == 1:
            ctx.fail(invalid('MISSING_ARGUMENT', 'move needs both X and Y (or only --z).', 'usage: %s' % ctx.verb.usage(self.instrument_id)))
        if not p.pos and p.get('z') is None:
            ctx.fail(invalid('MISSING_ARGUMENT', 'move needs X Y, --z Z, or both.', 'usage: %s' % ctx.verb.usage(self.instrument_id)))
        return {'x': Q(p.pos[0], 'length'), 'y': Q(p.pos[1], 'length')} if p.pos else {}

    def env_from(self, ctx, p, extra=None):
        env = SampleAdapter.env_from(self, ctx, p, extra)
        at = p.get('at') if ctx.verb.name in ('load',) else None
        if at:
            parts = [x for x in at.replace(';', ',').split(',') if x.strip()]
            if len(parts) != 2:
                ctx.fail(invalid('BAD_POSITION', '--at %s is not X,Y.' % at, 'write it like --at 2mm,-1mm.'))
            env['opt:at_x'], env['opt:at_y'] = Q(units.parse(parts[0], 'length', what='X'), 'length'), Q(units.parse(parts[1], 'length', what='Y'), 'length')
            env.pop('opt:at', None)
        return env

    def h_focus(self, ctx, p):
        text = p.raw['auto|Z'].strip().lower()
        m = self.vmap('focus') or {}
        if text == 'auto':
            sub, what = m.get('auto'), 'autofocus'
            if sub is None:
                ctx.fail(invalid('NO_AUTOFOCUS', '%s has no autofocus.' % self.instrument_id, (m.get('why_auto') or 'give the focus position as a length, e.g. `focus 2mm`.')))
            env = self.env_from(ctx, p)
        else:
            sub, what = m.get('set'), 'focus %s' % text
            if sub is None:
                ctx.fail(invalid('NO_MANUAL_FOCUS', '%s has no manual focus setting from the computer.' % self.instrument_id, m.get('why_set') or 'use `focus auto`.'))
            env = self.env_from(ctx, p, {'value': Q(units.parse(text, 'length', what='Z'), 'length')})
        lines, js, mm = self.run_mapping(ctx, p, 'focus', env, what, m=sub)
        if ctx.dry_run:
            return {'text': '', 'json': js}
        return {'text': '\n'.join(['ok: %s %s' % (self.instrument_id, self.head_text(ctx, sub, js, what, env))] + self.waited_lines(ctx) + lines
                                   + [self.now_line(ctx)]) + '\n', 'json': js}

    def h_zoom(self, ctx, p):
        m = self.vmap('zoom') or {}
        obj = p.get('objective')
        if obj is not None:
            sub = m.get('objective')
            if sub is None:
                ctx.fail(invalid('NO_OBJECTIVES', '%s has no objective turret.' % self.instrument_id, m.get('why_objective') or 'give a magnification: `zoom 2000x`.'))
            table = sub.get('table') or {}
            key = obj.strip().lower().rstrip('x') if obj.strip().lower().endswith('x') else None
            if key is not None:
                hit = [v for k, v in table.items() if k.rstrip('x') == key or (k.rstrip('x').replace('.', '', 1).isdigit() and key.replace('.', '', 1).isdigit()
                                                                                 and abs(float(k.rstrip('x')) - float(key)) < 1e-9)]
                if not hit:
                    ctx.fail(invalid('NO_SUCH_OBJECTIVE', '%s has no %s objective.' % (self.instrument_id, obj), 'objectives: %s.' % ', '.join(table)))
                pos = hit[0]
            else:
                try:
                    pos = int(obj)
                except ValueError:
                    ctx.fail(invalid('BAD_OBJECTIVE', '--objective %s is neither a position nor a magnification like 10x.' % obj,
                                     'objectives: %s.' % ', '.join('%s (position %s)' % kv for kv in table.items())))
            env = self.env_from(ctx, p, {'value': pos})
            env.pop('opt:objective', None)
            what = 'objective %s' % obj
        else:
            if not p.pos:
                ctx.fail(invalid('MISSING_ARGUMENT', 'zoom needs MAG or --objective N.', 'usage: %s' % ctx.verb.usage(self.instrument_id)))
            sub = m.get('mag')
            if sub is None:
                ctx.fail(invalid('NO_MAGNIFICATION', '%s has no magnification setting.' % self.instrument_id, m.get('why_mag') or 'choose an objective: `zoom --objective 10x`.'))
            t = p.pos[0].strip().lower().rstrip('x').rstrip('×')
            try:
                mag = float(t)
            except ValueError:
                ctx.fail(invalid('BAD_NUMBER', '%r is not a magnification.' % p.pos[0], 'write it like 2000x or 2000.'))
            env = self.env_from(ctx, p, {'value': mag})
            what = 'magnification %gx' % mag
        lines, js, mm = self.run_mapping(ctx, p, 'zoom', env, what, m=sub)
        if ctx.dry_run:
            return {'text': '', 'json': js}
        return {'text': '\n'.join(['ok: %s %s' % (self.instrument_id, what)] + lines + [self.now_line(ctx)]) + '\n', 'json': js}

    def check_options(self, ctx, p, verb, m):
        if verb == 'zoom':
            return
        SampleAdapter.check_options(self, ctx, p, verb, m)


_ = (copy, json, CompileError, unexpected)

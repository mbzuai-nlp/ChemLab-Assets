"""Thermal analysis and calorimetry class ("thermal_analysis"): DSC, TGA, DMA, TMA, laser flash, transient plane source,
isothermal microcalorimeter, adiabatic (ARC) calorimeter, oxygen bomb calorimeter.  Operator action: run a temperature
programme on a specimen and record a thermal signal.

Verbs: load [WHERE] [--preload F], unload [WHERE], open|close [PART] (the shared verb plus the wait for the lid / furnace /
cover to finish moving), method KEY=VALUE ..., set-temp T|off [--rate R], temp, wait-temp, gas [GAS] on|off|FLOW [--balance FLOW]
[--channel CH], start (job-N), stop, result [--save-to F], analyze [MODEL] KEY=VALUE ... [--save-to F], zero, calibrate
KEY=VALUE ..., status.  power, estop, ack and wait
come from the shared layer (configs/adapters/shared.json).  Members and mappings: configs/adapters/thermal_analysis.json.

Vendor-neutral terms: ISO 11357 / ASTM E967, E968 (DSC: segments, purge, start temperature), ASTM E1131 / ISO 11358 (TGA: tare,
purge and balance gas), ASTM E1640 / ISO 6721 (DMA: preload, frequency, strain amplitude), ASTM E831 (TMA: probe force, CTE),
ASTM E1461 (flash diffusivity: pulse, detector, Parker), ISO 22007-2 (transient plane source: heating power, measuring time),
ASTM E1981 (ARC heat-wait-search), ISO 1928 / ASTM D5865 (bomb calorimetry: vessel, C value, gross calorific value).  The TA
Instruments TRIOS REST API is not public, so the verbs follow the standards' vocabulary (manual manifests: page references only).

Method keys of the class (each member maps the ones it has; a native argument name passes through, parsed into the unit the
argument declares; settings not given come from the member's defaults - its own state, or the contract's documented example
method - and the reply lists them):

    start=25C                  start / equilibration temperature       ramp=300C@10K/min[:5min]  one programme segment
    hold=5min                  hold at the end of the ramp before       iso=10min                 an isothermal segment
    gas=N2                     purge gas of the segments that follow    force=0.05N               probe force (TMA segments)
    step=30C / steps=30C..150C/10K   DMA temperature blocks             freq=1Hz strain=0.01% control=strain|force
    interval=1s  tolerance=0.5K  timeout=2h  rate=10K/min  temp=50C  time=40s  power=0.5W  energy=0.1J  ...
    (C 6000: name= mass=1g kind=measurement|calibration vessel=NAME aid=NAME[:MASS] mode=isoperibol_25)

The tester machinery (TATester) is shared with the electrical class (classes/electrical.py).
"""
import copy
import json
import math
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import CompileError, invalid, refused, unexpected, unsupported
from ..jobs import RUNNING, OperationJob
from ..verbs import Arg, Opt, Verb, save_to_opt, target_opt
from ...contract import validate
from ...generic import coerce

ON_OFF = ['on', 'off']
RUN_PHASES = ('running', 'measuring', 'equilibrating', 'baseline', 'pulse', 'recording', 'heat', 'wait', 'search', 'exotherm', 'iso')
_NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'

# display of the framework kinds this class shows its own way (the units themselves are framework units, units.KINDS)
EXTRA_SHOW = {'field_rate': (1e-3, 'mT/s'), 'force_rate': (1.0, 'N/s'), 'resistance': (1.0, 'ohm'),
              'sheet_resistance': (1.0, 'ohm/sq'), 'moment': (1e-3, 'emu'), 'heat_capacity': (1.0, 'J/K'), 'number': (1.0, '')}
TRUE, FALSE = ('true', 'yes', 'on', '1', 'y'), ('false', 'no', 'off', '0', 'n')


# ------------------------------------------------------------------ quantities and schemas
def parse_q(text, kind, what='value'):
    """Agent text -> SI (units.parse; field_rate, force_rate, resistance, sheet_resistance, moment, heat_capacity and number are
    framework kinds; an unknown kind is a plain number)."""
    return units.parse(text, kind if kind in units.KINDS else 'number', what=what)


def fmt_q(v, kind, decimals=None):
    if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        return 'n/a' if v is None else str(v)
    if kind in EXTRA_SHOW:
        f, u = EXTRA_SHOW[kind]
        return ('%.4g %s' % (v / f, u)).strip()
    if kind in units.KINDS:
        return units.fmt(v, kind, decimals)
    return '%.4g' % v


def kind_example(kind):
    return units.example(kind if kind in units.KINDS else 'number')


def nonnull(schema):
    """The non-null branch of a schema (anyOf) and whether null is allowed."""
    schema = schema or {}
    if 'anyOf' in schema:
        br = [b for b in schema['anyOf'] if isinstance(b, dict)]
        return next((b for b in br if b.get('type') != 'null'), {}), any(b.get('type') == 'null' for b in br)
    return schema, schema.get('type') == 'null'


def types_of(schema):
    t = (schema or {}).get('type')
    return set(t if isinstance(t, list) else [t] if t else [])


def _declared_kind(schema):
    u = units.declared(schema)
    return units.unit_kind(u) if u else None


def friendly_range(schema, kind):
    """'from 93.15 to 998.15 K' in agent units."""
    lo, hi = schema.get('minimum'), schema.get('maximum')
    u = units.declared(schema)

    def f(x):
        if x is None:
            return None
        if kind in EXTRA_SHOW:
            return fmt_q(x, kind)
        if kind in units.KINDS and u and units.unit_kind(u) == kind:
            return fmt_q(units.from_declared(x, u), kind)
        if kind in units.KINDS and kind == 'temperature_delta' and u in ('K', '°C'):
            return fmt_q(x, kind)
        return '%g%s' % (x, (' ' + u) if u else '')
    a, b = f(lo), f(hi)
    if a is not None and b is not None:
        return '%s to %s' % (a, b)
    return ('at least %s' % a) if a is not None else ('at most %s' % b) if b is not None else 'any value'


def to_native(text, schema, kind=None, what='value'):
    """Agent text -> the JSON value the argument takes, in the unit it declares; enum, type and range are checked here (usage
    errors before anything is sent).  ``kind``: the quantity the agent types (class keys); None = a native argument name (a
    bare number is in the declared unit; a number with a unit is converted)."""
    s, nullable = nonnull(schema)
    t = (text if isinstance(text, str) else json.dumps(text)).strip()
    if nullable and t.lower() in ('none', 'null'):
        return None
    typ = types_of(s)
    if 'boolean' in typ and not ({'number', 'integer'} & typ):
        if t.lower() in TRUE:
            return True
        if t.lower() in FALSE:
            return False
        raise invalid('BAD_VALUE', '%s takes true or false, not %r.' % (what, t), 'write %s=true or %s=false.' % (what, what))
    if typ <= {'string'} and typ:
        enum = s.get('enum')
        if enum:
            for e in enum:
                if str(e).lower() == t.lower():
                    return e
            raise invalid('BAD_CHOICE', '%s %r is not one of: %s.' % (what, t, ', '.join(map(str, enum))), 'use one of: %s.' % ', '.join(map(str, enum)))
        return t
    if not ({'number', 'integer'} & typ):
        return coerce(t, s, what)
    u = units.declared(s)
    bare = re.fullmatch(_NUM, t) is not None
    if kind in EXTRA_SHOW:                                              # class kinds in SI = the unit their contracts declare
        v = parse_q(t, kind, what)
        if u and units.unit_kind(u) not in (None, kind):
            raise unexpected('UNIT_MISMATCH', '%s declares %s, not a %s.' % (what, u, kind.replace('_', ' ')), 'report this; nothing was sent.')
        if u and units.unit_kind(u) == kind:
            v = units.to_declared(v, u)
    elif kind:
        if u and units.unit_kind(u) in (kind, 'temperature' if kind == 'temperature_delta' else None):
            v = units.parse_for(t, s, kind, what=what)
        else:
            v = parse_q(t, kind, what)
    elif bare:
        v = float(t)
    elif u and units.unit_kind(u):
        v = units.parse_for(t, s, None, what=what)
    else:
        m = re.fullmatch('(%s)(.*)' % _NUM, t.replace(' ', ''))
        if not m or (m.group(2) and units.norm_unit(m.group(2)) != u):
            raise invalid('BAD_NUMBER', '%s %r is not a number%s.' % (what, t, ' in %s' % u if u else ''),
                          'write a number%s.' % (' in %s' % u if u else ''))
        v = float(m.group(1))
    if 'integer' in typ and 'number' not in typ:
        if abs(v - round(v)) > 1e-9:
            raise invalid('NOT_AN_INTEGER', '%s must be a whole number, not %s.' % (what, t), 'give a whole number.')
        v = int(round(v))
    enum = s.get('enum')
    if enum:
        hit = [e for e in enum if isinstance(e, (int, float)) and abs(e - v) <= 1e-9 * max(1.0, abs(e))]
        if not hit:
            shown = ', '.join(fmt_q(units.from_declared(e, u), kind) if kind in units.KINDS and u else '%g' % e for e in enum)
            raise invalid('BAD_CHOICE', '%s %s is not one of the settings the instrument has.' % (what, t), 'use one of: %s.' % shown)
        v = hit[0]
    lo, hi = s.get('minimum'), s.get('maximum')
    if (lo is not None and v < lo - 1e-12 * max(1.0, abs(lo))) or (hi is not None and v > hi + 1e-12 * max(1.0, abs(hi))):
        raise invalid('OUT_OF_RANGE', '%s %s is outside the range of the instrument (%s).' % (what, t, friendly_range(s, kind or _declared_kind(s))),
                      'give a value from %s.' % friendly_range(s, kind or _declared_kind(s)))
    return v


def _get(obj, path):
    cur = obj
    for part in path.split('.'):
        if isinstance(cur, dict):
            cur = cur.get(part)
        elif isinstance(cur, list) and re.fullmatch(r'-?\d+', part):
            i = int(part)
            cur = cur[i] if -len(cur) <= i < len(cur) else None
        else:
            return None
    return cur


def _num_list(rows, key):
    return [r.get(key) for r in rows if isinstance(r, dict) and finite(r.get(key))]


class TATester(ClassAdapter):
    """The tester machinery of the thermal_analysis and electrical classes: KEY=VALUE settings with class keys, programmes and
    defaults; start / stop / result with jobs; set-temp / temp / wait-temp; short mechanical waits; the class view from slot roles."""
    PATH_GROUPS = (('start', 'stop', 'result'),)
    ROLE_VIEW = ()                     # (role, label, kind) rows of the class view; kind: a quantity kind, 'onoff', 'yesno', 'open', 'text'
    KEY_HELP = {}                      # class key -> help (help INST method)
    CODES = {}

    # ------------------------------------------------------------------ contract helpers
    def example(self, cap):
        return copy.deepcopy((self.lab.contract['capabilities'].get(cap) or {}).get('example_arguments') or {})

    def props(self, cap):
        return ((self.lab.contract['capabilities'].get(cap) or {}).get('input_schema') or {}).get('properties') or {}

    def required(self, cap):
        return list(((self.lab.contract['capabilities'].get(cap) or {}).get('input_schema') or {}).get('required') or [])

    def item_schema(self, cap, arg):
        s, _ = nonnull(self.props(cap).get(arg) or {})
        it = s.get('items') or {}
        return it, (it.get('properties') or {})

    # ------------------------------------------------------------------ member requirements (with comparisons)
    def need(self, ctx, verb):
        """"requires": {verb: [{"slot", "value" | "not" | "truthy" | "falsy" | "min" | "max" | "in", "code", "message", "fix",
        "label"}]} - checked before anything is sent; message / fix may use {inst} and {value}."""
        for r in (self.map.get('requires') or {}).get(verb, []):
            got = ctx.v(r['slot'])
            if r.get('key') and isinstance(got, dict):
                got = got.get(r['key'])
            if 'value' in r:
                ok = got == r['value']
            elif 'not' in r:
                ok = got not in r['not']
            elif 'in' in r:
                ok = got in r['in']
            elif 'in_slot' in r:
                ok = got in (ctx.v(r['in_slot']) or [])
            elif r.get('truthy'):
                ok = bool(got)
            elif r.get('falsy'):
                ok = not got
            else:
                ok = finite(got) and ('min' not in r or got >= r['min']) and ('max' not in r or got <= r['max'])
            shown = ('%.4g' % got) if isinstance(got, float) else got
            ctx.check(ok, r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=shown),
                      r['fix'].format(inst=self.instrument_id, value=shown), label=r.get('label', r['slot']))

    def role(self, values, role):
        spec = self.slot(role)
        if spec is None:
            return None
        if isinstance(spec, dict):
            spec = spec.get('slot')
        sid, _, key = spec.partition('#')
        v = values.v(sid) if hasattr(values, 'v') else values.get(sid)
        if key and isinstance(v, dict):
            v = v.get(key)
        return v

    def role_si(self, values, role):
        spec = self.slot(role)
        return self.slot_si(values, spec) if spec else None

    def targets_for(self, ctx, p, verb):
        """--zone / --channel / --cell / --head of this verb (a target applies to the verbs in its "verbs" list, default all)."""
        out = {}
        for k, t in (self.map.get('targets') or {}).items():
            if t.get('verbs') and verb not in t['verbs']:
                continue
            v = p.get(k)
            if v is None:
                if t.get('required'):
                    ctx.fail(invalid('MISSING_' + k.upper(), '%s needs --%s on %s.' % (verb, k, self.instrument_id),
                                     'give --%s %s.' % (k, '|'.join(map(str, (t.get('values') or {}))) or k.upper())))
                continue
            vals = t.get('values')
            if vals is not None and str(v) not in vals:
                ctx.fail(invalid('UNKNOWN_' + k.upper(), '%s has no %s %r.' % (self.instrument_id, k, v),
                                 '%ss of %s: %s.' % (k, self.instrument_id, ', '.join(map(str, vals)))))
            out[t['arg']] = vals[str(v)] if vals is not None else v
        return out

    def tgt_opts(self, verb):
        return {k: target_opt(k, sorted((t.get('values') or {}).keys()) or None) for k, t in (self.map.get('targets') or {}).items()
                if not t.get('verbs') or verb in t['verbs']}

    def running(self, values):
        if self.slot('busy'):
            return bool(self.role(values, 'busy'))
        ph = self.role(values, 'phase')
        if isinstance(ph, bool):
            return ph
        return ph in RUN_PHASES or ph in RUNNING

    def settle(self, ctx, test, what, max_s=300.0, poll_s=1.0):
        """Let lab time pass until ``test(ctx)`` holds (the framework's wait_until; moving_done is the framework's too)."""
        return self.wait_until(ctx, test, what, max_s=max_s, poll_s=poll_s)

    # ------------------------------------------------------------------ settings (method, calibrate, contact check)
    def settings_help(self, spec):
        keys = sorted(set((spec or {}).get('keys') or {}))
        return keys

    def build_calls(self, ctx, spec, items, verb):
        """KEY=VALUE items -> [(cap, args, defaults_used)] for the calls of a settings spec (one call or "calls": [...])."""
        specs = spec.get('calls') or [spec]
        parsed = [self._pairs(ctx, items, verb)]
        out = []
        claimed = set()
        for sp in specs:
            mine = self._claim(sp)
            claimed |= mine
        unknown = [k for k, _ in parsed[0] if k not in claimed and not any(k in self.props(sp['cap']) and sp.get('passthrough', True) for sp in specs)]
        if unknown:
            allk = sorted(claimed | {a for sp in specs if sp.get('passthrough', True) for a in self.props(sp['cap'])})
            ctx.fail(invalid('UNKNOWN_SETTING', '%s has no %s setting %r.' % (self.instrument_id, verb, unknown[0]),
                             'settings: %s (see `chemlab help %s %s`).' % (', '.join(allk), self.instrument_id, verb)))
        for sp in specs:
            mine = self._claim(sp) | (set(self.props(sp['cap'])) if sp.get('passthrough', True) else set())
            its = [(k, v) for k, v in parsed[0] if k in mine]
            if not its and not sp.get('always') and len(specs) > 1:
                continue
            out.append(self._one_call(ctx, sp, its, verb))
        if not out:
            ctx.fail(invalid('MISSING_SETTING', '%s needs at least one KEY=VALUE.' % verb, 'see `chemlab help %s %s`.' % (self.instrument_id, verb)))
        return out

    @staticmethod
    def _claim(sp):
        keys = set(sp.get('keys') or {})
        pg = sp.get('program') or {}
        for k in ('key', 'range_key', 'iso', 'hold_key'):
            if pg.get(k):
                keys.add(pg[k])
        keys |= set(pg.get('sticky') or {})
        return keys

    def _pairs(self, ctx, items, verb):
        out = []
        for item in items:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write %s KEY=VALUE ... (see `chemlab help %s %s`).' % (verb, self.instrument_id, verb)))
            out.append((k.strip(), v.strip()))
        return out

    def _one_call(self, ctx, sp, items, verb):
        cap = sp['cap']
        props = self.props(cap)
        keys = sp.get('keys') or {}
        pg = sp.get('program') or {}
        args, given, defaults = {}, set(), []
        segs, sticky_now, trailing = [], {}, {}
        isch, iprops = self.item_schema(cap, pg['arg']) if pg else ({}, {})

        def field(name):
            return (pg.get('fields') or {}).get(name) or {}

        def item_val(fspec, text, label):
            return to_native(text, iprops.get(fspec['field']) or {}, fspec.get('kind'), label)

        def new_seg(seg):
            for f, val in sticky_now.items():
                seg.setdefault(f, val)
            segs.append(seg)
            trailing.clear()

        for k, v in items:
            if pg and k == pg.get('key'):
                if pg.get('style') == 'ramp':
                    m = re.fullmatch(r'([^@]+)@([^:]+)(?::(.+))?', v.replace(' ', ''))
                    if not m:
                        ctx.fail(invalid('BAD_SETTING', '%s=%s is not TARGET@RATE[:HOLD].' % (k, v), 'write e.g. %s=300C@10K/min:5min.' % k))
                    seg = {field('target')['field']: item_val(field('target'), m.group(1), 'target'),
                           field('rate')['field']: item_val(field('rate'), m.group(2), 'rate')}
                    if field('hold'):
                        seg[field('hold')['field']] = item_val(field('hold'), m.group(3), 'hold') if m.group(3) else 0.0
                    new_seg(seg)
                else:
                    new_seg({field('target')['field']: item_val(field('target'), v, k)})
            elif pg and k == pg.get('range_key'):
                m = re.fullmatch(r'([^.]+)\.\.([^/]+)/(.+)', v.replace(' ', ''))
                if not m:
                    ctx.fail(invalid('BAD_SETTING', '%s=%s is not FROM..TO/STEP.' % (k, v), 'write e.g. %s=30C..150C/10K.' % k))
                a, b = parse_q(m.group(1), 'temperature', 'from'), parse_q(m.group(2), 'temperature', 'to')
                st = abs(parse_q(m.group(3), 'temperature_delta', 'step'))
                if st <= 0:
                    ctx.fail(invalid('BAD_SETTING', 'the step of %s must be above 0.' % k, 'write e.g. %s=30C..150C/10K.' % k))
                n = int(math.floor(abs(b - a) / st + 1e-9)) + 1
                if n > 100:
                    ctx.fail(invalid('TOO_MANY_STEPS', '%s=%s gives %d steps (at most 100).' % (k, v, n), 'use a larger step.'))
                for i in range(n):
                    tk = a + math.copysign(i * st, b - a)
                    new_seg({field('target')['field']: to_native('%.6fK' % tk, iprops.get(field('target')['field']) or {}, 'temperature', 'step')})
            elif pg and k == pg.get('iso'):
                hold = item_val(field('hold'), v, 'hold')
                prev = segs[-1] if segs else None
                tgt = prev[field('target')['field']] if prev else args.get(pg.get('start_arg'))
                if tgt is None:
                    ctx.fail(invalid('BAD_SETTING', '%s= needs a temperature before it (start= or a ramp).' % k, 'give start=T first.'))
                rate = prev.get(field('rate')['field']) if prev else self._item_default(ctx, cap, pg, field('rate')['field'])
                new_seg({field('target')['field']: tgt, field('rate')['field']: rate, field('hold')['field']: hold})
            elif pg and k == pg.get('hold_key'):
                if not segs:
                    ctx.fail(invalid('BAD_SETTING', '%s= follows a %s= segment.' % (k, pg.get('key')), 'write %s=TARGET@RATE %s=TIME.' % (pg.get('key'), k)))
                segs[-1][field('hold')['field']] = item_val(field('hold'), v, 'hold')
            elif pg and k in (pg.get('sticky') or {}):
                fs = pg['sticky'][k]
                val = item_val(fs, v, k)
                sticky_now[fs['field']] = val
                trailing[fs['field']] = val
            elif k in keys and v.lower() in ('default', 'auto') and not (keys[k].get('list') or keys[k].get('record') or keys[k].get('loop')) \
                    and keys[k]['arg'] in (sp.get('defaults') or {}):
                continue                                                    # KEY=default / KEY=auto: the member's default applies
            elif k in keys:
                ks = keys[k]
                arg = ks['arg']
                schema = props.get(arg) or {}
                if ks.get('list') or ks.get('record'):
                    vals = args.setdefault(arg, [])
                    for part in ([v] if ks.get('record') else [x for x in re.split(r'[,;]', v) if x.strip()]):
                        vals.extend(self._list_value(ctx, cap, arg, ks, part.strip(), k))
                elif ks.get('loop'):
                    args[arg] = self._loop(ctx, cap, arg, ks, v, k)
                else:
                    if arg in given:
                        ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % k, 'give it once.'))
                    val = to_native(v, schema, ks.get('kind'), k)
                    if ks.get('scale'):
                        val = val * ks['scale']
                    args[arg] = val
                    for a2, v2 in (ks.get('also') or {}).items():
                        args[a2] = v2
                given.add(arg)
            elif k in props and sp.get('passthrough', True):
                if k in given:
                    ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % k, 'give it once.'))
                args[k] = to_native(v, props[k], None, k)
                given.add(k)
        if pg and segs:
            for f, val in trailing.items():
                for seg in segs:
                    seg.setdefault(f, val)
            req = list(isch.get('required') or [])
            for seg in segs:
                for f in req:
                    if f not in seg:
                        seg[f] = self._item_default(ctx, cap, pg, f)
            if pg['arg'] in given:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice (as %s= and natively).' % (pg['arg'], pg.get('key')), 'give it once.'))
            args[pg['arg']] = segs
            given.add(pg['arg'])
        # defaults for required arguments the agent did not give
        dflt = sp.get('defaults') or {}
        need = sp.get('need') or []
        for k in need:
            arg = (keys.get(k) or {}).get('arg') or (pg['arg'] if pg and k == pg.get('key') else k)
            if arg not in args:
                hint = keys.get(k, {}).get('kind')
                ctx.fail(invalid('MISSING_SETTING', ('%s %s needs %s= on %s.' % (verb, sp.get('what', ''), k, self.instrument_id)).replace('  ', ' '),
                                 'give it, e.g. %s=%s (see `chemlab help %s %s`).' % (k, kind_example(hint) if hint else '...', self.instrument_id, verb)))
        for arg in self.required(cap) + [a for a in dflt if a not in self.required(cap)]:
            if arg in args:
                continue
            d = dflt.get(arg)
            if d is None and arg not in dflt:
                if arg in self.required(cap):
                    key = next((k for k, ks in keys.items() if ks.get('arg') == arg), arg)
                    ctx.fail(invalid('MISSING_SETTING', '%s needs %s= on %s.' % (verb, key, self.instrument_id),
                                     'give it (see `chemlab help %s %s`).' % (self.instrument_id, verb)))
                continue
            val, why = self._default(ctx, cap, arg, d, args, pg)
            args[arg] = val
            defaults.append('%s=%s (%s)' % (arg, short(val, 40), why))
        problems = validate((self.lab.contract['capabilities'][cap].get('input_schema') or {}), args, cap)
        if problems:
            ctx.fail(invalid('INVALID_SETTINGS', 'the %s settings do not fit %s: %s.' % (verb, cap, '; '.join(problems[:4])),
                             'check the values (see `chemlab help %s %s`).' % (self.instrument_id, verb)))
        return cap, args, defaults

    def _list_value(self, ctx, cap, arg, ks, text, key):
        s, _ = nonnull(self.props(cap).get(arg) or {})
        it = s.get('items') or {}
        if ks.get('record'):
            names = ks['record']
            parts = [p.strip() for p in text.split(ks.get('sep', ':'))]
            if len(parts) > len(names):
                ctx.fail(invalid('BAD_SETTING', '%s=%s has too many parts.' % (key, text), 'write %s=%s.' % (key, ks.get('example', ks.get('sep', ':').join(n[0] for n in names)))))
            rec = {}
            ip = it.get('properties') or {}
            for (name, kind), part in zip(names, parts + [None] * (len(names) - len(parts))):
                if part is None or part == '':
                    sch, nullable = nonnull(ip.get(name) or {})
                    if not nullable:
                        ctx.fail(invalid('BAD_SETTING', '%s=%s misses %s.' % (key, text, name), 'write %s=%s.' % (key, ks.get('example', '...'))))
                    rec[name] = None
                else:
                    rec[name] = to_native(part, ip.get(name) or {}, kind, '%s %s' % (key, name))
            return [rec]
        m = re.fullmatch(r'([^.]+)\.\.([^/]+)/(\d+)', text.replace(' ', '')) if ks.get('sweep') else None
        if m:
            a, b, n = parse_q(m.group(1), ks['kind'], key), parse_q(m.group(2), ks['kind'], key), int(m.group(3))
            if n < 2 or n > 1000:
                ctx.fail(invalid('BAD_SETTING', '%s sweep needs 2..1000 points.' % key, 'write e.g. %s=10Hz..100kHz/9.' % key))
            if ks['sweep'] == 'log':
                if a <= 0 or b <= 0:
                    ctx.fail(invalid('BAD_SETTING', 'a log sweep needs values above 0.', 'write e.g. %s=10Hz..100kHz/9.' % key))
                vals = [a * (b / a) ** (i / (n - 1)) for i in range(n)]
            else:
                vals = [a + (b - a) * i / (n - 1) for i in range(n)]
            return [self._si_item(ctx, it, x, ks['kind'], key) for x in vals]
        return [to_native(text, it, ks.get('kind'), key)]

    def _si_item(self, ctx, schema, si, kind, key):
        """An SI value (of ``kind``) -> the item's declared unit, range-checked."""
        u = units.declared(schema)
        val = units.to_declared(si, u) if (u and kind in units.KINDS) else si
        lo, hi = schema.get('minimum'), schema.get('maximum')
        if (lo is not None and val < lo * (1 - 1e-12)) or (hi is not None and val > hi * (1 + 1e-12)):
            ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of the instrument (%s).' % (key, fmt_q(si, kind), friendly_range(schema, kind)),
                             'give values from %s.' % friendly_range(schema, kind)))
        return val

    def _loop(self, ctx, cap, arg, ks, text, key):
        """loop=MAX/STEP -> MAX .. -MAX .. MAX in STEP increments (a hysteresis loop of field points)."""
        m = re.fullmatch(r'([^/]+)/(.+)', text.replace(' ', ''))
        if not m:
            ctx.fail(invalid('BAD_SETTING', '%s=%s is not MAX/STEP.' % (key, text), 'write e.g. %s=0.5T/0.1T.' % key))
        mx, st = abs(parse_q(m.group(1), ks['kind'], key)), abs(parse_q(m.group(2), ks['kind'], key))
        if st <= 0 or mx <= 0 or mx / st > 200:
            ctx.fail(invalid('BAD_SETTING', '%s=%s gives no usable loop (step above 0, at most 200 steps per branch).' % (key, text), 'write e.g. %s=0.5T/0.1T.' % key))
        n = int(round(mx / st))
        down = [mx - i * st for i in range(2 * n + 1)]
        pts = down + [x for x in reversed(down[:-1])]
        s, _ = nonnull(self.props(cap).get(arg) or {})
        it = s.get('items') or {}
        return [self._si_item(ctx, it, round(x, 12), ks['kind'], key) for x in pts]

    def _item_default(self, ctx, cap, pg, f):
        d = (pg.get('item_defaults') or {}).get(f, (pg.get('item_defaults') or {}).get('*'))
        if d == 'example':
            ex = self.example(cap).get(pg['arg']) or [{}]
            if f in ex[0]:
                return ex[0][f]
        elif isinstance(d, dict) and d.get('slot'):
            v = ctx.v(d['slot'])
            if v is not None:
                return v
        elif d is not None:
            return d
        sticky = next((k for k, s in (pg.get('sticky') or {}).items() if s['field'] == f), None)
        ctx.fail(invalid('MISSING_SETTING', 'the %s segments need %s.' % (self.instrument_id, f),
                         'give %s=... before the segments (see `chemlab help %s method`).' % (sticky or f, self.instrument_id)))

    def _default(self, ctx, cap, arg, d, args, pg):
        if d == 'example':
            ex = self.example(cap)
            if arg in ex:
                return ex[arg], 'the contract\'s example'
        if isinstance(d, dict) and d.get('slot'):
            v = ctx.v(d['slot'])
            if d.get('only_key'):
                keys = list(v or {}) if isinstance(v, dict) else []
                if len(keys) == 1:
                    return keys[0], 'the only entry of %s' % d['slot']
                key = next((k for k, ks in (self.vmap(ctx.verb.name if ctx.verb else '') or {}).get('keys', {}).items() if ks.get('arg') == arg), arg)
                ctx.fail(invalid('MISSING_SETTING', '%s has %s entries in %s; say which one.' % (self.instrument_id, len(keys) or 'no', d['slot']),
                                 ('give %s=NAME (one of: %s).' % (key, ', '.join(keys))) if keys else 'register one first (see `chemlab describe %s`).' % self.instrument_id))
            if v is not None or d.get('null_ok'):
                return v, 'kept from %s' % d['slot']
        if d == 'auto_timeout' and pg:
            return self._auto_timeout(cap, pg, args), 'programme time x 1.5 + 30 min'
        if isinstance(d, dict) and 'value' in d:
            return d['value'], d.get('why', 'class default')
        if d not in ('example', 'auto_timeout') and not isinstance(d, dict):
            return d, 'class default'
        ctx.fail(invalid('MISSING_SETTING', '%s needs %s on %s (no default).' % (cap, arg, self.instrument_id), 'give %s=VALUE.' % arg))

    def _auto_timeout(self, cap, pg, args):
        f = pg.get('fields') or {}
        t, tk = 0.0, args.get(pg.get('start_arg'))
        for seg in args.get(pg['arg']) or []:
            target = seg.get(f.get('target', {}).get('field'))
            rate = seg.get(f.get('rate', {}).get('field')) if f.get('rate') else None
            if finite(tk) and finite(target) and finite(rate) and rate > 0:
                t += abs(target - tk) / rate
            t += seg.get(f.get('hold', {}).get('field'), 0.0) or 0.0
            tk = target
        sch, _ = nonnull(self.props(cap).get(pg.get('timeout_arg', 'timeout_s')) or {})
        val = t * 1.5 + 1800.0
        if sch.get('maximum') is not None:
            val = min(val, sch['maximum'])
        return round(val, 1)

    def run_settings(self, ctx, p, verb, spec=None, step_word=None):
        """Build and send the calls of a settings verb; returns (calls made [(cap, args, defaults, response)], text)."""
        m = spec or self.vmap(verb)
        self.need(ctx, verb)
        calls = self.build_calls(ctx, m, p.extra, verb)
        tg = self.targets_for(ctx, p, verb)
        done = []
        for cap, args, defaults in calls:
            for a, v in tg.items():
                if a in self.props(cap):
                    args[a] = v
            r = ctx.call(cap, args, '%s (%s)' % (step_word or 'set the %s' % verb, cap))
            done.append((cap, args, defaults, r))
        return done

    # ------------------------------------------------------------------ the class view
    def summary(self, values):
        lines, js = [], {}
        pw = self.role(values, 'power')
        if self.slot('power') and not pw:
            return ['power off'], {'power': False}
        bits = []
        relabel = self.param('labels') or {}
        for role, label, kind in self.ROLE_VIEW:
            if not self.slot(role):
                continue
            label = relabel.get(role, label)
            if kind in units.KINDS:
                v = self.role(values, role) if kind in EXTRA_SHOW else self.role_si(values, role)
                js[role] = v
                if v is not None:
                    bits.append('%s %s' % (label, fmt_q(v, kind)))
            else:
                v = self.role(values, role)
                js[role] = v
                if v is None:
                    continue
                if role == 'phase' and isinstance(v, bool):
                    v = 'running' if v else 'idle'
                elif isinstance(v, bool) and kind == 'text':
                    v = 'yes' if v else 'no'
                if kind == 'onoff':
                    bits.append('%s %s' % (label, 'on' if v else 'off'))
                elif kind == 'yesno':
                    bits.append('%s %s' % (label, 'yes' if v else 'no'))
                elif kind == 'open':
                    bits.append('%s %s' % (label, 'open' if v else 'closed'))
                elif kind == 'present':
                    bits.append('%s %s' % (label, 'loaded' if v else 'none'))
                elif isinstance(v, float):
                    bits.append('%s %.6g' % (label, v))
                else:
                    bits.append('%s %s' % (label, short(v, 40).strip('"')))
        line = ''
        for b in bits:
            if len(line) + len(b) > 100:
                lines.append(line)
                line = ''
            line = (line + '; ' + b) if line else b
        if line:
            lines.append(line)
        return lines or ['(no state to show)'], js

    # ------------------------------------------------------------------ refusals
    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        mc = (self.map.get('codes') or {}).get('%s:%s' % (cap, code)) or (self.map.get('codes') or {}).get(code)
        if mc and mc.get('show'):
            ctx.refresh()
            fill = {'inst': self.instrument_id, 'cap': cap, 'code': code}
            now = ', '.join('%s = %s' % (x, short(ctx.v(x), 60)) for x in mc['show'])
            return CompileError(mc.get('kind', 'refused'), mc.get('code', code), mc['message'].format(**fill) + ' Now: %s.' % now,
                                (mc.get('fix') or '').format(**fill) or None)
        if code == 'HOT_CELL':
            safe = (self.example('plant.configure') or {}).get('safe_access_temperature_k')
            limit = fmt_q(safe, 'temperature') if finite(safe) else 'the safe access temperature'
            ctl = ('switch the control off (`chemlab run %s set-temp off`), then ' % self.instrument_id) if self.role(ctx, 'control') else ''
            return refused('HOT_CELL', 'the cell of %s is still hot (%s); it opens and transfers only below %s.' % (
                self.instrument_id, fmt_q(self.role_si(ctx, 'temp'), 'temperature'), limit),
                '%slet it cool: `chemlab run %s wait-temp --to %s --within 2K --max 1h`.' % (
                    ctl, self.instrument_id, ('%.0fC' % (safe - 273.15 - 4)) if finite(safe) else '35C'))
        if code in ('PRECONDITION_FAILED', 'NOT_READY'):
            ctx.refresh()
            verb = ctx.verb.name if ctx.verb else None
            for v in (verb, '*'):
                for r in (self.map.get('diagnose') or {}).get(v, []):
                    got = ctx.v(r['slot'])
                    bad = ((got != r['value']) if 'value' in r else (not got) if r.get('truthy') else bool(got) if r.get('falsy') else
                           (finite(got) and (('max' in r and got > r['max']) or ('min' in r and got < r['min']))))
                    if bad:
                        shown = ('%.4g' % got) if isinstance(got, float) else got
                        return refused(r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=shown) + ' (it answered %s)' % code,
                                       r['fix'].format(inst=self.instrument_id, value=shown))
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)

    # ------------------------------------------------------------------ run set
    def _measurement_id(self, ctx, m, for_result=False):
        """The C 6000 style record id: the running one, else the caller's latest record (created for start)."""
        rid = ctx.v(m['current_slot']) if m.get('current_slot') else None
        if rid and for_result:
            return rid
        rows = ctx.v(m['list_slot']) or []
        want = m.get('status') if not for_result else None
        cand = [r for r in rows if isinstance(r, dict) and (want is None or r.get('status') == want)]
        if not cand:
            ctx.fail(refused('NO_RECORD', '%s has no %s record%s.' % (self.instrument_id, 'measurement' if not for_result else 'finished',
                                                                       ' waiting to start' if not for_result else ''),
                             ('create one first: `chemlab run %s method name=... mass=1g`.' if not for_result else
                              'start one first (`chemlab run %s start`).') % self.instrument_id))
        return cand[-1].get(m['id_field'])

    def h_start(self, ctx, p):
        m = self.vmap('start')
        if self.running(ctx):
            ctx.fail(refused('BUSY', '%s is already running (%s).' % (self.instrument_id, self.role(ctx, 'phase')),
                             'wait for it (`chemlab run %s wait`) or stop it (`chemlab run %s stop`).' % (self.instrument_id, self.instrument_id)))
        self.need(ctx, 'start')
        args = dict(m.get('args') or {})
        if m.get('id_arg'):
            args[m['id_arg']] = self._measurement_id(ctx, m)
        args.update({a: v for a, v in self.targets_for(ctx, p, 'start').items() if a in self.props(m['cap'])})
        r = ctx.call(m['cap'], args, 'start (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        notes = list((self.map.get('notes') or {}).get('start-after', []))
        if r.get('operation_id'):
            job = OperationJob(ctx.core, self, ctx.req, '%s %s' % (self.instrument_id, m['cap']), m['cap'], r['operation_id'], max_s=m.get('max_s'),
                         poll_s=float(self.param('job_poll_s', 10.0)))
            text = 'started %s: %s run on %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
                job.handle, m['cap'], self.instrument_id, job.handle, job.handle, job.handle)
            return {'text': text, 'json': {'job': job.handle, 'kind': job.kind, 'operation_id': r['operation_id']}, 'notes': notes}
        return {'text': 'ok: %s started (%s)\n' % (self.instrument_id, r.get('status')), 'json': {'status': r.get('status'), 'result': r.get('result')}, 'notes': notes}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        if self.slot('phase') and not self.running(ctx):
            ctx.fail(refused('NOT_RUNNING', '%s is not running (%s).' % (self.instrument_id, self.role(ctx, 'phase')), 'nothing to stop.'))
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop (%s)' % m['cap'])
        return {'text': 'ok: %s stopped (%s)\n' % (self.instrument_id, self.role(ctx, 'phase')), 'json': {'phase': self.role(ctx, 'phase')}}

    def h_result(self, ctx, p):
        m = self.vmap('result')
        args = dict(m.get('args') or {})
        if m.get('id_arg'):
            args[m['id_arg']] = self._measurement_id(ctx, m, for_result=True)
        r = ctx.call(m['cap'], args, 'read the result (%s)' % m['cap'], query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') or {}
        lines = ['%s result (%s):' % (self.instrument_id, m['cap'])]
        rows = res.get(m.get('rows', 'rows')) if isinstance(res, dict) else None
        head = []
        for k in ('phase', 'status', 'kind', 'elapsed_s', 'error_code', 'error'):
            if isinstance(res, dict) and res.get(k) is not None:
                head.append('%s %s' % (k.replace('_s', '').replace('_', ' '), fmt_q(res[k], 'duration') if k == 'elapsed_s' else res[k]))
        if isinstance(rows, list):
            head.append('%d rows' % len(rows))
        lines.append('  ' + ', '.join(head))
        for path, (label, kind) in (m.get('show') or {}).items():
            v = _get(res, path)
            if v is not None:
                lines.append('  %-22s %s' % (label, fmt_q(units.from_declared(v, m.get('units', {}).get(path)) if m.get('units', {}).get(path) else v, kind) if kind else v))
        for col, (label, kind) in (m.get('columns') or {}).items():
            vals = _num_list(rows or [], col)
            if vals:
                f = (lambda x: fmt_q(x, kind)) if kind else (lambda x: '%.6g' % x)
                lines.append('  %-22s first %s, last %s (min %s, max %s)' % (label, f(vals[0]), f(vals[-1]), f(min(vals)), f(max(vals))))
        per = m.get('per_item')
        if per and isinstance(_get(res, per['path']), list):
            items = _get(res, per['path'])
            nmax = int(per.get('max', 12))
            for i, it in enumerate(items[:nmax]):
                bits = []
                for path, (label, kind) in per['fields'].items():
                    v = _get(it, path)
                    if v is not None:
                        bits.append('%s %s' % (label, fmt_q(v, kind) if kind else ('%.6g' % v if isinstance(v, float) else v)))
                lines.append('  %s %d: %s' % (per.get('label', 'item'), i + 1, ', '.join(bits)))
            if len(items) > nmax:
                lines.append('  ... %d more (see --json or --save-to)' % (len(items) - nmax))
        if not isinstance(rows, list) and not m.get('show'):
            lines.append('  ' + short(res, 400))
        return {'text': '\n'.join(lines) + '\n', 'json': {'result': res}}

    # ------------------------------------------------------------------ temperature
    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        text = p.raw['TEMP|off'].strip().lower()
        if text in ('off', 'stop'):
            if not m.get('off_cap'):
                ctx.fail(unsupported('NO_OFF', '%s has no separate switch-off of the temperature control.' % self.instrument_id,
                                     'set a temperature instead.'))
            self.need(ctx, 'set-temp-off')
            ctx.call(m['off_cap'], {}, 'switch the temperature control off')
            return {'text': 'ok: temperature control of %s off\n' % self.instrument_id, 'json': {'control': False}}
        schema = self.props(m['cap']).get(m['arg']) or {}
        val = to_native(p.raw['TEMP|off'], schema, 'temperature', 'temperature')
        args = {m['arg']: val}
        if m.get('rate_arg'):
            rs = self.props(m['cap']).get(m['rate_arg']) or {}
            if p.raw.get('rate'):
                args[m['rate_arg']] = to_native(p.raw['rate'], rs, 'heating_rate', '--rate')
            else:
                d = self.param('default_rate', 'example')
                args[m['rate_arg']] = self.example(m['cap']).get(m['rate_arg']) if d == 'example' else d
                ctx.notes.append('rate not given: %s (%s)' % (fmt_q(units.from_declared(args[m['rate_arg']], units.declared(rs)), 'heating_rate'),
                                                             'the contract\'s example' if d == 'example' else 'class default'))
        elif p.raw.get('rate'):
            ctx.fail(unsupported('NO_RATE', '%s takes no ramp rate for its set point.' % self.instrument_id, 'leave out --rate.'))
        self.need(ctx, 'set-temp')
        if self.running(ctx) and not self.param('set_temp_while_running'):
            ctx.fail(refused('BUSY', '%s is running a measurement; its temperature follows the method now.' % self.instrument_id,
                             'wait for the run or stop it (`chemlab run %s stop`).' % self.instrument_id))
        k = units.from_declared(val, units.declared(schema))
        ctx.call(m['cap'], args, 'set the temperature to %s' % fmt_q(k, 'temperature'))
        out = {'set_point_k': k}
        text = 'ok: %s set point %s; now %s\n' % (self.instrument_id, fmt_q(k, 'temperature'), fmt_q(self.role_si(ctx, 'temp'), 'temperature'))
        if p.raw.get('gradient') is not None:
            g = m.get('gradient') or {}
            if not g:
                ctx.fail(unsupported('NO_GRADIENT', '%s has no temperature gradient setting.' % self.instrument_id, 'leave out --gradient.'))
            gv = to_native(p.raw['gradient'], self.props(g['cap']).get(g['arg']) or {}, 'temperature_delta', '--gradient')
            ctx.call(g['cap'], {g['arg']: gv}, 'set the gradient to %s' % fmt_q(gv, 'temperature_delta'))
            out['gradient_k'] = gv
            text += 'ok: gradient %s\n' % fmt_q(gv, 'temperature_delta')
        return {'text': text, 'json': out}

    def h_temp(self, ctx, p):
        lines, js = self.summary(ctx)
        return {'text': '%s: %s\n' % (self.instrument_id, '; '.join(lines)), 'json': js}

    def h_wait_temp(self, ctx, p):
        target = p.get('to') if p.get('to') is not None else self.role_si(ctx, 'set')
        if not finite(target):
            ctx.fail(refused('NO_SET_POINT', '%s has no set point to wait for.' % self.instrument_id, 'set one (`set-temp T`) or give --to T.'))
        if self.slot('control') and not self.role(ctx, 'control'):
            tk = self.role_si(ctx, 'temp')
            cooling = p.get('to') is not None and finite(tk) and target < tk          # with the control off it only cools down
            if not cooling and not (finite(tk) and abs(tk - target) <= (p.get('within') or self.param('tolerance_k', 0.5))):
                ctx.fail(refused('CONTROL_OFF', 'the temperature control of %s is off, so it will not reach %s (now %s).' % (
                    self.instrument_id, fmt_q(target, 'temperature'), fmt_q(tk, 'temperature')), 'set a temperature first: `chemlab run %s set-temp T`.' % self.instrument_id))
        within = p.get('within') if p.get('within') is not None else float(self.param('tolerance_k', 0.5))
        max_s = p.get('max') or 3600.0
        if ctx.dry_run:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': max_s}, 'step': 'wait until %s ± %s' % (fmt_q(target, 'temperature'), fmt_q(within, 'temperature_delta'))})
            return {'text': '', 'json': {}}
        t0 = ctx.now()

        ramp = self.slot('ramp') if p.get('to') is None else None          # the control's own ramp target must have arrived too

        def inside(view):
            tk = self.role_si(ctx, 'temp')
            if ramp and finite(ctx.v(ramp)) and abs(ctx.v(ramp) - target) > 1e-6:
                return False
            return finite(tk) and abs(tk - target) <= within + 1e-9
        if not inside(ctx.view):
            ctx.advance(max_s, until=inside, step='wait until %s' % fmt_q(target, 'temperature'), poll_s=float(self.param('poll_s', 5.0)))
        tk = self.role_si(ctx, 'temp')
        if not inside(ctx.view):
            ctx.fail(refused('NOT_REACHED', '%s is at %s after %s; %s ± %s was not reached.' % (
                self.instrument_id, fmt_q(tk, 'temperature'), units.fmt_s(ctx.now() - t0), fmt_q(target, 'temperature'), fmt_q(within, 'temperature_delta')),
                'wait longer (`chemlab run %s wait-temp --max 2h`) or check `chemlab run %s status`.' % (self.instrument_id, self.instrument_id),
                detail={'temperature_k': tk, 'target_k': target}))
        return {'text': 'reached %s (target %s ± %s) after %s of waiting\n' % (fmt_q(tk, 'temperature'), fmt_q(target, 'temperature'),
                                                                             fmt_q(within, 'temperature_delta'), units.fmt_s(ctx.now() - t0)),
                'json': {'reached': True, 'temperature_k': tk, 'target_k': target, 'waited_s': ctx.now() - t0}}

    # ------------------------------------------------------------------ analyze: an analysis capability fed from the last result
    def h_analyze(self, ctx, p):
        """analyze [MODEL] KEY=VALUE ...: read the last result (the member's result capability), take the arrays / objects /
        scalars the model names from it, add the agent's settings (in units) and the member's defaults, call the analysis
        capability (a pure computation: the instrument does not change)."""
        m = self.vmap('analyze')
        models = m.get('models') or {}
        name = p.pos[0] if p.pos else None
        extra = list(p.extra)
        if name is not None and '=' in name:
            extra.insert(0, name)
            name = None
        name = name or m.get('default')
        if name not in models:
            ctx.fail(invalid('UNKNOWN_MODEL', '%s has no analysis %r.' % (self.instrument_id, name),
                             'analyses: %s (see `chemlab help %s analyze`).' % (', '.join(sorted(models)), self.instrument_id)))
        sp = models[name]
        cap = sp['cap']
        props = self.props(cap)
        given, keyvals = {}, {}
        for item in extra:
            k, eq, v = item.partition('=')
            if not eq:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write analyze %s KEY=VALUE ...' % name))
            keyvals[k.strip()] = v.strip()
        allowed = set(sp.get('keys') or {}) | {a for a in props if a not in self._fed_args(sp)}
        bad = [k for k in keyvals if k not in allowed]
        if bad:
            ctx.fail(invalid('UNKNOWN_SETTING', '%s analyze %s has no setting %r.' % (self.instrument_id, name, bad[0]),
                             'settings: %s.' % ', '.join(sorted(allowed))))
        for k, ks in (sp.get('keys') or {}).items():
            if k not in keyvals:
                continue
            if ks.get('ctl'):                                               # a setting of the feeding (window, baseline, block ...), not an argument
                slot = '_' + ks['ctl']
                if ks.get('list'):
                    given[slot] = [parse_q(x, ks['kind'], k) for x in keyvals[k].split(',') if x.strip()]
                elif ks.get('choices'):
                    if keyvals[k] not in ks['choices']:
                        ctx.fail(invalid('BAD_CHOICE', '%s=%s is not one of: %s.' % (k, keyvals[k], ', '.join(ks['choices'])), 'use one of them.'))
                    given[slot] = keyvals[k]
                else:
                    given[slot] = parse_q(keyvals[k], ks.get('kind', 'number'), k)
            else:
                given[ks['arg']] = to_native(keyvals[k], props.get(ks['arg']) or {}, ks.get('kind'), k)
        for k, v in keyvals.items():
            if k in props and k not in (sp.get('keys') or {}):
                given[k] = to_native(v, props[k], None, k)
        for k in sp.get('need') or []:
            ks = (sp.get('keys') or {}).get(k, {})
            arg = ('_' + ks['ctl']) if ks.get('ctl') else ks.get('arg', k)
            if arg not in given:
                kind = ks.get('kind')
                ctx.fail(invalid('MISSING_SETTING', 'analyze %s needs %s= on %s.' % (name, k, self.instrument_id),
                                 'give it, e.g. %s=%s (see `chemlab help %s analyze`).' % (
                                     k, (ks.get('example') or (kind_example(kind) if kind else '...')), self.instrument_id)))
        self.need(ctx, 'analyze')
        # the record the analysis works on
        rm = self.vmap('result')
        rargs = dict(rm.get('args') or {})
        if rm.get('id_arg'):
            rargs[rm['id_arg']] = self._measurement_id(ctx, rm, for_result=True)
        args = {}
        if sp.get('id_arg'):
            args[sp['id_arg']] = rargs.get(rm.get('id_arg')) if rm.get('id_arg') else None
        if self._fed_args(sp):
            r = ctx.call(rm['cap'], rargs, 'read the record (%s)' % rm['cap'], query=True)
            res = (r.get('result') or {}) if not ctx.dry_run else {}
            if not ctx.dry_run:
                args.update(self._feed(ctx, sp, res, given, name))
        for a, v in given.items():
            if not str(a).startswith('_'):
                args[a] = v
        for a in self.required(cap):
            if a not in args:
                d = (sp.get('defaults') or {}).get(a, None)
                if d is None and a not in (sp.get('defaults') or {}):
                    key = next((k for k, ks in (sp.get('keys') or {}).items() if ks.get('arg') == a), a)
                    ctx.fail(invalid('MISSING_SETTING', 'analyze %s needs %s= on %s.' % (name, key, self.instrument_id),
                                     'give it (see `chemlab help %s analyze`).' % self.instrument_id))
                args[a], _ = self._default(ctx, cap, a, d, args, None)
        if ctx.dry_run:
            ctx.plan.append({'capability': cap, 'arguments': {k: ('[%d values]' % len(v) if isinstance(v, list) else v) for k, v in args.items()},
                             'step': 'analyze (%s)' % cap})
            return {'text': '', 'json': {}}
        problems = validate((self.lab.contract['capabilities'][cap].get('input_schema') or {}), args, cap)
        if problems:
            ctx.fail(invalid('INVALID_SETTINGS', 'the %s inputs do not fit %s: %s.' % (name, cap, '; '.join(problems[:3])),
                             (sp.get('fix') or 'check the settings and the record (`chemlab run %s result`).' % self.instrument_id).replace('INST', self.instrument_id)))
        r = ctx.call(cap, args, 'analyze (%s)' % cap, query=True)
        out = r.get('result') or {}
        lines = ['%s analyze %s (%s):' % (self.instrument_id, name, cap)]
        for k, v in (out.items() if isinstance(out, dict) else []):
            if k == 'evidence':
                continue
            if isinstance(v, list):
                lines.append('  %-34s %d items' % (k, len(v)))
                for i, item in enumerate(v[:6]):
                    if isinstance(item, dict):
                        lines.append('    %d: %s' % (i + 1, ', '.join('%s %s' % (a, ('%.6g' % b) if isinstance(b, float) else b)
                                                                for a, b in item.items() if not isinstance(b, (dict, list)) and a != 'evidence')))
                continue
            if isinstance(v, dict):
                continue
            lines.append('  %-34s %s' % (k, ('%.6g' % v) if isinstance(v, float) else v))
        shown = {k: (('[%d values]' % len(v)) if isinstance(v, list) and len(v) > 8 else v) for k, v in args.items()}
        return {'text': '\n'.join(lines) + '\n', 'json': {'model': name, 'capability': cap, 'inputs': shown, 'result': out}}

    @staticmethod
    def _fed_args(sp):
        fed = set(sp.get('arrays') or {}) | set(sp.get('scalars') or {}) | set(sp.get('zeros') or []) | set((sp.get('objects') or {}).keys())
        for k in ('baseline', 'indices'):
            if sp.get(k):
                fed.add(sp[k]['arg'])
        fed |= set(sp.get('window_index') or {})
        if sp.get('pass_rows'):
            fed.add(sp['pass_rows'])
        return fed

    def _feed(self, ctx, sp, res, given, name):
        """Arrays, objects and scalars for the analysis from the result record."""
        out = {}
        rows = _get(res, sp.get('rows', 'rows')) or []
        if not isinstance(rows, list) or not rows:
            ctx.fail(refused('NO_RECORD', '%s has no record to analyse (run it first: `chemlab run %s start`).' % (self.instrument_id, self.instrument_id),
                             'start a run, wait for it, then analyze.'))
        it = None
        if sp.get('pick'):
            pk = sp['pick']
            n = int(given.pop('_' + pk['key'], None) or 1)
            items = _get(res, pk['path']) or []
            if not 1 <= n <= len(items):
                ctx.fail(invalid('OUT_OF_RANGE', '%s=%d is not one of the %d %ss of the record.' % (pk['key'], n, len(items), pk['key']),
                                 'give %s=1..%d.' % (pk['key'], len(items))))
            it = items[n - 1]
            if pk.get('rows_match'):
                col, base = pk['rows_match'], pk.get('base', 0)
                rows = [r for r in rows if r.get(col) == n - 1 + base]
            if pk.get('group'):
                g = int(pk['group'])
                rows = rows[(n - 1) * g:n * g]
        for col, val in (sp.get('filter') or {}).items():
            rows = [r for r in rows if r.get(col) == val]
        if sp.get('positive'):
            rows = [r for r in rows if finite(r.get(sp['positive'])) and r[sp['positive']] > 0]
        win = sp.get('window')
        if win:
            lo, hi = given.pop('_from', None), given.pop('_to', None)
            if lo is not None or hi is not None:
                rows = [r for r in rows if finite(r.get(win['col'])) and (lo is None or r[win['col']] >= lo) and (hi is None or r[win['col']] <= hi)]
        if len(rows) < int(sp.get('min_rows', 3)):
            ctx.fail(refused('TOO_FEW_POINTS', 'the record of %s has %d usable points for %s (at least %d).' % (self.instrument_id, len(rows), name, int(sp.get('min_rows', 3))),
                             (sp.get('fix') or 'widen the window or measure longer.').replace('INST', self.instrument_id)))
        for arg, col in (sp.get('arrays') or {}).items():
            out[arg] = [_get(r, col) for r in rows]
        for arg in sp.get('zeros') or []:
            out[arg] = [0.0] * len(rows)
        for arg, spec in (sp.get('objects') or {}).items():
            src = rows if spec.get('from', 'rows') == 'rows' else (_get(it, spec['from'][3:]) if spec['from'].startswith('it.') else _get(res, spec['from']))
            out[arg] = [{f: r.get(f) for f in spec['fields']} for r in (src or [])]
        if sp.get('pass_rows'):
            out[sp['pass_rows']] = rows
        b = sp.get('baseline')
        if b:
            mode = given.pop('_baseline', None) or b.get('default', 'linear')
            ys = out[b['of']]
            if mode == 'zero':
                out[b['arg']] = [0.0] * len(ys)
            else:
                y0, y1, n = ys[0], ys[-1], len(ys)
                out[b['arg']] = [y0 + (y1 - y0) * i / (n - 1) for i in range(n)]
        ix = sp.get('indices')
        if ix:
            temps = given.pop('_' + ix['key'], None) or []
            col = [r.get(ix['col']) for r in rows]
            idx = [0, len(rows) - 1] if ix.get('ends') else []
            for t in temps:
                k = min(range(len(col)), key=lambda i: abs((col[i] if finite(col[i]) else 1e18) - t))
                idx.append(k)
            out[ix['arg']] = sorted(set(idx))
        for arg, (key, end) in (sp.get('window_index') or {}).items():
            out[arg] = 0 if end == 'first' else len(rows) - 1
        for arg, src in (sp.get('scalars') or {}).items():
            src, _, fallback = src.partition('|')                         # 'result:calibration_gain|1.0': a value when the record has none
            how, _, path = src.partition(':')
            if how == 'result':
                v = _get(res, path)
            elif how == 'it':
                v = _get(it, path)
            elif how == 'first':
                v = rows[0].get(path)
            elif how == 'row':                                              # 'row:polarity=zero/voltage_v': a column of the row with col == value
                cond, _, col = path.partition('/')
                c_, _, val = cond.partition('=')
                hit = [r for r in rows if str(r.get(c_)) == val]
                v = hit[0].get(col) if hit else None
            elif how == 'max':
                vals = [r[path] for r in rows if finite(r.get(path))]
                v = max(vals) if vals else None
            elif how == 'mean_before':                                      # 'mean_before:temperature_k<time_s@pulse_time_s'
                col, _, rest = path.partition('<')
                tcol, _, t0path = rest.partition('@')
                t0 = _get(res, t0path)
                pts = [r[col] for r in rows if finite(r.get(col)) and finite(r.get(tcol)) and finite(t0) and r[tcol] < t0]
                v = sum(pts) / len(pts) if pts else None
            else:
                v = None
            if v is None and fallback:
                v = json.loads(fallback)
            if v is None:
                ctx.fail(refused('NO_RECORD', 'the record of %s has no %s for %s.' % (self.instrument_id, path, name), 'run a complete measurement first.'))
            out[arg] = v
        return out

    def analyze_verb(self):
        m = (self.map.get('verbs') or {}).get('analyze') or {}
        example = 'chemlab run INST analyze %s' % (m.get('example') or m.get('default') or 'MODEL KEY=VALUE')
        lines = []
        for name, sp in sorted((m.get('models') or {}).items()):
            keys = ', '.join('%s=' % k for k in sorted(sp.get('keys') or {}))
            lines.append('  %-12s -> %s%s%s' % (name, sp['cap'], ' (default)' if name == m.get('default') else '', ('; settings: ' + keys) if keys else ''))
        details = ('analyses on this instrument (the inputs come from the last record; give the settings the record cannot know):\n' + '\n'.join(lines)) if lines else None
        return Verb('analyze', 'evaluate the last record with an analysis of the instrument (MODEL KEY=VALUE ...)', self.h_analyze,
                    positionals=[Arg('MODEL', 'which analysis (default: the member\'s main one)', required=False)], variadic=('KEY=VALUE', 'a setting'),
                    readonly=True, options={'save-to': save_to_opt()}, details=details,
                    preconditions=['a finished record', 'the settings the record cannot know (mass, thickness, length ...)'],
                    errors=['UNKNOWN_MODEL, MISSING_SETTING, INVALID_SETTINGS (usage)', 'NO_RECORD, TOO_FEW_POINTS (refused)'], example=example)

    # ------------------------------------------------------------------ open / close (with the wait for the movement)
    def h_part(self, action):
        """open|close PART: the shared verb's meaning, plus the wait until the lid / furnace / cover has finished moving."""
        def h(ctx, p):
            parts = (self.vmap('open') or {}).get('parts') or {}
            name = p.pos[0].lower() if p.pos else None
            if name is None:
                mains = [k for k, v in parts.items() if v.get('main')] or (list(parts) if len(parts) == 1 else [])
                if len(mains) != 1:
                    ctx.fail(invalid('WHICH_PART', '%s has several parts: %s.' % (self.instrument_id, ', '.join(parts)),
                                     'name one, e.g. `chemlab run %s %s %s`.' % (self.instrument_id, action, next(iter(parts)))))
                name = mains[0]
            if name not in parts:
                ctx.fail(invalid('UNKNOWN_PART', '%s has no part %r to %s.' % (self.instrument_id, name, action), 'parts: %s.' % ', '.join(parts)))
            sp = parts[name]
            want = sp.get(action + '_state', action == 'open')
            if sp.get('state_slot') and ctx.v(sp['state_slot']) == want and not any(ctx.v(x) for x in sp.get('moving') or []):
                return {'text': 'ok: %s %s is already %s\n' % (self.instrument_id, name, 'open' if action == 'open' else 'closed'),
                        'json': {'part': name, 'action': action, 'changed': False}}
            self.need(ctx, '%s-%s' % (action, name))
            if sp.get('%s_cap' % action):
                ctx.call(sp['%s_cap' % action], dict(sp.get('args') or {}), '%s the %s' % (action, name))
            else:
                ctx.call(sp['cap'], {sp['arg']: sp[action]}, '%s the %s' % (action, name))
            waited = 0.0
            if sp.get('moving') or sp.get('state_slot'):
                def test(c):
                    return all(not c.v(x) for x in sp.get('moving') or []) and (not sp.get('state_slot') or c.v(sp['state_slot']) == want)
                waited = self.settle(ctx, test, 'the %s is %s' % (name, 'open' if action == 'open' else 'closed'), float(sp.get('max_s', 180)))
            return {'text': 'ok: %s %s %s%s\n' % (self.instrument_id, name, 'opened' if action == 'open' else 'closed',
                                                  ' (after %s)' % units.fmt_s(waited) if waited else ''),
                    'json': {'part': name, 'action': action, 'changed': True, 'waited_s': waited}}
        return h

    # ------------------------------------------------------------------ simple one-call verbs
    def h_simple(self, verb, done_text):
        def h(ctx, p):
            m = self.vmap(verb)
            self.need(ctx, verb)
            ctx.call(m['cap'], dict(m.get('args') or {}), done_text)
            return {'text': 'ok: %s %s\n' % (self.instrument_id, done_text), 'json': {'done': verb}}
        return h

    def h_calibrate(self, ctx, p):
        m = self.vmap('calibrate')
        if self.running(ctx) and m.get('idle_only', True):
            ctx.fail(refused('BUSY', '%s is running a measurement.' % self.instrument_id, 'calibrate before or after the run.'))
        done = self.run_settings(ctx, p, 'calibrate', step_word='calibrate')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        cap, args, defaults, r = done[-1]
        if r.get('operation_id'):
            job = OperationJob(ctx.core, self, ctx.req, '%s %s' % (self.instrument_id, cap), cap, r['operation_id'], poll_s=float(self.param('job_poll_s', 10.0)))
            return {'text': 'started %s: %s on %s\nfollow it: chemlab status %s | chemlab wait %s\n' % (job.handle, cap, self.instrument_id, job.handle, job.handle),
                    'json': {'job': job.handle, 'arguments': args, 'defaults': defaults}}
        text = 'ok: %s calibration set: %s\n' % (self.instrument_id, short(args, 300))
        if defaults:
            text += 'not given, so taken from the instrument: %s\n' % '; '.join(defaults)
        return {'text': text, 'json': {'arguments': args, 'defaults': defaults}}

    def h_method(self, ctx, p):
        if self.running(ctx):
            ctx.fail(refused('BUSY', '%s is running; its method cannot change now.' % self.instrument_id,
                             'wait for the run (`chemlab run %s wait`) or stop it.' % self.instrument_id))
        done = self.run_settings(ctx, p, 'method', step_word='set the method')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        lines, js = [], []
        for cap, args, defaults, r in done:
            res = r.get('result')
            lines.append('ok: %s %s: %s' % (self.instrument_id, cap, short(args, 260)))
            if defaults:
                lines.append('  not given, so taken from defaults: %s' % '; '.join(defaults))
            if isinstance(res, dict) and len(res) <= 4 and any(k.endswith('_id') for k in res):
                lines.append('  -> %s' % ', '.join('%s %s' % kv for kv in res.items()))
            js.append({'capability': cap, 'arguments': args, 'defaults': defaults, 'result': res})
        return {'text': '\n'.join(lines) + '\n', 'json': {'calls': js}}


# ------------------------------------------------------------------ the thermal_analysis class
REF_ALL = ('TA Instruments Discovery DSC 2500 TRIOS guide (UCSB MRL) pp. 2-13 and brochure pp. 7-12; TGA 5500 SOP (WWU) pp. 3-9 and '
       'brochure pp. 10-13 (autosampler, tare, purge); DMA 850 quick start pp. 1-3, brochure pp. 15-20 (clamps, preload); TMA 450 '
       'brochure pp. 5-8, 21-28 (probe force, test modes); LFA 467 brochure pp. 3-5, 14-20 (sample changer, atmosphere, pulse); Hot '
       'Disk TPS 2500 S brochure pp. 2-7; TAM IV brochure pp. 4-13, 45-46 and TAM theory pp. 50-62 (calibration); MMC 315 / MMC 274 '
       'brochure pp. 5-9 (heat-wait-search); IKA C 6000 operating instructions pp. 6-11 (calorific value, C value, vessels)')


class ThermalAnalysisAdapter(TATester):
    CLASS = 'thermal_analysis'
    TITLE = 'thermal analysis and calorimetry'
    ABOUT = ('Instruments that run a temperature programme on a specimen and record a thermal signal (heat flow, mass, modulus, '
             'expansion, diffusivity, heat of reaction or combustion): load the specimen, set the method in plain units, purge, '
             'start a run (job-N), read the result.')
    STANDARDS = ('ISO 11357 / ASTM E967, E968 (DSC); ASTM E1131 / ISO 11358 (TGA); ASTM E1640 / ISO 6721 (DMA); ASTM E831 (TMA); '
                 'ASTM E1461 (flash); ISO 22007-2 (TPS); ASTM E1981 (ARC); ISO 1928 / ASTM D5865 (bomb calorimetry)')
    ALWAYS = ('status',)
    UNIT_KINDS = ('temperature', 'temperature_delta', 'heating_rate', 'duration', 'flow', 'mass', 'force', 'power', 'energy')
    ROLE_VIEW = (('temp', 'sample', 'temperature'), ('rear', 'rear face', 'temperature'), ('furnace', 'furnace', 'temperature'),
                 ('jacket', 'jacket', 'temperature'), ('set', 'set point', 'temperature'), ('control', 'control', 'onoff'),
                 ('phase', 'run', 'text'), ('stage', 'stage', 'text'), ('points', 'points', 'text'), ('sample', 'sample', 'present'),
                 ('ref', 'reference', 'present'), ('open', 'lid/furnace', 'open'), ('cover', 'cover', 'text'), ('gas', 'purge', 'onoff'),
                 ('flow', 'purge flow', 'flow'), ('gas_name', 'gas', 'text'), ('mass', 'mass', 'mass'), ('force', 'force', 'force'),
                 ('pressure', 'pressure', 'pressure'), ('clamp', 'clamp', 'onoff'), ('probe', 'probe', 'onoff'),
                 ('sensor', 'sensor connected', 'yesno'), ('detector', 'detector', 'onoff'), ('system', 'system', 'text'),
                 ('mode', 'mode', 'text'), ('record', 'measurement', 'text'), ('record_phase', 'measurement phase', 'text'),
                 ('vessel', 'vessel hung', 'text'), ('result', 'last Ho (J/kg)', 'text'))
    CODES = {
        'START_NOT_EQUILIBRATED': ('NOT_EQUILIBRATED', '{inst} is not at the start temperature of its method yet (within its start tolerance).',
                                   'set it and wait: `chemlab run {inst} set-temp START`, then `chemlab run {inst} wait-temp --within 0.3K`, then start.'),
        'NO_SAMPLE': ('NO_SAMPLE', 'there is no specimen in the measuring position of {inst}.', 'load it first (`chemlab run {inst} load ...`).'),
        'SOURCE_ANCHOR_ONLY': ('OUT_OF_DATA', 'the specimen of {inst} is described only at the temperature and frequency of its reference '
                               'data; the method asks for other conditions.', 'measure at the specimen\'s reference point (ask the lab operator which one).'),
        'NO_PRELOAD': ('NO_PRELOAD', 'the DMA specimen has no static preload yet.', 'apply one: `chemlab run {inst} load --preload 0.01N`.'),
        'PURGE_REQUIRED': ('NO_PURGE', '{inst} needs purge gas flowing (a purge line and the protective gas) before it heats or measures.',
                           'open them: `chemlab run {inst} gas 50mL/min --channel purge1` and `... --channel protective`.'),
        'DETECTOR_NOT_READY': ('DETECTOR_OFF', 'the IR detector of {inst} is not ready (off or still cooling).',
                               'switch it on (`chemlab call {inst} detector.set_enabled --enabled true`) and wait about a minute (`chemlab wait 60`).'),
        'INSUFFICIENT_OPTICAL_ENERGY': ('NO_PULSE_ENERGY', 'the flash lamp of {inst} has not enough stored energy for the method.',
                                        'lower energy= in the method, or ask the lab operator to charge the lamp.'),
        'THERMAL_CONTROL_ACTIVE': ('CONTROL_ON', 'the temperature control of {inst} is on.', 'switch it off first: `chemlab run {inst} set-temp off`.'),
        'TRAY_POSITION_EMPTY': ('EMPTY_POSITION', 'that tray position of {inst} holds no pan.', 'name a position that holds a pan (`chemlab run {inst} status`).'),
        'TRAY_POSITION_OCCUPIED': ('POSITION_TAKEN', 'that tray position of {inst} already holds a pan.', 'unload to an empty position.'),
        'ALREADY_LOADED': ('ALREADY_LOADED', 'a specimen is already in the measuring position of {inst}.', 'unload it first (`chemlab run {inst} unload`).'),
        'CLAMP_UNDER_LOAD': ('CLAMP_BLOCKED', 'the clamp of {inst} moves only with the furnace fully open and no load on the specimen.',
                             'open the furnace (`chemlab run {inst} open furnace`); `chemlab run {inst} unload` releases a preload first.'),
        'PROBE_ACCESS': ('FURNACE_CLOSED', 'the probe of {inst} cannot move while the furnace is closed.', 'open it: `chemlab run {inst} open furnace`.'),
        'ZERO_REQUIRES_EMPTY_EQUILIBRIUM': ('NOT_EMPTY', '{inst} zeroes only with an empty, equilibrated cell.', 'unload the specimen and let the cell settle, then zero.'),
        'ZERO_REQUIRES_EMPTY': ('NOT_EMPTY', '{inst} zeroes only with no specimen clamped.', 'unload the specimen first.'),
        'TARE_REQUIRES_EMPTY_STABLE': ('NOT_EMPTY', '{inst} tares only with an empty, stable balance (no pan on the hang-down wire).',
                                       'unload the pan first (`chemlab run {inst} unload`) and let it settle.'),
        'ZERO_REQUIRES_CONTACT': ('NO_CONTACT', 'the TMA probe must touch the specimen to zero the displacement.', 'bring it down first: `chemlab run {inst} load`.'),
        'FURNACE_NOT_CLOSED': ('FURNACE_OPEN', 'the furnace of {inst} is not closed.', 'close it: `chemlab run {inst} close furnace`.'),
        'LID_NOT_CLOSED': ('LID_OPEN', 'the cell lid of {inst} is not closed.', 'close it: `chemlab run {inst} close lid`.'),
        'NO_SAMPLE_AT_SELECTED_SLOT': ('NO_SAMPLE', 'the selected sample-changer position of {inst} is empty.', 'select the position that holds the sample: `chemlab run {inst} load N`.'),
        'BUSY': ('BUSY', '{inst} is busy (a run or a movement is in progress).', 'wait (`chemlab run {inst} wait`) or stop the run.'),
        'NOT_RUNNING': ('NOT_RUNNING', '{inst} has nothing running.', 'nothing to stop.'),
        'NO_GAS': ('NO_GAS', '{inst} has no purge gas connected.', 'ask the lab operator to connect the gas supply.'),
        'GAS_EXHAUSTED': ('NO_GAS', 'the purge gas of {inst} ran out.', 'ask the lab operator to replace the cylinder.'),
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'EXTERNAL_TEMPERATURE_BOUNDARY': ('NOT_AMBIENT', 'the TPS method temperature must equal the room temperature of the lab.',
                                          'leave temp= out (it keeps the current one).'),
        'SMALL_RISE_DOMAIN': ('RISE_TOO_LARGE', 'the heating power and time of {inst} would heat the sensor more than its small-rise limit.',
                              'lower power= or time= in the method.'),
        'FINITE_BOUNDARY_DOMAIN': ('TIME_TOO_LONG', 'the measuring time of {inst} is too long for the specimen size (the heat would reach its boundary).',
                                   'shorten time= in the method.'),
        'RELOAD_REQUIRED': ('RELOAD', 'the specimen of {inst} was already heated by a measurement; it must cool and be reloaded.',
                            'ask the lab operator to reload it, or wait for it to relax.'),
        'REFERENCE_TEMPERATURE_RANGE': ('OUT_OF_DATA', 'the specimen\'s reference data of {inst} do not cover this temperature.',
                                        'measure inside the temperature range of the specimen (see `chemlab run {inst} status`).'),
        'TIMEOUT': ('TIMEOUT', 'the run of {inst} timed out.', 'raise timeout= in the method or widen tolerance=.'),
        'ACCESS_DENIED': ('NOT_YOURS', 'another agent\'s run is active on {inst}.', 'wait until it has finished.'),
        'SWITCH_OFF_REQUIRED': ('SWITCH_OFF', '{inst} shows an error that clears only by switching off and on.', '`chemlab run {inst} power off`, then `power on`.'),
    }

    def build_verbs(self):
        REF = self.param('manual') or REF_ALL
        temp = Arg('TEMP|off', 'set point, e.g. 140C or 413.15K (a bare number is °C); off switches the control off')
        return {
            'load': Verb('load', 'bring the specimen into the measuring position (autosampler pan, sample changer, clamp, probe or bomb)',
                         self.h_load, positionals=[Arg('WHERE', 'tray / changer position (DSC, TGA, LFA) or the vessel RFID (C 6000)', required=False)],
                         options={'preload': Opt('static preload after clamping (DMA), e.g. 0.01N', kind='force', metavar='FORCE')},
                         long_action=True, preconditions=['a specimen at that position', 'nothing loaded yet', 'the member\'s own state (cool cell, open furnace)'],
                         errors=['EMPTY_POSITION, ALREADY_LOADED, HOT_CELL (refused)', 'NOT_SETTLED (refused: the transfer did not end)'],
                         details='waits for the transfer to finish (lab time moves, about a minute).',
                         example='chemlab run INST load 1', reference=REF),
            'unload': Verb('unload', 'take the specimen out of the measuring position', self.h_unload,
                           positionals=[Arg('WHERE', 'tray position to return the pan to (default: where it came from)', required=False)],
                           long_action=True, preconditions=['a specimen loaded', 'no run', 'cell cool / control off where the member needs it'],
                           errors=['NOT_LOADED, BUSY, HOT_CELL, CONTROL_ON (refused)'], details='waits for the transfer to finish.',
                           example='chemlab run INST unload', reference=REF),
            'method': Verb('method', 'set the method: KEY=VALUE ... (class keys with units: start=40C ramp=300C@10K/min hold=5min interval=1s ...)',
                           self.h_method, variadic=('KEY=VALUE', 'a setting'), options=self.tgt_opts('method'),
                           preconditions=['no run in progress', 'every value inside the instrument range (usage error otherwise)'],
                           errors=['UNKNOWN_SETTING, MISSING_SETTING, OUT_OF_RANGE (usage)', 'BUSY (refused)'],
                           details=self.method_details(), example='chemlab run INST method start=40C ramp=200C@10K/min hold=1min', reference=REF),
            'set-temp': Verb('set-temp', 'set the cell / furnace / bath temperature (isothermal), or switch the control off', self.h_set_temp,
                             positionals=[temp], options={'rate': Opt('heating / cooling rate, e.g. 20K/min (default: the contract\'s example rate)', kind='heating_rate', metavar='RATE')},
                             preconditions=['inside the instrument range', 'no run in progress', 'the member\'s own state (closed furnace, purge)'],
                             errors=['OUT_OF_RANGE (usage)', 'BUSY, NO_PURGE, FURNACE_OPEN (refused)'], example='chemlab run INST set-temp 140C --rate 20K/min', reference=REF),
            'temp': Verb('temp', 'the temperatures and the run state, in °C', self.h_temp, readonly=True, example='chemlab run INST temp'),
            'wait-temp': Verb('wait-temp', 'wait until the specimen is at the set point (or --to T), moving lab time', self.h_wait_temp, readonly=True,
                              long_action=True, options={'within': Opt('tolerance (default the member\'s, about 0.5 K)', kind='temperature_delta', metavar='DELTA'),
                                                         'max': Opt('longest wait (default 1 h)', kind='duration', metavar='TIME'),
                                                         'to': Opt('wait for this temperature instead of the set point', kind='temperature', metavar='TEMP')},
                              errors=['CONTROL_OFF, NOT_REACHED (refused)'], example='chemlab run INST wait-temp --within 0.5K --max 30min'),
            'gas': Verb('gas', 'purge gas: on, off, a flow (50mL/min), or a gas name (N2, O2) [and a flow]', self.h_gas,
                        positionals=[Arg('WHAT', 'on, off, a flow like 50mL/min, or a gas name'), Arg('FLOW', 'a flow after a gas name, or on/off', required=False)],
                        options=dict(self.tgt_opts('gas'), balance=Opt('balance purge flow (TGA; default: keep the current one)', kind='flow', metavar='FLOW')),
                        preconditions=['flow inside the mass-flow range', 'a known gas'], errors=['OUT_OF_RANGE, BAD_CHOICE (usage)'],
                        example='chemlab run INST gas N2 50mL/min', reference=REF),
            'start': Verb('start', 'start the run of the configured method (gives job-N)', self.h_start, long_action=True, options=self.tgt_opts('start'),
                          preconditions=['not already running', 'specimen loaded, furnace / lid closed, purge on (member requirements)'],
                          errors=['BUSY, NO_SAMPLE, FURNACE_OPEN, NOT_EQUILIBRATED, NO_PURGE (refused)'], example='chemlab run INST start', reference=REF),
            'stop': Verb('stop', 'stop the run', self.h_stop, errors=['NOT_RUNNING (refused)'], example='chemlab run INST stop'),
            'result': Verb('result', 'the record of the last run (curve, points, values)', self.h_result, readonly=True,
                           options={'save-to': save_to_opt()}, example='chemlab run INST result --save-to dsc.json'),
            'open': Verb('open', 'open the lid / furnace / cover (or the purge valve: open gas), waiting until it is open', self.h_part('open'),
                         positionals=[Arg('PART', 'lid, furnace, cover or gas; optional when there is one main part', required=False)],
                         long_action=True, errors=['UNKNOWN_PART, WHICH_PART (usage)', 'NOT_SETTLED (refused)'],
                         details='waits until the part has stopped moving (lab time moves, up to about a minute).', example='chemlab run INST open furnace'),
            'close': Verb('close', 'close the lid / furnace / cover (or the purge valve: close gas), waiting until it is closed', self.h_part('close'),
                          positionals=[Arg('PART', 'lid, furnace, cover or gas; optional when there is one main part', required=False)],
                          long_action=True, errors=['UNKNOWN_PART, WHICH_PART (usage)', 'NOT_SETTLED (refused)'],
                          details='waits until the part has stopped moving (lab time moves, up to about a minute).', example='chemlab run INST close furnace'),
            'analyze': self.analyze_verb(),
            'zero': Verb('zero', 'zero / tare the measuring sensor (empty cell)', self.h_simple('zero', 'sensor zeroed'),
                         preconditions=['empty cell (member requirement)'], errors=['NOT_EMPTY, NO_CONTACT (refused)'], example='chemlab run INST zero', reference=REF),
            'calibrate': Verb('calibrate', 'set the sensor calibration (correction factors) or the vessel C value: KEY=VALUE ...', self.h_calibrate,
                              variadic=('KEY=VALUE', 'a correction'), preconditions=['no run in progress', 'every factor given (1 = no gain correction, 0 = no offset)'],
                              errors=['MISSING_SETTING, OUT_OF_RANGE (usage)'], example='chemlab run INST calibrate temperature_gain_correction=1 temperature_zero_k=0.2K ...', reference=REF),
        }

    def method_details(self):
        m = self.map.get('verbs', {}).get('method') or {}
        specs = m.get('calls') or ([m] if m else [])
        lines = []
        for sp in specs:
            pg = sp.get('program') or {}
            for k, ks in sorted((sp.get('keys') or {}).items()):
                lines.append('  %-12s -> %s%s' % (k + '=', ks['arg'], (' (%s, e.g. %s)' % (ks['kind'], kind_example(ks['kind']))) if ks.get('kind') else ''))
            if pg:
                lines.append('  %-12s -> one %s item%s' % (pg['key'] + '=', pg['arg'], ' (TARGET@RATE[:HOLD], e.g. 300C@10K/min:5min)' if pg.get('style') == 'ramp' else ' (TEMP, e.g. 30C)'))
                for k in ('range_key', 'iso', 'hold_key'):
                    if pg.get(k):
                        lines.append('  %-12s -> %s' % (pg[k] + '=', {'range_key': 'FROM..TO/STEP, many items', 'iso': 'an isothermal segment of this time',
                                                                   'hold_key': 'hold time of the segment before it'}[k]))
                for k, fs in sorted((pg.get('sticky') or {}).items()):
                    lines.append('  %-12s -> %s of the items that follow (or all, when given last)' % (k + '=', fs['field']))
        if not lines:
            return None
        return 'class keys on this instrument:\n' + '\n'.join(lines) + '\nnative argument names pass through (a bare number is in the declared unit).'

    # ------------------------------------------------------------------ load / unload
    def h_load(self, ctx, p):
        m = self.vmap('load')
        kind = m['kind']
        where = p.pos[0] if p.pos else None
        if kind != 'vessel' and self.running(ctx):
            ctx.fail(refused('BUSY', '%s is running.' % self.instrument_id, 'wait for the run or stop it.'))
        if p.get('preload') is not None and not m.get('preload'):
            ctx.fail(unsupported('NO_PRELOAD', '%s has no static preload.' % self.instrument_id, 'leave out --preload.'))
        if kind == 'vessel':
            return self._load_vessel(ctx, m, where)
        if kind in ('transfer', 'select'):
            if where is None:
                ctx.fail(invalid('MISSING_ARGUMENT', 'load needs the position on %s.' % self.instrument_id, 'e.g. `chemlab run %s load 1`.' % self.instrument_id))
            pos = to_native(where, self.props(m['cap']).get(m['arg']) or {}, None, 'position')
            if m.get('tray_slot'):
                tray = ctx.v(m['tray_slot']) or []
                ctx.check(0 < pos <= len(tray) and tray[pos - 1], 'EMPTY_POSITION', 'tray position %d of %s holds no pan.' % (pos, self.instrument_id),
                          'positions with a pan: %s.' % (', '.join(str(i + 1) for i, x in enumerate(tray) if x) or 'none (ask the lab operator)'), label='pan at the position')
            if m.get('position_slot') and ctx.v(m['position_slot']) is not None:
                at = ctx.v(m['position_slot'])
                ctx.check(at == pos, 'EMPTY_POSITION', 'changer position %d of %s holds no specimen; the specimen sits at position %s.' % (pos, self.instrument_id, at),
                          'select that position: `chemlab run %s load %s`.' % (self.instrument_id, at), label='specimen at the position')
            if m.get('loaded_slot'):
                ctx.check(not ctx.v(m['loaded_slot']), 'ALREADY_LOADED', 'a pan is already in the cell of %s.' % self.instrument_id,
                          'unload it first: `chemlab run %s unload`.' % self.instrument_id, label='cell empty')
            self.need(ctx, 'load')
            ctx.call(m['cap'], {m['arg']: pos}, 'load position %d' % pos)
            waited = self.settle(ctx, self.moving_done(m.get('busy') or []), 'the transfer has ended', float(m.get('max_s', 300)))
            text = 'ok: %s position %d in the measuring position (after %s)\n' % (self.instrument_id, pos, units.fmt_s(waited))
            return {'text': text, 'json': {'position': pos, 'waited_s': waited}}
        if kind == 'set':
            done_slot = m.get('done_slot')
            changed = False
            if p.get('preload') is not None:
                self.need(ctx, 'load-preload')
            if done_slot and ctx.v(done_slot) == m['value']:
                text = 'ok: %s already %s\n' % (self.instrument_id, m.get('word', 'loaded'))
            else:
                self.need(ctx, 'load')
                ctx.call(m['cap'], {m['arg']: m['value']}, m.get('word', 'load'))
                waited = self.settle(ctx, self.moving_done(m.get('busy') or []), '%s has ended' % m.get('word', 'the movement'), float(m.get('max_s', 120)))
                text = 'ok: %s %s (after %s)\n' % (self.instrument_id, m.get('word', 'loaded'), units.fmt_s(waited))
                changed = True
            out = {'changed': changed}
            if p.get('preload') is not None:
                pl = m['preload']
                self.need(ctx, 'load-preload')
                f = to_native(p.raw['preload'], self.props(pl['cap']).get(pl['force_arg']) or {}, 'force', '--preload')
                args = {pl['force_arg']: f}
                if pl.get('ramp_arg'):
                    args[pl['ramp_arg']] = self.example(pl['cap']).get(pl['ramp_arg'])
                ctx.call(pl['cap'], args, 'apply a preload of %s' % fmt_q(f, 'force'))
                w2 = self.settle(ctx, self.moving_done(pl.get('busy') or []), 'the preload is reached', 120.0)
                text += 'ok: preload %s (after %s)\n' % (fmt_q(f, 'force'), units.fmt_s(w2))
                out['preload_n'] = f
            return {'text': text, 'json': out}
        raise unexpected('MAPPING', 'unknown load kind %r' % kind, 'report this.')

    def _load_vessel(self, ctx, m, rfid):
        if not rfid:
            ctx.fail(invalid('MISSING_ARGUMENT', 'load needs the vessel RFID on %s.' % self.instrument_id, 'e.g. `chemlab run %s load C6012-0001`.' % self.instrument_id))
        phase = ctx.v(m['phase_slot'])
        ctx.check(phase == m['await_phase'], 'NOT_WAITING', '%s is not waiting for a vessel (measurement phase %s); it identifies the vessel '
                  'after the measurement has started.' % (self.instrument_id, phase),
                  'create and start a measurement first (`chemlab run %s method ...`, `chemlab run %s start`), then load the vessel.' % (
                      self.instrument_id, self.instrument_id), label='a measurement waits for the vessel')
        self.need(ctx, 'load')
        if ctx.v(m['auto_slot']):
            ctx.call(m['scan_cap'], {'rfid': rfid}, 'scan vessel %s' % rfid)
        ctx.call(m['cap'], {'rfid': rfid}, 'hang vessel %s' % rfid)
        ctx.call(m['confirm_cap'], {}, 'confirm the vessel is hung')
        return {'text': 'ok: vessel %s hung and confirmed; %s closes and measures (phase %s)\n' % (rfid, self.instrument_id, ctx.v(m['phase_slot'])),
                'json': {'rfid': rfid, 'phase': ctx.v(m['phase_slot'])}}

    def h_unload(self, ctx, p):
        m = self.vmap('unload')
        kind = m['kind']
        if kind != 'vessel' and self.running(ctx):
            ctx.fail(refused('BUSY', '%s is running.' % self.instrument_id, 'wait for the run or stop it.'))
        self.need(ctx, 'unload')
        if kind == 'transfer':
            ctx.check(bool(ctx.v(m['loaded_slot'])), 'NOT_LOADED', 'no pan is in the cell of %s.' % self.instrument_id, 'nothing to unload.', label='a pan in the cell')
            if p.pos:
                pos = to_native(p.pos[0], self.props(m['cap']).get(m['arg']) or {}, None, 'position')
            else:
                pos = ctx.v(m['origin_slot'])
                if not pos:
                    ctx.fail(invalid('MISSING_ARGUMENT', 'unload needs the tray position on %s (the origin is unknown).' % self.instrument_id,
                                     'e.g. `chemlab run %s unload 1`.' % self.instrument_id))
            ctx.call(m['cap'], {m['arg']: pos}, 'unload to position %d' % pos)
            waited = self.settle(ctx, self.moving_done(m.get('busy') or []), 'the transfer has ended', float(m.get('max_s', 300)))
            return {'text': 'ok: %s pan back at tray position %d (after %s)\n' % (self.instrument_id, pos, units.fmt_s(waited)), 'json': {'position': pos}}
        if kind == 'set':
            if m.get('done_slot') and ctx.v(m['done_slot']) == m['value']:
                return {'text': 'ok: %s already %s\n' % (self.instrument_id, m.get('word', 'unloaded')), 'json': {'changed': False}}
            text = ''
            pl = m.get('release')
            if pl and finite(ctx.v(pl['slot'])) and ctx.v(pl['slot']) > 0:
                args = {pl['ramp_arg']: self.example(pl['cap']).get(pl['ramp_arg'])} if pl.get('ramp_arg') else {}
                ctx.call(pl['cap'], args, 'release the preload')
                self.settle(ctx, lambda c: not c.v(pl['slot']) and all(not c.v(s) for s in pl.get('busy') or []), 'the preload is released', 120.0)
                text += 'ok: preload released\n'
            ctx.call(m['cap'], {m['arg']: m['value']}, m.get('word', 'unload'))
            waited = self.settle(ctx, self.moving_done(m.get('busy') or []), '%s has ended' % m.get('word', 'the movement'), float(m.get('max_s', 120)))
            return {'text': text + 'ok: %s %s (after %s)\n' % (self.instrument_id, m.get('word', 'unloaded'), units.fmt_s(waited)), 'json': {'changed': True}}
        if kind == 'vessel':
            ctx.check(bool(ctx.v(m['hung_slot'])), 'NOT_LOADED', 'no vessel hangs in %s.' % self.instrument_id, 'nothing to unload.', label='a vessel hung')
            rf = ctx.v(m['hung_slot'])
            ctx.call(m['cap'], {}, 'take vessel %s off the filling head' % rf)
            return {'text': 'ok: vessel %s back on the bench\n' % rf, 'json': {'rfid': rf}}
        raise unexpected('MAPPING', 'unknown unload kind %r' % kind, 'report this.')

    # ------------------------------------------------------------------ gas
    def h_gas(self, ctx, p):
        m = self.vmap('gas')
        words = [p.pos[0]] + ([p.pos[1]] if len(p.pos) > 1 else [])
        gas, flow, onoff = None, None, None
        for w in words:
            lw = w.lower()
            if lw in ('on', 'off'):
                onoff = lw
            elif re.match(r'^\s*[+-]?(\d|\.\d)', w):
                flow = parse_q(w, 'flow', 'flow')
            else:
                gas = w
        if gas is not None and not m.get('select_cap'):
            ctx.fail(unsupported('NO_GAS_SELECT', '%s has no gas selection (one purge line).' % self.instrument_id, 'leave out the gas name.'))
        if flow is not None and not m.get('flow_cap'):
            ctx.fail(unsupported('NO_FLOW', '%s has no purge flow setting; the purge is only on or off.' % self.instrument_id, 'use `gas on` or `gas off`.'))
        if p.get('balance') is not None and not m.get('balance_arg'):
            ctx.fail(unsupported('NO_BALANCE_GAS', '%s has no balance purge.' % self.instrument_id, 'leave out --balance.'))
        tg = self.targets_for(ctx, p, 'gas')
        calls = []
        reopen = False
        if gas is not None or (m.get('select_cap') and any(a in self.props(m['select_cap']) for a in tg)):
            args = {a: v for a, v in tg.items() if a in self.props(m['select_cap'])}
            if gas is not None:
                args[m['select_arg']] = to_native(gas, self.props(m['select_cap']).get(m['select_arg']) or {}, None, 'gas')
            sel_slot = m.get('selected_slot')
            if not (sel_slot and all(ctx.v(sel_slot) == v for v in args.values())):
                if m.get('close_to_select') and ctx.v(m['open_slot']):
                    calls.append((m['open_cap'], {m['open_arg']: False}, 'close the purge valve to switch gas'))
                    reopen = onoff != 'off'
                calls.append((m['select_cap'], args, 'select %s' % ', '.join('%s' % v for v in args.values())))
        if flow is not None:
            props = self.props(m['flow_cap'])
            args = {a: v for a, v in tg.items() if a in props}
            args[m['flow_arg']] = self._si_item(ctx, props.get(m['flow_arg']) or {}, flow, 'flow', 'flow')
            if m.get('balance_arg'):
                if p.get('balance') is not None:
                    args[m['balance_arg']] = self._si_item(ctx, props.get(m['balance_arg']) or {}, p.get('balance'), 'flow', '--balance')
                else:
                    args[m['balance_arg']] = ctx.v(m['balance_slot'])
            calls.append((m['flow_cap'], args, 'set the purge flow to %s' % fmt_q(flow, 'flow')))
        want_open = onoff != 'off' if (onoff or flow is not None or reopen) else None
        if want_open and m.get('open_needs_flow'):
            f_now = flow if flow is not None else ctx.v(m['flow_slot'])
            ctx.check(finite(f_now) and f_now > 0, 'NO_FLOW', '%s opens its purge only with a flow set (now 0).' % self.instrument_id,
                      'give one: `chemlab run %s gas 25mL/min --balance 10mL/min`.' % self.instrument_id, label='purge flow set')
            if m.get('balance_arg'):
                b_now = p.get('balance') if p.get('balance') is not None else ctx.v(m['balance_slot'])
                ctx.check(finite(b_now) and b_now > 0, 'NO_BALANCE_FLOW', '%s opens its purge only with the balance purge flowing too (now 0).' % self.instrument_id,
                          'add it: `chemlab run %s gas %s --balance 10mL/min`.' % (self.instrument_id, ' '.join(words)), label='balance purge set')
        if want_open is not None:
            open_slot = m.get('open_slot')
            if isinstance(open_slot, dict):
                open_slot = open_slot.get(str(tg.get(m.get('channel_arg'))) if tg else None)
            if reopen or not (open_slot and bool(ctx.v(open_slot)) == want_open and onoff is None):
                args = {a: v for a, v in tg.items() if a in self.props(m['open_cap'])}
                args[m['open_arg']] = want_open
                calls.append((m['open_cap'], args, 'purge %s' % ('on' if want_open else 'off')))
        if not calls:
            ctx.fail(invalid('NOTHING_TO_DO', 'gas %s changes nothing.' % ' '.join(words), 'write `gas on`, `gas off`, `gas 50mL/min` or `gas N2`.'))
        self.need(ctx, 'gas')
        for cap, args, step in calls:
            ctx.call(cap, args, step)
        return {'text': 'ok: %s %s\n' % (self.instrument_id, '; '.join(c[2] for c in calls)), 'json': {'calls': [{'capability': c[0], 'arguments': c[1]} for c in calls]}}

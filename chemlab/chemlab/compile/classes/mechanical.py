"""Mechanical testing and rheology class ("mechanical"): tribometer, scratch tester, nanoindenter, universal testing machine,
pendulum impact tester, melt flow indexer, rotational rheometer, powder rheometer.  Operator action: load a specimen and measure
its mechanical response.

Verbs: clamp on|off, contact on|off, zero, calibrate [KEY=VALUE ...], move POS [--y Y] [--speed V] [--wait], method KEY=VALUE ...,
set-temp T [--wait], start (job-N), stop, result [--save-to F], analyze [KEY=VALUE ...] [--save-to F], status.  power, open/close,
ack, estop and wait come from the shared layer (configs/adapters/shared.json).  Members and mappings: configs/adapters/mechanical.json.

Vendor-neutral references: ASTM E8 / ISO 6892-1 and ASTM D638 (tensile: test speed, force, extension, modulus fit), ISO 14577
(instrumented indentation, Oliver-Pharr), ASTM C1624 / ISO 20502 (scratch: progressive load, critical load), ASTM G99 (pin-on-disk:
normal load, track radius, sliding distance), ISO 179-1 (Charpy: pendulum friction calibration, absorbed energy), ISO 1133 (melt
flow: test temperature, nominal load, preheat, measuring window), ISO 3219 (rotational rheometry: gap, shear rate, oscillation),
ASTM D7891 (powder shear, FT4 dynamic method).  No public remote command set exists for these testers (the vendors' PC
software only), so the verbs follow the standards' vocabulary.

The canonical method keys of the class (each member maps the ones it has; native argument names pass through, parsed into the
unit the argument declares; anything not given comes from the member's method template, the contract's reference method):

    mode=      test mode (speed | force | strain, rotation | oscillation, dynamic | shear | ..., prescan | scratch | postscan)
    load=      test / normal / peak force (5N, 500mN, 2kN); melt-flow nominal load as a mass (2.16kg)
    speed=     test speed (crosshead / stage / blade: 5mm/min, 0.1mm/s; disk: 100rpm)
    time=      test duration (20s, 5min)          interval=  sampling interval (0.1s)
    rate=      loading / unloading rate (1.7mN/s)  hold=  hold time at peak load (5s)
    strain=    strain or strain amplitude (1%, 0.01)  freq=  frequency (1Hz)    shear-rate=  shear rate (100, in 1/s)
    distance=  sliding distance (100m)  start= / length= / end=  path start, length, end (mm)
    temp-tol=  temperature tolerance before the start (0.5K)   preheat=  preheat time (5min)
    window=    melt-flow measuring window as UPPER..LOWER piston positions (40mm..10mm)
"""
import copy
import json
import math
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import CompileError, invalid, refused, unexpected
from ..jobs import OperationJob, StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt
from ...contract import validate
from ...generic import CmdError, coerce

ON_OFF = ['on', 'off']
RUNNING_PHASES = ('running', 'paused', 'heating', 'holding', 'cooling', 'pouring', 'fusion', 'awaiting_manual_pump')
_RATE = re.compile(r'^(.*?)/(s|min)$', re.I)


def _get(obj, path):
    """A value from a nested dict by 'a.b.c' (list index allowed: rows.-1.x)."""
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


def _shown(v):
    """A state value for a message: floats to 4 significant digits."""
    return ('%.4g' % v) if isinstance(v, float) else v


def _nonnull(schema):
    """The non-null branch of an anyOf schema (or the schema itself), and whether null is allowed."""
    if 'anyOf' in schema:
        branches = [b for b in schema['anyOf'] if isinstance(b, dict)]
        nullable = any(b.get('type') == 'null' for b in branches)
        main = next((b for b in branches if b.get('type') != 'null'), {})
        return main, nullable
    return schema, schema.get('type') == 'null'


class TesterBase(ClassAdapter):
    """What the mechanical and solid_prep classes share: settings with a method template, start / stop / result with jobs,
    set-temp, the class view from the member's status slots, refusal explanations from the member's requirements."""
    PATH_GROUPS = (('start', 'stop', 'result'),)
    KEY_HELP = {}

    # ------------------------------------------------------------------ values typed by the agent
    def props(self, cap):
        return ((self.lab.contract['capabilities'].get(cap) or {}).get('input_schema') or {}).get('properties') or {}

    def template(self, cap):
        """The member's method template for a capability: the contract's reference arguments (example_arguments)."""
        return copy.deepcopy((self.lab.contract['capabilities'].get(cap) or {}).get('example_arguments') or {})

    def _range_text(self, lo, hi, u):
        kind = units.unit_kind(u) if u else None

        def f(x):
            if x is None:
                return 'open'
            return units.fmt(units.from_declared(x, u), kind) if kind else ('%g%s' % (x, (' ' + u) if u else ''))
        return f(lo), f(hi), kind

    def parse_setting(self, ctx, cap, arg, text, kind=None, what=None, native=False):
        """Agent text -> the value of a native argument, in the unit it declares, checked against its range and enum (usage errors,
        nothing sent).  ``kind`` overrides the quantity kind (e.g. temperature_delta, force_rate).  ``native``: the agent used the
        native argument name, so a bare number is already in the declared unit (duration_s=60, x_m=0.01); class keys always
        take units (a bare number there is the kind's default unit)."""
        return self.parse_schema(ctx, self.cap_schema(cap, arg), text, kind, what or arg, native=native)

    def parse_schema(self, ctx, schema, text, kind=None, what='value', native=False):
        schema, nullable = _nonnull(schema)
        t = (text or '').strip()
        if native and schema.get('type') in ('number', 'integer') and re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', t):
            kind = 'plain'
        if nullable and t.lower() in ('null', 'none', '-'):
            return None
        typ = schema.get('type')
        if typ in ('number', 'integer'):
            u = units.declared(schema)
            if kind in ('force_rate',):
                m = _RATE.match(t.replace(' ', ''))
                if not m:
                    ctx.fail(invalid('BAD_NUMBER', '%s=%s is not a rate.' % (what, text), 'write it like 1.7mN/s or 0.1N/min.'))
                v = units.parse(m.group(1), 'force', what=what) / (60.0 if m.group(2).lower() == 'min' else 1.0)
            elif kind and kind != 'plain' and (not u or not units.unit_kind(u) or kind == 'temperature_delta'):
                v = units.parse_for(t, schema, kind, what=what) if u and units.unit_kind(u) else units.parse(t, kind, what=what)
            elif u and units.unit_kind(u) and kind != 'plain':
                v = units.parse_for(t, schema, kind, what=what)
            else:
                m = re.fullmatch(r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)(%?)', t.replace(' ', ''))
                if not m:
                    ctx.fail(invalid('BAD_NUMBER', '%s=%s is not a plain number%s.' % (what, text, ' in %s' % u if u else ''),
                                     'write a number%s, e.g. %s=%s.' % (' in %s' % u if u else '', what, schema.get('minimum', 1))))
                v = float(m.group(1)) / (100.0 if m.group(2) else 1.0)
            if typ == 'integer':
                if abs(v - round(v)) > 1e-9:
                    ctx.fail(invalid('NOT_AN_INTEGER', '%s=%s must be a whole number.' % (what, text), 'give a whole number.'))
                v = int(round(v))
            lo, hi = schema.get('minimum'), schema.get('maximum')
            if (lo is not None and v < lo - 1e-12 * max(1.0, abs(lo))) or (hi is not None and v > hi + 1e-12 * max(1.0, abs(hi))):
                a, b, _ = self._range_text(lo, hi, u)
                ctx.fail(invalid('OUT_OF_RANGE', '%s=%s is outside the range of %s (%s to %s).' % (what, text, self.instrument_id, a, b),
                                 'give %s from %s to %s.' % (what, a, b)))
            if 'enum' in schema and v not in schema['enum']:
                ctx.fail(invalid('BAD_CHOICE', '%s=%s is not one of the values %s takes.' % (what, text, self.instrument_id),
                                 'use one of: %s.' % ', '.join(map(str, schema['enum']))))
            return v
        if typ == 'boolean':
            low = t.lower()
            if low in ('on', 'true', 'yes', '1'):
                return True
            if low in ('off', 'false', 'no', '0'):
                return False
            ctx.fail(invalid('BAD_VALUE', '%s=%s is not on/off.' % (what, text), 'write %s=on or %s=off.' % (what, what)))
        if 'enum' in schema:
            if t not in [str(x) for x in schema['enum']]:
                ctx.fail(invalid('BAD_CHOICE', '%s=%s is not one of the values %s takes.' % (what, text, self.instrument_id),
                                 'use one of: %s.' % ', '.join(map(str, schema['enum']))))
            return next(x for x in schema['enum'] if str(x) == t)
        try:
            return coerce(t, schema, what)
        except CmdError as exc:
            ctx.fail(invalid(exc.code, exc.message, exc.fix))

    def custom_value(self, ctx, spec, text, what):
        """Class-specific value syntaxes (overridden by classes): None when the spec has no special kind."""
        if spec.get('syntax') == 'range':                                     # window=40mm..10mm -> two arguments
            a, sep, b = text.partition('..')
            if not sep:
                ctx.fail(invalid('BAD_VALUE', '%s=%s is not a range.' % (what, text), 'write %s=UPPER..LOWER, e.g. %s=40mm..10mm.' % (what, what)))
            return [a, b]
        return None

    def settings(self, ctx, spec, items, verb):
        """KEY=VALUE items -> [(cap, args, given)] for the spec and its "also" capabilities.  Each call carries the member's
        template (contract reference arguments) + "keep" (current values from slots) + fixed "args" + what the agent gave."""
        groups = list(spec.get('also') or []) + [spec]           # the extra capabilities first: they are the ones a state refuses
        given = [dict() for _ in groups]
        names = [list() for _ in groups]
        for item in items:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write %s KEY=VALUE ... (see `chemlab help %s %s`).' % (
                    verb, self.instrument_id, verb)))
            found = None
            for i, g in enumerate(groups):
                if k in (g.get('keys') or {}):
                    found = (i, dict(g['keys'][k]))
                    break
            if found is None:
                for i, g in enumerate(groups):
                    if g.get('passthrough', True) and k in self.props(g['cap']) and k not in (g.get('args') or {}) and k not in self.hidden(g):
                        found = (i, {'arg': k, 'native': True})
                        break
            if found is None:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no %s setting %r.' % (self.instrument_id, verb, k),
                                 'settings: %s (see `chemlab help %s %s`).' % (', '.join(self.setting_names(spec)), self.instrument_id, verb)))
            i, ks = found
            cap = groups[i]['cap']
            if ks.get('field'):                                                # one member of an object argument
                a = ks['arg']
                sub = (self.cap_schema(cap, a).get('properties') or {}).get(ks['field']) or {}
                obj = given[i].setdefault(a, copy.deepcopy(self.template(cap).get(a) or {}))
                obj[ks['field']] = self.parse_schema(ctx, sub, v, ks.get('kind'), what=k)
                names[i].append(k)
                continue
            args = ks.get('args') or [ks['arg']]
            parts = self.custom_value(ctx, ks, v, k)
            if parts is not None and len(parts) == len(args):
                for a, text in zip(args, parts):
                    given[i][a] = self.parse_setting(ctx, cap, a, text, ks.get('kind'), what=k)
            else:
                for a in args:
                    given[i][a] = self.parse_setting(ctx, cap, a, parts if isinstance(parts, str) else v, ks.get('kind'), what=k,
                                                     native=ks.get('native', False))
            names[i].append(k)
        calls = []
        for i, g in enumerate(groups):
            if not given[i] and not (g is spec and g.get('always')):
                continue
            args = self.template(g['cap']) if g.get('template', True) else {}
            props = self.props(g['cap'])
            args = {a: x for a, x in args.items() if a in props}
            for a, slot in (g.get('keep') or {}).items():
                cur = ctx.v(slot)
                if cur is not None:
                    args[a] = cur
            args.update(copy.deepcopy(g.get('args') or {}))
            args.update(given[i])
            calls.append((g['cap'], args, given[i], names[i]))
        return calls

    @staticmethod
    def hidden(g):
        """Native arguments an evaluation fills from the result (not settable by KEY=VALUE)."""
        return set(g.get('columns') or {}) | set(g.get('from') or {}) | set(g.get('from_slots') or {}) | set(g.get('from_list') or {}) | \
            set(g.get('from_rows') or []) | ({g['rows_arg']} if g.get('rows_arg') else set())

    def setting_names(self, spec):
        out = []
        for g in [spec] + list(spec.get('also') or []):
            out += [k for k in (g.get('keys') or {}) if k not in out]
            if g.get('passthrough', True):
                out += [k for k in self.props(g['cap']) if k not in out and k not in (g.get('args') or {}) and k not in self.hidden(g)]
        return out

    def settings_help(self, verb):
        spec = self.vmap(verb) or {}
        if not spec.get('cap'):
            return []
        lines = ['settings on %s (KEY=VALUE; class keys first, then native argument names):' % self.instrument_id]
        for g in [spec] + list(spec.get('also') or []):
            props = self.props(g['cap'])
            tmpl = self.template(g['cap']) if g.get('template', True) else {}
            for k, ks in (g.get('keys') or {}).items():
                args = ks.get('args') or [ks['arg']]
                lines.append('  %-12s -> %s.%s%s%s' % (k, g['cap'], '+'.join(args), ('.' + ks['field']) if ks.get('field') else '',
                                                       ('  (%s)' % self.KEY_HELP[k]) if k in self.KEY_HELP else ''))
            if g.get('passthrough', True):
                rest = [a for a in props if a not in (g.get('args') or {}) and a not in self.hidden(g)]
                lines.append('  native %s arguments: %s' % (g['cap'], ', '.join('%s%s' % (a, ' [%s]' % units.declared(props[a])
                                                                                      if units.declared(_nonnull(props[a])[0]) else '') for a in rest)))
            if self.hidden(g):
                lines.append('  from the last result: %s' % ', '.join(sorted(self.hidden(g))))
            tmpl = {a: v for a, v in tmpl.items() if a in props and a not in self.hidden(g)}
            if tmpl:
                lines.append('  template (what you do not give): %s' % short(tmpl, 400))
        return lines

    def apply_settings(self, ctx, calls, what):
        done = []
        for cap, args, given, names in calls:
            step = '%s %s (%s)' % (what, cap, ', '.join('%s=%s' % (k, short(v, 24)) for k, v in given.items()) or 'template')
            ctx.call(cap, args, step)
            done.append({'capability': cap, 'arguments': args, 'given': given})
        return done

    # ------------------------------------------------------------------ requirements of a verb (checked before sending)
    def unmet(self, ctx, verb):
        """The member's requirements for a verb from its mapping ("requires": {verb: [{"slot", "value" | "not" | "min" | "max" |
        "present", "code", "message", "fix"}]}): the first one not met, or None."""
        for r in (self.map.get('requires') or {}).get(verb, []):
            got = ctx.v(r['slot'])
            if 'value' in r:
                ok = got == r['value']
            elif 'not' in r:
                ok = got not in r['not']
            elif 'max' in r:
                ok = finite(got) and got <= r['max']
            elif 'min' in r:
                ok = finite(got) and got >= r['min']
            elif 'present' in r:
                ok = (got is not None) == r['present']
            elif 'equals_slot' in r:
                other = ctx.v(r['equals_slot'])
                ok = finite(got) and finite(other) and abs(got - other) <= 1e-9
            else:
                ok = bool(got)
            if not ok:
                return r, got
            ctx.checks.append(r.get('label') or self.req_label(r))
        return None

    @staticmethod
    def req_label(r):
        for k, op in (('value', '='), ('max', '<='), ('min', '>='), ('equals_slot', '=')):
            if k in r:
                return '%s %s %s' % (r['slot'], op, json.dumps(r[k]))
        if 'not' in r:
            return '%s not in %s' % (r['slot'], json.dumps(r['not']))
        return r['slot']

    def need(self, ctx, verb):
        bad = self.unmet(ctx, verb)
        if bad:
            r, got = bad
            got = _shown(got)
            ctx.fail(refused(r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=got),
                             r['fix'].format(inst=self.instrument_id, value=got)))

    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        mapped = (self.map.get('codes') or {}).get('%s:%s' % (cap, code)) or (self.map.get('codes') or {}).get(code) or self.CODES.get(code)
        if code in ('PRECONDITION_FAILED', 'NOT_READY') and not mapped and ctx.verb is not None:
            ctx.refresh()
            for verb in [ctx.verb.name] + list((self.map.get('explain') or {}).get(ctx.verb.name, [])):
                bad = self.unmet(ctx, verb)
                if bad:
                    r, got = bad
                    got = _shown(got)
                    return refused(r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=got) + ' (it answered %s)' % code,
                                   r['fix'].format(inst=self.instrument_id, value=got))
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)

    # ------------------------------------------------------------------ class view
    def show_value(self, v, kind, u=None):
        if v is None:
            return 'n/a'
        if kind == 'bool':
            return 'yes' if v else 'no'
        if kind == 'stress' and finite(v):
            for f, lab in ((1e9, 'GPa'), (1e6, 'MPa'), (1e3, 'kPa'), (1.0, 'Pa')):
                if abs(v) >= f or f == 1.0:
                    return '%.4g %s' % (v / f, lab)
        if kind and finite(v):
            return units.fmt(units.from_declared(v, u) if u else v, kind)
        if isinstance(v, float):
            return '%.6g' % v
        if isinstance(v, str):
            return v
        return short(v, 60)

    def summary(self, values):
        get = values.v if hasattr(values, 'v') else values.get
        p = self.slot('power')
        if p and get(p) is False:
            return ['power off'], {'power': False}
        parts, js = [], {}
        for row in self.map.get('status') or []:
            label, slot, kind = (list(row) + [None, None])[:3]
            v = get(slot.split('#')[0])
            if '#' in slot and isinstance(v, dict):
                v = v.get(slot.split('#')[1])
            u = units.declared((self.lab.contract['state_slots'].get(slot) or {})) if self.lab and '#' not in slot else None
            parts.append('%s %s' % (label, self.show_value(v, kind, u)))
            js[re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_')] = v
        lines = ['; '.join(parts[i:i + 4]) for i in range(0, len(parts), 4)]
        return lines or ['(see `chemlab read %s`)' % self.instrument_id], js

    # ------------------------------------------------------------------ shared verb handlers
    def h_settings(self, verb, what):
        def h(ctx, p):
            spec = self.vmap(verb)
            if not p.extra and not spec.get('always'):
                ctx.fail(invalid('MISSING_SETTING', '%s needs at least one KEY=VALUE.' % verb,
                                 'see `chemlab help %s %s` for the settings and the template.' % (self.instrument_id, verb)))
            calls = self.settings(ctx, spec, p.extra, verb)
            self.need(ctx, verb)
            done = self.apply_settings(ctx, calls, what)
            sent = {c['capability']: c['arguments'] for c in done}
            lines = ['ok: %s %s set' % (self.instrument_id, what)]
            for c in done:
                lines.append('  %s: %s' % (c['capability'], short(c['given'], 200)))
                rest = {a: v for a, v in c['arguments'].items() if a not in c['given']}
                if rest:
                    lines.append('    (from the template / current state: %s)' % short(rest, 300))
            return {'text': '\n'.join(lines) + '\n', 'json': {'sent': sent}}
        return h

    def h_start(self, ctx, p):
        m = self.vmap('start')
        self.need(ctx, 'start')
        args = dict(m.get('args') or {})
        if p.get('delay') is not None:
            if not m.get('delay_cap'):
                ctx.fail(invalid('NO_DELAYED_START', '%s has no delayed start.' % self.instrument_id, 'leave out --delay.'))
            cap = m['delay_cap']
            args = {m['delay_arg']: self.parse_setting(ctx, cap, m['delay_arg'], p.raw['delay'], 'duration', what='--delay')}
        else:
            cap = m['cap']
        r = ctx.call(cap, args, 'start (%s)' % cap)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') if isinstance(r.get('result'), dict) else {}
        opid = r.get('operation_id') or res.get('operation_id')
        job = None
        if opid and r.get('operation_id'):
            job = OperationJob(ctx.core, self, ctx.req, '%s %s' % (self.instrument_id, self.param('run_word', 'test')), cap, opid,
                               max_s=self.param('job_max_s'))
        elif self.slot('phase'):
            job = self.state_job(ctx, cap)
        if job is None:
            return {'text': 'ok: %s started\n' % self.instrument_id, 'json': {'result': r.get('result')}}
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}

    def state_job(self, ctx, cap):
        ad, slot = self, self.slot('phase')
        done = self.param('done_phases', ['succeeded', 'completed', 'done'])
        failed = self.param('failed_phases', ['failed', 'cancelled', 'aborted', 'stopped'])

        def state(c):
            v = c.v(slot)
            if v in done:
                return 'succeeded', '%s = %s' % (slot, v), {'phase': v}
            if v in failed:
                return 'failed', '%s = %s' % (slot, v), {'phase': v}
            return 'running', '%s = %s' % (slot, v), {'phase': v}

        def stop(c):
            sm = ad.vmap('stop')
            c.call(sm['cap'], dict(sm.get('args') or {}), 'stop')
            return 'stopped with %s' % sm['cap']
        return StateJob(ctx.core, self, ctx.req, '%s %s' % (self.instrument_id, self.param('run_word', 'run')), state,
                        stop=stop if self.vmap('stop') else None, max_s=float(self.param('job_max_s', 7200)))

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        ph = self.slot('phase')
        alt = self.param('stop_ok_slot')                                   # e.g. a start countdown that stop also cancels
        if ph and not ctx.dry_run and not (alt and ctx.v(alt) is not None):
            v = ctx.v(ph)
            ctx.check(v in self.param('running_phases', list(RUNNING_PHASES)), 'NOT_RUNNING', '%s is not running (%s = %s).' % (
                self.instrument_id, ph, v), 'nothing to stop; see `chemlab run %s status`.' % self.instrument_id, label='running')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop (%s)' % m['cap'])
        return {'text': 'ok: %s stopped (%s)\n' % (self.instrument_id, ctx.v(ph) if ph else 'stop sent'), 'json': {'phase': ctx.v(ph) if ph else None}}

    def result_lines(self, res, show):
        lines, js = [], {}
        for label, spec in (show or {}).items():
            path, kind = (list(spec) + [None])[:2] if isinstance(spec, list) else (spec, None)
            v = self.aggregate(res, path)
            js[label] = v
            lines.append('  %-22s %s' % (label, self.show_value(v, kind)))
        return lines, js

    @staticmethod
    def aggregate(res, path):
        m = re.fullmatch(r'(max|min|mean|last|first|count)\((.+)\)', path)
        if not m:
            return _get(res, path)
        fn, inner = m.groups()
        lst, _, col = inner.partition(':')
        flt = re.fullmatch(r'(.+)\[(\w+)=(.+)\]', lst)
        rows = _get(res, flt.group(1) if flt else lst) or []
        if flt and isinstance(rows, list):
            rows = [r for r in rows if isinstance(r, dict) and str(r.get(flt.group(2))) == flt.group(3)]
        if fn == 'count':
            return len(rows)
        vals = [_get(r, col) if col else r for r in rows] if isinstance(rows, list) else []
        vals = [v for v in vals if finite(v)]
        if not vals:
            return None
        return {'max': max, 'min': min, 'mean': lambda x: sum(x) / len(x), 'last': lambda x: x[-1], 'first': lambda x: x[0]}[fn](vals)

    def h_result(self, ctx, p):
        m = self.vmap('result')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the result (%s)' % m['cap'], query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') if isinstance(r.get('result'), dict) else {}
        lines, js = self.result_lines(res, m.get('show'))
        head = '%s result (%s):' % (self.instrument_id, m['cap'])
        n = {k: len(v) for k, v in res.items() if isinstance(v, list)}
        tail = ['  (%s; the full record: --json or --save-to FILE)' % ', '.join('%d %s' % (v, k) for k, v in n.items())] if n else []
        return {'text': '\n'.join([head] + lines + tail) + '\n', 'json': {'summary': js, 'result': res}}

    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        lo, hi = self.arg_range_si(m['cap'], m['arg'])
        if (lo is not None and k < lo - 1e-9) or (hi is not None and k > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'set point %s is outside the range of %s (%s to %s).' % (
                units.fmt(k, 'temperature'), self.instrument_id, units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature')),
                'give a set point from %s to %s.' % (units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature'))))
        self.need(ctx, 'set-temp')
        args = dict(m.get('args') or {})
        args[m['arg']] = round(self.arg_value(m['cap'], m['arg'], k), 10)
        ctx.call(m['cap'], args, 'set the temperature set point to %s' % units.fmt(k, 'temperature'))
        now = self.slot_si(ctx, self.slot('temp'))
        text = 'ok: %s set point %s (now %s)\n' % (self.instrument_id, units.fmt(k, 'temperature'), units.fmt(now, 'temperature') if finite(now) else 'n/a')
        js = {'set_point_k': k, 'temperature_k': now}
        if p.get('wait') and not ctx.dry_run:
            within = p.get('within') if p.get('within') is not None else float(self.param('tolerance_k', 0.5))
            hold = float(self.param('settle_s', 0.0))
            state = {'since': None}

            def there(view):
                t = self.slot_si(ctx, self.slot('temp'))
                if not (finite(t) and abs(t - k) <= within + 1e-9):
                    state['since'] = None
                    return False
                if state['since'] is None:
                    state['since'] = ctx.now()
                return ctx.now() - state['since'] >= hold - 1e-9
            t0 = ctx.now()
            if not there(ctx.view):
                ctx.advance(p.get('max') or 3600.0, until=there, step='wait until %s ± %s' % (units.fmt(k, 'temperature'),
                                                                                           units.fmt(within, 'temperature_delta')), poll_s=5.0)
            t = self.slot_si(ctx, self.slot('temp'))
            if not there(ctx.view):
                ctx.fail(refused('NOT_REACHED', '%s is at %s after %s; %s ± %s was not reached.' % (
                    self.instrument_id, units.fmt(t, 'temperature'), units.fmt_s(ctx.now() - t0), units.fmt(k, 'temperature'),
                    units.fmt(within, 'temperature_delta')), 'wait longer (`chemlab run %s set-temp %s --wait --max 2h`).' % (
                        self.instrument_id, p.raw['TEMP'])))
            text += 'reached %s after %s of waiting\n' % (units.fmt(t, 'temperature'), units.fmt_s(ctx.now() - t0))
            js.update(reached=True, temperature_k=t)
        return {'text': text, 'json': js}

    def temp_verb(self, ref):
        return Verb('set-temp', 'set the temperature set point (chamber, plate, barrel or furnace)', self.h_set_temp,
                    positionals=[Arg('TEMP', 'set point, e.g. 250C or 523.15K (a bare number is °C)', kind='temperature')],
                    options={'wait': Opt('then wait until the temperature is there (moves lab time)', kind='flag'),
                             'within': Opt('with --wait: tolerance (default: the member\'s)', kind='temperature_delta', metavar='DELTA'),
                             'max': Opt('with --wait: the longest wait (default 1 h)', kind='duration', metavar='TIME')},
                    preconditions=['set point within the contract range', 'the member\'s own conditions'],
                    errors=['OUT_OF_RANGE (usage)', 'NOT_REACHED (refused, with --wait)', 'member codes (refused)'],
                    example='chemlab run INST set-temp 250C --wait', reference=ref)

    def run_verbs(self, ref, result_summary):
        return {
            'start': Verb('start', 'start the test / run (returns job-N: chemlab wait job-N)', self.h_start, long_action=True,
                          options={'delay': Opt('start after this delay (members with a delayed start)', kind='duration', metavar='TIME')},
                          preconditions=['the member\'s own start conditions (power, specimen mounted, lid / door / guard closed ...)'],
                          errors=['member codes (refused): what is missing and how to fix it'], example='chemlab run INST start',
                          reference=ref),
            'stop': Verb('stop', 'stop the running test / run', self.h_stop, preconditions=['something is running'],
                         errors=['NOT_RUNNING (refused)'], example='chemlab run INST stop', reference=ref),
            'result': Verb('result', result_summary, self.h_result, readonly=True, options={'save-to': save_to_opt()},
                           example='chemlab run INST result --save-to result.json', reference=ref),
        }

    def check_mapping(self, lab):
        problems = ClassAdapter.check_mapping(self, lab)
        visible = {x.lower() for x in lab.view.slots}
        for row in self.map.get('status') or []:
            if row[1].split('#')[0].lower() not in visible:
                problems.append('status: no visible state slot %s' % row[1])
        for verb, m in (self.map.get('verbs') or {}).items():
            for g in [m] + list(m.get('also') or []):
                caps = lab.contract['capabilities']
                props = ((caps.get(g.get('cap')) or {}).get('input_schema') or {}).get('properties') or {}
                for k, ks in (g.get('keys') or {}).items():
                    for a in ks.get('args') or [ks.get('arg')]:
                        if a not in props:
                            problems.append('verbs.%s.keys.%s: %s has no argument %s' % (verb, k, g.get('cap'), a))
                        elif ks.get('field') and ks['field'] not in (props[a].get('properties') or {}):
                            problems.append('verbs.%s.keys.%s: %s.%s has no field %s' % (verb, k, g.get('cap'), a, ks['field']))
                for a in [x for x in (g.get('speed_arg'), g.get('y_arg'), g.get('y_cap_arg') and None) if x]:
                    if a not in props:
                        problems.append('verbs.%s: %s has no argument %s' % (verb, g.get('cap'), a))
        return problems

    def verb_help(self, name):
        r = ClassAdapter.verb_help(self, name)
        extra = self.extra_help(name) if name in self.table and self.supported(name) and self.lab is not None else []
        if extra:
            r['text'] = r['text'] + '\n'.join(extra) + '\n'
        return r

    def extra_help(self, name):
        if name in ('method', 'calibrate', 'analyze'):
            return self.settings_help(name)
        return []


REF = ('ASTM E8 / ISO 6892-1, ASTM D638 (tensile); ISO 14577 (indentation); ASTM C1624 (scratch); ASTM G99 (pin-on-disk); '
       'ISO 179-1 (Charpy); ISO 1133 (melt flow); ISO 3219 (rheometry); ASTM D7891 (powder); vendor manuals: TRAPEZIUM X user guide '
       'pp.42-64, AGS-X quick guide pp.1-3, RheoCompass manual pp.54-102, FT4 user manual pp.7-26, HIT5P brochure pp.3-7, Mflow '
       'product information pp.1-3')


class MechanicalAdapter(TesterBase):
    CLASS = 'mechanical'
    TITLE = 'mechanical testing and rheology'
    ABOUT = ('Testers that load a specimen and measure its response: clamp it, bring the probe into contact, zero and calibrate the '
             'sensors, position the stage / crosshead / gap, set the test method in plain units, start the test as a job, read the '
             'result and run the standard evaluation (tensile modulus, Oliver-Pharr hardness, critical load, impact energy, melt '
             'flow rate, oscillation moduli, flow energy).')
    STANDARDS = ('ASTM E8 / ISO 6892-1 / ASTM D638, ISO 14577, ASTM C1624 / ISO 20502, ASTM G99, ISO 179-1, ISO 1133 / ASTM D1238, '
                 'ISO 3219, ASTM D7891; no vendor remote command set is public (PC software only)')
    UNIT_KINDS = ('force', 'length', 'speed', 'duration', 'temperature', 'rotation', 'frequency', 'mass', 'area')
    KEY_HELP = {'mode': 'test mode', 'load': 'test / normal / peak force', 'load-start': 'initial force', 'load-end': 'final force',
                'speed': 'test speed', 'time': 'test duration', 'interval': 'sampling interval', 'rate': 'loading rate (N/s)',
                'unload-rate': 'unloading rate (N/s)', 'hold': 'hold time', 'strain': 'strain (amplitude)', 'freq': 'frequency',
                'shear-rate': 'shear rate (1/s)', 'distance': 'sliding distance', 'start': 'path start', 'length': 'path length',
                'end': 'path end', 'temp-tol': 'temperature tolerance', 'preheat': 'preheat time', 'window': 'UPPER..LOWER positions',
                'positions': 'indent positions (JSON list in m)', 'target': 'end position', 'cycles': 'measurement cycles',
                'area': 'specimen cross-section (prior)', 'gauge': 'gauge length (prior)', 'ref': 'reference force',
                'index': 'which indent (0 = first)', 'channel': 'criterion channel', 'threshold': 'criterion threshold',
                'radius': 'track radius / plate radius', 'drive': 'drive mode'}
    CODES = {
        'HEAD_NOT_CLEAR': ('HEAD_NOT_CLEAR', 'the measuring head of {inst} is down or in contact; that is not possible now.',
                           'lift it first: `chemlab run {inst} contact off`.'),
        'CONTACT_ENGAGED': ('CONTACT_ENGAGED', 'the probe of {inst} is in contact with the specimen.', 'lift it: `chemlab run {inst} contact off`.'),
        'ARM_LOADED': ('ARM_LOADED', 'the load arm of {inst} rests on the specimen; the load, radius, drive and zero are set with the arm '
                                     'lifted.', 'lift it: `chemlab run {inst} contact off` (lid open), set it, then `contact on` again.'),
        'LID_CLOSED': ('LID_CLOSED', 'the lid of {inst} is closed; the arm is lowered or lifted and the specimen reached only with the lid '
                                     'open.', 'open it: `chemlab run {inst} open lid`; close it again before `start`.'),
        'LOAD_NOT_RELEASED': ('LOAD_NOT_RELEASED', '{inst} still carries a load.', 'wait until the force is released (`chemlab wait 5`).'),
        'BUSY': ('BUSY', '{inst} is busy (a test runs or an axis is moving).', 'wait for it (`chemlab wait 5`, or wait for your job), then retry.'),
        'MOVING': ('MOVING', 'an axis of {inst} is still moving.', 'wait until it stops (`chemlab wait 5`) or use `move ... --wait`.'),
        'CLAMP_LOCKED': ('CLAMP_LOCKED', 'the specimen clamp of {inst} is locked.', 'release it: `chemlab run {inst} clamp off`.'),
        'NO_SPECIMEN': ('NO_SPECIMEN', 'there is no specimen on {inst}.', 'ask the lab operator to load one (not an agent action).'),
        'NO_SAMPLE': ('NO_SAMPLE', 'there is no specimen / sample in {inst}.', 'ask the lab operator to load one (not an agent action).'),
        'NO_CLAMPED_SPECIMEN': ('NO_CLAMPED_SPECIMEN', 'the head of {inst} may only come down onto a clamped specimen.',
                                'clamp it first: `chemlab run {inst} clamp on`.'),
        'CONTACT_NOT_READY': ('CONTACT_NOT_READY', '{inst} cannot make contact now (it needs power, a clamped specimen, a tool and the head '
                                                   'fully down).', 'check `chemlab run {inst} status`; `clamp on`, then `contact on`.'),
        'NO_CONTACT_PAIR': ('NO_CONTACT_PAIR', '{inst} has no specimen and pin pair loaded.', 'ask the lab operator to load the disk and the pin.'),
        'STAGE_NOT_AT_START': ('STAGE_NOT_AT_START', 'the stage of {inst} is not at the start position of the method.',
                               'move it there first: `chemlab run {inst} contact off`, `move START --wait`, then `contact on`.'),
        'HEATER_NOT_INSTALLED': ('NO_HEATER', '{inst} has no heating module installed in this lab, so it tests at room temperature only.',
                                 'leave the temperature at ambient (or ask the lab operator for the heater module).'),
        'NO_ACTIVE_COOLING': ('NO_COOLING', '{inst} cannot cool below the room temperature.', 'give a set point at or above ambient.'),
        'TEMPERATURE_NOT_READY': ('TEMPERATURE_NOT_READY', 'the temperature of {inst} is not within the method tolerance of the set point.',
                                  'wait for it: `chemlab run {inst} set-temp T --wait`.'),
        'TEMPERATURE_NOT_STABLE': ('TEMPERATURE_NOT_STABLE', 'the barrel of {inst} is not within the tolerance of its set point yet.',
                                   'wait for it: `chemlab run {inst} set-temp T --wait`, then start.'),
        'PREHEAT_NOT_COMPLETE': ('PREHEAT_NOT_COMPLETE', 'the preheat time of the charge in {inst} has not passed yet.',
                                 'wait (`chemlab wait 60`) and start again.'),
        'EMERGENCY_ENGAGED': ('EMERGENCY_STOP', 'the emergency stop of {inst} is engaged.', 'the lab operator releases it after checking the frame.'),
        'EMERGENCY_STOP': ('EMERGENCY_STOP', 'the emergency stop of {inst} is latched.', 'check the machine, then reset it (`chemlab call {inst} emergency.reset`).'),
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'NOT_RUNNING': ('NOT_RUNNING', 'no test is running on {inst}.', 'nothing to stop.'),
        'UNLOAD_REQUIRED': ('UNLOAD_REQUIRED', '{inst} reads a load (or an axis moves); the force is zeroed only unloaded and at rest.',
                            'unload the specimen (or zero before the grips close) and retry.'),
        'GRIPS_NOT_CLOSED': ('GRIPS_NOT_CLOSED', 'the grips of {inst} are not both closed on the specimen.', 'ask the lab operator to close the grips.'),
        'SAMPLE_BROKEN': ('SAMPLE_BROKEN', 'the specimen in {inst} is already broken.', 'ask the lab operator for a new specimen.'),
        'CALIBRATION_REQUIRED': ('CALIBRATION_REQUIRED', '{inst} needs its friction calibration swing before an impact test.',
                                 'run `chemlab run {inst} calibrate` with the supports empty.'),
        'EMPTY_REQUIRED': ('EMPTY_REQUIRED', 'the calibration swing of {inst} needs empty supports.', 'ask the lab operator to take the specimen out.'),
        'NOT_ARMED': ('NOT_ARMED', 'the pendulum of {inst} is not raised and latched.', 'ask the lab operator to raise the pendulum.'),
        'SHIELD_OPEN': ('SHIELD_OPEN', 'the safety shield of {inst} is open.', 'ask the lab operator to close the shield.'),
        'SAMPLE_SPENT': ('SAMPLE_SPENT', 'the specimen in {inst} has already been struck.', 'ask the lab operator for a new specimen.'),
        'LOAD_MISMATCH': ('LOAD_MISMATCH', 'the weight on the piston of {inst} is not the method\'s test load.',
                          'set the method load to the weight in place (`method load=2.16kg`; see `chemlab run {inst} status`).'),
        'PISTON_NOT_ON_CHARGE': ('PISTON_NOT_ON_CHARGE', 'the piston of {inst} is not resting on the charge.', 'ask the lab operator to insert the piston.'),
        'CLAMP_REQUIRED': ('CLAMP_REQUIRED', 'the vessel of {inst} is not clamped.', 'clamp it: `chemlab run {inst} clamp on` (door open), then close the door.'),
        'DOOR_INTERLOCK': ('DOOR_INTERLOCK', 'the safety door of {inst} is open.', 'close it: `chemlab run {inst} close door`.'),
        'SAMPLE_ACCESS': ('SAMPLE_ACCESS', 'the vessel of {inst} can be clamped or reached only with the door open and the blade parked at the '
                                           'top.', 'open the door (`chemlab run {inst} open door`) and park the blade (`move 0mm --wait`).'),
        'ATTACHMENT_REQUIRED': ('ATTACHMENT_REQUIRED', 'the attachment on {inst} does not fit the method mode.',
                                'select the matching tool (`chemlab call {inst} tool.select --tool_type blade`).'),
        'GAP_FILL_MISMATCH': ('GAP_FILL_MISMATCH', 'the gap of {inst} does not match the sample fill (the sample must just fill the gap).',
                              'set the measuring gap the sample was loaded for: `chemlab run {inst} move 1mm --wait`.'),
        'REFERENCE_TEMPERATURE_RANGE': ('TEMPERATURE_NOT_READY', 'the specimen of {inst} is not at the temperature of its reference data '
                                                                 'within the method tolerance.', 'set and wait for the reference temperature '
                                                                 '(`chemlab run {inst} set-temp T --wait`).'),
        'SAMPLE_PRESENT': ('SAMPLE_PRESENT', '{inst} has a sample loaded; this needs it empty.', 'ask the lab operator to remove the sample first.'),
        'PRESCAN_REQUIRED': ('PRESCAN_REQUIRED', 'a scratch on {inst} needs a prescan of the same path first.',
                             'run `method mode=prescan` + `start` on the same path, then the scratch.'),
        'TRACK_ALREADY_DAMAGED': ('TRACK_ALREADY_DAMAGED', 'that path on the specimen of {inst} is already scratched.',
                                  'choose another path (`method start=... ` with another --y).'),
        'NO_RESULT': ('NO_RESULT', '{inst} has no result to evaluate yet.', 'run a test first (`chemlab run {inst} start`).'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        t = {
            'clamp': Verb('clamp', 'lock or release the specimen / vessel clamp', self.h_clamp,
                          positionals=[Arg('on|off', 'on (lock) or off (release)', choices=ON_OFF)],
                          preconditions=['the head is up and nothing moves', 'a specimen is there (to lock)'],
                          errors=['HEAD_NOT_CLEAR, NO_SPECIMEN, SAMPLE_ACCESS (refused)'], example='chemlab run INST clamp on', reference=REF),
            'contact': Verb('contact', 'bring the probe (pin, stylus, indenter tip) into contact with the specimen, or lift it; waits '
                                       'while the head travels', self.h_contact,
                            positionals=[Arg('on|off', 'on (contact) or off (lift)', choices=ON_OFF)],
                            options={'max': Opt('longest wait for the head (default 2 min)', kind='duration', metavar='TIME')},
                            preconditions=['the member\'s own conditions (power, clamped specimen, lid open for the TRB3 arm)'],
                            errors=['CONTACT_NOT_READY, LID_CLOSED, NO_CLAMPED_SPECIMEN (refused)'], example='chemlab run INST contact on',
                            reference=REF),
            'zero': Verb('zero', 'zero the force / torque / depth sensors (or the rheometer gap)', self.h_zero,
                         options={'max': Opt('for a zero procedure that takes time: the longest wait (default 5 min)', kind='duration',
                                             metavar='TIME')},
                         preconditions=['unloaded: probe lifted, no load on the cell'], errors=['UNLOAD_REQUIRED, ARM_LOADED (refused)'],
                         example='chemlab run INST zero', reference=REF),
            'calibrate': Verb('calibrate', 'calibrate the sensors: KEY=VALUE corrections (or the member\'s calibration run)',
                              self.h_calibrate, variadic=('KEY=VALUE', 'a calibration value'),
                              preconditions=['the member\'s own conditions'], errors=['OUT_OF_RANGE, UNKNOWN_SETTING (usage)'],
                              example='chemlab run INST calibrate ref=100N', reference=REF),
            'move': Verb('move', 'move the stage / crosshead / blade / gap to a position (track radius on the tribometer)', self.h_move,
                         positionals=[Arg('POS', 'position, e.g. 10mm or 0.5mm', kind='length')],
                         options={'y': Opt('second axis (scratch stage y, indenter y table)', kind='length', metavar='Y'),
                                  'speed': Opt('travel speed (default: the member\'s)', kind='speed', metavar='SPEED'),
                                  'wait': Opt('wait until it is there (moves lab time)', kind='flag'),
                                  'max': Opt('with --wait: the longest wait (default 5 min)', kind='duration', metavar='TIME')},
                         preconditions=['position and speed within the contract range', 'the member\'s own conditions (head up, power)'],
                         errors=['OUT_OF_RANGE (usage)', 'HEAD_NOT_CLEAR, GRIPS_NOT_CLOSED (refused)'], example='chemlab run INST move 10mm --wait',
                         reference=REF),
            'method': Verb('method', 'set the test method: KEY=VALUE ... in plain units (class keys or native names; the rest from the '
                                     'member\'s template)', self.h_settings('method', 'method'), variadic=('KEY=VALUE', 'a setting'),
                           preconditions=['every value within the contract range'], errors=['OUT_OF_RANGE, UNKNOWN_SETTING (usage)'],
                           example='chemlab run INST method speed=5mm/min time=60s', reference=REF),
            'set-temp': self.temp_verb(REF),
            'analyze': Verb('analyze', 'the standard evaluation of the last result (KEY=VALUE for the priors: area=, gauge=, ...)',
                            self.h_analyze, readonly=True, variadic=('KEY=VALUE', 'an evaluation input'),
                            options={'index': Opt('which segment / indent of the result (default 0)', metavar='N'),
                                     'save-to': save_to_opt()},
                            preconditions=['a finished result'], errors=['NO_RESULT (refused)', 'the evaluation\'s own codes'],
                            example='chemlab run INST analyze --save-to analysis.json', reference=REF),
        }
        t.update(self.run_verbs(REF, 'the raw test data with the key numbers (peak force, critical load, energy, MFR ...)'))
        return t

    # ------------------------------------------------------------------ handlers
    def h_clamp(self, ctx, p):
        m = self.vmap('clamp')
        want = p.pos[0] == 'on'
        s = self.slot('clamp')
        if s and bool(ctx.v(s)) == want:
            return {'text': 'ok: the clamp of %s is already %s\n' % (self.instrument_id, 'locked' if want else 'released'),
                    'json': {'clamped': want, 'changed': False}}
        self.need(ctx, 'clamp')
        ctx.call(m['cap'], {m['arg']: want}, '%s the clamp' % ('lock' if want else 'release'))
        return {'text': 'ok: %s clamp %s\n' % (self.instrument_id, 'locked' if want else 'released'), 'json': {'clamped': want, 'changed': True}}

    def wait_axis(self, ctx, slot, target, moving_slot, max_s, what):
        """Advance lab time until the axis is at ``target`` and at rest (its moving flag off, or - without one - the same
        position on two readings in a row)."""
        last = {'v': ctx.v(slot)}

        def there(view):
            v = ctx.v(slot)
            near = finite(v) and abs(v - target) <= 1e-9 + 1e-9 * abs(target)
            still = (not ctx.v(moving_slot)) if moving_slot else (v == last['v'])
            last['v'] = v
            return near and still
        if moving_slot and there(ctx.view):
            return 0.0
        waited = ctx.advance(max_s, until=there, step='wait until %s is there' % what, poll_s=0.5)
        if not ctx.dry_run and not there(ctx.view):
            ctx.fail(refused('NOT_THERE', '%s did not reach %s within %s (%s = %s).' % (what, units.fmt(target, 'length'), units.fmt_s(max_s),
                                                                                       slot, short(ctx.v(slot))),
                             'check `chemlab run %s status` and retry with a longer --max.' % self.instrument_id))
        return waited

    def h_contact(self, ctx, p):
        m = self.vmap('contact')
        want = p.pos[0] == 'on'
        s = self.slot('contact')
        if s and bool(ctx.v(s)) == want:
            return {'text': 'ok: %s is already %s\n' % (self.instrument_id, 'in contact' if want else 'lifted'), 'json': {'contact': want, 'changed': False}}
        self.need(ctx, 'contact' if want else 'contact-off')
        head = m.get('head')
        mx = p.get('max') or 120.0
        if want:
            if head:
                if not finite(ctx.v(head['slot'])) or ctx.v(head['slot']) > 1e-12:
                    ctx.call(head['cap'], {head['arg']: 0.0}, 'lower the head onto the specimen')
                    self.wait_axis(ctx, head['slot'], 0.0, head.get('moving_slot'), mx, 'the head')
            ctx.call(m['cap'], {m['arg']: True}, 'engage the contact')
        else:
            ctx.call(m['cap'], {m['arg']: False}, 'release the contact')
            if head:
                up = self.arg_range(head['cap'], head['arg'])[1]
                ctx.call(head['cap'], {head['arg']: up}, 'lift the head to %s' % units.fmt(up, 'length'))
                self.wait_axis(ctx, head['slot'], up, head.get('moving_slot'), mx, 'the head')
        return {'text': 'ok: %s %s\n' % (self.instrument_id, 'in contact with the specimen' if want else 'probe lifted'),
                'json': {'contact': want, 'changed': True}}

    def h_zero(self, ctx, p):
        m = self.vmap('zero')
        self.need(ctx, 'zero')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'zero (%s)' % m['cap'])
        text = 'ok: %s zeroed\n' % self.instrument_id
        w = m.get('wait')
        if w and not ctx.dry_run:
            def done(view):
                return ctx.v(w['slot']) in w.get('done', [None])
            t0 = ctx.now()
            if not done(ctx.view):
                ctx.advance(p.get('max') or 300.0, until=done, step='wait for the zero procedure', poll_s=1.0)
            if not done(ctx.view):
                ctx.fail(refused('NOT_DONE', 'the zero procedure of %s has not finished after %s.' % (self.instrument_id, units.fmt_s(ctx.now() - t0)),
                                 'wait longer: `chemlab run %s status`.' % self.instrument_id))
            text = 'ok: %s zeroed (%s took %s)\n' % (self.instrument_id, m['cap'], units.fmt_s(ctx.now() - t0))
        return {'text': text, 'json': {'zeroed': True}}

    def h_calibrate(self, ctx, p):
        m = self.vmap('calibrate')
        if m.get('job'):
            if p.extra:
                ctx.fail(invalid('NO_SETTINGS', 'calibrate on %s is a calibration run and takes no values.' % self.instrument_id,
                                 'write just `chemlab run %s calibrate`.' % self.instrument_id))
            self.need(ctx, 'calibrate')
            r = ctx.call(m['cap'], dict(m.get('args') or {}), 'start the calibration run (%s)' % m['cap'])
            if ctx.dry_run or not r.get('operation_id'):
                return {'text': '' if ctx.dry_run else 'ok: %s calibration started\n' % self.instrument_id, 'json': {}}
            job = OperationJob(ctx.core, self, ctx.req, '%s calibration run' % self.instrument_id, m['cap'], r['operation_id'])
            return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
                job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}
        if not p.extra and not m.get('always'):
            ctx.fail(invalid('MISSING_SETTING', 'calibrate needs at least one KEY=VALUE.', 'see `chemlab help %s calibrate`.' % self.instrument_id))
        return self.h_settings('calibrate', 'calibration')(ctx, p)

    def h_move(self, ctx, p):
        m = self.vmap('move')
        pos = p.pos[0]
        target = self.parse_setting(ctx, m['cap'], m['arg'], p.raw['POS'], 'length', what='POS')
        args = dict(m.get('args') or {})
        args[m['arg']] = target
        calls = []
        if p.get('y') is not None:
            if m.get('y_arg'):
                args[m['y_arg']] = self.parse_setting(ctx, m['cap'], m['y_arg'], p.raw['y'], 'length', what='--y')
            elif m.get('y_cap'):
                ya = dict(m.get('y_args') or {})
                ya[m['y_cap_arg']] = self.parse_setting(ctx, m['y_cap'], m['y_cap_arg'], p.raw['y'], 'length', what='--y')
                calls.append((m['y_cap'], ya, 'set the y position to %s' % units.fmt(p.get('y'), 'length')))
            else:
                ctx.fail(invalid('NO_Y_AXIS', '%s has one axis here; --y does not apply.' % self.instrument_id, 'leave out --y.'))
        elif m.get('y_arg'):
            args[m['y_arg']] = ctx.v(m['y_slot'])
        if m.get('speed_arg'):
            text = p.raw.get('speed') or self.param('move_speed', '1mm/s')
            args[m['speed_arg']] = self.parse_setting(ctx, m['cap'], m['speed_arg'], text, 'speed', what='--speed')
        elif p.get('speed') is not None:
            ctx.fail(invalid('NO_SPEED', '%s moves this axis at its own speed.' % self.instrument_id, 'leave out --speed.'))
        self.need(ctx, 'move')
        for cap, a, step in calls:
            ctx.call(cap, a, step)
        ctx.call(m['cap'], args, 'move to %s' % units.fmt(pos, 'length'))
        text = 'ok: %s moving to %s\n' % (self.instrument_id, units.fmt(pos, 'length'))
        if p.get('wait') and not ctx.dry_run and m.get('slot'):
            w = self.wait_axis(ctx, m['slot'], target, m.get('moving_slot'), p.get('max') or 300.0, 'the %s' % self.param('axis_word', 'axis'))
            text = 'ok: %s at %s (waited %s)\n' % (self.instrument_id, units.fmt(pos, 'length'), units.fmt_s(w))
        return {'text': text, 'json': {'target_m': pos, 'arguments': args}}

    def h_analyze(self, ctx, p):
        m = self.vmap('analyze')
        rm = m.get('result') or self.vmap('result')
        r = ctx.call(rm['cap'], dict(rm.get('args') or {}), 'read the result (%s)' % rm['cap'], query=True)
        res = (r.get('result') if isinstance(r.get('result'), dict) else {}) or {}
        if ctx.dry_run:
            res = {}
        rows = _get(res, m.get('rows', 'rows')) or []
        for k, v in (m.get('where') or {}).items():
            rows = [x for x in rows if x.get(k) == v]
        need = [c for c in (m.get('columns') or {}).values()] + list(m.get('columns_required') or [])
        rows = [x for x in rows if isinstance(x, dict) and all(x.get(c) is not None for c in need)]
        if m.get('group'):
            groups = sorted({x.get(m['group']) for x in rows if x.get(m['group']) is not None})
            idx = p.get('index')
            try:
                i = int(idx) if idx is not None else 0
            except ValueError:
                ctx.fail(invalid('BAD_INDEX', '--index %s is not a whole number.' % idx, 'give --index 0, 1, ...'))
            if groups and not (0 <= i < len(groups)):
                ctx.fail(invalid('BAD_INDEX', 'the result has %d %ss (0..%d).' % (len(groups), m['group'], len(groups) - 1), 'give --index 0..%d.' % (len(groups) - 1)))
            if groups:
                rows = [x for x in rows if x.get(m['group']) == groups[i]]
        if not ctx.dry_run:
            phase = _get(res, m.get('phase_path', 'phase'))
            ok_rows = m.get('rows_ok') or len(rows) >= int(m.get('min_rows', 1))
            for path, why in (m.get('needs') or {}).items():
                p_, eq, want = path.partition('=')
                ok = (str(_get(res, p_)) == want) if eq else _get(res, p_) is not None
                ctx.check(ok, 'NO_RESULT', '%s: %s' % (self.instrument_id, why),
                          'run the matching test first (see `chemlab help %s analyze`).' % self.instrument_id, label=path)
            ctx.check(ok_rows and (phase in m.get('phases', ['succeeded']) if 'phase_path' in m or 'phases' in m else True),
                      'NO_RESULT', '%s has no finished result to evaluate (phase %s, %d usable rows).' % (self.instrument_id, phase, len(rows)),
                      'run a test to the end first (`chemlab run %s start`, then `chemlab wait job-N`).' % self.instrument_id, label='a finished result')
        calls = self.settings(ctx, dict(m, always=True), p.extra, 'analyze')
        cap, args, given, _ = calls[0]
        for a, col in (m.get('columns') or {}).items():
            if a not in given:
                args[a] = [x.get(col) for x in rows]
        for a, path in (m.get('from') or {}).items():
            if a not in given:
                args[a] = _get(res, path)
        for a, slot in (m.get('from_slots') or {}).items():
            if a not in given:
                args[a] = ctx.v(slot)
        for a, spec in (m.get('from_list') or {}).items():               # a list of records rebuilt from a list in the result
            if a not in given:
                items = [x for x in (_get(res, spec['path']) or []) if isinstance(x, dict)
                         and all(x.get(k) == v for k, v in (spec.get('where') or {}).items())
                         and all(x.get(f) is not None for f in spec['fields'].values())]
                args[a] = [{k: x.get(f) for k, f in spec['fields'].items()} for x in items]
        for a in ([m['rows_arg']] if m.get('rows_arg') else []) + list(m.get('from_rows') or []):
            if a not in given:
                args[a] = rows
        if ctx.dry_run:
            ctx.plan.append({'capability': cap, 'arguments': {a: (('<%d values from the result>' % len(v)) if isinstance(v, list) else v)
                                                              for a, v in args.items()}, 'step': 'evaluate (%s)' % cap})
            return {'text': '', 'json': {}}
        bad = validate((ctx.cap_spec(cap).get('input_schema') or {}), args, cap)
        if bad:                                                             # the result does not carry what the evaluation needs
            ctx.fail(refused('NOT_EVALUABLE', 'the last result of %s cannot be evaluated with %s: %s.' % (self.instrument_id, cap, '; '.join(bad[:3])),
                             'check `chemlab run %s result`; give the missing values as KEY=VALUE (see `chemlab help %s analyze`).' % (
                                 self.instrument_id, self.instrument_id)))
        out = ctx.call(cap, args, 'evaluate (%s)' % cap, query=True)
        ev = out.get('result') if isinstance(out.get('result'), dict) else {}
        lines, js = self.result_lines(ev, m.get('show'))
        return {'text': '\n'.join(['%s evaluation (%s, %d rows of the result):' % (self.instrument_id, cap, len(rows))] + lines) + '\n',
                'json': {'summary': js, 'evaluation': ev, 'inputs': {a: (len(v) if isinstance(v, list) else v) for a, v in args.items()}}}

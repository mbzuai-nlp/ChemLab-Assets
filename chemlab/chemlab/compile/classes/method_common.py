"""Common machinery of the sample_intro, titration and electrochem classes.  Instrument facts stay in the
fragments (configs/adapters/{sample_intro,titration,electrochem}.json); this module holds no member data.

``MethodAdapter`` extends ClassAdapter with

* member preconditions with comparisons (``"requires": {verb: [{"slot", "value" | "not" | "in" | "min" | "max" | "truthy" |
  "falsy" | "set", "code", "message", "fix", "label"}]}``), checked before anything is sent;
* ``method KEY=VALUE ...`` over one or more native calls (``"method": {"calls": [{"cap", "keys": {CLASS_KEY: NATIVE_ARG | {"arg",
  "values"}}, "object": ARG?, "passthrough": true, "keep": {"slots": {ARG: SLOT}, "last": true, "example": true}}]}``): class keys
  take units (the class dictionary ``KEYS`` gives the kind), native argument names pass through (a bare number is in the unit the
  contract declares); a call is sent only when one of its settings is given.  Calls whose capability needs every argument keep
  the ones the agent did not give: from the member's own state slots, else the last accepted call of that capability on this
  instrument in this lab (the lab's own request log), else the contract's documented example - never invented, and the reply
  says which.  Every call is validated against the contract (types, enums, ranges in friendly units) before the first is sent;
* jobs for long actions: ``"job": {"slot", "done", "failed", "null_done"}`` -> jobs.StateJob on the member's own state slots, else
  jobs.OperationJob when the runtime answers with an operation id;
* record verbs (result, curve, data) that call a query capability (``"cap"``) or read slots (``"reads"``), with friendly units for
  keys whose name ends in a unit (``_kg``, ``_m3``, ``_k``, ``_v``, ``_a`` ...) and --save-to;
* ``connect PORT=ID ...`` for logical connection slots (electrodes, cells): ``"connect": {"cap", "port_arg", "id_arg",
  "ports": {CLASS_PORT: NATIVE_PORT}, "slots": {CLASS_PORT: SLOT}}``;
* short waits for mechanical moves (``settle``), used only by verbs that say they wait;
* a status summary from the member's ``"show": [[label, slot(#key), kind?], ...]``.
"""
import copy
import math
import re

from ...contract import validate
from ...generic import coerce
from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused, unexpected
from ..jobs import RUNNING, OperationJob, StateJob

_NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
# key suffix -> declared unit (friendly display of record values)
SUFFIX_UNITS = (('_mol_m3', 'mol/m^3'), ('_m3_s', 'm^3/s'), ('_kg_s', 'kg/s'), ('_rad_s', 'rad/s'), ('_m3', 'm^3'), ('_kg', 'kg'),
                ('_k', 'K'), ('_pa', 'Pa'), ('_v', 'V'), ('_a', 'A'), ('_hz', 'Hz'), ('_m', 'm'), ('_s', 's'), ('_c', None))


def types_of(schema):
    t = (schema or {}).get('type')
    out = set(t if isinstance(t, list) else [t] if t else [])
    for x in (schema or {}).get('anyOf') or []:
        out |= types_of(x)
    return out


def numeric_schema(schema):
    """The number branch of a schema (anyOf number|null -> the number one)."""
    if 'anyOf' in (schema or {}):
        for x in schema['anyOf']:
            if types_of(x) & {'number', 'integer'}:
                return x
    return schema or {}


def friendly_value(key, v):
    """A record value with a unit in its key name -> friendly text (None when nothing nicer exists)."""
    if not finite(v):
        return None
    k = key.lower()
    for suf, u in SUFFIX_UNITS:
        if k.endswith(suf):
            if u is None:
                return None
            kind = units.unit_kind(u)
            if kind is None:
                return '%s %s' % (short(v, 20), u)
            if kind == 'length' and abs(v) < 1e-6:
                return '%.4g nm' % (v * 1e9)
            if kind == 'current' and abs(v) < 1e-6:
                return '%.4g nA' % (v * 1e9)
            if kind == 'mass' and abs(v) < 1e-6:
                return '%.4g µg' % (v * 1e9)
            if kind == 'mass_flow' and abs(v) < 1e-6:
                return '%.4g µg/min' % (v * 6e10)
            return units.fmt(units.from_declared(v, u), kind)
    return None


class MethodAdapter(ClassAdapter):
    KEYS = {}                    # class method dictionary: key -> (quantity kind or None, help)
    PORTS = {}                   # class connection ports: name -> help

    def __init__(self, instrument_id, config_entry):
        ClassAdapter.__init__(self, instrument_id, config_entry)
        for name, ex in (self.map.get('examples') or {}).items():          # member-specific examples and manual pages
            if name in self.table:
                self.table[name].example = ex
        if self.map.get('manual'):
            for v in self.table.values():
                if v.reference:
                    v.reference = self.map['manual']

    # ------------------------------------------------------------------ mapping checks beyond the base
    def check_mapping(self, lab):
        problems = ClassAdapter.check_mapping(self, lab)
        caps = lab.contract['capabilities']
        visible = {s.lower() for s in lab.view.slots}

        def props(cap, obj=None):
            p = ((caps.get(cap) or {}).get('input_schema') or {}).get('properties') or {}
            return ((p.get(obj) or {}).get('properties') or {}) if obj else p
        m = self.vmap('method') or {}
        for i, call in enumerate(m.get('calls') or []):
            pr = props(call['cap'], call.get('object'))
            for key, spec in (call.get('keys') or {}).items():
                arg = spec if isinstance(spec, str) else spec.get('arg')
                if arg not in pr:
                    problems.append('verbs.method.calls[%d]: %s has no %s %s (key %s)' % (i, call['cap'], 'field' if call.get('object') else
                                                                                         'argument', arg, key))
                if key not in self.KEYS and not (isinstance(spec, dict) and 'kind' in spec):
                    problems.append('verbs.method.calls[%d]: key %s is not in the %s dictionary (give "kind")' % (i, key, self.CLASS))
            for arg, sid in ((call.get('keep') or {}).get('slots') or {}).items():
                if arg not in pr:
                    problems.append('verbs.method.calls[%d].keep: %s has no argument %s' % (i, call['cap'], arg))
                if sid.split('#', 1)[0].lower() not in visible:
                    problems.append('verbs.method.calls[%d].keep: no visible state slot %s' % (i, sid))
        for verb in self.table:
            vm = self.vmap(verb) or {}
            for k, v in vm.items():
                cap = vm.get(k[:-4] + '_cap') or vm.get('cap')
                if k.endswith('_arg') and isinstance(v, str) and cap and v not in props(cap):
                    problems.append('verbs.%s.%s: %s has no argument %s' % (verb, k, cap, v))
            job = vm.get('job') or {}
            if isinstance(job, dict) and job.get('slot') and job['slot'].split('#', 1)[0].lower() not in visible:
                problems.append('verbs.%s.job: no visible state slot %s' % (verb, job['slot']))
            for sid in (vm.get('slots') or {}).values() if isinstance(vm.get('slots'), dict) else []:
                if sid.split('#', 1)[0].lower() not in visible:
                    problems.append('verbs.%s.slots: no visible state slot %s' % (verb, sid))
        for row in self.map.get('show') or []:
            if row[1].split('#', 1)[0].lower() not in visible:
                problems.append('show: no visible state slot %s' % row[1])
        return problems

    # ------------------------------------------------------------------ state helpers
    def sv(self, values, spec):
        """Value of 'slot' or 'slot#key' from a Ctx or a {slot: value} dict."""
        if spec is None:
            return None
        sid, _, key = spec.partition('#')
        v = values.v(sid) if hasattr(values, 'v') else values.get(sid)
        if key and isinstance(v, dict):
            v = v.get(key)
        return v

    def need(self, ctx, verb, only=None, skip=()):
        """The member's own preconditions for a verb (from "requires"), checked before anything is sent."""
        for r in (self.map.get('requires') or {}).get(verb, []):
            if (only is not None and r.get('code') not in only) or r.get('code') in skip:
                continue
            got = self.sv(ctx, r['slot'])
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
            elif r.get('set'):
                ok = got not in (None, '', [], {})
            else:
                ok = finite(got) and ('max' not in r or got <= r['max'] + 1e-12) and ('min' not in r or got >= r['min'] - 1e-12)
            shown = short(got, 40)
            ctx.check(ok, r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=shown),
                      r['fix'].format(inst=self.instrument_id, value=shown), label=r.get('label', r['slot']))

    def summary(self, values):
        lines, js = [], {}
        parts = []
        for row in self.map.get('show') or []:
            label, spec = row[0], row[1]
            kind = row[2] if len(row) > 2 else None
            v = self.sv(values, spec)
            js[label] = v
            if kind and finite(v):
                if kind in units.KINDS:
                    text = units.fmt(v, kind)
                elif units.unit_kind(kind):                              # a display unit: the SI value shown in it
                    text = '%.4g %s' % (units.to_declared(v, kind), kind)
                else:
                    text = '%s %s' % (short(v, 20), kind)
            elif isinstance(v, list) and any(x is None for x in v):
                text = '%d of %d filled' % (sum(1 for x in v if x is not None), len(v))
            elif isinstance(v, (list, dict)):
                text = '%d entries' % len(v) if isinstance(v, list) else ('none' if not v else short(v, 60))
            elif isinstance(v, str):
                text = v[:60] if v else '-'
            elif isinstance(v, float):
                text = '%.4g' % v
            else:
                text = short(v, 60) if v is not None else 'n/a'
            parts.append('%s %s' % (label, text))
        while parts:
            lines.append('; '.join(parts[:4]))
            parts = parts[4:]
        return (lines or ['(see `chemlab read %s`)' % self.instrument_id]), js

    # ------------------------------------------------------------------ values and ranges
    def parse_setting(self, ctx, key, text, schema, kind=None):
        """One KEY=VALUE value -> the native value: class keys parse units by their kind; a native name with a bare number is in the
        unit the contract declares, with a unit it is converted; non-numeric values by the schema's type (JSON, enums, text)."""
        num = numeric_schema(schema)
        t = types_of(schema)
        isnum = bool(t & {'number', 'integer'})
        txt = text.strip()
        if isnum and txt.lower() not in ('null', 'none') and re.match(r'\s*[+-]?(\d|\.\d)', txt):
            u = units.declared(num)
            bare = re.fullmatch(_NUM, txt) is not None
            try:
                if kind and kind in units.KINDS:
                    if u and units.unit_kind(u):
                        v = units.parse_for(txt, num, kind, what=key)
                    else:                                        # unitless argument (a fraction, a level): the SI value
                        v = units.parse(txt, kind, what=key)
                elif u and units.unit_kind(u) and not bare:
                    v = units.parse_for(txt, num, what=key)
                else:
                    m = re.fullmatch('(' + _NUM + r')\s*(.*)', txt)
                    if m.group(2) and units.norm_unit(m.group(2)) != u:
                        ctx.fail(invalid('BAD_SETTING', '%s=%s: %s takes a plain number%s.' % (key, text, key, ' in %s' % u if u else ''),
                                         'write it like %s=%s.' % (key, m.group(1))))
                    v = float(m.group(1))
            except Exception as exc:                                     # noqa: BLE001 - unit errors are usage errors already
                if hasattr(exc, 'kind'):
                    raise
                ctx.fail(invalid('BAD_SETTING', '%s=%s is not understood.' % (key, text), 'see `chemlab help %s method`.' % self.instrument_id))
            if 'integer' in types_of(num) and float(v).is_integer():
                v = int(v)
            return v
        if isnum and txt.lower() in ('null', 'none') and 'null' in t:
            return None
        if txt.lower() in ('on', 'yes') and 'boolean' in t:
            return True
        if txt.lower() in ('off', 'no') and 'boolean' in t:
            return False
        try:
            return coerce(txt, schema, key)
        except Exception:                                                # noqa: BLE001
            ctx.fail(invalid('BAD_SETTING', '%s=%s does not fit (%s).' % (key, text, '/'.join(sorted(x for x in t if x)) or 'value'),
                             'see `chemlab help %s method`.' % self.instrument_id))

    def range_text(self, schema, kind=None):
        num = numeric_schema(schema)
        lo, hi = num.get('minimum'), num.get('maximum')
        u = units.declared(num)
        k2 = units.unit_kind(u) if u else (kind if kind in units.KINDS else None)

        def f(x):
            return units.fmt(units.from_declared(x, u) if u else x, k2) if k2 else '%g' % x
        if lo is not None and hi is not None:
            return '%s to %s' % (f(lo), f(hi))
        if lo is not None:
            return 'at least %s' % f(lo)
        if hi is not None:
            return 'at most %s' % f(hi)
        return 'any value'

    def check_ranges(self, ctx, cap, args, names=None):
        """Every numeric argument (and array / object member) inside the contract range: a usage error in friendly units, before
        anything is sent.  ``names``: native argument -> the name the agent typed (class key)."""
        sch = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        names = names or {}

        def walk(schema, v, where):
            if isinstance(v, bool) or v is None:
                return
            if isinstance(v, (int, float)):
                num = numeric_schema(schema)
                lo, hi = num.get('minimum'), num.get('maximum')
                exlo = num.get('exclusiveMinimum')
                bad = (lo is not None and v < lo - 1e-12 * max(1, abs(lo))) or (hi is not None and v > hi + 1e-12 * max(1, abs(hi))) or \
                      (exlo is not None and v <= exlo)
                if 'enum' in num and v not in num['enum']:
                    u = units.declared(num)
                    kind = units.unit_kind(u) if u else None
                    allowed = ', '.join(units.fmt(units.from_declared(x, u), kind) if kind else str(x) for x in num['enum'])
                    ctx.fail(invalid('OUT_OF_RANGE', '%s on %s takes only %s.' % (where, self.instrument_id, allowed),
                                     'give one of: %s.' % allowed))
                if bad:
                    u = units.declared(num)
                    kind = units.unit_kind(u) if u else None
                    got = units.fmt(units.from_declared(v, u), kind) if kind else '%g' % v
                    ctx.fail(invalid('OUT_OF_RANGE', '%s = %s is outside the range of %s (%s).' % (where, got, self.instrument_id,
                                                                                                 self.range_text(num)),
                                     'give %s %s.' % (where, self.range_text(num))))
            elif isinstance(v, list):
                for i, x in enumerate(v):
                    walk((schema or {}).get('items') or {}, x, '%s[%d]' % (where, i))
            elif isinstance(v, dict):
                sch = schema or {}
                if 'anyOf' in sch:                                       # the branch whose constants match (e.g. mode)
                    for b in sch['anyOf']:
                        pr = b.get('properties') or {}
                        if all(x.get('const', v.get(k)) == v.get(k) for k, x in pr.items() if isinstance(x, dict)) and pr:
                            sch = b
                            break
                for k, x in v.items():
                    walk((sch.get('properties') or {}).get(k) or {}, x, '%s.%s' % (where, k) if where else k)
        for k, v in args.items():
            if isinstance(v, dict) and k not in names and (sch.get(k) or {}).get('properties'):
                for k2, v2 in v.items():
                    walk((((sch.get(k) or {}).get('properties') or {}).get(k2)) or {}, v2, names.get(k2, k2))
            else:
                walk(sch.get(k) or {}, v, names.get(k, k))

    def validate_call(self, ctx, cap, args, names=None):
        """Contract validation of a whole call before anything is sent (usage error, nothing sent)."""
        self.check_ranges(ctx, cap, args, names)
        problems = validate(ctx.cap_spec(cap).get('input_schema') or {'type': 'object', 'properties': {}}, args, cap)
        if problems:
            ctx.fail(invalid('BAD_SETTING', 'the settings do not fit %s of %s: %s.' % (cap, self.instrument_id, '; '.join(problems[:4])),
                             'see `chemlab help %s method` (and `chemlab help %s %s`).' % (self.instrument_id, self.instrument_id, cap)))

    def last_args(self, ctx, cap):
        """Arguments of the last accepted call of ``cap`` on this instrument in this lab (any caller, from the lab's request log)."""
        entries = list(getattr(ctx.core.log, 'entries', []) or [])
        for e in reversed(entries):
            for c in reversed(e.get('backend_calls') or []):
                if c.get('op') == 'invoke' and c.get('instrument') == self.instrument_id and c.get('capability') == cap and \
                        c.get('status') in ('succeeded', 'accepted'):
                    return copy.deepcopy(c.get('arguments') or {})
        return None

    def slot_in_unit(self, ctx, sid, schema):
        """A slot value converted into the unit an argument declares (slot and argument units may differ)."""
        v = self.sv(ctx, sid)
        if not finite(v):
            return v
        su = units.declared(self.lab.contract['state_slots'].get(sid.split('#', 1)[0]) or {})
        au = units.declared(numeric_schema(schema))
        if su and au and su != au and units.unit_kind(su) == units.unit_kind(au):
            return units.to_declared(units.from_declared(v, su), au)
        return v

    # ------------------------------------------------------------------ method
    def method_calls(self, ctx, pairs, mapping=None):
        """[(key, text)] -> [(cap, args, info)] for the member's method calls (nothing sent).  info: given / kept lists."""
        m = mapping or self.vmap('method')
        calls = m.get('calls') or []
        plan = [{'call': c, 'given': {}, 'raw': {}} for c in calls]
        for key, text in pairs:
            found = None
            for row in plan:
                c = row['call']
                for k, spec in (c.get('keys') or {}).items():
                    if k == key:
                        found = (row, spec if isinstance(spec, str) else spec['arg'], spec if isinstance(spec, dict) else {},
                                 (spec.get('kind') if isinstance(spec, dict) and 'kind' in spec else (self.KEYS.get(k) or (None,))[0]))
                        break
                if found:
                    break
            if not found:
                for row in plan:
                    c = row['call']
                    if c.get('passthrough', True) and key in self.call_props(ctx, c):
                        found = (row, key, {}, None)
                        break
            if not found:
                known = sorted({k for c in calls for k in (c.get('keys') or {})} |
                               {k for c in calls if c.get('passthrough', True) for k in self.call_props(ctx, c)})
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (self.instrument_id, key),
                                 'settings: %s (see `chemlab help %s method`).' % (', '.join(known), self.instrument_id)))
            row, arg, spec, kind = found
            if arg in row['given']:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % key, 'give each setting once.'))
            schema = self.call_props(ctx, row['call']).get(arg) or {}
            vals = spec.get('values') or {}
            value = vals[text] if text in vals else self.parse_setting(ctx, key, text, schema, kind)
            row['given'][arg] = value
            row['raw'][arg] = key
        out = []
        for row in plan:
            c = row['call']
            if not row['given'] and not c.get('always'):
                continue
            args = dict(c.get('args') or {})
            info = {'given': dict(row['given']), 'kept_slots': {}, 'kept_last': {}, 'kept_example': {}}
            if c.get('object'):
                args[c['object']] = dict(row['given'])
            else:
                args.update(row['given'])
                self.keep_missing(ctx, c['cap'], args, c.get('keep') or {}, info)
                for arg, rule in (c.get('null_when') or {}).items():
                    if arg not in row['given'] and all(args.get(k) == v for k, v in rule.items()):
                        args[arg] = None
                        for kk in ('kept_slots', 'kept_last', 'kept_example'):
                            info[kk].pop(arg, None)
                need = list(((ctx.cap_spec(c['cap']).get('input_schema') or {}).get('required')) or [])
                missing = [a for a in need if a not in args]
                if missing:
                    ctx.fail(invalid('MISSING_SETTING', '%s needs %s too (no current value to keep).' % (c['cap'], ', '.join(missing)),
                                     'give %s (see `chemlab help %s method`).' % (', '.join(missing), self.instrument_id)))
            self.validate_call(ctx, c['cap'], args, row['raw'])
            out.append((c['cap'], args, info))
        if not out:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'see `chemlab help %s method`.' % self.instrument_id))
        return out

    def verb_help(self, name):
        out = ClassAdapter.verb_help(self, name)
        m = self.vmap(name) if name == 'method' else None
        if m and self.lab is not None and name not in self.unsupported_reasons():
            lines = ['', 'settings on %s:' % self.instrument_id]
            for c in m.get('calls') or []:
                p = (self.lab.contract['capabilities'][c['cap']].get('input_schema') or {}).get('properties') or {}
                if c.get('object'):
                    p = (p.get(c['object']) or {}).get('properties') or {}
                for key, spec in (c.get('keys') or {}).items():
                    arg = spec if isinstance(spec, str) else spec['arg']
                    sch = p.get(arg) or {}
                    kind = (spec.get('kind') if isinstance(spec, dict) and 'kind' in spec else (self.KEYS.get(key) or (None,))[0])
                    rng = (self.range_text(sch, kind) if types_of(sch) & {'number', 'integer'} else
                           ', '.join(map(str, sch.get('enum') or [])) or '/'.join(sorted(types_of(sch))))
                    lines.append('  %-16s -> %s.%s (%s)' % (key, c['cap'], arg, rng))
                if c.get('passthrough', True):
                    rest = [a for a in p if a not in [(s if isinstance(s, str) else s['arg']) for s in (c.get('keys') or {}).values()]]
                    if rest:
                        lines.append('  native names also work on %s: %s' % (c['cap'], ', '.join(rest)))
            out['text'] = out['text'].rstrip('\n') + '\n' + '\n'.join(lines) + '\n'
        return out

    def keep_missing(self, ctx, cap, args, keep, info=None):
        """Fill the required arguments the agent did not give: the member's own state slot ("slots"), else the last accepted call of
        ``cap`` on this instrument in this lab ("last"), else the contract's documented example ("example").  ``info`` collects
        which (kept_slots / kept_last / kept_example)."""
        info = info if info is not None else {'kept_slots': {}, 'kept_last': {}, 'kept_example': {}}
        sch = (ctx.cap_spec(cap).get('input_schema') or {})
        props = sch.get('properties') or {}
        need = list(sch.get('required') or []) if keep.get('required_only', True) else list(props)
        last = self.last_args(ctx, cap) if keep.get('last') else None
        example = (ctx.cap_spec(cap).get('example_arguments') or {}) if keep.get('example') else {}
        for arg in need:
            if arg in args:
                continue
            sid = (keep.get('slots') or {}).get(arg)
            if sid is not None and self.sv(ctx, sid) is not None:
                args[arg] = self.slot_in_unit(ctx, sid, props.get(arg) or {})
                info['kept_slots'][arg] = args[arg]
            elif last is not None and arg in last:
                args[arg] = copy.deepcopy(last[arg])
                info['kept_last'][arg] = args[arg]
            elif arg in example:
                args[arg] = copy.deepcopy(example[arg])
                info['kept_example'][arg] = args[arg]
        return info

    def call_props(self, ctx, call):
        p = (ctx.cap_spec(call['cap']).get('input_schema') or {}).get('properties') or {}
        if call.get('object'):
            return ((p.get(call['object']) or {}).get('properties') or {})
        return p

    def pairs(self, ctx, p, verb='method'):
        out = []
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', '%s needs at least one KEY=VALUE.' % verb, 'see `chemlab help %s %s`.' % (self.instrument_id, verb)))
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq or not k or v == '':
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write %s KEY=VALUE ... (see `chemlab help %s %s`).' % (
                    verb, self.instrument_id, verb)))
            out.append((k.strip(), v.strip()))
        return out

    def show_args(self, cap, args, obj=None):
        props = (self.lab.contract['capabilities'][cap].get('input_schema') or {}).get('properties') or {}
        if obj:
            props = (props.get(obj) or {}).get('properties') or {}
        parts = []
        for k, v in args.items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                u = units.declared(numeric_schema(props.get(k) or {}))
                kind = units.unit_kind(u) if u else None
                parts.append('%s=%s' % (k, units.fmt(units.from_declared(v, u), kind) if kind else short(v, 20)))
            else:
                parts.append('%s=%s' % (k, short(v, 40)))
        return ', '.join(parts)

    def h_method(self, ctx, p):
        self.need(ctx, 'method')
        calls = self.method_calls(ctx, self.pairs(ctx, p))
        lines, js = [], []
        objs = {c['cap']: c.get('object') for c in (self.vmap('method') or {}).get('calls') or []}
        for cap, args, info in calls:
            ctx.call(cap, args, 'set %s (%s)' % (cap, self.show_args(cap, info['given'] or args, objs.get(cap))))
            kept = []
            if info['kept_slots']:
                kept.append('%d kept as they are' % len(info['kept_slots']))
            if info['kept_last']:
                kept.append('%d from the last accepted method' % len(info['kept_last']))
            if info['kept_example']:
                kept.append('%d documented defaults (%s)' % (len(info['kept_example']), ', '.join(sorted(info['kept_example']))))
            lines.append('  %s: %s%s' % (cap, self.show_args(cap, info['given'], objs.get(cap)) or '(no change)', ('; other settings: ' + ', '.join(kept)) if kept else ''))
            js.append({'capability': cap, 'arguments': args, 'given': info['given'], 'kept_slots': info['kept_slots'],
                       'kept_last': info['kept_last'], 'documented_defaults': info['kept_example']})
        if ctx.dry_run:
            return {'text': '', 'json': {'calls': js}}
        return {'text': 'ok: method of %s set\n%s\n' % (self.instrument_id, '\n'.join(lines)), 'json': {'calls': js}}

    # ------------------------------------------------------------------ jobs
    def make_job(self, ctx, m, r, description, stop=None, max_s=None):
        """A job for a started long action: a StateJob on the member's slot ("job": {"slot", "done", "failed", "null_done"}), else an
        OperationJob when the runtime gave an operation id, else None."""
        spec = m.get('job') if isinstance(m.get('job'), dict) else None
        ad = self
        if spec:
            def state(c):
                v = ad.sv(c, spec['slot'])
                extra = {'state': v}
                for lab, sid in (spec.get('report') or {}).items():
                    extra[lab] = ad.sv(c, sid)
                text = '%s = %s' % (spec['slot'], short(v, 60)) + ''.join('; %s %s' % (k, short(x, 40)) for k, x in extra.items() if k != 'state')
                if spec.get('null_done'):
                    return ('succeeded' if v is None else 'running'), text, extra
                if v in (spec.get('done') or []):
                    return 'succeeded', text, extra
                if v in (spec.get('failed') or []):
                    return 'failed', text, extra
                return 'running', text, extra
            return StateJob(ctx.core, self, ctx.req, description, state, stop=stop, max_s=float(max_s or spec.get('max_s', 7200)),
                            poll_s=float(spec.get('poll_s', 1.0)))
        if r.get('operation_id'):
            return OperationJob(ctx.core, self, ctx.req, description, m['cap'], r['operation_id'], max_s=max_s)
        return None

    def job_reply(self, job, head):
        if job is None:
            return {'text': head + '\n', 'json': {}}
        return {'text': '%s\nstarted %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            head, job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}

    def stop_fn(self, verb='stop'):
        sm = self.vmap(verb)
        if not sm:
            return None

        def stop(c):
            c.call(sm['cap'], dict(sm.get('args') or {}), 'stop (%s)' % sm['cap'])
            return 'stopped with %s' % sm['cap']
        return stop

    def running_check(self, ctx, verb='start'):
        """Refuse a second start while the member's job slot says it runs."""
        m = self.vmap(verb) or {}
        spec = m.get('job') if isinstance(m.get('job'), dict) else None
        busy = (self.map.get('params') or {}).get('busy') or {}
        if busy.get('slot'):
            v = self.sv(ctx, busy['slot'])
            ok = v not in busy.get('values', []) if 'values' in busy else not (v is not None if busy.get('not_null') else bool(v))
            ctx.check(ok, 'BUSY', '%s is already running (%s = %s).' % (self.instrument_id, busy['slot'], short(v, 60)),
                      'wait for it (`chemlab run %s wait`) or stop it (`chemlab run %s stop`).' % (self.instrument_id, self.instrument_id),
                      label='not running')
        _ = spec

    # ------------------------------------------------------------------ records
    def record(self, ctx, verb, title):
        m = self.vmap(verb)
        if m.get('cap'):
            args = dict(m.get('args') or {})
            r = ctx.call(m['cap'], args, 'read the %s (%s)' % (title, m['cap']), query=True)
            res = r.get('result')
        else:
            res = {s: ctx.v(s) for s in m.get('reads') or []}
        return res

    def record_lines(self, res, keys=None, limit=24):
        lines = []
        if isinstance(res, dict):
            items = [(k, res[k]) for k in keys if k in res] if keys else list(res.items())
            for k, v in items[:limit]:
                if isinstance(v, list):
                    lines.append('  %s: %d points%s' % (k, len(v), (' (first %s)' % short(v[0], 120)) if v else ''))
                elif isinstance(v, dict) and any(isinstance(x, list) and len(x) > 5 for x in v.values()):
                    lines.append('  %s: %s' % (k, ', '.join('%s %s' % (k2, ('%d points' % len(x)) if isinstance(x, list) else short(x, 40))
                                                         for k2, x in list(v.items())[:12])))
                elif isinstance(v, dict):
                    lines.append('  %s: %s' % (k, short(v, 160)))
                else:
                    f = friendly_value(k, v)
                    lines.append('  %s = %s%s' % (k, short(v, 100), ('  (= %s)' % f) if f else ''))
        else:
            lines.append('  %s' % short(res, 400))
        return lines

    def h_record(self, verb, title):
        def h(ctx, p):
            self.need(ctx, verb)
            res = self.record(ctx, verb, title)
            if ctx.dry_run:
                return {'text': '', 'json': {}}
            keys = (self.vmap(verb) or {}).get('show')
            return {'text': '%s %s:\n%s\n' % (self.instrument_id, title, '\n'.join(self.record_lines(res, keys))), 'json': {title.replace(' ', '_'): res}}
        return h

    # ------------------------------------------------------------------ connections
    def h_connect(self, ctx, p):
        m = self.vmap('connect')
        ports = m.get('ports') or {}
        pairs = self.pairs(ctx, p, 'connect')
        todo = []
        for port, ident in pairs:
            if port not in ports:
                ctx.fail(invalid('UNKNOWN_PORT', '%s has no connection %r.' % (self.instrument_id, port),
                                 'connections of %s: %s.' % (self.instrument_id, ', '.join(ports))))
            native = ports[port]
            value = None if ident.lower() in ('none', 'null', '-') else ident
            todo.append((port, native, value))
        self.need(ctx, 'connect')
        done = []
        for port, native, value in todo:
            sid = (m.get('slots') or {}).get(port)
            if sid and self.sv(ctx, sid) == value:
                done.append('%s already %s' % (port, value))
                continue
            args = dict(m.get('args') or {})
            args[m['port_arg']] = native
            args[m['id_arg']] = value
            self.validate_call(ctx, m['cap'], args)
            ctx.call(m['cap'], args, ('connect %s to %s' % (port, value)) if value else 'disconnect %s' % port)
            done.append('%s = %s' % (port, value))
        return {'text': 'ok: %s connections: %s\n' % (self.instrument_id, '; '.join(done)), 'json': {'connections': {pt: v for pt, _, v in todo}}}

    # ------------------------------------------------------------------ waits
    def settle(self, ctx, test, what, max_s, poll_s=0.5):
        """Let lab time pass until test(ctx) holds (a stage or a positioner still moving).  Only verbs that say they wait use it."""
        if ctx.dry_run or test(ctx):
            return 0.0
        waited = ctx.advance(max_s, until=lambda v: test(ctx), step=what, poll_s=poll_s)
        if not test(ctx):
            ctx.fail(unexpected('NOT_SETTLED', '%s did not finish (%s) within %s.' % (self.instrument_id, what, units.fmt_s(max_s)),
                                'check `chemlab run %s status`.' % self.instrument_id, changed='yes'))
        return waited


_ = (math, refused, RUNNING)

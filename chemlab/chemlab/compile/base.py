"""ClassAdapter: the base of every class-level adapter (one class = one module with its verb table + one config fragment with the
per-instrument mapping).

What the base does for every class:

* verbs(): the class verbs this member supports (describe and `chemlab list` show them);
* dispatch_run(): `chemlab run INST VERB ...` -> help, unknown / unsupported verbs with reasons, argument and unit parsing,
  --dry-run (check and plan only; nothing is sent), destructive verbs need --yes, the handler, the reply (text and --json with
  the native calls it made), errors with what is wrong / how to fix / instrument changed / steps done;
* intercept(): `read` in friendly units with the class summary on top, `describe` with the class section, `help INST VERB`;
* explain_refusal(): a refused native call in plain words, from the runtime code, the mapping's code table, the class table,
  and the contract preconditions with the state already read;
* check_mapping(): the mapping against the contract (capabilities agent-callable and implemented, argument names in the input
  schema, state slots visible) - run at the first use and by the tests for every member.

A class subclasses it with CLASS / TITLE / ABOUT, build_verbs() and summary(); see COMPILE_GUIDE.md.
"""
import copy
import difflib
import json
import math
import re

from ..adapters import InstrumentAdapter
from ..generic import CmdError
from ..errors import ChemlabError
from . import units
from .ctx import Ctx
from .errors import CompileError, from_pilot, invalid, refused, unexpected, unsupported
from .verbs import Verb, parse_args


class MappingError(Exception):
    pass


def short(v, n=80):
    """Human-readable value (floats with 6 significant digits, units.tidy); --json replies keep the exact values."""
    s = json.dumps(units.tidy(v), ensure_ascii=False, default=str)
    return s if len(s) <= n else s[:n] + '...'


class ClassAdapter(InstrumentAdapter):
    CLASS = None                     # 'balance', 'temperature', 'pipetting', ...
    TITLE = ''
    ABOUT = ''
    STANDARDS = ''                   # vendor-neutral command sets the verbs follow
    CODES = {}                       # runtime code -> (CLASS_CODE, message, fix); templates may use {inst} {cap} {code}
    ALWAYS = ('status',)             # verbs every member has (no mapping needed)
    UNIT_KINDS = ()                  # quantity kinds shown in describe
    PATH_GROUPS = ()                 # verb groups that must use one native path per member, e.g. (('start', 'stop', 'result'),)
    layer = 'class'                  # set from the fragment: 'class' or 'shared'

    def __init__(self, instrument_id, config_entry):
        super().__init__(instrument_id, config_entry)
        self.map = copy.deepcopy(config_entry.get('class_map') or {})
        self.asset = (config_entry.get('asset_type_id') or ('instrument.' + instrument_id)).split('.', 1)[1]
        self.table = {}
        self.table['status'] = Verb('status', 'what the instrument is doing now, in plain units (class view)', self._h_status,
                                    readonly=True, example='chemlab run INST status')
        self.table.update(self.build_verbs())
        self.lab = None

    # ------------------------------------------------------------------ class hooks
    def build_verbs(self):
        """{name: Verb} of the class (handlers take (ctx, parsed) and return {'text', 'json', 'notes'})."""
        return {}

    def summary(self, values):
        """Plain-language lines (and a json dict) of the class view from a {slot: value} dict."""
        return [], {}

    def new_shadow(self):
        """Class bookkeeping a dry run may change without touching the real one (e.g. the liquid class's tip ledger)."""
        return None

    def shadow(self, ctx, cap, args):
        """Dry-run: update ctx.view as the call would (default: the contract's write_via slots of this capability)."""
        for sid, s in self.lab.contract['state_slots'].items():
            wv = s.get('write_via') or {}
            if isinstance(wv, dict) and wv.get('capability_id') == cap and wv.get('argument') in args:
                ctx.view[sid] = args[wv['argument']]

    # ------------------------------------------------------------------ mapping access
    MAP_ALIASES = {'close': 'open'}  # a verb that reads another verb's mapping (open/close share the parts table)

    def vmap(self, verb):
        v = self.map.get('verbs') or {}
        return v.get(verb) if verb in v else v.get(self.MAP_ALIASES.get(verb))

    def requirements(self, ctx, verb):
        """The member's own preconditions for a verb, from its mapping: "requires": {verb: [{"slot", <test>, "code", "message",
        "fix", "label"}]}; message and fix may use {inst} and {value} (the current slot value).  "slot" may be 'slot#key' (a key
        of an object slot), or give "key".  Tests (see requirement_ok): "value", "not", "in", "prefix", "contains_not",
        "min" / "max" / "gt" / "lt", "truthy", "falsy", "null", "set"; an entry without a test asks for a truthy value."""
        for r in (self.map.get('requires') or {}).get(verb, []):
            got = self.requirement_value(ctx, r)
            ok = self.requirement_ok(got, r)
            ctx.check(ok, r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=got),
                      r['fix'].format(inst=self.instrument_id, value=got), label=r.get('label', r['slot']))

    @staticmethod
    def requirement_value(ctx, r):
        sid, _, key = r['slot'].partition('#')
        got = ctx.v(sid) if key or r.get('key') else ctx.v(r['slot'])
        key = key or r.get('key')
        if key:
            got = got.get(key) if isinstance(got, dict) else None
        return got

    @staticmethod
    def requirement_ok(got, r):
        """One "requires" test on a slot value (several tests in one entry must all hold)."""
        tests = []
        if 'value' in r:
            tests.append(got == r['value'])
        if 'not' in r:
            tests.append(got not in r['not'])
        if 'in' in r:
            tests.append(got in r['in'])
        if 'prefix' in r:
            pre = [r['prefix']] if isinstance(r['prefix'], str) else list(r['prefix'])
            tests.append(isinstance(got, str) and any(got.startswith(x) for x in pre))
        if 'contains_not' in r:
            c = r['contains_not']
            tests.append(not (isinstance(got, (list, tuple, str, dict, set)) and c in got))
        num = isinstance(got, (int, float)) and not isinstance(got, bool) and math.isfinite(got)
        for op, f in (('min', lambda x, y: x >= y - 1e-12 * max(1.0, abs(y))), ('max', lambda x, y: x <= y + 1e-12 * max(1.0, abs(y))),
                      ('gt', lambda x, y: x > y), ('lt', lambda x, y: x < y)):
            if op in r:
                tests.append(num and f(got, r[op]))
        if r.get('truthy'):
            tests.append(bool(got))
        if r.get('falsy'):
            tests.append(not got)
        if r.get('null'):
            tests.append(got is None)
        if r.get('set'):
            tests.append(got is not None)
        return all(tests) if tests else bool(got)

    def wait_until(self, ctx, test, what, max_s=300.0, poll_s=1.0):
        """Let lab time pass until ``test(ctx)`` holds (a lid, a sash, a drawer, a stage or a transfer still moving); refused
        NOT_SETTLED when it does not within ``max_s``.  A dry run plans the wait.  Returns the seconds waited."""
        if ctx.dry_run:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': max_s},
                             'step': 'wait until %s (up to %s of lab time)' % (what, units.fmt_s(max_s))})
            return 0.0
        if test(ctx):
            return 0.0
        t0 = ctx.now()
        ctx.advance(max_s, until=lambda v: test(ctx), step='wait until %s' % what, poll_s=poll_s)
        if not test(ctx):
            ctx.fail(refused('NOT_SETTLED', '%s did not finish: %s was not reached after %s.' % (
                self.instrument_id, what, units.fmt_s(ctx.now() - t0)),
                'check `chemlab run %s status`; wait longer with `chemlab wait 60`, then retry.' % self.instrument_id))
        return ctx.now() - t0

    @staticmethod
    def moving_done(slots):
        """A test for wait_until: every slot in ``slots`` is false (not moving, no transfer)."""
        def test(ctx):
            return all(not ctx.v(s) for s in slots)
        return test

    def target_args(self, ctx, parsed, kinds=('zone', 'channel', 'cell', 'head')):
        """--zone / --channel / --cell / --head -> native arguments, from the mapping's "targets":
        {"zone": {"arg": NATIVE, "values": {"1": native value, ...}, "required": false}}."""
        out = {}
        for k in kinds:
            v = parsed.get(k)
            t = (self.map.get('targets') or {}).get(k)
            if v is None:
                if t and t.get('required'):
                    ctx.fail(invalid('MISSING_' + k.upper(), '%s needs --%s on %s.' % (ctx.verb.name, k, self.instrument_id),
                                     'give --%s %s.' % (k, '|'.join(map(str, (t.get('values') or {}))) or k.upper())))
                continue
            if not t:
                ctx.fail(unsupported('NO_' + k.upper(), '%s has no %ss.' % (self.instrument_id, k), 'leave out --%s.' % k))
            vals = t.get('values')
            if vals is not None and str(v) not in vals:
                ctx.fail(invalid('UNKNOWN_' + k.upper(), '%s has no %s %r.' % (self.instrument_id, k, v),
                                 '%ss of %s: %s.' % (k, self.instrument_id, ', '.join(map(str, vals)))))
            out[t['arg']] = vals[str(v)] if vals is not None else v
        return out

    def location(self, ctx, name):
        """A named location (autosampler slot, rack position, port) -> its native arguments, from the lab config entry's
        "locations" (task / lab specific) or the mapping's "locations" (fixed by the instrument)."""
        reg = dict(self.map.get('locations') or {})
        reg.update(self.config_entry.get('locations') or {})
        if name not in reg:
            close = difflib.get_close_matches(name, list(reg), 2)
            ctx.fail(invalid('UNKNOWN_LOCATION', '%s has no location %r.' % (self.instrument_id, name),
                             ('did you mean %s? ' % ' or '.join(close) if close else '') + 'locations: %s.' % (', '.join(sorted(reg)) or 'none')))
        return dict(reg[name])

    def slot(self, role):
        return (self.map.get('slots') or {}).get(role)

    def param(self, name, default=None):
        return (self.map.get('params') or {}).get(name, default)

    def unsupported_reasons(self):
        out = {k: str(v).replace('INST', self.instrument_id) for k, v in (self.map.get('unsupported') or {}).items()}
        for name in self.table:
            if name not in self.ALWAYS and name not in out and self.vmap(name) is None:
                out[name] = 'not mapped for this instrument yet'
        return out

    HIDES = {}                       # shared verb -> reason, for every member: verbs this class replaces (e.g. {'start': 'use `run`'})
    COVERS = {}                      # shared verb -> the class verb that replaces it (e.g. {'start': 'measure'}); same mechanism

    def hidden_verbs(self):
        """Shared verbs this class hides on this member: class HIDES / COVERS plus the member's "hides" ({verb: reason}) and
        "covers" ({shared verb: class verb}), minus the member's "shared_fallthrough"."""
        covers = dict(self.COVERS, **(self.map.get('covers') or {}))
        hides = {k: 'use `chemlab run INST %s`' % v for k, v in covers.items()
                 if v in self.table and self.supported(v)}             # only where the covering verb exists on this member
        hides.update(self.HIDES)
        hides.update(self.map.get('hides') or {})
        out = {k: str(v).replace('INST', self.instrument_id) for k, v in hides.items()}
        for k in self.map.get('shared_fallthrough') or []:
            out.pop(k, None)
        return out

    def blocks(self, name):
        """True when this class answers a verb it does not support on this member, so that the shared layer must not answer
        it: a verb of its table the member marks unsupported, or a shared verb it hides - unless the member lists it in
        "shared_fallthrough".  (Another class that supports the verb still wins: see CompositeAdapter.owner.)"""
        if self.layer == 'shared' or name in (self.map.get('shared_fallthrough') or []):
            return False
        return (name in self.table and name in (self.map.get('unsupported') or {})) or name in self.hidden_verbs()

    def supported(self, name):
        return name in self.table and name not in self.unsupported_reasons()

    def verbs(self):
        return {n: v.summary for n, v in self.table.items() if self.supported(n)}

    def maps_to(self, name):
        """Native capabilities a verb uses on this member (for help and describe)."""
        m = self.vmap(name) or {}
        caps = []

        def walk(o):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ('cap', 'caps') or k.endswith('_cap'):
                        for c in (v if isinstance(v, list) else [v]):
                            if isinstance(c, str) and c not in caps:
                                caps.append(c)
                    else:
                        walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(m)
        reads = [s for s in (m.get('reads') or [])]
        return caps + (['read %s' % ', '.join(reads)] if reads else [])

    # ------------------------------------------------------------------ binding and the mapping check
    def bind(self, core):
        if self.lab is None:
            lab = core.backend.instruments[self.instrument_id]
            problems = self.check_mapping(lab)
            if problems:
                raise MappingError('%s mapping of %s does not fit its contract: %s' % (self.CLASS, self.instrument_id, '; '.join(problems)))
            self.lab = lab
        return self.lab

    def check_mapping(self, lab):
        """Problems of this member's mapping against the contract and the agent view (empty list = fine)."""
        v, caps = lab.view, lab.contract['capabilities']
        problems = []
        visible = {s.lower() for s in v.slots}

        def is_slot_ref(s):
            """A state slot reference: a string 'component.field' (or 'component.field#key'), or a visible slot id.  Other
            values under a key named slot / *_slot (a rack slot "A1", a number) are class arguments, not slot references."""
            return isinstance(s, str) and ('.' in s.split('#', 1)[0] or s.split('#', 1)[0].lower() in visible)

        def cap_ok(c, where):
            if c not in caps:
                problems.append('%s: no capability %s' % (where, c))
            elif c not in v.capabilities:
                problems.append('%s: %s is not agent-callable and implemented' % (where, c))

        def walk(o, where, cap=None):
            if isinstance(o, dict):
                c = o.get('cap')
                if isinstance(c, str):
                    cap_ok(c, where)
                    cap = c
                for c2 in o.get('caps') or []:
                    cap_ok(c2, where)
                props = ((caps.get(cap) or {}).get('input_schema') or {}).get('properties') or {} if cap else {}
                if cap and isinstance(o.get('arg'), str) and o['arg'] not in props:
                    problems.append('%s: %s has no argument %s' % (where, cap, o['arg']))
                if cap and isinstance(o.get('args'), dict):
                    for a in o['args']:
                        if a not in props:
                            problems.append('%s: %s has no argument %s' % (where, cap, a))
                for key in [k for k in o if k in ('reads', 'slot', 'slots_read') or k.endswith('_slot')]:
                    for s in (o.get(key) if isinstance(o.get(key), list) else [o.get(key)]):
                        if is_slot_ref(s) and s.split('#', 1)[0].lower() not in visible:
                            problems.append('%s: no visible state slot %s' % (where, s))
                for k, x in o.items():
                    if k not in ('cap', 'arg', 'args', 'reads', 'slot', 'caps') and isinstance(x, (dict, list)):
                        walk(x, '%s.%s' % (where, k), cap if k not in ('verbs',) else None)
                    elif k.endswith('_cap') and isinstance(x, str):
                        cap_ok(x, '%s.%s' % (where, k))
            elif isinstance(o, list):
                for x in o:
                    walk(x, where, cap)
        for name, m in (self.map.get('verbs') or {}).items():
            if name not in self.table:
                problems.append('verbs.%s: not a %s verb' % (name, self.CLASS))
            walk(m, 'verbs.' + name)
        for role, s in (self.map.get('slots') or {}).items():
            for x in (s if isinstance(s, list) else [s]):
                if isinstance(x, dict):                                       # {'slot': 'a.b#key', 'unit_slot': ...}
                    x = [x.get('slot')] + ([x['unit_slot']] if x.get('unit_slot') else [])
                for y in (x if isinstance(x, list) else [x]):
                    if y is not None and y.split('#', 1)[0].lower() not in visible:
                        problems.append('slots.%s: no visible state slot %s' % (role, y))
        for verb, reqs in (self.map.get('requires') or {}).items():
            for r in reqs:
                if str(r.get('slot', '')).split('#', 1)[0].lower() not in visible:
                    problems.append('requires.%s: no visible state slot %s' % (verb, r.get('slot')))
        for name in self.map.get('unsupported') or {}:
            if name not in self.table:
                problems.append('unsupported.%s: not a %s verb' % (name, self.CLASS))
        problems += self.check_paths(lab)
        return problems

    def check_paths(self, lab):
        """One native path per verb group: the capabilities of the verbs in a PATH_GROUPS group share one namespace (the mapping's
        "path", e.g. "acquisition", or else the namespace of the first capability; "path_exceptions" lists extra allowed
        capabilities, and state slots a result may read although the start capability's state_effects leave them out), and a
        result read from state slots reads slots that the group's start capability changes."""
        problems = []
        caps = lab.contract['capabilities']
        for group in self.PATH_GROUPS:
            used = []
            for verb in group:
                used += [(verb, c) for c in self.maps_to(verb) if not c.startswith('read ')]
            if not used:
                continue
            path = self.map.get('path') or used[0][1].split('.', 1)[0]
            allowed = set(self.map.get('path_exceptions') or [])
            for verb, c in used:
                if c.split('.', 1)[0] != path and c not in allowed:
                    problems.append('path: %s uses %s, outside the %s path of this member (one native path per verb; list a '
                                    'deliberate exception in "path_exceptions")' % (verb, c, path))
            start = (self.vmap(group[0]) or {}).get('cap')
            for verb in group[1:]:
                reads = (self.vmap(verb) or {}).get('reads') or []
                if start and reads:
                    eff = {x.get('state_id') for x in (caps.get(start) or {}).get('state_effects') or []} | set((caps.get(start) or {}).get('observes') or [])
                    for r in reads:
                        if r not in eff and r not in allowed and r.split('#', 1)[0] not in allowed:   # slots named in path_exceptions are allowed
                            problems.append('path: %s reads %s, which %s (the %s capability) does not change' % (verb, r, start, group[0]))
        return problems

    # ------------------------------------------------------------------ contract helpers
    def cap_schema(self, cap, arg):
        return ((self.lab.contract['capabilities'][cap].get('input_schema') or {}).get('properties') or {}).get(arg) or {}

    def arg_range(self, cap, arg):
        """(minimum, maximum) of a numeric argument from the contract (None when open)."""
        s = self.cap_schema(cap, arg)
        if 'anyOf' in s:
            s = next((x for x in s['anyOf'] if x.get('type') in ('number', 'integer')), s)
        return s.get('minimum'), s.get('maximum')

    def arg_unit(self, cap, arg):
        """The unit an argument declares (normalised; None when unitless)."""
        return units.declared(self.cap_schema(cap, arg))

    def arg_value(self, cap, arg, si):
        """An SI value -> the value in the unit this argument declares (K stays K, degC gets °C, rpm gets rpm ...)."""
        u = self.arg_unit(cap, arg)
        return units.to_declared(si, u) if u else si

    def arg_range_si(self, cap, arg):
        """(minimum, maximum) of a numeric argument in SI (None when open)."""
        lo, hi = self.arg_range(cap, arg)
        u = self.arg_unit(cap, arg)
        return (units.from_declared(lo, u) if lo is not None and u else lo, units.from_declared(hi, u) if hi is not None and u else hi)

    def slot_si(self, values, spec):
        """A slot value in SI: ``spec`` is a slot id, 'slot#key' (a key of an object slot) or {'slot', 'key', 'unit',
        'unit_slot'} (a declared unit, or a slot holding the display unit such as C / F)."""
        if spec is None:
            return None
        if isinstance(spec, str):
            spec = {'slot': spec}
        sid, _, key = spec['slot'].partition('#')
        get = values.v if hasattr(values, 'v') else (lambda k: values.get(k))
        v = get(sid)
        if key and isinstance(v, dict):
            v = v.get(key)
        if not finite(v):
            return None
        u = spec.get('unit')
        if spec.get('unit_slot'):
            u = {'C': '°C', 'F': '°F', 'K': 'K'}.get(str(get(spec['unit_slot'])), u)
        if u is None:
            s = self.lab.contract['state_slots'].get(sid) or {}
            u = units.declared(s) if not key else None
        return units.from_declared(v, u) if u else v

    def arg_enum(self, cap, arg):
        s = self.cap_schema(cap, arg)
        return list(s.get('enum') or [])

    def contract_error_text(self, ctx, cap, code):
        for e in (self.lab.contract['capabilities'].get(cap) or {}).get('errors') or []:
            if e.get('code') == code:
                return e.get('meaning_zh') or e.get('meaning') or ''
        return ''

    def contract_param(self, name):
        """A documented parameter of the contract ({'value', 'unit'} found anywhere under a 'parameters' key)."""
        found = []

        def walk(o):
            if isinstance(o, dict):
                p = o.get('parameters')
                if isinstance(p, dict) and isinstance(p.get(name), dict) and 'value' in p[name]:
                    found.append(p[name])
                for x in o.values():
                    walk(x)
            elif isinstance(o, list):
                for x in o:
                    walk(x)
        walk(self.lab.contract)
        return found[0] if found else None

    # ------------------------------------------------------------------ run
    def dispatch_run(self, core, req, args):
        try:
            self.bind(core)
        except MappingError as exc:
            self.mapping_error = str(exc)
            raise unexpected('MAPPING_ERROR', 'the %s verbs of %s are misconfigured (their mapping does not fit the contract).' % (
                self.CLASS, self.instrument_id), 'use its own capabilities meanwhile (`chemlab describe %s`, `chemlab call %s CAP`); '
                'this is reported to the lab.' % (self.instrument_id, self.instrument_id), changed='no')
        inst = self.instrument_id
        if not args or args[0] in ('--help', '-h'):
            return self.overview(core, req)
        name, tokens = args[0], args[1:]
        if req.flags.get('help') or '--help' in tokens or '-h' in tokens:
            return self.verb_help(name)
        hidden = self.hidden_verbs()
        if name in hidden and name not in self.table:
            raise unsupported('NOT_SUPPORTED', '%s: %s is replaced here: %s.' % (inst, name, hidden[name]),
                              'see `chemlab run %s` for its verbs.' % inst)
        if name not in self.table:
            close = difflib.get_close_matches(name, list(self.verbs()), 2)
            raise invalid('UNKNOWN_VERB', '%s has no %s verb %r.' % (inst, self.CLASS, name),
                          ('did you mean %s? ' % ' or '.join(close) if close else '') +
                          'its verbs: %s (`chemlab run %s` lists them); its own capabilities stay available with `chemlab call %s CAP`.'
                          % (', '.join(self.verbs()), inst, inst))
        reasons = self.unsupported_reasons()
        if name in reasons:
            alt = (self.map.get('alternatives') or {}).get(name)
            alt = alt.replace('INST', inst) if alt else alt
            raise unsupported('NOT_SUPPORTED', '%s cannot %s: %s.' % (inst, name, reasons[name]),
                              alt or 'see `chemlab run %s` for what it can do, or `chemlab describe %s` for its own capabilities.' % (inst, inst))
        verb = self.table[name]
        parsed = parse_args(verb, tokens, inst)
        if parsed.get('save-to'):
            core._work_path(parsed.get('save-to'))                          # a path outside the work dir is a usage error, sent nothing
        dry = parsed.dry_run or (verb.destructive and not parsed.yes)
        ctx = Ctx(self, core, req, verb, parsed, dry_run=dry)
        ctx.shadow = self.new_shadow() if dry else None
        try:
            result = verb.handler(ctx, parsed) or {}
        except CompileError as exc:
            if not exc.done:
                exc.done = list(ctx.done)
            if exc.changed == 'no' and ctx.changed:
                exc.changed = 'yes'
            exc.extra = {'instrument': inst, 'class': self.CLASS, 'verb': name, 'native_calls': self.native_calls(req)}
            raise
        except ChemlabError as exc:                                       # shared pilot code raising the pilot classes
            e = from_pilot(exc)
            e.done = e.done or list(ctx.done)
            e.extra = {'instrument': inst, 'class': self.CLASS, 'verb': name, 'native_calls': self.native_calls(req)}
            raise e
        except MappingError:
            raise
        except CmdError as exc:                                           # a generic usage/refusal error (e.g. a bad value)
            if not ctx.changed:
                raise                                                     # nothing was sent: keep it as it is (exit 2 etc.)
            e = CompileError(exc.kind, exc.code, exc.message, exc.fix, changed='yes', done=list(ctx.done), see=exc.see)
            e.extra = {'instrument': inst, 'class': self.CLASS, 'verb': name, 'native_calls': self.native_calls(req)}
            raise e
        except Exception as exc:                                          # an adapter bug: report what was done
            e = unexpected('INTERNAL', 'the %s layer hit an unexpected problem (%s).' % (self.CLASS, type(exc).__name__),
                           'check `chemlab run %s status` before going on; report this.' % inst,
                           changed='unknown' if ctx.changed else 'no', done=ctx.done)
            e.trace = repr(exc)
            raise e
        return self.render(ctx, verb, result, destructive_preview=verb.destructive and not parsed.yes and not parsed.dry_run)

    @staticmethod
    def native_calls(req):
        return [{'capability': c.get('capability'), 'arguments': c.get('arguments'), 'status': c.get('status'), 'code': c.get('code')}
                for c in getattr(req, 'calls', []) if c.get('op') == 'invoke']

    def render(self, ctx, verb, result, destructive_preview=False):
        js = {'instrument': self.instrument_id, 'class': self.CLASS, 'verb': verb.name, 'dry_run': ctx.dry_run, 'time_s': ctx.now()}
        js.update(result.get('json') or {})
        notes = list(ctx.notes) + list(result.get('notes') or [])
        if ctx.waited_s:
            js['waited_s'] = ctx.waited_s
        if ctx.dry_run:
            js['plan'] = [p['step'] for p in ctx.plan]
            js['backend_calls'] = [{'capability': p['capability'], 'arguments': p['arguments']} for p in ctx.plan if p['capability']]
            js['checks'] = ctx.checks + list(result.get('checks') or [])
            js['unchecked'] = list(result.get('unchecked') or [])
            head = ('not done: %s changes or deletes things that cannot be restored; add --yes to do it. It would:' % verb.name
                    if destructive_preview else 'dry-run (nothing was sent to %s):' % self.instrument_id)
            lines = [head]
            for n, p in enumerate(ctx.plan, 1):
                call = (' -> %s %s' % (p['capability'], short(p['arguments'], 120))) if p['capability'] else ''
                lines.append('  %d. %s%s' % (n, p['step'], call))
            if not ctx.plan:
                lines.append('  (no instrument calls needed)')
            if js['checks']:
                lines.append('checks passed: ' + '; '.join(js['checks']))
            if js['unchecked']:
                lines.append('checked only when it runs: ' + '; '.join(js['unchecked']))
            text = '\n'.join(lines) + '\n'
        else:
            js['native_calls'] = self.native_calls(ctx.req)
            text = result.get('text', '')
            if ctx.args is not None and ctx.args.get('save-to'):
                p = ctx.core._work_path(ctx.args.get('save-to'))
                rec = ctx.lab.view.redact(dict(js, request_id=ctx.req.request_id))
                p.write_text(json.dumps(rec, ensure_ascii=False, indent=1, default=str) + '\n')
                rel = str(p.relative_to(ctx.core.work_dir))
                js['saved_to'] = rel
                text += 'saved the full record to %s (in your work dir)\n' % rel
        return {'text': text, 'json': js, 'notes': notes}

    def _h_status(self, ctx, p):
        lines, js = self.summary(ctx.view)
        head = '%s (%s) at t = %.1f s' % (self.instrument_id, self.TITLE or self.CLASS, ctx.now())
        return {'text': '\n'.join([head] + ['  ' + x for x in lines]) + '\n', 'json': {'state': js}}

    # ------------------------------------------------------------------ refusals
    def explain_refusal(self, ctx, cap, args, response):
        err = response.get('error') or {}
        code = err.get('code') or 'NO_CODE'
        status = response.get('status')
        fill = {'inst': self.instrument_id, 'cap': cap, 'code': code}
        m = (self.map.get('codes') or {}).get('%s:%s' % (cap, code)) or (self.map.get('codes') or {}).get(code)
        if m:
            return CompileError(m.get('kind', 'refused'), m.get('code', code), m['message'].format(**fill), (m.get('fix') or '').format(**fill) or None)
        if code in self.CODES:
            c2, msg, fix = self.CODES[code]
            return CompileError('refused', c2, msg.format(**fill), fix.format(**fill))
        if status == 'unsupported':
            return unsupported('UNSUPPORTED', '%s says %s is not supported by this twin.' % (self.instrument_id, cap),
                               'see `chemlab describe %s`.' % self.instrument_id)
        if code == 'RUNTIME_EXCEPTION' or status not in ('rejected', 'failed'):
            return unexpected('INSTRUMENT_ERROR', '%s answered %s with %s %s.' % (self.instrument_id, cap, status, code),
                              'check `chemlab run %s status` before going on.' % self.instrument_id)
        # a generic code: say what the instrument checks, with the state already read
        pre = (self.lab.contract['capabilities'].get(cap) or {}).get('preconditions') or []
        ids, texts = [], []
        for p in pre:
            texts.append(p.get('description_zh') or '')
            for sid in p.get('state_ids') or []:
                if sid in self.lab.view.slots:
                    if sid not in ids:
                        ids.append(sid)
        now = ', '.join('%s=%s' % (s, short(ctx.v(s), 40)) for s in ids[:8])
        msg = '%s refused %s (%s).' % (self.instrument_id, cap, code)
        if now:
            msg += ' It checks %s; now %s.' % (', '.join(ids[:8]), now)
        why = '; '.join(t for t in texts if t)
        fix = ('the instrument\'s own conditions (zh): %s. ' % why if why else '') + \
              'Check them with `chemlab read %s%s` and see `chemlab help %s %s`.' % (self.instrument_id, (' ' + ' '.join(ids[:8])) if ids else '',
                                                                                   self.instrument_id, cap)
        return refused(code, msg, fix)

    # ------------------------------------------------------------------ describe / help / read
    def class_section(self, taken=()):
        """Describe lines and json of this class on this member; ``taken``: verbs an earlier class owns (composite instruments)."""
        inst = self.instrument_id
        reasons = self.unsupported_reasons()
        title = '%s: %s' % (self.CLASS, self.TITLE) + (', besides the verbs above' if taken else '')
        lines = ['class-level verbs (%s)   chemlab run %s VERB ... [--dry-run]   chemlab help %s VERB' % (title, inst, inst)]
        rows = []
        comp = getattr(self, 'composite', None)

        def mine(n):                                   # on a composite instrument, the verbs another part answers are not ours
            return n not in taken and (comp is None or comp.owner(n) is self)
        for n, v in self.table.items():
            if n in reasons or not mine(n):
                continue
            caps = self.maps_to(n)
            lines.append('  %-14s %s%s' % (n, v.summary, ('   [%s]' % ', '.join(caps)) if caps else ''))
            rows.append({'verb': n, 'summary': v.summary, 'usage': v.usage(inst), 'maps_to': caps, 'readonly': v.readonly,
                         'destructive': v.destructive})
        missing = {n: r for n, r in reasons.items() if n in self.table and mine(n)}
        missing.update({n: 'replaced here: ' + r for n, r in self.hidden_verbs().items() if n not in self.table and mine(n)
                        and (comp is not None or self.layer != 'shared')})
        if missing:
            lines.append('  not on this instrument: ' + '; '.join('%s (%s)' % kv for kv in missing.items()))
        kinds = sorted({k for v in self.table.values() for k in v.kinds()} | set(self.UNIT_KINDS))
        if kinds:
            lines.append('  units: ' + units.describe_kinds(kinds))
        lines.append('  errors say what is wrong, how to fix it and whether the instrument changed; long actions give job-N '
                     '(chemlab status|wait|cancel job-N).')
        return lines, {'name': self.CLASS, 'title': self.TITLE, 'about': self.ABOUT, 'verbs': rows, 'not_supported': missing,
                       'units': kinds, 'standards': self.STANDARDS}

    def overview(self, core, req, taken=()):
        lines, js = self.class_section(taken)
        return {'text': '\n'.join(lines) + '\n', 'json': {'instrument': self.instrument_id, 'class': js}}

    def verb_help(self, name):
        inst = self.instrument_id
        hidden = self.hidden_verbs()
        if name in hidden and name not in self.table:
            text = '%s on %s: replaced here: %s.\n  see `chemlab run %s` for its verbs.\n' % (name, inst, hidden[name], inst)
            return {'text': text, 'json': {'instrument': inst, 'class': self.CLASS, 'verb': name, 'supported': False,
                                           'replaced': hidden[name]}}
        if name not in self.table:
            raise invalid('UNKNOWN_VERB', '%s has no %s verb %r.' % (inst, self.CLASS, name), 'see `chemlab run %s`.' % inst)
        reasons = self.unsupported_reasons()
        v = self.table[name]
        raw = (self.map.get('notes') or {}).get(name, []) if isinstance(self.map.get('notes'), dict) else []
        notes = [n.replace('INST', inst) if isinstance(n, str) else n for n in ([raw] if isinstance(raw, str) else list(raw))]
        if name in reasons:
            notes.insert(0, 'not supported on %s: %s' % (inst, reasons[name]))
        text = re.sub(r'\bINST\b', inst, v.help_text(inst, self.maps_to(name), notes))     # INST anywhere in the help (notes, errors)
        return {'text': text, 'json': {'instrument': inst, 'class': self.CLASS, 'verb': name, 'usage': v.usage(inst), 'summary': v.summary,
                                       'maps_to': self.maps_to(name), 'supported': name not in reasons}}

    def intercept(self, core, req):
        cmd = req.command
        try:
            if cmd == 'read':
                self.bind(core)
                return self.friendly_read(core, req)
            if cmd == 'describe' and len(req.args) == 1:
                self.bind(core)
                return self.describe(core, req)
            if cmd == 'help' and len(req.args) >= 2 and (req.args[1] in self.table or req.args[1] in self.hidden_verbs()):
                self.bind(core)
                return self.verb_help(req.args[1])
        except MappingError as exc:                                      # a broken mapping never breaks the generic commands
            self.mapping_error = str(exc)
            return None
        return None

    def describe(self, core, req):
        r = core._describe(self.instrument_id, req.caller, full=req.flags.get('json'))
        text = r['text']
        lines, js = self.class_section()
        a = text.find('\nclass-level verbs')
        b = text.find('\nfull form:')
        if a >= 0 and b > a:
            text = text[:a] + '\n' + '\n'.join(lines) + '\n' + text[b:]
        elif b >= 0:
            text = text[:b] + '\n' + '\n'.join(lines) + '\n' + text[b:]
        else:
            text += '\n'.join(lines) + '\n'
        out = dict(r['json'])
        out['class'] = js
        return {'text': text, 'json': out}

    def friendly_read(self, core, req):
        r = core._read(req)
        obs = r['json']['observations']
        friendly = {}
        for k, o in obs.items():
            if isinstance(o, dict):
                slot = (self.lab.contract['state_slots'].get(k) or {}) if getattr(self, 'lab', None) else {}
                f = units.friendly(o.get('value'), o.get('unit'), k, slot.get('meaning_zh') or slot.get('description'))
                if f:
                    friendly[k] = f
        lines = []
        for line in r['text'].splitlines():
            key = line.strip().split(' ', 1)[0] if line.startswith('  ') else None
            if key in friendly:
                line += '  (= %s)' % friendly[key]
            lines.append(line)
        js = dict(r['json'])
        js['friendly'] = friendly
        if len(req.args) == 1:
            values = {k: (o.get('value') if isinstance(o, dict) else o) for k, o in obs.items()}
            try:
                s_lines, s_js = self.summary(values)
            except Exception:                                             # noqa: BLE001 - a summary must never break read
                s_lines, s_js = [], {}
            if s_lines:
                lines[1:1] = ['  %s view: %s' % (self.CLASS, s_lines[0])] + ['  %s       %s' % (' ' * len(self.CLASS), x) for x in s_lines[1:]]
                js['class_view'] = s_js
        return {'text': '\n'.join(lines) + '\n', 'json': js, 'notes': r.get('notes') or []}


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

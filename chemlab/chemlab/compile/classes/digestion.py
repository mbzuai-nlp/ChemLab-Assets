"""Digestion and hydrolysis class ("digestion"): microwave acid digestion (Multiwave 7101), graphite block digestion (DigiPREP MS),
UV digestion (Metrohm 909) and acid hydrolysis (BUCHI HydrolEx H-506).

Verbs: method KEY=VALUE ..., load POSITIONS [--material --method --mass --acid], set-temp T, preheat on|off [--wait], start
(job), stop, result [--slot N] [--save-to F], status.  power, open/close (door, shield), ack and wait come from the shared layer.
Members and mappings: configs/adapters/digestion.json.

Method dictionary (keys with units; native argument names of the member also pass through):

    temp=T          digestion temperature (Multiwave step temperature, DigiPREP block, Metrohm sample temperature)
    ramp=TIME       time to reach temp (Multiwave first step, DigiPREP programme)
    hold=TIME       time at temperature (alias time=; Multiwave hold step, DigiPREP hold, Metrohm duration, HydrolEx hydrolysis)
    program=T@TIME,T@TIME,...   several steps; each ramps to T within TIME (equal T = hold)   (Multiwave, DigiPREP)
    power=P         microwave power limit of the method (Multiwave; default the maximum)
    fan=N           Multiwave fan level of the steps (1-3, default 1)
    name=NAME       Multiwave method name (`method name=NAME` alone selects a method of the library)
    timeout=TIME    DigiPREP: abort the cycle after this long (default the programme + hold + 2 h)
    rinse-temp=T rinse-cycles=N rinse-volume=V    HydrolEx rinse settings (default: the documented example)

Positions (load, result --slot) are counted from 1 as on the rack (Multiwave 1-24 = slot index 0-23, HydrolEx 1-6).

Vendor-neutral references: EPA SW-846 3051A / 3052 / 3015A (microwave digestion programmes: ramp, hold, temperature window),
ISO 15587 (aqua regia digestion), USP <233> sample preparation; process-equipment style start / stop / recipe select (SEMI E30
terms).  There is no lab command standard for digesters.
"""
from .. import units
from ..base import finite, short
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb, save_to_opt
from .process_common import ProcessAdapter, positions

REF = ('Anton Paar Multiwave 7101 manual: methods (create, step editor), start/stop, rack and vessels; SCP DigiPREP MS manual: '
       'temperature programme; Metrohm 909 UV Digester manual: method, start, lamp cooling; BUCHI HydrolEx H-506 manual: '
       'preheating, rack, stopcocks, hydrolysis')
ON_OFF = ['on', 'off']


class DigestionAdapter(ProcessAdapter):
    CLASS = 'digestion'
    TITLE = 'digestion and hydrolysis'
    ABOUT = ('Instruments that digest or hydrolyse a sample under a temperature programme: set the method (temperature, ramp, hold), '
             'load vessels, start the run (a job), stop it, read the run record.')
    STANDARDS = ('EPA SW-846 3051A / 3052 / 3015A microwave digestion programmes (ramp, hold, temperature window); ISO 15587; '
                 'USP <233> sample preparation; SEMI E30 style start / stop / recipe select')
    ALWAYS = ('status',)
    UNIT_KINDS = ('temperature', 'duration', 'power', 'mass', 'volume')
    PATH_GROUPS = (('start', 'stop', 'result'),)
    KEYS = {'temp': ('temperature', 'digestion temperature, e.g. 180C'),
            'ramp': ('duration', 'time to reach temp, e.g. 15min'),
            'hold': ('duration', 'time at temperature, e.g. 15min (alias time=)'),
            'program': (None, 'steps T@TIME,..., e.g. 180C@15min,180C@15min'),
            'power': ('power', 'microwave power limit, e.g. 1500W'),
            'fan': ('count', 'fan level 1-3'),
            'name': (None, 'method name'),
            'timeout': ('duration', 'abort the cycle after this long'),
            'rinse-temp': ('temperature', 'rinse water temperature'),
            'rinse-cycles': ('count', 'least rinse cycles'),
            'rinse-volume': ('volume', 'least rinse volume per position')}
    ALIASES = {'time': 'hold', 'temperature': 'temp'}
    SUMMARY_FLAGS = (('method', 'method'), ('sample', 'samples'), ('door', 'door'), ('shield', 'shield'),
                     ('selected', 'method'), ('mode', 'heater'), ('preheat', 'preheated'), ('rack', 'rack'))
    FLAG_WORDS = {'method': ('set', 'not set'), 'sample': ('loaded', 'none'), 'door': ('closed', 'open'), 'shield': ('open', 'closed')}
    CODES = {
        'TEMPLATE_SAMPLE_REQUIRED': ('NO_VESSEL_CONTENT', 'no vessel contents are declared at that position of {inst}.',
                                    'declare them first: `chemlab run {inst} load N --material ID --method ID --mass M --acid V` (door open).'),
        'METHOD_MATRIX_MISMATCH': ('METHOD_MATRIX_MISMATCH', 'that EPA method is for aqueous samples, not for the solid reference material.',
                                   'choose a method for solids (e.g. epa_3051a_hno3 or epa_3052_hno3_hf).'),
        'UNKNOWN_REFERENCE': ('UNKNOWN_REFERENCE', '{inst} does not know that material or method id.',
                              'look it up with `chemlab call {inst} template.read --template_id ID`.'),
        'RAMP_PROGRAM': ('RAMP_PROGRAM', 'the programme of {inst} is a ramp of several steps; set-temp changes only a single-temperature programme.',
                         'set the programme with `chemlab run {inst} method program=...` (or `method temp=... hold=...`).'),
        'TUBE_TEMPERATURE_LIMIT': ('TUBE_LIMIT', 'a programme temperature is outside the range the tubes of {inst} allow (lab set-up).',
                                   'choose a lower temperature (the block reads the tube limit from the lab set-up).'),
        'PROGRAM_ORDER': ('BAD_PROGRAM', 'the programme steps of {inst} must have increasing times.', 'give each step a time > 0.'),
        'REFERENCE_RANGE': ('REFERENCE_RANGE', 'a loaded sample has no reference data at that temperature.', 'choose a temperature inside the sample data range.'),
        'BUSY': ('BUSY', '{inst} is running.', 'stop it first (`chemlab run {inst} stop`) or wait for the job.'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        return {
            'method': Verb('method', 'set the digestion method: KEY=VALUE ... (temp, ramp, hold, program, power, ...)', self.h_method,
                           variadic=('KEY=VALUE', 'a method setting'),
                           preconditions=['values within the contract ranges', 'not running', 'the member\'s own rules (whole °C, login)'],
                           errors=['MISSING_SETTING, UNKNOWN_SETTING, OUT_OF_RANGE (usage)', 'BUSY, NOT_LOGGED_IN (refused)'],
                           details='the keys depend on the instrument; they are listed below with their units.'),
            'load': Verb('load', 'load vessels at rack positions (and declare their contents where the instrument records them)', self.h_load,
                         positionals=[Arg('POSITIONS', 'rack positions from 1, e.g. 1,3,5 or 1-4')],
                         options={'material': Opt('reference material id of the sample (Multiwave)', metavar='ID'),
                                  'method': Opt('EPA method id of the digestion (Multiwave), e.g. epa_3051a_hno3', metavar='ID'),
                                  'mass': Opt('sample mass per vessel, e.g. 0.2g', kind='mass', metavar='MASS'),
                                  'acid': Opt('acid volume per vessel, e.g. 4mL', kind='volume', metavar='VOLUME')},
                         preconditions=['door open (Multiwave)', 'not running', 'positions on the rack'],
                         errors=['BAD_POSITION, MISSING_OPTION (usage)', 'DOOR_CLOSED, BUSY (refused)'],
                         example='chemlab run INST load 1,2 --material nist_srm_2711a --method epa_3051a_hno3 --mass 0.2g --acid 4mL'),
            'set-temp': Verb('set-temp', 'set the digestion temperature of the configured programme', self.h_set_temp,
                             positionals=[Arg('TEMP', 'temperature, e.g. 95C', kind='temperature')],
                             preconditions=['within the contract range', 'a method is set (DigiPREP)', 'the member\'s step (whole °C)'],
                             errors=['OUT_OF_RANGE, BAD_STEP (usage)', 'NO_METHOD, BUSY, RAMP_PROGRAM (refused)'],
                             example='chemlab run INST set-temp 95C'),
            'preheat': Verb('preheat', 'switch the heater to preheating (on) or off; --wait until it is preheated', self.h_preheat,
                            positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                            options={'wait': Opt('wait until the preheating time is reached (lab time passes)', kind='flag'),
                                     'max': Opt('with --wait: the longest wait (default 15 min)', kind='duration', metavar='TIME')},
                            preconditions=['power on', 'not running'], errors=['POWER_OFF, NOT_READY (refused)'],
                            example='chemlab run INST preheat on --wait'),
            'start': Verb('start', 'start the digestion run (returns job-N)', self.h_start, long_action=True,
                          preconditions=['the member\'s run conditions (method set, door / shield closed, preheated, samples present)'],
                          errors=['NOT_READY, DOOR_OPEN, SHIELD_OPEN, COOLING, NOT_PREHEATED, RACK_UP, NO_METHOD, BUSY (refused)'],
                          example='chemlab run INST start'),
            'stop': Verb('stop', 'stop the running digestion (it then cools down)', self.h_stop, preconditions=['running'],
                         errors=['NOT_RUNNING, ALREADY_COOLING (refused)'], example='chemlab run INST stop'),
            'result': Verb('result', 'the run record (Multiwave: the evaluation of one vessel, --slot N)', self.h_result, readonly=True,
                           options={'slot': Opt('rack position from 1 (Multiwave)', metavar='N'), 'save-to': save_to_opt()},
                           errors=['MISSING_SLOT (usage)', 'NO_VESSEL_CONTENT (refused)'],
                           example='chemlab run INST result --save-to digest.json'),
        }

    def verb_help(self, name):
        out = super().verb_help(name)
        if name == 'method' and self.vmap('method'):
            lines = ['method keys on %s:' % self.instrument_id] + ['  %-14s %s' % (k + '=', self.KEYS[k][1]) for k in self.member_keys()]
            d = self.defaults_text()
            if d:
                lines.append('defaults for settings you leave out (documented example): %s' % d)
            if self.param('method_example'):
                lines.append('example on %s: chemlab run %s method %s' % (self.instrument_id, self.instrument_id, self.param('method_example')))
            out['text'] += '\n'.join(lines) + '\n'
        return out

    def member_keys(self):
        m = self.vmap('method') or {}
        b = m.get('build', 'keys')
        if b == 'editor':
            return ['name', 'temp', 'ramp', 'hold', 'program', 'power', 'fan']
        if b == 'knots':
            return ['temp', 'ramp', 'hold', 'program', 'timeout']
        return list((m.get('keys') or {}).keys())

    def defaults_text(self):
        m = self.vmap('method') or {}
        if m.get('build', 'keys') != 'keys' or not m.get('defaults') or self.lab is None:
            return ''
        ex = self.example(m['cap'])
        inv = {v: k for k, v in (m.get('keys') or {}).items()}
        req = set(m.get('required') or [])
        props = ((self.lab.contract['capabilities'][m['cap']].get('input_schema') or {}).get('properties') or {})
        out = []
        for a, v in ex.items():
            if a in (m.get('args') or {}) or inv.get(a) in req:
                continue
            u = units.declared(props.get(a) or {})
            out.append('%s=%s' % (inv.get(a, a), self.fmt_val(v, u)))
        return ', '.join(out)

    # ------------------------------------------------------------------ method
    def h_method(self, ctx, p):
        m = self.vmap('method')
        kv = self.kv(ctx, p)
        b = m.get('build', 'keys')
        allowed = set(self.member_keys())
        props = (ctx.cap_spec(m['cap']).get('input_schema') or {}).get('properties') or {} if m.get('cap') else {}
        for k in kv:
            if k not in allowed and not (b == 'keys' and m.get('passthrough', True) and k in props):
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (self.instrument_id, k),
                                 'settings of %s: %s.' % (self.instrument_id, ', '.join(self.member_keys()))))
        self.need(ctx, 'method')
        return getattr(self, 'm_' + b)(ctx, m, kv)

    def _si(self, ctx, kv, key):
        kind = self.KEYS[key][0]
        if key not in kv:
            return None
        if kind == 'count':
            return self._number(ctx, kv[key], key)
        return units.parse(kv[key], kind, what=key)

    def m_keys(self, ctx, m, kv):
        """Plain keys -> native arguments (Metrohm, HydrolEx); required native arguments left out take the documented example."""
        cap = m['cap']
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        req_native = (ctx.cap_spec(cap).get('input_schema') or {}).get('required') or []
        args = dict(m.get('args') or {})
        missing = [k for k in m.get('required') or [] if k not in kv]
        if missing:
            ctx.fail(invalid('MISSING_SETTING', 'method on %s needs %s.' % (self.instrument_id, ', '.join('%s=' % k for k in missing)),
                             'e.g. `chemlab run %s method %s`.' % (self.instrument_id, self.param('method_example', 'temp=90C hold=60min'))))
        used_default = []
        for k, text in kv.items():
            native = (m.get('keys') or {}).get(k, k)
            args[native] = self.parse_key(ctx, text, props.get(native) or {}, self.KEYS.get(k, (None,))[0], what=k)
        if m.get('defaults') == 'example':
            ex = self.example(cap)
            inv = {v: k for k, v in (m.get('keys') or {}).items()}
            for a in req_native:
                if a not in args and a in ex:
                    args[a] = ex[a]
                    used_default.append(inv.get(a, a))
        self.step_checks(ctx, m, args)
        self.check_args(ctx, cap, args, {v: k for k, v in (m.get('keys') or {}).items()})
        ctx.call(cap, args, 'set the method (%s)' % ', '.join('%s=%s' % (k, short(v, 24)) for k, v in args.items()))
        shown = self.method_text(m, args)
        return {'text': 'ok: method of %s set: %s%s\n' % (self.instrument_id, shown,
                                                         ('\n  defaults used: %s' % ', '.join(used_default)) if used_default else ''),
                'json': {'arguments': args, 'defaults_used': used_default}}

    def method_text(self, m, args):
        inv = {v: k for k, v in (m.get('keys') or {}).items()}
        props = ((self.lab.contract['capabilities'][m['cap']].get('input_schema') or {}).get('properties') or {})
        out = []
        for a, v in args.items():
            if a in (m.get('args') or {}):
                continue
            u = units.declared(props.get(a) or {})
            out.append('%s=%s' % (inv.get(a, a), self.fmt_val(v, u)))
        return ', '.join(out)

    def step_checks(self, ctx, m, args):
        """Member steps: whole °C (Metrohm), whole minutes (Metrohm duration)."""
        names = {v: k for k, v in (m.get('keys') or {}).items()}
        if m.get('arg'):
            names[m['arg']] = 'the temperature'
        for a, (q, word) in (m.get('steps') or {}).items():
            if a in args and finite(args[a]):
                v = args[a] - 273.15 if word == 'C' else args[a]
                n = v / q
                if abs(n - round(n)) > 1e-6:
                    ex = round(n) * q
                    ctx.fail(invalid('BAD_STEP', '%s takes %s in steps of %g %s.' % (self.instrument_id, names.get(a, a), q, {'C': '°C', 's': 's'}.get(word, word)),
                                     'round it, e.g. %s.' % ('%gC' % ex if word == 'C' else '%gmin' % (ex / 60) if q % 60 == 0 else '%gs' % ex)))

    def _steps(self, ctx, kv):
        """temp/ramp/hold or program -> [(T_si, seconds), ...] and the hold after the last step (or None)."""
        if 'program' in kv:
            if 'temp' in kv or 'ramp' in kv:
                ctx.fail(invalid('BAD_SETTING', 'give either program= or temp=/ramp=, not both.', 'see `chemlab help %s method`.' % self.instrument_id))
            return self.program(ctx, kv['program'], 'temperature'), self._si(ctx, kv, 'hold')
        if 'temp' not in kv:
            return None, self._si(ctx, kv, 'hold')
        t = self._si(ctx, kv, 'temp')
        return [(t, self._si(ctx, kv, 'ramp') or 0.0)], self._si(ctx, kv, 'hold')

    def m_knots(self, ctx, m, kv):
        """DigiPREP: a linear programme of (time, temperature) knots from the current block temperature, a hold at the last
        temperature and a timeout."""
        steps, hold = self._steps(ctx, kv)
        if steps is None:
            ctx.fail(invalid('MISSING_SETTING', 'method on %s needs temp= (with ramp= and hold=) or program=.' % self.instrument_id,
                             'e.g. `chemlab run %s method temp=95C ramp=10min hold=30min`.' % self.instrument_id))
        cap = m['cap']
        t0 = self.slot_si(ctx, self.slot('temp'))
        knots, t = [], 0.0
        if len(steps) == 1 and steps[0][1] <= 0:
            knots = [{'time_s': 0.0, 'temperature_k': round(steps[0][0], 6)}]
            t = 0.0
        else:
            if not finite(t0):
                ctx.fail(refused('NO_READING', '%s shows no block temperature to start the programme from.' % self.instrument_id,
                                 'check `chemlab run %s status`.' % self.instrument_id))
            knots = [{'time_s': 0.0, 'temperature_k': round(t0, 6)}]
            for T, dt in steps:
                if dt <= 0:
                    ctx.fail(invalid('BAD_PROGRAM', 'each step after the first needs a time > 0 on %s.' % self.instrument_id, 'e.g. 95C@10min.'))
                t += dt
                knots.append({'time_s': round(t, 6), 'temperature_k': round(T, 6)})
        hold = hold or 0.0
        timeout = self._si(ctx, kv, 'timeout')
        if timeout is None:
            lo, hi = self.arg_range(cap, m['timeout_arg'])
            timeout = min(hi or 172800.0, t + hold + float(self.param('timeout_margin_s', 7200)))
        args = {m['knots_arg']: knots, m['hold_arg']: hold, m['timeout_arg']: timeout}
        self.check_args(ctx, cap, args, {m['hold_arg']: 'hold', m['timeout_arg']: 'timeout'})
        ctx.call(cap, args, 'set the programme (%d knots, hold %s, timeout %s)' % (len(knots), units.fmt_s(hold), units.fmt_s(timeout)))
        prog = ', '.join('%s at %s' % (units.fmt(k['temperature_k'], 'temperature'), units.fmt_s(k['time_s'])) for k in knots)
        return {'text': 'ok: programme of %s: %s; hold %s at the last temperature; timeout %s\n' % (
            self.instrument_id, prog, units.fmt_s(hold), units.fmt_s(timeout)), 'json': {'arguments': args}}

    def m_editor(self, ctx, m, kv):
        """Multiwave: create (or replace) a user temperature method with its steps and select it; name= alone selects."""
        lib = ctx.v(self.slot('library')) or []
        names = {x.get('name'): x for x in lib if isinstance(x, dict)}
        name = kv.get('name')
        steps, hold = self._steps(ctx, kv)
        if steps is None:
            if not name or hold is not None or set(kv) - {'name'}:
                ctx.fail(invalid('MISSING_SETTING', 'method on %s needs temp= with ramp= and hold=, or program=; name=NAME alone selects '
                                 'a method of the library.' % self.instrument_id,
                                 'e.g. `chemlab run %s method name="MY DIGEST" temp=180C ramp=15min hold=15min`.' % self.instrument_id))
            if name not in names:
                ctx.fail(invalid('UNKNOWN_METHOD', '%s has no method %r.' % (self.instrument_id, name), 'methods: %s.' % ', '.join(names)))
            ctx.call(m['select_cap'], {'name': name}, 'select the method %r' % name)
            return {'text': 'ok: %s method %r selected (%s)\n' % (self.instrument_id, name, self.steps_text(names[name].get('steps'))),
                    'json': {'selected': name}}
        if 'program' not in kv:
            if hold is None:
                ctx.fail(invalid('MISSING_SETTING', 'method on %s needs hold= with temp=.' % self.instrument_id,
                                 'e.g. `chemlab run %s method temp=180C ramp=15min hold=15min`.' % self.instrument_id))
            steps = steps + [(steps[0][0], hold)]
        elif hold:
            steps = steps + [(steps[-1][0], hold)]
        name = name or 'chemlab %s' % ' '.join('%gC/%gmin' % (round(T - 273.15, 1), round(t / 60, 2)) for T, t in steps)
        name = name[:40]
        if names.get(name, {}).get('preinstalled'):
            ctx.fail(refused('PREINSTALLED', 'the method %r of %s is pre-installed and cannot be changed.' % (name, self.instrument_id),
                             'give another name= for your method.'))
        fan = int(self._si(ctx, kv, 'fan') or 1)
        cap_c, cap_s = m['create_cap'], m['step_cap']
        lo_p, hi_p = self.arg_range(cap_c, 'max_power_W')
        lo_t, hi_t = self.arg_range(cap_c, 'max_temperature_C')
        power = self._si(ctx, kv, 'power')
        create = {'name': name, 'program': 'temperature', 'max_power_W': float(hi_p) if power is None else self.arg_value(cap_c, 'max_power_W', power),
                  'max_temperature_C': float(hi_t)}
        self.check_args(ctx, cap_c, create, {'max_power_W': 'power'})
        rows = []
        for i, (T, t) in enumerate(steps):
            rows.append({'name': name, 'index': i, 'temperature_C': round(self.arg_value(cap_s, 'temperature_C', T), 6), 'time_s': float(t),
                         'step_type': 'time', 'fan': fan})
        n_max = (self.arg_range(cap_s, 'index')[1] or 9) + 1
        if len(rows) > n_max:
            ctx.fail(invalid('TOO_MANY_STEPS', '%s takes at most %d steps per method.' % (self.instrument_id, n_max), 'merge steps.'))
        for r in rows:
            self.check_args(ctx, cap_s, r, {'temperature_C': 'temp', 'time_s': 'step time', 'fan': 'fan'})
        if name in names:
            ctx.call(m['delete_cap'], {'name': name}, 'delete the old user method %r' % name)
        ctx.call(cap_c, create, 'create the method %r' % name)
        for i, r in enumerate(rows):
            if i > 0:
                ctx.call(m['add_cap'], {'name': name}, 'add step %d' % (i + 1))
            ctx.call(cap_s, r, 'step %d: %s for %s' % (i + 1, units.fmt(steps[i][0], 'temperature'), units.fmt_s(steps[i][1])))
        return {'text': 'ok: %s method %r created and selected: %s (power limit %s, fan %d)\n' % (
            self.instrument_id, name, self.steps_text(rows), units.fmt(units.from_declared(create['max_power_W'], 'W'), 'power'), fan),
            'json': {'name': name, 'steps': rows, 'max_power_W': create['max_power_W']}}

    def steps_text(self, steps):
        out = []
        for s in steps or []:
            if s.get('temperature_C') is not None:
                out.append('%g °C for %s' % (s['temperature_C'], units.fmt_s(s['time_s'])))
            elif s.get('power_W') is not None:
                out.append('%g W for %s' % (s['power_W'], units.fmt_s(s['time_s'])))
        return ', then '.join(out) or 'no steps'

    # ------------------------------------------------------------------ load
    def h_load(self, ctx, p):
        m = self.vmap('load')
        n = int(m.get('positions', 1))
        pos = positions(ctx, p.pos[0], n, self.instrument_id)
        self.need(ctx, 'load')
        return getattr(self, 'l_' + m.get('build', 'template'))(ctx, m, p, pos)

    def l_template(self, ctx, m, p, pos):
        """Multiwave: place vessels (rack.set_slot) and declare their contents (digestion.load_template)."""
        given = [k for k in ('material', 'method', 'mass', 'acid') if p.get(k) is not None]
        if given and len(given) < 4:
            ctx.fail(invalid('MISSING_OPTION', 'declaring vessel contents needs --material, --method, --mass and --acid together.',
                             'e.g. `chemlab run %s load 1 --material nist_srm_2711a --method epa_3051a_hno3 --mass 0.2g --acid 4mL`.' % self.instrument_id))
        occ = ctx.v(self.slot('occupied')) or []
        cap_t = m['template_cap']
        tmpl = None
        if given:
            tmpl = {'material_id': p.get('material'), 'method_id': p.get('method'),
                    'sample_mass_kg': self.arg_value(cap_t, 'sample_mass_kg', p.get('mass')),
                    'acid_volume_m3': self.arg_value(cap_t, 'acid_volume_m3', p.get('acid'))}
            self.check_args(ctx, cap_t, tmpl, {'sample_mass_kg': '--mass', 'acid_volume_m3': '--acid'})
        placed, declared = [], []
        for i in pos:
            if not (i - 1 < len(occ) and occ[i - 1]):
                ctx.call(m['slot_cap'], {'slot_index': i - 1, 'occupied': True}, 'place a vessel at position %d' % i)
                placed.append(i)
            if tmpl:
                ctx.call(cap_t, dict(tmpl, slot_index=i - 1), 'declare the contents of position %d' % i)
                declared.append(i)
        text = 'ok: %s vessels at position %s%s%s\n' % (self.instrument_id, ','.join(map(str, pos)),
                                                       (' (placed %s)' % ','.join(map(str, placed))) if placed else ' (already in the rack)',
                                                       ('; contents %s, %s, %s sample, %s acid' % (tmpl['material_id'], tmpl['method_id'],
                                                                                                 units.fmt(p.get('mass'), 'mass'), units.fmt(p.get('acid'), 'volume')))
                                                       if tmpl else '')
        return {'text': text, 'json': {'positions': pos, 'placed': placed, 'declared': declared}}

    def l_valves(self, ctx, m, p, pos):
        """HydrolEx: open the stopcocks of the loaded positions, close the others, lower the rack onto the heater."""
        extra = [k for k in ('material', 'method', 'mass', 'acid') if p.get(k) is not None]
        if extra:
            ctx.fail(invalid('NOT_RECORDED', '%s does not record vessel contents (the lab operator fills the beakers); leave out --%s.' % (
                self.instrument_id, ', --'.join(extra)), 'give only the positions: `chemlab run %s load 1,3`.' % self.instrument_id))
        n = int(m.get('positions', 6))
        for i in range(1, n + 1):
            ctx.call(m['valve_cap'], {'position': i, 'open': i in pos}, '%s the stopcock of position %d' % ('open' if i in pos else 'close', i))
        ctx.call(m['rack_cap'], {'raised': False}, 'lower the rack onto the heater')
        lift, ang = self.slot('rack'), self.param('valve_slot', 'valve_{n}.angle_rad')

        def ready(view):
            if not finite(view.get(lift)) or view.get(lift) > 1e-9:
                return False
            return all(abs((view.get(ang.format(n=i)) or 0.0) - (1.5707963267948966 if i in pos else 0.0)) < 1e-9 for i in range(1, n + 1))
        ok = self.settle(ctx, ready, 'the rack is down and the stopcocks are set', 10.0)
        return {'text': 'ok: %s rack lowered; stopcocks open at %s, closed elsewhere%s\n' % (
            self.instrument_id, ','.join(map(str, pos)), '' if ok else ' (still moving)'), 'json': {'positions': pos, 'settled': ok}}

    # ------------------------------------------------------------------ set-temp
    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        args = dict(m.get('args') or {})
        args[m['arg']] = round(self.arg_value(m['cap'], m['arg'], k), 10)
        self.step_checks(ctx, m, args)
        self.check_args(ctx, m['cap'], args, {m['arg']: 'temperature'})
        self.need(ctx, 'set-temp')
        before = self.slot_si(ctx, self.slot('set'))
        ctx.call(m['cap'], args, 'set the temperature to %s' % units.fmt(k, 'temperature'))
        return {'text': 'ok: %s temperature %s (was %s)\n' % (self.instrument_id, units.fmt(k, 'temperature'), units.fmt(before, 'temperature')),
                'json': {'set_point_k': k, 'was_k': before}}

    # ------------------------------------------------------------------ preheat
    def h_preheat(self, ctx, p):
        m = self.vmap('preheat')
        want = p.pos[0] == 'on'
        if want:
            self.need(ctx, 'preheat')
        mode = ctx.v(self.slot('mode'))
        target = m['on'] if want else m['off']
        if mode != target:
            ctx.call(m['cap'], {m['arg']: target}, 'heater to %s' % target)
        need_s = float(self.param('preheat_s', 600))
        text = 'ok: %s heater %s' % (self.instrument_id, target)
        js = {'mode': target}
        if want and p.get('wait'):
            ok = self.settle(ctx, lambda v: finite(v.get(self.slot('preheat'))) and v.get(self.slot('preheat')) >= need_s - 1e-6,
                             'preheated (%s)' % units.fmt_s(need_s), p.get('max') or 900.0)
            if not ctx.dry_run and not ok:
                ctx.fail(refused('NOT_PREHEATED', '%s is not preheated after waiting (%s of %s).' % (
                    self.instrument_id, units.fmt_s(ctx.v(self.slot('preheat')) or 0), units.fmt_s(need_s)),
                    'wait longer (`chemlab run %s preheat on --wait --max 30min`).' % self.instrument_id))
            text += '; preheated for %s' % units.fmt_s(ctx.v(self.slot('preheat')) or 0)
        elif want:
            text += '; preheating %s so far (needs %s: `chemlab run %s preheat on --wait`)' % (
                units.fmt_s(ctx.v(self.slot('preheat')) or 0), units.fmt_s(need_s), self.instrument_id)
        return {'text': text + '\n', 'json': js}

    # ------------------------------------------------------------------ start / stop / result
    def start_checks(self, ctx, p):
        ph = self.role(ctx, 'phase')
        busy = self.param('busy_phases', ['running'])
        ctx.check(ph not in busy, 'BUSY', '%s is already running (%s).' % (self.instrument_id, ph),
                  'wait for it (`chemlab run %s status`) or stop it first.' % self.instrument_id, label='not running')

    def start_job(self, ctx, r, cap, m):
        if m.get('job') == 'multiwave':
            return self.multiwave_job(ctx, m, cap)
        return super().start_job(ctx, r, cap, m)

    def multiwave_job(self, ctx, m, cap):
        """Multiwave run: running -> cooling (safety cooling) -> finished; the record of the run says completed or aborted."""
        from ..jobs import StateJob
        st_slot, rec_slot = self.slot('phase'), self.slot('records')
        n0 = len(ctx.v(rec_slot) or [])
        ad = self

        def state(c):
            v = c.v(st_slot)
            recs = c.v(rec_slot) or []
            if v in ('running', 'cooling'):
                return 'running', 'run %s (%s)' % (v, units.fmt(ad.slot_si(c, ad.slot('temp')), 'temperature')), {'run_state': v}
            if len(recs) > n0:
                res = (recs[-1] or {}).get('result')
                return ('succeeded' if res == 'completed' else 'failed'), 'run %s: %s' % (v, res), {'run_state': v, 'record': recs[-1]}
            return 'failed', 'run %s without a record' % v, {'run_state': v}

        def stop(c):
            c.call(ad.vmap('stop')['cap'], {}, 'stop the run')
            return 'stop sent; the run goes into its safety cooling'
        return StateJob(ctx.core, self, ctx.req, '%s method run' % self.instrument_id, state, stop=stop, max_s=float(m.get('max_s', 14400)),
                        poll_s=5.0)

    def h_stop(self, ctx, p):
        ph = self.role(ctx, 'phase')
        cooling = self.param('cooling_phases', [])
        if ph in cooling:
            ctx.fail(refused('ALREADY_COOLING', '%s has finished heating and is in its safety cooling; that cannot be stopped.' % self.instrument_id,
                             'wait for the job (`chemlab run %s status`).' % self.instrument_id))
        return super().h_stop(ctx, p)

    def result_args(self, ctx, p, m):
        args = dict(m.get('args') or {})
        slot = p.get('slot')
        if m.get('slot_arg'):
            occ = ctx.v(self.slot('occupied')) or []
            used = [i + 1 for i, x in enumerate(occ) if x]
            if slot is None:
                if len(used) == 1:
                    slot = str(used[0])
                else:
                    ctx.fail(invalid('MISSING_SLOT', '%s evaluates one vessel at a time: give --slot N.' % self.instrument_id,
                                     'occupied positions: %s.' % (','.join(map(str, used)) or 'none')))
            n = (self.arg_range(m['cap'], m['slot_arg'])[1] or 0) + 1
            args[m['slot_arg']] = positions(ctx, slot, n, self.instrument_id)[0] - 1
        elif slot is not None:
            ctx.fail(invalid('NO_SLOTS', '%s gives one record for the whole run; leave out --slot.' % self.instrument_id,
                             '`chemlab run %s result`.' % self.instrument_id))
        return args

    def h_result(self, ctx, p):
        out = super().h_result(ctx, p)
        res = (out.get('json') or {}).get('result') or {}
        if 'status' in res and 'method_conformant' in res:              # Multiwave evaluation: the verdict on top
            prog = res.get('program') or {}
            head = 'vessel %s: %s, %s; hold in window %s of %s; conformant %s' % (
                res.get('slot_index', 0) + 1 if isinstance(res.get('slot_index'), int) else '?', res.get('material_id'), res.get('status'),
                units.fmt_s(prog.get('hold_in_window_s') or 0), units.fmt_s(prog.get('required_hold_s') or 0), res.get('method_conformant'))
            out['text'] = out['text'].replace(':\n', ':\n  %s\n' % head, 1)
        return out

    def flag_word(self, role, v):
        if role == 'preheat' and finite(v):
            return units.fmt_s(v)
        if role in self.FLAG_WORDS and isinstance(v, bool):
            return self.FLAG_WORDS[role][0 if v else 1]
        if role == 'rack' and finite(v):
            return 'down' if v <= 1e-6 else 'up (%.0f mm)' % (v * 1000)
        return super().flag_word(role, v)

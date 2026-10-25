"""Reactor class ("reactor"): batch, flow and programme-driven reactors and reaction stations.

Members (configs/adapters/reactor.json): Anton Paar Monowave 400 (microwave), IKA ElectraSyn 2.0 (electrolysis), HepatoChem
PhotoRedOx Box (photochemistry), Parr 4591 (high-pressure stirred vessel), Syrris Asia (flow), ThalesNano H-Cube Pro (flow
hydrogenation), Radleys Mya 4 (four independent zones, --zone 1..4) and METTLER TOLEDO RC1mx (reaction calorimeter, Tr / Tj).

Verbs: set-temp T [--zone N] [--ramp T] [--jacket] [--controller C] [--wait], heat on|off, temp, wait-temp [--job], stir RATE|off,
flow F|off [--channel N], pressure P|on|off, light LEVEL|off, current I, voltage V, dose SUBSTANCE --rate R --mass M | dose stop,
program T@HOLD,... [--zone N], safety-limit T [--jacket], method KEY=VALUE ..., start [--zone N] [--name NAME], stop [--zone N]
[--cool-to T], result [--save-to F], wait, open / close PART (Monowave lid + lock, Parr lid + vent, with the safety checks), status.
power / ack / estop come from the shared layer.

Method keys (class dictionary, each member maps the ones it has): temp, ramp, hold, cool, stir, power, pressure, time, flow,
flows, h2, mode, current, voltage, voltage-limit, stop-by, charge, inject, collect.

Safety first: every verb checks the member's own conditions before anything is sent (lid closed and locked, sample loaded,
cooling source confirmed, set point under the safety limit, emergency programme off, heater off / cold / vented before the lid
opens), and a refused native call is explained with the state already read.
"""
from .. import units
from ..base import finite
from ..errors import invalid
from ..jobs import OperationJob
from ..verbs import Arg, Opt, Verb
from .reactor_common import SetpointAdapter

REFS = {   # manual page references per member (Chemistry-Sim/references/manuals/<slug>/manifest.json key sections; no manual text)
    'antonpaar_monowave400': 'Monowave 400 specification sheet pp.11-13 (IR sensor 30-300 °C, 30 bar, 1200 rpm); CEITEC SOP p.1 (experiment steps)',
    'ika_electrasyn2': 'ElectraSyn 2.0 operating instructions pp.6-7 (controls), pp.13-15 (setting up a reaction); brochure p.11 (technical data)',
    'hepatochem_photoredoxbox': 'PhotoRedOx Box brochure pp.1-2 (LED intensity control, temperature RT-60 °C, stir plate)',
    'parr_4591': 'Parr 399M micro reactor manual pp.7-9 (limits, assembly, vent / gas release valve, rupture disc), pp.10-12 (opening and '
                 'closing the vessel); 4848 controller communication notes (set points, stirrer speed)',
    'syrris_asiadiscovery': 'Asia systems manual pp.29-51 (flow experiments), pp.52-69 (manual module controls), pp.94-103 (priming, '
                            'pressure), pp.105-116 (solvent limits); Asia syringe pump manual pp.12-17',
    'thalesnano_hcube': 'H-Cube Pro brochure pp.2-4 (flow 0.3-3 mL/min, 10-150 °C, up to 100 bar, water reservoir, CatCart)',
    'radleys_mya4': 'Mya 4 operating manual pp.13-20 (coolant supply), pp.89-105 (zone view: manual mode, profile mode, zone limits)',
    'mt_rc1mxhfcal': 'RC1mx user manual pp.19-26 (safety settings Tsafe / Tr / Tj / Rmax, experiments, Tj / Tr changes); RC1e thermostat '
                     'instructions pp.15-18 (Tj / Tr control modes), pp.19-30 (safety system, emergency programmes)',
}

KEYS = {
    'temp': ('temperature', 'reaction / target temperature, e.g. 80C'), 'ramp': ('duration', 'time to reach the temperature'),
    'hold': ('duration', 'hold time'), 'cool': ('temperature', 'cool to this temperature at the end'),
    'stir': ('rotation', 'stirring speed, e.g. 600rpm'), 'power': ('power', 'power limit, e.g. 850W'),
    'pressure': ('pressure', 'pressure limit or set point (the member says which: absolute or gauge)'),
    'time': ('duration', 'run time'), 'flow': ('flow', 'liquid flow, e.g. 1mL/min'),
    'flows': ('flow', 'one flow per pump, comma separated (0.5mL/min,0.5mL/min)'), 'h2': ('flow', 'hydrogen reference flow'),
    'mode': (None, 'constant_current or constant_cell_voltage'), 'current': ('current', 'electrolysis current, e.g. 10mA'),
    'voltage': ('voltage', 'cell voltage, e.g. 3V'), 'voltage-limit': ('voltage', 'compliance voltage (10V or 30V)'),
    'stop-by': (None, 'time or charge'), 'charge': (None, 'charge target in C'),
    'inject': ('duration', 'injection times per loop, comma separated'), 'collect': (None, 'collection windows (JSON)'),
}


class ReactorAdapter(SetpointAdapter):
    CLASS = 'reactor'
    TITLE = 'reactors and reaction stations'
    ABOUT = ('Run a reaction: set and wait for the reaction temperature (per zone, per jacket), stir, set flows and back-pressure on '
             'flow reactors, light / current / dosing as the energy or reagent input, a method or temperature programme, and start / '
             'stop / result with job ids. Safety conditions (lid, pressure, temperature, limits, emergency) are checked before '
             'anything is sent.')
    STANDARDS = ('MTP / VDI 2658 and ISA-88 (procedure / phase states: start, stop, hold); OPC UA LADS (functional units, controller '
                 'set points); SiLA 2 TemperatureController and vendor features (e.g. METTLER TOLEDO iControl: Tr / Tj, dosing)')
    ALWAYS = ('status', 'wait')
    UNIT_KINDS = ('temperature', 'temperature_delta', 'duration', 'rotation', 'pressure', 'flow', 'mass_flow', 'mass', 'current',
                  'voltage', 'fraction', 'power')
    PATH_GROUPS = (('start', 'stop', 'result'),)
    KEYS = KEYS
    VIEW = (('temp', 'reaction', 'temperature'), ('temp2', 'liquid', 'temperature'), ('set', 'set point', 'temperature'),
            ('jacket', 'jacket', 'temperature'), ('heat', 'temperature control', 'bool'), ('stir', 'stirring', 'rotation'),
            ('stir_on', 'stirrer', 'bool'), ('pressure', 'pressure', 'pressure'), ('flow', 'flow', 'flow'), ('light', 'light', 'fraction'),
            ('current', 'current', 'current'), ('voltage', 'cell voltage', 'voltage'), ('dosing', 'dosing', 'bool'),
            ('dosed', 'dosed', 'mass'), ('vent', 'vent', 'bool'), ('record', 'experiment', 'text'),
            ('mode', 'control', 'text'), ('lid', 'lid', 'text'), ('phase', 'run', 'text'), ('program', 'program', 'text'),
            ('fault', 'fault', 'text'))
    CODES = {
        'OUTSIDE_SAFETY_LIMITS': ('ABOVE_SAFETY_LIMIT', 'the set point is outside the safety limits of {inst} (Tr / Tj min and max).',
                                  'read them (`chemlab run {inst} status`); raise the limit first (`chemlab run {inst} safety-limit T`) '
                                  'if the reaction allows it, or choose a set point inside them.'),
        'OUTSIDE_STIRRER_RANGE': ('OUT_OF_RANGE', 'the stirrer speed is outside what {inst} allows (0, or 30 rpm up to its Rmax).',
                                  'give 0 (off) or a speed from 30 rpm to the Rmax shown by `chemlab run {inst} status`.'),
        'EMERGENCY_ACTIVE': ('EMERGENCY_ACTIVE', 'the emergency programme of {inst} is active; it takes no new settings.',
                             'remove the cause, then reset it (`chemlab call {inst} emergency.reset`).'),
        'TEMPLATE_RANGE_UNAVAILABLE': ('OUTSIDE_MODEL_RANGE', '{inst} has no validated model data for that temperature with the loaded '
                                       'solvent / carrier.', 'choose a lower temperature (see `chemlab describe {inst}`).'),
        'LID_OPEN': ('LID_OPEN', 'the lid of {inst} is open or not locked.', 'close it: `chemlab run {inst} close lid`.'),
        'NO_VIAL': ('NO_SAMPLE', 'no sealed vial is in {inst}.', 'ask the lab operator to load the sample.'),
        'NO_COOLING_AIR': ('NO_COOLING_AIR', 'the cooling air of {inst} is not connected or not at pressure.', 'ask the lab operator.'),
        'BUSY': ('BUSY', '{inst} is running and takes no new settings now.', 'wait for the run (`chemlab run {inst} wait`) or stop it.'),
    }

    def build_verbs(self):
        REF = REFS.get(self.asset)
        out = self.temp_verbs(REF, 'start or stop temperature control (heater, thermostat, zone)')
        out['stir'] = self.knob_verb('stir', 'stir at a speed, or stop stirring', 'rotation', 'speed, e.g. 300rpm, or off',
                                     example='chemlab run INST stir 300rpm', reference=REF,
                                     preconditions=['power on', 'speed within the stirrer range', 'the member\'s own conditions (lid closed, ...)'],
                                     errors=['OUT_OF_RANGE (usage)', 'POWER_OFF, LID_OPEN (refused)'])
        out['flow'] = self.knob_verb('flow', 'pump at a flow (flow reactors), or stop the pump', 'flow', 'flow, e.g. 0.5mL/min, or off',
                                     example='chemlab run INST flow 0.5mL/min --channel 1', reference=REF)
        out['pressure'] = self.knob_verb('pressure', 'set the back-pressure / reactor pressure, or switch its control on / off', 'pressure',
                                         'pressure, e.g. 5bar, or on / off', example='chemlab run INST pressure 5bar', reference=REF,
                                         details='the value is passed in the sense of the instrument\'s own argument: absolute for '
                                                 'the H-Cube (pressure_abs_pa), back-pressure regulator setting for the Asia.')
        out['light'] = self.knob_verb('light', 'set the light (LED) intensity, or switch it off', 'fraction', 'intensity, e.g. 80%, or off',
                                      example='chemlab run INST light 80%', reference=REF)
        out['current'] = self.knob_verb('current', 'constant-current electrolysis: set the current', 'current', 'current, e.g. 10mA',
                                        on_off=False, example='chemlab run INST current 10mA', reference=REF)
        out['voltage'] = self.knob_verb('voltage', 'constant-voltage electrolysis: set the cell voltage', 'voltage', 'voltage, e.g. 3V',
                                        on_off=False, example='chemlab run INST voltage 3V', reference=REF)
        out['dose'] = Verb('dose', 'dose a reagent at a rate (a job), or stop dosing', self.h_dose,
                           positionals=[Arg('SUBSTANCE|stop', 'the substance id to dose, or stop')],
                           options={'rate': Opt('dosing rate, e.g. 9g/min', kind='mass_flow', metavar='RATE'),
                                    'mass': Opt('total mass to dose, e.g. 102g', kind='mass', metavar='MASS'),
                                    'feed-temp': Opt('feed temperature (default: the instrument\'s)', kind='temperature', metavar='TEMP')},
                           long_action=True, preconditions=['power on', 'no emergency programme', 'no dosing running', 'rate and mass in range'],
                           errors=['OUT_OF_RANGE, MISSING_OPTION (usage)', 'DOSING_ACTIVE, EMERGENCY_ACTIVE (refused)'],
                           example='chemlab run INST dose acetic_anhydride --rate 9g/min --mass 102g', reference=REF)
        out['program'] = Verb('program', 'load a temperature programme: TEMP@HOLD,TEMP@HOLD,... (start it with `start`)', self.h_program,
                              positionals=[Arg('SEGMENTS', 'e.g. 80C@30min,25C@10min (approach each temperature, then hold)')],
                              options=dict(self.target_opts(verb='program')), preconditions=['power on', 'no programme running in that zone',
                                                                               'each temperature and hold in range'],
                              errors=['BAD_SEGMENT, OUT_OF_RANGE (usage)', 'PROGRAM_RUNNING (refused)'],
                              example='chemlab run INST program 80C@30min,25C@0s --zone 1', reference=REF)
        out['safety-limit'] = Verb('safety-limit', 'set the maximum reaction temperature (or with --jacket the jacket maximum)', self.h_limit,
                                   positionals=[Arg('TEMP', 'the new maximum, e.g. 140C', kind='temperature')],
                                   options={'jacket': Opt('set the jacket maximum (Tj max) instead of Tr max', kind='flag')},
                                   preconditions=['not below the current set point', 'no emergency programme'],
                                   errors=['OUT_OF_RANGE (usage)', 'BELOW_SET_POINT (refused)'], example='chemlab run INST safety-limit 140C',
                                   reference=REF)
        out['method'] = self.method_verb(REF)
        out.update(self.run_verbs(REF))
        out.update(self.part_verbs())
        return out

    # ------------------------------------------------------------------ dose (reagent addition, a job)
    def h_dose(self, ctx, p):
        inst = self.instrument_id
        m = self.vmap('dose')
        what = p.pos[0]
        if what.lower() in ('stop', 'off'):
            if not self.raw(ctx, m['active']):
                return {'text': 'ok: %s is not dosing\n' % inst, 'json': {'changed': False}}
            ctx.call(m['stop_cap'], {}, 'stop dosing')
            return {'text': 'ok: %s dosing stopped (dosed %s)\n' % (inst, units.fmt(self.si(ctx, 'dosed'), 'mass')),
                    'json': {'changed': True, 'dosed_kg': self.si(ctx, 'dosed')}}
        for o in ('rate', 'mass'):
            if p.get(o) is None:
                ctx.fail(invalid('MISSING_OPTION', 'dose needs --%s.' % o, 'e.g. `chemlab run %s dose %s --rate 9g/min --mass 100g`.' % (inst, what)))
        cap = m['cap']
        self.check_value(ctx, {'cap': cap, 'arg': m['rate_arg']}, p.get('rate'), 'mass_flow', 'rate', {'max_slot': m.get('max_rate_slot')})
        self.check_value(ctx, {'cap': cap, 'arg': m['mass_arg']}, p.get('mass'), 'mass', 'mass')
        if p.get('feed-temp') is not None:
            self.check_value(ctx, {'cap': cap, 'arg': m['feed_arg']}, p.get('feed-temp'), 'temperature', 'feed temperature')
        self.need_power(ctx)
        self.need(ctx, 'dose')
        args = {m['substance_arg']: what, m['rate_arg']: self.to_arg(cap, m['rate_arg'], p.get('rate')),
                m['mass_arg']: self.to_arg(cap, m['mass_arg'], p.get('mass'))}
        if m.get('feed_arg'):
            args[m['feed_arg']] = self.to_arg(cap, m['feed_arg'], p.get('feed-temp')) if p.get('feed-temp') is not None else None
        r = ctx.call(cap, args, 'dose %s at %s, %s in total' % (what, units.fmt(p.get('rate'), 'mass_flow'), units.fmt(p.get('mass'), 'mass')))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        if r.get('operation_id'):
            job = OperationJob(ctx.core, self, ctx.req, 'dose %s on %s' % (what, inst), cap, r['operation_id'])
            return {'text': 'started %s: dose %s %s at %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
                job.handle, units.fmt(p.get('mass'), 'mass'), what, units.fmt(p.get('rate'), 'mass_flow'), job.handle, job.handle, job.handle),
                'json': {'job': job.handle, 'kind': job.kind}}
        return {'text': 'ok: %s dosing %s\n' % (inst, what), 'json': {'result': r.get('result')}}

    # ------------------------------------------------------------------ programme (zone temperature profile)
    def h_program(self, ctx, p):
        inst = self.instrument_id
        m = self.vmap('program')
        tg = self.targets(ctx, p)
        cap = m['cap']
        item = ((self.cap_schema(cap, m['arg']).get('items') or {}).get('properties') or {})
        tk, hk = m['temp_key'], m['time_key']
        lo_n, hi_n = (self.cap_schema(cap, m['arg']).get('minItems'), self.cap_schema(cap, m['arg']).get('maxItems'))
        segs = []
        for part in [x for x in p.pos[0].split(',') if x.strip()]:
            t, at, h = part.partition('@')
            if not at:
                ctx.fail(invalid('BAD_SEGMENT', '%r is not TEMP@HOLD.' % part, 'write e.g. 80C@30min,25C@0s.'))
            k = units.parse(t.strip(), 'temperature', what='temperature')
            s = units.parse(h.strip(), 'duration', what='hold')
            for v, key, kind, what in ((k, tk, 'temperature', 'temperature'), (s, hk, 'duration', 'hold')):
                sch = item.get(key) or {}
                u = units.declared(sch)
                lo = units.from_declared(sch['minimum'], u) if sch.get('minimum') is not None and u else sch.get('minimum')
                hi = units.from_declared(sch['maximum'], u) if sch.get('maximum') is not None and u else sch.get('maximum')
                if (lo is not None and v < lo - 1e-9) or (hi is not None and v > hi + 1e-9):
                    ctx.fail(invalid('OUT_OF_RANGE', '%s %s in %r is outside %s to %s.' % (what, units.fmt(v, kind), part, units.fmt(lo, kind),
                                                                                        units.fmt(hi, kind)),
                                     'give a %s from %s to %s.' % (what, units.fmt(lo, kind), units.fmt(hi, kind))))
            segs.append({tk: units.to_declared(k, units.declared(item.get(tk) or {})) if units.declared(item.get(tk) or {}) else k,
                         hk: units.to_declared(s, units.declared(item.get(hk) or {})) if units.declared(item.get(hk) or {}) else s})
        if (lo_n and len(segs) < lo_n) or (hi_n and len(segs) > hi_n):
            ctx.fail(invalid('BAD_SEGMENT', 'a programme of %s has %s to %s segments, not %d.' % (inst, lo_n, hi_n, len(segs)),
                             'give between %s and %s TEMP@HOLD segments.' % (lo_n, hi_n)))
        self.need_power(ctx)
        self.need(ctx, 'program', tg)
        args = {m['arg']: [{k: round(v, 10) for k, v in s.items()} for s in segs]}
        for kk, (lab, nat) in tg.items():
            t = (self.map.get('targets') or {}).get(kk) or {}
            if t.get('arg'):
                args[t['arg']] = nat
        where = ''.join(' (%s %s)' % (kk, lab) for kk, (lab, _) in tg.items())
        ctx.call(cap, args, 'load the programme%s: %s' % (where, ', '.join('%s for %s' % (self.c(units.from_declared(s[tk], units.declared(item.get(tk) or {})) if units.declared(item.get(tk) or {}) else s[tk]),
                                                                                         units.fmt_s(s[hk])) for s in segs)))
        return {'text': 'ok: programme of %s%s loaded (%d segments); run it with `chemlab run %s start%s`\n' % (
            inst, where, len(segs), inst, ''.join(' --%s %s' % (kk, lab) for kk, (lab, _) in tg.items())),
            'json': {'segments': args[m['arg']], 'target': {kk: lab for kk, (lab, _) in tg.items()}}}

    # ------------------------------------------------------------------ safety limit
    def h_limit(self, ctx, p):
        inst = self.instrument_id
        m = self.vmap('safety-limit')
        if p.get('jacket'):
            if not m.get('jacket'):
                ctx.fail(invalid('NO_JACKET', '%s has no separate jacket limit.' % inst, 'leave out --jacket.'))
            m = dict(m, **m['jacket'])
        k = p.pos[0]
        steps = [dict(s) for s in m['set']]
        step = self.first_arg_step(steps)
        step['arg'] = m.get('arg', step['arg'])
        step['keep'] = {a: s for a, s in (step.get('keep') or {}).items() if a != step['arg']}
        self.check_value(ctx, step, k, 'temperature', 'safety limit')
        self.need_power(ctx)
        self.need(ctx, 'safety-limit')
        sk = self.si(ctx, m.get('set_role', 'set'))
        if finite(sk):
            ctx.check(k >= sk - 1e-9, 'BELOW_SET_POINT', 'a limit of %s is below the current set point %s of %s.' % (self.c(k), self.c(sk), inst),
                      'lower the set point first, or choose a limit above it.', label='limit above the set point')
        for role, word in ((m.get('above_role'), 'Tsafe'),):
            v = self.si(ctx, role) if role else None
            if finite(v):
                ctx.check(k >= v - 1e-9, 'BELOW_TSAFE', 'a limit of %s is below the safe temperature %s (%s) of %s.' % (self.c(k), word, self.c(v), inst),
                          'choose a limit at or above %s.' % self.c(v), label='limit above %s' % word)
        before = self.si(ctx, m.get('limit_role', 'limit'))
        self.run_steps(ctx, steps, k, {}, 'set the safety limit to %s' % self.c(k))
        return {'text': 'ok: %s limit of %s %s (was %s)\n' % ('jacket' if p.get('jacket') else 'reaction temperature', inst, self.c(k), self.c(before)),
                'json': {'limit_k': k, 'was_k': before}}

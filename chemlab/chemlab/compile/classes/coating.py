"""Coating and thin-film deposition class ("coating"): spin, dip, doctor-blade and slot-die coaters (Ossila), sputter / carbon
coater (Quorum Q150T ES), CVD tube furnace (LABOAO CVD-LTF12P50), ALD reactor (Arradiance GEMStar XT) and materials inkjet
printer (FUJIFILM Dimatix DMP-2850).

Verbs: method KEY=VALUE ..., move POS [--speed V], home, gap G [--left L --right R], set-temp T|off [--zone Z], vacuum
pump|vent|off [--pressure P], prime VOLUME [--flow F], start [--mode M] (job), stop, result [--save-to F], status.  power,
open/close (lid, door, furnace, flange), estop, ack and wait come from the shared layer.  Members: configs/adapters/coating.json.

Method dictionary (keys with units; a member's own argument names also pass through):

    speed=V time=T               spin coater: one step (3000rpm, 30s); doctor blade / slot die: coating speed (10mm/s)
    program=V@T,V@T,...          spin coater: several steps (500rpm@5s,3000rpm@30s)
    distance=L                   doctor blade / slot die: coating length (200mm)
    flow=Q delay=T               slot die: ink flow (1.2mL/min), stage delay
    length=L dip-speed=V withdraw=V [withdraw-final=V] dwell=T dry=T dry-gap=L cycles=N     dip coater
    current=I pressure=P time=T thickness=L tooling=X rotation=RPM|off preclean=T lift=L carbon-current=I carbon-ramp=X   sputter coater
    temp=T rate=R hold=T flow=Q1,Q2,Q3 pressure=P purge=T mode=fixed|program pump=auto|manual valve=X curve=N   CVD furnace (sccm = mL/min)
    cycles=N temp=T door-temp=T manifold-temp=T pulse=T,T exposure=T,T purge=T,T carrier=Q rf=P        ALD
    spacing=L height=L voltage=V frequency=F nozzles=0,5,11 pattern=010/111/010 pattern-name=NAME at=X,Y   inkjet printer

Settings a member requires that the agent leaves out take the member's documented example method (listed by `help INST method`);
the keys each member insists on are named in the error.

Vendor-neutral references: SEMI E30 (GEM) / E5 style process equipment (recipe select / start / stop), ASTM D823 (producing films of
uniform thickness), ISO 2808 (film thickness); there is no lab command standard for coaters.
"""
from .. import units
from ..base import finite, short
from ..errors import invalid, refused
from ..jobs import OperationJob
from ..verbs import Arg, Opt, Verb, save_to_opt, target_opt
from .process_common import ProcessAdapter

REF = ('manuals: Ossila spin / dip / doctor-blade / slot-die coater manuals: programs, manual mode, gap, hotplate, pump; Quorum Q150T manual: '
       'profiles, pump / coat / vent; LABOAO CVD-LTF12P50 datasheet: controller, vacuum, gas; Arradiance GEMStar operation notes: recipe, '
       'vacuum ready, run; Dimatix DMP-2850 guides: pattern, waveform, platen and cartridge temperature, print')
STEP_WORDS = {'m': ('µm', 1e6), 'C': ('°C', 1), 's': ('s', 1)}


class CoatingAdapter(ProcessAdapter):
    CLASS = 'coating'
    TITLE = 'coating and thin-film deposition'
    ABOUT = ('Instruments that coat or deposit a film: set the method (speeds, gap, current, recipe), position the stage or head, set the '
             'gap, heat the bed or furnace, pump or vent the chamber, start the coating (a job), stop it, read the process record.')
    STANDARDS = ('SEMI E30 (GEM) / SEMI E5 process-equipment style recipe select / start / stop for CVD / ALD / sputter; ASTM D823 (film '
                 'application); ISO 2808 (film thickness)')
    ALWAYS = ('status',)
    UNIT_KINDS = ('rotation', 'duration', 'length', 'speed', 'flow', 'temperature', 'pressure', 'current', 'volume')
    PATH_GROUPS = (('start', 'stop', 'result'),)
    KEYS = {'speed': (None, 'spin speed (3000rpm) or coating speed (10mm/s)'), 'time': ('duration', 'step / deposition time'),
            'program': (None, 'spin steps SPEED@TIME,...'), 'distance': ('length', 'coating length'), 'flow': (None, 'ink or gas flow'),
            'delay': ('duration', 'stage delay'), 'length': ('length', 'dip coating length'), 'dip-speed': ('speed', 'immersion speed'),
            'withdraw': ('speed', 'withdrawal speed'), 'withdraw-final': ('speed', 'final withdrawal speed'), 'dwell': ('duration', 'dwell time'),
            'dry': ('duration', 'drying time'), 'dry-gap': ('length', 'drying gap'), 'cycles': ('count', 'number of cycles'),
            'current': ('current', 'sputter current'), 'pressure': ('pressure', 'working / process pressure'), 'thickness': ('length', 'end at this thickness (FTM)'),
            'tooling': ('count', 'FTM tooling factor'), 'rotation': (None, 'stage rotation rpm or off'), 'preclean': ('duration', 'target pre-clean'),
            'lift': ('length', 'stage height'), 'carbon-current': ('current', 'carbon rod current'), 'carbon-ramp': ('count', 'carbon current ramp (A/s)'),
            'temp': ('temperature', 'process temperature'), 'rate': ('heating_rate', 'heating rate (5K/min)'), 'hold': ('duration', 'hold time'),
            'purge': ('duration', 'purge time(s)'), 'mode': (None, 'controller mode'), 'pump': (None, 'pump mode'), 'valve': ('count', 'manual valve fraction'),
            'curve': ('count', 'controller curve 0-3'), 'door-temp': ('temperature', 'door zone'), 'manifold-temp': ('temperature', 'manifold zone'),
            'pulse': ('duration', 'precursor pulses T,T'), 'exposure': ('duration', 'exposures T,T'), 'carrier': ('flow', 'carrier gas flow'),
            'rf': ('power', 'plasma RF power'), 'precursor-flow': ('count', 'precursor flows (mol/s)'), 'spacing': ('length', 'drop spacing'),
            'height': ('length', 'print height (head gap)'), 'voltage': ('voltage', 'firing voltage'), 'frequency': ('frequency', 'jetting frequency'),
            'nozzles': ('count', 'nozzle numbers 0,5,11'), 'rise': ('duration', 'waveform rise'), 'fall': ('duration', 'waveform fall'),
            'clean-every': ('count', 'clean every N drops'), 'clean-time': ('duration', 'cleaning time'),
            'pattern': (None, 'pattern rows 010/111/010'), 'pattern-name': (None, 'pattern name'), 'at': (None, 'pattern origin X,Y')}
    ALIASES = {'temperature': 'temp', 'rpm': 'speed', 'gap': 'height'}
    SUMMARY_FLAGS = (('method', 'method'), ('sample', 'substrate'), ('position', 'stage'), ('gap', 'gap'), ('lid', 'lid'),
                     ('pressure', 'pressure'), ('speed', 'spin'), ('homed', 'homed'), ('heating', 'heater'), ('vacuum', 'pump'))
    FLAG_WORDS = {'method': ('set', 'not set'), 'sample': ('loaded', 'none'), 'homed': ('yes', 'no'), 'heating': ('on', 'off'),
                  'vacuum': ('on', 'off')}
    CODES = {
        'NOT_PRIMED': ('NOT_PRIMED', 'the die of {inst} is not primed: the ink line and the meniscus under the die are not filled.',
                       'prime it: `chemlab run {inst} prime 100uL`, then start.'),
        'MODEL_OUT_OF_RANGE': ('GAP_FILM_MISMATCH', 'the gap of {inst} does not fit the wet film of this method (gap / film thickness '
                               'outside the coating window, or no gap).', 'set a gap of 1-10 x the wet film (flow / (width x speed)), e.g. `gap 100um`.'),
        'VENT_BEFORE_OPEN': ('UNDER_VACUUM', 'the chamber of {inst} is under vacuum.', 'vent it first: `chemlab run {inst} vacuum vent`, wait ~30 s.'),
        'OPEN_TO_ADJUST_STAGE': ('CHAMBER_CLOSED', 'the stage height of {inst} is set by hand with the chamber open.', 'leave lift= out or open the chamber.'),
        'FTM_OPTION_REQUIRED': ('NO_FTM', 'a thickness end point needs the film-thickness monitor.', 'drop thickness= or use time=.'),
        'PROGRAM_MODE': ('PROGRAM_MODE', 'the controller of {inst} runs a programme (curve); a fixed set value works only in fixed mode.',
                         'switch to fixed mode with a method: `chemlab run {inst} method mode=fixed temp=600C hold=30min`, then set-temp.'),
        'ZERO_RAMP_TRANSITION': ('BAD_PROGRAM', 'a programme step of {inst} changes the temperature with a zero heating rate.', 'give rate= > 0.'),
        'FIXED_ONE_SEGMENT': ('BAD_PROGRAM', 'fixed mode takes one segment.', 'use mode=program for several.'),
        'INERT_PURGE_REQUIRED': ('NO_INERT_FLOW', 'the method of {inst} needs a flow of N2 or Ar for purging.', 'give the inert channel a flow, e.g. flow=200,0,0.'),
        'MFC_FULL_SCALE': ('OUT_OF_RANGE', 'a gas flow is above the full scale of its mass-flow controller.', 'lower flow=.'),
        'COLD_VENTED_ACCESS_REQUIRED': ('NOT_COLD_VENTED', '{inst} opens only cold (below 60 °C), vented and switched off.', 'vent, let it cool, switch off.'),
        'REFERENCE_TEMPERATURE_RANGE': ('TEMP_NOT_IN_RECIPE', 'the reactor temperature does not match the deposition chemistry of {inst} (within 1 K).',
                                        'use the recipe temperature of the loaded chemistry (`chemlab call {inst} template.read ...`).'),
        'PLASMA_POWER_REQUIRED': ('PLASMA_MISMATCH', 'rf= must be > 0 exactly for a plasma process.', 'set rf=0W for thermal ALD.'),
        'EMO_LATCHED': ('EMO_LATCHED', 'the emergency off of {inst} is latched.', 'reset it with power off: `chemlab call {inst} safety.reset`.'),
        'SOURCE_EMPTY': ('SOURCE_EMPTY', 'a source of {inst} is empty.', 'ask the lab operator to refill it.'),
        'SYRINGE_EMPTY': ('INK_EMPTY', 'the syringe of {inst} has not enough ink.', 'ask the lab operator to refill it.'),
        'INK_EMPTY': ('INK_EMPTY', 'the cartridge of {inst} is empty.', 'ask the lab operator to refill it.'),
        'INVALID_GEOMETRY': ('BAD_GEOMETRY', 'the move or coating length does not fit the stage travel / substrate of {inst}.', 'shorten distance= or move the stage.'),
        'PATTERN_OUT_OF_BOUNDS': ('PATTERN_OUT_OF_BOUNDS', 'the pattern does not fit on the substrate.', 'move at= or shrink the pattern.'),
        'INVALID_PATTERN': ('BAD_PATTERN', 'pattern rows must be 0/1 strings of equal length (up to 16) with at least one 1.', 'e.g. pattern=010/111/010.'),
        'UNCALIBRATED_WAVEFORM_RANGE': ('WAVEFORM_RANGE', 'the waveform is too far from the ink calibration.', 'keep voltage and pulse near the ink reference.'),
        'BUSY': ('BUSY', '{inst} is busy (a move or a run is going on).', 'wait for it (`chemlab run {inst} status`).'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        return {
            'method': Verb('method', 'set the coating / deposition method: KEY=VALUE ...', self.h_method, variadic=('KEY=VALUE', 'a method setting'),
                           preconditions=['values within the contract ranges', 'not running', 'the member\'s own steps (whole µm)'],
                           errors=['MISSING_SETTING, UNKNOWN_SETTING, OUT_OF_RANGE (usage)'], details='the keys depend on the instrument; they are listed below with their units.'),
            'move': Verb('move', 'move the stage / print head to a position (waits until it is there)', self.h_move,
                         positionals=[Arg('POS', 'position, e.g. 100mm (stage) or 50mm,40mm,2mm (x,y,z head)')],
                         options={'speed': Opt('stage speed (default: the member\'s)', kind='speed', metavar='SPEED'),
                                  'job': Opt('return a job id at once instead of waiting', kind='flag')},
                         preconditions=['power on', 'stage homed', 'not running'], errors=['OUT_OF_RANGE (usage)', 'NOT_HOMED, POWER_OFF (refused)'],
                         example='chemlab run INST move 100mm --speed 10mm/s'),
            'home': Verb('home', 'home the stage (waits until it is home)', self.h_home,
                         options={'job': Opt('return a job id at once instead of waiting', kind='flag')},
                         preconditions=['power on', 'not running'], errors=['NOT_READY (refused)'], example='chemlab run INST home'),
            'gap': Verb('gap', 'set the blade / die gap or the substrate clamp (waits until it is set)', self.h_gap,
                        positionals=[Arg('GAP', 'gap, e.g. 100um or 1mm', kind='length', required=False)],
                        options={'left': Opt('left side gap (blade / die)', kind='length', metavar='GAP'),
                                 'right': Opt('right side gap (blade / die)', kind='length', metavar='GAP')},
                        preconditions=['within the range', 'whole µm (doctor blade)', 'not running'], errors=['OUT_OF_RANGE, BAD_STEP (usage)'],
                        example='chemlab run INST gap 100um'),
            'set-temp': Verb('set-temp', 'heat the bed / platen / furnace to a temperature (or off)', self.h_set_temp,
                             positionals=[Arg('TEMP|off', 'temperature, e.g. 60C, or off')], options={'zone': target_opt('zone')},
                             preconditions=['within the range', 'power on'], errors=['OUT_OF_RANGE (usage)', 'POWER_OFF, PROGRAM_MODE (refused)'],
                             example='chemlab run INST set-temp 60C'),
            'vacuum': Verb('vacuum', 'pump the chamber down, vent it, or switch the pump off', self.h_vacuum,
                           positionals=[Arg('pump|vent|off', 'pump, vent or off', choices=['pump', 'vent', 'off'])],
                           options={'pressure': Opt('pressure set point (CVD furnace, e.g. 30Pa)', kind='pressure', metavar='P')},
                           preconditions=['power on', 'chamber / door / flange closed (pump)'], errors=['CHAMBER_OPEN, POWER_OFF (refused)'],
                           example='chemlab run INST vacuum pump'),
            'prime': Verb('prime', 'dispense ink to prime the die (waits until done)', self.h_prime,
                          positionals=[Arg('VOLUME', 'volume, e.g. 100uL', kind='volume')],
                          options={'flow': Opt('pump flow (default: the member\'s)', kind='flow', metavar='FLOW')},
                          preconditions=['power on', 'stage homed', 'a gap set', 'not running'], errors=['OUT_OF_RANGE (usage)', 'NOT_READY (refused)'],
                          example='chemlab run INST prime 100uL'),
            'start': Verb('start', 'start the coating / deposition (returns job-N)', self.h_start, long_action=True,
                          options={'mode': Opt('run mode where the instrument has several (CVD: cvd or purge)', metavar='MODE')},
                          preconditions=['the member\'s run conditions (method, substrate, lid / chamber closed, homed, gap set)'],
                          errors=['LID_OPEN, CHAMBER_OPEN, NOT_HOMED, NO_SUBSTRATE, NOT_PRIMED, ... (refused)'],
                          example='chemlab run INST start'),
            'stop': Verb('stop', 'stop the running coating / deposition', self.h_stop, preconditions=['running'], errors=['NOT_RUNNING (refused)'],
                         example='chemlab run INST stop'),
            'result': Verb('result', 'the process record (speeds, positions, pressure, thickness monitor, temperatures)', self.h_result,
                           readonly=True, options={'save-to': save_to_opt()}, example='chemlab run INST result --save-to film.json'),
        }

    def member_keys(self):
        m = self.vmap('method') or {}
        keys = list((m.get('keys') or {}).keys()) + list((m.get('flag_keys') or {}).keys()) + list((m.get('then') or {}).keys())
        b = m.get('build', 'keys')
        if b == 'spin':
            keys = ['speed', 'time', 'program']
        elif b == 'segments':
            keys = ['temp', 'rate', 'hold'] + keys
        elif b == 'dmp':
            keys = keys + ['pattern', 'pattern-name', 'at']
        return keys

    def verb_help(self, name):
        out = super().verb_help(name)
        if name == 'method' and self.vmap('method'):
            m = self.vmap('method')
            lines = ['method keys on %s:' % self.instrument_id] + ['  %-15s %s' % (k + '=', self.KEYS.get(k, (None, 'native argument'))[1])
                                                                   for k in self.member_keys()]
            if m.get('required') or m.get('required_any'):
                lines.append('required: %s' % ', '.join(list(m.get('required') or []) + ['/'.join(a) for a in m.get('required_any') or []]))
            d = self.defaults_text(m)
            if d:
                lines.append('defaults for settings you leave out (documented example): %s' % d)
            lines.append('its own argument names (`chemlab help %s %s`) pass through too.' % (self.instrument_id, m['cap']))
            if self.param('method_example'):
                lines.append('example on %s: chemlab run %s method %s' % (self.instrument_id, self.instrument_id, self.param('method_example')))
            out['text'] += '\n'.join(lines) + '\n'
        return out

    def defaults_text(self, m):
        if not m.get('defaults') or self.lab is None:
            return ''
        ex = self.example(m['cap'])
        props = ((self.lab.contract['capabilities'][m['cap']].get('input_schema') or {}).get('properties') or {})
        out = []
        for a, v in ex.items():
            u = units.declared(props.get(a) or {})
            out.append('%s=%s' % (self.key_of(m, a), self.fmt_val(v, u) if not isinstance(v, list) else short(v, 40)))
        return ', '.join(out)

    def key_of(self, m, native):
        for k, f in (m.get('flag_keys') or {}).items():
            if f.get('arg') == native:
                return k
        for k, v in (m.get('then') or {}).items():
            if v == native:
                return k
        for k, v in (m.get('keys') or {}).items():
            if v == native or (isinstance(v, list) and native in v):
                return k
        return native

    # ------------------------------------------------------------------ method
    def h_method(self, ctx, p):
        m = self.vmap('method')
        kv = self.kv(ctx, p)
        props = (ctx.cap_spec(m['cap']).get('input_schema') or {}).get('properties') or {}
        allowed = set(self.member_keys())
        for k in kv:
            if k not in allowed and k not in props:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (self.instrument_id, k),
                                 'settings of %s: %s.' % (self.instrument_id, ', '.join(self.member_keys()))))
        self.need(ctx, 'method')
        return getattr(self, 'm_' + m.get('build', 'keys'))(ctx, m, kv)

    def parse_list(self, ctx, text, schema, what):
        items = schema.get('items') or {}
        if items.get('type') == 'string':
            return [x.strip() for x in text.split('/') if x.strip()]
        return [self.parse_key(ctx, x.strip().replace('sccm', 'mL/min'), items, None, what=what) for x in text.split(',') if x.strip()]

    def native_args(self, ctx, m, kv, skip=()):
        """Plain and list keys -> native arguments (in the declared units), flags, then the documented defaults."""
        cap = m['cap']
        sch = ctx.cap_spec(cap).get('input_schema') or {}
        props = sch.get('properties') or {}
        args = dict(m.get('args') or {})
        missing = [k for k in m.get('required') or [] if k not in kv]
        missing += ['/'.join(alt) for alt in m.get('required_any') or [] if not any(a in kv for a in alt)]
        if missing:
            ctx.fail(invalid('MISSING_SETTING', 'method on %s needs %s.' % (self.instrument_id, ', '.join('%s=' % k for k in missing)),
                             'e.g. `chemlab run %s method %s` (see `chemlab help %s method`).' % (self.instrument_id, self.param('method_example', ''),
                                                                                                  self.instrument_id)))
        for k, text in kv.items():
            if k in skip or k in (m.get('then') or {}):
                continue
            flag = (m.get('flag_keys') or {}).get(k)
            if flag is not None:                                            # e.g. rotation=off|12rpm, thickness=20nm (+ end point on)
                off = text.strip().lower() in ('off', 'no', 'false')
                for a, v in (flag.get('off') if off else flag.get('on') or {}).items():
                    args[a] = v
                if off or not flag.get('arg'):
                    continue
                natives = [flag['arg']]
            else:
                natives = (m.get('keys') or {}).get(k, k)
                natives = natives if isinstance(natives, list) else [natives]
            for native in natives:
                s = props.get(native) or {}
                if s.get('type') == 'array':
                    args[native] = self.parse_list(ctx, text, s, k)
                else:
                    args[native] = self.parse_key(ctx, text.replace('sccm', 'mL/min'), s, self.KEYS.get(k, (None,))[0], what=k)
        for k, natives in (m.get('then') or {}).items():                      # a later key wins over a shared one (withdraw-final)
            if k in kv:
                args[natives] = self.parse_key(ctx, kv[k], props.get(natives) or {}, None, what=k)
        used = []
        if m.get('defaults') == 'example':
            ex = self.example(cap)
            for a in sch.get('required') or []:
                if a not in args and a in ex:
                    args[a] = ex[a]
                    used.append(self.key_of(m, a))
        return args, used

    def m_keys(self, ctx, m, kv, skip=()):
        args, used = self.native_args(ctx, m, kv, skip)
        self.step_checks(ctx, m, args)
        self.check_args(ctx, m['cap'], args, self.labels(m))
        ctx.call(m['cap'], args, 'set the method (%s)' % ', '.join('%s=%s' % (k, short(v, 24)) for k, v in args.items()))
        return {'text': 'ok: method of %s set: %s%s\n' % (self.instrument_id, self.method_text(m, args),
                                                         ('\n  defaults used: %s' % ', '.join(used)) if used else ''),
                'json': {'arguments': args, 'defaults_used': used}}

    def labels(self, m):
        out = {}
        for k, v in (m.get('keys') or {}).items():
            for a in (v if isinstance(v, list) else [v]):
                out[a] = k
        return out

    def method_text(self, m, args):
        props = ((self.lab.contract['capabilities'][m['cap']].get('input_schema') or {}).get('properties') or {})
        out = []
        for a, v in args.items():
            if a in (m.get('args') or {}):
                continue
            s = props.get(a) or {}
            u = units.declared(s) or units.declared(s.get('items') or {})
            if isinstance(v, list) and u and units.unit_kind(u):
                shown = ','.join(self.fmt_val(x, u) if finite(x) else short(x, 20) for x in v)
            elif u and units.unit_kind(u) and finite(v):
                shown = self.fmt_val(v, u)
            elif a == m.get('seg_arg') and isinstance(v, list):
                shown = ', '.join('%s at %s, hold %s' % (units.fmt(x.get('target_k'), 'temperature'), units.fmt(x.get('ramp_k_s'), 'heating_rate'),
                                                         units.fmt_s(x.get('hold_s') or 0)) for x in v if isinstance(x, dict))
                out.append(shown)
                continue
            else:
                shown = short(v, 60)
            out.append('%s=%s' % (self.key_of(m, a), shown))
        return ', '.join(out)

    def step_checks(self, ctx, m, args):
        for a, (q, word) in (m.get('steps') or {}).items():
            v = args.get(a)
            if finite(v) and abs(v / q - round(v / q)) > 1e-6:
                name, f = STEP_WORDS.get(word, (word, 1))
                ctx.fail(invalid('BAD_STEP', '%s takes %s in steps of %g %s.' % (self.instrument_id, self.key_of(m, a) if m.get('keys') else m.get('what', 'the value'), q * f, name),
                                 'round it, e.g. %g%s.' % (round(v / q) * q * f, name.replace('µ', 'u'))))

    def m_spin(self, ctx, m, kv):
        """Spin coater: speed= time= (one step) or program=SPEED@TIME,... -> steps [{speed_rad_s, duration_s}]."""
        if 'program' in kv:
            if 'speed' in kv or 'time' in kv:
                ctx.fail(invalid('BAD_SETTING', 'give either program= or speed= with time=.', 'e.g. program=500rpm@5s,3000rpm@30s.'))
            steps = self.program(ctx, kv['program'], 'rotation')
        else:
            missing = [k for k in ('speed', 'time') if k not in kv]
            if missing:
                ctx.fail(invalid('MISSING_SETTING', 'method on %s needs %s (or program=).' % (self.instrument_id, ', '.join(k + '=' for k in missing)),
                                 'e.g. `chemlab run %s method speed=3000rpm time=30s`.' % self.instrument_id))
            steps = [(units.parse(kv['speed'], 'rotation', what='speed'), units.parse(kv['time'], 'duration', what='time'))]
        cap = m['cap']
        item = (((ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}).get(m['arg']) or {}).get('items') or {}
        ip = item.get('properties') or {}
        rows = [{'speed_rad_s': round(units.to_declared(w, units.declared(ip.get('speed_rad_s') or {})), 12),
                 'duration_s': units.to_declared(t, units.declared(ip.get('duration_s') or {}))} for w, t in steps]
        args = {m['arg']: rows}
        self.check_args(ctx, cap, args, {'speed_rad_s': 'speed', 'duration_s': 'time'})
        lo = self.param('min_speed_rad_s')
        if lo:
            for w, _ in steps:
                if 0 < w < lo - 1e-9:
                    ctx.fail(invalid('OUT_OF_RANGE', 'speed %s is below the lowest spin speed of %s (%s; 0 = not spinning).' % (
                        units.fmt(w, 'rotation'), self.instrument_id, units.fmt(lo, 'rotation')), 'give at least %s.' % units.fmt(lo, 'rotation')))
        ctx.call(cap, args, 'set the spin programme (%d step%s)' % (len(rows), '' if len(rows) == 1 else 's'))
        return {'text': 'ok: spin programme of %s: %s\n' % (self.instrument_id, ', then '.join('%s for %s' % (units.fmt(w, 'rotation'), units.fmt_s(t))
                                                                                             for w, t in steps)), 'json': {'arguments': args}}

    def m_segments(self, ctx, m, kv):
        """CVD furnace: temp= rate= hold= -> one controller segment; other keys as plain keys; defaults from the documented example."""
        if 'temp' not in kv or 'hold' not in kv:
            ctx.fail(invalid('MISSING_SETTING', 'method on %s needs temp= and hold= (rate= defaults to the documented example).' % self.instrument_id,
                             'e.g. `chemlab run %s method temp=600C rate=5K/min hold=30min flow=200,0,0`.' % self.instrument_id))
        args, used = self.native_args(ctx, m, kv, skip=('temp', 'rate', 'hold'))
        cap = m['cap']
        item = ((((ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}).get(m['seg_arg']) or {}).get('items') or {}).get('properties') or {}
        ex_seg = (self.example(cap).get(m['seg_arg']) or [{}])[0]
        seg = {'target_k': self.parse_key(ctx, kv['temp'], item.get('target_k') or {}, 'temperature', what='temp'),
               'ramp_k_s': (self.parse_key(ctx, kv['rate'], item.get('ramp_k_s') or {}, 'heating_rate', what='rate') if 'rate' in kv else ex_seg.get('ramp_k_s')),
               'hold_s': self.parse_key(ctx, kv['hold'], item.get('hold_s') or {}, 'duration', what='hold')}
        if 'rate' not in kv:
            used.append('rate')
        args[m['seg_arg']] = [seg]
        used = [u for u in used if u != self.key_of(m, m['seg_arg'])]
        self.check_args(ctx, cap, args, dict(self.labels(m), target_k='temp', ramp_k_s='rate', hold_s='hold'))
        ctx.call(cap, args, 'set the furnace programme (%s, %s, hold %s)' % (units.fmt(seg['target_k'], 'temperature'),
                                                                            units.fmt(seg['ramp_k_s'], 'heating_rate'), units.fmt_s(seg['hold_s'])))
        return {'text': 'ok: method of %s set: %s%s\n' % (self.instrument_id, self.method_text(m, args),
                                                         ('\n  defaults used: %s' % ', '.join(used)) if used else ''),
                'json': {'arguments': args, 'defaults_used': used}}

    def m_dmp(self, ctx, m, kv):
        """Inkjet printer: waveform / drop keys -> method.configure; pattern= [pattern-name=] [at=X,Y] -> pattern.configure."""
        pkeys = {'pattern', 'pattern-name', 'at'}
        mk = {k: v for k, v in kv.items() if k not in pkeys}
        if set(kv) & {'pattern-name', 'at'} and 'pattern' not in kv:
            ctx.fail(invalid('MISSING_SETTING', 'pattern-name= and at= go with pattern=ROWS.', 'e.g. pattern=010/111/010 at=50mm,50mm.'))
        pat = None
        if 'pattern' in kv:
            pcap = m['pattern_cap']
            pp = (ctx.cap_spec(pcap).get('input_schema') or {}).get('properties') or {}
            rows = [x.strip() for x in kv['pattern'].split('/') if x.strip()]
            if 'at' not in kv:
                ctx.fail(invalid('MISSING_SETTING', 'pattern= needs at=X,Y (where the pattern starts on the substrate).', 'e.g. at=50mm,50mm.'))
            xy = [x for x in kv['at'].split(',') if x.strip()]
            if len(xy) != 2:
                ctx.fail(invalid('BAD_SETTING', 'at= takes X,Y.', 'e.g. at=50mm,50mm.'))
            pat = {'name': kv.get('pattern-name', 'pattern'), 'rows': rows,
                   'x_m': self.parse_key(ctx, xy[0].strip(), pp.get('x_m') or {}, 'length', what='at x'),
                   'y_m': self.parse_key(ctx, xy[1].strip(), pp.get('y_m') or {}, 'length', what='at y')}
            self.check_args(ctx, pcap, pat, {'x_m': 'at x', 'y_m': 'at y'})
        out_text, js = [], {}
        if mk or not pat:
            args, used = self.native_args(ctx, m, mk)
            self.check_args(ctx, m['cap'], args, self.labels(m))
            ctx.call(m['cap'], args, 'set the jetting method')
            out_text.append('method %s%s' % (self.method_text(m, args), (' (defaults used: %s)' % ', '.join(used)) if used else ''))
            js.update({'arguments': args, 'defaults_used': used})
        if pat:
            ctx.call(m['pattern_cap'], pat, 'set the pattern %r (%d rows)' % (pat['name'], len(rows)))
            out_text.append('pattern %r %s at %s,%s' % (pat['name'], '/'.join(rows), units.fmt(pat['x_m'], 'length'), units.fmt(pat['y_m'], 'length')))
            js['pattern'] = pat
        return {'text': 'ok: %s %s\n' % (self.instrument_id, '; '.join(out_text)), 'json': js}

    # ------------------------------------------------------------------ motion
    def motion_reply(self, ctx, p, r, cap, what):
        m_max = float(self.param('motion_max_s', 600))
        if p.get('job') and not ctx.dry_run:
            if not r.get('operation_id'):
                return {'text': 'ok: %s %s\n' % (self.instrument_id, what), 'json': {}}
            job = OperationJob(ctx.core, self, ctx.req, '%s %s' % (self.instrument_id, what), cap, r['operation_id'], max_s=m_max)
            return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s\n' % (job.handle, what, job.handle, job.handle),
                    'json': {'job': job.handle}}
        st = self.wait_op(ctx, r, what, m_max)
        if not ctx.dry_run and st != 'succeeded':
            ctx.fail(refused('MOTION_' + str(st).upper(), '%s: %s ended %s.' % (self.instrument_id, what, st),
                             'check `chemlab run %s status`.' % self.instrument_id))
        pos = self.role(ctx, 'position')
        return {'text': 'ok: %s %s%s\n' % (self.instrument_id, what, (' (stage at %s)' % units.fmt(pos, 'length')) if finite(pos) else ''),
                'json': {'status': st, 'position_m': pos}}

    def h_move(self, ctx, p):
        m = self.vmap('move')
        cap = m['cap']
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        parts = [x for x in p.pos[0].split(',') if x.strip()]
        axes = m.get('axes') or [m['arg']]
        if len(parts) != len(axes):
            ctx.fail(invalid('BAD_POSITION', '%s moves to %d coordinate%s (%s).' % (self.instrument_id, len(axes), '' if len(axes) == 1 else 's',
                                                                                      ', '.join(axes)),
                             'e.g. `chemlab run %s move %s`.' % (self.instrument_id, ','.join(['50mm'] * len(axes)))))
        args = dict(m.get('args') or {})
        for a, t in zip(axes, parts):
            args[a] = self.parse_key(ctx, t.strip(), props.get(a) or {}, 'length', what=a)
        if m.get('speed_arg'):
            sp = p.get('speed')
            args[m['speed_arg']] = self.arg_value(cap, m['speed_arg'], sp) if sp is not None else self.example(cap).get(m['speed_arg'])
        elif p.get('speed') is not None:
            ctx.fail(invalid('NO_SPEED', '%s moves at its own axis speed; leave out --speed.' % self.instrument_id, 'drop --speed.'))
        self.check_args(ctx, cap, args, {m.get('speed_arg'): 'speed'})
        self.need(ctx, 'move')
        r = ctx.call(cap, args, 'move to %s' % ', '.join(units.fmt(args[a], 'length') for a in axes))
        return self.motion_reply(ctx, p, r, cap, 'moved to %s' % ', '.join(units.fmt(args[a], 'length') for a in axes))

    def h_home(self, ctx, p):
        m = self.vmap('home')
        self.need(ctx, 'home')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'home the stage')
        return self.motion_reply(ctx, p, r, m['cap'], 'homed')

    def h_gap(self, ctx, p):
        m = self.vmap('gap')
        cap = m['cap']
        g, left, right = (p.pos[0] if p.pos else None), p.get('left'), p.get('right')
        if m.get('arg'):                                                   # one clamp gap
            if left is not None or right is not None:
                ctx.fail(invalid('ONE_GAP', '%s has one gap; leave out --left / --right.' % self.instrument_id, 'e.g. `gap 1mm`.'))
            if g is None:
                ctx.fail(invalid('MISSING_ARGUMENT', 'gap needs GAP.', 'e.g. `chemlab run %s gap 1mm`.' % self.instrument_id))
            args = {m['arg']: self.arg_value(cap, m['arg'], g)}
            done = [(m['arg'], self.slot('gap'))]
        else:
            left = left if left is not None else g
            right = right if right is not None else g
            if left is None or right is None:
                ctx.fail(invalid('MISSING_ARGUMENT', 'gap needs GAP (both sides) or --left and --right.', 'e.g. `chemlab run %s gap 100um`.' % self.instrument_id))
            args = {m['left_arg']: round(self.arg_value(cap, m['left_arg'], left), 12), m['right_arg']: round(self.arg_value(cap, m['right_arg'], right), 12)}
            done = [(m['left_arg'], m['left_slot']), (m['right_arg'], m['right_slot'])]
        self.step_checks(ctx, m, args)
        self.check_args(ctx, cap, args, {a: 'gap' for a in args})
        self.need(ctx, 'gap')
        ctx.call(cap, args, 'set the gap to %s' % ', '.join(units.fmt(v, 'length') for v in args.values()))
        ok = self.settle(ctx, lambda v: all(finite(v.get(s)) and abs(v.get(s) - args[a]) <= 1e-12 for a, s in done), 'the gap is set',
                         float(self.param('gap_max_s', 30)))
        return {'text': 'ok: %s gap %s%s\n' % (self.instrument_id, ', '.join(units.fmt(v, 'length') for v in args.values()), '' if ok else ' (still moving)'),
                'json': {'gap': args, 'settled': ok}}

    # ------------------------------------------------------------------ heat, vacuum, prime
    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        text = p.raw['TEMP|off'].strip().lower()
        cap = m['cap']
        off = text in ('off', 'stop')
        zone = p.get('zone')
        zones = m.get('zones')
        if zone is not None and not zones:
            ctx.fail(invalid('NO_ZONE', '%s has one heater; leave out --zone.' % self.instrument_id, 'e.g. `set-temp 60C`.'))
        if off:
            if not m.get('on_cap'):
                ctx.fail(invalid('NO_OFF', '%s has no separate heater switch; set a low temperature instead.' % self.instrument_id, 'e.g. `set-temp 25C`.'))
            if ctx.v(self.slot('heating')) is False:
                return {'text': 'ok: %s heater is already off\n' % self.instrument_id, 'json': {'heating': False, 'changed': False}}
            ctx.call(m['on_cap'], {m['on_arg']: False}, 'heater off')
            return {'text': 'ok: %s heater off\n' % self.instrument_id, 'json': {'heating': False}}
        k = units.parse(text, 'temperature', what='temperature')
        args = dict(m.get('args') or {})
        if zones:
            zone = zone or m.get('default_zone')
            if zone not in zones:
                ctx.fail(invalid('UNKNOWN_ZONE', '%s has no zone %r.' % (self.instrument_id, zone), 'zones: %s.' % ', '.join(zones)))
            for z, spec in zones.items():                                  # the other zone keeps its set point
                args[spec['arg']] = round(self.arg_value(cap, spec['arg'], k), 10) if z == zone else ctx.v(spec['keep'])
            label = {zones[zone]['arg']: 'temperature'}
        else:
            args[m['arg']] = round(self.arg_value(cap, m['arg'], k), 10)
            label = {m['arg']: 'temperature'}
        self.check_args(ctx, cap, args, label)
        self.need(ctx, 'set-temp')
        ctx.call(cap, args, 'set point %s%s' % (units.fmt(k, 'temperature'), (' (%s)' % zone) if zones else ''))
        if m.get('on_cap') and not ctx.v(self.slot('heating')):
            ctx.call(m['on_cap'], {m['on_arg']: True}, 'heater on')
        return {'text': 'ok: %s %sset point %s; heater on\n' % (self.instrument_id, ('%s ' % zone) if zones else '', units.fmt(k, 'temperature')),
                'json': {'set_point_k': k, 'zone': zone}}

    def h_vacuum(self, ctx, p):
        m = self.vmap('vacuum')
        what = p.pos[0]
        if p.get('pressure') is not None and (what != 'pump' or not m.get('pressure_arg')):
            ctx.fail(invalid('NO_PRESSURE', '--pressure goes with `vacuum pump` on an instrument with a pressure set point.', 'leave out --pressure.'))
        self.need(ctx, 'vacuum-' + what)
        if what == 'vent':
            ctx.call(m['vent_cap'], {}, 'vent the chamber')
            return {'text': 'ok: %s venting (the chamber reaches room pressure in a few seconds)\n' % self.instrument_id, 'json': {'vacuum': 'vent'}}
        cap = m['cap']
        args = {}
        if m.get('full'):                                                  # CVD: enabled + mode + pressure + valve in one call
            args = self.example(cap)
            args[m['arg']] = what == 'pump'
            if p.get('pressure') is not None:
                args[m['pressure_arg']] = self.arg_value(cap, m['pressure_arg'], p.get('pressure'))
                if m.get('auto'):
                    args.update(m['auto'])
        else:
            args[m['arg']] = what == 'pump'
        self.check_args(ctx, cap, args, {m.get('pressure_arg'): 'pressure'})
        ctx.call(cap, args, 'pump %s' % ('on' if what == 'pump' else 'off'))
        return {'text': 'ok: %s pump %s%s\n' % (self.instrument_id, 'on' if what == 'pump' else 'off',
                                               (' (set point %s)' % units.fmt(p.get('pressure'), 'pressure')) if p.get('pressure') is not None else ''),
                'json': {'vacuum': what}}

    def h_prime(self, ctx, p):
        m = self.vmap('prime')
        cap = m['cap']
        args = {m['arg']: self.arg_value(cap, m['arg'], p.pos[0])}
        fl = p.get('flow')
        args[m['flow_arg']] = self.arg_value(cap, m['flow_arg'], fl) if fl is not None else self.example(cap).get(m['flow_arg'])
        self.check_args(ctx, cap, args, {m['arg']: 'volume', m['flow_arg']: '--flow'})
        self.need(ctx, 'prime')
        r = ctx.call(cap, args, 'dispense %s' % units.fmt(p.pos[0], 'volume'))
        st = self.wait_op(ctx, r, 'the ink is dispensed', float(self.param('motion_max_s', 600)))
        if not ctx.dry_run and st != 'succeeded':
            ctx.fail(refused('PRIME_' + str(st).upper(), '%s: priming ended %s.' % (self.instrument_id, st), 'check `chemlab run %s status`.' % self.instrument_id))
        return {'text': 'ok: %s primed with %s\n' % (self.instrument_id, units.fmt(p.pos[0], 'volume')), 'json': {'volume_m3': p.pos[0]}}

    # ------------------------------------------------------------------ start / stop
    def start_checks(self, ctx, p):
        ph = self.role(ctx, 'phase')
        st = self.role(ctx, 'stage')
        ctx.check(ph != 'running', 'BUSY', '%s is busy (%s%s).' % (self.instrument_id, ph, (' / %s' % st) if st else ''),
                  'wait for it (`chemlab run %s status`) or stop it.' % self.instrument_id, label='not running')
        m = self.vmap('start')
        if p.get('mode') is not None and not m.get('mode_arg'):
            ctx.fail(invalid('NO_MODE', '%s has one run mode; leave out --mode.' % self.instrument_id, '`chemlab run %s start`.' % self.instrument_id))

    def start_args(self, ctx, p, m):
        if not m.get('mode_arg'):
            return {}
        mode = p.get('mode') or m.get('default_mode')
        modes = self.arg_enum(m['cap'], m['mode_arg'])
        if modes and mode not in modes:
            ctx.fail(invalid('BAD_CHOICE', '--mode %s is not a mode of %s.' % (mode, self.instrument_id), 'modes: %s.' % ', '.join(modes)))
        return {m['mode_arg']: mode}

    def flag_word(self, role, v):
        if role in self.FLAG_WORDS and isinstance(v, bool):
            return self.FLAG_WORDS[role][0 if v else 1]
        if finite(v):
            kind = {'position': 'length', 'gap': 'length', 'lid': 'angle', 'pressure': 'pressure', 'speed': 'rotation'}.get(role)
            if kind:
                return units.fmt(v, kind)
        return super().flag_word(role, v)

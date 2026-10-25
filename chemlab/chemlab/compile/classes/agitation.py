"""Agitation class ("agitation"): spin, shake, mix or sonicate - the refrigerated centrifuge (Eppendorf 5910 Ri), the planetary
mixer (THINKY ARE-310), the incubated orbital shaker (IKA KS 4000), the rotor-stator disperser (IKA T 25), the probe sonicator
(Sonics VCX 750) and the ultrasonic bath (Elma S60H).

Verbs: speed RATE [--rcf G] [--time T] [--defoam RATE --defoam-time T], intensity AMPLITUDE%|MODE, time DURATION|continuous,
set-temp T (centrifuge chamber), safety-limit T (sonicator probe temperature), load SAMPLE --slot S | load --mass M, unload --slot S,
start [--wait] [--max T] [--at-temp], stop, result [--save-to F], status.  Power and lids / hoods / covers come from the shared
layer (`power on`, `open lid`, `close hood`); the KS 4000 and the S60H keep their temperature verbs (set-temp, heat, wait-temp,
safety-limit) in the thermal class - an instrument in both classes answers each verb from the class that has it.

Speeds are typed in rpm (or rad/s, Hz); a centrifuge speed can be given as relative centrifugal force (--rcf 4000g, ISO 15189
style), converted with the radius of the rotor in use as the lab declares it (lab config "rotor_radius_m"); results show both.
Arguments a capability needs and the agent did not give are kept from the instrument's state (the centrifuge packs speed, time,
temperature, acceleration and braking into one call; the sonicator's method holds amplitude, time, pulse and energy end point).

One native path per member (start / stop / result): the centrifuge's centrifuge.* core (set_method / measure / stop / get_curve)
rather than the touchscreen run.start / set_point; the mixers' and sonicator's acquisition core; the shaker's shaking.* and the
bath's ultrasound.* - each with its status query as a declared path exception.

Standards: PyLabRobot Centrifuge (spin at g for a time) and Shaker (shake, stop_shaking); SiLA 2 device features; NAMUR (IKA
KS 4000: OUT_SP_4, START_4, STOP_4); ISO 15189-style RCF reporting.
"""
import math

from .. import units
from ..base import finite, short
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb, save_to_opt
from .run_steps import StepAdapter, fmt_value

G = 9.80665
REF = ('5910 Ri manual pp.27-36 (lid, rotor, loading), 37-45 (touch interface, run records); ARE-310 manual pp.30-48 (balance, '
       'STD/STEP conditions, run); KS 4000 manual pp.17-20 (speed, timer, safety limits) and 23-25 (NAMUR); T 25 manual p.6 (speed); '
       'VCX 750 manual pp.13-21 (amplitude, timer, pulser, temperature and energy set points); Elmasonic S manual pp.20-24 (timer, '
       'sweep, degas, temperature-controlled start)')
RPM = 2 * math.pi / 60


def rpm(w):
    return units.fmt(w, 'rotation') if finite(w) else 'n/a'


class AgitationAdapter(StepAdapter):
    CLASS = 'agitation'
    TITLE = 'agitation (spin, shake, mix, sonicate)'
    ABOUT = ('Centrifuges, mixers, shakers, dispersers and sonicators: set the speed (rpm, or g for a centrifuge), the ultrasound '
             'amplitude or mode, the run time and the chamber temperature, load tubes or declare the cup mass, start a run (a job), '
             'stop it and read its record.')
    STANDARDS = ('PyLabRobot Centrifuge / Shaker; SiLA 2 device features; NAMUR (IKA KS 4000); ISO 15189 RCF reporting')
    ALWAYS = ('status',)
    UNIT_KINDS = ('rotation', 'duration', 'temperature', 'fraction', 'mass')
    PATH_GROUPS = (('start', 'stop', 'result'),)
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'BUSY': ('BUSY', '{inst} is running.', 'wait for the run (`chemlab run {inst} status`) or stop it (`chemlab run {inst} stop`).'),
    }

    # ------------------------------------------------------------------ helpers
    def running(self, values):
        ph = self.role(values, 'phase')
        return ph in (self.param('running_values') or ['running', 'stopping'])

    def need_power(self, ctx):
        s = self.slot('power')
        if s:
            ctx.check(bool(ctx.v(s)), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                      'switch it on: `chemlab run %s power on`.' % self.instrument_id, label='power on')

    def need_idle(self, ctx, what):
        if self.slot('phase') and self.param('idle_for_settings', True):
            ctx.check(not self.running(ctx), 'BUSY', '%s is running (%s = %s); %s cannot change now.' % (
                self.instrument_id, self.slot('phase'), self.role(ctx, 'phase'), what),
                'wait for the run (`chemlab run %s status`) or stop it (`chemlab run %s stop`).' % (self.instrument_id, self.instrument_id),
                label='not running')

    def radius(self, ctx):
        """Radius of the rotor in use (lab config "rotor_radius_m": {configuration: m}, or a single number)."""
        decl = self.config_entry.get('rotor_radius_m')
        conf = self.sval(ctx, self.slot('rotor')) if self.slot('rotor') else None
        if isinstance(decl, (int, float)):
            return float(decl), conf
        if isinstance(decl, dict) and conf in decl:
            return float(decl[conf]), conf
        return None, conf

    def rcf_of(self, ctx, w):
        r, _ = self.radius(ctx)
        return None if r is None or not finite(w) else r * w * w / G

    def summary(self, values):
        p = self.slot('power')
        if p and not self.sval(values, p):
            return ['power off'], {'power': False}
        w, ws = self.slot_si(values, self.slot('speed')), self.slot_si(values, self.slot('speed_set'))
        amp = self.sval(values, self.slot('amplitude'))
        mode = self.sval(values, self.slot('mode'))
        ph = self.role(values, 'phase')
        bits = []
        if w is not None or ws is not None:
            bits.append('speed %s (set %s)' % (rpm(w), rpm(ws)))
        if amp is not None:
            bits.append('amplitude %.0f %%' % (amp * 100) if finite(amp) else 'amplitude %s' % amp)
        if mode is not None:
            bits.append('mode %s' % mode)
        line = '; '.join(bits) or 'no speed reported'
        more = []
        if ph is not None:
            more.append('run %s' % ph)
        rem = self.slot_si(values, self.slot('remaining'))
        if finite(rem):
            more.append('%s left' % units.fmt_s(rem))
        tk = self.slot_si(values, self.slot('temp'))
        if finite(tk):
            more.append('%s' % units.fmt(tk, 'temperature'))
        lid = self.slot('lid')
        if lid:
            lv = self.sval(values, lid)
            if finite(lv):
                state = 'closed' if lv <= 1e-4 else 'open (%.2f)' % lv
            else:
                state = self.param('lid_states', {}).get(str(lv), short(lv, 20))
            more.append('%s %s' % (self.param('lid_word', 'lid'), state))
        js = {'power': True, 'speed_rad_s': w, 'speed_set_rad_s': ws, 'amplitude_fraction': amp, 'mode': mode, 'phase': ph,
              'remaining_s': rem, 'temperature_k': tk}
        return [line] + (['; '.join(more)] if more else []), js

    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code in ('PRECONDITION_FAILED', 'BUSY'):
            ctx.refresh()
            p = self.slot('power')
            if p and not ctx.v(p):
                return refused('POWER_OFF', '%s is switched off (it answered %s).' % (self.instrument_id, code),
                               'switch it on: `chemlab run %s power on`.' % self.instrument_id)
        return StepAdapter.explain_refusal(self, ctx, cap, args, response)

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        t = Opt('run time, e.g. 10min (sets the timer in the same step)', kind='duration', metavar='TIME')
        return {
            'speed': Verb('speed', 'set the rotation / shaking / mixing speed (rpm, or --rcf for a centrifuge)', self.h_speed,
                          positionals=[Arg('RATE', 'speed, e.g. 4000rpm (a bare number is rpm)', required=False, kind='rotation')],
                          options={'rcf': Opt('relative centrifugal force instead of rpm, e.g. 2000g (centrifuges; needs the rotor radius)', metavar='G'),
                                   'time': t,
                                   'defoam': Opt('planetary mixer: the speed of the defoaming step after mixing', kind='rotation', metavar='RATE'),
                                   'defoam-time': Opt('planetary mixer: the time of the defoaming step', kind='duration', metavar='TIME')},
                          preconditions=['power on', 'not running (where the member cannot change speed in a run)', 'within the range of the '
                                         'member (and its safety / rotor limit)'],
                          errors=['OUT_OF_RANGE, BAD_STEP, MISSING_TIME, NO_ROTOR_RADIUS (usage)', 'POWER_OFF, BUSY, ABOVE_SPEED_LIMIT, NO_ROTOR (refused)'],
                          example='chemlab run INST speed 4000rpm --time 10min', reference=REF),
            'intensity': Verb('intensity', 'set the ultrasound amplitude (e.g. 40%) or the bath mode (normal, sweep, degas)', self.h_intensity,
                              positionals=[Arg('VALUE', 'amplitude in %, or a mode')],
                              preconditions=['power on', 'a method set (probe sonicator)', 'within the range / one of the modes'],
                              errors=['OUT_OF_RANGE, BAD_CHOICE (usage)', 'NO_METHOD (refused)'], example='chemlab run INST intensity 40%', reference=REF),
            'time': Verb('time', 'set the run time (or continuous where the member has it)', self.h_time,
                         positionals=[Arg('TIME', 'run time, e.g. 10min, or continuous')],
                         preconditions=['power on', 'not running', 'within the timer range'], errors=['OUT_OF_RANGE (usage)', 'BUSY (refused)'],
                         example='chemlab run INST time 10min', reference=REF),
            'set-temp': Verb('set-temp', 'set the chamber temperature (refrigerated centrifuge)', self.h_set_temp,
                             positionals=[Arg('TEMP', 'temperature, e.g. 4C', kind='temperature')],
                             preconditions=['power on', 'not running', 'within the range'], errors=['OUT_OF_RANGE (usage)', 'BUSY (refused)'],
                             example='chemlab run INST set-temp 4C', reference=REF),
            'safety-limit': Verb('safety-limit', 'set the sample temperature limit (the sonicator pauses above it)', self.h_limit,
                                 positionals=[Arg('TEMP', 'limit, e.g. 40C', kind='temperature')],
                                 preconditions=['power on', 'within the range of the probe controller'], errors=['OUT_OF_RANGE (usage)'],
                                 example='chemlab run INST safety-limit 40C', reference=REF),
            'load': Verb('load', 'load a tube into a rotor position (--slot), or declare the container mass for the balance (--mass)', self.h_load,
                         positionals=[Arg('SAMPLE', 'the tube / sample name (centrifuge)', required=False)],
                         options={'slot': Opt('rotor position, e.g. slot_1', metavar='SLOT'),
                                  'mass': Opt('gross mass of the container with its content (planetary mixer balance)', kind='mass', metavar='MASS')},
                         preconditions=['lid open', 'rotor selected / position empty (centrifuge)', 'mass within the balance range (mixer)'],
                         errors=['MISSING_SLOT, MISSING_MASS (usage)', 'LID_CLOSED, NO_ROTOR (refused)'], example='chemlab run INST load tube_a --slot slot_1',
                         reference=REF),
            'unload': Verb('unload', 'take a tube out of a rotor position', self.h_unload,
                           options={'slot': Opt('rotor position', metavar='SLOT', required=True)},
                           preconditions=['lid open', 'not running'], errors=['LID_CLOSED (refused)'], example='chemlab run INST unload --slot slot_1',
                           reference=REF),
            'start': Verb('start', 'start the run (a job; --wait blocks until it ends)', self.h_start,
                          options={'wait': Opt('wait until the run ends (moves lab time)', kind='flag'),
                                   'max': Opt('with --wait: the longest wait', kind='duration', metavar='TIME'),
                                   'at-temp': Opt('ultrasonic bath: start the ultrasound only when the bath has reached its set temperature', kind='flag')},
                          preconditions=['power on', 'lid / hood / cover closed', 'speed, time / method set', 'load balanced, fixture / mount '
                                         'ready (the member\'s own)', 'not already running'],
                          errors=['LID_OPEN, NO_METHOD, IMBALANCE, NO_FIXTURE, NOT_MOUNTED, BUSY (refused)'],
                          example='chemlab run INST start --wait', reference=REF, long_action=True),
            'stop': Verb('stop', 'stop the run', self.h_stop, preconditions=['a run in progress'], errors=['NOT_RUNNING (refused)'],
                         example='chemlab run INST stop', reference=REF),
            'result': Verb('result', 'the record of the last run (speed, time, temperature, energy - what the member reports)', self.h_result,
                           readonly=True, options={'save-to': save_to_opt()}, example='chemlab run INST result --save-to run.json', reference=REF),
        }

    # ------------------------------------------------------------------ speed
    def h_speed(self, ctx, p):
        m = self.vmap('speed')
        if p.pos and p.get('rcf') is not None:
            ctx.fail(invalid('BOTH_SPEEDS', 'give the speed in rpm or as --rcf, not both.', 'e.g. `chemlab run %s speed 4000rpm`.' % self.instrument_id))
        if not p.pos and p.get('rcf') is None:
            ctx.fail(invalid('MISSING_SPEED', 'speed needs RATE (e.g. 2000rpm)%s.' % (' or --rcf G' if m.get('rcf') else ''),
                             'e.g. `chemlab run %s speed 2000rpm`.' % self.instrument_id))
        if p.get('rcf') is not None:
            if not m.get('rcf'):
                ctx.fail(invalid('NO_RCF', '%s takes speeds in rpm only (--rcf is for centrifuges).' % self.instrument_id, 'give RATE in rpm.'))
            g = self.parse_rcf(p.get('rcf'))
            r, conf = self.radius(ctx)
            if conf is None and self.slot('rotor'):
                ctx.fail(refused('NO_ROTOR', '%s has no rotor selected, so --rcf cannot be converted to rpm.' % self.instrument_id,
                                 'select the rotor (`chemlab call %s rotor.select --configuration ...`) or give rpm.' % self.instrument_id))
            if r is None:
                ctx.fail(invalid('NO_ROTOR_RADIUS', 'the lab does not declare the radius of rotor %s on %s, so --rcf cannot be converted.' % (conf, self.instrument_id),
                                 'give the speed in rpm.'))
            w = math.sqrt(g * G / r)
            ctx.notes.append('%g x g at r = %.3f m (%s rotor) = %s' % (g, r, conf, rpm(w)))
        else:
            w = p.pos[0]
        if (p.get('defoam') is not None or p.get('defoam-time') is not None) and not m.get('program'):
            ctx.fail(invalid('NO_DEFOAM', '%s has no defoaming step.' % self.instrument_id, 'leave out --defoam / --defoam-time.'))
        self.need_power(ctx)
        if m.get('idle', True):
            self.need_idle(ctx, 'the speed')
        self.requirements(ctx, 'speed')
        if m.get('program'):
            return self.speed_program(ctx, p, m, w)
        cap, arg = m['cap'], m['arg']
        native = self.arg_value(cap, arg, w)
        if self.param('integer_rpm'):
            r = w / RPM
            if abs(r - round(r)) > 1e-6:
                ctx.fail(invalid('BAD_STEP', '%s takes whole rpm, not %.3f rpm.' % (self.instrument_id, r), 'round it, e.g. %drpm.' % round(r)))
        self.check_range(ctx, cap, arg, native, 'speed')
        lim = self.slot_si(ctx, self.slot('speed_limit'))
        if finite(lim):
            ctx.check(w <= lim + 1e-9, 'ABOVE_SPEED_LIMIT', 'the speed %s is above the speed limit of %s (%s).' % (rpm(w), self.instrument_id, rpm(lim)),
                      'choose a lower speed, or raise the limit (`chemlab describe %s`, safety settings) if the load allows it.' % self.instrument_id,
                      label='below the speed limit')
        given = {arg: round(native, 10)}
        if p.get('time') is not None and m.get('time_arg'):
            given[m['time_arg']] = self.arg_value(cap, m['time_arg'], p.get('time'))
            self.check_range(ctx, cap, m['time_arg'], given[m['time_arg']], 'run time')
        args = self.build_args(ctx, m, given, 'speed')
        ctx.call(cap, args, 'set the speed to %s' % rpm(w))
        text = 'ok: %s speed %s' % (self.instrument_id, rpm(w))
        rcf = self.rcf_of(ctx, w) if m.get('rcf') else None
        if rcf is not None:
            text += ' (%.0f x g)' % rcf
        js = {'speed_rad_s': w, 'speed_rpm': w / RPM, 'rcf': rcf}
        if p.get('time') is not None:
            if m.get('time_arg'):
                text += ', run time %s' % units.fmt_s(p.get('time'))
            else:
                r2 = self.set_time(ctx, p.get('time'), False)
                text += ', ' + r2
            js['time_s'] = p.get('time')
        return {'text': text + '\n', 'json': js}

    @staticmethod
    def parse_rcf(text):
        t = (text or '').strip().lower().replace(' ', '').replace('xg', '').rstrip('g')
        try:
            v = float(t)
        except ValueError:
            raise invalid('BAD_NUMBER', '%r is not a relative centrifugal force.' % text, 'write it like 2000g (multiples of g).')
        if not math.isfinite(v) or v <= 0:
            raise invalid('BAD_NUMBER', '--rcf %s must be positive.' % text, 'write it like 2000g.')
        return v

    def speed_program(self, ctx, p, m, w):
        """Planetary mixer: one mix step (speed + time), optionally a defoam step, as the method program."""
        pr = m['program']
        if p.get('time') is None:
            ctx.fail(invalid('MISSING_TIME', 'a mixing step on %s needs its time too.' % self.instrument_id,
                             'give it: `chemlab run %s speed %drpm --time 60s` (optional --defoam 2200rpm --defoam-time 30s).' % (self.instrument_id, round(w / RPM))))
        if (p.get('defoam') is None) != (p.get('defoam-time') is None):
            ctx.fail(invalid('MISSING_DEFOAM', 'a defoaming step needs both --defoam RATE and --defoam-time TIME.', 'give both or neither.'))
        item = (self.cap_schema(m['cap'], pr['arg']).get('items') or {}).get('properties') or {}
        steps = [('mix', w, p.get('time'))]
        if p.get('defoam') is not None:
            steps.append(('defoam', p.get('defoam'), p.get('defoam-time')))
        prog = []
        for mode, sp, tm in steps:
            v = units.to_declared(sp, units.declared(item.get(pr['speed_key']) or {}) or 'rad/s')
            d = units.to_declared(tm, units.declared(item.get(pr['time_key']) or {}) or 's')
            for val, key, label in ((v, pr['speed_key'], '%s speed' % mode), (d, pr['time_key'], '%s time' % mode)):
                s = item.get(key) or {}
                if (s.get('minimum') is not None and val < s['minimum']) or (s.get('maximum') is not None and val > s['maximum']):
                    ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside %s..%s on %s.' % (label, fmt_value(val, units.declared(s)), s.get('minimum'),
                                                                                        s.get('maximum'), self.instrument_id),
                                     'give a %s from %s to %s %s.' % (label, s.get('minimum'), s.get('maximum'), units.declared(s) or '')))
            prog.append({pr['mode_key']: mode, pr['time_key']: round(d, 10), pr['speed_key']: round(v, 10)})
        args = self.build_args(ctx, m, {pr['arg']: prog}, 'speed')
        ctx.call(m['cap'], args, 'set the mixing program (%s)' % ', '.join('%s %s %s' % (s[0], rpm(s[1]), units.fmt_s(s[2])) for s in steps))
        return {'text': 'ok: %s program: %s\n' % (self.instrument_id, '; '.join('%s %s for %s' % (s[0], rpm(s[1]), units.fmt_s(s[2])) for s in steps)),
                'json': {'program': prog}}

    # ------------------------------------------------------------------ intensity / time / temperatures
    def h_intensity(self, ctx, p):
        m = self.vmap('intensity')
        cap, arg = m['cap'], m['arg']
        text = p.pos[0].strip()
        enum = self.arg_enum(cap, arg)
        if enum:
            if text.lower() not in [e.lower() for e in enum]:
                ctx.fail(invalid('BAD_CHOICE', '%s has no mode %r.' % (self.instrument_id, text), 'modes: %s.' % ', '.join(enum)))
            native = next(e for e in enum if e.lower() == text.lower())
            shown = native
        else:
            v = units.parse(text, 'fraction', what='amplitude')
            native = self.arg_value(cap, arg, v)
            lo, hi = self.arg_range(cap, arg)
            if (lo is not None and native < lo - 1e-9) or (hi is not None and native > hi + 1e-9):
                ctx.fail(invalid('OUT_OF_RANGE', 'amplitude %s is outside %.0f %% to %.0f %% on %s.' % (text, lo * 100, hi * 100, self.instrument_id),
                                 'give an amplitude from %.0f%% to %.0f%%.' % (lo * 100, hi * 100)))
            ctx.checks.append('amplitude within %.0f..%.0f %%' % (lo * 100, hi * 100))
            shown = '%.0f %%' % (v * 100)
        self.need_power(ctx)
        self.requirements(ctx, 'intensity')
        args = dict(m.get('args') or {})
        args[arg] = native
        ctx.call(cap, args, 'set the %s to %s' % ('mode' if enum else 'amplitude', shown))
        return {'text': 'ok: %s %s %s\n' % (self.instrument_id, 'mode' if enum else 'amplitude', shown), 'json': {arg: native}}

    def set_time(self, ctx, seconds, continuous):
        m = self.vmap('time')
        given = dict(m.get('args') or {})
        if continuous:
            if not m.get('continuous'):
                ctx.fail(invalid('NO_CONTINUOUS', '%s has no continuous mode.' % self.instrument_id, 'give a run time, e.g. 10min.'))
            given.update(m['continuous'])
        else:
            given.update(m.get('timed') or {})
            native = self.arg_value(m['cap'], m['arg'], seconds)
            self.check_range(ctx, m['cap'], m['arg'], native, 'run time')
            given[m['arg']] = round(native, 10)
        spec = dict(m)
        spec['args'] = {}
        args = self.build_args(ctx, spec, given, 'time')
        what = 'continuous run' if continuous else 'run time %s' % units.fmt_s(seconds)
        ctx.call(m['cap'], args, 'set the %s' % what)
        return what

    def h_time(self, ctx, p):
        text = p.pos[0].strip().lower()
        cont = text in ('continuous', 'cont', 'on', 'infinite')
        sec = None if cont else units.parse(text, 'duration', what='run time')
        self.need_power(ctx)
        self.need_idle(ctx, 'the run time')
        self.requirements(ctx, 'time')
        what = self.set_time(ctx, sec, cont)
        return {'text': 'ok: %s %s\n' % (self.instrument_id, what), 'json': {'time_s': sec, 'continuous': cont}}

    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        native = self.arg_value(m['cap'], m['arg'], k)
        self.check_range(ctx, m['cap'], m['arg'], native, 'temperature')
        self.need_power(ctx)
        self.need_idle(ctx, 'the temperature')
        self.requirements(ctx, 'set-temp')
        args = self.build_args(ctx, m, {m['arg']: round(native, 10)}, 'set-temp')
        ctx.call(m['cap'], args, 'set the temperature to %s' % units.fmt(k, 'temperature'))
        return {'text': 'ok: %s temperature %s\n' % (self.instrument_id, units.fmt(k, 'temperature')), 'json': {'temperature_k': k}}

    def h_limit(self, ctx, p):
        m = self.vmap('safety-limit')
        k = p.pos[0]
        native = self.arg_value(m['cap'], m['arg'], k)
        self.check_range(ctx, m['cap'], m['arg'], native, 'temperature limit')
        self.need_power(ctx)
        self.requirements(ctx, 'safety-limit')
        ctx.call(m['cap'], {m['arg']: round(native, 10)}, 'set the temperature limit to %s' % units.fmt(k, 'temperature'))
        return {'text': 'ok: %s temperature limit %s\n' % (self.instrument_id, units.fmt(k, 'temperature')), 'json': {'limit_k': k}}

    # ------------------------------------------------------------------ load / unload
    def h_load(self, ctx, p):
        m = self.vmap('load')
        self.need_power(ctx)
        if m.get('mass_arg'):
            if p.get('mass') is None:
                ctx.fail(invalid('MISSING_MASS', 'load on %s needs --mass: the gross mass of the container with its content.' % self.instrument_id,
                                 'weigh the filled container and give it, e.g. `chemlab run %s load --mass 140g`.' % self.instrument_id))
            if p.pos or p.get('slot'):
                ctx.fail(invalid('NO_SLOTS', '%s has one container holder: give only --mass.' % self.instrument_id, 'leave out SAMPLE / --slot.'))
            native = self.arg_value(m['cap'], m['mass_arg'], p.get('mass'))
            self.check_range(ctx, m['cap'], m['mass_arg'], native, 'mass')
            self.need_idle(ctx, 'the balance')
            self.requirements(ctx, 'load')
            ctx.call(m['cap'], {m['mass_arg']: round(native, 12)}, 'set the counterbalance to %s' % units.fmt(p.get('mass'), 'mass'))
            return {'text': 'ok: %s balance set to %s\n' % (self.instrument_id, units.fmt(p.get('mass'), 'mass')), 'json': {'mass_kg': p.get('mass')}}
        if not p.pos or not p.get('slot'):
            ctx.fail(invalid('MISSING_SLOT', 'load on %s needs SAMPLE and --slot.' % self.instrument_id,
                             'e.g. `chemlab run %s load tube_a --slot slot_1`; positions come in opposite pairs for balance.' % self.instrument_id))
        if p.get('mass') is not None:
            ctx.fail(invalid('NO_MASS', '%s weighs nothing itself: tube masses are known to the lab.' % self.instrument_id, 'leave out --mass.'))
        self.need_idle(ctx, 'the rotor load')
        self.requirements(ctx, 'load')
        ctx.call(m['cap'], {m['slot_arg']: p.get('slot'), m['sample_arg']: p.pos[0]}, 'load %s into %s' % (p.pos[0], p.get('slot')))
        imb = self.sval(ctx, self.slot('imbalance')) if self.slot('imbalance') else None
        notes = ['the rotor is out of balance now - load the opposite position' ] if imb else []
        return {'text': 'ok: %s %s in %s%s\n' % (self.instrument_id, p.pos[0], p.get('slot'), '; imbalance' if imb else ''),
                'json': {'slot': p.get('slot'), 'sample': p.pos[0], 'imbalance': imb}, 'notes': notes}

    def h_unload(self, ctx, p):
        m = self.vmap('unload')
        self.need_power(ctx)
        self.need_idle(ctx, 'the rotor load')
        self.requirements(ctx, 'unload')
        ctx.call(m['cap'], {m['slot_arg']: p.get('slot')}, 'unload %s' % p.get('slot'))
        return {'text': 'ok: %s %s emptied\n' % (self.instrument_id, p.get('slot')), 'json': {'slot': p.get('slot')}}

    # ------------------------------------------------------------------ start / stop / result
    def h_start(self, ctx, p):
        m = self.vmap('start')
        self.need_power(ctx)
        ctx.check(not self.running(ctx), 'BUSY', '%s is already running (%s = %s).' % (self.instrument_id, self.slot('phase'), self.role(ctx, 'phase')),
                  'wait for it or stop it (`chemlab run %s stop`).' % self.instrument_id, label='not running')
        self.requirements(ctx, 'start')
        args = dict(m.get('args') or {})
        if p.get('at-temp'):
            at = m.get('at_temp')
            if not at:
                ctx.fail(invalid('NO_AT_TEMP', '%s has no temperature-controlled start.' % self.instrument_id, 'leave out --at-temp.'))
            ctx.check(bool(self.sval(ctx, at['heater_slot'])), 'HEATING_OFF', 'the heater of %s is off, so it would never reach its set temperature.' % self.instrument_id,
                      'switch heating on (`chemlab run %s heat on`) or start without --at-temp.' % self.instrument_id, label='heating on')
            args[at['arg']] = True
        ctx.call(m['cap'], args, 'start (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.state_job(ctx, m, 'run on %s' % self.instrument_id)
        if not p.get('wait'):
            return {'text': 'started %s: run on %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s; then `chemlab run %s result`\n' % (
                job.handle, self.instrument_id, job.handle, job.handle, job.handle, self.instrument_id), 'json': {'job': job.handle, 'kind': job.kind}}
        r = self.follow_job(ctx, job, p.get('max') or float(m.get('max_s', 7200)))
        if job.state != 'succeeded':
            ctx.fail(refused('NOT_FINISHED' if job.state == 'running' else 'RUN_FAILED', 'the run on %s is %s after %s (%s).' % (
                self.instrument_id, job.state, units.fmt_s(r['json'].get('waited_s') or 0), r['text'].strip().splitlines()[-1][:160]),
                'follow it with `chemlab wait %s` or check `chemlab run %s status`.' % (job.handle, self.instrument_id), changed='yes'))
        res = self.read_result(ctx)
        return {'text': 'run on %s finished after %s:\n%s' % (self.instrument_id, units.fmt_s(r['json'].get('waited_s') or 0), res['text']),
                'json': dict(res['json'], job=job.handle)}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        self.need_power(ctx)
        ctx.check(self.running(ctx), 'NOT_RUNNING', '%s is not running (%s = %s).' % (self.instrument_id, self.slot('phase'), self.role(ctx, 'phase')),
                  'nothing to stop.', label='running')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop (%s)' % m['cap'])
        return {'text': 'ok: %s stopped (%s = %s)\n' % (self.instrument_id, self.slot('phase'), self.role(ctx, 'phase')), 'json': {'phase': self.role(ctx, 'phase')}}

    def read_result(self, ctx):
        m = self.vmap('result')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the run record (%s)' % m['cap'], query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') if isinstance(r.get('result'), dict) else {}
        out_props = ((ctx.cap_spec(m['cap']).get('output_schema') or {}).get('properties') or {})
        lines, js = [], {'result': res}
        if m.get('curve'):
            cv = res.get(m['curve']) or {}
            sp = [x for x in (cv.get('speed_rad_s') or []) if finite(x)]
            tt = [x for x in (cv.get('time_s') or []) if finite(x)]
            tk = [x for x in (cv.get('temperature_k') or []) if finite(x)]
            wmax = max(sp) if sp else None
            summ = {'run_s': tt[-1] if tt else None, 'max_speed_rad_s': wmax, 'max_speed_rpm': None if wmax is None else wmax / RPM,
                    'max_rcf': self.rcf_of(ctx, wmax), 'final_temperature_k': tk[-1] if tk else None, 'outcome': cv.get('outcome'),
                    'complete': cv.get('complete'), 'points': len(tt)}
            lines.append('  run %s, top speed %s%s, final chamber %s, outcome %s (%d curve points)' % (
                units.fmt_s(summ['run_s'] or 0), rpm(wmax), (' (%.0f x g)' % summ['max_rcf']) if finite(summ['max_rcf']) else '',
                units.fmt(summ['final_temperature_k'], 'temperature') if finite(summ['final_temperature_k']) else 'n/a', summ['outcome'], len(tt)))
            js.update(summ)
        for k in m.get('show') or []:
            if k in res:
                lines.append('  %s = %s' % (k, fmt_value(res.get(k), units.declared(out_props.get(k) or {}))))
                js[k] = res.get(k)
        if not lines:
            lines = ['  %s = %s' % (k, short(v, 120)) for k, v in list(res.items())[:12]]
        return {'text': '%s run record:\n%s\n' % (self.instrument_id, '\n'.join(lines)), 'json': js}

    def h_result(self, ctx, p):
        return self.read_result(ctx)

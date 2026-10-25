"""Temperature-control class ("thermal"): circulators, hot plates, ovens, furnaces, incubators, climate chambers, freezers.

Verbs: power, set-temp T [--wait], heat on|off, temp, wait-temp [--within] [--max] [--hold] [--job], stir RATE|off,
safety-limit T, remote on|off, status.  Members and mappings: configs/adapters/thermal.json.

Vendor-neutral models followed: SiLA 2 TemperatureController (ControlTemperature / CurrentTemperature / StopTemperatureControl),
PyLabRobot TemperatureController (set_temperature / get_temperature / wait_for_temperature / deactivate), the NAMUR command set
used by IKA (OUT_SP_1, IN_PV_1, START_1, STOP_1) and the JULABO remote commands (out_sp_00, in_pv_00, out_mode_05).

Class checks before anything is sent: the set point within the member's contract range (converted from the unit the argument
declares, K or °C); power on; the member's own requirements from its mapping (e.g. the JULABO takes set points from a computer
only in remote mode); the set point not above the member's safety limit; wait-temp only while the member is heating / cooling.
A refused native call is explained with the member's slots (current set point, limit, remote mode).
"""
from .. import units
from ..base import ClassAdapter, finite
from ..errors import invalid, refused
from ..jobs import ConditionJob
from ..verbs import Arg, Opt, Verb, save_to_opt

REF = ('JULABO CORIO operating manual: set point, remote control (rOn, OUT/IN commands), over-temperature cut-off; '
       'IKA RCT digital manual: temperature and speed setting, safety circuit, NAMUR commands; BINDER FD/VD/KBF manuals: '
       'set points and safety controller class 3.1; Nabertherm B510 and Carbolite 3216 controller manuals: set point; '
       'Eppendorf CellXpert C170i and CryoCube F570n manuals: set point')
ON_OFF = ['on', 'off']


class ThermalAdapter(ClassAdapter):
    CLASS = 'thermal'
    TITLE = 'temperature control'
    ABOUT = ('Instruments whose main job is holding a temperature: set the set point in °C, start or stop heating, read the '
             'temperature, wait until it is there (or get a job id), stir where the device stirs, set the safety limit.')
    STANDARDS = ('SiLA 2 TemperatureController; PyLabRobot TemperatureController; NAMUR (IKA: OUT_SP_1, IN_PV_1, START_1, STOP_1); '
                 'JULABO remote commands; DIN 12880 terms for ovens and incubators')
    ALWAYS = ('status', 'temp', 'wait-temp')
    UNIT_KINDS = ('temperature', 'temperature_delta', 'duration', 'rotation')
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'NO_MAINS': ('NO_MAINS', '{inst} has no mains power.', 'ask the lab operator to plug it in; then `chemlab run {inst} power on`.'),
        'STANDBY': ('STANDBY', '{inst} is in standby.', 'switch it on: `chemlab run {inst} power on`.'),
        'LOCKED': ('LOCKED', 'the keys of {inst} are locked.', 'unlock it first (see `chemlab describe {inst}`).'),
        'PANEL_LOCKED': ('LOCKED', 'the panel of {inst} is locked.', 'unlock it first (see `chemlab describe {inst}`).'),
        'REMOTE_DISABLED': ('REMOTE_OFF', '{inst} does not accept commands from the computer now (remote access is off).',
                            'switch remote access on (`chemlab run {inst} remote on` if it has the verb, else see `chemlab describe {inst}`).'),
        'HTD_RUNNING': ('BUSY', '{inst} is running its high-temperature disinfection.', 'wait until it has finished (`chemlab run {inst} status`).'),
        'INVALID_INCREMENT': ('BAD_STEP', '{inst} takes set points in 0.1 K steps.', 'give the temperature with one decimal, e.g. 37.0C.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab (no environment declared by the lab).', 'ask the lab operator.'),
        'ERROR_ACTIVE': ('DEVICE_ERROR', '{inst} shows an error code.', 'read it (`chemlab run {inst} status`); switch off and on to clear it.'),
        'SENSOR_FAULT': ('SENSOR_FAULT', 'the temperature sensor of {inst} reports a fault.', 'check the probe connection (`chemlab describe {inst}`).'),
    }

    # ------------------------------------------------------------------ helpers
    def t(self, values, role):
        """A temperature role (temp, set, limit) in K, or None."""
        return self.slot_si(values, self.slot(role))

    def c(self, k):
        return units.fmt(k, 'temperature') if finite(k) else 'n/a'

    def tolerance_k(self):
        return float(self.param('tolerance_k', 1.0))

    def need_power(self, ctx):
        s = self.slot('power')
        if s:
            ctx.check(bool(ctx.v(s)), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                      'switch it on: `chemlab run %s power on`.' % self.instrument_id, label='power on')

    def heating_active(self, values):
        s = self.slot('heat')
        if not s:
            return None
        v = values.v(s) if hasattr(values, 'v') else values.get(s)
        return bool(v)

    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code == 'ABOVE_SAFETY_LIMIT' or (code in ('PRECONDITION_FAILED', 'INVALID_ARGUMENT') and self.over_limit(ctx, args, cap)):
            lim = self.t(ctx, 'limit')
            return refused('ABOVE_SAFETY_LIMIT', 'the set point is above the safety limit of %s (%s).' % (self.instrument_id, self.c(lim)),
                           'set a lower temperature, or raise the limit first (`chemlab run %s safety-limit T`) if the sample and the '
                           'vessel allow it.' % self.instrument_id)
        if code == 'PRECONDITION_FAILED':
            ctx.refresh()
            p = self.slot('power')
            if p and not ctx.v(p):
                return refused('POWER_OFF', '%s is switched off (it answered %s).' % (self.instrument_id, code),
                               'switch it on: `chemlab run %s power on`.' % self.instrument_id)
            for verb in ('set-temp', 'heat'):
                for r in (self.map.get('requires') or {}).get(verb, []):
                    if ctx.v(r['slot']) != r.get('value', ctx.v(r['slot'])):
                        return refused(r.get('code', 'NOT_READY'), r['message'].format(inst=self.instrument_id, value=ctx.v(r['slot'])),
                                       r['fix'].format(inst=self.instrument_id))
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)

    def over_limit(self, ctx, args, cap):
        m = self.vmap('set-temp') or {}
        lim = self.t(ctx, 'limit')
        if cap != m.get('cap') or not finite(lim):
            return False
        v = units.from_declared(args.get(m['arg']), self.arg_unit(cap, m['arg']))
        return finite(v) and v > lim - float(self.param('limit_margin_k', 0.0)) + 1e-9

    # ------------------------------------------------------------------ summary
    def summary(self, values):
        p = self.slot('power')
        on = (values.get(p) if isinstance(values, dict) else values.v(p)) if p else True
        if p and not on:
            return ['power off'], {'power': False}
        tk, sk, lk = self.t(values, 'temp'), self.t(values, 'set'), self.t(values, 'limit')
        heat = self.heating_active(values)
        line = '%s (set point %s)' % (self.c(tk), self.c(sk))
        more = []
        if heat is not None:
            more.append('%s %s' % (self.param('heat_word', 'heating'), 'on' if heat else 'off'))
        if finite(lk):
            more.append('safety limit %s' % self.c(lk))
        stir = self.slot_si(values, self.slot('stir_speed'))
        if self.slot('stir_on'):
            son = values.get(self.slot('stir_on')) if isinstance(values, dict) else values.v(self.slot('stir_on'))
            more.append('stirring %s' % (('%s' % units.fmt(stir, 'rotation')) if son and finite(stir) else 'off'))
        rem = self.slot('remote')
        if rem:
            more.append('remote %s' % ('on' if (values.get(rem) if isinstance(values, dict) else values.v(rem)) else 'off'))
        js = {'power': True, 'temperature_k': tk, 'temperature_c': None if not finite(tk) else tk - 273.15, 'set_point_k': sk,
              'set_point_c': None if not finite(sk) else sk - 273.15, 'heating': heat, 'safety_limit_k': lk}
        return [line] + (['; '.join(more)] if more else []), js

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        temp = Arg('TEMP', 'set point, e.g. 80C, 353.15K or 176F (a bare number is °C)', kind='temperature')
        return {
            'power': Verb('power', 'switch the instrument on or off', self.h_power, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                          example='chemlab run INST power on', reference=REF),
            'set-temp': Verb('set-temp', 'set the temperature set point', self.h_set_temp, positionals=[temp],
                             options={'wait': Opt('then wait until the temperature is within the tolerance (a job is not needed)', kind='flag'),
                                      'max': Opt('with --wait: the longest wait (default 1 h)', kind='duration', metavar='TIME')},
                             preconditions=['set point within the instrument range (contract)', 'power on', 'the member\'s own mode (e.g. remote on)',
                                            'not above the safety limit'],
                             errors=['OUT_OF_RANGE (usage)', 'POWER_OFF, REMOTE_OFF, ABOVE_SAFETY_LIMIT (refused)'],
                             example='chemlab run INST set-temp 80C', reference=REF),
            'heat': Verb('heat', 'start or stop temperature control (heating / cooling / circulation)', self.h_heat,
                         positionals=[Arg('on|off', 'on or off', choices=ON_OFF)], preconditions=['power on', 'the member\'s own mode'],
                         errors=['POWER_OFF, REMOTE_OFF (refused)'], example='chemlab run INST heat on', reference=REF),
            'temp': Verb('temp', 'the current temperature and the set point, in °C', self.h_temp, readonly=True,
                         options={'save-to': save_to_opt()}, example='chemlab run INST temp'),
            'wait-temp': Verb('wait-temp', 'wait until the temperature is at the set point (or --to T), moving lab time', self.h_wait,
                              readonly=True, long_action=True,
                              options={'within': Opt('tolerance (default: the member\'s, about 1 K)', kind='temperature_delta', metavar='DELTA'),
                                       'max': Opt('longest wait (default 1 h)', kind='duration', metavar='TIME'),
                                       'hold': Opt('stay within the tolerance this long (default 0)', kind='duration', metavar='TIME'),
                                       'to': Opt('wait for this temperature instead of the set point', kind='temperature', metavar='TEMP'),
                                       'job': Opt('return a job id at once instead of waiting (chemlab wait|status|cancel job-N)', kind='flag')},
                              preconditions=['power on', 'heating / control on (where the instrument has a switch)'],
                              errors=['HEATING_OFF (refused)', 'NOT_REACHED (refused: still outside the tolerance after --max)'],
                              example='chemlab run INST wait-temp --within 0.5K --max 30min'),
            'stir': Verb('stir', 'stir (or shake) at a speed, or stop', self.h_stir, positionals=[Arg('RATE|off', 'speed, e.g. 300rpm, or off')],
                         preconditions=['power on', 'speed within the range'], errors=['OUT_OF_RANGE (usage)', 'POWER_OFF (refused)'],
                         example='chemlab run INST stir 300rpm', reference=REF),
            'safety-limit': Verb('safety-limit', 'set the over-temperature safety limit (cut-off)', self.h_limit, positionals=[temp],
                                 preconditions=['within the range of the safety controller'], errors=['OUT_OF_RANGE (usage)'],
                                 example='chemlab run INST safety-limit 120C', reference=REF),
            'remote': Verb('remote', 'switch remote control (commands from the computer) on or off', self.h_remote,
                           positionals=[Arg('on|off', 'on or off', choices=ON_OFF)], example='chemlab run INST remote on', reference=REF),
        }

    def h_power(self, ctx, p):
        m = self.vmap('power')
        want = p.pos[0] == 'on'
        s = self.slot('power')
        if s and bool(ctx.v(s)) == want:
            return {'text': 'ok: %s is already %s\n' % (self.instrument_id, p.pos[0]), 'json': {'power': want, 'changed': False}}
        args = dict(m.get('args') or {})
        args[m['arg']] = want
        ctx.call(m['cap'], args, 'switch %s' % p.pos[0], readback=(lambda v: None if bool(ctx.v(s)) == want else 'power %s' % ctx.v(s)) if s else None)
        return {'text': 'ok: %s switched %s\n' % (self.instrument_id, p.pos[0]), 'json': {'power': want, 'changed': True}}

    def check_range(self, ctx, cap, arg, k, what):
        lo, hi = self.arg_range_si(cap, arg)
        lo2, hi2 = self.param('min_k'), self.param('max_k')
        lo = max(x for x in (lo, lo2) if x is not None) if (lo is not None or lo2 is not None) else None
        hi = min(x for x in (hi, hi2) if x is not None) if (hi is not None or hi2 is not None) else None
        if (lo is not None and k < lo - 1e-9) or (hi is not None and k > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of %s (%s to %s).' % (what, self.c(k), self.instrument_id, self.c(lo), self.c(hi)),
                             'give a %s from %s to %s.' % (what, self.c(lo), self.c(hi))))
        ctx.checks.append('%s within %s..%s' % (what, self.c(lo), self.c(hi)))

    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        self.check_range(ctx, m['cap'], m['arg'], k, 'set point')
        step = self.param('step_c')
        if step:
            n = (k - 273.15) / float(step)
            if abs(n - round(n)) > 1e-6:
                ctx.fail(invalid('BAD_STEP', '%s takes set points in %g °C steps, not %s.' % (self.instrument_id, step, p.raw['TEMP']),
                                 'round it, e.g. %.1fC.' % (round(n) * float(step))))
        self.need_power(ctx)
        self.requirements(ctx, 'set-temp')
        lim = self.t(ctx, 'limit')
        if finite(lim) and self.param('limit_check', True):
            margin = float(self.param('limit_margin_k', 0.0))
            ctx.check(k <= lim - margin + 1e-9, 'ABOVE_SAFETY_LIMIT',
                      'the set point %s is above the safety limit of %s (%s%s).' % (self.c(k), self.instrument_id, self.c(lim),
                                                                                 ', minus %g K margin' % margin if margin else ''),
                      'set a lower temperature, or raise the limit first (`chemlab run %s safety-limit T`) if the sample and the vessel '
                      'allow it.' % self.instrument_id, label='below the safety limit')
        before = self.t(ctx, 'set')
        args = dict(m.get('args') or {})
        args[m['arg']] = units.sig(self.arg_value(m['cap'], m['arg'], k))
        set_spec = self.slot('set')
        ctx.call(m['cap'], args, 'set the set point to %s' % self.c(k),
                 readback=(lambda v: None if not finite(self.t(ctx, 'set')) or abs(self.t(ctx, 'set') - k) < 0.051
                           else 'set point %s' % self.c(self.t(ctx, 'set'))) if set_spec else None)
        notes = []
        heat = self.heating_active(ctx)
        if heat is False and self.supported('heat'):
            notes.append('%s is off - start it with `chemlab run %s heat on`.' % (self.param('heat_word', 'heating'), self.instrument_id))
        text = 'ok: set point %s (was %s); now %s\n' % (self.c(k), self.c(before), self.c(self.t(ctx, 'temp')))
        js = {'set_point_k': k, 'set_point_c': k - 273.15, 'was_k': before, 'temperature_k': self.t(ctx, 'temp')}
        if p.get('wait') and not ctx.dry_run:
            r = self.wait_for(ctx, k, self.tolerance_k(), p.get('max') or 3600.0, 0.0)
            text += r['text']
            js.update(r['json'])
        return {'text': text, 'json': js, 'notes': notes}

    def h_heat(self, ctx, p):
        m = self.vmap('heat')
        want = p.pos[0] == 'on'
        self.need_power(ctx)
        self.requirements(ctx, 'heat')
        if self.heating_active(ctx) == want:
            return {'text': 'ok: %s %s is already %s\n' % (self.instrument_id, self.param('heat_word', 'heating'), p.pos[0]), 'json': {'heat': want, 'changed': False}}
        if m.get('on_cap'):
            cap, args = (m['on_cap'] if want else m['off_cap']), dict(m.get('args') or {})
        else:
            cap, args = m['cap'], dict(m.get('args') or {})
            args[m['arg']] = want
        hs = self.slot('heat')
        ctx.call(cap, args, '%s %s' % (self.param('heat_word', 'heating'), p.pos[0]),
                 readback=(lambda v: None if bool(ctx.v(hs)) == want else '%s %s' % (hs, ctx.v(hs))) if hs else None)
        text = 'ok: %s %s %s (set point %s, now %s)\n' % (self.instrument_id, self.param('heat_word', 'heating'), p.pos[0],
                                                         self.c(self.t(ctx, 'set')), self.c(self.t(ctx, 'temp')))
        return {'text': text, 'json': {'heat': want, 'changed': True, 'set_point_k': self.t(ctx, 'set')}}

    def h_temp(self, ctx, p):
        tk, sk = self.t(ctx, 'temp'), self.t(ctx, 'set')
        lines, js = self.summary(ctx)
        return {'text': '%s: %s\n' % (self.instrument_id, '; '.join(lines)), 'json': js}

    def wait_for(self, ctx, target, within, max_s, hold_s):
        """Advance lab time until |T - target| <= within (held for hold_s).  Returns the reply part, or refuses NOT_REACHED."""
        state = {'since': None, 'done': False}

        def inside(view):
            tk = self.t(ctx, 'temp')
            ok = finite(tk) and abs(tk - target) <= within + 1e-9
            if not ok:
                state['since'] = None
                return False
            if state['since'] is None:
                state['since'] = ctx.now()
            state['done'] = ctx.now() - state['since'] >= hold_s - 1e-9
            return state['done']
        t0 = ctx.now()
        if not inside(ctx.view):
            ctx.advance(max_s, until=inside, step='wait until %s ± %s' % (self.c(target), units.fmt(within, 'temperature_delta')),
                        poll_s=float(self.param('poll_s', 5.0)))
        tk = self.t(ctx, 'temp')
        if not state['done']:
            ctx.fail(refused('NOT_REACHED', '%s is at %s after %s; the target %s ± %s%s was not reached.' % (
                self.instrument_id, self.c(tk), units.fmt_s(ctx.now() - t0), self.c(target), units.fmt(within, 'temperature_delta'),
                ' (held for %s)' % units.fmt_s(hold_s) if hold_s else ''),
                'wait longer (`chemlab run %s wait-temp --max 2h`), or check that it is heating (`chemlab run %s status`) and its '
                'door / lid is closed.' % (self.instrument_id, self.instrument_id), detail={'temperature_k': tk, 'target_k': target}))
        return {'text': 'reached %s (target %s ± %s) after %s of waiting (t = %.0f s)\n' % (
            self.c(tk), self.c(target), units.fmt(within, 'temperature_delta'), units.fmt_s(ctx.now() - t0), ctx.now()),
            'json': {'reached': True, 'temperature_k': tk, 'target_k': target, 'waited_s': ctx.now() - t0}}

    def h_wait(self, ctx, p):
        self.need_power(ctx)
        target = p.get('to') if p.get('to') is not None else self.t(ctx, 'set')
        if not finite(target):
            ctx.fail(refused('NO_SET_POINT', '%s has no set point to wait for.' % self.instrument_id, 'set one (`set-temp`) or give --to T.'))
        within = p.get('within') if p.get('within') is not None else self.tolerance_k()
        if self.heating_active(ctx) is False and self.supported('heat'):
            tk = self.t(ctx, 'temp')
            if not (finite(tk) and abs(tk - target) <= within):
                ctx.fail(refused('HEATING_OFF', '%s %s is off, so it will not reach %s (now %s).' % (
                    self.instrument_id, self.param('heat_word', 'heating'), self.c(target), self.c(tk)),
                    'start it: `chemlab run %s heat on`, then wait.' % self.instrument_id))
        max_s = p.get('max') or 3600.0
        hold = p.get('hold') or 0.0
        if ctx.dry_run:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': max_s}, 'step': 'wait until %s ± %s' % (self.c(target), units.fmt(within, 'temperature_delta'))})
            return {'text': '', 'json': {}}
        if p.get('job'):
            ad = self

            def test(c):
                tk = ad.t(c, 'temp')
                ok = finite(tk) and abs(tk - target) <= within + 1e-9
                return ok, '%s now, target %s ± %s' % (ad.c(tk), ad.c(target), units.fmt(within, 'temperature_delta'))
            job = ConditionJob(ctx.core, self, ctx.req, 'reach %s on %s' % (self.c(target), self.instrument_id), test, max_s=max_s,
                               poll_s=float(self.param('poll_s', 5.0)))
            return {'text': 'started %s: wait until %s ± %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
                job.handle, self.c(target), units.fmt(within, 'temperature_delta'), job.handle, job.handle, job.handle),
                'json': {'job': job.handle, 'target_k': target}}
        return self.wait_for(ctx, target, within, max_s, hold)

    def h_stir(self, ctx, p):
        m = self.vmap('stir')
        text = p.raw['RATE|off'].strip().lower()
        self.need_power(ctx)
        if text not in ('off', '0', 'stop'):
            self.requirements(ctx, 'stir')
        if text in ('off', '0', 'stop'):
            if m.get('stop_cap'):
                ctx.call(m['stop_cap'], {}, 'stop %s' % self.param('stir_word', 'stirring'))
            else:
                ctx.call(m['on_cap'], {m['on_arg']: False}, 'stop %s' % self.param('stir_word', 'stirring'))
            return {'text': 'ok: %s %s off\n' % (self.instrument_id, self.param('stir_word', 'stirring')), 'json': {'stir': False}}
        w = units.parse(text, 'rotation', what='speed')
        lo, hi = self.arg_range_si(m['speed_cap'], m['speed_arg'])
        if (lo is not None and w < lo - 1e-9) or (hi is not None and w > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'speed %s is outside %s..%s on %s.' % (units.fmt(w, 'rotation'), units.fmt(lo, 'rotation'),
                                                                                     units.fmt(hi, 'rotation'), self.instrument_id),
                             'give a speed from %s to %s.' % (units.fmt(lo, 'rotation'), units.fmt(hi, 'rotation'))))
        ctx.call(m['speed_cap'], {m['speed_arg']: self.arg_value(m['speed_cap'], m['speed_arg'], w)}, 'set the speed to %s' % units.fmt(w, 'rotation'))
        on_slot = self.slot('stir_on')
        if m.get('start_cap'):
            r = ctx.call(m['start_cap'], {}, 'start %s' % self.param('stir_word', 'stirring'))
            _ = r
        elif not (on_slot and ctx.v(on_slot)):
            ctx.call(m['on_cap'], {m['on_arg']: True}, 'start %s' % self.param('stir_word', 'stirring'))
        return {'text': 'ok: %s %s at %s\n' % (self.instrument_id, self.param('stir_word', 'stirring'), units.fmt(w, 'rotation')),
                'json': {'stir': True, 'speed_rad_s': w, 'speed_rpm': w * 60 / (2 * 3.141592653589793)}}

    def h_limit(self, ctx, p):
        m = self.vmap('safety-limit')
        k = p.pos[0]
        self.check_range(ctx, m['cap'], m['arg'], k, 'safety limit')
        self.need_power(ctx)
        self.requirements(ctx, 'safety-limit')
        sk = self.t(ctx, 'set')
        if finite(sk):
            ctx.check(k >= sk - 1e-9, 'BELOW_SET_POINT', 'a safety limit of %s is below the set point %s of %s; it would cut the heating off.' % (
                self.c(k), self.c(sk), self.instrument_id), 'lower the set point first, or choose a limit above it.', label='limit above the set point')
        args = dict(m.get('args') or {})
        for a, role in (m.get('keep') or {}).items():                 # arguments the capability needs that stay as they are
            args[a] = ctx.v(role)
        args[m['arg']] = units.sig(self.arg_value(m['cap'], m['arg'], k))
        before = self.t(ctx, 'limit')
        ctx.call(m['cap'], args, 'set the safety limit to %s' % self.c(k))
        return {'text': 'ok: safety limit of %s %s (was %s); set point %s\n' % (self.instrument_id, self.c(k), self.c(before), self.c(self.t(ctx, 'set'))),
                'json': {'safety_limit_k': k, 'was_k': before}}

    def h_remote(self, ctx, p):
        m = self.vmap('remote')
        want = p.pos[0] == 'on'
        s = self.slot('remote')
        if s and bool(ctx.v(s)) == want:
            return {'text': 'ok: remote control of %s is already %s\n' % (self.instrument_id, p.pos[0]), 'json': {'remote': want, 'changed': False}}
        ctx.call(m['cap'], {m['arg']: want}, 'remote control %s' % p.pos[0])
        return {'text': 'ok: remote control of %s %s\n' % (self.instrument_id, p.pos[0]), 'json': {'remote': want, 'changed': True}}

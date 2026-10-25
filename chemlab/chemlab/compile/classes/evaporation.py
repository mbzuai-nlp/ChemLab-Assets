"""Evaporation, drying and distillation class ("evaporation"): rotary evaporator, freeze dryer, spray dryer, critical point
dryer, centrifugal concentrator, fractional distillation, wiped-film molecular still.

Verbs: set-temp T|off [--heat], vacuum P, rotate SPEED|off, method KEY=VALUE ..., start (job), stop, result [--save-to],
lift up|down|POS [--wait], endpoint schedule|moisture [--max-moisture X], fraction N, reflux RATIO|total, condenser T,
feed RATE, preheat on|off [--wait], status.  Members and mappings: configs/adapters/evaporation.json.

A first draft had one umbrella verb ``step endpoint|fraction N|lift``; it is split here into lift / endpoint / fraction /
reflux / condenser / feed / preheat because the six process steps take six different kinds of argument (a position, a mode and
a moisture fraction, a receiver index, a ratio, a temperature, a flow, on/off) that one verb grammar cannot parse or check.  The
names follow the manuals' own terms (lift, endpoint, fraction collector, reflux ratio, condenser, feed pump, preheat).

Method dictionary (class keys with units; native argument names pass through where the member maps a method capability):
  temp=40C (bath / wall / shelf / inlet set point), pressure=20mbar (vacuum), time=30min (run time / timer), timeout=1h,
  interval=10s (rotation reversal), speed=120rpm (rotation / wiper), reflux=5 (ratio), total_reflux=on, cut=100mL (fraction
  volume), min_boiler=50mL, condenser=20C, feed=2g/min (mass flow) or feed=5mL/min (spray feed pump), purge=10min, bleed=14000
  (Pa/s, plain number), heat_time=5min, program=water|alcohol|other, endpoint=1% (remaining fraction), freeze=2h / primary=20h /
  secondary=4h (freeze-drying phases), shelf=-20C, protect=0C, inlet=150C, aspirator=25m3/h... (drying gas flow in mL/min,
  L/min), spray=10L/min (dispersion gas).

Vendor-neutral models: the MTP / VDI 2658 service state model (start / hold / stop / abort) and ISA-88 procedural terms; the
NAMUR command set of the IKA RV 10 (OUT_SP_1 / START_1 / STOP_1 for the bath, OUT_SP_4 for speed).

Class checks before anything is sent: values in the member's contract range (converted from the unit the argument declares),
the member's own requirements from its mapping (e.g. a method on the miVac, PILODIST and Vivaflow-style runtimes is fixed once
the sample is charged; the Lyovapor takes set points only while no cycle runs; the rotavap bath needs its heater configured).
"""
from .. import units
from ..base import finite, short
from ..errors import invalid
from ..verbs import Arg, Opt, Verb
from .separation_common import ON_OFF, SeparationAdapter

REF = None                       # manual pages are member notes in the fragment ("manual: ...")


class EvaporationAdapter(SeparationAdapter):
    CLASS = 'evaporation'
    TITLE = 'evaporation, drying, distillation'
    ABOUT = ('Remove solvent, dry or distill: set the bath / shelf / inlet / wall temperature, the vacuum, the rotation or wiper '
             'speed, a method (program) of class keys, start a run as a job, stop it, read its record; process steps (rotavap '
             'lift, freeze-drying endpoint, distillation fraction and reflux, molecular-still condenser, spray-dryer feed, '
             'preheat).  Samples, baths, vacuum sources and charges are the lab\'s set-up, not agent actions.')
    STANDARDS = 'MTP / VDI 2658 service states (start/hold/stop/abort), ISA-88 procedural terms; NAMUR (IKA RV 10)'
    UNIT_KINDS = ('temperature', 'pressure', 'rotation', 'duration', 'flow', 'length')
    SETTER_KINDS = {'set-temp': 'temperature', 'vacuum': 'pressure', 'condenser': 'temperature', 'feed': 'flow'}
    ROLES = (('power', 'power', 'bool'), ('phase', 'run', 'text'), ('stage', 'stage', 'text'), ('fault', 'fault', 'text'),
             ('temp', 'temperature', 'temperature'), ('temp_set', 'temperature set point', 'temperature'),
             ('heating', 'heating', 'bool'), ('temp2', 'second temperature', 'temperature'),
             ('pressure', 'pressure', 'pressure'), ('pressure_set', 'vacuum set point', 'pressure'),
             ('speed', 'speed', 'rotation'), ('speed_set', 'speed set point', 'rotation'), ('rotating', 'rotating', 'bool'),
             ('lift', 'lift', 'length'), ('timer', 'timer', 'duration'), ('flow', 'flow', 'flow'), ('collected', 'collected', 'mass'),
             ('progress', 'progress', 'plain'), ('fraction', 'receiver', 'plain'))

    def build_verbs(self):
        v = {
            'set-temp': self.setter_verb('set-temp', 'set the bath / shelf / inlet / wall temperature (off: heater off)', 'temperature',
                                         'TEMP', 'chemlab run INST set-temp 40C --heat', off=True, heat=True, reference=REF,
                                         preconditions=['set point within the member range', 'the member\'s own conditions (method, '
                                                        'no running cycle, heater ready)']),
            'vacuum': self.setter_verb('vacuum', 'set the vacuum (absolute pressure set point)', 'pressure', 'PRESSURE',
                                       'chemlab run INST vacuum 20mbar', reference=REF),
            'rotate': Verb('rotate', 'rotate the flask / wiper at a speed, or stop rotating (off)', self.h_rotate,
                           positionals=[Arg('SPEED', 'e.g. 120rpm, or off')], example='chemlab run INST rotate 120rpm',
                           preconditions=['speed within the member range'], errors=['OUT_OF_RANGE (usage)'], reference=REF),
            'method': self.method_verb('set method / program settings: KEY=VALUE ... (class keys take units: temp=40C time=30min '
                                       'pressure=20mbar; native argument names pass through)',
                                       'chemlab run INST method temp=40C time=30min', reference=REF,
                                       details='class keys: temp, pressure, time, timeout, interval, speed, reflux, total_reflux, cut, '
                                               'min_boiler, condenser, feed, purge, bleed, heat_time, program, endpoint, freeze, '
                                               'primary, secondary, shelf, protect, inlet, aspirator, spray (the member notes say '
                                               'which it has)'),
            'lift': Verb('lift', 'move the evaporator lift: up (flask out of the bath), down, or a height', self.h_lift,
                         positionals=[Arg('POS', 'up, down or a height such as 60mm')],
                         options={'wait': Opt('wait until the lift has stopped moving', kind='flag')},
                         example='chemlab run INST lift down --wait', errors=['OUT_OF_RANGE (usage)'], reference=REF),
            'endpoint': Verb('endpoint', 'how a drying cycle ends: on schedule, or when the modelled moisture is below a limit', self.h_endpoint,
                             positionals=[Arg('MODE', 'schedule or moisture', choices=['schedule', 'moisture'])],
                             options={'max-moisture': Opt('residual moisture limit (fraction), e.g. 1%', kind='fraction', metavar='X')},
                             example='chemlab run INST endpoint moisture --max-moisture 1%', reference=REF),
            'fraction': Verb('fraction', 'switch the fraction collector to receiver N', self.h_fraction,
                             positionals=[Arg('N', 'receiver number as the instrument counts it')],
                             example='chemlab run INST fraction 2', errors=['OUT_OF_RANGE (usage)'], reference=REF),
            'reflux': Verb('reflux', 'set the reflux ratio (reflux : take-off), or total reflux', self.h_reflux,
                           positionals=[Arg('RATIO', 'e.g. 5 (5:1), or total')], example='chemlab run INST reflux 5', reference=REF),
            'condenser': self.setter_verb('condenser', 'set the condenser temperature', 'temperature', 'TEMP',
                                          'chemlab run INST condenser 20C', reference=REF),
            'feed': self.setter_verb('feed', 'set the feed pump flow', 'flow', 'FLOW', 'chemlab run INST feed 5mL/min', reference=REF),
            'preheat': Verb('preheat', 'preheat on / off (chamber or drying gas)', self.h_preheat,
                            positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                            options={'wait': Opt('wait until the temperature is at the set point', kind='flag'),
                                     'max': Opt('longest wait (with --wait)', kind='duration', metavar='TIME', default='30min')},
                            example='chemlab run INST preheat on --wait', reference=REF),
        }
        v.update(self.run_verbs())
        return v

    # ------------------------------------------------------------------ handlers
    def h_rotate(self, ctx, p):
        m = self.vmap('rotate')
        text = p.pos[0]
        if str(text).strip().lower() == 'off':
            self.need(ctx, 'rotate:off')
            self.run_calls(ctx, m['off'].get('calls') or [dict(m['off'], step='stop rotating')])
            return {'text': 'ok: %s stopped rotating\n' % self.instrument_id, 'json': {'off': True}}
        si = units.parse(text, 'rotation', what='speed')
        if si <= 0:
            ctx.fail(invalid('NOT_POSITIVE', 'a speed of 0 is not a rotation.', 'write `rotate off` to stop.'))
        value = self.to_arg(ctx, m['speed_cap'], m['speed_arg'], si, 'rotation', 'speed')
        self.need(ctx, 'rotate')
        args = dict(m.get('speed_args') or {})
        args[m['speed_arg']] = value
        ctx.call(m['speed_cap'], args, 'set the speed to %s' % units.fmt(si, 'rotation'))
        if m.get('on'):
            self.run_calls(ctx, m['on'] if isinstance(m['on'], list) else [m['on']])
        return {'text': 'ok: %s rotating at %s\n' % (self.instrument_id, units.fmt(si, 'rotation')), 'json': {'speed_si': si}}

    def h_lift(self, ctx, p):
        m = self.vmap('lift')
        lo, hi, _ = self.bounds(self.schema_of(m['cap'], m['arg']))
        word = str(p.pos[0]).strip().lower()
        u = self.arg_unit(m['cap'], m['arg'])
        if word in ('up', 'down'):
            value = hi if word == 'up' else lo
            si = units.from_declared(value, u) if u else value
        else:
            si = units.parse(p.pos[0], 'length', what='lift height')
            value = self.to_arg(ctx, m['cap'], m['arg'], si, 'length', 'lift height')
        self.need(ctx, 'lift')
        ctx.call(m['cap'], {m['arg']: value}, 'move the lift to %s' % units.fmt(si, 'length'))
        waited = 0.0
        if p.get('wait') and m.get('moving'):
            waited = self.wait_until(ctx, lambda view: not view.get(m['moving']), 120.0, 'the lift move')
        return {'text': 'ok: the lift of %s is %s %.0f mm%s\n' % (self.instrument_id, 'moving to' if not p.get('wait') else 'at', si * 1000,
                                                                 ' (moved for %s)' % units.fmt_s(waited) if waited else ''),
                'json': {'position_si': si, 'waited_s': waited}}

    def h_endpoint(self, ctx, p):
        m = self.vmap('endpoint')
        mode = p.pos[0]
        native_mode = (m.get('modes') or {}).get(mode, mode)
        thr = p.get('max-moisture')
        if thr is None:
            thr = self.slot_value(ctx, m['keep_slot']) if m.get('keep_slot') else None
            if not finite(thr):
                ctx.fail(invalid('MISSING_OPTION', 'endpoint on %s needs --max-moisture (no stored limit to keep).' % self.instrument_id,
                                 'give e.g. --max-moisture 1%.'))
        thr = self.check_range(ctx, self.schema_of(m['cap'], m['threshold_arg']), thr, 'fraction', 'moisture limit')
        self.need(ctx, 'endpoint')
        ctx.call(m['cap'], {m['mode_arg']: native_mode, m['threshold_arg']: thr}, 'set the endpoint to %s (moisture limit %s)' % (
            mode, units.fmt(thr, 'fraction')))
        return {'text': 'ok: %s endpoint %s, moisture limit %s\n' % (self.instrument_id, mode, units.fmt(thr, 'fraction')),
                'json': {'mode': native_mode, 'moisture_threshold': thr}}

    def h_fraction(self, ctx, p):
        m = self.vmap('fraction')
        try:
            n = float(p.pos[0])
        except ValueError:
            ctx.fail(invalid('BAD_NUMBER', '%r is not a receiver number.' % p.pos[0], 'give a whole number, e.g. 2.'))
        value = self.check_range(ctx, self.schema_of(m['cap'], m['arg']), n, 'plain', 'receiver')
        self.need(ctx, 'fraction')
        ctx.call(m['cap'], {m['arg']: value}, 'switch to receiver %d' % value)
        return {'text': 'ok: %s collecting into receiver %d\n' % (self.instrument_id, value), 'json': {'receiver': value}}

    def h_reflux(self, ctx, p):
        m = self.vmap('reflux')
        word = str(p.pos[0]).strip().lower()
        if word == 'total':
            ratio = self.slot_value(ctx, m['keep_slot'])
            if not finite(ratio):
                ctx.fail(invalid('MISSING_VALUE', 'no stored reflux ratio to keep on %s.' % self.instrument_id, 'give a ratio, e.g. `reflux 5`.'))
            total = True
        else:
            t = word.split(':')[0]
            try:
                ratio = float(t)
            except ValueError:
                ctx.fail(invalid('BAD_NUMBER', '%r is not a reflux ratio.' % p.pos[0], 'give a ratio such as 5 (or 5:1), or total.'))
            total = False
        ratio = self.check_range(ctx, self.schema_of(m['cap'], m['ratio_arg']), ratio, 'plain', 'reflux ratio')
        self.need(ctx, 'reflux')
        ctx.call(m['cap'], {m['ratio_arg']: ratio, m['total_arg']: total}, 'set the reflux to %s' % ('total' if total else '%g:1' % ratio))
        return {'text': 'ok: %s reflux %s\n' % (self.instrument_id, 'total' if total else '%g:1' % ratio), 'json': {'ratio': ratio, 'total_reflux': total}}

    def h_preheat(self, ctx, p):
        m = self.vmap('preheat')
        on = p.pos[0] == 'on'
        spec = m['on' if on else 'off']
        self.need(ctx, 'preheat:' + ('on' if on else 'off'))
        self.run_calls(ctx, spec.get('calls') or [dict(spec, step='preheat %s' % p.pos[0])])
        waited = 0.0
        if on and p.get('wait'):
            w = m.get('wait')
            if not w:
                ctx.fail(invalid('NO_WAIT', '%s has no temperature to wait for after preheat.' % self.instrument_id, 'leave out --wait.'))
            tol = float(w.get('tol_k', 1.0))

            def at(view):
                t, s = self.slot_si(view, w['slot']), self.slot_si(view, w['target'])
                return t is not None and s is not None and abs(t - s) <= tol
            waited = self.wait_until(ctx, at, p.get('max'), 'preheating (to within %g K of the set point)' % tol, poll_s=2.0)
        return {'text': 'ok: %s preheat %s%s\n' % (self.instrument_id, p.pos[0], ' - at temperature after %s' % units.fmt_s(waited) if (on and p.get('wait')) else ''),
                'json': {'preheat': on}}


_ = short

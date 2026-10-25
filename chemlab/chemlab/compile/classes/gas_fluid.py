"""Gas and fluid class ("gas_fluid"): mass-flow and pressure controllers, an evaporator-mixer, a stream-selection valve, a vacuum
pump, a hydrogen generator, an ultrapure-water system, a NO/NOx analyzer and a helium leak detector (and the glovebox's gas
purification as a secondary member).

Verbs: setpoint VALUE [--wait], start, stop, select GAS|POSITION, dispense VOLUME, read, zero [on|off], calibrate [ACTION]
[KEY=VALUE ...], vent [on|off], status.  power, open/close, ack, estop come from the shared layer.  Members and mappings:
configs/adapters/gas_fluid.json.

setpoint takes the quantity the unit says: a flow (500sccm, 1.5slpm, 2mL/min), a mass flow (12g/h), a pressure (50mbar, 3bar)
or a temperature (140C); each member maps the quantities it controls to its own capability, converted to the unit that
argument declares.  Gauge and absolute pressure are never converted into each other: a member's pressure set point is whatever
its controller regulates (the help and the reply say which).  Where the device must also be switched to automatic / pressure
control to act on the set point, setpoint does that as a second, reported step.

Class checks before anything is sent: the value within the member's contract range (and the controller's full scale), the
member's own requirements from its mapping (power, a gas supply, a vessel on the pump, a part on the leak detector, a zero or
span gas on the analyzer inlet, the reading not over range), and for calibrate the gauge reading near the reference.

Vendor-neutral models followed: OPC UA LADS controller and sensor functions (SetPoint / CurrentValue / Start / Stop), NAMUR
for the vacuum controller, Alicat ASCII and Bronkhorst FLOW-BUS (set point, measure, auto-zero, control mode) as vendor styles,
ASTM E1603 / EN 1779 terms for helium leak testing, EN 14211 terms (zero, span) for the NOx analyzer.
"""
import json
import math
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import CompileError, invalid, refused, unsupported
from ..jobs import ConditionJob, OperationJob, StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt

REF = ('Alicat MC operating manual: set point, gas select, tare; Bronkhorst EL-PRESS / CEM manuals: set point, control mode, '
       'auto-zero, bleed; VACUUBRAND PC 3001 VARIO select manual: vacuum control, venting, sensor adjustment; PEAK Precision '
       'Hydrogen manual: outlet pressure, start / standby, depressurise; ELGA PURELAB flex manual: dispense volume and rate, '
       'calibration; ECO PHYSICS nCLD 822 manual: measurement, zero and span calibration; INFICON UL1000 Fab manual: START / '
       'STOP / ZERO, calibration, venting; VICI EUTA manual: position selection')
ON_OFF = ['on', 'off']
KIND_WORDS = {'flow': 'flow', 'mass_flow': 'mass flow', 'pressure': 'pressure', 'temperature': 'temperature'}
def parse_quantity(text, kind, what):
    """Agent text -> SI (units.parse: sccm, slpm, ppm, ppb ... are framework units)."""
    return units.parse((text or '').strip().replace(' ', ''), kind, what=what)


def fmt_value(v, unit):
    """A slot value in its declared unit -> plain text with a friendly unit."""
    if v is None:
        return 'n/a'
    if isinstance(v, bool):
        return 'yes' if v else 'no'
    if not finite(v):
        return short(v, 80)
    u = units.norm_unit(unit)
    if u == 'mol/mol':
        return '%.4g ppm' % (v * 1e6)
    if u in ('Pa m3/s', 'Pa*m3/s', 'Pa m^3/s'):
        return '%.2e Pa·m³/s (%.2e mbar·L/s)' % (v, v * 10.0)
    if u == 'ohm*m':
        return '%.2f MΩ·cm' % (v * 1e-4)
    if u == 'Pa':
        return units.fmt(v, 'pressure', 1 if abs(v) < 1e5 else 3)
    if u in ('m^3/s',):
        return '%s (%.4g L/min)' % (units.fmt(v, 'flow'), v * 6e4)
    k = units.unit_kind(u) if u else None
    if k:
        return units.fmt(units.from_declared(v, u), k)
    return ('%g %s' % (v, u)) if u and u != '1' else '%g' % v


class GasFluidAdapter(ClassAdapter):
    CLASS = 'gas_fluid'
    TITLE = 'gas / fluid supply, control and monitoring'
    ABOUT = ('Devices that supply, control or monitor a gas or liquid stream: give a set point in the unit you mean (500sccm, '
             '3bar, 50mbar, 12g/h, 140C), start / stop the pump, generator, dispenser or measurement, select a gas or a valve '
             'position, dispense a volume, read the measured value, zero and calibrate sensors, vent.')
    STANDARDS = ('OPC UA LADS controller / sensor functions; NAMUR (vacuum controller); Alicat ASCII and Bronkhorst FLOW-BUS '
                 '(vendor styles: set point, measure, tare / auto-zero, control mode); ASTM E1603 / EN 1779 (He leak testing); '
                 'EN 14211 (NOx analyzer zero / span)')
    ALWAYS = ('status',)
    UNIT_KINDS = ('flow', 'mass_flow', 'pressure', 'temperature', 'volume', 'fraction', 'duration')
    PATH_GROUPS = (('start', 'stop'),)
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'ACCESS_DENIED': ('NOT_YOURS', 'what is running on {inst} was started by someone else; only they can stop, interrupt or change it.',
                          'wait until it has ended (`chemlab run {inst} status`), or ask whoever started it.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab (the lab has not declared its surroundings).', 'ask the lab operator.'),
        'NO_GAS_SUPPLY': ('NO_GAS_SUPPLY', 'no carrier gas is connected to {inst}.', 'ask the lab operator to connect the gas supply.'),
        'NO_LIQUID_SUPPLY': ('NO_LIQUID_SUPPLY', 'no liquid is connected to {inst}.', 'ask the lab operator to connect the liquid supply.'),
        'BLEED_OPEN': ('BLEED_OPEN', 'the bleed (vent) of {inst} is open, so it takes no liquid flow.', 'close it: `chemlab run {inst} vent off`.'),
        'LIQUID_SETPOINT_NOT_ZERO': ('LIQUID_FLOWING', 'the liquid set point of {inst} is not zero; it vents only without liquid flow.',
                                     'set the liquid flow to 0 first: `chemlab run {inst} setpoint 0g/h`.'),
        'SETPOINT_ABOVE_FULL_SCALE': ('ABOVE_FULL_SCALE', 'the set point is above the full scale of the {inst} controller.', 'give a smaller value (see `chemlab run {inst} status`).'),
        'GAS_FLOWING': ('GAS_FLOWING', 'gas is still flowing through {inst}.', 'set the gas flow to 0 first.'),
        'RUNNING_UP': ('RUNNING_UP', '{inst} is still running up (about 3 min after switching on).', 'wait (`chemlab wait 180`), then retry.'),
        'NOT_MEASURING': ('NOT_MEASURING', '{inst} is not measuring.', 'start the measurement first: `chemlab run {inst} start --wait`.'),
        'SIGNAL_NOT_STABLE': ('NOT_STABLE', 'the signal of {inst} is not stable yet.', 'wait a little (`chemlab wait 30`), then retry.'),
        'ZERO_DISABLED': ('ZERO_DISABLED', 'the zero function of {inst} is disabled in its settings.', 'see `chemlab describe {inst}` (settings.configure zero_mode).'),
        'DETECTOR_FAULT': ('DEVICE_ERROR', '{inst} shows an error message that is not cleared.', 'read it (`chemlab run {inst} status`) and acknowledge it (`chemlab run {inst} ack`).'),
        'CALIBRATING': ('BUSY', '{inst} is calibrating.', 'wait until the calibration has finished.'),
        'BUSY': ('BUSY', '{inst} is busy with another action.', 'wait until it has finished (`chemlab run {inst} status`).'),
        'NO_PART': ('NO_PART', 'no test part is connected to the inlet of {inst}.', 'ask the lab operator to connect the part.'),
        'INLET_NOT_VENTED': ('NOT_VENTED', 'the inlet of {inst} is not vented.', 'vent it: `chemlab run {inst} vent`.'),
        'REMOTE_DISABLED': ('REMOTE_OFF', '{inst} does not accept commands from the computer now (control location).', 'see `chemlab describe {inst}`.'),
        'NOT_READY_FOR_CALIBRATION': ('NOT_READY', '{inst} calibrates only in standby or while measuring.', 'see `chemlab run {inst} status`.'),
        'MISSING_SETTING': ('MISSING_SETTING', '{inst} needs one more setting for this.', 'see `chemlab help {inst} calibrate`.'),
        'DEPRESSURISING': ('DEPRESSURISING', '{inst} is depressurising its outlet.', 'wait until it is back in standby (`chemlab run {inst} status`), then retry.'),
    }

    SLOT_KEYS = ('reading', 'set', 'full_scale', 'in_position', 'delivered', 'rate_slot', 'pending', 'done', 'duration_slot')

    def check_mapping(self, lab):
        """The base check, plus the class's own slot keys (reading, set, full_scale, ...; 'slot#key' allowed)."""
        visible = {s.lower() for s in lab.view.slots}
        out = []
        for pr in ClassAdapter.check_mapping(self, lab):
            m = re.search(r'no visible state slot (\S+)$', pr)
            if m and '#' in m.group(1) and m.group(1).split('#', 1)[0].lower() in visible:
                continue
            out.append(pr)

        def walk(o, where):
            if isinstance(o, dict):
                for k, x in o.items():
                    if (k in self.SLOT_KEYS or k == 'pre_keep') and x is not None:
                        vals = list(x.values()) if isinstance(x, dict) and k == 'pre_keep' else [x] if isinstance(x, str) else []
                        for sid in vals:
                            if sid.split('#', 1)[0].lower() not in visible:
                                out.append('%s.%s: no visible state slot %s' % (where, k, sid))
                    if isinstance(x, (dict, list)):
                        walk(x, '%s.%s' % (where, k))
            elif isinstance(o, list):
                for x in o:
                    walk(x, where)
        walk(self.map.get('verbs') or {}, 'verbs')
        return out

    # ------------------------------------------------------------------ helpers
    def kinds(self):
        return [k for k in ('flow', 'mass_flow', 'pressure', 'temperature') if k in (self.vmap('setpoint') or {})]

    def sv(self, values, sid):
        if sid is None:
            return None
        get = values.v if hasattr(values, 'v') else values.get
        base, _, key = sid.partition('#')
        v = get(base)
        return v.get(key) if key and isinstance(v, dict) else v

    def slot_unit(self, sid):
        base = sid.split('#', 1)[0]
        s = self.lab.contract['state_slots'].get(base) or {}
        return s.get('unit')

    def show(self, values, sid):
        return fmt_value(self.sv(values, sid), self.slot_unit(sid) if '#' not in sid else None)

    def need(self, ctx, verb):
        self.requirements(ctx, verb)

    def parse_value(self, ctx, text, kinds, what):
        """Text -> (kind, SI) for the first kind whose units the text uses."""
        t = (text or '').strip()
        bare = re.fullmatch(r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?', t) is not None
        if bare and len(kinds) > 1:
            ctx.fail(invalid('UNIT_NEEDED', '%s controls %s; give a unit so it is clear which one (e.g. %s).' % (
                self.instrument_id, ' and '.join(KIND_WORDS[k] for k in kinds), ', '.join(units.example(k) for k in kinds)),
                'write the value with its unit, e.g. %s.' % units.example(kinds[0])))
        last = None
        for k in kinds:
            try:
                return k, parse_quantity(t, k, what)
            except CompileError as exc:
                last = exc
                if exc.code != 'UNIT_NOT_ALLOWED':
                    raise
        ctx.fail(invalid('UNIT_NOT_ALLOWED', '%r is not a %s on %s.' % (text, ' or '.join(KIND_WORDS[k] for k in kinds), self.instrument_id),
                         'units: %s.' % units.describe_kinds(kinds)))

    def fmt_si(self, si, kind):
        if kind == 'pressure':
            return units.fmt(si, 'pressure', 1 if abs(si) < 1e5 else 3)
        if kind == 'flow' and self.param('gas', True):
            return '%s (%.4g sccm)' % (units.fmt(si, 'flow'), si * 6e7)
        return units.fmt(si, kind)

    def range_check(self, ctx, cap, arg, si, kind, what):
        lo, hi = self.arg_range_si(cap, arg)
        sch = self.cap_schema(cap, arg)
        ex_lo = sch.get('exclusiveMinimum')
        if ex_lo is not None:
            u = self.arg_unit(cap, arg)
            ex_lo = units.from_declared(ex_lo, u) if u else ex_lo
            if si <= ex_lo + 1e-15:
                ctx.fail(invalid('OUT_OF_RANGE', '%s %s must be above %s on %s.' % (what, self.fmt_si(si, kind), self.fmt_si(ex_lo, kind), self.instrument_id),
                                 'give a value above %s.' % self.fmt_si(ex_lo, kind)))
        if (lo is not None and si < lo - 1e-12 * max(1, abs(lo))) or (hi is not None and si > hi + 1e-12 * max(1, abs(hi))):
            ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of %s (%s to %s).' % (
                what, self.fmt_si(si, kind), self.instrument_id, 'n/a' if lo is None else self.fmt_si(lo, kind), 'n/a' if hi is None else self.fmt_si(hi, kind)),
                'give a %s from %s to %s.' % (what, 'n/a' if lo is None else self.fmt_si(lo, kind), 'n/a' if hi is None else self.fmt_si(hi, kind))))
        ctx.checks.append('%s within the contract range' % what)

    def then_steps(self, ctx, steps):
        for st in steps or []:
            cond = st.get('unless')
            if cond and self.sv(ctx, cond['slot']) == cond['value']:
                continue
            ctx.call(st['cap'], dict(st.get('args') or {}), st.get('step', st['cap']))

    def reading_si(self, values, spec):
        if not spec:
            return None
        if isinstance(spec, dict):
            return self.slot_si(values, spec)
        v = self.sv(values, spec)
        if not finite(v):
            return None
        return units.from_declared(v, self.slot_unit(spec)) if '#' not in spec else v

    # ------------------------------------------------------------------ summary
    def summary(self, values):
        lines, js = [], {}
        p = self.slot('power')
        if p:
            on = bool(self.sv(values, p))
            js['power'] = on
            if not on:
                return ['power off'], js
        run = self.slot('running')
        if run:
            rv = self.sv(values, run)
            js['running'] = rv
            lines.append('%s: %s' % (self.param('running_word', 'running'), rv if not isinstance(rv, bool) else ('yes' if rv else 'no')))
        sp = self.vmap('setpoint') or {}
        for k in ('pressure', 'flow', 'mass_flow', 'temperature'):
            m = sp.get(k)
            if not m:
                continue
            now = self.reading_si(values, m.get('reading'))
            setv = self.reading_si(values, m.get('set'))
            label = m.get('label', KIND_WORDS[k])
            lines.append('%s %s (set point %s)' % (label, 'n/a' if now is None else self.fmt_si(now, k), 'n/a' if setv is None else self.fmt_si(setv, k)))
            js[k.replace('mass_flow', 'mass_flow') + '_si'] = now
            js[k + '_set_si'] = setv
        for sid in (self.vmap('read') or {}).get('reads') or []:
            if any(sid == (sp.get(k) or {}).get('reading') for k in sp):
                continue
            lines.append('%s = %s' % (sid, self.show(values, sid)))
            js[sid] = self.sv(values, sid)
        for sid in self.param('status_slots', []):
            lines.append('%s = %s' % (sid, self.show(values, sid)))
            js[sid] = self.sv(values, sid)
        return (lines or ['(see `chemlab read %s`)' % self.instrument_id]), js

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        return {
            'setpoint': Verb('setpoint', 'set the flow, pressure, mass flow or temperature set point (the unit says which)', self.h_setpoint,
                             positionals=[Arg('VALUE', 'e.g. 500sccm, 1.5slpm, 2mL/min, 12g/h, 3bar, 50mbar, 140C')],
                             options={'wait': Opt('then wait until the measured value is there (moves lab time)', kind='flag'),
                                      'within': Opt('with --wait: tolerance in the same unit (default: the member\'s)', metavar='DELTA'),
                                      'max': Opt('with --wait: the longest wait (default 30 min)', kind='duration', metavar='TIME')},
                             preconditions=['value within the contract range and the controller\'s full scale', 'the member\'s own requirements (power, supply, vessel)'],
                             errors=['OUT_OF_RANGE, UNIT_NEEDED (usage)', 'POWER_OFF, NO_GAS_SUPPLY, NOT_CONNECTED, ABOVE_FULL_SCALE (refused)',
                                     'NOT_REACHED (refused: --wait ran out)'],
                             example='chemlab run INST setpoint 500sccm', reference=REF),
            'start': Verb('start', 'start the pump, generator, water delivery or measurement', self.h_start,
                          options={'wait': Opt('then wait until it is ready (e.g. evacuated and measuring)', kind='flag'),
                                   'max': Opt('with --wait: the longest wait (default 10 min)', kind='duration', metavar='TIME')},
                          preconditions=['the member\'s own requirements (power, a vessel / part connected, warm)'],
                          errors=['POWER_OFF, NOT_CONNECTED, NO_PART (refused)'], example='chemlab run INST start', reference=REF),
            'stop': Verb('stop', 'stop the pump, generator, water delivery or measurement', self.h_stop, example='chemlab run INST stop', reference=REF),
            'select': Verb('select', 'select the gas (gas table), the valve position or the analyzer inlet gas', self.h_select,
                           positionals=[Arg('GAS|POSITION', 'a gas id, a valve position number, or a calibration gas / "sample"')],
                           options={'wait': Opt('then wait until the valve is in position (moves lab time)', kind='flag')},
                           preconditions=['position within the valve\'s positions'], errors=['UNKNOWN_POSITION (usage)', 'BUSY (refused)'],
                           example='chemlab run INST select 3', reference=REF),
            'dispense': Verb('dispense', 'dispense a volume of water (returns job-N; --wait blocks until it is delivered)', self.h_dispense,
                             positionals=[Arg('VOLUME', 'e.g. 500mL or 1.5L', kind='volume')],
                             options={'rate': Opt('flow rate, e.g. 1L/min (default: the rate already set)', kind='flow', metavar='FLOW'),
                                      'wait': Opt('wait until the volume is delivered', kind='flag'),
                                      'max': Opt('with --wait: the longest wait (default 30 min)', kind='duration', metavar='TIME')},
                             preconditions=['volume within the range', 'a flow rate set (or --rate)', 'supply, receiver and consumables ready'],
                             errors=['OUT_OF_RANGE (usage)', 'NO_RATE, NO_RECEIVER, BUSY (refused)'], long_action=True,
                             example='chemlab run INST dispense 500mL --rate 1L/min --wait', reference=REF),
            'read': Verb('read', 'the measured values (flow, pressure, concentration, leak rate, position, volume) in plain units', self.h_read,
                         readonly=True, options={'save-to': save_to_opt()}, example='chemlab run INST read'),
            'zero': Verb('zero', 'zero the sensor (on the leak detector: background subtraction on / off)', self.h_zero,
                         positionals=[Arg('on|off', 'leak detector only: zero on (default) or off', required=False, choices=ON_OFF)],
                         preconditions=['the member\'s own requirements (zero gas on the inlet, measuring and stable)'],
                         errors=['NO_ZERO_GAS, NOT_MEASURING, NOT_STABLE (refused)'], example='chemlab run INST zero', reference=REF),
            'calibrate': Verb('calibrate', 'calibrate: span / reference / test leak / dispense volume (KEY=VALUE settings)', self.h_calibrate,
                              positionals=[Arg('ACTION', 'the member\'s calibration kind or step (see help)', required=False)],
                              variadic=('KEY=VALUE', 'a calibration setting with its unit'),
                              preconditions=['the reference / span gas really applied (reading near it, not over range)'],
                              errors=['NOT_AT_REFERENCE, NO_SPAN_GAS, OVER_RANGE (refused)', 'UNKNOWN_SETTING (usage)'],
                              example='chemlab run INST calibrate reference=1013mbar', reference=REF),
            'vent': Verb('vent', 'vent / depressurise / open the bleed (vent off closes it again where possible)', self.h_vent,
                         positionals=[Arg('on|off', 'on (default) or off', required=False, choices=ON_OFF)],
                         preconditions=['the member\'s own requirements (no liquid flow on the evaporator)'],
                         errors=['LIQUID_FLOWING (refused)'], example='chemlab run INST vent', reference=REF),
        }

    # ------------------------------------------------------------------ setpoint
    def h_setpoint(self, ctx, p):
        maps = self.vmap('setpoint')
        kinds = self.kinds()
        kind, si = self.parse_value(ctx, p.raw['VALUE'], kinds, 'set point')
        m = maps[kind]
        cap, arg = m['cap'], m['arg']
        self.range_check(ctx, cap, arg, si, kind, '%s set point' % KIND_WORDS[kind])
        fs = m.get('full_scale')
        if fs:
            lim = self.reading_si(ctx, fs)
            if finite(lim):
                ctx.check(si <= lim * (1 + 1e-9), 'ABOVE_FULL_SCALE', 'the %s set point %s is above the full scale of %s (%s).' % (
                    KIND_WORDS[kind], self.fmt_si(si, kind), self.instrument_id, self.fmt_si(lim, kind)),
                    'give at most %s.' % self.fmt_si(lim, kind), label='within the full scale')
        self.requirements(ctx, 'setpoint')
        self.requirements(ctx, 'setpoint:' + kind)
        args = dict(m.get('args') or {})
        args[arg] = round(self.arg_value(cap, arg, si), 15)
        if self.cap_schema(cap, arg).get('type') == 'integer':
            args[arg] = int(round(args[arg]))
        if p.get('wait') and m.get('needs_running') and self.slot('running'):
            rv = self.sv(ctx, self.slot('running'))
            ctx.check(rv in ((self.vmap('start') or {}).get('running_values') or [True]), 'NOT_RUNNING',
                      '%s is not running (%s), so it will not reach %s.' % (self.instrument_id, self.param('running_word', 'state') + ': ' + str(rv),
                                                                          self.fmt_si(si, kind)),
                      'start it first: `chemlab run %s start`, then set the set point with --wait.' % self.instrument_id, label='running')
        before = self.reading_si(ctx, m.get('set'))
        ctx.call(cap, args, 'set the %s set point to %s' % (KIND_WORDS[kind], self.fmt_si(si, kind)),
                 readback=(lambda v: None if not finite(self.reading_si(ctx, m['set'])) or abs(self.reading_si(ctx, m['set']) - si) <= 1e-9 * max(1.0, abs(si))
                           else 'set point %s' % self.fmt_si(self.reading_si(ctx, m['set']), kind)) if m.get('set') else None)
        self.then_steps(ctx, m.get('then'))
        ref = m.get('pressure_ref')
        text = 'ok: %s %s set point %s%s (was %s)' % (self.instrument_id, KIND_WORDS[kind], self.fmt_si(si, kind),
                                                     (' %s' % ref) if ref else '', 'n/a' if before is None else self.fmt_si(before, kind))
        now = self.reading_si(ctx, m.get('reading'))
        if now is not None:
            text += '; now %s' % self.fmt_si(now, kind)
        js = {'kind': kind, 'set_point_si': si, 'was_si': before, 'reading_si': now}
        if ref:
            js['pressure_reference'] = ref
        notes = list(m.get('notes') or [])
        if p.get('wait') and not ctx.dry_run:
            r = self.wait_value(ctx, kind, m, si, p)
            text += '\n' + r['text']
            js.update(r['json'])
        return {'text': text + '\n', 'json': js, 'notes': notes}

    def wait_value(self, ctx, kind, m, target, p):
        if not m.get('reading'):
            ctx.fail(unsupported('NO_READING', '%s has no %s reading to wait for.' % (self.instrument_id, KIND_WORDS[kind]), 'leave out --wait.'))
        if p.raw.get('within'):
            tol = parse_quantity(p.raw['within'], kind, '--within')
        else:
            tol = max(float(m.get('tol_abs', 0.0)), float(m.get('tol_rel', 0.02)) * abs(target))
        max_s = p.get('max') or 1800.0

        def there(view):
            now = self.reading_si(ctx, m['reading'])
            return now is not None and abs(now - target) <= tol + 1e-12
        t0 = ctx.now()
        if not there(ctx.view):
            ctx.advance(max_s, until=there, step='wait until %s %s ± %s' % (KIND_WORDS[kind], self.fmt_si(target, kind), self.fmt_si(tol, kind)),
                        poll_s=float(self.param('poll_s', 2.0)))
        now = self.reading_si(ctx, m['reading'])
        if not there(ctx.view):
            ctx.fail(refused('NOT_REACHED', '%s %s is %s after %s; %s ± %s was not reached.' % (
                self.instrument_id, KIND_WORDS[kind], 'n/a' if now is None else self.fmt_si(now, kind), units.fmt_s(ctx.now() - t0),
                self.fmt_si(target, kind), self.fmt_si(tol, kind)),
                'wait longer (`--max`), or check `chemlab run %s status` (running? supply? leak?).' % self.instrument_id,
                detail={'reading_si': now, 'target_si': target}))
        return {'text': 'reached %s (target %s ± %s) after %s (t = %.0f s)' % (self.fmt_si(now, kind), self.fmt_si(target, kind), self.fmt_si(tol, kind),
                                                                            units.fmt_s(ctx.now() - t0), ctx.now()),
                'json': {'reached': True, 'reading_si': now, 'waited_s': ctx.now() - t0}}

    # ------------------------------------------------------------------ start / stop
    def h_start(self, ctx, p):
        m = self.vmap('start')
        self.requirements(ctx, 'start')
        run = self.slot('running')
        if run and m.get('running_values') and self.sv(ctx, run) in m['running_values']:
            return {'text': 'ok: %s is already %s\n' % (self.instrument_id, self.sv(ctx, run)), 'json': {'changed': False, 'state': self.sv(ctx, run)}}
        ctx.call(m['cap'], dict(m.get('args') or {}), m.get('step', 'start (%s)' % m['cap']))
        text = 'ok: %s started' % self.instrument_id
        js = {'changed': True}
        if run:
            rv = self.sv(ctx, run)
            text += ' (%s: %s)' % (self.param('running_word', 'state'), ('yes' if rv else 'no') if isinstance(rv, bool) else rv)
            js['state'] = self.sv(ctx, run)
        ready = m.get('ready')
        if p.get('wait') and not ctx.dry_run:
            if not ready:
                ctx.fail(unsupported('NO_READY_STATE', '%s has no ready state to wait for.' % self.instrument_id, 'leave out --wait.'))
            t0 = ctx.now()

            def ok(view):
                return self.sv(ctx, ready['slot']) in ready['values']
            if not ok(ctx.view):
                ctx.advance(p.get('max') or 600.0, until=ok, step='wait until %s is %s' % (ready['slot'], '/'.join(map(str, ready['values']))))
            if not ok(ctx.view):
                ctx.fail(refused('NOT_READY', '%s is not ready after %s (%s = %s).' % (self.instrument_id, units.fmt_s(ctx.now() - t0), ready['slot'],
                                                                                        self.sv(ctx, ready['slot'])),
                                 'wait longer (`chemlab run %s start --wait --max 20min` is safe to repeat) or check `chemlab run %s status`.' % (
                                     self.instrument_id, self.instrument_id)))
            text += '; ready after %s (%s = %s)' % (units.fmt_s(ctx.now() - t0), ready['slot'], self.sv(ctx, ready['slot']))
            js['ready'] = True
        return {'text': text + '\n', 'json': js}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        self.requirements(ctx, 'stop')
        ctx.call(m['cap'], dict(m.get('args') or {}), m.get('step', 'stop (%s)' % m['cap']))
        run = self.slot('running')
        return {'text': 'ok: %s stopped%s\n' % (self.instrument_id, (' (%s: %s)' % (self.param('running_word', 'state'), self.sv(ctx, run))) if run else ''),
                'json': {'state': self.sv(ctx, run) if run else None}}

    # ------------------------------------------------------------------ select
    def h_select(self, ctx, p):
        m = self.vmap('select')
        text = p.raw['GAS|POSITION'].strip()
        off = m.get('off') or {}
        if text.lower() in [w.lower() for w in off.get('words', [])]:
            self.requirements(ctx, 'select')
            ctx.call(off['cap'], dict(off.get('args') or {}), 'back to %s' % text.lower())
            return {'text': 'ok: %s inlet back to %s\n' % (self.instrument_id, self.sv(ctx, m.get('slot')) if m.get('slot') else text), 'json': {'selected': text.lower()}}
        cap, arg = m['cap'], m['arg']
        sch = self.cap_schema(cap, arg)
        if sch.get('type') == 'integer':
            try:
                val = int(text)
            except ValueError:
                ctx.fail(invalid('BAD_POSITION', '%r is not a position number.' % text, 'give a number, e.g. `select 3`.'))
            lo, hi = sch.get('minimum'), sch.get('maximum')
            n = self.sv(ctx, self.slot('positions')) if self.slot('positions') else None
            off_ = self.sv(ctx, self.slot('offset')) if self.slot('offset') else 1
            if finite(n):
                lo, hi = int(off_ or 1), int(off_ or 1) + int(n) - 1
            if (lo is not None and val < lo) or (hi is not None and val > hi):
                ctx.fail(invalid('UNKNOWN_POSITION', '%s has no position %d (positions %s to %s).' % (self.instrument_id, val, lo, hi),
                                 'give a position from %s to %s.' % (lo, hi)))
            ctx.checks.append('position within %s..%s' % (lo, hi))
        else:
            enum = self.arg_enum(cap, arg)
            val = text
            pre = m.get('id_prefix')
            if pre and not val.startswith(pre):
                val = pre + val.lower().replace(' ', '_')
            if enum and val not in enum:
                close = [e for e in enum if text.lower() in e.lower()][:4]
                ctx.fail(invalid('UNKNOWN_GAS', '%s has no %s %r.' % (self.instrument_id, m.get('what', 'gas'), text),
                                 ('did you mean %s? ' % ' or '.join(close) if close else '') + 'choices: %s%s.' % (
                                     ', '.join(enum), (' or ' + '/'.join(off.get('words', []))) if off else '')))
        cur = self.sv(ctx, m.get('slot')) if m.get('slot') else None
        if cur is not None and str(cur) == str(val) and m.get('slot_is_value', True):
            return {'text': 'ok: %s is already at %s\n' % (self.instrument_id, val), 'json': {'selected': val, 'changed': False}}
        self.requirements(ctx, 'select')
        busy = m.get('busy')
        if busy:
            pend = self.sv(ctx, busy['pending'])
            ctx.check(pend is None or bool(self.sv(ctx, busy['done'])), 'BUSY',
                      '%s is still moving to position %s.' % (self.instrument_id, pend),
                      'wait until it is in position (`chemlab wait 2`), then select again.', label='not moving')
        args = dict(m.get('args') or {})
        args[arg] = val
        ctx.call(cap, args, 'select %s %s' % (m.get('what', 'gas'), val))
        txt = 'ok: %s %s %s selected' % (self.instrument_id, m.get('what', 'gas'), val)
        js = {'selected': val, 'changed': True}
        ip = m.get('in_position')
        if ip:
            if p.get('wait') and not ctx.dry_run:
                t0 = ctx.now()
                ctx.advance(float(m.get('max_wait_s', 10.0)), until=lambda v: bool(self.sv(ctx, ip)), step='wait until in position', poll_s=1.0)
                if not self.sv(ctx, ip):
                    ctx.fail(refused('NOT_IN_POSITION', '%s did not report "in position" within %s.' % (self.instrument_id, units.fmt_s(ctx.now() - t0)),
                                     'check `chemlab run %s status`; the valve drive may be stuck.' % self.instrument_id))
                txt += '; in position after %s (%s)' % (units.fmt_s(ctx.now() - t0), self.show(ctx, m['slot']) if m.get('slot') else '')
                js['in_position'] = True
            else:
                txt += '; moving (use --wait, or check `chemlab run %s read`)' % self.instrument_id
        return {'text': txt + '\n', 'json': js}

    # ------------------------------------------------------------------ dispense
    def h_dispense(self, ctx, p):
        m = self.vmap('dispense')
        vol = p.pos[0]
        self.range_check(ctx, m['volume_cap'], m['volume_arg'], vol, 'volume', 'volume')
        rate = p.get('rate')
        if rate is not None:
            self.range_check(ctx, m['rate_cap'], m['rate_arg'], rate, 'flow', 'flow rate')
        else:
            cur = self.reading_si(ctx, m.get('rate_slot'))
            ctx.check(finite(cur) and cur > 0, 'NO_RATE', '%s has no dispense flow rate set.' % self.instrument_id,
                      'give one: `chemlab run %s dispense %s --rate 1L/min` (or `setpoint 1L/min` first).' % (self.instrument_id, p.raw['VOLUME']),
                      label='a flow rate is set')
        self.requirements(ctx, 'dispense')
        ctx.call(m['volume_cap'], {m['volume_arg']: round(self.arg_value(m['volume_cap'], m['volume_arg'], vol), 15)}, 'set the volume to %s' % units.fmt(vol, 'volume'))
        if rate is not None:
            ctx.call(m['rate_cap'], {m['rate_arg']: round(self.arg_value(m['rate_cap'], m['rate_arg'], rate), 15)}, 'set the flow rate to %s' % units.fmt(rate, 'flow'))
        ctx.call(m['start_cap'], dict(m.get('start_args') or {}), 'start dispensing')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        st = m['state']
        ad = self
        final = {}

        def state(c):
            if final:                                                    # a finished dispense keeps its result
                return final['r']
            v = ad.sv(c, st['slot'])
            got = ad.reading_si(c, m.get('delivered'))
            prog = '%s = %s, delivered %s' % (st['slot'], v, 'n/a' if got is None else units.fmt(got, 'volume'))
            js = {'state': v, 'delivered_m3': got}
            if v in st['done']:
                final['r'] = ('succeeded', prog, js)
                return final['r']
            if v in st['failed']:
                final['r'] = ('failed', prog, js)
                return final['r']
            return 'running', prog, js
        stop = None
        if m.get('stop_cap'):
            def stop(c):
                c.call(m['stop_cap'], {}, 'stop dispensing')
                return 'stopped with %s' % m['stop_cap']
        job = StateJob(ctx.core, self, ctx.req, 'dispense %s on %s' % (units.fmt(vol, 'volume'), self.instrument_id), state, stop=stop, max_s=7200.0)
        if p.get('wait'):
            r = job.wait_reply(ctx.core, ctx.req, p.get('max') or 1800.0)
            ctx.refresh()
            if r['json'].get('status') != 'succeeded':
                ctx.fail(refused('NOT_DELIVERED', 'the dispense on %s ended as %s (%s).' % (self.instrument_id, r['json'].get('status'), r['json'].get('state')),
                                 'see `chemlab run %s status` (supply empty? receiver full?).' % self.instrument_id))
            return {'text': 'ok: dispensed %s (%s)\n%s' % (units.fmt(vol, 'volume'), job.handle, r['text']), 'json': dict(r['json'], job=job.handle, volume_m3=vol)}
        return {'text': 'started %s: dispense %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, units.fmt(vol, 'volume'), job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'volume_m3': vol}}

    # ------------------------------------------------------------------ read
    def h_read(self, ctx, p):
        m = self.vmap('read')
        rows = {}
        lines = ['%s at t = %.1f s:' % (self.instrument_id, ctx.now())]
        for sid in m.get('reads') or []:
            v = self.sv(ctx, sid)
            rows[sid] = v
            q = ctx.quality.get(sid.split('#', 1)[0])
            lines.append('  %-36s %s%s' % (sid, self.show(ctx, sid), ('  [%s]' % q) if q and q not in ('measured', 'nominal', 'valid') else ''))
        for line in self.summary(ctx.view)[0]:
            if not any(line.startswith(s + ' =') for s in rows):
                lines.append('  ' + line)
        return {'text': '\n'.join(lines) + '\n', 'json': {'readings': rows, 'view': self.summary(ctx.view)[1]}}

    # ------------------------------------------------------------------ zero
    def h_zero(self, ctx, p):
        m = self.vmap('zero')
        want = (p.pos[0] if p.pos else 'on') == 'on'
        if not want and not m.get('arg'):
            ctx.fail(invalid('NO_ZERO_OFF', '%s has no "zero off"; zero only applies a new zero.' % self.instrument_id, 'write just `zero`.'))
        self.requirements(ctx, 'zero')
        args = dict(m.get('args') or {})
        if m.get('arg'):
            args[m['arg']] = want
        r = ctx.call(m['cap'], args, 'zero %s' % ('on' if want else 'off') if m.get('arg') else 'zero the sensor')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result')
        if r.get('operation_id'):
            job = OperationJob(ctx.core, self, ctx.req, 'zero %s' % self.instrument_id, m['cap'], r['operation_id'])
            return {'text': 'started %s: zero calibration of %s\nfollow it: chemlab status %s | chemlab wait %s\n' % (job.handle, self.instrument_id, job.handle, job.handle),
                    'json': {'job': job.handle, 'result': res}}
        word = ('zero %s' % ('on' if want else 'off')) if m.get('arg') else 'zeroed'
        return {'text': 'ok: %s %s (%s)\n' % (self.instrument_id, word, short(res, 160)), 'json': {'result': res, 'zero': want}}

    # ------------------------------------------------------------------ calibrate
    def h_calibrate(self, ctx, p):
        m = self.vmap('calibrate')
        actions = m.get('actions') or []
        action = p.raw.get('ACTION')
        extra = list(p.extra)
        if action and '=' in action:
            extra.insert(0, action)
            action = None
        if action and action not in actions:
            ctx.fail(invalid('UNKNOWN_ACTION', '%s has no calibration %r.' % (self.instrument_id, action),
                             ('choices: %s.' % ', '.join(actions)) if actions else 'leave it out; give KEY=VALUE settings (see `chemlab help %s calibrate`).' % self.instrument_id))
        if not action and actions:
            action = m.get('default_action', actions[0])
        keys = m.get('keys') or {}
        main, pre = {}, {}
        for item in extra:
            k, eq, v = item.partition('=')
            if not eq or k not in keys:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s calibrate takes %s.' % (self.instrument_id, ', '.join('%s=...' % x for x in keys) or 'no settings'),
                                 'see `chemlab help %s calibrate`.' % self.instrument_id))
            spec = keys[k]
            cap = spec.get('cap', m['cap'])
            for a in (spec['arg'] if isinstance(spec['arg'], list) else [spec['arg']]):
                sch = self.cap_schema(cap, a)
                if spec.get('kind') == 'fraction':
                    val = parse_quantity(v, 'fraction', k)
                    lo, hi = sch.get('minimum'), sch.get('maximum')
                    if (lo is not None and val < lo) or (hi is not None and val > hi):
                        ctx.fail(invalid('OUT_OF_RANGE', '%s=%s is outside %g..%g ppm on %s.' % (k, v, lo * 1e6, hi * 1e6, self.instrument_id), 'give a value in that range.'))
                elif spec.get('kind'):
                    si = parse_quantity(v, spec['kind'], k)
                    self.range_check(ctx, cap, a, si, spec['kind'], k)
                    val = round(self.arg_value(cap, a, si), 15)
                else:
                    val = units.parse_for(v, sch, what=k)
                (main if cap == m['cap'] else pre)[a] = val
        for k, spec in keys.items():
            if spec.get('required_for') and action in spec['required_for'] and spec['arg'] not in main:
                ctx.fail(invalid('MISSING_SETTING', '%s calibrate %s needs %s=VALUE.' % (self.instrument_id, action, k), 'see `chemlab help %s calibrate`.' % self.instrument_id))
            if spec.get('required') and not any(a in main or a in pre for a in (spec['arg'] if isinstance(spec['arg'], list) else [spec['arg']])):
                ctx.fail(invalid('MISSING_SETTING', '%s calibrate needs %s=VALUE.' % (self.instrument_id, k), 'e.g. `chemlab run %s calibrate %s=%s`.' % (
                    self.instrument_id, k, spec.get('example', '...'))))
        self.requirements(ctx, 'calibrate')
        if action:
            self.requirements(ctx, 'calibrate:' + action)
        ref = m.get('near_reading')
        if ref and ref['arg'] in main:
            want = units.from_declared(main[ref['arg']], self.arg_unit(m['cap'], ref['arg']))
            now = self.reading_si(ctx, ref['slot'])
            dev = float(ref.get('max_rel', 0.1))
            ctx.check(finite(now) and abs(now - want) <= dev * max(abs(want), float(ref.get('floor', 100.0))), 'NOT_AT_REFERENCE',
                      'the gauge of %s reads %s, not near the reference %s: it is not at that pressure, so the adjustment would be wrong.' % (
                          self.instrument_id, 'n/a' if now is None else self.fmt_si(now, 'pressure'), self.fmt_si(want, 'pressure')),
                      'bring the gauge to the reference first (for the atmosphere point: `chemlab run %s vent`, wait until it reads about 1 atm), then calibrate.' % self.instrument_id,
                      label='gauge reads near the reference')
        if pre:
            pc = m['pre_cap']
            args = {}
            for a, sid in (m.get('pre_keep') or {}).items():
                args[a] = self.sv(ctx, sid)
            args.update(pre)
            ctx.call(pc, args, 'set the calibration values (%s)' % ', '.join('%s=%s' % kv for kv in pre.items()))
        args = dict(m.get('args') or {})
        if m.get('action_arg'):
            args[m['action_arg']] = action
        args.update(main)
        r = ctx.call(m['cap'], args, 'calibrate%s' % ((' ' + action) if action else ''))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result')
        if r.get('operation_id'):
            job = OperationJob(ctx.core, self, ctx.req, 'calibrate %s' % self.instrument_id, m['cap'], r['operation_id'])
            return {'text': 'started %s: calibration of %s\nfollow it: chemlab status %s | chemlab wait %s\n' % (job.handle, self.instrument_id, job.handle, job.handle),
                    'json': {'job': job.handle, 'result': res}}
        rem = m.get('duration_slot')
        extra_txt = ''
        if rem and finite(self.sv(ctx, rem)):
            extra_txt = '; it takes about %s (`chemlab run %s status`)' % (units.fmt_s(self.sv(ctx, rem)), self.instrument_id)
        return {'text': 'ok: %s calibration%s started/applied: %s%s\n' % (self.instrument_id, (' ' + action) if action else '', short(res, 200), extra_txt),
                'json': {'result': res, 'action': action}}

    # ------------------------------------------------------------------ vent
    def h_vent(self, ctx, p):
        m = self.vmap('vent')
        want = (p.pos[0] if p.pos else 'on') == 'on'
        spec = m.get('on' if want else 'off')
        if spec is None:
            ctx.fail(unsupported('NO_VENT_OFF', '%s has no "vent off": venting ends by itself.' % self.instrument_id, 'write just `vent`.'))
        self.requirements(ctx, 'vent' if want else 'vent:off')
        args = dict(spec.get('args') or {})
        hf = spec.get('hold_from')
        if hf:
            base = self.sv(ctx, hf['slot'] + ('#' + hf['key'] if hf.get('key') else ''))
            hold = (float(base) if finite(base) else float(hf.get('default', 2.0))) + float(hf.get('plus', 1.0))
            lo, hi = self.arg_range(spec['cap'], hf['arg'])
            args[hf['arg']] = min(hold, hi) if hi is not None else hold
        st = self.slot(spec.get('state_role', 'vent'))
        if st and spec.get('already') is not None and self.sv(ctx, st) == spec['already']:
            return {'text': 'ok: %s vent is already %s\n' % (self.instrument_id, 'open' if want else 'closed'), 'json': {'vent': want, 'changed': False}}
        ctx.call(spec['cap'], args, spec.get('step', 'vent' if want else 'close the vent'))
        return {'text': 'ok: %s %s\n' % (self.instrument_id, spec.get('say', 'venting' if want else 'vent closed')), 'json': {'vent': want, 'changed': True}}

    # ------------------------------------------------------------------ refusals
    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code == 'PRECONDITION_FAILED':
            ctx.refresh()
            p = self.slot('power')
            if p and not self.sv(ctx, p):
                return refused('POWER_OFF', '%s is switched off (it answered %s).' % (self.instrument_id, code),
                               'switch it on: `chemlab run %s power on`.' % self.instrument_id)
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)


_ = (json, math, ConditionJob)

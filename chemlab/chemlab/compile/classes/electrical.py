"""Electrical, magnetic and thermoelectric property testers ("electrical"): Hall / van der Pauw system, four-point-probe sheet
resistance mapper, vibrating sample magnetometer, broadband dielectric spectrometer, Seebeck / resistivity tester.  Operator action:
measure electrical or magnetic transport of a specimen.

Verbs: contact on|off|check [KEY=VALUE ...], move [X Y] [--lift Z] [--angle A], set-field B [--rate R] [--wait], set-temp T|off
[--rate R] [--gradient D], temp, wait-temp, method KEY=VALUE ..., calibrate KEY=VALUE ..., start (job-N), stop, result
[--save-to F], analyze [MODEL] KEY=VALUE ... [--save-to F], status.  power, estop, ack and wait come from the shared layer (configs/adapters/shared.json).  Members and
mappings: configs/adapters/electrical.json.

Vendor-neutral terms: ASTM F76 (Hall / van der Pauw: excitation current, contact check, field), ASTM F84 / F1529 (four-point
probe: probe spacing, current, map points, edge exclusion), IEC 60404 / ASTM A894 (magnetometry: field points, sweep rate, moment
calibration), ASTM D150 (dielectric: frequency sweep, AC voltage), SCPI / Keithley TSP style (the QuadPro2 SMU commands stay on
`chemlab call`).  Lake Shore M91: SCPI command set (users manual pp. 48-57, 65-156); the other members have no public command set,
so the verbs follow the standards' vocabulary (manual manifests: page references only).

Method keys (each member maps the ones it has; native argument names pass through; settings not given come from the member's
defaults - its state or the contract's documented example method - and the reply lists them):

    current=0.5mA  range=auto|1mA  vrange=10mV  phase=0.1s  repeats=1        (Hall: current range auto = smallest range >= current)
    point=0mm,20mm (repeatable)  edge=3mm  thickness=1um  calibration=false  (four-point map)
    field=0.2T (repeatable)  loop=0.5T/0.1T  rate=10mT/s  average=0.05s       (VSM field points; loop = +MAX..-MAX..+MAX; a bare number is mT)
    range=10emu  vibrate=on  amplitude=0.5mm  vfreq=82Hz                      (VSM coil and vibration)
    freq=1kHz (repeatable)  sweep=10Hz..100kHz/9  voltage=0.3V  cycles=1      (dielectric frequencies, log sweep)
    time=3s  interval=0.05s  current=10mA  min-delta=0.1K  tolerance=0.05K    (Seebeck / resistivity)
"""
from .. import units
from ..errors import invalid, refused, unsupported
from ..verbs import Arg, Opt, Verb, save_to_opt
from ..jobs import OperationJob
from .thermal_analysis import TATester, fmt_q, to_native

REF_ALL = ('Lake Shore MeasureReady M91 FastHall users manual pp. 11-33 (Hall, contact check), pp. 48-57 (SCPI measurement structure); '
       'Signatone QuadPro2 data sheet pp. 1-6, 8-15 (mapping, probe heads, calibration standards); Lake Shore 8600 VSM catalog '
       'pp. 6-11, 16-21 (sample exchange, field, speed); Novocontrol Alpha-A/Beta specification pp. 4-11 (frequency range, voltage); '
       'Linseis LSR-3 specifications pp. 1-2 and brochure pp. 4-9 (Seebeck, four-terminal resistivity)')


class ElectricalAdapter(TATester):
    CLASS = 'electrical'
    TITLE = 'electrical and magnetic transport'
    ABOUT = ('Instruments that measure electrical, magnetic or thermoelectric transport of a specimen: check or make the probe contact, '
             'move the stage or the sample rod, set the field or the temperature, set the method in plain units, start a run (job-N), '
             'read the result.')
    STANDARDS = ('ASTM F76 (Hall / van der Pauw); ASTM F84 / F1529 (four-point probe); IEC 60404 / ASTM A894 (magnetometry); ASTM D150 '
                 '(dielectric); SCPI (Lake Shore M91)')
    ALWAYS = ('status',)
    UNIT_KINDS = ('current', 'voltage', 'temperature', 'temperature_delta', 'length', 'duration', 'frequency', 'heating_rate')
    ROLE_VIEW = (('temp', 'sample', 'temperature'), ('set', 'set point', 'temperature'), ('control', 'control', 'onoff'),
                 ('delta', 'gradient', 'temperature_delta'), ('field', 'field', 'magnetic_field'), ('field_set', 'field set', 'magnetic_field'),
                 ('moment', 'moment', 'moment'), ('phase', 'run', 'text'), ('stage', 'stage', 'text'), ('points', 'points', 'text'),
                 ('sample', 'sample', 'present'), ('probe', 'probe height', 'length'), ('x', 'stage x', 'length'), ('y', 'stage y', 'length'),
                 ('lift', 'sample lift', 'length'), ('vibration', 'vibration', 'onoff'), ('warmup', 'warmed up', 'yesno'),
                 ('calibration', 'calibration', 'text'), ('sheet', 'last sheet resistance', 'sheet_resistance'),
                 ('voltage', 'voltage', 'voltage'), ('current', 'current', 'current'))
    CODES = {
        'WARMUP_REQUIRED': ('WARMING_UP', '{inst} is still warming up after power-on.', 'wait until it is ready (`chemlab run {inst} status`, `chemlab wait 10min`).'),
        'FIELD_NOT_SETTLED': ('FIELD_NOT_SETTLED', 'the magnetic field of {inst} has not settled at its target.', 'wait a little (`chemlab wait 30`), then retry.'),
        'MAP_REQUIRED': ('NO_MAP', '{inst} has no map points.', 'give them: `chemlab run {inst} method point=0mm,0mm point=20mm,0mm`.'),
        'CALIBRATION_REQUIRED': ('NOT_CALIBRATED', 'the method of {inst} requires a probe calibration, and there is none.',
                                 'calibrate on a standard (`chemlab run {inst} calibrate ref=100ohm/sq`), or set `method calibration=false`.'),
        'STANDARD_REQUIRED': ('NO_STANDARD', 'calibration needs the calibration standard on {inst}, and a specimen is loaded.',
                              'the lab operator mounts the standard; ask for it.'),
        'REFERENCE_TEMPERATURE_RANGE': ('OUT_OF_DATA', 'the specimen\'s reference data of {inst} do not cover the present temperature.',
                                        'measure at a temperature inside the specimen\'s range (see `chemlab run {inst} status`).'),
        'ZERO_EXCITATION': ('NO_EXCITATION', 'the excitation of {inst} is zero.', 'set a current: `chemlab run {inst} method current=...`.'),
        'STAGE_TRAVEL_OR_CLEARANCE': ('STAGE_BLOCKED', 'the stage of {inst} moves only with the probe raised and inside its travel.',
                                      'lift the probe (`chemlab run {inst} contact off`) and keep |x|, |y| at most 130 mm.'),
        'BUSY': ('BUSY', '{inst} is busy (a run or a movement is in progress).', 'wait (`chemlab run {inst} wait`) or stop the run.'),
        'NOT_RUNNING': ('NOT_RUNNING', '{inst} has nothing running.', 'nothing to stop.'),
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'FIELD_ENVELOPE': ('OUT_OF_RANGE', 'the field is outside what the magnet of {inst} can reach.', 'give a smaller field.'),
        'THERMAL_ENVELOPE': ('OUT_OF_RANGE', 'the temperature is outside the range of the temperature stage of {inst}.', 'give a temperature inside its range.'),
        'FIELD_REVERSAL_REQUIRED': ('NO_FIELD_REVERSAL', 'the Hall analysis needs readings at a positive and a negative field in one record; '
                                    'this record has no field reversal (the field of {inst} is set by the lab).',
                                    'ask the lab operator for a reversing field, or use the record\'s resistivity readings (`chemlab run {inst} result`).'),
        'LOGIN_REQUIRED': ('LOGIN_REQUIRED', 'the source meter of {inst} asks for a login before the software may use it.', 'see `chemlab describe {inst}` (smu.remote_login).'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        REF = self.param('manual') or REF_ALL
        return {
            'contact': Verb('contact', 'lower (on) or lift (off) the probe, or run the contact check (check [KEY=VALUE ...])', self.h_contact,
                            positionals=[Arg('on|off|check', 'on, off or check', choices=['on', 'off', 'check'])], variadic=('KEY=VALUE', 'contact-check settings'),
                            long_action=True, preconditions=['probe over the wafer and the hood closed (four-point probe)', 'warmed up (Hall)'],
                            errors=['NOT_SUPPORTED (on|off without a probe actuator, check without a contact-check routine)', 'WARMING_UP (refused)'],
                            details='contact on|off waits until the probe has stopped; contact check returns job-N.', example='chemlab run INST contact on', reference=REF),
            'move': Verb('move', 'move the stage to X Y, or the sample rod (--lift, --angle)', self.h_move,
                         positionals=[Arg('X', 'stage x, e.g. 10mm', required=False, kind='length'), Arg('Y', 'stage y, e.g. -5mm', required=False, kind='length')],
                         options={'lift': Opt('sample rod height, e.g. 0mm (measuring position)', kind='length', metavar='Z'),
                                  'angle': Opt('sample rotation, e.g. 90deg', kind='angle', metavar='ANGLE')},
                         long_action=True, preconditions=['inside the travel', 'probe raised (stage moves)'], errors=['OUT_OF_RANGE (usage)', 'PROBE_DOWN (refused)'],
                         details='waits until the movement has ended.', example='chemlab run INST move 20mm 0mm', reference=REF),
            'set-field': Verb('set-field', 'set the magnetic field (ramped), optionally waiting until it is there', self.h_set_field,
                              positionals=[Arg('FIELD', 'e.g. 0.5T, 200mT or 0 (a bare number is mT)')],
                              options={'rate': Opt('ramp rate, e.g. 10mT/s (default: the contract\'s example rate)', metavar='RATE'),
                                       'wait': Opt('wait until the field is there', kind='flag')},
                              preconditions=['inside the field range of the magnet'], errors=['OUT_OF_RANGE (usage)'],
                              example='chemlab run INST set-field 0.5T --rate 10mT/s --wait', reference=REF),
            'set-temp': Verb('set-temp', 'set the specimen temperature (or off)', self.h_set_temp,
                             positionals=[Arg('TEMP|off', 'set point, e.g. 100C (a bare number is °C); off switches the control off')],
                             options={'rate': Opt('ramp rate where the instrument takes one, e.g. 10K/min', kind='heating_rate', metavar='RATE'),
                                      'gradient': Opt('temperature difference across the specimen (Seebeck), e.g. 5K', kind='temperature_delta', metavar='DELTA')},
                             preconditions=['inside the instrument range', 'no run in progress'], errors=['OUT_OF_RANGE (usage)', 'BUSY (refused)'],
                             example='chemlab run INST set-temp 100C --rate 10K/min', reference=REF),
            'temp': Verb('temp', 'the temperatures, field and run state', self.h_temp, readonly=True, example='chemlab run INST temp'),
            'wait-temp': Verb('wait-temp', 'wait until the specimen is at the set point (or --to T), moving lab time', self.h_wait_temp, readonly=True,
                              long_action=True, options={'within': Opt('tolerance (default about 0.5 K)', kind='temperature_delta', metavar='DELTA'),
                                                         'max': Opt('longest wait (default 1 h)', kind='duration', metavar='TIME'),
                                                         'to': Opt('wait for this temperature instead of the set point', kind='temperature', metavar='TEMP')},
                              errors=['CONTROL_OFF, NOT_REACHED (refused)'], example='chemlab run INST wait-temp --within 0.5K'),
            'method': Verb('method', 'set the measurement: KEY=VALUE ... (class keys with units: current=0.5mA point=0mm,20mm field=0.2T freq=1kHz ...)',
                           self.h_method, variadic=('KEY=VALUE', 'a setting'), preconditions=['no run in progress', 'every value inside the range'],
                           errors=['UNKNOWN_SETTING, MISSING_SETTING, OUT_OF_RANGE, BAD_CHOICE (usage)', 'BUSY (refused)'],
                           details=self.method_details(), example='chemlab run INST method current=0.5mA', reference=REF),
            'calibrate': Verb('calibrate', 'calibrate: correction factors, or a run on the calibration standard (job-N)', self.h_calibrate,
                              variadic=('KEY=VALUE', 'a calibration setting'), preconditions=['no run in progress', 'the standard mounted (standard runs)'],
                              errors=['MISSING_SETTING, OUT_OF_RANGE (usage)', 'NO_STANDARD (refused)'], example='chemlab run INST calibrate ref=100ohm/sq', reference=REF),
            'start': Verb('start', 'start the measurement (gives job-N)', self.h_start, long_action=True,
                          preconditions=['not already running', 'specimen mounted, warmed up, contact / vibration / coil ready (member requirements)'],
                          errors=['BUSY, WARMING_UP, NO_MAP, NOT_CALIBRATED (refused)'], example='chemlab run INST start', reference=REF),
            'stop': Verb('stop', 'stop the measurement', self.h_stop, errors=['NOT_RUNNING (refused)'], example='chemlab run INST stop'),
            'result': Verb('result', 'the record of the last measurement', self.h_result, readonly=True, options={'save-to': save_to_opt()},
                           example='chemlab run INST result --save-to hall.json'),
            'analyze': self.analyze_verb(),
        }

    def method_details(self):
        m = (self.map.get('verbs') or {}).get('method') or {}
        specs = m.get('calls') or ([m] if m else [])
        lines = []
        for sp in specs:
            for k, ks in sorted((sp.get('keys') or {}).items()):
                extra = ' (repeatable)' if ks.get('list') or ks.get('record') else ' (MAX/STEP)' if ks.get('loop') else ''
                lines.append('  %-12s -> %s.%s%s%s' % (k + '=', sp['cap'], ks['arg'], (' (%s)' % ks['kind']) if ks.get('kind') else '', extra))
        if not lines:
            return None
        return 'class keys on this instrument:\n' + '\n'.join(lines) + '\nnative argument names pass through (a bare number is in the declared unit).'

    # ------------------------------------------------------------------ contact
    def h_contact(self, ctx, p):
        m = self.vmap('contact')
        what = p.pos[0]
        if what == 'check':
            spec = m.get('check')
            if not spec:
                ctx.fail(unsupported('NO_CONTACT_CHECK', '%s has no contact-check routine.' % self.instrument_id,
                                     (m.get('check_alternative') or 'see `chemlab describe %s`.' % self.instrument_id).replace('INST', self.instrument_id)))
            if self.running(ctx):
                ctx.fail(refused('BUSY', '%s is running a measurement.' % self.instrument_id, 'wait for it or stop it.'))
            self.need(ctx, 'contact-check')
            calls = self.build_calls(ctx, dict(spec, always=True), p.extra, 'contact') if p.extra else [self._all_defaults(ctx, spec)]
            cap, args, defaults = calls[0]
            r = ctx.call(cap, args, 'run the contact check (%s)' % cap)
            if ctx.dry_run:
                return {'text': '', 'json': {}}
            if r.get('operation_id'):
                job = OperationJob(ctx.core, self, ctx.req, '%s contact check' % self.instrument_id, cap, r['operation_id'], poll_s=float(self.param('job_poll_s', 5.0)))
                return {'text': 'started %s: contact check on %s\nfollow it: chemlab status %s | chemlab wait %s\n%s' % (
                    job.handle, self.instrument_id, job.handle, job.handle, ('settings from defaults: %s\n' % '; '.join(defaults)) if defaults else ''),
                    'json': {'job': job.handle, 'arguments': args, 'defaults': defaults}}
            return {'text': 'ok: contact check done (%s)\n' % r.get('status'), 'json': {'arguments': args}}
        if p.extra:
            ctx.fail(invalid('EXTRA_ARGUMENT', 'contact %s takes no settings.' % what, 'write `contact %s`.' % what))
        act = m.get('probe')
        if not act:
            ctx.fail(unsupported('NO_PROBE', '%s has no probe to lower or lift.' % self.instrument_id,
                                 (m.get('probe_alternative') or 'see `chemlab run %s`.' % self.instrument_id).replace('INST', self.instrument_id)))
        want = what == 'on'
        pos, tgt = act['pos_slot'], act['target_slot']
        down = act.get('down_value', 0.0)
        if abs((ctx.v(pos) or 0) - (ctx.v(tgt) or 0)) < 1e-9 and (abs((ctx.v(pos) or 0) - down) < 1e-9) == want:
            return {'text': 'ok: %s probe already %s\n' % (self.instrument_id, 'down' if want else 'up'), 'json': {'contact': want, 'changed': False}}
        self.need(ctx, 'contact-%s' % what)
        ctx.call(act['cap'], {act['arg']: want}, 'probe %s' % ('down' if want else 'up'))
        waited = self.settle(ctx, lambda c: abs((c.v(pos) or 0) - (c.v(tgt) or 0)) < 1e-9, 'the probe has stopped', float(act.get('max_s', 60)))
        return {'text': 'ok: %s probe %s (after %s)\n' % (self.instrument_id, 'down on the specimen' if want else 'lifted', units.fmt_s(waited)),
                'json': {'contact': want, 'changed': True}}

    def _all_defaults(self, ctx, spec):
        """A settings call with every argument from the member's defaults (contact check with no settings given)."""
        cap = spec['cap']
        args, used = {}, []
        for arg in self.required(cap):
            val, why = self._default(ctx, cap, arg, (spec.get('defaults') or {}).get(arg, 'example'), args, None)
            args[arg] = val
            used.append('%s=%s (%s)' % (arg, val, why))
        return cap, args, used

    # ------------------------------------------------------------------ move
    def h_move(self, ctx, p):
        m = self.vmap('move')
        xy = [x for x in p.pos if x is not None]
        if len(xy) == 1:
            ctx.fail(invalid('MISSING_ARGUMENT', 'move needs both X and Y.', 'e.g. `chemlab run %s move 10mm 0mm`.' % self.instrument_id))
        if not xy and p.get('lift') is None and p.get('angle') is None:
            ctx.fail(invalid('NOTHING_TO_DO', 'move needs X Y, --lift or --angle.', 'see `chemlab help %s move`.' % self.instrument_id))
        if self.running(ctx):
            ctx.fail(refused('BUSY', '%s is running a measurement.' % self.instrument_id, 'wait for it or stop it.'))
        plan = []
        if xy:
            s = m.get('xy')
            if not s:
                ctx.fail(unsupported('NO_STAGE', '%s has no XY stage.' % self.instrument_id, 'leave out X Y.'))
            props = self.props(s['cap'])
            plan.append(('xy', s, {s['x_arg']: to_native(p.raw['X'], props.get(s['x_arg']) or {}, 'length', 'X'),
                                   s['y_arg']: to_native(p.raw['Y'], props.get(s['y_arg']) or {}, 'length', 'Y')}))
        for opt, kind in (('lift', 'length'), ('angle', 'angle')):
            if p.get(opt) is None:
                continue
            s = m.get(opt)
            if not s:
                ctx.fail(unsupported('NO_' + opt.upper(), '%s has no sample %s axis.' % (self.instrument_id, opt), 'leave out --%s.' % opt))
            plan.append((opt, s, {s['arg']: to_native(p.raw[opt], self.props(s['cap']).get(s['arg']) or {}, kind, '--' + opt)}))
        for name, s, args in plan:
            self.need(ctx, 'move-' + name)
        done = {}
        for name, s, args in plan:
            ctx.call(s['cap'], args, 'move %s to %s' % (name, ', '.join('%s=%.6g' % kv for kv in args.items())))
            if s.get('busy'):
                test = self.moving_done(s['busy'])
            else:
                test = (lambda s_: (lambda c: abs((c.v(s_['pos_slot']) or 0) - (c.v(s_['target_slot']) or 0)) < 1e-9))(s)
            done[name] = self.settle(ctx, test, 'the %s movement has ended' % name, float(s.get('max_s', 120)))
        text = 'ok: %s moved (%s)\n' % (self.instrument_id, '; '.join('%s after %s' % (k, units.fmt_s(v)) for k, v in done.items()))
        return {'text': text, 'json': {'moved': list(done), 'waited_s': done}}

    # ------------------------------------------------------------------ field
    def h_set_field(self, ctx, p):
        m = self.vmap('set-field')
        props = self.props(m['cap'])
        b = to_native(p.raw['FIELD'], props.get(m['arg']) or {}, 'magnetic_field', 'field')
        args = {m['arg']: b}
        if m.get('rate_arg'):
            if p.raw.get('rate'):
                args[m['rate_arg']] = to_native(p.raw['rate'], props.get(m['rate_arg']) or {}, 'field_rate', '--rate')
            else:
                args[m['rate_arg']] = self.example(m['cap']).get(m['rate_arg'])
                ctx.notes.append('rate not given: %s (the contract\'s example)' % fmt_q(args[m['rate_arg']], 'field_rate'))
        self.need(ctx, 'set-field')
        if self.running(ctx):
            ctx.fail(refused('BUSY', '%s is running a measurement; the field follows the method now.' % self.instrument_id, 'wait for it or stop it.'))
        ctx.call(m['cap'], args, 'set the field to %s' % fmt_q(b, 'magnetic_field'))
        text = 'ok: %s field set point %s; now %s\n' % (self.instrument_id, fmt_q(b, 'magnetic_field'), fmt_q(self.role(ctx, 'field'), 'magnetic_field'))
        out = {'field_t': b}
        if p.get('wait') and not ctx.dry_run:
            tol = float(self.param('field_tolerance_t', 1e-4))
            target = self.role(ctx, 'field_set')
            target = b if target is None else target
            waited = self.settle(ctx, lambda c: c.v(self.slot('field')) is not None and abs(c.v(self.slot('field')) - target) <= tol,
                                 'the field is at %s' % fmt_q(target, 'magnetic_field'), float(self.param('field_max_s', 3600)))
            text += 'reached %s after %s\n' % (fmt_q(self.role(ctx, 'field'), 'magnetic_field'), units.fmt_s(waited))
            out['waited_s'] = waited
        return {'text': text, 'json': out}

    # ------------------------------------------------------------------ method: the Hall current range
    def _default(self, ctx, cap, arg, d, args, pg):
        if isinstance(d, dict) and d.get('auto_range_for'):
            cur = args.get(d['auto_range_for'])
            enum = sorted(self.props(cap).get(arg, {}).get('enum') or [])
            fit = [e for e in enum if cur is not None and e >= abs(cur) * (1 - 1e-12)]
            if fit:
                return fit[0], 'the smallest range that holds %s' % fmt_q(cur, 'current')
        return TATester._default(self, ctx, cap, arg, d, args, pg)

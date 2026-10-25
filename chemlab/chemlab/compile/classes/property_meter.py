"""Property-meter class ("property_meter"): benchtop meters that measure one property of a sample - density (DMA 5002),
refractive index (Abbemat), optical rotation (P-4200), pH / conductivity (914), water activity (AQUALAB 4TE), turbidity
(TL2300), osmolality (K-7400S), loss on drying (HX204), melting range (MP70).

Verbs: set-temp T [--wait], method KEY=VALUE ..., calibrate [NAME] [--standard V], check [NAME] [--reference V], zero,
measure [--wait] [--max T], result [--channel N] [--save-to F], clean [--volume V] [--wait], status.  Power, open/close of lids,
stop and wait come from the shared layer (`chemlab run INST power on`, `close lid`, `stop`); `chemlab call` stays available.

One native path per member (COMPILE_GUIDE 5.2): measure / result use the acquisition core (acquisition.start + the member's
X.measure query, a declared path exception) where a member also has a touchscreen path (DMA 5002 / Abbemat measurement.start,
HX204 measurement.*, MP70 analysis.*, P-4200 measure.start).  The 914 measures continuously: measure waits for its endpoint.

Method dictionary (class keys; a member maps the ones it has, other native argument names pass through):

    mode=...            measuring mode (enum of the member)          temp=T         measuring / compensation temperature
    time=T              drying time (HX204)                           basis=wet|dry  result basis (HX204)
    start=T end=T rate=R  melting program (MP70; rate in K/min)       criterion=...  melting criterion (MP70)
    name=ID             stored method id (MP70; default chemlab)
    integration=T repeats=N conc=C path=L   polarimeter (C in g/L, L in mm)
    stability=D tolerance=dT readings=N     water-activity equilibrium criterion
    ratio=on|off average=1|10               turbidimeter signal options
    volume=50uL|150uL freeze=T cool=T interval=T   osmometer head and cooling program
    probe=ph|conductance|do hold=T tolerance=F interval=T temp=T|auto   914 channel, endpoint criterion, recording, compensation

Arguments the agent does not give are kept from the instrument's own state where it reports them; otherwise the verb names the
keys it still needs (nothing is invented).  Composite verbs (calibrate, check, zero) start the member's procedure, wait for its
run (lab time moves) and finish it (accept, decide, quartz factor) - each step is a native call listed in --dry-run.

Standards: ASTM D4052 / OIML R 22 (U-tube density), ASTM D1218 + USP <831> (refractive index), USP <781> (optical rotation),
USP <791> (pH), ISO 18787 + USP <922> (water activity), ISO 7027 / EPA 180.1 (turbidity), USP <785> (osmolality), USP <731>
(loss on drying), USP <741> (melting range); PyLabRobot / SiLA 2 measurement services (start, result).
"""
from .. import units
from ..base import finite, short
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb, save_to_opt, target_opt
from .run_steps import EXTRA, StepAdapter, fmt_value, kind_example, parse_quantity

REF = ('DMA M manual pp.24-33 (checks, adjustments), 39-42 (measuring); Abbemat 350/550 manual pp.23-36 and the 5201 product page; '
       'P-2000 iRM manual pp.12-24 and P-4000 application notes; 914 manual pp.33-47; AQUALAB 4 manual pp.6-23, 44-52; TL2300 '
       'manual pp.16-26; K-7400S manual pp.20-27; HX204 reference manual pp.25-62; MP70 user manual pp.17-23, 38-39')


class PropertyMeterAdapter(StepAdapter):
    CLASS = 'property_meter'
    TITLE = 'property meter (measure one property of a sample)'
    ABOUT = ('Benchtop meters that measure one property of a sample (density, refractive index, optical rotation, pH, water '
             'activity, turbidity, osmolality, loss on drying, melting range): set the cell temperature, set the method, '
             'calibrate and check against standards, zero (blank), measure (a job) and read the result in its units.')
    STANDARDS = ('ASTM D4052, ASTM D1218, USP <781> <791> <922> <785> <731> <741> <831>, ISO 18787, ISO 7027 / EPA 180.1; '
                 'SiLA 2 / PyLabRobot measurement services (start, result)')
    ALWAYS = ('status',)
    UNIT_KINDS = ('temperature', 'duration', 'density', 'volume', 'angle', 'heating_rate', 'length')
    PATH_GROUPS = (('measure', 'result'),)
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'BUSY': ('BUSY', '{inst} is busy with a run.', 'wait for it (`chemlab run {inst} status`) or stop it (`chemlab run {inst} stop`).'),
        'NOT_ALLOWED': ('NOT_ALLOWED', '{inst} does not allow this now ({cap}).', 'see `chemlab run {inst} status`.'),
    }

    # ------------------------------------------------------------------ helpers
    def c(self, k):
        return units.fmt(k, 'temperature') if finite(k) else 'n/a'

    def busy(self, values):
        ph = self.role(values, 'phase')
        return ph in (self.param('busy_values') or ['running'])

    def summary(self, values):
        p = self.slot('power')
        on = self.sval(values, p) if p else True
        if p and not on:
            return ['power off'], {'power': False}
        tk, sk = self.slot_si(values, self.slot('temp')), self.slot_si(values, self.slot('set'))
        val = self.sval(values, self.slot('value'))
        label = self.param('value_label', 'value')
        unit = self.param('value_unit')
        line = '%s %s' % (label, fmt_value(val, unit, self.param('value_decimals')) if val is not None else 'none yet')
        more = []
        if finite(tk) or finite(sk):
            more.append('cell %s (set %s)' % (self.c(tk), self.c(sk)))
        ph = self.role(values, 'phase')
        if ph is not None:
            more.append('run %s' % ph)
        for role in ('ready', 'sample'):
            s = self.slot(role)
            if s:
                more.append('%s %s' % (self.param('%s_word' % role, role), 'yes' if self.sval(values, s) else 'no'))
        js = {'power': True, 'value': val, 'value_label': label, 'value_unit': unit, 'temperature_k': tk, 'set_point_k': sk, 'phase': ph}
        return [line] + (['; '.join(more)] if more else []), js

    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code in ('PRECONDITION_FAILED', 'BUSY'):
            ctx.refresh()
            p = self.slot('power')
            if p and not ctx.v(p):
                return refused('POWER_OFF', '%s is switched off (it answered %s).' % (self.instrument_id, code),
                               'switch it on: `chemlab run %s power on`.' % self.instrument_id)
            if self.busy(ctx):
                return refused('BUSY', '%s is busy with a run (%s = %s).' % (self.instrument_id, self.slot('phase'), self.role(ctx, 'phase')),
                               'wait for it (`chemlab run %s status`) or stop it (`chemlab run %s stop`).' % (self.instrument_id, self.instrument_id))
        return StepAdapter.explain_refusal(self, ctx, cap, args, response)

    def class_token(self, ctx, name, cap, arg, tokens):
        if name == 'offset_to_standard':                       # AQUALAB: linear offset so that the standard reads its value
            m = self.vmap('calibrate')
            reading = self.sval(ctx, m['reading_slot'])
            old = self.sval(ctx, m['offset_slot']) or 0.0
            if not finite(reading):
                ctx.fail(refused('NO_READING', '%s has no equilibrium reading of the standard.' % self.instrument_id,
                                 'measure the standard first (`chemlab run %s measure --wait`), then calibrate.' % self.instrument_id))
            std = tokens['standard'][0]
            new = round(old + (std - reading), 6)
            lo, hi = self.arg_range(cap, arg)
            if (lo is not None and new < lo) or (hi is not None and new > hi):
                ctx.fail(refused('OFFSET_TOO_LARGE', 'the standard reads %.4f on %s, so the offset would be %+.4f, outside %+.3f..%+.3f.' % (
                    reading, self.instrument_id, new, lo, hi), 'check that the standard is the one you named and that the chamber is clean; '
                    'measure it again.'))
            ctx.notes.append('offset %+.4f -> %+.4f (the standard read %.4f, certified %.4f)' % (old, new, reading, std))
            return new
        if name == 'apply_if_recommended':                     # Anton Paar: apply the adjustment only when the instrument recommends it
            if ctx.dry_run:
                return True                                    # decided from the adjustment result when it runs
            st = self.sval(ctx, self.vmap('calibrate')['state_slot'])
            if not isinstance(st, dict):
                ctx.fail(refused('NO_ADJUSTMENT', '%s has no adjustment result to apply.' % self.instrument_id, 'run calibrate again.'))
            ok = bool(st.get('ok')) and st.get('recommend') == 'Apply'
            if not ok:
                tokens['_rejected'] = st
            return ok
        if name == 'standards_one_point':                     # Abbemat one-point adjustment: the standard at the cell temperature
            k = self.slot_si(ctx, self.slot('set'))
            nd = tokens['standard'][0]
            item = (self.cap_schema(cap, arg).get('items') or {}).get('properties') or {}
            nds = next((x for x in (item.get('nd') or {}).get('anyOf', [item.get('nd') or {}]) if x.get('type') == 'number'), {})
            if (nds.get('minimum') is not None and nd < nds['minimum']) or (nds.get('maximum') is not None and nd > nds['maximum']):
                ctx.fail(invalid('OUT_OF_RANGE', '--standard %s is outside nD %s..%s of %s.' % (nd, nds.get('minimum'), nds.get('maximum'), self.instrument_id),
                                 'give the standard\'s refractive index, e.g. 1.33299 for water at 20 °C.'))
            return [{'name': tokens.get('name') or 'Standard', 'temperature_c': round(k - 273.15, 6), 'nd': nd}]
        return StepAdapter.class_token(self, ctx, name, cap, arg, tokens)

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        temp = Arg('TEMP', 'measuring temperature, e.g. 20C (a bare number is °C)', kind='temperature')
        return {
            'set-temp': Verb('set-temp', 'set the measuring-cell (or drying / furnace) temperature', self.h_set_temp, positionals=[temp],
                             options={'wait': Opt('then wait until the cell is within the tolerance (moves lab time)', kind='flag'),
                                      'max': Opt('with --wait: the longest wait (default 30 min)', kind='duration', metavar='TIME')},
                             preconditions=['set point within the instrument range (contract)', 'power on', 'no run in progress',
                                            'the member\'s own step (e.g. HX204 whole °C)'],
                             errors=['OUT_OF_RANGE, BAD_STEP (usage)', 'POWER_OFF, BUSY, NOT_REACHED (refused)'],
                             example='chemlab run INST set-temp 20C --wait', reference=REF),
            'method': Verb('method', 'set the measuring method: KEY=VALUE ... (class keys with units, or native argument names)',
                           self.h_method, variadic=('KEY=VALUE', 'a setting'),
                           preconditions=['no run in progress', 'every value within its range; keys the instrument needs and does not report must be given'],
                           errors=['UNKNOWN_SETTING, MISSING_SETTING, OUT_OF_RANGE, BAD_CHOICE (usage)', 'BUSY (refused)'],
                           example='chemlab run INST method mode=density', reference=REF,
                           details='class keys: mode temp time basis start end rate criterion name integration repeats conc path stability '
                                   'tolerance readings ratio average volume freeze cool interval probe hold (see the class description); '
                                   'other native argument names pass through.'),
            'calibrate': Verb('calibrate', 'calibrate / adjust against a standard (runs the procedure, waits for it, applies it)',
                              self.h_calibrate, positionals=[Arg('NAME', 'which adjustment, where the member has several', required=False)],
                              options={'standard': Opt('the certified value of the standard (units of the property, e.g. 0.760, 20NTU, '
                                                       '400mOsmol/kg, 10deg, 1.33299)', metavar='VALUE'),
                                       'max': Opt('longest wait for the procedure (default 15 min)', kind='duration', metavar='TIME')},
                              preconditions=['the standard is in the cell (the lab operator fills it)', 'a stable reading where the member needs one',
                                             'warm-up done where the member needs it', 'no run in progress'],
                              errors=['MISSING_STANDARD, OUT_OF_RANGE (usage)', 'NO_SAMPLE, UNSTABLE, WARMING_UP, CALIBRATION_REJECTED, '
                                      'OFFSET_TOO_LARGE, RUN_FAILED (refused)'],
                              example='chemlab run INST calibrate --standard 1.33299', reference=REF, long_action=True),
            'check': Verb('check', 'verify the instrument against a reference standard (reports the deviation, changes nothing)',
                          self.h_check, positionals=[Arg('NAME', 'which check / test, where the member has several', required=False)],
                          options={'reference': Opt('the reference value (units of the property)', metavar='VALUE'),
                                   'max': Opt('longest wait (default 15 min)', kind='duration', metavar='TIME')},
                          preconditions=['the reference is in the cell', 'a finished / stable reading where the member needs one'],
                          errors=['MISSING_REFERENCE, UNKNOWN_NAME (usage)', 'NO_READING, WARMING_UP, RUN_FAILED (refused)'],
                          example='chemlab run INST check --reference 0.7893g/cm3', reference=REF, long_action=True),
            'zero': Verb('zero', 'zero the instrument on a blank (solvent-only cell)', self.h_zero,
                         options={'max': Opt('longest wait (default 10 min)', kind='duration', metavar='TIME')},
                         preconditions=['the blank is in the cell', 'method set', 'lid closed, lamp on'],
                         errors=['RUN_FAILED (refused)'], example='chemlab run INST zero', reference=REF, long_action=True),
            'measure': Verb('measure', 'measure the sample in the cell (a job; --wait blocks and prints the result)', self.h_measure,
                            options={'wait': Opt('wait for the result (moves lab time) and print it', kind='flag'),
                                     'max': Opt('with --wait: the longest wait (default: the member\'s run time limit)', kind='duration', metavar='TIME')},
                            preconditions=['power on', 'sample in the cell (the lab operator fills it)', 'lid closed', 'no run in progress',
                                           'the member\'s own (warm-up, calibration, method, temperature)'],
                            errors=['NO_SAMPLE, LID_OPEN, BUSY, WARMING_UP, NOT_CALIBRATED (refused)'],
                            example='chemlab run INST measure --wait', reference=REF, long_action=True),
            'result': Verb('result', 'the measured value with its unit (and the member\'s other result fields)', self.h_result, readonly=True,
                           options={'save-to': save_to_opt(), 'channel': target_opt('channel')},
                           errors=['NO_RESULT (refused: no finished measurement)'],
                           example='chemlab run INST result --save-to result.json', reference=REF),
            'clean': Verb('clean', 'clean the measuring cell with cleaning liquid (a job; --wait blocks)', self.h_clean,
                          options={'volume': Opt('cleaning liquid volume (default 10mL)', kind='volume', metavar='VOLUME', default='10mL'),
                                   'wait': Opt('wait until the cleaning ends', kind='flag'),
                                   'max': Opt('with --wait: the longest wait (default 10 min)', kind='duration', metavar='TIME')},
                          preconditions=['power on', 'no run in progress', 'lid open where the member needs it'],
                          errors=['OUT_OF_RANGE (usage)', 'LID_CLOSED, BUSY (refused)'], example='chemlab run INST clean --volume 5mL --wait',
                          reference=REF, long_action=True),
        }

    def need_power(self, ctx):
        s = self.slot('power')
        if s:
            ctx.check(bool(ctx.v(s)), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                      'switch it on: `chemlab run %s power on`.' % self.instrument_id, label='power on')

    def need_idle(self, ctx):
        if self.slot('phase'):
            ctx.check(not self.busy(ctx), 'BUSY', '%s is busy with a run (%s = %s).' % (self.instrument_id, self.slot('phase'), self.role(ctx, 'phase')),
                      'wait for it (`chemlab run %s status`) or stop it (`chemlab run %s stop`).' % (self.instrument_id, self.instrument_id),
                      label='no run in progress')

    # ------------------------------------------------------------------ set-temp
    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        lo, hi = self.arg_range_si(m['cap'], m['arg'])
        if (lo is not None and k < lo - 1e-9) or (hi is not None and k > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'set point %s is outside the range of %s (%s to %s).' % (self.c(k), self.instrument_id, self.c(lo), self.c(hi)),
                             'give a temperature from %s to %s.' % (self.c(lo), self.c(hi))))
        ctx.checks.append('set point within %s..%s' % (self.c(lo), self.c(hi)))
        step = self.param('step_c')
        if step:
            n = (k - 273.15) / float(step)
            if abs(n - round(n)) > 1e-6:
                ctx.fail(invalid('BAD_STEP', '%s takes set points in %g °C steps, not %s.' % (self.instrument_id, step, p.raw['TEMP']),
                                 'round it, e.g. %gC.' % (round(n) * float(step))))
        if p.get('wait') and not self.param('wait_ok', True):
            ctx.fail(invalid('NO_WAIT', 'set-temp --wait is not available on %s: %s.' % (self.instrument_id, self.param('wait_reason', 'it heats only during a run')),
                             'set it without --wait; the run uses it.'))
        self.need_power(ctx)
        self.need_idle(ctx)
        self.requirements(ctx, 'set-temp')
        before = self.slot_si(ctx, self.slot('set'))
        args = self.build_args(ctx, m, {m['arg']: round(self.arg_value(m['cap'], m['arg'], k), 10)}, 'set-temp')
        ctx.call(m['cap'], args, 'set the temperature to %s' % self.c(k),
                 readback=lambda v: None if not finite(self.slot_si(ctx, self.slot('set'))) or abs(self.slot_si(ctx, self.slot('set')) - k) < 0.051
                 else 'set point %s' % self.c(self.slot_si(ctx, self.slot('set'))))
        text = 'ok: %s set to %s (was %s); now %s\n' % (self.instrument_id, self.c(k), self.c(before), self.c(self.slot_si(ctx, self.slot('temp'))))
        js = {'set_point_k': k, 'set_point_c': k - 273.15, 'was_k': before, 'temperature_k': self.slot_si(ctx, self.slot('temp'))}
        if p.get('wait') and not ctx.dry_run:
            tol = float(self.param('tolerance_k', 0.1))
            t0 = ctx.now()

            def inside(view):
                tk = self.slot_si(ctx, self.slot('temp'))
                return finite(tk) and abs(tk - k) <= tol + 1e-9
            if not inside(ctx.view):
                ctx.advance(p.get('max') or 1800.0, until=inside, step='wait until %s ± %g K' % (self.c(k), tol), poll_s=float(self.param('poll_s', 5.0)))
            tk = self.slot_si(ctx, self.slot('temp'))
            if not inside(ctx.view):
                ctx.fail(refused('NOT_REACHED', '%s is at %s after %s; %s ± %g K was not reached.' % (self.instrument_id, self.c(tk), units.fmt_s(ctx.now() - t0),
                                                                                                    self.c(k), tol),
                                 'wait longer (`--max 1h`) or check `chemlab run %s status`.' % self.instrument_id, changed='yes'))
            text += 'reached %s after %s of waiting\n' % (self.c(tk), units.fmt_s(ctx.now() - t0))
            js.update({'reached': True, 'temperature_k': tk})
        return {'text': text, 'json': js}

    # ------------------------------------------------------------------ method
    def h_method(self, ctx, p):
        m = self.vmap('method')
        keys = m.get('keys') or {}
        calls = m.get('calls') or []
        order = [c['cap'] for c in calls]
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'keys of %s: %s (see `chemlab help %s method`).' % (
                self.instrument_id, ', '.join(keys) or 'native argument names', self.instrument_id)))
        given = {c: {} for c in order}
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write method KEY=VALUE ... (see `chemlab help %s method`).' % self.instrument_id))
            if k in keys:
                spec = keys[k]
                cap, arg = spec['cap'], spec['arg']
                special = (spec.get('special') or {}).get(v.strip().lower())
                if special is not None:
                    given.setdefault(cap, {}).update(special)
                    continue
                given.setdefault(cap, {})[arg] = self.parse_setting(ctx, cap, arg, v, spec, k)
                given[cap].update(spec.get('also') or {})
            else:
                hit = [c for c in order if k in ((ctx.cap_spec(c).get('input_schema') or {}).get('properties') or {})]
                if not hit or not m.get('passthrough', True):
                    names = sorted(set(keys))
                    ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (self.instrument_id, k),
                                     'settings: %s.' % ', '.join(names)))
                given[hit[0]][k] = self.parse_setting(ctx, hit[0], k, v, {}, k)
        self.need_power(ctx)
        self.need_idle(ctx)
        self.requirements(ctx, 'method')
        done = []
        for spec in calls:
            cap = spec['cap']
            if not given.get(cap) and not spec.get('always'):
                continue
            args = self.build_args(ctx, spec, given.get(cap) or {}, 'method')
            ctx.call(cap, args, 'set %s (%s)' % (cap, ', '.join('%s=%s' % (a, short(x, 30)) for a, x in args.items())))
            done.append({'capability': cap, 'arguments': args})
            for then in spec.get('then') or []:
                targs = dict(then.get('args') or {})
                for a, src in (then.get('from_args') or {}).items():
                    targs[a] = args.get(src)
                ctx.call(then['cap'], targs, 'then %s' % then['cap'])
                done.append({'capability': then['cap'], 'arguments': targs})
        text = 'ok: method of %s set: %s\n' % (self.instrument_id, '; '.join('%s %s' % (d['capability'], short(d['arguments'], 160)) for d in done))
        return {'text': text, 'json': {'calls': done}}

    # ------------------------------------------------------------------ composite procedures
    def tokens_from(self, ctx, p, m):
        tokens = {}
        for opt in ('standard', 'reference'):
            raw = p.get(opt)
            spec = dict(m.get(opt) or {})
            if spec.get('kind_slot'):                                   # the kind follows the selected channel (914: pH / conductance)
                spec['kind'] = (spec.get('kinds') or {}).get(self.sval(ctx, spec['kind_slot']), 'plain')
            if raw is not None:
                if spec.get('fixed') is not None:
                    v = parse_quantity(raw, spec.get('kind', 'plain'), '--' + opt)
                    if abs(v - spec['fixed']) > 1e-9 * max(1.0, abs(spec['fixed'])):
                        ctx.fail(invalid('WRONG_STANDARD', '%s on %s uses a %s standard, not %s.' % (
                            ctx.verb.name, self.instrument_id, spec.get('fixed_text', spec['fixed']), raw),
                            'fill that standard and give --%s %s (or leave it out).' % (opt, spec.get('fixed_text', spec['fixed']))))
                tokens[opt] = (parse_quantity(raw, spec.get('kind', 'plain'), '--' + opt), spec.get('kind', 'plain'))
                g = spec.get('grid')
                if g and abs(tokens[opt][0] / g - round(tokens[opt][0] / g)) > 1e-6:
                    ctx.fail(invalid('BAD_%s' % opt.upper(), '%s takes --%s values in steps of %g %s, not %s.' % (
                        self.instrument_id, opt, g, EXTRA.get(spec.get('kind'), ('',))[0], raw),
                        'round it, e.g. %s.' % (spec.get('example') or kind_example(spec.get('kind', 'plain')))))
            elif spec.get('fixed') is not None:
                tokens[opt] = (spec['fixed'], spec.get('kind', 'plain'))
            elif spec.get('required'):
                ctx.fail(invalid('MISSING_%s' % opt.upper(), '%s on %s needs --%s VALUE (%s).' % (ctx.verb.name, self.instrument_id, opt,
                                                                                               spec.get('help', 'the certified value')),
                                 'e.g. `chemlab run %s %s --%s %s`.' % (self.instrument_id, ctx.verb.name, opt, spec.get('example') or kind_example(spec.get('kind', 'plain')))))
        if m.get('name'):
            tokens['name'] = self.resolve_name(ctx, m['name'], p.pos[0] if p.pos else None)
        elif p.pos:
            ctx.fail(invalid('NO_NAMES', '%s on %s takes no NAME.' % (ctx.verb.name, self.instrument_id), 'leave out %r.' % p.pos[0]))
        return tokens

    def procedure(self, ctx, p, verb, default_max):
        m = self.vmap(verb)
        tokens = self.tokens_from(ctx, p, m)
        self.need_power(ctx)
        if m.get('idle', True):
            self.need_idle(ctx)
        self.requirements(ctx, verb)
        results, reports = self.run_steps(ctx, m['steps'], tokens, verb, float(p.get('max') or m.get('max_s') or default_max))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        rejected = tokens.get('_rejected')
        if rejected is not None:
            ctx.fail(refused('CALIBRATION_REJECTED', 'the adjustment on %s was not accepted (the instrument recommends %s; rows %s); it was rejected '
                                                     'and the old adjustment stays.' % (self.instrument_id, rejected.get('recommend'), short(rejected.get('rows'), 200)),
                             'check that the standard in the cell is the one you named, at the set temperature, and that its value is right; '
                             'then calibrate again.', changed='yes'))
        report = {}
        for cap, res in results:
            if isinstance(res, dict) and res:
                report[cap] = res
        for slot, v in reports:
            report[slot] = v
        lines = ['ok: %s %s done%s' % (self.instrument_id, verb, (' (%s)' % tokens['name']) if tokens.get('name') else '')]
        for k, v in report.items():
            if isinstance(v, dict):
                lines.append('  %s: %s' % (k, ', '.join('%s=%s' % (a, short(b, 60)) for a, b in list(v.items())[:12])))
            else:
                lines.append('  %s = %s' % (k, short(v, 200)))
        verdict = self.verdict(report, m)
        if verdict is not None:
            lines.append('  verdict: %s' % ('passed' if verdict else 'NOT passed'))
        return {'text': '\n'.join(lines) + '\n', 'json': {'report': report, 'passed': verdict, 'name': tokens.get('name')}}

    @staticmethod
    def verdict(report, m):
        key = m.get('pass_key')
        if not key:
            return None
        for v in report.values():
            if isinstance(v, dict) and key in v:
                x = v[key]
                if 'pass_value' in m:
                    return x == m['pass_value']
                return bool(x)
        return None

    def h_calibrate(self, ctx, p):
        return self.procedure(ctx, p, 'calibrate', 900.0)

    def h_check(self, ctx, p):
        return self.procedure(ctx, p, 'check', 900.0)

    def h_zero(self, ctx, p):
        return self.procedure(ctx, p, 'zero', 600.0)

    # ------------------------------------------------------------------ measure / result / clean
    def h_measure(self, ctx, p):
        m = self.vmap('measure')
        self.need_power(ctx)
        self.need_idle(ctx)
        self.requirements(ctx, 'measure')
        tol = self.param('temp_ready_k')
        if tol is not None:
            tk, sk = self.slot_si(ctx, self.slot('temp')), self.slot_si(ctx, self.slot('set'))
            ctx.check(finite(tk) and finite(sk) and abs(tk - sk) <= tol + 1e-9, 'TEMPERATURE_NOT_READY',
                      'the cell of %s is at %s, not within %g K of the set point %s.' % (self.instrument_id, self.c(tk), tol, self.c(sk)),
                      'wait for it: `chemlab run %s set-temp %.2fC --wait`.' % (self.instrument_id, (sk or 273.15) - 273.15), label='cell at temperature')
        if m.get('before'):
            self.run_steps(ctx, m['before'], {}, 'measure', 60.0)
        if m.get('cap'):
            ctx.call(m['cap'], dict(m.get('args') or {}), 'start the measurement (%s)' % m['cap'])
        if ctx.dry_run:
            if p.get('wait'):
                ctx.plan.append({'capability': None, 'arguments': {}, 'step': 'wait for the result (%s)' % (m.get('state') or m.get('until'))['slot']})
            return {'text': '', 'json': {}}
        job = self.state_job(ctx, m, 'measure on %s' % self.instrument_id)
        if not p.get('wait'):
            return {'text': 'started %s: measure on %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s; then `chemlab run %s result`\n' % (
                job.handle, self.instrument_id, job.handle, job.handle, job.handle, self.instrument_id), 'json': {'job': job.handle, 'kind': job.kind}}
        r = self.follow_job(ctx, job, p.get('max') or float(m.get('max_s', 3600)))
        if job.state != 'succeeded':
            ctx.fail(refused('NOT_FINISHED' if job.state == 'running' else 'RUN_FAILED',
                             'the measurement on %s is %s after %s (%s).' % (self.instrument_id, job.state, units.fmt_s(r['json'].get('waited_s') or 0),
                                                                            r['text'].strip().splitlines()[-1][:160]),
                             'follow it with `chemlab wait %s`, or check `chemlab run %s status`.' % (job.handle, self.instrument_id),
                             changed='yes' if ctx.changed else 'no'))
        res = self.read_result(ctx, None)
        return {'text': 'measured on %s after %s of waiting:\n%s' % (self.instrument_id, units.fmt_s(r['json'].get('waited_s') or 0), res['text']),
                'json': dict(res['json'], job=job.handle)}

    def read_result(self, ctx, channel):
        m = self.vmap('result')
        cap = m['cap']
        out_props = ((ctx.cap_spec(cap).get('output_schema') or {}).get('properties') or {})
        targets = [None]
        if m.get('channels') is not None:
            targets = [channel] if channel is not None else list(m['channels'])
        rows, raw = [], {}
        for ch in targets:
            args = dict(m.get('args') or {})
            if ch is not None:
                args[m['channel_arg']] = ch
            r = ctx.call(cap, args, 'read the result (%s)' % cap, query=True)
            res = r.get('result') if isinstance(r.get('result'), dict) else {}
            raw['all' if ch is None else 'channel %s' % (ch + m.get('channel_base', 0))] = res
            rows.append((ch, res))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        key, label = m['value'], m.get('label', m['value'])
        unit = units.declared(out_props.get(key) or {}) or m.get('unit')
        lines, values = [], {}
        for ch, res in rows:
            if m.get('value_keys'):
                key = m['value_keys'].get(res.get(m.get('value_key_field', 'slot')), key)
                unit = units.declared(out_props.get(key) or {}) or m.get('unit')
            v = res.get(key)
            head = '%s%s' % (label, '' if ch is None else ' (channel %d)' % (ch + m.get('channel_base', 0)))
            extra = ['%s=%s' % (k, fmt_value(res.get(k), units.declared(out_props.get(k) or {}))) for k in m.get('extra') or [] if k in res]
            shown = ('%.2f %%' % (v * 100.0)) if (m.get('percent') and finite(v)) else fmt_value(v, unit, m.get('decimals'))
            lines.append('  %s = %s%s' % (head, shown, ('   ' + ', '.join(extra)) if extra else ''))
            values['value' if ch is None else 'channel_%d' % (ch + m.get('channel_base', 0))] = v
        if all(v is None for v in values.values()):
            ready = rows[0][1].get(m.get('phase_key', 'phase'))
            ctx.fail(refused('NO_RESULT', '%s has no finished %s yet (%s = %s).' % (self.instrument_id, label, m.get('phase_key', 'phase'), short(ready, 40)),
                             'measure first: `chemlab run %s measure --wait` (the sample must be in the cell).' % self.instrument_id))
        js = {'quantity': label, 'unit': unit, 'key': key, 'result': raw if len(raw) > 1 else rows[0][1]}
        if m.get('percent'):
            js['percent'] = True
        js.update(values)
        v0 = values.get('value')
        if v0 is not None and unit and units.unit_kind(unit):
            js['value_si'] = units.from_declared(v0, unit)
        return {'text': '%s result:\n%s\n' % (self.instrument_id, '\n'.join(lines)), 'json': js}

    def h_result(self, ctx, p):
        m = self.vmap('result')
        ch = None
        if p.get('channel') is not None:
            if m.get('channels') is None:
                ctx.fail(invalid('NO_CHANNEL', '%s has no channels.' % self.instrument_id, 'leave out --channel.'))
            base = m.get('channel_base', 0)
            try:
                ch = int(p.get('channel')) - base
            except ValueError:
                ch = None
            if ch not in m['channels']:
                ctx.fail(invalid('UNKNOWN_CHANNEL', '%s has no channel %r.' % (self.instrument_id, p.get('channel')),
                                 'channels: %s.' % ', '.join(str(c + base) for c in m['channels'])))
        return self.read_result(ctx, ch)

    def h_clean(self, ctx, p):
        m = self.vmap('clean')
        self.need_power(ctx)
        self.need_idle(ctx)
        self.requirements(ctx, 'clean')
        v = self.arg_value(m['cap'], m['arg'], p.get('volume'))
        self.check_range(ctx, m['cap'], m['arg'], v, 'cleaning volume')
        ctx.call(m['cap'], {m['arg']: v}, 'start cleaning with %s' % units.fmt(p.get('volume'), 'volume'))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.state_job(ctx, m, 'cleaning on %s' % self.instrument_id)
        if not p.get('wait'):
            return {'text': 'started %s: cleaning on %s\nfollow it: chemlab status %s | chemlab wait %s\n' % (job.handle, self.instrument_id, job.handle, job.handle),
                    'json': {'job': job.handle, 'kind': job.kind}}
        r = self.follow_job(ctx, job, p.get('max') or 600.0)
        if job.state != 'succeeded':
            ctx.fail(refused('NOT_FINISHED' if job.state == 'running' else 'RUN_FAILED', 'cleaning on %s is %s.' % (self.instrument_id, job.state),
                             'follow it with `chemlab wait %s`.' % job.handle, changed='yes'))
        return {'text': 'ok: %s cleaned with %s (%s of lab time)\n' % (self.instrument_id, units.fmt(p.get('volume'), 'volume'),
                                                                   units.fmt_s(r['json'].get('waited_s') or 0)), 'json': {'job': job.handle, 'cleaned': True}}

"""Electrochemistry class ("electrochem"): potentiostats and electrochemical workstations - Gamry Reference 620, CH Instruments
CHI920D (scanning electrochemical microscope) and CHI440C (EQCM), and the Pine WaveVortex 10 rotating ring-disk electrode rotator.
Members and mappings: configs/adapters/electrochem.json.

Verbs: connect PORT=ID ..., technique NAME [options], start [--time T --interval DT] (job), stop, data [--save-to F],
rotate SPEED|off, move X Y Z, zero, status.  power / open / close come from the shared layer.

Techniques (potentials vs the instrument's reference electrode; current signs follow each member's own convention, given in
`chemlab help INST technique` - the Gamry and the Pine use IUPAC (anodic positive), the CHI440C twin cathodic positive):

    cv    --from 0.6V --to -0.2V [--rate 100mV/s] [--step 1mV] [--cycles 1]       cyclic voltammetry (from -> to -> from)
    lsv   --from 0V --to 0.8V [--rate 50mV/s] [--step 1mV]                        linear sweep
    ca    --potential 0.5V --time 10s [--interval 0.1s]                            chronoamperometry (potential step)
    cp    --current 10uA --time 10s [--interval 0.1s]                              chronopotentiometry (current step)
    steps --program 0.2V@2s,0.5V@1s | 10uA@2s,-10uA@1s [--interval 0.1s]          a staircase of potential or current steps
    qcm   --time 10s [--interval 0.1s]                                             quartz crystal frequency only (EQCM)
    secm-line / secm-approach --path X,Y,Z;X,Y,Z;... --tip 0.5V --substrate 0V [--dwell 0.3s] [--min-gap 1um]
                                                                                   SECM probe scan / approach curve

Each member lowers the technique to its own form: the Gamry plays a discrete waveform (a sample interval and a list of values),
the CHI440C runs a step list, the CHI920D a probe path.  Values the technique needs but the agent did not give (the reference
electrode label, the SECM dwell or minimum gap) keep the last accepted ones on the instrument, else the documented defaults.

Vendor-neutral references: IUPAC electrochemical conventions; Gamry Framework / Toolkit, the CHI macro language and BioLogic
EC-Lab technique names as vendor styles; ASTM G5 / G59 terms for potentiodynamic scans.
"""
import re

from .. import units
from ..base import finite, short
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb, save_to_opt
from .method_common import MethodAdapter

REF = ('Gamry Reference 620 operator\'s manual (cell connections p.49-52, controls p.53-63) and Framework Pstat class help; CHI900D '
       'series SECM user manual (setup and control menus p.57-158); CHI 400C series EQCM product page and brochure (p.3-7); Pine '
       'WaveVortex 10 user guide (rate control, external stop, enclosure p.13-40)')
TECHNIQUES = ('cv', 'lsv', 'ca', 'cp', 'steps', 'qcm', 'secm-line', 'secm-approach')
_NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


def parse_rate(text):
    """A scan rate like 100mV/s, 0.1V/s or 50 (mV/s) -> V/s."""
    t = text.strip().replace(' ', '').lower()
    m = re.fullmatch('(' + _NUM + r')(mv/s|v/s|mv/min|v/min)?', t)
    if not m:
        raise invalid('BAD_NUMBER', '%r is not a scan rate.' % text, 'write it like 100mV/s or 0.05V/s.')
    v = float(m.group(1))
    unit = m.group(2) or 'mv/s'
    f = {'mv/s': 1e-3, 'v/s': 1.0, 'mv/min': 1e-3 / 60, 'v/min': 1.0 / 60}[unit]
    if v <= 0:
        raise invalid('BAD_NUMBER', 'the scan rate must be positive.', 'write it like 100mV/s.')
    return v * f


class ElectrochemAdapter(MethodAdapter):
    CLASS = 'electrochem'
    TITLE = 'electrochemistry'
    ABOUT = ('Electrochemical workstations: connect the electrodes and the cell, choose a technique (cv, lsv, ca, cp, steps, qcm, '
             'SECM scans) in plain units, start it and follow it as a job, read the current / potential / frequency series, rotate '
             'a ring-disk electrode, move an SECM probe, zero a QCM baseline.')
    STANDARDS = ('IUPAC electrochemical conventions (potential vs reference, anodic current positive); ASTM G5 / G59 terms; Gamry '
                 'Toolkit, CHI macro language and BioLogic EC-Lab technique names as vendor styles')
    UNIT_KINDS = ('voltage', 'current', 'duration', 'length', 'rotation')
    PATH_GROUPS = (('start', 'stop', 'data'),)

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        e = lambda h: Opt(h, kind='voltage', metavar='E')       # noqa: E731
        return {
            'connect': Verb('connect', 'register the electrodes and the cell in their slots: PORT=ID ... (ID none = take out)',
                            self.h_connect, variadic=('PORT=ID', 'e.g. working=we_01 reference=ref_01 counter=ce_01 cell=cell_01'),
                            preconditions=['no technique running'], errors=['UNKNOWN_PORT (usage)', 'BUSY (refused)'],
                            example='chemlab run INST connect working=we_01 reference=ref_01 counter=ce_01 cell=cell_01', reference=REF),
            'technique': Verb('technique', 'choose the technique and its waveform / steps / path (see help)', self.h_technique,
                              positionals=[Arg('NAME', 'technique', choices=list(TECHNIQUES))],
                              options={'from': e('start potential (cv, lsv)'), 'to': e('vertex / end potential (cv, lsv)'),
                                       'rate': Opt('scan rate (cv, lsv), e.g. 100mV/s (default 100mV/s)', metavar='RATE'),
                                       'step': Opt('potential step of the sweep (default 1mV)', kind='voltage', metavar='E'),
                                       'cycles': Opt('number of cycles (cv, default 1)', metavar='N'),
                                       'potential': e('step potential (ca)'),
                                       'current': Opt('step current (cp), e.g. 10uA', kind='current', metavar='I'),
                                       'time': Opt('duration (ca, cp, qcm)', kind='duration', metavar='TIME'),
                                       'interval': Opt('sample interval (default 0.1s)', kind='duration', metavar='TIME'),
                                       'program': Opt('steps: VALUE@TIME,... e.g. 0.2V@2s,0.5V@1s or 10uA@2s,-10uA@1s', metavar='LIST'),
                                       'path': Opt('SECM probe path X,Y,Z;X,Y,Z;... (bare numbers in mm; e.g. -40um,-40um,5um;40um,-40um,5um)',
                                                   metavar='PATH'),
                                       'tip': e('SECM tip potential'), 'substrate': e('SECM substrate potential'),
                                       'dwell': Opt('SECM dwell per point', kind='duration', metavar='TIME'),
                                       'min-gap': Opt('SECM smallest tip-substrate gap', kind='length', metavar='L'),
                                       'reference': Opt('reference electrode label recorded with the method', metavar='TEXT')},
                              preconditions=['the technique exists on this instrument', 'potentials, currents, times within the contract '
                                             'ranges', 'no technique running'],
                              errors=['NOT_ON_THIS_INSTRUMENT, MISSING_OPTION, OUT_OF_RANGE, TOO_MANY_POINTS (usage)', 'BUSY (refused)'],
                              example='chemlab run INST technique cv --from 0.6V --to -0.2V --rate 100mV/s', reference=REF),
            'start': Verb('start', 'start the technique (returns job-N)', self.h_start, long_action=True,
                          options={'time': Opt('measurement duration (rotator data acquisition)', kind='duration', metavar='TIME'),
                                   'interval': Opt('sample interval (rotator, default 0.1s)', kind='duration', metavar='TIME')},
                          preconditions=['power on', 'electrodes and cell connected', 'a technique set', 'a sample in the cell',
                                         'covers closed', 'the electrode rotating (rotator)'],
                          errors=['POWER_OFF, NOT_CONNECTED, NO_TECHNIQUE, NO_SAMPLE, LID_OPEN, ROTATION_OFF, BUSY (refused)'],
                          example='chemlab run INST start', reference=REF),
            'stop': Verb('stop', 'stop the technique (the data so far are kept)', self.h_stop, preconditions=['a technique running'],
                         errors=['NOT_RUNNING (refused)'], example='chemlab run INST stop'),
            'data': Verb('data', 'the recorded series (time, potential, current, frequency, ring/disk currents)', self.h_record('data', 'data'),
                         readonly=True, options={'save-to': save_to_opt()}, example='chemlab run INST data --save-to cv.json'),
            'rotate': Verb('rotate', 'rotate the electrode at a speed, or stop it', self.h_rotate,
                           positionals=[Arg('SPEED|off', 'e.g. 1600rpm, or off')], preconditions=['power on', 'speed 100..8000 rpm'],
                           errors=['OUT_OF_RANGE (usage)', 'POWER_OFF (refused)'], example='chemlab run INST rotate 1600rpm', reference=REF),
            'move': Verb('move', 'move the probe to X Y Z (bare numbers in mm) and wait until it is there', self.h_move, long_action=True,
                         positionals=[Arg('X', 'x, e.g. 0um', kind='length'), Arg('Y', 'y', kind='length'), Arg('Z', 'z (height)', kind='length')],
                         preconditions=['power on', 'no scan running', 'inside the stage travel'],
                         errors=['OUT_OF_RANGE (usage)', 'POWER_OFF, BUSY (refused)'], example='chemlab run INST move 0um 0um 1mm', reference=REF),
            'zero': Verb('zero', 'zero the QCM frequency baseline (the next shifts count from here)', self.h_zero,
                         preconditions=['power on', 'a valid frequency reading', 'no technique running'],
                         errors=['NO_SIGNAL, BUSY (refused)'], example='chemlab run INST zero', reference=REF),
        }

    # ------------------------------------------------------------------ technique
    def program_steps(self, ctx, text):
        """'0.2V@2s,0.5V@1s' -> ('potential' | 'current', [(value SI, seconds)])."""
        out, kinds = [], set()
        for part in [x for x in text.replace(';', ',').split(',') if x.strip()]:
            val, at, dur = part.partition('@')
            if not at:
                ctx.fail(invalid('BAD_PROGRAM', '%r is not VALUE@TIME.' % part, 'write e.g. --program 0.2V@2s,0.5V@1s.'))
            v = val.strip().lower()
            kind = 'current' if re.search(r'[mun]?a$', v) or v.endswith('µa') else 'voltage'
            kinds.add(kind)
            out.append((units.parse(val, kind, what='step value'), units.parse(dur, 'duration', what='step time')))
        if not out:
            ctx.fail(invalid('BAD_PROGRAM', 'the program is empty.', 'write e.g. --program 0.2V@2s,0.5V@1s.'))
        if len(kinds) > 1:
            ctx.fail(invalid('BAD_PROGRAM', 'a program holds either potentials or currents, not both.', 'give all steps in V or all in A.'))
        return ('current' if kinds == {'current'} else 'potential'), out

    def need_opts(self, ctx, p, name, opts):
        missing = [o for o in opts if p.get(o) is None]
        if missing:
            ctx.fail(invalid('MISSING_OPTION', 'technique %s needs %s.' % (name, ', '.join('--' + o for o in missing)),
                             'see `chemlab help %s technique`.' % self.instrument_id))

    def waveform(self, ctx, p, name, m):
        """Gamry style: {'mode', 'sample_interval_s', 'values'}."""
        limit = int(m.get('max_points', 10000))
        if name in ('cv', 'lsv'):
            self.need_opts(ctx, p, name, ('from', 'to'))
            e1, e2 = p.get('from'), p.get('to')
            rate = parse_rate(p.get('rate') or '100mV/s')
            step = p.get('step') or 1e-3
            if step <= 0:
                ctx.fail(invalid('BAD_NUMBER', '--step must be positive.', 'give e.g. --step 1mV.'))
            if e1 == e2:
                ctx.fail(invalid('BAD_SWEEP', '--from and --to are the same potential.', 'give two different potentials.'))
            try:
                cycles = int(p.get('cycles') or 1)
            except ValueError:
                ctx.fail(invalid('BAD_NUMBER', '--cycles %s is not a whole number.' % p.get('cycles'), 'give e.g. --cycles 3.'))
            n = int(round(abs(e2 - e1) / step))
            if n < 1:
                ctx.fail(invalid('BAD_SWEEP', 'the step is larger than the sweep.', 'give a smaller --step.'))
            fwd = [round(e1 + (e2 - e1) * i / n, 9) for i in range(n + 1)]
            if name == 'lsv':
                values = fwd
            else:
                values = [fwd[0]]
                for _ in range(max(1, cycles)):
                    values += fwd[1:] + list(reversed(fwd[:-1]))
            interval = round(abs(e2 - e1) / n / rate, 12)
            mode = 'potential'
        elif name in ('ca', 'cp'):
            self.need_opts(ctx, p, name, ('potential' if name == 'ca' else 'current', 'time'))
            interval = p.get('interval') or 0.1
            n = int(round(p.get('time') / interval))
            values = [round(p.get('potential') if name == 'ca' else p.get('current'), 12)] * max(1, n)
            mode = 'potential' if name == 'ca' else 'current'
        elif name == 'steps':
            self.need_opts(ctx, p, name, ('program',))
            mode, steps = self.program_steps(ctx, p.get('program'))
            interval = p.get('interval') or 0.1
            values = []
            for v, t in steps:
                values += [round(v, 12)] * max(1, int(round(t / interval)))
        else:
            ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s does not run %s.' % (self.instrument_id, name),
                             'techniques of %s: %s.' % (self.instrument_id, ', '.join(m['techniques']))))
        if len(values) > limit:
            ctx.fail(invalid('TOO_MANY_POINTS', 'that waveform has %d points; %s plays at most %d.' % (len(values), self.instrument_id, limit),
                             'use a larger --step or --interval, or fewer cycles.'))
        return {m['arg']: {'mode': mode, 'sample_interval_s': interval, 'values': values}}, {
            'technique': name, 'points': len(values), 'sample_interval_s': interval, 'duration_s': interval * max(0, len(values) - 1),
            'mode': mode}

    def step_list(self, ctx, p, name, m):
        """CHI440C style: {'mode', 'steps': [{duration_s, potential_v, current_a}], 'sample_interval_s', 'reference_electrode'}."""
        interval = p.get('interval') or 0.1
        if name == 'ca':
            self.need_opts(ctx, p, name, ('potential', 'time'))
            mode, steps = 'potential', [(p.get('potential'), p.get('time'))]
        elif name == 'cp':
            self.need_opts(ctx, p, name, ('current', 'time'))
            mode, steps = 'current', [(p.get('current'), p.get('time'))]
        elif name == 'steps':
            self.need_opts(ctx, p, name, ('program',))
            mode, steps = self.program_steps(ctx, p.get('program'))
        elif name == 'qcm':
            self.need_opts(ctx, p, name, ('time',))
            mode, steps = 'qcm_only', [(0.0, p.get('time'))]
        else:
            ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s does not run %s.' % (self.instrument_id, name),
                             'techniques of %s: %s.' % (self.instrument_id, ', '.join(m['techniques']))))
        native_mode = {'potential': 'potential', 'current': 'current', 'qcm_only': 'qcm_only'}[mode]
        rows = []
        for v, t in steps:
            k = t / interval
            if abs(k - round(k)) > 1e-6 or round(k) < 1:
                ctx.fail(invalid('BAD_STEP', 'step time %s is not a whole number of sample intervals (%s).' % (units.fmt_s(t), units.fmt_s(interval)),
                                 'make every step time a multiple of --interval.'))
            rows.append({'duration_s': t, 'potential_v': round(v, 12) if native_mode == 'potential' else 0.0,
                         'current_a': round(v, 12) if native_mode == 'current' else 0.0})
        args = {'mode': native_mode, 'steps': rows, 'sample_interval_s': interval}
        if p.get('reference'):
            args['reference_electrode'] = p.get('reference')
        return args, {'technique': name, 'steps': len(rows), 'sample_interval_s': interval, 'duration_s': sum(r['duration_s'] for r in rows)}

    def probe_path(self, ctx, p, name, m):
        """CHI920D style: {'mode', 'path': [{x_m, y_m, z_m}], 'dwell_s', 'tip_potential_v', 'substrate_potential_v', ...}."""
        if name not in ('secm-line', 'secm-approach'):
            ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s does not run %s.' % (self.instrument_id, name),
                             'techniques of %s: %s.' % (self.instrument_id, ', '.join(m['techniques']))))
        self.need_opts(ctx, p, name, ('path',))
        pts = []
        for chunk in [x for x in p.get('path').split(';') if x.strip()]:
            xyz = [x for x in chunk.split(',') if x.strip()]
            if len(xyz) != 3:
                ctx.fail(invalid('BAD_PATH', '%r is not X,Y,Z.' % chunk, 'write points like -40um,-40um,5um;40um,-40um,5um.'))
            x, y, z = (units.parse(v, 'length', what='path') for v in xyz)
            pts.append({'x_m': round(x, 12), 'y_m': round(y, 12), 'z_m': round(z, 12)})
        args = {'mode': 'constant_height' if name == 'secm-line' else 'approach', 'path': pts}
        for opt, arg in (('tip', 'tip_potential_v'), ('substrate', 'substrate_potential_v'), ('dwell', 'dwell_s'), ('min-gap', 'min_gap_m')):
            if p.get(opt) is not None:
                args[arg] = p.get(opt)
        if p.get('reference'):
            args['reference_electrode'] = p.get('reference')
        return args, {'technique': name, 'points': len(pts)}

    def h_technique(self, ctx, p):
        m = self.vmap('technique')
        name = p.pos[0]
        if name not in m['techniques']:
            ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s does not run %s.' % (self.instrument_id, name),
                             'techniques of %s: %s.' % (self.instrument_id, ', '.join(m['techniques']))))
        style = m['style']
        args, info = {'waveform': self.waveform, 'steps': self.step_list, 'secm': self.probe_path}[style](ctx, p, name, m)
        kept = self.keep_missing(ctx, m['cap'], args, m.get('keep') or {})
        self.need(ctx, 'technique')
        self.validate_call(ctx, m['cap'], args)
        ctx.call(m['cap'], args, 'set the technique (%s)' % name)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        extra = []
        if kept['kept_last'] or kept['kept_example']:
            extra.append('kept: %s' % ', '.join('%s=%s' % (k, short(v, 40)) for k, v in list(kept['kept_last'].items()) + list(kept['kept_example'].items())))
        def show(k, v):
            if k.endswith('_s') and finite(v):
                return ('%g ms' % round(v * 1000, 6)) if v < 1 else units.fmt_s(v)
            return v if isinstance(v, str) else short(v, 30)
        desc = ', '.join('%s %s' % (k.replace('_s', '').replace('_', ' '), show(k, v)) for k, v in info.items())
        return {'text': 'ok: %s technique set (%s)%s\n' % (self.instrument_id, desc, ('; ' + '; '.join(extra)) if extra else ''),
                'json': dict(info, arguments_kept=kept)}

    # ------------------------------------------------------------------ run
    def h_start(self, ctx, p):
        m = self.vmap('start')
        args = dict(m.get('args') or {})
        if m.get('time_arg'):
            if p.get('time') is None:
                ctx.fail(invalid('MISSING_OPTION', 'start on %s needs --time (how long to record).' % self.instrument_id,
                                 'give e.g. --time 30s (and --interval 0.1s).'))
            args[m['time_arg']] = p.get('time')
            args[m['interval_arg']] = p.get('interval') or 0.1
            self.validate_call(ctx, m['cap'], args, {m['time_arg']: '--time', m['interval_arg']: '--interval'})
        elif p.get('time') is not None or p.get('interval') is not None:
            ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s takes the duration from its technique; start takes no --time / --interval.'
                             % self.instrument_id, 'set them with `chemlab run %s technique ...`.' % self.instrument_id))
        self.running_check(ctx)
        self.need(ctx, 'start')
        r = ctx.call(m['cap'], args, 'start (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.make_job(ctx, m, r, '%s on %s' % (self.TITLE, self.instrument_id), stop=self.stop_fn())
        out = self.job_reply(job, 'ok: %s started' % self.instrument_id)
        out['json'].update({'arguments': args})
        return out

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        self.need(ctx, 'stop')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop (%s)' % m['cap'])
        return {'text': 'ok: %s stopped\n' % self.instrument_id, 'json': {}}

    def h_rotate(self, ctx, p):
        m = self.vmap('rotate')
        text = p.raw['SPEED|off'].strip().lower()
        self.need(ctx, 'rotate')
        if text in ('off', 'stop', '0'):
            ctx.call(m['run_cap'], {m['run_arg']: False}, 'stop the rotation')
            return {'text': 'ok: %s rotation off\n' % self.instrument_id, 'json': {'rotating': False}}
        w = units.parse(text, 'rotation', what='speed')
        lo, hi = float(m.get('min_rad_s', 0.0)), self.arg_range_si(m['cap'], m['arg'])[1]
        if w < lo - 1e-9 or (hi is not None and w > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'speed %s is outside the range of %s (%s to %s, or off).' % (
                units.fmt(w, 'rotation'), self.instrument_id, units.fmt(lo, 'rotation'), units.fmt(hi, 'rotation')),
                'give a speed from %s to %s.' % (units.fmt(lo, 'rotation'), units.fmt(hi, 'rotation'))))
        ctx.call(m['cap'], {m['arg']: self.arg_value(m['cap'], m['arg'], w)}, 'set the speed to %s' % units.fmt(w, 'rotation'))
        if not ctx.v(m['run_slot']):
            ctx.call(m['run_cap'], {m['run_arg']: True}, 'start the motor')
        return {'text': 'ok: %s rotating at %s\n' % (self.instrument_id, units.fmt(w, 'rotation')),
                'json': {'rotating': True, 'speed_rad_s': w, 'speed_rpm': w * 60 / (2 * 3.141592653589793)}}

    def h_move(self, ctx, p):
        m = self.vmap('move')
        xyz = [p.pos[0], p.pos[1], p.pos[2]]
        self.need(ctx, 'move')
        args = {a: round(self.arg_value(m['cap'], a, v), 12) for a, v in zip(m['args_xyz'], xyz)}
        self.validate_call(ctx, m['cap'], args, dict(zip(m['args_xyz'], ('X', 'Y', 'Z'))))
        ctx.call(m['cap'], args, 'move the probe to %s' % ', '.join(units.fmt(v, 'length') for v in xyz))
        tol = float(m.get('tolerance_m', 1e-8))
        axes = m['axes']
        waited = self.settle(ctx, lambda c: not c.v(m['moving_slot']) and all(finite(c.v(a)) and abs(c.v(a) - args[n]) <= tol
                                                                              for a, n in zip(axes, m['args_xyz'])),
                             'wait for the positioner', float(m.get('max_s', 120.0)), poll_s=0.5)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': 'ok: %s probe at %s (%s of moving)\n' % (self.instrument_id, ', '.join(units.fmt(ctx.v(a), 'length') for a in axes),
                                                               units.fmt_s(waited)),
                'json': {'position_m': [ctx.v(a) for a in axes], 'waited_s': waited}}

    def h_zero(self, ctx, p):
        m = self.vmap('zero')
        self.need(ctx, 'zero')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'zero the baseline')
        b = self.sv(ctx, m.get('report_slot')) if m.get('report_slot') else None
        return {'text': 'ok: %s baseline zeroed%s\n' % (self.instrument_id, (' at %s' % short(b, 30)) if b is not None else ''),
                'json': {'baseline': b}}

    def explain_refusal(self, ctx, cap, args, response):
        return MethodAdapter.explain_refusal(self, ctx, cap, args, response)


_ = refused

"""Solid sample preparation class ("solid_prep"): planetary ball mill, vibratory sieve shaker, rotary sample divider, manual
hydraulic pellet press, automated fusion bead maker.  Operator action: mill, sieve, divide, press or fuse a solid.

Verbs: method KEY=VALUE ..., intensity VALUE, set-temp T [--wait], program N|NAME, start [--delay T] (job-N), pause on|off,
stop, rearm, result [--save-to F], feeder on|off, pump [N] [--to LOAD] [--hold] [--max T], valve open|close, leadscrew POS,
open|close guard (the press), status.  power, open/close of lids, covers and doors, estop and wait come from the shared layer.
Members and mappings: configs/adapters/solid_prep.json.

Vendor-neutral references: ISO 3310-1 / ASTM E11 (test sieves) and ISO 2591-1 (test sieving: amplitude, time, interval), ISO
14488 (sample division), DIN 51001 / ISO 12677 (fused beads for XRF: fusion temperature programme, rocking, pouring, cooling),
the KBr pellet method (load, hold time, vacuum); no remote command standard exists (the RETSCH, Specac and Claisse devices are
operated from their own panels; EasySieve is a proprietary serial link).

Canonical method keys (each member maps the ones it has; native argument names pass through in their declared unit; the rest
of the method comes from the member's template, the contract's reference method):

    speed=     mill speed (300rpm)          time=  run time (5min)       interval=  run time between reversals / pauses (30s)
    pause=     pause between intervals (5s) reverse=  on|off           amplitude=  sieving amplitude (1.2mm)
    intensity= feeder intensity (0..99)     gap=  hopper gap (5mm)
    load=      press target load (68kN, 7t is not a unit: give N or kN)   hold=  hold time at load (2min)   tolerance= (5%)
    limit=     press load limit (relief valve, 70kN)
    segments=  fusion temperature programme TIME@TEMP,... (10min@1065C,9min@1065C)   rocking= (10deg)  rocking-freq= (0.3Hz)
    pour=      pouring mode 1..4            air=  cooling air (100%)     cooling=  minimum cooling time (5min)   output=
"""
import re

from .. import units
from ..base import finite
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb
from .. import shared
from .mechanical import TesterBase

ON_OFF = ['on', 'off']
REF = ('ISO 3310-1, ISO 2591-1 (sieving), ISO 14488 (dividing), ISO 12677 (fused beads), KBr pellet method; vendor manuals: '
       'PM 100 operating manual pp.23-37 and 47-55, AS 200 control manual pp.29-36, PT 100 manual pp.23-29, DR 100 manual pp.33-36, '
       'Atlas Manual 15T manual pp.12-17, LeNeo brochure pp.2-4')


class SolidPrepAdapter(TesterBase):
    CLASS = 'solid_prep'
    TITLE = 'solid sample preparation'
    ABOUT = ('Mill, sieve, divide, press or fuse a solid: set the process (speed, amplitude, time, load, temperature programme) in '
             'plain units, start it as a job, pause / stop / re-arm it, read the process record; on the manual press pump the load, '
             'hold it and release it with the valve.')
    STANDARDS = ('ISO 3310-1 / ASTM E11 and ISO 2591-1 (test sieving), ISO 14488 (sample division), ISO 12677 (fused beads), KBr pellet '
                 'method; no remote command standard (panel-operated devices)')
    UNIT_KINDS = ('rotation', 'length', 'duration', 'force', 'temperature', 'angle', 'frequency')
    KEY_HELP = {'speed': 'mill speed', 'time': 'run time', 'interval': 'run time between reversals / pauses', 'pause': 'pause time',
                'reverse': 'direction reversal on|off', 'direction': '1 or -1', 'amplitude': 'sieving amplitude (total lift)',
                'intensity': 'feeder intensity 0..99', 'gap': 'hopper gap', 'load': 'target load', 'hold': 'hold time at load',
                'tolerance': 'load tolerance', 'limit': 'load limit of the relief valve', 'segments': 'TIME@TEMP,... programme',
                'rocking': 'rocking angle', 'rocking-freq': 'rocking frequency', 'pour': 'pouring mode 1..4', 'air': 'cooling air fraction',
                'cooling': 'minimum cooling time', 'output': 'glass_disk or borate_solution', 'stirring': 'stirring fraction'}
    CODES = {
        'BUSY': ('BUSY', '{inst} is running (or still coasting); that is not possible now.', 'wait for the job, or stop it first.'),
        'PROGRAM_EMPTY': ('PROGRAM_EMPTY', 'that program slot of {inst} is empty.', 'see the stored programs in `chemlab run {inst} status`.'),
        'NOT_FOUND': ('PROGRAM_NOT_FOUND', '{inst} has no stored program of that name.', 'see `chemlab run {inst} status` for the program names.'),
        'REFERENCE_RANGE': ('OUT_OF_RANGE', 'the value is outside what {inst} can do with this load (amplitude / acceleration limit).',
                            'give a smaller amplitude.'),
        'INTERVAL_REQUIRED': ('INTERVAL_REQUIRED', 'a pause or a direction reversal on {inst} needs an interval time.', 'add interval=... (or reverse=off pause=0s).'),
    }

    def build_verbs(self):
        t = {
            'method': Verb('method', 'set the process: KEY=VALUE ... in plain units (class keys or native names; the rest from the '
                                     'member\'s template)', self.h_settings('method', 'method'), variadic=('KEY=VALUE', 'a setting'),
                           preconditions=['every value within the contract range', 'not while a cycle runs or a finished cycle waits'],
                           errors=['OUT_OF_RANGE, UNKNOWN_SETTING (usage)', 'CYCLE_FINISHED, BUSY (refused)'],
                           example='chemlab run INST method speed=300rpm time=5min', reference=REF),
            'intensity': Verb('intensity', 'set the intensity: mill speed, sieving amplitude or feeder intensity', self.h_intensity,
                              positionals=[Arg('VALUE', 'speed (300rpm), amplitude (1.2mm) or feeder intensity (0..99)')],
                              preconditions=['within the contract range', 'a method is set'], errors=['OUT_OF_RANGE (usage)'],
                              example='chemlab run INST intensity 300rpm', reference=REF),
            'set-temp': self.temp_verb(REF),
            'program': Verb('program', 'load a stored program (slot number or name; 0 = manual mode)', self.h_program,
                            positionals=[Arg('N|NAME', 'program slot or name')], preconditions=['not while running'],
                            errors=['PROGRAM_EMPTY / PROGRAM_NOT_FOUND (refused)'], example='chemlab run INST program 1', reference=REF),
            'pause': Verb('pause', 'pause or resume the running process', self.h_pause, positionals=[Arg('on|off', 'on (pause) or off (resume)', choices=ON_OFF)],
                          preconditions=['running (to pause) / paused (to resume)'], errors=['NOT_RUNNING, NOT_PAUSED (refused)'],
                          example='chemlab run INST pause on', reference=REF),
            'rearm': Verb('rearm', 'clear the finished or stopped cycle so that a new one can be set and started', self.h_simple('rearm', 'cycle re-armed'),
                          preconditions=['the cycle has ended and the drive has stopped'], errors=['NOTHING_TO_REARM (refused)'],
                          example='chemlab run INST rearm', reference=REF),
            'feeder': Verb('feeder', 'switch the vibratory feeder on or off', self.h_feeder, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                           example='chemlab run INST feeder on', reference=REF),
            'pump': Verb('pump', 'pump the press: N strokes, or until the load is reached (--to), and keep it there for the cycle (--hold); '
                                 'waits stroke by stroke', self.h_pump, positionals=[Arg('N', 'number of strokes (default 1)', required=False)],
                         options={'to': Opt('pump until the gauge reads this load', kind='force', metavar='LOAD'),
                                  'hold': Opt('with --to: keep topping the load up until the press cycle is complete', kind='flag'),
                                  'max': Opt('longest time (default 10 min)', kind='duration', metavar='TIME')},
                         preconditions=['guards down, valve closed, die and sample in place, method set'],
                         errors=['NOT_READY (refused, with what is missing)', 'NOT_REACHED (refused)'],
                         example='chemlab run INST pump --to 68kN --hold', reference=REF, long_action=True),
            'valve': Verb('valve', 'open (release the load) or close the release valve; waits until it is there', self.h_valve,
                          positionals=[Arg('open|close', 'open or close', choices=['open', 'close'])], example='chemlab run INST valve close',
                          reference=REF),
            'leadscrew': Verb('leadscrew', 'set the leadscrew (upper bolster) height to close the gap above the die; waits until it is there',
                              self.h_leadscrew, positionals=[Arg('POS', 'position, e.g. 73mm', kind='length')],
                              preconditions=['no load, ram down, the die stack fits under it'], errors=['OUT_OF_RANGE (usage)', 'NOT_READY (refused)'],
                              example='chemlab run INST leadscrew 73mm', reference=REF),
        }
        t.update(self.run_verbs(REF, 'the process record (phase, time, speed / amplitude / load / temperature)'))
        if (self.map.get('verbs') or {}).get('open'):                         # parts the shared layer cannot name (the press guards)
            pos = [Arg('PART', 'which part (guard, rear-guard); optional when there is only one', required=False)]
            t['open'] = Verb('open', 'raise a guard (waits until it is up)', self.h_part('open'), positionals=pos,
                             example='chemlab run INST open guard', reference=REF)
            t['close'] = Verb('close', 'lower a guard (waits until it is down)', self.h_part('close'), positionals=pos,
                              example='chemlab run INST close guard', reference=REF)
        return t

    def h_part(self, action):
        def h(ctx, p):
            name, spec = shared._part(self, ctx, p.pos[0] if p.pos else None)
            args = dict(spec.get('args') or {})
            args[spec['arg']] = spec[action]
            self.need(ctx, '%s:%s' % (action, name))
            ctx.call(spec['cap'], args, '%s the %s' % (action, name))
            if spec.get('slot') and not ctx.dry_run:
                target = spec['%s_at' % action]
                self.wait_slot(ctx, lambda view: finite(ctx.v(spec['slot'])) and abs(ctx.v(spec['slot']) - target) <= 1e-9, 60.0,
                               'the %s' % name, poll_s=0.5)
            return {'text': 'ok: %s %s %s\n' % (self.instrument_id, name, 'raised' if action == 'open' else 'lowered'),
                    'json': {'part': name, 'action': action}}
        return h

    def custom_value(self, ctx, spec, text, what):
        if spec.get('syntax') == 'segments':                                  # 10min@1065C,9min@1065C
            out = []
            for part in [p for p in text.split(',') if p.strip()]:
                d, at, temp = part.partition('@')
                if not at:
                    ctx.fail(invalid('BAD_VALUE', '%s=%s: %r is not TIME@TEMP.' % (what, text, part), 'write %s=10min@1065C,9min@1065C.' % what))
                out.append({'duration_s': units.parse(d, 'duration', what=what), 'end_temperature_K': units.parse(temp, 'temperature', what=what)})
            if not out:
                ctx.fail(invalid('BAD_VALUE', '%s needs at least one TIME@TEMP.' % what, 'write %s=10min@1065C.' % what))
            return out
        return TesterBase.custom_value(self, ctx, spec, text, what)

    def settings(self, ctx, spec, items, verb):
        segs = {k: ks for g in [spec] + list(spec.get('also') or []) for k, ks in (g.get('keys') or {}).items() if ks.get('syntax') == 'segments'}
        rest, pre = [], {}
        for item in items:
            k, eq, v = item.partition('=')
            if eq and k in segs:
                pre[segs[k]['arg']] = self.custom_value(ctx, segs[k], v, k)
            else:
                rest.append(item)
        calls = TesterBase.settings(self, ctx, dict(spec, always=spec.get('always') or bool(pre)), rest, verb)
        for cap, args, given, names in calls:
            if cap == spec['cap']:
                args.update(pre)
                given.update(pre)
        return calls

    # ------------------------------------------------------------------ handlers
    def h_simple(self, verb, step):
        def h(ctx, p):
            m = self.vmap(verb)
            self.need(ctx, verb)
            ctx.call(m['cap'], dict(m.get('args') or {}), step)
            ph = self.slot('phase')
            return {'text': 'ok: %s %s%s\n' % (self.instrument_id, step, (' (%s)' % ctx.v(ph)) if ph else ''), 'json': {'phase': ctx.v(ph) if ph else None}}
        return h

    def h_intensity(self, ctx, p):
        m = self.vmap('intensity')
        v = self.parse_setting(ctx, m['cap'], m['arg'], p.raw['VALUE'], m.get('kind'), what='VALUE')
        self.need(ctx, 'intensity')
        ctx.call(m['cap'], {m['arg']: v}, 'set the intensity to %s' % p.raw['VALUE'])
        return {'text': 'ok: %s %s = %s\n' % (self.instrument_id, m['cap'], p.raw['VALUE']), 'json': {m['arg']: v}}

    def h_program(self, ctx, p):
        m = self.vmap('program')
        v = self.parse_setting(ctx, m['cap'], m['arg'], p.raw['N|NAME'], what='N|NAME')
        self.need(ctx, 'program')
        ctx.call(m['cap'], {m['arg']: v}, 'load program %s' % v)
        return {'text': 'ok: %s program %s loaded\n' % (self.instrument_id, v), 'json': {'program': v}}

    def h_pause(self, ctx, p):
        m = self.vmap('pause')
        want = p.pos[0] == 'on'
        self.need(ctx, 'pause' if want else 'resume')
        cap = m['on_cap'] if want else m['off_cap']
        ctx.call(cap, {}, 'pause' if want else 'resume')
        return {'text': 'ok: %s %s\n' % (self.instrument_id, 'paused' if want else 'resumed'), 'json': {'paused': want}}

    def h_feeder(self, ctx, p):
        m = self.vmap('feeder')
        want = p.pos[0] == 'on'
        s = self.slot('feeder')
        if s and bool(ctx.v(s)) == want:
            return {'text': 'ok: the feeder of %s is already %s\n' % (self.instrument_id, p.pos[0]), 'json': {'feeder': want, 'changed': False}}
        ctx.call(m['cap'], {m['arg']: want}, 'feeder %s' % p.pos[0])
        return {'text': 'ok: %s feeder %s\n' % (self.instrument_id, p.pos[0]), 'json': {'feeder': want, 'changed': True}}

    def wait_slot(self, ctx, test, max_s, what, poll_s=0.1):
        if test(ctx.view):
            return 0.0
        w = ctx.advance(max_s, until=test, step=what, poll_s=poll_s)
        if not ctx.dry_run and not test(ctx.view):
            ctx.fail(refused('NOT_THERE', '%s did not finish within %s.' % (what, units.fmt_s(max_s)), 'check `chemlab run %s status`.' % self.instrument_id))
        return w

    def h_valve(self, ctx, p):
        m = self.vmap('valve')
        want = p.pos[0] == 'open'
        ctx.call(m['cap'], {m['arg']: want}, '%s the release valve' % p.pos[0])
        if not ctx.dry_run:
            lo, hi = m.get('closed', 0.0), m.get('opened')

            def there(view):
                a = ctx.v(m['slot'])
                return finite(a) and (abs(a - hi) <= 1e-9 if want else a <= lo + 1e-9)
            self.wait_slot(ctx, there, 30.0, 'the valve %s' % ('opening' if want else 'closing'))
            if want and m.get('release_slot'):                                  # opening releases the load: wait until it is off
                below = float(m.get('released_below', 10.0))
                self.wait_slot(ctx, lambda view: (ctx.v(m['release_slot']) or 0.0) < below, 120.0, 'the load release', poll_s=0.5)
        load = self.slot_si(ctx, self.slot('load'))
        return {'text': 'ok: %s valve %s (load %s)\n' % (self.instrument_id, 'open' if want else 'closed', units.fmt(load, 'force') if finite(load) else 'n/a'),
                'json': {'valve_open': want, 'load_n': load}}

    def h_leadscrew(self, ctx, p):
        m = self.vmap('leadscrew')
        v = self.parse_setting(ctx, m['cap'], m['arg'], p.raw['POS'], 'length', what='POS')
        self.need(ctx, 'leadscrew')
        ctx.call(m['cap'], {m['arg']: v}, 'set the leadscrew to %s' % units.fmt(v, 'length'))
        if not ctx.dry_run:
            self.wait_slot(ctx, lambda view: finite(ctx.v(m['slot'])) and abs(ctx.v(m['slot']) - v) <= 1e-9, 60.0, 'the leadscrew', poll_s=0.5)
        return {'text': 'ok: %s leadscrew at %s\n' % (self.instrument_id, units.fmt(v, 'length')), 'json': {'position_m': v}}

    def h_pump(self, ctx, p):
        m = self.vmap('pump')
        to, hold = p.get('to'), p.get('hold')
        if hold and to is None:
            ctx.fail(invalid('NEED_TO', '--hold needs --to LOAD (the load to keep).', 'e.g. `chemlab run %s pump --to 68kN --hold`.' % self.instrument_id))
        try:
            n = int(p.pos[0]) if p.pos else (None if to is not None else 1)
        except ValueError:
            ctx.fail(invalid('BAD_NUMBER', '%r is not a number of strokes.' % p.pos[0], 'give a whole number, e.g. `pump 5`.'))
        if n is not None and not 1 <= n <= int(self.param('max_strokes', 500)):
            ctx.fail(invalid('OUT_OF_RANGE', '%d strokes is not allowed.' % n, 'give 1 to %d strokes.' % int(self.param('max_strokes', 500))))
        if to is not None:
            lim = ctx.v(self.slot('limit'))
            ctx.check(not finite(lim) or to <= lim + 1e-9, 'ABOVE_LIMIT', 'the load %s is above the load limit of %s (%s).' % (
                units.fmt(to, 'force'), self.instrument_id, units.fmt(lim, 'force')), 'raise the limit first (`method limit=...`) or press less.',
                label='below the load limit')
        self.need(ctx, 'pump')
        if ctx.dry_run:
            ctx.plan.append({'capability': m['cap'], 'arguments': {}, 'step': 'pump strokes (%s)' % (n if n else 'until %s' % units.fmt(to, 'force'))})
            return {'text': '', 'json': {}}
        max_s = p.get('max') or 600.0
        t0, strokes = ctx.now(), 0
        load = lambda: self.slot_si(ctx, self.slot('load')) or 0.0                     # noqa: E731
        active = self.slot('active')

        def stroke():
            ctx.call(m['cap'], {}, 'pump stroke %d' % (strokes + 1))
            self.wait_slot(ctx, lambda view: not ctx.v(active), 10.0, 'the pump stroke')
        while ctx.now() - t0 < max_s:
            if n is not None and strokes >= n:
                break
            if to is not None and not hold and load() >= to:
                break
            if hold:
                if ctx.v(self.slot('phase')) != 'running':
                    break
                if load() >= to:
                    ctx.advance(0.5, step='watch the gauge')
                    continue
            stroke()
            strokes += 1
        text = 'ok: %s pumped %d stroke%s in %s; gauge %s' % (self.instrument_id, strokes, '' if strokes == 1 else 's', units.fmt_s(ctx.now() - t0),
                                                             units.fmt(load(), 'force'))
        if to is not None and not hold and load() < to:
            ctx.fail(refused('NOT_REACHED', '%s reached only %s of %s after %d strokes.' % (self.instrument_id, units.fmt(load(), 'force'), units.fmt(to, 'force'), strokes),
                             'check the valve is closed and the limit (`chemlab run %s status`); pump again.' % self.instrument_id))
        if hold:
            text += '; press cycle %s' % ctx.v(self.slot('phase'))
        return {'text': text + '\n', 'json': {'strokes': strokes, 'load_n': load(), 'phase': ctx.v(self.slot('phase'))}}

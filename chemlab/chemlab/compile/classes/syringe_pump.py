"""Liquid class, syringe-pump member (New Era NE-1000 Multi-Phaser): the member-specific adapter of configs/adapters/liquid.json.

The Flex verbs (classes/liquid.py) are untouched; this member has its own verbs because a syringe pump has no deck, tips or
wells - a syringe and a line instead.  Only the panel-free remote command set of the contract is used (syringe.set_diameter,
rate.set, volume.set, direction.set, pump.run / stop / purge, phase.select / set_function, program.clear, volume.clear); the
keypad, serial and TTL capabilities stay generic.

    syringe [DIAMETER | TYPE | loaded]       the inside diameter: 14.43mm, a syringe type (bd_10ml ...), or the loaded type
    rate RATE                                pumping rate (1mL/h, 50uL/min ...; the pump shows it in those units)
    volume VOLUME | clear                    volume to dispense (0 = pump until stopped) / clear the dispensed counters
    direction infuse | withdraw | reverse
    run [--phase N] [--wait] [--max T]       start pumping (a job: it ends when the volume is done or the program stops)
    stop                                     stop (or pause) pumping
    purge on | off                           purge at the top speed
    phase [N [FUNCTION [KEY=VALUE ...]]]     show the program, or set phase N (rate, fill, increment, decrement, stop, jump,
                                             loop-start, loop, loop-end, pause, beep, ...); `phase clear` clears the program

Class checks before anything is sent: power on; the environment set up by the lab; no alarm; a diameter set before a rate,
volume or run; rates within the pump's plunger-speed range for the set diameter (0.004205 cm/h to 5.1005 cm/min); not running
for settings the pump refuses while running.

Manual references (references/manuals/newera_ne1000/, internal; NE-1000 user manual 1200-01): direction key p.9; syringe inside
diameter 7.1 p.12; operating the pump, purging, volume dispensed 7.3-7.6 p.13; paused / stalled 7.7-7.8 p.14; pumping programs
and program functions 9.1-9.3 pp.18-23; specifications 12.5 p.49; syringe diameters and rate limits 12.7 p.50.
"""
import math
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused
from ..jobs import StateJob
from ..verbs import Arg, Opt, Verb

REF = ('NE-1000 user manual: syringe diameter 7.1 p.12, operating / purging / volume dispensed pp.13, paused / stalled p.14, '
       'pumping programs 9.1-9.3 pp.18-23, syringe diameters and rate limits 12.7 p.50')
DIRECTIONS = {'infuse': 'INF', 'withdraw': 'WDR', 'reverse': 'REV'}
FUNCTIONS = {'rate': 'RAT', 'fill': 'FIL', 'increment': 'INC', 'decrement': 'DEC', 'stop': 'STP', 'jump': 'JMP',
             'loop-start': 'LPS', 'loop': 'LOP', 'loop-end': 'LPE', 'pause': 'PAS', 'if': 'IF', 'event': 'EVN', 'event-square': 'EVS',
             'event-reset': 'EVR', 'clear-dispensed': 'CLD', 'beep': 'BEP', 'out': 'OUT'}
RATE_UNITS = {'ml/h': 'MH', 'ml/min': 'MM', 'ul/h': 'UH', 'ul/min': 'UM', 'µl/h': 'UH', 'µl/min': 'UM'}
NUM = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'


class SyringePumpAdapter(ClassAdapter):
    CLASS = 'liquid'
    TITLE = 'liquid handling (syringe pump)'
    ABOUT = ('A programmable syringe pump: set the syringe, rate, volume and direction, run / stop / purge, and the multi-phase '
             'program; volumes dispensed are counted from the set diameter.')
    STANDARDS = 'NE-1000 RS-232 basic command set (DIA, RAT, VOL, DIR, RUN, STP, PUR, PHN, FUN) as the contract models it'
    UNIT_KINDS = ('volume', 'flow', 'length', 'duration')
    PATH_GROUPS = (('run', 'stop'),)
    ALWAYS = ('status',)
    CODES = {
        'DIAMETER_NOT_SET': ('DIAMETER_NOT_SET', 'no syringe diameter is set on {inst}.', 'set it first: `chemlab run {inst} syringe bd_10ml` '
                             '(or a diameter such as 14.43mm).'),
        'OUT_OF_RANGE': ('OUT_OF_RANGE', 'that rate is outside what {inst} can pump with this syringe diameter.',
                         'see the range in `chemlab help {inst} rate`.'),
        'ALARM_ACTIVE': ('ALARM', '{inst} shows an alarm (stall, program error or out of range).',
                         'see `chemlab run {inst} status`; clear the cause (e.g. an occluded line), then `chemlab run {inst} stop`.'),
        'PROGRAM_OPERATING': ('BUSY', '{inst} is pumping, so it does not take that setting now.', 'stop it first: `chemlab run {inst} stop`.'),
        'ALREADY_RUNNING': ('BUSY', '{inst} is already pumping.', 'wait for the job, or `chemlab run {inst} stop`.'),
        'NOT_APPLICABLE': ('NOT_APPLICABLE', 'the selected program phase of {inst} is not a rate phase, or the change is not possible while '
                           'pumping.', 'select a rate phase (`chemlab run {inst} phase 1`) or stop pumping first.'),
        'UNITS_WHILE_PUMPING': ('BUSY', '{inst} does not change the rate units while pumping.', 'give the rate in the units it shows, or stop first.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab.', 'ask the lab operator to set it up.'),
    }

    # ------------------------------------------------------------------ helpers
    def syringe_table(self):
        return self.param('syringe_inside_diameter_m') or {}

    def area(self, d_m):
        return math.pi * d_m * d_m / 4.0

    def rate_range(self, d_m):
        """(min, max) rate in m3/s for a diameter, from the plunger-speed range (manual 12.5 / 12.7)."""
        lo, hi = float(self.param('min_plunger_speed_m_s')), float(self.param('max_plunger_speed_m_s'))
        a = self.area(d_m)
        return a * lo, a * hi

    def running(self, values):
        """Pumping or inside a running program (a pause phase, a trigger wait, purging); 'paused' (stopped once by the user) takes
        settings again (manual 7.7 p.14: a change while paused cancels the pause and the program restarts at phase 1)."""
        return self.val(values, 'run') not in (None, 'stopped', 'paused')

    def val(self, values, role):
        s = self.slot(role)
        if s is None:
            return None
        return values.get(s) if isinstance(values, dict) else values.v(s)

    def need_ready(self, ctx, diameter=True):
        inst = self.instrument_id
        ctx.check(bool(ctx.v(self.slot('power'))), 'POWER_OFF', '%s is switched off.' % inst, 'switch it on: `chemlab run %s power on`.' % inst,
                  label='power on')
        if self.slot('configured'):
            ctx.check(bool(ctx.v(self.slot('configured'))), 'NOT_SET_UP', '%s is not set up in this lab.' % inst,
                      'ask the lab operator to set it up.', label='set up by the lab')
        alarm = ctx.v(self.slot('alarm'))
        ctx.check(alarm is None, 'ALARM', '%s shows an alarm: %s.' % (inst, alarm),
                  'clear the cause (a stall is often an occluded line or the end of the syringe), then `chemlab run %s stop` to '
                  'reset it (manual 7.8 p.14).' % inst, label='no alarm')
        if diameter:
            d = ctx.v(self.slot('diameter'))
            ctx.check(finite(d) and d > 0, 'DIAMETER_NOT_SET', 'no syringe diameter is set on %s.' % inst,
                      'set it first: `chemlab run %s syringe bd_10ml` (a syringe type) or `syringe 14.43mm`.' % inst, label='diameter set')

    def my_run(self, ctx):
        """True when the current run is one this caller started through chemlab (the pump refuses changes to another user's run)."""
        from ..jobs import latest
        job = latest(ctx.core, ctx.caller, self.instrument_id)
        return job is not None and job.description.endswith(' pumping') and job.state in ('running',)

    def need_stopped(self, ctx, doing):
        if not self.running(ctx):
            ctx.checks.append('not pumping')
            return
        inst = self.instrument_id
        fix = ('stop it first: `chemlab run %s stop`.' % inst if self.my_run(ctx) else
               'it was started by another user (the pump refuses changes to their run): wait until it has ended '
               '(`chemlab run %s status`, `chemlab wait 30`), then retry.' % inst)
        ctx.fail(refused('BUSY', '%s is not stopped (%s), so it cannot %s now.' % (inst, ctx.v(self.slot('run')), doing), fix))

    def explain_refusal(self, ctx, cap, args, response):
        if (response.get('error') or {}).get('code') == 'ACCESS_DENIED':
            return refused('NOT_YOURS', 'the run on %s was started by another user; the pump refuses to stop or change it.' % self.instrument_id,
                           'wait until it has ended (`chemlab run %s status`, `chemlab wait 30`).' % self.instrument_id)
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)

    def ml(self, m3):
        return units.fmt(m3, 'volume') if finite(m3) else 'n/a'

    def flow(self, m3s):
        if not finite(m3s):
            return 'n/a'
        if m3s and abs(m3s) < 1e-9 / 60:
            return '%s µL/h' % ('%.4g' % (m3s * 1e9 * 3600))
        return '%s mL/h' % ('%.4g' % (m3s * 1e6 * 3600)) if abs(m3s) < 1e-6 / 60 * 10 else units.fmt(m3s, 'flow')

    def parse_rate(self, ctx, text, what='RATE'):
        t = (text or '').strip().replace(' ', '')
        m = re.fullmatch('(%s)(.*)' % NUM, t)
        if not m:
            ctx.fail(invalid('BAD_NUMBER', '%s %r is not a rate.' % (what, text), 'write it like 2mL/h or 50uL/min.'))
        unit = m.group(2).lower().replace('μ', 'µ')
        si = units.parse(t if unit else t + 'ml/h', 'flow', what=what)
        return si, RATE_UNITS.get(unit or 'ml/h')

    def check_rate(self, ctx, si, d=None):
        d = d if d is not None else ctx.v(self.slot('diameter'))
        if not (finite(d) and d > 0) or si == 0:
            return
        lo, hi = self.rate_range(d)
        if si < lo * (1 - 1e-9) or si > hi * (1 + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'the rate %s is outside what %s pumps with a %.4g mm syringe (%s to %s).' % (
                self.flow(si), self.instrument_id, d * 1e3, self.flow(lo), self.flow(hi)),
                'give a rate in that range, or use a syringe of another size (manual 12.7 p.50).'))
        ctx.checks.append('rate within %s .. %s' % (self.flow(lo), self.flow(hi)))

    # ------------------------------------------------------------------ summary
    def summary(self, values):
        if not self.val(values, 'power'):
            return ['power off'], {'power': False}
        run, d, rate = self.val(values, 'run'), self.val(values, 'diameter'), self.val(values, 'rate')
        disp, syr, alarm = self.val(values, 'dispensed') or {}, self.val(values, 'syringe') or {}, self.val(values, 'alarm')
        prog = self.val(values, 'program') or {}
        sel = prog.get('selected') if isinstance(prog, dict) else None
        phases = prog.get('phases') if isinstance(prog, dict) else None
        ph = phases[sel - 1] if isinstance(phases, list) and isinstance(sel, int) and 0 < sel <= len(phases) else None
        lines = ['%s%s · phase %s · pumping %s %s' % (run, (' (ALARM %s)' % alarm) if alarm else '', self.val(values, 'phase'), self.flow(rate),
                                                     self.val(values, 'direction') or ''),
                 'syringe %s (%s) · diameter %s · infused %s · withdrawn %s' % (
                     syr.get('type') or 'none loaded', syr.get('liquid_id') or '-', ('%.4g mm' % (d * 1e3)) if finite(d) and d else 'not set',
                     self.ml(disp.get('infused_m3')), self.ml(disp.get('withdrawn_m3')))]
        if isinstance(ph, dict):
            lines.append('selected phase %s: %s rate %s volume %s %s' % (sel, ph.get('function'), self.flow(ph.get('rate_m3_s')),
                                                                         self.ml(ph.get('volume_m3')), ph.get('direction')))
        js = {'power': True, 'run_state': run, 'alarm': alarm, 'phase': self.val(values, 'phase'), 'rate_m3_s': rate,
              'direction': self.val(values, 'direction'), 'diameter_m': d, 'syringe': syr, 'dispensed': disp, 'selected_phase': ph}
        return lines, js

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        wait = {'wait': Opt('wait until pumping has ended (lab time moves)', kind='flag'),
                'max': Opt('longest wait with --wait (default 2h)', kind='duration', metavar='TIME')}
        return {
            'syringe': Verb('syringe', 'show the syringe, or set the inside diameter: a diameter (14.43mm), a syringe type (bd_10ml ...) '
                            'or `loaded` (the type the operator loaded)', self.h_syringe,
                            positionals=[Arg('DIAMETER|TYPE', '14.43mm (a bare number is mm), a type such as bd_10ml, or loaded', required=False)],
                            preconditions=['power on', 'not pumping'], errors=['UNKNOWN_SYRINGE, OUT_OF_RANGE (usage)', 'BUSY, NO_SYRINGE (refused)'],
                            example='chemlab run INST syringe bd_10ml', reference=REF,
                            details='The pump computes volumes from this diameter; a wrong diameter gives wrong volumes (manual 7.1 p.12).'),
            'rate': Verb('rate', 'set the pumping rate (2mL/h, 50uL/min ...; a bare number is mL/h)', self.h_rate,
                         positionals=[Arg('RATE', 'rate with a unit')], preconditions=['power on', 'diameter set', 'rate within the range '
                                                                                      'for the diameter'],
                         errors=['OUT_OF_RANGE (usage)', 'DIAMETER_NOT_SET, ALARM, NOT_APPLICABLE (refused)'],
                         example='chemlab run INST rate 2mL/h', reference=REF),
            'volume': Verb('volume', 'set the volume to dispense (0 = until stopped), or `volume clear` for the dispensed counters',
                           self.h_volume, positionals=[Arg('VOLUME', 'e.g. 0.5mL or 250uL (a bare number is mL), 0, or clear')],
                           preconditions=['power on', 'diameter set', 'not pumping'], errors=['BUSY, DIAMETER_NOT_SET (refused)'],
                           example='chemlab run INST volume 1mL', reference=REF),
            'direction': Verb('direction', 'set the pumping direction', self.h_direction,
                              positionals=[Arg('DIRECTION', 'infuse, withdraw or reverse', choices=list(DIRECTIONS))],
                              preconditions=['power on', 'while pumping only in continuous mode (volume 0)'],
                              errors=['NOT_APPLICABLE (refused)'], example='chemlab run INST direction infuse', reference=REF),
            'run': Verb('run', 'start pumping (returns job-N; it ends when the volume is dispensed or the program stops)', self.h_run,
                        options=dict(wait, phase=Opt('start at this program phase (default: phase 1)', metavar='N')), long_action=True,
                        preconditions=['power on', 'diameter set', 'no alarm', 'not pumping'],
                        errors=['DIAMETER_NOT_SET, BUSY, ALARM (refused)'], example='chemlab run INST run --wait', reference=REF),
            'stop': Verb('stop', 'stop pumping (a running program pauses; a second stop resets it to phase 1)', self.h_stop,
                         preconditions=['power on', 'your own run (the pump refuses another user\'s)'],
                         errors=['NOT_RUNNING, NOT_YOURS (refused)'], example='chemlab run INST stop', reference=REF),
            'purge': Verb('purge', 'purge at the top speed (on) or stop purging (off)', self.h_purge,
                          positionals=[Arg('on|off', 'on or off', choices=['on', 'off'])],
                          preconditions=['power on', 'diameter set', 'not pumping'], errors=['BUSY, DIAMETER_NOT_SET (refused)'],
                          example='chemlab run INST purge on', reference=REF),
            'phase': Verb('phase', 'show the pumping program, set phase N (FUNCTION KEY=VALUE ...), or `phase clear`', self.h_phase,
                          positionals=[Arg('N', 'phase number 1-41, or clear', required=False),
                                       Arg('FUNCTION', 'rate | fill | increment | decrement | stop | jump | loop-start | loop | loop-end | '
                                                       'pause | beep | ...', required=False)],
                          variadic=('KEY=VALUE', 'rate=2mL/h volume=0.5mL direction=infuse|withdraw param=N'),
                          preconditions=['power on', 'not pumping', 'rates within range'], errors=['BAD_FUNCTION, BAD_SETTING (usage)',
                                                                                                   'BUSY (refused)'],
                          example='chemlab run INST phase 1 rate rate=2mL/h volume=0.5mL direction=infuse', reference=REF,
                          details='Functions follow manual 9.3 (pp.20-23): rate (RAT) pumps at a rate for a volume, fill (FIL) withdraws '
                                  'back, stop (STP) ends the program, pause (PAS) waits param seconds, loop-start / loop (LPS / LOP) repeat, '
                                  'jump (JMP) goes to phase param.'),
        }

    def h_syringe(self, ctx, p):
        m = self.vmap('syringe')
        inst = self.instrument_id
        cur, syr = ctx.v(self.slot('diameter')), ctx.v(self.slot('syringe')) or {}
        table = self.syringe_table()
        if not p.pos:
            loaded = syr.get('type')
            nominal = table.get(loaded) if loaded else None
            text = 'syringe of %s: %s (%s) · diameter %s%s\n' % (
                inst, loaded or 'none loaded', syr.get('liquid_id') or '-', ('set to %.4g mm' % (cur * 1e3)) if finite(cur) and cur else 'not set',
                (' · %s has %.4g mm' % (loaded, nominal * 1e3)) if nominal else '')
            if nominal and finite(cur) and cur and abs(cur - nominal) > 1e-7:
                text += '  the set diameter differs from the loaded syringe: `chemlab run %s syringe loaded`\n' % inst
            return {'text': text, 'json': {'syringe': syr, 'diameter_m': cur, 'nominal_diameter_m': nominal}}
        arg = p.pos[0].strip()
        key = arg.lower()
        if key == 'loaded':
            key = syr.get('type')
            ctx.check(key is not None, 'NO_SYRINGE', 'no syringe is loaded on %s.' % inst,
                      'the lab operator loads it; or give its diameter or type: `chemlab run %s syringe bd_10ml`.' % inst, label='syringe loaded')
        if key in table:
            d, how = float(table[key]), '%s (manual 12.7 p.50)' % key
        elif re.fullmatch('(%s)(.*)' % NUM, key):
            d = units.parse(key if re.search('[a-zµ]', key) else key + 'mm', 'length', what='DIAMETER')
            how = 'given'
        else:
            close = [t for t in table if key.replace('-', '').replace(' ', '') in t.replace('_', '')][:6]
            ctx.fail(invalid('UNKNOWN_SYRINGE', '%r is not a syringe type %s knows.' % (arg, inst),
                             'types like %s (the full list: `chemlab describe %s` under syringe.load), or give the diameter, e.g. 14.43mm.'
                             % (', '.join(close or sorted(table)[:6]), inst)))
        lo, hi = self.arg_range(m['cap'], m.get('arg', 'diameter_m'))
        if (lo is not None and d < lo) or (hi is not None and d > hi):
            ctx.fail(invalid('OUT_OF_RANGE', 'the diameter %.4g mm is outside %.4g..%.4g mm.' % (d * 1e3, lo * 1e3, hi * 1e3), 'check the value.'))
        self.need_ready(ctx, diameter=False)
        self.need_stopped(ctx, 'change the syringe diameter')
        if finite(cur) and abs(cur - d) <= 1e-12:
            return {'text': 'ok: the diameter of %s is already %.4g mm\n' % (inst, d * 1e3), 'json': {'diameter_m': d, 'changed': False}}
        ctx.call(m['cap'], {m.get('arg', 'diameter_m'): units.sig(d)}, 'set the syringe diameter to %.4g mm (%s)' % (d * 1e3, how))
        lo_r, hi_r = self.rate_range(d)
        return {'text': 'ok: syringe diameter of %s set to %.4g mm (%s); rates %s to %s\n' % (inst, d * 1e3, how, self.flow(lo_r), self.flow(hi_r)),
                'json': {'diameter_m': d, 'source': how, 'rate_min_m3_s': lo_r, 'rate_max_m3_s': hi_r, 'changed': True}}

    def h_rate(self, ctx, p):
        m = self.vmap('rate')
        si, ru = self.parse_rate(ctx, p.raw['RATE'])
        self.need_ready(ctx)
        self.check_rate(ctx, si)
        args = {m.get('arg', 'rate_m3_s'): units.sig(si)}
        if ru and not self.running(ctx):
            args[m.get('units_arg', 'units')] = ru
        ctx.call(m['cap'], args, 'set the rate to %s' % self.flow(si))
        return {'text': 'ok: rate of %s set to %s\n' % (self.instrument_id, self.flow(si)), 'json': {'rate_m3_s': si, 'units': args.get('units')}}

    def h_volume(self, ctx, p):
        inst = self.instrument_id
        arg = p.raw['VOLUME'].strip().lower()
        if arg == 'clear':
            m = self.vmap('volume')
            self.need_ready(ctx, diameter=False)
            self.need_stopped(ctx, 'clear the dispensed volume')
            ctx.call(m['clear_cap'], {m.get('clear_arg', 'direction'): 'BOTH'}, 'clear the dispensed volumes')
            return {'text': 'ok: dispensed volumes of %s cleared\n' % inst, 'json': {'cleared': True}}
        m = self.vmap('volume')
        v = units.parse(arg if re.search('[a-zµ]', arg) else arg + 'ml', 'volume', what='VOLUME')
        lo, hi = self.arg_range(m['cap'], m.get('arg', 'volume_m3'))
        if hi is not None and v > hi:
            ctx.fail(invalid('OUT_OF_RANGE', 'the volume %s is more than %s takes (%s).' % (self.ml(v), inst, self.ml(hi)), 'give a smaller volume.'))
        self.need_ready(ctx)
        self.need_stopped(ctx, 'change the volume')
        ctx.call(m['cap'], {m.get('arg', 'volume_m3'): units.sig(v)}, 'set the volume to %s' % (self.ml(v) if v else '0 (until stopped)'))
        return {'text': 'ok: volume of %s set to %s\n' % (inst, self.ml(v) if v else '0 (pumps until stopped)'), 'json': {'volume_m3': v}}

    def h_direction(self, ctx, p):
        m = self.vmap('direction')
        want = DIRECTIONS[p.pos[0]]
        self.need_ready(ctx, diameter=False)
        if want != 'REV' and ctx.v(self.slot('direction')) == want and not self.running(ctx):
            return {'text': 'ok: %s already pumps %s\n' % (self.instrument_id, p.pos[0]), 'json': {'direction': want, 'changed': False}}
        ctx.call(m['cap'], {m.get('arg', 'direction'): want}, 'set the direction to %s' % p.pos[0])
        return {'text': 'ok: direction of %s: %s\n' % (self.instrument_id, ctx.v(self.slot('direction'))), 'json': {'direction': ctx.v(self.slot('direction'))}}

    def run_job(self, ctx, cap):
        ad, inst = self, self.instrument_id

        def state(c):
            st, alarm = c.v(ad.slot('run')), c.v(ad.slot('alarm'))
            disp = c.v(ad.slot('dispensed')) or {}
            text = '%s; infused %s, withdrawn %s' % (st, ad.ml(disp.get('infused_m3')), ad.ml(disp.get('withdrawn_m3')))
            if alarm:
                return 'failed', text + '; ALARM %s' % alarm, {'state': st, 'alarm': alarm}
            if st == 'stopped':
                return 'succeeded', text, {'state': st}
            if st == 'paused':                                      # stopped once by the user: this run is over
                return 'stopped', text + ' (paused by stop; `run` resumes, a second stop resets)', {'state': st}
            return 'running', text, {'state': st}

        def stop(c):
            sm = ad.vmap('stop')
            c.call(sm['cap'], {}, 'stop pumping')
            return 'stopped with %s' % sm['cap']
        return StateJob(ctx.core, self, ctx.req, '%s pumping' % inst, state, stop=stop, max_s=float(self.param('max_run_s', 7200.0)),
                        poll_s=float(self.param('poll_s', 1.0)))

    def h_run(self, ctx, p):
        m = self.vmap('run')
        inst = self.instrument_id
        args = {}
        if p.get('phase') is not None:
            try:
                n = int(p.get('phase'))
            except ValueError:
                n = 0
            if not 1 <= n <= 41:
                ctx.fail(invalid('BAD_PHASE', '--phase takes 1 to 41.', 'e.g. --phase 1.'))
            args[m.get('phase_arg', 'phase')] = n
        self.need_ready(ctx)
        self.need_stopped(ctx, 'start again')                             # a paused pump resumes with run
        ctx.call(m['cap'], args, 'start pumping', readback=lambda v: None if ctx.v(self.slot('run')) != 'stopped' or ctx.v(self.slot('alarm'))
                 else None)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.run_job(ctx, m['cap'])
        js = {'job': job.handle, 'run_state': ctx.v(self.slot('run')), 'rate_m3_s': ctx.v(self.slot('rate')), 'direction': ctx.v(self.slot('direction'))}
        if p.get('wait'):
            w = job.wait_reply(ctx.core, ctx.req, p.get('max') or float(self.param('max_run_s', 7200.0)))
            ctx.refresh()
            disp = ctx.v(self.slot('dispensed')) or {}
            js.update({'status': w['json'].get('status'), 'waited_s': w['json'].get('waited_s'), 'dispensed': disp,
                       'alarm': ctx.v(self.slot('alarm'))})
            return {'text': 'pumping on %s: %s after %s (infused %s, withdrawn %s)%s\n' % (
                inst, w['json'].get('status'), units.fmt_s(w['json'].get('waited_s') or 0), self.ml(disp.get('infused_m3')),
                self.ml(disp.get('withdrawn_m3')), ('; ALARM %s' % ctx.v(self.slot('alarm'))) if ctx.v(self.slot('alarm')) else ''), 'json': js}
        return {'text': 'started %s: %s pumping %s %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, inst, self.flow(js['rate_m3_s']), js['direction'], job.handle, job.handle, job.handle), 'json': js}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        inst = self.instrument_id
        ctx.check(bool(ctx.v(self.slot('power'))), 'POWER_OFF', '%s is switched off.' % inst, 'switch it on: `chemlab run %s power on`.' % inst,
                  label='power on')
        ctx.check(ctx.v(self.slot('run')) not in (None, 'stopped') or ctx.v(self.slot('alarm')) is not None, 'NOT_RUNNING',
                  '%s is not pumping.' % inst, 'nothing to stop.', label='pumping, paused or an alarm to reset')
        ctx.call(m['cap'], {}, 'stop pumping')
        disp = ctx.v(self.slot('dispensed')) or {}
        return {'text': 'ok: %s %s (infused %s, withdrawn %s)\n' % (inst, ctx.v(self.slot('run')), self.ml(disp.get('infused_m3')),
                                                                    self.ml(disp.get('withdrawn_m3'))),
                'json': {'run_state': ctx.v(self.slot('run')), 'dispensed': disp}}

    def h_purge(self, ctx, p):
        m = self.vmap('purge')
        on = p.pos[0] == 'on'
        self.need_ready(ctx)
        if on:
            self.need_stopped(ctx, 'purge')
        ctx.call(m['cap'], {m.get('arg', 'on'): on}, 'purge %s' % p.pos[0])
        return {'text': 'ok: %s purge %s (%s)\n' % (self.instrument_id, p.pos[0], ctx.v(self.slot('run'))), 'json': {'purge': on, 'run_state': ctx.v(self.slot('run'))}}

    def h_phase(self, ctx, p):
        m = self.vmap('phase')
        inst = self.instrument_id
        if not p.pos:
            prog = ctx.v(self.slot('program')) or {}
            rows = []
            for i, ph in enumerate(prog.get('phases') or []):
                if ph:
                    rows.append('  %2d %s rate %s volume %s %s%s' % (i + 1, ph.get('function'), self.flow(ph.get('rate_m3_s')), self.ml(ph.get('volume_m3')),
                                                                  ph.get('direction'), (' param %s' % ph.get('parameter')) if ph.get('parameter') else ''))
            return {'text': 'program of %s (phase %s selected):\n%s\n' % (inst, prog.get('selected'), '\n'.join(rows) or '  (empty)'),
                    'json': {'program': prog}}
        if p.pos[0].lower() == 'clear':
            self.need_ready(ctx, diameter=False)
            self.need_stopped(ctx, 'clear the program')
            ctx.call(m['clear_cap'], {}, 'clear the program')
            return {'text': 'ok: program of %s cleared\n' % inst, 'json': {'cleared': True}}
        try:
            n = int(p.pos[0])
        except ValueError:
            n = 0
        if not 1 <= n <= 41:
            ctx.fail(invalid('BAD_PHASE', 'phase takes a number 1 to 41 (or clear), not %r.' % p.pos[0], 'e.g. `phase 1 rate rate=2mL/h volume=1mL`.'))
        args = None
        if len(p.pos) > 1:
            fn = FUNCTIONS.get(p.pos[1].lower(), p.pos[1].upper() if p.pos[1].upper() in FUNCTIONS.values() else None)
            if fn is None:
                ctx.fail(invalid('BAD_FUNCTION', '%r is not a program function.' % p.pos[1], 'functions: %s.' % ', '.join(FUNCTIONS)))
            args = {'function': fn}
            for item in p.extra:
                k, eq, v = item.partition('=')
                if not eq:
                    ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'e.g. rate=2mL/h volume=0.5mL direction=infuse param=3.'))
                k = k.lower()
                if k == 'rate':
                    si, ru = self.parse_rate(ctx, v, 'rate=')
                    self.check_rate(ctx, si)
                    args['rate_m3_s'] = units.sig(si)
                    if ru:
                        args['rate_units'] = ru
                elif k == 'volume':
                    args['volume_m3'] = units.sig(units.parse(v if re.search('[a-zµ]', v.lower()) else v + 'ml', 'volume', what='volume='))
                elif k == 'direction':
                    if v.lower() not in ('infuse', 'withdraw'):
                        ctx.fail(invalid('BAD_SETTING', 'direction= takes infuse or withdraw.', 'e.g. direction=infuse.'))
                    args['direction'] = DIRECTIONS[v.lower()]
                elif k in ('param', 'parameter'):
                    try:
                        args['parameter'] = int(v)
                    except ValueError:
                        ctx.fail(invalid('BAD_SETTING', 'param= takes a whole number 0-99.', 'e.g. param=3.'))
                elif k == 'tenths':
                    args['tenths'] = v.lower() in ('1', 'true', 'yes', 'on')
                else:
                    ctx.fail(invalid('BAD_SETTING', 'phase has no setting %r.' % k, 'settings: rate=, volume=, direction=, param=, tenths=.'))
        elif p.extra:
            ctx.fail(invalid('BAD_SETTING', 'give the FUNCTION before its settings.', 'e.g. `phase 1 rate rate=2mL/h volume=1mL`.'))
        self.need_ready(ctx, diameter=args is not None and 'rate_m3_s' in (args or {}))
        self.need_stopped(ctx, 'change the program')
        ctx.call(m['select_cap'], {m.get('select_arg', 'phase'): n}, 'select phase %d' % n)
        if args:
            ctx.call(m['cap'], args, 'set phase %d to %s' % (n, args['function']))
        return {'text': 'ok: phase %d of %s%s\n' % (n, inst, (' set: %s' % short(args, 200)) if args else ' selected'), 'json': {'phase': n, 'function': args}}

    _ = (ClassAdapter, refused)

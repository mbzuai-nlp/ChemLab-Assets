"""Sample-introduction class ("sample_intro"): front ends that prepare a sample and inject it into an analyzer - headspace
(Agilent 8697), purge and trap (Teledyne Tekmar Atomx XYZ), laser ablation (Teledyne CETAC Analyte Excite+), pyrolysis (Frontier
EGA/PY-3030D) and thermal desorption (Markes UNITY-xr).  Members and mappings: configs/adapters/sample_intro.json.

Verbs: method KEY=VALUE ..., load SAMPLE --vial N, unload --vial N, set-temp T --zone Z, start [--vial N | --vials 1,2,3]
[--injections K] (job), inject [--wait] (the next manual step of a pyrolyzer run), stop, result [--save-to F], position X Y Z
(laser stage), status.  power / open / close come from the shared layer.

Method dictionary (class keys take units; native argument names also work, a bare number is in the unit the contract declares):

    equilibration=30min gc-cycle=10min shake=3 vial-size=20mL fill-pressure=1bar        (headspace)
    matrix=water sample-volume=5mL dilution=1 purge-temp=20C purge-time=11min purge-flow=40mL/min dry-purge-time=1min
    desorb-temp=250C desorb-time=4min bake-temp=260C bake-time=2min                      (purge and trap)
    mode=single|double|ega pyrolysis-temp=600C pyrolysis-time=0.1min td-start=50C td-end=300C ramp=20K/min hold=1min
    interface-temp=300C carrier-flow=1.2mL/min                                          (pyrolyzer)
    mode=analyze desorb-temp=300C desorb-time=10min trap-low=-10C trap-high=300C trap-time=3min trap-ramp=40K/s
    valve-temp=190C line-temp=200C purge-flow=20mL/min purge-time=1min                   (thermal desorber)
    mode=spot|scan shape=circle|square spot=80um fluence=60000 frequency=10Hz shots=10 washout=1s scan-speed=0.4mm/s   (laser)

Settings left out keep their current value (state slots), else the last accepted method on the instrument, else the documented
default of the contract (the reply says which).  Vial positions are tray numbers as printed on the instrument (1-based); the Atomx
contract counts from 0 and the mapping converts.

Vendor-neutral references: USP <467> (headspace residual solvents), EPA 5030C / 524.2 (purge and trap), EPA TO-17 / ISO 16000-6
(thermal desorption); pyrolysis-GC and laser ablation are method-specific.  Manual pages: see REF.
"""
import re

from .. import units
from ..base import finite, short
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb, save_to_opt, target_opt
from .method_common import MethodAdapter

REF = ('Agilent 8697 operation manual (method, tray, sequence, Park, p.35-57); Atomx XYZ user manual (TekLink method, schedule, '
       'printed 3-x); Frontier PY-3030D operation manual (single/double shot, EGA, ch.4-5); Markes UNITY-xr user manual (method '
       'editor, tube desorption p.25-42); Analyte Excite+ specification sheet (laser, stage, gas p.1-2)')


class SampleIntroAdapter(MethodAdapter):
    CLASS = 'sample_intro'
    TITLE = 'sample introduction'
    ABOUT = ('Front ends that prepare a sample and inject it into an analyzer (GC, GC-MS, ICP-MS): set the front-end method in '
             'plain units, load vials (where the instrument loads them itself), set heated zones, start a run or a sequence and '
             'follow it as a job, do the manual step a pyrolyzer waits for, read the run record.')
    STANDARDS = ('USP <467>, EPA 5030C / 524.2, EPA TO-17 / ISO 16000-6 terms; OPC UA LADS (FunctionalUnit Start/Stop); '
                 'SiLA 2 core (abort)')
    UNIT_KINDS = ('temperature', 'duration', 'volume', 'flow', 'length', 'heating_rate')
    PATH_GROUPS = (('start', 'stop', 'result'),)
    KEYS = {
        'equilibration': ('duration', 'vial equilibration time in the oven (headspace)'),
        'gc-cycle': ('duration', 'GC cycle time between injections (headspace)'),
        'shake': (None, 'shaking level 0..9 (headspace)'),
        'vial-size': ('volume', 'vial size 10mL | 20mL | 22mL (headspace)'),
        'fill-pressure': ('pressure', 'vial fill pressure (headspace)'),
        'matrix': (None, 'sample matrix water | soil_low | soil_high (purge and trap)'),
        'sample-volume': ('volume', 'sample volume taken from the vial (purge and trap)'),
        'dilution': (None, 'dilution factor 1 | 2 | 5 | 10 | 25 | 50 | 100 (purge and trap)'),
        'purge-temp': ('temperature', 'purge temperature (purge and trap)'),
        'purge-time': ('duration', 'purge time (purge and trap) / pre-purge time (thermal desorber)'),
        'purge-flow': ('flow', 'purge gas flow'),
        'dry-purge-time': ('duration', 'dry purge time (purge and trap)'),
        'desorb-temp': ('temperature', 'desorption temperature: the trap (purge and trap) or the tube (thermal desorber)'),
        'desorb-time': ('duration', 'desorption time: the trap (purge and trap) or the tube hold (thermal desorber)'),
        'bake-temp': ('temperature', 'trap bake temperature (purge and trap)'),
        'bake-time': ('duration', 'trap bake time (purge and trap)'),
        'mode': (None, 'run mode: single | double | ega (pyrolyzer); analyze | tube_condition | trap_condition (thermal desorber); '
                       'spot | scan (laser)'),
        'pyrolysis-temp': ('temperature', 'pyrolysis furnace temperature'),
        'pyrolysis-time': ('duration', 'pyrolysis time'),
        'td-start': ('temperature', 'thermal desorption / EGA start temperature (pyrolyzer)'),
        'td-end': ('temperature', 'thermal desorption / EGA end temperature (pyrolyzer)'),
        'ramp': ('heating_rate', 'furnace ramp rate (pyrolyzer)'),
        'hold': ('duration', 'hold time at the end temperature (pyrolyzer)'),
        'interface-temp': ('temperature', 'GC interface temperature (pyrolyzer)'),
        'carrier-flow': ('flow', 'carrier gas flow (pyrolyzer)'),
        'trap-low': ('temperature', 'focusing trap low (trapping) temperature (thermal desorber)'),
        'trap-high': ('temperature', 'focusing trap desorption temperature (thermal desorber)'),
        'trap-time': ('duration', 'focusing trap hold time (thermal desorber)'),
        'trap-ramp': ('heating_rate', 'focusing trap heating rate, e.g. 40K/s (thermal desorber)'),
        'valve-temp': ('temperature', 'valve temperature (thermal desorber)'),
        'line-temp': ('temperature', 'transfer line temperature (thermal desorber)'),
        'shape': (None, 'laser spot shape circle | square'),
        'spot': ('length', 'laser spot size, e.g. 80um'),
        'fluence': (None, 'laser fluence in J/m² (60000 = 6 J/cm²)'),
        'frequency': ('frequency', 'laser repetition rate, e.g. 10Hz'),
        'shots': (None, 'laser shots per spot'),
        'washout': ('duration', 'cell washout time (laser)'),
        'scan-speed': ('speed', 'stage speed in scan mode (laser), e.g. 0.4mm/s'),
    }
    CODES = {
        'BUSY': ('BUSY', '{inst} is busy with a run.', 'wait for it (`chemlab run {inst} wait`) or stop it (`chemlab run {inst} stop`).'),
        'NOT_RUNNING': ('NOT_RUNNING', '{inst} has no run in progress.', 'start one first (`chemlab run {inst} start`).'),
        'PARKED': ('PARKED', 'the tray of {inst} is parked, so no sequence can start.', 'unpark it (`chemlab call {inst} tray.park --parked false`).'),
        'HOT_INTERLOCK': ('HOT', '{inst} is still hot; that part stays locked until it has cooled.', 'wait for it to cool (`chemlab run {inst} status`).'),
        'MANUAL_INTERLOCK': ('NOT_NOW', '{inst} does not take that manual step at this stage of the run.', 'see the stage in `chemlab run {inst} status`.'),
        'ACCESS_INTERLOCK': ('ACCESS_INTERLOCK', 'that part of {inst} is interlocked now (a cover, lever or the heat).', 'see `chemlab run {inst} status`.'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        vial_help = 'tray position as printed on the instrument (1, 2, ...)'
        return {
            'method': Verb('method', 'set the front-end method: KEY=VALUE ... (class keys take units; see help)', self.h_method,
                           variadic=('KEY=VALUE', 'a setting, e.g. purge-temp=40C or desorb-time=4min'),
                           preconditions=['every value inside the contract range (in friendly units)', 'no run in progress'],
                           errors=['UNKNOWN_SETTING, OUT_OF_RANGE, BAD_SETTING (usage)', 'BUSY (refused)'],
                           details=('class keys take units (purge-temp=40C, desorb-time=4min, purge-flow=40mL/min); the native argument names of '
                                    'this instrument work too (a bare number is in the unit its contract declares).  Settings you leave '
                                    'out keep their value (the last accepted method, else the documented default); the reply says which.'),
                           example='chemlab run INST method purge-temp=40C purge-time=11min', reference=REF),
            'load': Verb('load', 'load a vial the lab has prepared into a tray position (opens and closes the lid itself)', self.h_load,
                         positionals=[Arg('SAMPLE', 'the vial id the lab gave you')],
                         options={'vial': Opt(vial_help, metavar='N', required=True)},
                         preconditions=['the tray position is empty', 'no sequence running'],
                         errors=['UNKNOWN_POSITION (usage)', 'POSITION_TAKEN, NOT_REGISTERED (refused)'],
                         example='chemlab run INST load v1 --vial 1', reference=REF),
            'unload': Verb('unload', 'take a vial out of a tray position (opens and closes the lid itself)', self.h_unload,
                           options={'vial': Opt(vial_help, metavar='N', required=True)},
                           preconditions=['a vial is at the position'], errors=['EMPTY_POSITION (refused)'],
                           example='chemlab run INST unload --vial 1', reference=REF),
            'set-temp': Verb('set-temp', 'set the temperature of a heated zone', self.h_set_temp,
                             positionals=[Arg('TEMP', 'set point, e.g. 80C', kind='temperature')], options={'zone': target_opt('zone')},
                             preconditions=['set point within the zone range', 'the lab has told the instrument the room temperature'],
                             errors=['OUT_OF_RANGE (usage)', 'NO_AMBIENT, POWER_OFF (refused)'],
                             example='chemlab run INST set-temp 80C --zone oven', reference=REF),
            'start': Verb('start', 'start the run (or the vial sequence); long runs return job-N', self.h_start, long_action=True,
                          options={'vial': Opt('one tray position to run (purge and trap)', metavar='N'),
                                   'vials': Opt('tray positions of a sequence, e.g. 1,2,3 or 1-4 (headspace)', metavar='LIST'),
                                   'injections': Opt('injections per vial (headspace)', metavar='K')},
                          preconditions=['power on', 'a method is set', 'covers and doors closed', 'a sample is loaded',
                                         'no run in progress'],
                          errors=['POWER_OFF, NO_METHOD, COVER_OPEN, NO_SAMPLE, BUSY (refused)'],
                          example='chemlab run INST start', reference=REF),
            'inject': Verb('inject', 'do the manual step the run is waiting for (drop the sample cup, insert or retract the sampler) '
                                     'and confirm it; --wait first lets lab time pass until the furnace is ready', self.h_inject,
                           long_action=True, options={'wait': Opt('wait (moving lab time) until the run waits for the manual step',
                                                                  kind='flag'),
                                                      'max': Opt('with --wait: the longest wait (default 30 min)', kind='duration', metavar='TIME')},
                           preconditions=['a run is in progress and waits for a manual step', 'the GC is ready'],
                           errors=['NOT_RUNNING, NOT_YET, GC_NOT_READY (refused)'],
                           example='chemlab run INST inject --wait', reference=REF),
            'stop': Verb('stop', 'stop the run (the sample and what was transferred stay as they are)', self.h_stop,
                         preconditions=['a run is in progress'], errors=['NOT_RUNNING (refused)'], example='chemlab run INST stop'),
            'result': Verb('result', 'the record of the last run (process log, temperatures, injections, GC triggers)',
                           self.h_record('result', 'record'), readonly=True, options={'save-to': save_to_opt()},
                           example='chemlab run INST result --save-to run.json'),
            'rinse': Verb('rinse', 'rinse the sample path after a run or a stop (purge and trap: water through the sparger; laser: '
                                   'a gas washout of the cell, a job)', self.h_rinse,
                          positionals=[Arg('VOLUME', 'rinse volume (purge and trap), e.g. 7mL; default: the documented volume',
                                           required=False, kind='volume')],
                          preconditions=['power on', 'no run in progress'], errors=['OUT_OF_RANGE (usage)', 'BUSY, POWER_OFF (refused)'],
                          example='chemlab run INST rinse 7mL', reference=REF),
            'position': Verb('position', 'move the sample stage to X Y Z (bare numbers in mm) and wait until it is there', self.h_position,
                             positionals=[Arg('X', 'x, e.g. 20mm', kind='length'), Arg('Y', 'y', kind='length'), Arg('Z', 'z (focus height)', kind='length')],
                             long_action=True, preconditions=['power on', 'sample drawer closed', 'inside the stage travel'],
                             errors=['OUT_OF_RANGE (usage)', 'POWER_OFF, DRAWER_OPEN (refused)'],
                             example='chemlab run INST position 20mm 20mm 10mm', reference=REF),
        }

    # ------------------------------------------------------------------ helpers
    def vial_no(self, ctx, text, m):
        try:
            n = int(str(text).strip())
        except ValueError:
            ctx.fail(invalid('UNKNOWN_POSITION', '%r is not a tray position.' % text, 'give a number, e.g. --vial 1.'))
        hi = m.get('positions')
        if n < 1 or (hi and n > hi):
            ctx.fail(invalid('UNKNOWN_POSITION', '%s has no tray position %d.' % (self.instrument_id, n),
                             'tray positions of %s: 1..%s.' % (self.instrument_id, hi or '?')))
        return n

    def vial_list(self, ctx, text, m):
        out = []
        for part in str(text).split(','):
            part = part.strip()
            mm = re.fullmatch(r'(\d+)\s*-\s*(\d+)', part)
            if mm:
                a, b = int(mm.group(1)), int(mm.group(2))
                if b < a:
                    ctx.fail(invalid('UNKNOWN_POSITION', 'the range %s runs backwards.' % part, 'write it low to high, e.g. 1-4.'))
                out += [self.vial_no(ctx, str(i), m) for i in range(a, b + 1)]
            elif part:
                out.append(self.vial_no(ctx, part, m))
        if not out:
            ctx.fail(invalid('UNKNOWN_POSITION', 'no tray positions given.', 'write e.g. --vials 1,2,3.'))
        return out

    def lid_open(self, ctx, lid):
        return finite(ctx.v(lid['slot'])) and ctx.v(lid['slot']) >= lid['min_open'] - 1e-9

    def with_lid(self, ctx, m, action, what):
        """Open the lid if needed, do the action, close the lid again if this verb opened it."""
        lid = m['lid']
        opened = False
        if not self.lid_open(ctx, lid):
            ctx.call(lid['cap'], {lid['arg']: lid['open']}, 'open the lid')
            opened = True
        action()
        if opened or lid.get('always_close'):
            ctx.call(lid['cap'], {lid['arg']: lid['close']}, 'close the lid')
        return opened

    def occupancy(self, ctx, m, n):
        occ = ctx.v(m['occupancy']) if m.get('occupancy') else None
        return occ[n - 1] if isinstance(occ, list) and len(occ) >= n else None

    # ------------------------------------------------------------------ handlers
    def h_load(self, ctx, p):
        m = self.vmap('load')
        n = self.vial_no(ctx, p.get('vial'), m)
        sample = p.pos[0]
        self.need(ctx, 'load')
        got = self.occupancy(ctx, m, n)
        ctx.check(got is None, 'POSITION_TAKEN', 'tray position %d of %s already holds %s.' % (n, self.instrument_id, got),
                  'choose an empty position (`chemlab run %s status`) or unload it first (`chemlab run %s unload --vial %d`).' % (
                      self.instrument_id, self.instrument_id, n), label='position %d empty' % n)
        slot_id = m['slot_format'].format(n)
        args = {m['id_arg']: sample, m['slot_arg']: slot_id}
        self.with_lid(ctx, m, lambda: ctx.call(m['cap'], args, 'put %s into tray position %d' % (sample, n)), 'load')
        return {'text': 'ok: %s is in tray position %d of %s (lid closed)\n' % (sample, n, self.instrument_id),
                'json': {'sample': sample, 'vial': n, 'slot_id': slot_id}}

    def h_unload(self, ctx, p):
        m = self.vmap('unload')
        n = self.vial_no(ctx, p.get('vial'), m)
        self.need(ctx, 'unload')
        got = self.occupancy(ctx, m, n)
        ctx.check(got is not None, 'EMPTY_POSITION', 'tray position %d of %s is empty.' % (n, self.instrument_id),
                  'see the tray in `chemlab run %s status`.' % self.instrument_id, label='a vial at position %d' % n)
        slot_id = m['slot_format'].format(n)
        self.with_lid(ctx, m, lambda: ctx.call(m['cap'], {m['slot_arg']: slot_id}, 'take the vial out of tray position %d' % n), 'unload')
        return {'text': 'ok: tray position %d of %s emptied (%s taken out; lid closed)\n' % (n, self.instrument_id, got),
                'json': {'vial': n, 'sample': got}}

    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        targ = self.target_args(ctx, p)
        lo, hi = self.arg_range_si(m['cap'], m['arg'])
        if (lo is not None and k < lo - 1e-9) or (hi is not None and k > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'set point %s is outside the range of %s (%s to %s).' % (
                units.fmt(k, 'temperature'), self.instrument_id, units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature')),
                'give a set point from %s to %s.' % (units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature'))))
        self.need(ctx, 'set-temp')
        amb = self.sv(ctx, self.slot('ambient')) if self.slot('ambient') else None
        margin = float(self.param('above_ambient_k', 0.0))
        if finite(amb) and margin:
            ctx.check(k >= amb + margin - 1e-9, 'TOO_LOW', 'a set point of %s is less than %g K above the room temperature (%s) of %s.' % (
                units.fmt(k, 'temperature'), margin, units.fmt(amb, 'temperature'), self.instrument_id),
                'give at least %s.' % units.fmt(amb + margin, 'temperature'), label='above room temperature + %g K' % margin)
        args = dict(m.get('args') or {})
        args.update(targ)
        args[m['arg']] = round(self.arg_value(m['cap'], m['arg'], k), 10)
        ctx.call(m['cap'], args, 'set %s to %s' % (p.get('zone'), units.fmt(k, 'temperature')))
        return {'text': 'ok: %s %s set point %s\n' % (self.instrument_id, p.get('zone'), units.fmt(k, 'temperature')),
                'json': {'zone': p.get('zone'), 'set_point_k': k, 'set_point_c': k - 273.15}}

    def h_start(self, ctx, p):
        m = self.vmap('start')
        args = dict(m.get('args') or {})
        if m.get('vials_arg'):
            if p.get('vial') and not p.get('vials'):
                vials = [self.vial_no(ctx, p.get('vial'), m)]
            elif p.get('vials'):
                vials = self.vial_list(ctx, p.get('vials'), m)
            else:
                ctx.fail(invalid('MISSING_OPTION', 'start on %s needs the tray positions of the sequence.' % self.instrument_id,
                                 'give --vials 1,2,3 (or --vial N for one vial).'))
            args[m['vials_arg']] = vials
            if p.get('injections'):
                if not m.get('injections_arg'):
                    ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s takes no --injections.' % self.instrument_id, 'leave it out.'))
                try:
                    args[m['injections_arg']] = int(p.get('injections'))
                except ValueError:
                    ctx.fail(invalid('BAD_NUMBER', '--injections %s is not a whole number.' % p.get('injections'), 'write e.g. --injections 2.'))
            for n in vials:
                if m.get('occupancy') and self.occupancy(ctx, m, n) is None:
                    ctx.fail(refused('EMPTY_POSITION', 'tray position %d of %s is empty.' % (n, self.instrument_id),
                                     'load a vial there first (`chemlab run %s load SAMPLE --vial %d`), or leave the position out.' % (
                                         self.instrument_id, n)))
        elif m.get('vial_arg'):
            if p.get('vials') or p.get('injections'):
                ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s runs one vial per start; it takes --vial N only.' % self.instrument_id,
                                 'give --vial N.'))
            if not p.get('vial'):
                ctx.fail(invalid('MISSING_OPTION', 'start on %s needs the tray position of the vial.' % self.instrument_id,
                                 'give --vial N (1..%s).' % m.get('positions')))
            n = self.vial_no(ctx, p.get('vial'), m)
            args[m['vial_arg']] = n - 1 + int(m.get('vial_base', 1))
        elif p.get('vial') or p.get('vials') or p.get('injections'):
            ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s has no tray: its sample is the one the lab has loaded.' % self.instrument_id,
                             'leave out --vial / --vials / --injections.'))
        self.running_check(ctx)
        self.need(ctx, 'start')
        self.validate_call(ctx, m['cap'], args)
        r = ctx.call(m['cap'], args, 'start (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        desc = '%s run on %s' % (self.TITLE, self.instrument_id)
        job = self.make_job(ctx, m, r, desc, stop=self.stop_fn())
        notes = list((m.get('after') or []))
        out = self.job_reply(job, 'ok: %s started (%s)' % (self.instrument_id, short(args, 80) if args else m['cap']))
        out['json'].update({'arguments': args})
        out['notes'] = [n.replace('INST', self.instrument_id) for n in notes]
        return out

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        self.need(ctx, 'stop')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop (%s)' % m['cap'])
        return {'text': 'ok: %s stopped\n' % self.instrument_id, 'json': {}}

    def h_inject(self, ctx, p):
        m = self.vmap('inject')
        stage_s, phase_s = m['stage_slot'], m['phase_slot']
        manual = set(m['steps'])
        if ctx.v(phase_s) != 'running':
            ctx.fail(refused('NOT_RUNNING', '%s has no run in progress (%s = %s).' % (self.instrument_id, phase_s, ctx.v(phase_s)),
                             'start one first (`chemlab run %s start`).' % self.instrument_id))
        if ctx.v(stage_s) not in manual:
            if p.get('wait') and not ctx.dry_run:
                mx = p.get('max') or 1800.0
                ctx.advance(mx, until=lambda v: ctx.v(stage_s) in manual or ctx.v(phase_s) != 'running', step='wait for the manual step',
                            poll_s=1.0)
            elif p.get('wait'):
                ctx.plan.append({'capability': None, 'arguments': {}, 'step': 'wait until the run waits for the manual step'})
                return {'text': '', 'json': {}}
            if ctx.v(stage_s) not in manual:
                ctx.fail(refused('NOT_YET', '%s is not waiting for a manual step now (stage %s, furnace %s).' % (
                    self.instrument_id, ctx.v(stage_s), units.fmt(self.sv(ctx, m.get('temp_slot')), 'temperature')),
                    'wait until it is (`chemlab run %s inject --wait`).' % self.instrument_id))
        stage = ctx.v(stage_s)
        step = m['steps'][stage]
        if step.get('gc'):
            ctx.check(bool(ctx.v(m['gc_slot'])), 'GC_NOT_READY', 'the GC is not ready to start yet (%s is false), so %s cannot '
                      'inject.' % (m['gc_slot'], self.instrument_id), 'wait until the GC is ready (`chemlab run %s status`); the lab '
                      'runs the GC.' % self.instrument_id, label='GC ready')
        done = []
        for c in step['calls']:
            args = dict(c.get('args') or {})
            if c.get('arg'):
                lo, hi = self.arg_range(c['cap'], c['arg'])
                args[c['arg']] = hi if c.get('value') == 'max' else (lo if c.get('value') == 'min' else c.get('value'))
            ctx.call(c['cap'], args, c['what'])
            done.append(c['what'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': 'ok: %s: %s; the run goes on (stage %s)\n' % (self.instrument_id, ', then '.join(done), ctx.v(stage_s)),
                'json': {'was_stage': stage, 'stage': ctx.v(stage_s), 'steps': done}}

    def h_rinse(self, ctx, p):
        m = self.vmap('rinse')
        args = dict(m.get('args') or {})
        if m.get('arg'):
            v = p.pos[0] if p.pos else None
            if v is None:
                v = (ctx.cap_spec(m['cap']).get('example_arguments') or {}).get(m['arg'])
                args[m['arg']] = v
            else:
                args[m['arg']] = round(self.arg_value(m['cap'], m['arg'], v), 15)
        elif p.pos:
            ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s takes no rinse volume (the washout is a gas flow for the method\'s washout time).'
                             % self.instrument_id, 'write just `chemlab run %s rinse`.' % self.instrument_id))
        self.need(ctx, 'rinse')
        self.validate_call(ctx, m['cap'], args, {m['arg']: 'VOLUME'} if m.get('arg') else None)
        r = ctx.call(m['cap'], args, 'rinse (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.make_job(ctx, m, r, 'rinse of %s' % self.instrument_id) if (m.get('job') or r.get('operation_id')) else None
        head = 'ok: %s rinsed%s' % (self.instrument_id, (' with %s' % units.fmt_declared(args[m['arg']], self.arg_unit(m['cap'], m['arg'])))
                                    if m.get('arg') else '')
        out = self.job_reply(job, head)
        out['json'].update({'arguments': args})
        return out

    def h_position(self, ctx, p):
        m = self.vmap('position')
        xyz = [p.pos[0], p.pos[1], p.pos[2]]
        self.need(ctx, 'position')
        args = {m['arg']: [round(self.arg_value(m['cap'], m['arg'], v), 12) for v in xyz]}
        self.validate_call(ctx, m['cap'], args)
        ctx.call(m['cap'], args, 'move the stage to %s' % ', '.join(units.fmt(v, 'length') for v in xyz))
        tol = float(m.get('tolerance_m', 1e-7))
        axes = m['axes']
        waited = self.settle(ctx, lambda c: all(finite(c.v(a)) and abs(c.v(a) - t) <= tol for a, t in zip(axes, args[m['arg']])),
                             'wait for the stage', float(m.get('max_s', 60.0)), poll_s=0.5)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': 'ok: %s stage at %s (%s of moving)\n' % (self.instrument_id, ', '.join(units.fmt(ctx.v(a), 'length') for a in axes),
                                                               units.fmt_s(waited)),
                'json': {'position_m': [ctx.v(a) for a in axes], 'waited_s': waited}}

    # ------------------------------------------------------------------ refusals
    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code in ('NOT_READY', 'PRECONDITION_FAILED'):
            ctx.refresh()
            for verb, m in (self.map.get('verbs') or {}).items():
                caps = [m.get('cap')] if isinstance(m, dict) else []
                if cap in caps:
                    for r in (self.map.get('requires') or {}).get(verb, []):
                        try:
                            self.need(ctx, verb, only=[r.get('code')])
                        except Exception as exc:                          # noqa: BLE001 - the first unmet requirement names the reason
                            if hasattr(exc, 'code'):
                                exc.backend = None
                                return exc
        return MethodAdapter.explain_refusal(self, ctx, cap, args, response)

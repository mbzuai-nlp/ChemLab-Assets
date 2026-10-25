"""Titration class ("titration"): potentiometric titrators, Karl Fischer coulometers and Kjeldahl distillation-titration units -
Metrohm OMNIS Professional Titrator, Metrohm 899 Coulometer, VELP UDK 159.  Members and mappings: configs/adapters/titration.json.

Verbs: titrant CONC [--cylinder V], method KEY=VALUE ..., sample SIZE, stir SPEED|on|off [--level N], connect PORT=ID ...,
start (job), stop, curve [--save-to F], result [--sample V] [--ratio R] [--save-to F], status.  power comes from the shared layer.

Method dictionary (class keys take units; native argument names also work, a bare number is in the unit the contract declares):

    mode=endpoint|dynamic ep=0.1V increment=0.1mL max=15mL hold=0.5s tolerance=1mV rate=0.5mL/min        (potentiometric)
    current=400mA drift-comp=0ug/min start-drift=20ug/min extraction=0s stir=8 hold=1s                    (KF coulometer)
    steam=100% window=4min water=50mL base=70mL boric=30mL base-conc=8mol/L boric-capacity=0.64mol/L
    titrant=0.2mol/L blank=0.1mL factor=6.25                                                             (Kjeldahl)

Settings left out keep their current value (state slots), else the last accepted method on the instrument, else the documented
default of the contract (the reply says which).  Concentrations take M / mol/L / mmol/L; volumes mL / µL; drift µg/min.

Vendor-neutral references: USP <541> titrimetry, USP <921> / Ph. Eur. 2.5.32 and ASTM E1064 (coulometric KF), ISO 5983-2 /
AOAC 2001.11 (Kjeldahl); Metrohm OMNIS / 899 and VELP UDK manuals for the native vocabulary (page references in REF).
"""
from .. import units
from ..base import finite, short
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb, save_to_opt
from .method_common import MethodAdapter

REF = ('Metrohm OMNIS titrator product manual (titration system, dosing unit, stirrer p.17-38); Metrohm 899 Coulometer manual '
       '(KF principle, drift, method parameters, determination p.38-71); VELP UDK 159 leaflet and series brochure (operation, '
       'method library p.4-5)')
ON_OFF = ('on', 'off', 'stop', '0')


class TitrationAdapter(MethodAdapter):
    CLASS = 'titration'
    TITLE = 'titration'
    ABOUT = ('Titrators: declare the titrant, set the titration method in plain units, give the sample size, stir, connect the '
             'electrode and the beaker, start the titration and follow it as a job, read the curve and the result (content, '
             'water, nitrogen).')
    STANDARDS = ('USP <541>, USP <921> / Ph. Eur. 2.5.32, ASTM E1064 (KF), ISO 5983-2 / AOAC 2001.11 (Kjeldahl) terms; '
                 'Metrohm OMNIS REST vocabulary as vendor style; SiLA 2 has no titration feature')
    UNIT_KINDS = ('concentration', 'volume', 'voltage', 'duration', 'flow', 'mass', 'mass_flow', 'current', 'rotation')
    PATH_GROUPS = (('start', 'stop', 'curve', 'result'),)
    KEYS = {
        'mode': (None, 'endpoint | dynamic (potentiometric: set end point or dynamic equivalence point)'),
        'ep': ('voltage', 'end-point potential (endpoint mode), e.g. 0.1V'),
        'increment': ('volume', 'dose increment, e.g. 0.1mL'),
        'max': ('volume', 'maximum titrant volume, e.g. 15mL'),
        'hold': ('duration', 'stability hold (potentiometric) / end-point hold (KF), e.g. 0.5s'),
        'tolerance': ('voltage', 'stability tolerance of the potential, e.g. 1mV'),
        'rate': ('flow', 'dosing rate, e.g. 0.5mL/min'),
        'current': ('current', 'generator current 100mA | 200mA | 400mA (KF coulometer)'),
        'drift-comp': ('mass_flow', 'drift compensation, e.g. 0ug/min (KF)'),
        'start-drift': ('mass_flow', 'start drift threshold, e.g. 20ug/min (KF)'),
        'extraction': ('duration', 'extraction time before titrating (KF)'),
        'stir': (None, 'stirring level -15..15 during the determination (KF)'),
        'steam': ('fraction', 'steam power 10%..100% (Kjeldahl)'),
        'window': ('duration', 'distillation time window (Kjeldahl)'),
        'water': ('volume', 'dilution water volume (Kjeldahl)'),
        'base': ('volume', 'NaOH volume (Kjeldahl)'),
        'boric': ('volume', 'boric acid volume (Kjeldahl)'),
        'base-conc': ('concentration', 'NaOH concentration (Kjeldahl), e.g. 8mol/L'),
        'boric-capacity': ('concentration', 'boric acid trapping capacity (Kjeldahl)'),
        'titrant': ('concentration', 'titrant concentration (Kjeldahl), e.g. 0.2mol/L'),
        'blank': ('volume', 'blank titrant volume (Kjeldahl), e.g. 0.1mL'),
        'factor': (None, 'protein conversion factor (Kjeldahl), e.g. 6.25'),
    }
    CODES = {
        'BUSY': ('BUSY', '{inst} is busy.', 'wait for it (`chemlab run {inst} wait`) or stop it (`chemlab run {inst} stop`).'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        return {
            'titrant': Verb('titrant', 'declare the titrant concentration (and the dosing cylinder)', self.h_titrant,
                            positionals=[Arg('CONC', 'titrant concentration, e.g. 0.1M or 100mmol/L', kind='concentration')],
                            options={'cylinder': Opt('dosing cylinder volume, e.g. 20mL', kind='volume', metavar='VOLUME')},
                            preconditions=['no titration running and no titrant dosed yet', 'value within the contract range'],
                            errors=['OUT_OF_RANGE (usage)', 'BUSY, ALREADY_DOSED (refused)'],
                            example='chemlab run INST titrant 0.1M --cylinder 20mL', reference=REF),
            'method': Verb('method', 'set the titration method: KEY=VALUE ... (class keys take units; see help)', self.h_method,
                           variadic=('KEY=VALUE', 'a setting, e.g. mode=dynamic or max=15mL'),
                           preconditions=['every value inside the contract range', 'no titration running'],
                           errors=['UNKNOWN_SETTING, OUT_OF_RANGE, BAD_SETTING (usage)', 'BUSY (refused)'],
                           details=('class keys take units (max=15mL, hold=0.5s, current=400mA); the native argument names of this '
                                    'instrument work too.  Settings you leave out keep their value (the current setting, the last '
                                    'accepted method, else the documented default); the reply says which.'),
                           example='chemlab run INST method mode=dynamic increment=0.1mL max=15mL', reference=REF),
            'sample': Verb('sample', 'give the sample size for the result calculation', self.h_sample,
                           positionals=[Arg('SIZE', 'sample mass, e.g. 0.5g or 10mg', kind='mass')],
                           preconditions=['no determination running'], errors=['OUT_OF_RANGE (usage)', 'BUSY (refused)'],
                           example='chemlab run INST sample 10mg', reference=REF),
            'stir': Verb('stir', 'stir at a speed, switch the stirrer on (at a level) or off', self.h_stir,
                         positionals=[Arg('SPEED|on|off', 'a speed such as 300rpm, or on / off')],
                         options={'level': Opt('stirring level for stirrers set in levels (-15..15)', metavar='N')},
                         preconditions=['speed within the range'], errors=['OUT_OF_RANGE, NEEDS_LEVEL, NEEDS_SPEED (usage)', 'BUSY (refused)'],
                         example='chemlab run INST stir 300rpm', reference=REF),
            'connect': Verb('connect', 'register the electrode and the titration cell in their slots: PORT=ID ... (ID none = take out)',
                            self.h_connect, variadic=('PORT=ID', 'e.g. electrode=electrode_01 cell=beaker_01'),
                            preconditions=['no titration running'], errors=['UNKNOWN_PORT (usage)', 'BUSY (refused)'],
                            example='chemlab run INST connect electrode=electrode_01 cell=beaker_01', reference=REF),
            'start': Verb('start', 'start the titration / determination (returns job-N)', self.h_start, long_action=True,
                          preconditions=['power on', 'titrant, method and stirring set', 'electrode and cell (or tube) in place',
                                         'a sample in place', 'nothing running'],
                          errors=['POWER_OFF, NOT_CONNECTED, NO_TITRANT, NO_METHOD, NOT_STIRRING, NO_SAMPLE, BUSY (refused)'],
                          example='chemlab run INST start', reference=REF),
            'stop': Verb('stop', 'stop the titration (what was dosed stays dosed)', self.h_stop, preconditions=['a titration running'],
                         errors=['NOT_RUNNING (refused)'], example='chemlab run INST stop'),
            'curve': Verb('curve', 'the titration curve (volume or charge against potential, pH or colour)', self.h_record('curve', 'curve'),
                          readonly=True, options={'save-to': save_to_opt()}, example='chemlab run INST curve --save-to curve.json'),
            'result': Verb('result', 'the result: end point and content (with --sample), water, or nitrogen', self.h_result, readonly=True,
                           options={'sample': Opt('sample volume for the content calculation (potentiometric), e.g. 25mL',
                                                  kind='volume', metavar='VOLUME'),
                                    'ratio': Opt('moles of analyte per mole of titrant (default 1)', metavar='R'),
                                    'save-to': save_to_opt()},
                           preconditions=['the titration has reached its end point (for --sample)'],
                           errors=['NO_ENDPOINT (refused)'], example='chemlab run INST result --sample 25mL'),
        }

    # ------------------------------------------------------------------ handlers
    def h_titrant(self, ctx, p):
        m = self.vmap('titrant')
        conc = p.pos[0]
        self.need(ctx, 'titrant')
        if m.get('calls'):                                          # the titrant is a field of the method (Kjeldahl)
            mm = {'calls': m['calls']}
            calls = self.method_calls(ctx, [(m['key'], p.raw['CONC'])], mm)
            if p.get('cylinder') is not None:
                ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s has no dosing cylinder to choose.' % self.instrument_id, 'leave out --cylinder.'))
            for cap, args, info in calls:
                ctx.call(cap, args, 'set the titrant to %s (%s)' % (units.fmt(conc, 'concentration'), cap))
            return {'text': 'ok: %s titrant %s (a method setting; other settings kept)\n' % (self.instrument_id, units.fmt(conc, 'concentration')),
                    'json': {'concentration_mol_m3': conc, 'calls': [{'capability': c, 'arguments': a} for c, a, _ in calls]}}
        args = {m['arg']: round(self.arg_value(m['cap'], m['arg'], conc), 12)}
        self.validate_call(ctx, m['cap'], args, {m['arg']: 'CONC'})
        cyl = None
        if p.get('cylinder') is not None:
            if not m.get('cylinder_cap'):
                ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s has no dosing cylinder to choose.' % self.instrument_id, 'leave out --cylinder.'))
            cyl = {m['cylinder_arg']: round(self.arg_value(m['cylinder_cap'], m['cylinder_arg'], p.get('cylinder')), 15)}
            self.validate_call(ctx, m['cylinder_cap'], cyl, {m['cylinder_arg']: '--cylinder'})
            ctx.call(m['cylinder_cap'], cyl, 'use the %s cylinder' % units.fmt(p.get('cylinder'), 'volume'))
        ctx.call(m['cap'], args, 'set the titrant to %s' % units.fmt(conc, 'concentration'))
        return {'text': 'ok: %s titrant %s%s\n' % (self.instrument_id, units.fmt(conc, 'concentration'),
                                                   (', cylinder %s' % units.fmt(p.get('cylinder'), 'volume')) if cyl else ''),
                'json': {'concentration_mol_m3': conc, 'cylinder_m3': p.get('cylinder')}}

    def h_sample(self, ctx, p):
        m = self.vmap('sample')
        kg = p.pos[0]
        self.need(ctx, 'sample')
        args = dict(m.get('args') or {})
        args[m['arg']] = round(self.arg_value(m['cap'], m['arg'], kg), 15)
        self.validate_call(ctx, m['cap'], args, {m['arg']: 'SIZE'})
        ctx.call(m['cap'], args, 'sample size %s' % units.fmt(kg, 'mass'))
        return {'text': 'ok: %s sample size %s\n' % (self.instrument_id, units.fmt(kg, 'mass')), 'json': {'sample_mass_kg': kg}}

    def h_stir(self, ctx, p):
        m = self.vmap('stir')
        text = p.raw['SPEED|on|off'].strip().lower()
        level = p.get('level')
        self.need(ctx, 'stir')
        if m.get('level_arg'):                                     # a stirrer set in levels with an on/off switch
            if text not in ('on', 'off', 'stop', '0'):
                ctx.fail(invalid('NEEDS_LEVEL', '%s sets its stirrer in levels, not as a speed.' % self.instrument_id,
                                 'write `chemlab run %s stir on --level 8` (levels -15..15) or `stir off`.' % self.instrument_id))
            args = {m['on_arg']: text == 'on'}
            if level is not None:
                if text != 'on':
                    ctx.fail(invalid('BAD_OPTION', '--level goes with `stir on`.', 'write `stir on --level N`.'))
                try:
                    args[m['level_arg']] = int(level)
                except ValueError:
                    ctx.fail(invalid('BAD_NUMBER', '--level %s is not a whole number.' % level, 'give a level from -15 to 15.'))
            self.validate_call(ctx, m['cap'], args, {m['level_arg']: '--level'})
            ctx.call(m['cap'], args, 'stirrer %s%s' % ('on' if args[m['on_arg']] else 'off', (' at level %d' % args[m['level_arg']]) if
                                                       m['level_arg'] in args else ''))
            return {'text': 'ok: %s stirrer %s%s\n' % (self.instrument_id, 'on' if args[m['on_arg']] else 'off',
                                                      (' at level %d' % args[m['level_arg']]) if m['level_arg'] in args else ''),
                    'json': {'on': args[m['on_arg']], 'level': args.get(m['level_arg'])}}
        if level is not None:
            ctx.fail(invalid('NEEDS_SPEED', '%s sets its stirrer as a speed, not in levels.' % self.instrument_id,
                             'write e.g. `chemlab run %s stir 300rpm`.' % self.instrument_id))
        if text == 'on':
            ctx.fail(invalid('NEEDS_SPEED', '%s needs a stirring speed.' % self.instrument_id,
                             'write e.g. `chemlab run %s stir 300rpm`.' % self.instrument_id))
        w = 0.0 if text in ('off', 'stop', '0') else units.parse(text, 'rotation', what='speed')
        if w == 0.0:
            self.need(ctx, 'stir-off')
        args = {m['arg']: self.arg_value(m['cap'], m['arg'], w)}
        self.validate_call(ctx, m['cap'], args, {m['arg']: 'speed'})
        ctx.call(m['cap'], args, 'stir at %s' % units.fmt(w, 'rotation') if w else 'stop stirring')
        return {'text': 'ok: %s %s\n' % (self.instrument_id, ('stirring at %s' % units.fmt(w, 'rotation')) if w else 'stirrer off'),
                'json': {'speed_rad_s': w, 'speed_rpm': w * 60 / (2 * 3.141592653589793)}}

    def h_start(self, ctx, p):
        m = self.vmap('start')
        self.running_check(ctx)
        self.need(ctx, 'start')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'start the titration (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.make_job(ctx, m, r, 'titration on %s' % self.instrument_id, stop=self.stop_fn())
        out = self.job_reply(job, 'ok: %s titration started' % self.instrument_id)
        out['notes'] = [n.replace('INST', self.instrument_id) for n in (m.get('after') or [])]
        return out

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        self.need(ctx, 'stop')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop (%s)' % m['cap'])
        return {'text': 'ok: %s stopped\n' % self.instrument_id, 'json': {}}

    def h_result(self, ctx, p):
        m = self.vmap('result')
        self.need(ctx, 'result')
        res = {}
        if p.get('sample') is not None or p.get('ratio') is not None:
            if not m.get('sample_arg'):
                ctx.fail(invalid('NOT_ON_THIS_INSTRUMENT', '%s computes its result from its own sample data; it takes no --sample / --ratio.'
                                 % self.instrument_id, 'see `chemlab help %s result`.' % self.instrument_id))
            if p.get('sample') is None:
                ctx.fail(invalid('MISSING_OPTION', '--ratio needs --sample.', 'give the sample volume, e.g. --sample 25mL.'))
            try:
                ratio = float(p.get('ratio') or 1)
            except ValueError:
                ctx.fail(invalid('BAD_NUMBER', '--ratio %s is not a number.' % p.get('ratio'), 'give e.g. --ratio 1 or --ratio 0.5.'))
            ep = self.sv(ctx, m['endpoint_slot'])
            ctx.check(finite(ep), 'NO_ENDPOINT', '%s has no end point yet, so there is no content to compute.' % self.instrument_id,
                      'run the titration to its end first (`chemlab run %s start`, then wait for the job).' % self.instrument_id,
                      label='end point found')
            args = {m['sample_arg']: round(self.arg_value(m['cap'], m['sample_arg'], p.get('sample')), 15), m['ratio_arg']: ratio}
            self.validate_call(ctx, m['cap'], args, {m['sample_arg']: '--sample', m['ratio_arg']: '--ratio'})
            r = ctx.call(m['cap'], args, 'compute the content for a %s sample' % units.fmt(p.get('sample'), 'volume'))
            res.update(r.get('result') or {})
        elif m.get('cap') and not m.get('sample_arg'):
            r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the result (%s)' % m['cap'], query=True)
            res.update(r.get('result') or {})
        for s in m.get('reads') or []:
            res.setdefault(s, ctx.v(s))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': '%s result:\n%s\n' % (self.instrument_id, '\n'.join(self.record_lines(res, m.get('show')))), 'json': {'result': res}}

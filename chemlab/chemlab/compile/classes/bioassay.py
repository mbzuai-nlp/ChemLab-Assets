"""Bioassay class ("bioassay"): plate reader, plate washer, real-time PCR, SPR, ITC, flow cytometer - instruments that run an
assay on plates, chips, cells or tubes.

Verbs (members and mappings: configs/adapters/bioassay.json):

  load ITEM [--into cell|syringe] [--volume V] [--type T] [--no-rinse]   put the plate / chip / solution / tube in (waits for the
                                                                           drawer, lid, port or pipette; a few seconds to minutes)
  unload [ITEM]                                                            take it out again
  method KEY=VALUE ...                                                     the assay protocol (class keys below, native names pass)
  start [NAME] [KEY=VALUE ...] [--events N] [--scan MODE] [--gate G]       start the run (a job: chemlab wait|status|cancel job-N)
  pause on|off, stop                                                       pause / resume, stop the run
  data [ID] [--cycle N] [--curves L] [--wells L] [--max N] [--save-to F]   the raw record (reads, curves, sensorgram, thermogram, stats)
  analyze MODEL [KEY=VALUE ...] [--save-to F]                              the instrument's own evaluation (fit, Cq, kinetics, ...)
  prime [KEY=VALUE ...]                                                    fill the fluid lines / start up the fluidics (a job)
  set-temp T|off                                                           incubation / analysis temperature
  status                                                                   the class view

Class keys of `method` (units as typed; a native argument name passes through, a bare number then takes the declared unit):
  plate reader: mode=abs_endpoint|fi_endpoint|... wells=A1:H12,... wl=450nm,620nm ex=485nm em=525nm format=96 (kept from the
                loaded plate) cycles=N interval=60s flashes=N optic=top|bottom gain=N focus=8mm settle=0.2s
  qPCR:         name=TEXT volume=20uL lid=105C steps=95C@3min,95C@10s,60C@30s+read,goto2x39,melt65C..95C@0.5C/5s,grad55C..65C@30s
                (goto2x39: back to step 2, 39 more times; +read: a plate read at the end of the step; +ramp=2C/s optional)
  ITC:          temp=25C ref=5ucal/s feedback=high|low|none stir=750rpm delay=60s injections=0.4uL@0.8s,18x2uL@4s spacing=150s
                cell=100uM syringe=1mM comment=TEXT (settings not given are kept from the current method when it has them)
  cytometer:    voltage.DETECTOR=520V (each detector), threshold_logic=or|and, native names with JSON values
Biacore runs take their method at start: `start RUN samples=R1B1,R1B2 contact=120s dissociation=600s flow=30uL/min regen=R1A9
regen-contact=30s path=1-2 ref=2-1 rate=10Hz startup=1` (one cycle per sample), or `cycles=@cycles.json` (JSON from your work dir).

Vendor-neutral models followed: SiLA 2 (plate readers and washers as SiLA devices: ObservableCommand runs), PyLabRobot PlateReader
(open / close / read_absorbance / read_fluorescence), MIQE / RDML terms for qPCR (Cq, threshold, melt), FCS 3.1 / MIFlowCyt for
cytometry records, ANSI/SLAS 1-4-2004 well names.
"""
import re

from .. import units
from ..base import finite, short
from ..errors import invalid, refused, unsupported
from ..verbs import Arg, Opt, Verb, save_to_opt
from .assay_common import StepAdapter, parse_si

REF = ('BMG CLARIOstar Plus operating manual (plate carrier, protocols, MARS evaluation); BioTek 405 TS operator manual (protocols, '
       'prime); Bio-Rad CFX Opus operation manual (protocol editor, run setup, Cq analysis); Biacore T200 instrument handbook (chip '
       'docking, prime, methods, evaluation); MicroCal PEAQ-ITC user manual (cell / syringe loading, experiment method, analysis); '
       'BD FACSAria III user guide (fluidics startup, sample loading, recording, compensation)')


class BioassayAdapter(StepAdapter):
    CLASS = 'bioassay'
    TITLE = 'run an assay on plates and samples'
    ABOUT = ('Instruments that run an assay on a loaded consumable: load the plate / chip / solution / tube, set the assay method '
             'with class keys and units, start the run as a job, pause or stop it, read the raw record and run the instrument\'s own '
             'evaluation; prime the fluidics; set the incubation temperature.')
    STANDARDS = ('SiLA 2 (plate readers / washers); PyLabRobot PlateReader; MIQE + RDML (qPCR); FCS 3.1 / MIFlowCyt (cytometry); '
                 'ANSI/SLAS 1-4-2004 microplate formats and well names')
    ALWAYS = ('status',)
    UNIT_KINDS = ('temperature', 'volume', 'duration', 'wavelength', 'concentration', 'flow')
    PATH_GROUPS = (('start', 'stop', 'pause'),)
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'BUSY': ('BUSY', '{inst} is busy (starting up, measuring or moving).', 'wait a little (`chemlab wait 20`) and retry; see `chemlab run {inst} status`.'),
        'INSTRUMENT_BUSY': ('BUSY', '{inst} is busy with another operation.', 'wait for it (`chemlab run {inst} status`), then retry.'),
        'NO_PLATE': ('NOTHING_LOADED', 'there is no plate in {inst}.', 'load one: `chemlab run {inst} load PLATE`.'),
        'DRAWER_OPEN': ('DRAWER_OPEN', 'the plate carrier of {inst} is out.', 'close it: `chemlab run {inst} close drawer`.'),
        'LID_OPEN': ('LID_OPEN', 'the lid of {inst} is open.', 'close it: `chemlab run {inst} close lid`.'),
        'LID_NOT_CLOSED': ('LID_OPEN', 'the lid of {inst} is not closed.', 'close it: `chemlab run {inst} close lid`.'),
        'LID_NOT_OPEN': ('LID_CLOSED', 'the lid of {inst} is closed.', 'open it: `chemlab run {inst} open lid`.'),
        'NO_PROTOCOL': ('NO_METHOD', '{inst} has no method yet.', 'set one: `chemlab run {inst} method ...` (see `chemlab help {inst} method`).'),
        'NO_METHOD': ('NO_METHOD', '{inst} has no method yet.', 'set one: `chemlab run {inst} method ...` (see `chemlab help {inst} method`).'),
        'RUN_IN_PROGRESS': ('RUN_ACTIVE', 'a run is in progress on {inst}.', 'wait for it or stop it (`chemlab run {inst} stop`).'),
        'RUN_ACTIVE': ('RUN_ACTIVE', 'a run is in progress on {inst}.', 'wait for it or stop it (`chemlab run {inst} stop`).'),
        'OPERATION_RUNNING': ('RUN_ACTIVE', '{inst} is running an operation.', 'wait for it or stop it (`chemlab run {inst} stop`).'),
        'NOT_RUNNING': ('NOT_RUNNING', 'nothing is running on {inst}.', 'nothing to stop; see `chemlab run {inst} status`.'),
        'NO_RUN': ('NOT_RUNNING', 'nothing is running on {inst}.', 'nothing to stop or pause; see `chemlab run {inst} status`.'),
        'NOT_PAUSED': ('NOT_PAUSED', 'the run on {inst} is not paused.', 'nothing to resume.'),
        'CHIP_NOT_DOCKED': ('NOTHING_LOADED', 'no sensor chip is docked in {inst}.', 'load one: `chemlab run {inst} load CHIP_ID --type CM5`.'),
        'NOT_ACQUIRING': ('NOT_ACQUIRING', '{inst} is not acquiring (no sample tube is up).', 'load a tube: `chemlab run {inst} load TUBE`.'),
        'PIPETTE_IN_CELL': ('PIPETTE_IN_CELL', 'the injection syringe of {inst} sits in the cell.', 'unload or load with the class verbs; they move it out first.'),
        'NO_WASTE_BOTTLE': ('NOT_SET_UP', '{inst} has no waste bottle in place.', 'ask the lab operator (or `chemlab call {inst} bottle.empty_waste`).'),
        'NO_BUFFER': ('NOT_SET_UP', '{inst} has no running buffer bottle.', 'place one (`chemlab call {inst} bottle.place ...`).'),
        'FLUIDICS_NOT_READY': ('FLUIDICS_OFF', 'the fluidics of {inst} are not started.', 'start them: `chemlab run {inst} prime`.'),
        'STREAM_OFF': ('STREAM_OFF', 'the stream of {inst} is off.', 'start the fluidics and the stream: `chemlab run {inst} prime`.'),
        'NO_CONCENTRATION': ('NO_CONCENTRATION', 'the cycles of this run carry no analyte concentrations, so {inst} cannot fit them.',
                             'give them: `chemlab run {inst} analyze MODEL ... concs=0,2nM,4nM` (one per cycle), or name them in the run\'s cycle keywords.'),
        'NO_SAMPLE_INJECTION': ('NO_DATA', 'a chosen cycle of this run has no sample injection.', 'choose sample cycles (see `chemlab run {inst} data`).'),
        'UNKNOWN_CYCLE': ('NO_DATA', 'this run has no such cycle.', 'see the cycles with `chemlab run {inst} data`.'),
        'CURVE_NOT_RECORDED': ('NO_DATA', 'that curve was not recorded in this run.', 'use a curve of the run\'s flow path (e.g. --curves 2-1).'),
        'NO_RUN_DATA': ('NO_DATA', 'the run has no data yet.', 'wait until it has finished (`chemlab run {inst} wait`).'),
        'NO_DATA': ('NO_DATA', 'there is no data to read or evaluate yet.', 'run something first (`chemlab run {inst} start ...`) and wait for it.'),
        'UNKNOWN_RUN': ('NO_DATA', 'there is no such run on {inst}.', 'see `chemlab run {inst} status`; name a run you started.'),
        'UNKNOWN_EXPERIMENT': ('NO_DATA', 'there is no such experiment on {inst}.', 'name one you ran (`chemlab call {inst} experiments.list`).'),
        'UNKNOWN_RECORD': ('NO_DATA', 'there is no such record on {inst}.', 'name one you recorded (`chemlab call {inst} records.list`).'),
        'TOO_FEW_EVENTS': ('NO_DATA', 'the record has too few events for this evaluation.', 'record more events (`start REC --events 10000`).'),
        'NO_POSITIVE_POPULATION': ('NO_POSITIVE', 'a compensation control shows no positive population.',
                                   'record a single-stained control tube for each detector and name it: controls.DETECTOR=RECORD.'),
        'PLATE_ON_CARRIER': ('PLATE_IN', 'a plate is on the carrier of {inst}; priming dispenses into the prime trough under it.',
                             'take it off first: `chemlab run {inst} unload`.'),
        'CARRIER_OCCUPIED': ('ALREADY_LOADED', '{inst} already holds a plate.', 'unload it first: `chemlab run {inst} unload`.'),
        'BLOCK_OCCUPIED': ('ALREADY_LOADED', '{inst} already holds a plate.', 'unload it first: `chemlab run {inst} unload`.'),
        'CELL_EMPTY': ('NOT_LOADED', 'the sample cell of {inst} is empty.', 'load it: `chemlab run {inst} load SOLUTION --into cell`.'),
        'SYRINGE_NOT_LOADED': ('NOT_LOADED', 'the syringe of {inst} is not loaded.', 'load it: `chemlab run {inst} load SOLUTION --into syringe`.'),
        'PIPETTE_NOT_IN_CELL': ('NOT_LOADED', 'the syringe of {inst} is not in the cell.', 'load the syringe (`load SOLUTION --into syringe`): it ends in the cell.'),
        'NO_REAGENT': ('NO_REAGENT', 'the injector pump of {inst} has no reagent.', 'load reagent first (`chemlab call {inst} injector.load_reagent ...`).'),
        'MAINS_OUT_OF_RANGE': ('NO_MAINS', '{inst} has no proper mains supply.', 'ask the lab operator.'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        item = Arg('ITEM', 'plate / chip / solution / tube id (see `chemlab run INST status` and `chemlab describe INST`)')
        return {
            'load': Verb('load', 'put a plate, chip, solution or tube into the instrument (waits for its mechanics)', self.h_load,
                         positionals=[item],
                         options={'into': Opt('where: cell or syringe (ITC)', metavar='PLACE'),
                                  'volume': Opt('volume to load (where the instrument asks for one)', kind='volume', metavar='V'),
                                  'type': Opt('consumable type (e.g. the SPR chip type CM5)', metavar='TYPE'),
                                  'no-rinse': Opt('do not rinse the cell with the solution first (ITC)', kind='flag')},
                         preconditions=['the item is known to the instrument (prepared on the bench)', 'nothing else loaded there',
                                        'the instrument is idle and switched on where it must be'],
                         errors=['UNKNOWN_ITEM (usage)', 'ALREADY_LOADED, BUSY, RUN_ACTIVE (refused)'], long_action=True,
                         example='chemlab run INST load p1', reference=REF),
            'unload': Verb('unload', 'take the plate, chip, solution or tube out again', self.h_unload,
                           positionals=[Arg('ITEM', 'which one (default: what is loaded)', required=False)],
                           preconditions=['no run in progress', 'something is loaded'], errors=['NOTHING_LOADED, RUN_ACTIVE (refused)'],
                           example='chemlab run INST unload', reference=REF),
            'method': Verb('method', 'set the assay method: KEY=VALUE ... (class keys take units; see the module help)', self.h_method,
                           variadic=('KEY=VALUE', 'a setting'), preconditions=['no run in progress', 'values within the contract ranges'],
                           errors=['UNKNOWN_SETTING, OUT_OF_RANGE, MISSING_SETTING (usage)', 'RUN_ACTIVE (refused)'],
                           example='chemlab run INST method mode=abs_endpoint wells=A1:H12 wl=450nm', reference=REF),
            'start': Verb('start', 'start the run (returns job-N: chemlab status|wait|cancel job-N)', self.h_start, long_action=True,
                          positionals=[Arg('NAME', 'protocol name / run, experiment or record id (where the instrument names runs)', required=False)],
                          options={'events': Opt('events to record (cytometer)', metavar='N'),
                                   'scan': Opt('scan mode (qPCR: all_channels, sybr_fam, fret)', metavar='MODE'),
                                   'gate': Opt('storage gate (cytometer)', metavar='GATE')},
                          variadic=('KEY=VALUE', 'run settings (SPR method keys)'),
                          preconditions=['loaded and ready (plate in, lid / drawer closed, method set, syringe in the cell, ...)'],
                          errors=['NOTHING_LOADED, NO_METHOD, LID_OPEN, RUN_ACTIVE (refused)'], example='chemlab run INST start', reference=REF),
            'pause': Verb('pause', 'pause (on) or resume (off) the run', self.h_pause, positionals=[Arg('on|off', 'on = pause, off = resume', choices=['on', 'off'])],
                          preconditions=['a run is going (on) / paused (off)'], errors=['NOT_RUNNING, NOT_PAUSED (refused)'],
                          example='chemlab run INST pause on', reference=REF),
            'stop': Verb('stop', 'stop the run', self.h_stop, preconditions=['something is running'], errors=['NOT_RUNNING (refused)'],
                         example='chemlab run INST stop', reference=REF),
            'data': Verb('data', 'the raw record of a run (reads, curves, sensorgram, thermogram, statistics)', self.h_data, readonly=True,
                         positionals=[Arg('ID', 'run / record / experiment id (default: the last one)', required=False)],
                         options={'cycle': Opt('cycle number', metavar='N'), 'curves': Opt('curves, e.g. 2-1,2 (SPR)', metavar='LIST'),
                                  'wells': Opt('wells, e.g. A1,B2 (qPCR)', metavar='LIST'), 'max': Opt('at most this many rows / points', metavar='N'),
                                  'save-to': save_to_opt()},
                         example='chemlab run INST data --save-to run.json', reference=REF),
            'analyze': Verb('analyze', 'evaluate a run with the instrument\'s own analysis: MODEL [KEY=VALUE ...]', self.h_analyze, readonly=True,
                            positionals=[Arg('MODEL', 'the evaluation (see `chemlab help INST analyze`)')],
                            options={'save-to': save_to_opt()}, variadic=('KEY=VALUE', 'evaluation settings'),
                            preconditions=['a finished run with data'], errors=['UNKNOWN_MODEL (usage)', 'NO_DATA (refused)'],
                            example='chemlab run INST analyze linear blank=H1,H2 std.A1=0uM std.A2=1uM', reference=REF),
            'prime': Verb('prime', 'prime the fluid lines / start up the fluidics (a job when it takes long)', self.h_prime, long_action=True,
                          variadic=('KEY=VALUE', 'prime settings (pump, buffer, volume, rate)'),
                          preconditions=['the fluid supply is in place', 'no run in progress'], errors=['BUSY, NOT_SET_UP (refused)'],
                          example='chemlab run INST prime', reference=REF),
            'set-temp': Verb('set-temp', 'set the incubation / analysis temperature (or off)', self.h_set_temp,
                             positionals=[Arg('TEMP|off', 'e.g. 37C, or off where the instrument can switch it off')],
                             preconditions=['within the instrument range (contract)', 'one of the fixed settings where it has them'],
                             errors=['OUT_OF_RANGE, NOT_ALLOWED (usage)', 'POWER_OFF (refused)'], example='chemlab run INST set-temp 37C', reference=REF),
        }

    # ------------------------------------------------------------------ helpers
    def unknown_opts(self, ctx, p, allowed, verb):
        for o in ('events', 'scan', 'gate', 'cycle', 'curves', 'wells', 'max', 'into', 'volume', 'type', 'no-rinse'):
            if p.get(o) is not None and o not in allowed:
                ctx.fail(invalid('NOT_AN_OPTION_HERE', '%s takes no --%s on %s.' % (verb, o, self.instrument_id),
                                 'leave it out (see `chemlab help %s %s`).' % (self.instrument_id, verb)))

    def int_opt(self, ctx, text, what):
        if text is None:
            return None
        if not re.fullmatch(r'\d+', str(text).strip()):
            ctx.fail(invalid('NOT_WHOLE', '--%s %s is not a whole number.' % (what, text), 'give a whole number.'))
        return int(text)

    def list_opt(self, text):
        return [x.strip() for x in str(text).split(',') if x.strip()] if text is not None else None

    def reply(self, what, r, extra=''):
        res = r.get('result')
        lines = ['%s %s:' % (self.instrument_id, what)]
        if isinstance(res, dict):
            for k, v in list(res.items())[:14]:
                lines.append('  %s = %s' % (k, short(v, 160)))
        else:
            lines.append('  %s' % short(res, 400))
        return '\n'.join(lines) + '\n' + extra

    # ------------------------------------------------------------------ load / unload
    def h_load(self, ctx, p):
        m = self.vmap('load')
        into = p.get('into')
        if 'into' in m:
            into = into or m.get('default_into')
            if into not in m['into']:
                ctx.fail(invalid('BAD_CHOICE', '--into %s is not a place of %s.' % (into, self.instrument_id), 'use one of: %s.' % ', '.join(m['into'])))
            sub = m['into'][into]
        else:
            if into is not None:
                ctx.fail(invalid('NOT_AN_OPTION_HERE', 'load takes no --into on %s.' % self.instrument_id, 'leave it out.'))
            sub = m
        allowed = {'into'} | set(sub.get('options') or [])
        self.unknown_opts(ctx, p, allowed, 'load')
        item = p.pos[0]
        self.need(ctx, 'load' if not into else 'load:' + into)
        self.known(ctx, item, sub.get('items'), sub.get('word', 'item'))
        env = {'item': item, 'rinse': not p.get('no-rinse')}
        vol = p.get('volume')
        if vol is None and sub.get('default_volume'):
            vol = units.parse(sub['default_volume'], 'volume')
        env['volume'] = vol
        if 'type' in (sub.get('options') or []):
            env['type'] = p.get('type') or sub.get('default_type')
            if not env['type']:
                ctx.fail(invalid('MISSING_OPTION', 'load needs --type on %s.' % self.instrument_id, 'e.g. --type %s.' % sub.get('type_example', 'TYPE')))
        self.run_steps(ctx, sub['steps'], env)
        where = ('the %s of %s' % (into, self.instrument_id)) if into else self.instrument_id
        return {'text': 'ok: %s %s loaded into %s\n' % (sub.get('word', 'item'), item, where) if not ctx.dry_run else '',
                'json': {'item': item, 'into': into, 'volume_m3': vol}}

    def h_unload(self, ctx, p):
        m = self.vmap('unload')
        item = p.pos[0] if p.pos else None
        loaded = self.sv(ctx, m['item_slot']) if m.get('item_slot') else None
        if isinstance(loaded, dict):
            loaded = loaded.get(m.get('item_key', 'id'))
        if m.get('item_slot') and loaded is None:
            ctx.fail(refused('NOTHING_LOADED', 'nothing is loaded in %s.' % self.instrument_id, 'nothing to unload; see `chemlab run %s status`.' % self.instrument_id))
        if item is not None and loaded is not None and item != loaded:
            ctx.fail(invalid('NOT_LOADED', '%s holds %s, not %s.' % (self.instrument_id, loaded, item), 'unload %s, or leave out the name.' % loaded))
        item = item or loaded
        self.need(ctx, 'unload')
        self.run_steps(ctx, m['steps'], {'item': item})
        return {'text': 'ok: %s unloaded from %s\n' % (item or 'it', self.instrument_id) if not ctx.dry_run else '', 'json': {'item': item}}

    # ------------------------------------------------------------------ method
    def qpcr_steps(self, ctx, text, schema):
        """95C@3min,95C@10s,60C@30s+read,goto2x39,melt65C..95C@0.5C/5s,grad55C..65C@30s -> the protocol step list."""
        items = (schema or {}).get('items') or {}
        props = items.get('properties') or {}

        def t(v, field, kind='temperature'):
            si = units.parse(v, kind, what='steps')
            u = units.declared(props.get(field) or {})
            return units.to_declared(si, u) if u else si
        out = []
        for part in [x.strip() for x in text.split(',') if x.strip()]:
            low = part.lower().replace(' ', '')
            m = re.fullmatch(r'goto(\d+)x(\d+)', low)
            if m:
                out.append({'kind': 'goto', 'goto_step': int(m.group(1)), 'cycles': int(m.group(2))})
                continue
            m = re.fullmatch(r'melt:?(.+?)\.\.(.+?)@(.+?)/(.+)', low)
            if m:
                out.append({'kind': 'melt', 'start_k': t(m.group(1), 'start_k'), 'end_k': t(m.group(2), 'end_k'),
                            'melt_increment_k': t(m.group(3), 'melt_increment_k', 'temperature_delta'), 'hold_s': t(m.group(4), 'hold_s', 'duration')})
                continue
            m = re.fullmatch(r'grad:?(.+?)\.\.(.+?)@([^+]+)(.*)', low)
            if m:
                st = {'kind': 'gradient', 'lower_k': t(m.group(1), 'lower_k'), 'upper_k': t(m.group(2), 'upper_k'), 'hold_s': t(m.group(3), 'hold_s', 'duration')}
                out.append(self._flags(ctx, st, m.group(4), t, part))
                continue
            m = re.fullmatch(r'([^@]+)@([^+]+)(.*)', low)
            if not m:
                ctx.fail(invalid('BAD_PROGRAM', 'qPCR step %r is not understood.' % part,
                                 'write steps like 95C@3min,95C@10s,60C@30s+read,goto2x39,melt65C..95C@0.5C/5s.'))
            st = {'kind': 'temperature', 'temperature_k': t(m.group(1), 'temperature_k'), 'hold_s': t(m.group(2), 'hold_s', 'duration')}
            out.append(self._flags(ctx, st, m.group(3), t, part))
        if not out:
            ctx.fail(invalid('BAD_PROGRAM', 'steps is empty.', 'give at least one step, e.g. 95C@3min.'))
        return out

    def _flags(self, ctx, st, rest, t, part):
        for f in [x for x in rest.split('+') if x]:
            if f == 'read':
                st['plate_read'] = True
            elif f == 'beep':
                st['beep'] = True
            elif f.startswith('ramp='):
                st['ramp_rate_k_s'] = t(f[5:], 'ramp_rate_k_s', 'heating_rate')
            else:
                ctx.fail(invalid('BAD_PROGRAM', 'qPCR step %r: %r is not +read, +beep or +ramp=RATE.' % (part, f), 'e.g. 60C@30s+read.'))
        return st

    def injections(self, ctx, text, schema, spacing, rate):
        """0.4uL@0.8s,18x2uL@4s (spacing=150s for all) -> the ITC injection list; a duration left out is volume / the member's
        documented injection rate."""
        items = ((schema or {}).get('items') or {}).get('properties') or {}
        out = []
        for part in [x.strip() for x in text.split(',') if x.strip()]:
            m = re.fullmatch(r'(?:(\d+)x)?([^@/]+)(?:@([^/]+))?(?:/(.+))?', part.replace(' ', ''))
            if not m:
                ctx.fail(invalid('BAD_PROGRAM', 'injection %r is not [Nx]VOLUME[@DURATION][/SPACING].' % part, 'e.g. 0.4uL@0.8s,18x2uL@4s.'))
            n = int(m.group(1) or 1)
            v = units.parse(m.group(2), 'volume', what='injection volume')
            d = units.parse(m.group(3), 'duration', what='injection duration') if m.group(3) else (v / rate if rate else None)
            sp = units.parse(m.group(4), 'duration', what='spacing') if m.group(4) else spacing
            if d is None or sp is None:
                ctx.fail(invalid('MISSING_SETTING', 'injection %r needs a duration and a spacing.' % part, 'write e.g. 2uL@4s and spacing=150s.'))
            one = {}
            for field, si in (('volume_m3', v), ('duration_s', d), ('spacing_s', sp)):
                u = units.declared(items.get(field) or {})
                one[field] = units.to_declared(si, u) if u else si
            out += [dict(one) for _ in range(n)]
        return out

    def h_method(self, ctx, p):
        m = self.vmap('method')
        cap = m['cap']
        pairs = self.pairs(ctx, p, 'method')
        spacing = next((units.parse(v, 'duration', what='spacing') for k, v in pairs if k == 'spacing'), None)
        pairs = [(k, v) for k, v in pairs if k != 'spacing' or 'spacing' in (m.get('keys') or {})]
        rate = self.param('injection_rate_m3_s')
        parsers = {'qpcr_steps': self.qpcr_steps, 'injections': lambda c, t, s: self.injections(c, t, s, spacing, rate)}
        args = self.settings(ctx, cap, pairs, m.get('keys') or {}, m.get('passthrough', True), dict(m.get('args') or {}), parsers)
        kept = []
        for arg, spec in (m.get('keep') or {}).items():
            if arg not in args:
                v = self.sv(ctx, spec['slot'] + ('#' + spec['key'] if spec.get('key') else ''))
                if v is not None:
                    args[arg] = v
                    kept.append(arg)
        req = ((ctx.cap_spec(cap).get('input_schema') or {}).get('required') or [])
        missing = [a for a in req if a not in args]
        if missing:
            keys = m.get('keys') or {}
            names = ['%s (%s)' % (next((k for k, s in keys.items() if s['arg'] == a), a), a) for a in missing]
            ctx.fail(invalid('MISSING_SETTING', 'the method of %s also needs %s.' % (self.instrument_id, ', '.join(names)),
                             'add them (see `chemlab help %s method`).' % self.instrument_id))
        self.need(ctx, 'method')
        self.check_args(ctx, cap, args)
        r = ctx.call(cap, args, 'set the method')
        text = 'ok: method of %s set (%s)%s\n' % (self.instrument_id, ', '.join(sorted(args)), ('; kept from the current method: %s' % ', '.join(kept)) if kept else '')
        res = r.get('result') if isinstance(r.get('result'), dict) else {}
        if res:
            text += ''.join('  %s = %s\n' % (k, short(v, 120)) for k, v in list(res.items())[:6])
        return {'text': text if not ctx.dry_run else '', 'json': {'arguments': args, 'kept': kept, 'result': res}}

    # ------------------------------------------------------------------ start / pause / stop
    def series_cycles(self, ctx, kv, schema):
        """SPR shorthand: samples=R1B1,R1B2 contact=120s dissociation=600s flow=30uL/min regen=R1A9 regen-contact=30s startup=N
        -> one cycle per sample (startup cycles first, with the first sample)."""
        cmd_props = ((((schema or {}).get('items') or {}).get('properties') or {}).get('commands') or {}).get('items', {}).get('properties') or {}

        def num(field, text, kind):
            si = units.parse(text, kind, what=field)
            u = units.declared(cmd_props.get(field) or {})
            return units.to_declared(si, u) if u else si
        samples = [x for x in kv.pop('samples').split(',') if x.strip()]
        for need in ('contact', 'flow'):
            if need not in kv:
                ctx.fail(invalid('MISSING_SETTING', 'samples=... needs %s=.' % need, 'e.g. contact=120s flow=30uL/min.'))
        base = {'type': 'sample', 'contact_s': num('contact_s', kv.pop('contact'), 'duration'),
                'dissociation_s': num('dissociation_s', kv.pop('dissociation', '0s'), 'duration'),
                'flow_rate_m3_s': num('flow_rate_m3_s', kv.pop('flow'), 'flow')}
        regen = None
        if 'regen' in kv:
            regen = {'type': 'regeneration', 'position': kv.pop('regen'), 'contact_s': num('contact_s', kv.pop('regen-contact', '30s'), 'duration'),
                     'flow_rate_m3_s': base['flow_rate_m3_s']}
        n_start = int(kv.pop('startup', '0'))
        cycles = []
        for i, pos in enumerate([samples[0]] * n_start + samples):
            cmds = [dict(base, position=pos.strip())] + ([dict(regen)] if regen else [])
            cycles.append({'purpose': 'startup' if i < n_start else 'sample', 'commands': cmds})
        return cycles

    def running(self, ctx, m):
        c = m.get('running')
        return True if c is None else self.cond(ctx, c)

    def h_start(self, ctx, p):
        m = self.vmap('start')
        cap = m['cap']
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        opts = m.get('opts') or {}
        self.unknown_opts(ctx, p, set(opts), 'start')
        args = dict(m.get('args') or {})
        name = p.pos[0] if p.pos else None
        nm = m.get('name')
        if nm:
            if name is None and nm.get('required'):
                ctx.fail(invalid('MISSING_ARGUMENT', 'start needs a %s on %s.' % (nm.get('word', 'name'), self.instrument_id),
                                 'e.g. `chemlab run %s start %s`.' % (self.instrument_id, nm.get('example', 'NAME'))))
            if name is not None:
                self.known(ctx, name, nm.get('list'), nm.get('word', 'name'))
                args[nm['arg']] = name
        elif name is not None:
            ctx.fail(invalid('EXTRA_ARGUMENT', '%s names no runs; start takes no NAME.' % self.instrument_id, 'write just `chemlab run %s start`.' % self.instrument_id))
        for o, spec in opts.items():
            v = p.get(o)
            if v is None:
                v = spec.get('default')
            if v is None:
                if spec.get('required'):
                    ctx.fail(invalid('MISSING_OPTION', 'start needs --%s on %s.' % (o, self.instrument_id), 'e.g. --%s %s.' % (o, spec.get('example', 'VALUE'))))
                continue
            args[spec['arg']] = self.value(ctx, str(v), props.get(spec['arg']) or {}, None, '--' + o)
        pairs = self.pairs(ctx, p, 'start', required=False)
        if pairs and not m.get('keys') and not m.get('passthrough'):
            ctx.fail(invalid('EXTRA_ARGUMENT', 'start takes no settings on %s.' % self.instrument_id, 'set them with `method` first.'))
        if pairs:
            kv = dict(pairs)
            if m.get('series') and 'samples' in kv:
                if 'cycles' in kv:
                    ctx.fail(invalid('REPEATED_SETTING', 'give samples=... or cycles=..., not both.', 'choose one.'))
                ser = {k: kv.pop(k) for k in list(kv) if k in ('samples', 'contact', 'dissociation', 'flow', 'regen', 'regen-contact', 'startup')}
                args['cycles'] = self.series_cycles(ctx, ser, props.get('cycles'))
            args = self.settings(ctx, cap, list(kv.items()), m.get('keys') or {}, m.get('passthrough', True), args)
        req = ((ctx.cap_spec(cap).get('input_schema') or {}).get('required') or [])
        missing = [a for a in req if a not in args]
        if missing:
            ctx.fail(invalid('MISSING_SETTING', 'start on %s also needs %s.' % (self.instrument_id, ', '.join(missing)),
                             'see `chemlab help %s start`.' % self.instrument_id))
        self.need(ctx, 'start')
        self.check_args(ctx, cap, args)
        what = '%s run%s' % (self.instrument_id, (' ' + name) if name else '')
        r = ctx.call(cap, args, 'start the run' + ((' ' + name) if name else ''))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.start_job(ctx, m, r, cap, what)
        if job is None:
            return {'text': 'ok: %s started\n' % what, 'json': {'result': r.get('result')}}
        return {'text': self.job_text(job, what), 'json': {'job': job.handle, 'kind': job.kind, 'arguments': args}}

    def h_pause(self, ctx, p):
        m = self.vmap('pause')
        on = p.pos[0] == 'on'
        c = m.get('running') if on else m.get('paused')
        ctx.check(c is None or self.cond(ctx, c), 'NOT_RUNNING' if on else 'NOT_PAUSED',
                  ('no run on %s can be paused now (it is not running).' if on else 'the run on %s is not paused.') % self.instrument_id,
                  'see `chemlab run %s status`%s.' % (self.instrument_id, '; a run that is still warming up is paused once it runs' if on else ''),
                  label='run %s' % ('going' if on else 'paused'))
        cap = m['on_cap'] if on else m['off_cap']
        ctx.call(cap, dict(m.get('args') or {}), 'pause the run' if on else 'resume the run')
        return {'text': 'ok: run on %s %s\n' % (self.instrument_id, 'paused' if on else 'resumed') if not ctx.dry_run else '',
                'json': {'paused': on}}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        ctx.check(self.running(ctx, m), 'NOT_RUNNING', 'nothing is running on %s.' % self.instrument_id,
                  'nothing to stop; see `chemlab run %s status`.' % self.instrument_id, label='something running')
        if m.get('steps'):
            self.run_steps(ctx, m['steps'], {})
        else:
            ctx.call(m['cap'], dict(m.get('args') or {}), 'stop the run')
        return {'text': 'ok: %s stopped\n' % self.instrument_id if not ctx.dry_run else '', 'json': {'stopped': True}}

    # ------------------------------------------------------------------ records
    def idarg(self, ctx, spec, given, args):
        """The run / record id argument: given, else the slot, else the spec default."""
        if not spec or spec['arg'] in args:
            return
        v = given
        if v is None and spec.get('slot'):
            v = self.sv(ctx, spec['slot'] + ('#' + spec['key'] if spec.get('key') else ''))
        if v is None:
            v = spec.get('default')
        if v is None and spec.get('required'):
            ctx.fail(refused('NO_DATA', '%s has no %s yet.' % (self.instrument_id, spec.get('word', 'run')),
                             'run something first (`chemlab run %s start`), or name it.' % self.instrument_id))
        if v is not None:
            args[spec['arg']] = v

    def h_data(self, ctx, p):
        m = self.vmap('data')
        if p.get('cycle') is not None and m.get('by_cycle'):
            m = dict(m['by_cycle'], id=m['by_cycle'].get('id', m.get('id')))
        cap = m['cap']
        opts = m.get('opts') or {}
        self.unknown_opts(ctx, p, set(opts), 'data')
        args = dict(m.get('args') or {})
        given = p.pos[0] if p.pos else None
        if given is not None and not m.get('id'):
            ctx.fail(invalid('EXTRA_ARGUMENT', '%s keeps one record; data takes no ID.' % self.instrument_id, 'write just `chemlab run %s data`.' % self.instrument_id))
        self.idarg(ctx, m.get('id'), given, args)
        for o, spec in opts.items():
            v = p.get(o)
            if v is None:
                if spec.get('required'):
                    ctx.fail(invalid('MISSING_OPTION', 'data needs --%s here.' % o, 'e.g. --%s %s.' % (o, spec.get('example', 'VALUE'))))
                continue
            args[spec['arg']] = self.int_opt(ctx, v, o) if spec.get('int') else self.list_opt(v) if spec.get('list') else v
        self.check_args(ctx, cap, args)
        r = ctx.call(cap, args, 'read the record (%s)' % cap, query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': self.reply('record', r), 'json': {'record': r.get('result'), 'arguments': args}}

    def h_analyze(self, ctx, p):
        m = self.vmap('analyze')
        model = p.pos[0]
        models = m.get('models') or {}
        if m.get('model_arg') and not models:
            models = {x: {} for x in self.arg_enum(m['cap'], m['model_arg'])}
        if model not in models:
            import difflib
            close = difflib.get_close_matches(model, list(models), 2)
            ctx.fail(invalid('UNKNOWN_MODEL', '%s has no evaluation %r.' % (self.instrument_id, model),
                             ('did you mean %s? ' % ' or '.join(close) if close else '') + 'evaluations: %s.' % ', '.join(models)))
        sub = dict(models[model])
        cap = sub.get('cap', m.get('cap'))
        args = dict(m.get('args') or {})
        args.update(sub.get('args') or {})
        if m.get('model_arg'):
            args[m['model_arg']] = model
        pairs = self.pairs(ctx, p, 'analyze', required=False)
        args = self.settings(ctx, cap, pairs, dict(m.get('keys') or {}, **(sub.get('keys') or {})), True, args)
        self.idarg(ctx, sub.get('id', m.get('id')), None, args)
        req = ((ctx.cap_spec(cap).get('input_schema') or {}).get('required') or [])
        missing = [a for a in req if a not in args]
        if missing:
            ctx.fail(invalid('MISSING_SETTING', 'analyze %s on %s also needs %s.' % (model, self.instrument_id, ', '.join(missing)),
                             'add them as KEY=VALUE (see `chemlab help %s analyze`).' % self.instrument_id))
        self.check_args(ctx, cap, args)
        r = ctx.call(cap, args, 'evaluate (%s)' % model, query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': self.reply('%s evaluation' % model, r), 'json': {'model': model, 'evaluation': r.get('result'), 'arguments': args}}

    # ------------------------------------------------------------------ prime / temperature
    def h_prime(self, ctx, p):
        m = self.vmap('prime')
        if m.get('steps'):                                               # a composite start-up that waits (cytometer fluidics + stream)
            if p.extra:
                ctx.fail(invalid('EXTRA_ARGUMENT', 'prime takes no settings on %s.' % self.instrument_id, 'write just `chemlab run %s prime`.' % self.instrument_id))
            self.need(ctx, 'prime')
            t0 = ctx.now()
            self.run_steps(ctx, m['steps'], {})
            return {'text': 'ok: %s primed (%s of lab time)\n' % (self.instrument_id, units.fmt_s(ctx.now() - t0)) if not ctx.dry_run else '',
                    'json': {'primed': True}}
        cap = m['cap']
        pairs = self.pairs(ctx, p, 'prime', required=False)
        args = self.settings(ctx, cap, pairs, m.get('keys') or {}, True, dict(m.get('args') or {}))
        defaulted = []
        req = ((ctx.cap_spec(cap).get('input_schema') or {}).get('required') or [])
        if m.get('defaults') == 'example':
            ex = (ctx.cap_spec(cap).get('example_arguments') or {})
            for a in req:
                if a not in args and a in ex:
                    args[a] = ex[a]
                    defaulted.append(a)
        missing = [a for a in req if a not in args]
        if missing:
            keys = m.get('keys') or {}
            names = ['%s (%s)' % (next((k for k, s in keys.items() if s['arg'] == a), a), a) for a in missing]
            ctx.fail(invalid('MISSING_SETTING', 'prime on %s needs %s.' % (self.instrument_id, ', '.join(names)), 'e.g. %s.' % m.get('example', '')))
        self.need(ctx, 'prime')
        self.check_args(ctx, cap, args)
        r = ctx.call(cap, args, 'prime (%s)' % cap)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        note = ('; the contract\'s documented example values for %s' % ', '.join(defaulted)) if defaulted else ''
        job = self.start_job(ctx, m, r, cap, 'prime %s' % self.instrument_id)
        if job is None:
            return {'text': 'ok: %s primed%s\n' % (self.instrument_id, note), 'json': {'arguments': args, 'defaulted': defaulted}}
        return {'text': self.job_text(job, 'prime %s%s' % (self.instrument_id, note)), 'json': {'job': job.handle, 'arguments': args, 'defaulted': defaulted}}

    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        cap, arg = m['cap'], m['arg']
        text = p.raw['TEMP|off'].strip().lower()
        args = dict(m.get('args') or {})
        if text == 'off':
            if not m.get('off'):
                ctx.fail(invalid('NO_OFF', '%s cannot switch this temperature control off.' % self.instrument_id, 'give a temperature instead.'))
            args[arg] = None
            k = None
        else:
            k = units.parse(text, 'temperature', what='temperature')
            args[arg] = self.resolve(ctx, cap, arg, k)
        self.need(ctx, 'set-temp')
        self.check_args(ctx, cap, args)
        ctx.call(cap, args, 'set the temperature to %s' % ('off' if k is None else units.fmt(k, 'temperature')))
        return {'text': 'ok: %s temperature %s\n' % (self.instrument_id, 'off' if k is None else units.fmt(k, 'temperature')) if not ctx.dry_run else '',
                'json': {'set_point_k': k}}


_ = (finite, unsupported, parse_si)

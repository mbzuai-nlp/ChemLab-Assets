"""Extraction and membrane separation class ("extraction"): pressurized solvent extraction, solid-phase extraction, supercritical
fluid extraction, flow liquid-liquid extraction, tangential-flow ultrafiltration, electrodialysis.

Verbs: method KEY=VALUE ..., set-temp T|off, pressure P|off [--channel bpr|cmp], flow F|off, prime [--inlet S1] [--volume V]
[--solvent ID] [--time T], start (job), stop, vent (job), result [--save-to], status.  Members and mappings:
configs/adapters/extraction.json.

Pressures are gauge pressures (above atmosphere) on every member whose native argument says so (pressure_gauge_pa,
bpr_gauge_pa, value_pa of the BPR); the Extrahera's positive-pressure set point has no stated reference.  Nothing is converted
between gauge and absolute.

Method dictionary (class keys with units; native argument names pass through):
  temp=100C (cell / oven), pressure=100bar (gauge), cycles=2, hold=5min (static hold), discharge=1min, flow=2mL/min (pump /
  liquid / feed flow), solvents=1,0,0,0 (fractions of solvents 1-4), flush=60s (solvent flush), nitrogen=3min (gas flush),
  static=10min / dynamic=20min (SFE), restrictor=80C, modifier=0%, aqueous=0.1mL/min / organic=0.1mL/min (FLLEX phases),
  bpr=2bar, cmp=100mbar (cross-membrane), time=10min, timeout=1h, mode=concentrate|diafilter|constant_current|constant_voltage,
  circulation=300mL/min, tmp=2bar (transmembrane), retentate=100mL, diavolumes=5, voltage=20V, current=10mA,
  endpoint=0.1mS/cm (diluate conductivity).  Solid-phase extraction (Extrahera): load=1mL wash=1mL elute=1mL flow=6mL/min
  drain=6mL/min reps=1, and per phase load.flow= / wash.reps= / elute.solvent=POSITION.

Vendor-neutral models: EPA 3545A (pressurized fluid extraction), EPA 3535A (solid-phase extraction), EPA 3560 (supercritical
fluid extraction) and the MTP / ISA-88 state model (start / hold / stop / abort); no command standard exists.
"""
from .. import units
from ..base import finite, short
from ..errors import invalid
from ..verbs import Opt, Verb
from .separation_common import SeparationAdapter, fmtq

REF = None                       # manual pages are member notes in the fragment ("manual: ...")
PHASES = ('load', 'wash', 'elute')


class ExtractionAdapter(SeparationAdapter):
    CLASS = 'extraction'
    TITLE = 'extraction and membrane separation'
    ABOUT = ('Extract or separate by phase or membrane: a method of class keys, the cell / oven temperature, the (gauge) pressure '
             'set point, the flow, priming / flushing the solvent lines, a run as a job, stop, vent the residual pressure, and the '
             'run record.  Samples, cells, membranes, cassettes and utilities are the lab\'s set-up, not agent actions.')
    STANDARDS = 'EPA 3545A (PLE), EPA 3535A (SPE), EPA 3560 (SFE); MTP / ISA-88 state model (start/hold/stop/abort)'
    UNIT_KINDS = ('temperature', 'pressure', 'flow', 'volume', 'duration')
    SETTER_KINDS = {'set-temp': 'temperature', 'pressure': 'pressure', 'flow': 'flow'}
    ROLES = (('power', 'power', 'bool'), ('phase', 'run', 'text'), ('stage', 'stage', 'text'), ('fault', 'fault', 'text'),
             ('temp', 'temperature', 'temperature'), ('temp_set', 'temperature set point', 'temperature'),
             ('pressure', 'pressure (gauge)', 'pressure'), ('pressure_set', 'pressure set point', 'pressure'),
             ('pressure2', 'second pressure', 'pressure'), ('flow', 'flow', 'flow'), ('flow2', 'second flow', 'flow'),
             ('voltage', 'voltage', 'voltage'), ('current', 'current', 'current'), ('conductivity', 'conductivity', 'conductivity'),
             ('volume', 'volume', 'volume'), ('volume2', 'second volume', 'volume'), ('progress', 'progress', 'plain'),
             ('state', 'state', 'plain'))

    def build_verbs(self):
        v = {
            'method': self.method_verb('set the extraction / filtration method: KEY=VALUE ... (class keys take units: temp=100C '
                                       'pressure=100bar flow=2mL/min time=10min; native argument names pass through)',
                                       'chemlab run INST method temp=100C pressure=100bar cycles=2', reference=REF,
                                       details='class keys: temp, pressure, cycles, hold, discharge, flow, solvents, flush, nitrogen, static, '
                                               'dynamic, restrictor, modifier, aqueous, organic, bpr, cmp, time, timeout, mode, circulation, '
                                               'tmp, retentate, diavolumes, voltage, current, endpoint; SPE: load, wash, elute, flow, drain, '
                                               'reps, PHASE.flow / PHASE.drain / PHASE.reps / PHASE.solvent (the member notes say which)'),
            'set-temp': self.setter_verb('set-temp', 'set the cell / oven temperature (off: preheating off)', 'temperature', 'TEMP',
                                         'chemlab run INST set-temp 100C', off=True, reference=REF),
            'pressure': self.setter_verb('pressure', 'set the pressure set point (gauge, above atmosphere; off = 0)', 'pressure', 'PRESSURE',
                                         'chemlab run INST pressure 100bar', off=True, targets=('channel',), reference=REF,
                                         details='--channel bpr (back-pressure regulator, the default) or cmp (cross-membrane) on '
                                                 'members with two pressure controls'),
            'flow': self.setter_verb('flow', 'set the pump / stream flow', 'flow', 'FLOW', 'chemlab run INST flow 1mL/min', reference=REF),
            'prime': Verb('prime', 'prime (or flush) the solvent lines before a run', self.h_prime,
                          options={'inlet': Opt('which solvent inlet (S1-S5)', metavar='INLET'),
                                   'volume': Opt('volume to pump through the line', kind='volume', metavar='VOL'),
                                   'solvent': Opt('assign this library solvent to the inlet first', metavar='ID'),
                                   'time': Opt('solvent flush time', kind='duration', metavar='TIME')},
                          example='chemlab run INST prime --inlet S1 --volume 15mL', reference=REF,
                          preconditions=['a solvent assigned to the inlet (or --solvent)', 'door / shield closed', 'enough solvent'],
                          errors=['NO_SOLVENT (refused)', 'DOOR_OPEN (refused)', 'OUT_OF_RANGE (usage)']),
            'vent': Verb('vent', 'release the residual pressure after a run (a job)', self.h_vent, example='chemlab run INST vent',
                         preconditions=['no run in progress', 'pressure left to release'], errors=['RUNNING (refused)', 'NOTHING_TO_VENT (refused)'],
                         reference=REF),
        }
        v.update(self.run_verbs())
        return v

    # ------------------------------------------------------------------ method (solid-phase extraction steps)
    def h_method(self, ctx, p):
        m = self.vmap('method')
        if m.get('mode') != 'spe':
            return super().h_method(ctx, p)
        cap, arg = m['cap'], m['arg']
        item = (self.schema_of(cap, arg).get('items') or {}).get('properties') or {}
        per = {ph: {} for ph in PHASES}
        common = {}
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'see `chemlab help %s method`.' % self.instrument_id))
        kinds = {'flow': ('flow_m3_s', 'flow'), 'drain': ('drain_flow_m3_s', 'flow'), 'reps': ('repetitions', None), 'solvent': ('solvent_position', None)}
        for x in p.extra:
            k, eq, val = x.partition('=')
            if not eq:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % x, 'write method load=1mL wash=1mL elute=1mL flow=6mL/min ...'))
            ph, dot, sub = k.partition('.')
            if not dot and k in PHASES:
                per[k]['volume_m3'] = self.parse_value(ctx, val, item['volume_m3'], 'volume', k)
            elif not dot and k in kinds:
                native, kind = kinds[k]
                common[native] = self.parse_value(ctx, val, item[native], kind, k)
            elif dot and ph in PHASES and sub in kinds:
                native, kind = kinds[sub]
                per[ph][native] = self.parse_value(ctx, val, item[native], kind, k)
            else:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (self.instrument_id, k),
                                 'settings: load, wash, elute (volumes), flow, drain, reps, solvent, and PHASE.flow / PHASE.drain / '
                                 'PHASE.reps / PHASE.solvent.'))
        self.need(ctx, 'method')
        positions = ctx.v(m.get('positions_slot')) or []
        steps, missing = [], []
        for ph in PHASES:
            st = {'phase': ph}
            st.update({k2: v2 for k2, v2 in common.items()})
            st.update(per[ph])
            st.setdefault('solvent_position', ph)
            st.setdefault('repetitions', 1)
            for need in ('volume_m3', 'flow_m3_s', 'drain_flow_m3_s'):
                if need not in st:
                    missing.append('%s.%s' % (ph, {'volume_m3': 'volume', 'flow_m3_s': 'flow', 'drain_flow_m3_s': 'drain'}[need])
                                   if need != 'volume_m3' else ph)
            if st['solvent_position'] not in positions and not ctx.dry_run:
                ctx.fail(invalid('UNKNOWN_POSITION', 'the deck of %s has no solvent position %r (positions: %s).' % (
                    self.instrument_id, st['solvent_position'], ', '.join(positions) or 'none'),
                    'give the position: `%s.solvent=POSITION`.' % ph))
            steps.append(st)
        if missing:
            names = []
            for x in missing:
                base = x.split('.', 1)[1] if '.' in x else None
                if base and all('%s.%s' % (ph, base) in missing for ph in PHASES):
                    x = base
                if x not in names:
                    names.append(x)
            ctx.fail(invalid('MISSING_SETTING', 'the SPE method of %s also needs %s.' % (self.instrument_id, ', '.join(names)),
                             'e.g. `chemlab run %s method load=1mL wash=1mL elute=1mL flow=6mL/min drain=6mL/min`.' % self.instrument_id))
        ctx.call(cap, {arg: steps}, 'set the SPE method (%s)' % ', '.join('%s %s' % (s['phase'], units.fmt(s['volume_m3'], 'volume')) for s in steps))
        return {'text': 'ok: SPE method of %s set: %s\n' % (self.instrument_id, '; '.join(
            '%s %s from %s at %s x%d' % (s['phase'], units.fmt(s['volume_m3'], 'volume'), s['solvent_position'], units.fmt(s['flow_m3_s'], 'flow'),
                                        s['repetitions']) for s in steps)), 'json': {'steps': steps}}

    # ------------------------------------------------------------------ prime
    def h_prime(self, ctx, p):
        m = self.vmap('prime')
        if m.get('mode') == 'flush':
            return self._prime_flush(ctx, p, m)
        inlet = p.get('inlet') or m.get('default_inlet')
        inlets = self.arg_enum(m['cap'], m['inlet_arg'])
        if inlet is None:
            ctx.fail(invalid('MISSING_OPTION', 'prime on %s needs --inlet (%s).' % (self.instrument_id, '|'.join(inlets)), 'e.g. --inlet S1.'))
        if inlet not in inlets:
            ctx.fail(invalid('UNKNOWN_INLET', '%s has no inlet %r.' % (self.instrument_id, inlet), 'inlets: %s.' % ', '.join(inlets)))
        vol = p.get('volume')
        if vol is None:
            vol = float(m['default_volume_m3'])
        value = self.to_arg(ctx, m['cap'], m['volume_arg'], vol, 'volume', 'prime volume')
        solvent = p.get('solvent')
        lib = [e for e in ((ctx.v(m['library_slot']) or {}).get('solvents') or []) if isinstance(e, dict)] if m.get('library_slot') else []
        known = ['%s (%s)' % (e.get('id'), e.get('name')) for e in lib]
        if solvent is not None and lib:
            hit = [e.get('id') for e in lib if solvent in (e.get('id'), e.get('name')) or str(e.get('name', '')).lower() == solvent.lower()]
            if not hit:
                ctx.fail(invalid('UNKNOWN_SOLVENT', '%s has no library solvent %r.' % (self.instrument_id, solvent), 'solvents: %s.' % ', '.join(known)))
            solvent = hit[0]
        state = (ctx.v(m['inlets_slot']) or {}).get(inlet) or {}
        self.need(ctx, 'prime')
        if solvent is None:
            ctx.check(state.get('assigned') is not None, 'NO_SOLVENT',
                      'inlet %s of %s has no solvent assigned, so it cannot be primed.' % (inlet, self.instrument_id),
                      'assign a solvent to inlet %s first: add --solvent ID (e.g. `chemlab run %s prime --inlet %s --solvent %s`)%s.' % (
                          inlet, self.instrument_id, inlet, lib[0].get('id') if lib else 'ID', '; library solvents: %s' % ', '.join(known) if known else ''),
                      label='solvent assigned to %s' % inlet)
        elif state.get('assigned') != solvent:
            ctx.call(m['assign_cap'], {m['assign_arg']: {inlet: solvent}}, 'assign solvent %s to inlet %s' % (solvent, inlet))
        r = ctx.call(m['cap'], {m['inlet_arg']: inlet, m['volume_arg']: value}, 'prime inlet %s with %s' % (inlet, units.fmt(vol, 'volume')))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        after = (ctx.v(m['inlets_slot']) or {}).get(inlet) or {}
        full = after.get('line') is not None and after.get('line') == after.get('assigned')
        return {'text': 'ok: inlet %s of %s primed with %s (%s)\n' % (
            inlet, self.instrument_id, units.fmt(vol, 'volume'), 'the line now holds %s' % after.get('line') if full else
            'not yet full of %s: prime again (about 2 x 15 mL changes a line)' % after.get('assigned')),
                'json': {'inlet': inlet, 'volume_si': vol, 'line': after.get('line'), 'assigned': after.get('assigned'),
                         'result': (r or {}).get('result')}}

    def _prime_flush(self, ctx, p, m):
        for bad in ('inlet', 'volume', 'solvent'):
            if p.get(bad) is not None:
                ctx.fail(invalid('NOT_FOR_THIS', '--%s does not apply to %s (it flushes all active cell positions).' % (bad, self.instrument_id),
                                 'use --time T for the solvent flush time.'))
        self.need(ctx, 'prime')
        if p.get('time') is not None:
            t = self.to_arg(ctx, m['config_cap'], m['time_arg'], p.get('time'), 'duration', 'flush time')
            ctx.call(m['config_cap'], {m['time_arg']: t}, 'set the solvent flush time to %s' % units.fmt_s(p.get('time')))
        r = ctx.call(m['cap'], {}, 'start the flush')
        job = self.make_job(ctx, m, r, m['cap'], 'flush')
        if job is None:
            return {'text': 'ok: %s flushing\n' % self.instrument_id, 'json': {}}
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s\n' % (job.handle, job.description, job.handle, job.handle),
                'json': {'job': job.handle, 'kind': job.kind}}

    # ------------------------------------------------------------------ vent
    def h_vent(self, ctx, p):
        m = self.vmap('vent')
        self.need(ctx, 'vent')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'release the pressure (%s)' % m['cap'])
        job = self.make_job(ctx, m, r, m['cap'], 'vent')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        if job is None:
            return {'text': 'ok: %s venting\n' % self.instrument_id, 'json': {}}
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s\n' % (job.handle, job.description, job.handle, job.handle),
                'json': {'job': job.handle, 'kind': job.kind}}


_ = (finite, short, fmtq)

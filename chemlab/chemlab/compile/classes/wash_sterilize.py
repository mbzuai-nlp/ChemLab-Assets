"""Washing and sterilization class ("wash_sterilize"): a laboratory glassware washer-disinfector and a steam autoclave -
programme-based devices.

Verbs: program NAME, configure KEY=VALUE ..., load ID [--kind --mass ...], unload ID, start [--wait] (job-N), stop,
pause on|off, open / close door|lid, status.  power, ack, estop come from the shared layer.  Members and mappings:
configs/adapters/wash_sterilize.json.

configure keys (class dictionary, with units): temp=134C (sterilization / wash temperature), time=15min (hold time), end_temp=80C
(liquids: the temperature below which the chamber may be opened), exhaust=fast|slow_liquids|slow_waste|cooling, drying=on|off.
A member maps the keys it has; keys outside the dictionary pass through by native argument name.

Class checks before anything is sent: power on; the door / lid closed before a start and the door / lid lock respected (a running
programme or a chamber under pressure / above its end temperature keeps it locked - EN 285 / ISO 17665 practice); a programme
selected that may be edited before configure; loads only with the lid open; pause / stop only while a programme runs.

Vendor-neutral terms: ISO 15883 (washer-disinfectors: programme, A0, drying), EN 285 / ISO 17665 (steam sterilization: cycle,
sterilization temperature and hold time, exhaust, end temperature), ISA-88 programme states (running, held, aborted, complete).
"""
import re

from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused, unsupported
from ..jobs import OperationJob, StateJob
from ..verbs import Arg, Opt, Verb

REF = ('Miele PLW 6111 operating manual: programme selection, start / stop, interruption, drying, door lock; Tuttnauer 3870ELV-D '
       'operating manual: program selection and editing, start / stop, loads, lid and pressure lock')
ON_OFF = ['on', 'off']
KEYS = {'temp': ('temperature', 'sterilization / wash temperature, e.g. 134C'), 'time': ('duration', 'hold time, e.g. 15min'),
        'end_temp': ('temperature', 'end temperature (liquids), e.g. 80C'), 'exhaust': (None, 'exhaust mode'), 'drying': ('onoff', 'drying on|off')}


class WashSterilizeAdapter(ClassAdapter):
    CLASS = 'wash_sterilize'
    TITLE = 'washing and sterilization cycles'
    ABOUT = ('Programme-based cleaning and sterilization: select a programme, change its parameters where the device allows, add loads, '
             'start it (job-N), pause / resume or stop it, and open / close the door or lid - with the door / lid interlocks checked.')
    STANDARDS = 'ISO 15883 (washer-disinfectors), EN 285 / ISO 17665 (steam sterilization), ISA-88 programme states'
    ALWAYS = ('status',)
    UNIT_KINDS = ('temperature', 'duration', 'mass', 'volume')
    PATH_GROUPS = (('start', 'stop'),)
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'ACCESS_DENIED': ('NOT_YOURS', 'what is running on {inst} was started by someone else; only they can stop, interrupt or change it.',
                          'wait until it has ended (`chemlab run {inst} status`), or ask whoever started it.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab (room and utilities not declared).', 'ask the lab operator.'),
        'CYCLE_RUNNING': ('BUSY', 'a cycle is running on {inst}.', 'wait for it (`chemlab run {inst} status`) or stop it: `chemlab run {inst} stop`.'),
        'BUSY': ('BUSY', '{inst} is running a programme.', 'wait for it (`chemlab run {inst} status`) or stop it: `chemlab run {inst} stop`.'),
        'LID_OPEN': ('LID_OPEN', 'the lid of {inst} is not closed.', 'close it: `chemlab run {inst} close lid`.'),
        'LID_LOCKED': ('LID_LOCKED', 'the pressure lock of {inst} is engaged (a cycle runs, or the chamber is under pressure or above its end temperature).',
                       'wait until the cycle has ended and the chamber has cooled (`chemlab run {inst} status`).'),
        'LID_CLOSED': ('LID_CLOSED', 'the lid of {inst} is closed.', 'open it first: `chemlab run {inst} open lid`.'),
        'NO_CYCLE': ('NO_CYCLE', 'no cycle is running on {inst}.', 'nothing to stop.'),
        'NO_WATER_SUPPLY': ('NO_WATER', '{inst} has no purified water supply.', 'ask the lab operator to fill the water reservoir.'),
        'NO_COOLING_UTILITIES': ('NO_COOLING', '{inst} has no cooling water / compressed air, so cooling programmes are not available.', 'choose another exhaust or ask the lab operator.'),
        'SERVICE_REQUIRED': ('SERVICE_REQUIRED', '{inst} shows a "call service" message.', 'it cannot start a cycle; ask the lab operator / service.'),
        'TIME_OUT_OF_RANGE': ('OUT_OF_RANGE', 'that sterilization time is too long for this programme on {inst} (max 99 min unless extended time).', 'give a shorter time or select extended_time.'),
        'OVERLOAD': ('OVERLOAD', 'the load would exceed the maximum of {inst} (liquids 20 L, solids / waste 20 kg).', 'load less.'),
        'LOAD_EXISTS': ('LOAD_EXISTS', 'a load with that id is already in {inst}.', 'use another id, or `unload` it first.'),
        'CONTAINER_OVERFILLED': ('OVERFILLED', 'the liquid does not fit the container volume given.', 'check --mass and --volume.'),
        'UNKNOWN_LIQUID': ('UNKNOWN_LIQUID', '{inst} does not know that liquid.', 'see `chemlab help {inst} load.add`.'),
        'MISSING_SETTING': ('MISSING_SETTING', 'a liquid load needs the liquid and the container volume (and a biological indicator both organism and log10 N0).',
                            'add --liquid and --volume.'),
    }

    # ------------------------------------------------------------------ helpers
    def check_mapping(self, lab):
        visible = {s.lower() for s in lab.view.slots}
        out = []
        for pr in ClassAdapter.check_mapping(self, lab):
            m = re.search(r'no visible state slot (\S+)$', pr)
            if m and '#' in m.group(1) and m.group(1).split('#', 1)[0].lower() in visible:
                continue
            out.append(pr)
        return out

    def sv(self, values, sid):
        if sid is None:
            return None
        get = values.v if hasattr(values, 'v') else values.get
        base, _, key = sid.partition('#')
        v = get(base)
        return v.get(key) if key and isinstance(v, dict) else v

    def summary(self, values):
        lines, js = [], {}
        p = self.slot('power')
        if p:
            js['power'] = bool(self.sv(values, p))
            if not js['power']:
                return ['power off'], js
        for role, label in (('program', 'programme'), ('state', 'state'), ('phase', 'phase'), ('remaining', 'remaining'), ('door', 'door / lid closed'),
                            ('locked', 'door / lid locked'), ('temp', 'temperature'), ('pressure', 'chamber pressure'), ('loads', 'loads')):
            sid = self.slot(role)
            if not sid:
                continue
            v = self.sv(values, sid)
            js[role] = v
            if role == 'remaining':
                txt = units.fmt_s(v) if finite(v) else 'n/a'
            elif role == 'temp':
                txt = units.fmt(v, 'temperature') if finite(v) else 'n/a'
            elif role == 'pressure':
                txt = units.fmt(v, 'pressure', 3) if finite(v) else 'n/a'
            elif role == 'loads':
                txt = ', '.join(str(x.get('load_id', x)) if isinstance(x, dict) else str(x) for x in (v or [])) or 'none'
            elif isinstance(v, bool):
                txt = 'yes' if v else 'no'
            else:
                txt = short(v, 120)
            lines.append('%s: %s' % (label, txt))
        return (lines or ['(see `chemlab read %s`)' % self.instrument_id]), js

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        part = [Arg('PART', 'door or lid (optional: it has one)', required=False)]
        return {
            'program': Verb('program', 'select the programme / program by name', self.h_program, positionals=[Arg('NAME', 'programme name (see help)')],
                            preconditions=['power on', 'no programme running'], errors=['UNKNOWN_PROGRAM (usage)', 'BUSY (refused)'],
                            example='chemlab run INST program universal', reference=REF),
            'configure': Verb('configure', 'change parameters of the selected programme: temp=, time=, end_temp=, exhaust=, drying=', self.h_configure,
                              variadic=('KEY=VALUE', 'a parameter with its unit'),
                              preconditions=['power on', 'no programme running', 'the selected programme may be edited'],
                              errors=['UNKNOWN_SETTING, OUT_OF_RANGE (usage)', 'NOT_EDITABLE, BUSY (refused)'],
                              example='chemlab run INST configure temp=121C time=20min', reference=REF),
            'load': Verb('load', 'put a load into the chamber (lid open)', self.h_load, positionals=[Arg('ID', 'a name for the load, e.g. media_1')],
                         options={'kind': Opt('liquid | solid | waste', metavar='KIND', required=True),
                                  'mass': Opt('mass of the load, e.g. 700g', kind='mass', metavar='MASS', required=True),
                                  'liquid': Opt('liquid loads: the liquid, e.g. water', metavar='NAME'),
                                  'volume': Opt('liquid loads: container volume, e.g. 1L', kind='volume', metavar='VOL'),
                                  'material': Opt('container / item material, e.g. borosilicate_glass', metavar='NAME', required=True),
                                  'closure': Opt('loose | sealed | none', metavar='CLOSURE'),
                                  'organism': Opt('biological indicator organism', metavar='NAME'),
                                  'log10': Opt('biological indicator initial log10 count', metavar='N')},
                         preconditions=['lid open', 'liquid loads: liquid and container volume given'], errors=['LID_CLOSED, OVERLOAD, LOAD_EXISTS (refused)'],
                         example='chemlab run INST load media_1 --kind liquid --mass 700g --liquid water --volume 1L --material borosilicate_glass --closure loose',
                         reference=REF),
            'unload': Verb('unload', 'take a load out of the chamber (lid open)', self.h_unload, positionals=[Arg('ID', 'the load name')],
                           preconditions=['lid open', 'the load is in the chamber'], errors=['UNKNOWN_LOAD (usage)', 'LID_CLOSED (refused)'],
                           example='chemlab run INST unload media_1', reference=REF),
            'start': Verb('start', 'start the selected programme (job-N; --wait blocks until it has ended)', self.h_start, long_action=True,
                          options={'wait': Opt('wait until the programme has ended', kind='flag'),
                                   'max': Opt('with --wait: the longest wait (default 3 h)', kind='duration', metavar='TIME')},
                          preconditions=['power on', 'door / lid closed', 'no programme running', 'baskets pushed in (washer)'],
                          errors=['DOOR_OPEN, LID_OPEN, BUSY (refused)', 'NOT_COMPLETED (refused: --wait ended badly)'],
                          example='chemlab run INST start --wait', reference=REF),
            'stop': Verb('stop', 'stop (cancel) the running programme', self.h_stop, preconditions=['a programme running'], errors=['NO_CYCLE (refused)'],
                         example='chemlab run INST stop', reference=REF),
            'pause': Verb('pause', 'interrupt (on) or resume (off) the running programme', self.h_pause, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                          preconditions=['on: a programme running; off: a programme interrupted'], errors=['NOT_RUNNING, NOT_PAUSED (refused)'],
                          example='chemlab run INST pause on', reference=REF),
            'open': Verb('open', 'open the door / lid (the lock is checked first)', self.h_part('open'), positionals=part,
                         preconditions=['no programme running', 'door / lid not locked (pressure, temperature)'], errors=['DOOR_LOCKED, LID_LOCKED (refused)'],
                         example='chemlab run INST open', reference=REF),
            'close': Verb('close', 'close the door / lid', self.h_part('close'), positionals=part, preconditions=['baskets pushed in (washer)'],
                          example='chemlab run INST close', reference=REF),
        }

    # ------------------------------------------------------------------ program / configure
    def h_program(self, ctx, p):
        m = self.vmap('program')
        name = p.raw['NAME'].strip()
        enum = self.arg_enum(m['cap'], m['arg'])
        if enum and name not in enum:
            close = [e for e in enum if name.lower() in e.lower()][:4]
            ctx.fail(invalid('UNKNOWN_PROGRAM', '%s has no programme %r.' % (self.instrument_id, name),
                             ('did you mean %s? ' % ' or '.join(close) if close else '') + 'programmes: %s.' % ', '.join(enum)))
        self.requirements(ctx, 'program')
        cur = self.sv(ctx, self.slot('program'))
        if cur == name:
            return {'text': 'ok: %s programme %s is already selected\n' % (self.instrument_id, name), 'json': {'program': name, 'changed': False}}
        ctx.call(m['cap'], {m['arg']: name}, 'select programme %s' % name)
        return {'text': 'ok: %s programme %s selected%s\n' % (self.instrument_id, name, self.describe_program(ctx)), 'json': {'program': name, 'changed': True}}

    def describe_program(self, ctx):
        d = self.sv(ctx, self.slot('program_params')) if self.slot('program_params') else None
        if not isinstance(d, dict):
            return ''
        bits = []
        if finite(d.get('temperature_k')):
            bits.append(units.fmt(d['temperature_k'], 'temperature'))
        if finite(d.get('time_s')):
            bits.append(units.fmt_s(d['time_s']))
        if d.get('exhaust'):
            bits.append('exhaust %s' % d['exhaust'])
        if finite(d.get('end_temperature_k')):
            bits.append('end %s' % units.fmt(d['end_temperature_k'], 'temperature'))
        return (' (%s)' % ', '.join(bits)) if bits else ''

    def h_configure(self, ctx, p):
        m = self.vmap('configure')
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'configure needs at least one KEY=VALUE.', 'see `chemlab help %s configure`.' % self.instrument_id))
        keys = m.get('keys') or {}
        calls = {}
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'see `chemlab help %s configure`.' % self.instrument_id))
            if k not in keys:
                ok = sorted(keys)
                reason = ((m.get('not_here') or {}).get(k) or '').replace('INST', self.instrument_id)
                ctx.fail(invalid('UNKNOWN_SETTING', '%s cannot set %r%s.' % (self.instrument_id, k, (': ' + reason) if reason else ''),
                                 'settings on %s: %s.' % (self.instrument_id, ', '.join(ok))))
            spec = keys[k]
            cap, arg = spec.get('cap', m.get('cap')), spec['arg']
            kind = KEYS.get(k, (None,))[0]
            sch = self.cap_schema(cap, arg)
            if kind == 'onoff':
                if v.lower() not in ON_OFF:
                    ctx.fail(invalid('BAD_VALUE', '%s=%s: write on or off.' % (k, v), 'e.g. %s=on.' % k))
                val = v.lower() == 'on'
            elif kind in ('temperature', 'duration'):
                si = units.parse(v, kind, what=k)
                lo, hi = self.arg_range_si(cap, arg)
                fmt = (lambda x: units.fmt(x, 'temperature')) if kind == 'temperature' else units.fmt_s
                if (lo is not None and si < lo - 1e-9) or (hi is not None and si > hi + 1e-9):
                    ctx.fail(invalid('OUT_OF_RANGE', '%s=%s is outside %s..%s on %s.' % (k, v, fmt(lo), fmt(hi), self.instrument_id),
                                     'give %s from %s to %s.' % (k, fmt(lo), fmt(hi))))
                val = round(self.arg_value(cap, arg, si), 6)
            else:
                enum = sch.get('enum') or []
                if enum and v not in enum:
                    ctx.fail(invalid('BAD_VALUE', '%s=%s is not allowed on %s.' % (k, v, self.instrument_id), 'use one of: %s.' % ', '.join(map(str, enum))))
                val = v
            calls.setdefault(cap, {})[arg] = val
        self.requirements(ctx, 'configure')
        prog_arg = m.get('program_arg')
        if prog_arg:
            cur = self.sv(ctx, self.slot('program'))
            editable = self.arg_enum(m['cap'], prog_arg)
            ctx.check(cur in editable, 'NOT_EDITABLE', 'the selected programme of %s (%s) cannot be edited.' % (self.instrument_id, cur),
                      'select an editable one first (`chemlab run %s program %s`), then configure.' % (self.instrument_id, editable[0] if editable else '...'),
                      label='programme editable')
        for cap, args in calls.items():
            a = dict(args)
            if prog_arg and cap == m['cap']:
                a[prog_arg] = self.sv(ctx, self.slot('program'))
            ctx.call(cap, a, 'set %s' % ', '.join('%s=%s' % kv for kv in args.items()))
        return {'text': 'ok: %s programme parameters set%s\n' % (self.instrument_id, self.describe_program(ctx)), 'json': {'settings': calls}}

    # ------------------------------------------------------------------ loads
    def h_load(self, ctx, p):
        m = self.vmap('load')
        kind = p.get('kind')
        kinds = self.arg_enum(m['cap'], 'kind')
        if kinds and kind not in kinds:
            ctx.fail(invalid('BAD_VALUE', '--kind %s is not one of %s.' % (kind, ', '.join(kinds)), 'e.g. --kind liquid.'))
        if kind == 'liquid' and (p.get('liquid') is None or p.get('volume') is None):
            ctx.fail(invalid('MISSING_SETTING', 'a liquid load needs --liquid and --volume (the container volume).', 'e.g. --liquid water --volume 1L.'))
        mass = p.get('mass')
        lo, hi = self.arg_range_si(m['cap'], 'mass_kg')
        if (lo is not None and mass < lo) or (hi is not None and mass > hi):
            ctx.fail(invalid('OUT_OF_RANGE', '--mass %s is outside %s..%s.' % (units.fmt(mass, 'mass'), units.fmt(lo, 'mass'), units.fmt(hi, 'mass')), 'give a smaller or larger mass.'))
        self.requirements(ctx, 'load')
        args = {'load_id': p.pos[0], 'kind': kind, 'mass_kg': round(self.arg_value(m['cap'], 'mass_kg', mass), 9), 'material': p.get('material')}
        if p.get('liquid') is not None:
            args['liquid'] = p.get('liquid')
        if p.get('volume') is not None:
            args['container_volume_m3'] = round(self.arg_value(m['cap'], 'container_volume_m3', p.get('volume')), 12)
        if p.get('closure') is not None:
            args['closure'] = p.get('closure')
        if p.get('organism') is not None:
            args['organism'] = p.get('organism')
        if p.get('log10') is not None:
            try:
                args['log10_n0'] = float(p.get('log10'))
            except ValueError:
                ctx.fail(invalid('BAD_VALUE', '--log10 takes a number.', 'e.g. --log10 6.'))
        ctx.call(m['cap'], args, 'load %s' % p.pos[0])
        return {'text': 'ok: %s loaded %s (%s, %s)\n' % (self.instrument_id, p.pos[0], kind, units.fmt(mass, 'mass')), 'json': {'load': args}}

    def h_unload(self, ctx, p):
        m = self.vmap('unload')
        loads = self.sv(ctx, self.slot('loads')) or []
        ids = [x.get('load_id') if isinstance(x, dict) else x for x in loads]
        if p.pos[0] not in ids:
            ctx.fail(invalid('UNKNOWN_LOAD', '%s has no load %r.' % (self.instrument_id, p.pos[0]), 'loads: %s.' % (', '.join(map(str, ids)) or 'none')))
        self.requirements(ctx, 'unload')
        ctx.call(m['cap'], {m['arg']: p.pos[0]}, 'unload %s' % p.pos[0])
        return {'text': 'ok: %s unloaded %s\n' % (self.instrument_id, p.pos[0]), 'json': {'unloaded': p.pos[0]}}

    # ------------------------------------------------------------------ start / stop / pause
    def h_start(self, ctx, p):
        m = self.vmap('start')
        self.requirements(ctx, 'start')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'start programme %s' % (self.sv(ctx, self.slot('program')) or ''))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        prog = self.sv(ctx, self.slot('program'))
        desc = 'programme %s on %s' % (prog, self.instrument_id)
        st = m.get('state')
        stop_spec = self.vmap('stop')

        def stop(c):
            c.call(stop_spec['cap'], dict(stop_spec.get('args') or {}), 'stop the programme')
            return 'stopped with %s' % stop_spec['cap']
        if r.get('operation_id') and m.get('job', 'operation') == 'operation':
            job = OperationJob(ctx.core, self, ctx.req, desc, m['cap'], r['operation_id'])
        else:
            ad = self
            final = {}

            def state(c):
                if final:
                    return final['r']
                v = ad.sv(c, st['slot'])
                rem = ad.sv(c, ad.slot('remaining')) if ad.slot('remaining') else None
                prog_txt = '%s = %s%s' % (st['slot'], v, (', about %s left' % units.fmt_s(rem)) if finite(rem) else '')
                js = {'state': v, 'remaining_s': rem}
                if v in st['done']:
                    final['r'] = ('succeeded', prog_txt, js)
                elif v in st['failed']:
                    final['r'] = ('failed', prog_txt, js)
                return final.get('r', ('paused' if v in st.get('paused', []) else 'running', prog_txt, js))
            job = StateJob(ctx.core, self, ctx.req, desc, state, stop=stop, max_s=float(m.get('max_s', 14400)), poll_s=5.0)
        if p.get('wait'):
            w = job.wait_reply(ctx.core, ctx.req, p.get('max') or 10800.0)
            ctx.refresh()
            if w['json'].get('status') != 'succeeded':
                ctx.fail(refused('NOT_COMPLETED', 'the programme on %s ended as %s.' % (self.instrument_id, w['json'].get('status')),
                                 'see `chemlab run %s status`.' % self.instrument_id, detail={'job': job.handle}))
            return {'text': 'ok: %s finished (%s)\n%s' % (desc, job.handle, w['text']), 'json': dict(w['json'], job=job.handle)}
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, desc, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'program': prog}}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        self.requirements(ctx, 'stop')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop the programme')
        return {'text': 'ok: %s programme stopped (%s)\n' % (self.instrument_id, short(self.sv(ctx, self.slot('state')), 80)), 'json': {'stopped': True}}

    def h_pause(self, ctx, p):
        m = self.vmap('pause')
        want = p.pos[0] == 'on'
        self.requirements(ctx, 'pause:' + ('on' if want else 'off'))
        cap = m['on_cap'] if want else m['off_cap']
        ctx.call(cap, {}, 'interrupt the programme' if want else 'resume the programme')
        return {'text': 'ok: %s programme %s (%s)\n' % (self.instrument_id, 'interrupted' if want else 'resumed', short(self.sv(ctx, self.slot('state')), 60)),
                'json': {'paused': want}}

    # ------------------------------------------------------------------ door / lid
    def h_part(self, action):
        def h(ctx, p):
            parts = (self.vmap('open') or {}).get('parts') or {}
            name = p.pos[0] if p.pos else (next(iter(parts)) if len(parts) == 1 else None)
            if name not in parts:
                ctx.fail(invalid('UNKNOWN_PART', '%s has no part %r to %s.' % (self.instrument_id, name, action), 'parts: %s.' % ', '.join(parts)))
            spec = parts[name]
            want = action == 'open'

            def is_closed():
                if spec.get('closed_slot'):
                    return bool(self.sv(ctx, spec['closed_slot']))
                a = self.sv(ctx, spec['angle_slot'])
                return finite(a) and a <= 1e-6

            def is_open():
                if spec.get('angle_slot') and spec.get('open_angle') is not None:
                    a = self.sv(ctx, spec['angle_slot'])
                    return finite(a) and a >= float(spec['open_angle']) - 1e-6
                return not is_closed()
            if (not want and is_closed()) or (want and is_open()):
                return {'text': 'ok: %s %s is already %s\n' % (self.instrument_id, name, 'open' if want else 'closed'), 'json': {'part': name, 'changed': False}}
            if want:
                err = self.cooling_lock(ctx)                       # say why the lock is still on after the cycle, with the temperatures
                if err is not None:
                    ctx.fail(err)
            self.requirements(ctx, '%s:%s' % (action, name))
            if spec.get('%s_cap' % action):
                ctx.call(spec['%s_cap' % action], {}, '%s the %s' % (action, name))
            else:
                ctx.call(spec['cap'], {spec['arg']: spec[action]}, '%s the %s' % (action, name))
            waited = ''
            if spec.get('settle_s') and not ctx.dry_run:
                done = is_open if want else is_closed
                if not done():
                    w = ctx.advance(float(spec['settle_s']), until=lambda v: done(), step='wait until the %s is %s' % (name, 'open' if want else 'closed'), poll_s=0.5)
                    waited = ' (after %s)' % units.fmt_s(w)
            return {'text': 'ok: %s %s %s%s\n' % (self.instrument_id, name, 'opened' if want else 'closed', waited), 'json': {'part': name, 'action': action}}
        return h

    def cooling_lock(self, ctx):
        """After a cycle the lid / door stays locked until the chamber has cooled to the programme's end temperature (tuttnauer 1.2.1,
        manual p.42): a LID_LOCKED refusal that says so, with both temperatures - or None when that is not the reason."""
        locked = self.slot('locked')
        if not (locked and self.sv(ctx, locked) and self.slot('temp') and self.slot('program_params')):
            return None
        t1 = self.sv(ctx, self.slot('temp'))
        t2 = self.sv(ctx, self.slot('temp2')) if self.slot('temp2') else None   # load probe with a liquid load (both must cool)
        t = max([x for x in (t1, t2) if finite(x)] or [None]) if finite(t1) or finite(t2) else None
        end = (self.sv(ctx, self.slot('program_params')) or {}).get('end_temperature_k')
        st = self.sv(ctx, self.slot('state').split('#', 1)[0]) if self.slot('state') else None
        ended = isinstance(st, dict) and (st.get('result') is not None or st.get('stage') in (None, 'idle', 'ready'))
        if not (finite(t) and finite(end)) or not ended or t <= end:      # still in the cycle (exhaust ...): the generic lock text
            return None
        inst = self.instrument_id
        now = ('%.1f °C chamber, %.1f °C load probe' % (t1 - 273.15, t2 - 273.15)) if finite(t1) and finite(t2) else '%.1f °C' % (t - 273.15)
        return refused('LID_LOCKED', 'the lid of %s stays locked until the chamber (and a liquid load) has cooled to the end temperature (now %s; end %.1f °C).'
                       % (inst, now, end - 273.15),
                       'wait for it to cool (`chemlab wait 600`; `chemlab run %s status` shows the temperature), then open the lid again.' % inst)

    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code == 'LID_LOCKED':
            ctx.refresh()
            err = self.cooling_lock(ctx)
            if err is not None:
                return err
        if code == 'PRECONDITION_FAILED':
            ctx.refresh()
            pw = self.slot('power')
            if pw and not self.sv(ctx, pw):
                return refused('POWER_OFF', '%s is switched off (it answered %s).' % (self.instrument_id, code), 'switch it on: `chemlab run %s power on`.' % self.instrument_id)
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)


_ = unsupported

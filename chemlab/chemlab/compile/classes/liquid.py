"""Liquid-handling class ("liquid"): the Opentrons Flex pilot gate (chemlab/flex_verbs.py), moved into the compile framework.

Verb names follow the Opentrons Python API (InstrumentContext.pick_up_tip / drop_tip / return_tip / aspirate / dispense / transfer
/ move_to / home) and the HTTP API for runs (/runs, /runs/{id}/actions); PyLabRobot LiquidHandler uses the same set.  What moved
to the framework: argument and unit parsing (verbs.py, units.py), the request context with native calls, dry-run, read-back and
steps done (ctx.py), the error format (errors.py), job ids (jobs.py: protocol runs are run-type jobs), named deck locations
(deck.py: LABWARE:WELL, lists, ranges).  What stays here: the liquid verbs, the tip ledger, busy checks against runs, protocol
runs and the maintenance verbs.  What is the Flex's: its mapping in configs/adapters/liquid.json (native capability and argument
names, fixture slots, settings values, unsupported verbs with reasons) and its deck in the lab config (chemlab/compile/deck.py);
the trusted host side is chemlab/compile/scenes/flex.py.

Manual references (references/manuals/opentrons_flex_single_5_1000ul/, internal): Python API reference, Instruments
(InstrumentContext) and Labware; Complex commands (transfer); Flex manual: deck fixtures, protocol run and error recovery,
robot settings; HTTP API reference (/runs).
"""
import copy
import math
import re

from ..base import ClassAdapter
from ..deck import Deck, Loc, build_registry
from ..errors import invalid, refused, unexpected
from ..jobs import Job
from ..units import fmt_s
from ..verbs import Arg, Opt, Verb

ACTIVE = ('setup', 'running', 'paused', 'awaiting_recovery')
JOB_STATUS = {'setup': 'setup', 'running': 'running', 'paused': 'paused', 'awaiting_recovery': 'awaiting_recovery',
              'succeeded': 'completed', 'failed': 'failed', 'stopped': 'canceled'}
TRASH_FIXTURES = ('trash_bin', 'waste_chute', 'waste_chute_staging')
ON_OFF = ['on', 'off']
REF = 'Opentrons Python API InstrumentContext and complex commands; Flex manual: deck fixtures, protocol runs; HTTP API /runs'


def _same(a, b):
    return all(abs(a[k] - b[k]) <= 1e-9 for k in ('x_m', 'y_m', 'z_m'))


def _nl(m3):
    return int(round(m3 * 1e12))


def _m3(nl):
    return float('%de-12' % nl)


def fmt_ul(m3):
    v = round(m3 * 1e9, 3)
    return '%s µL' % ('%.3f' % v).rstrip('0').rstrip('.')


def ul(m3):
    return round(m3 * 1e9, 3)


class RunJob(Job):
    """A protocol run started by `run-protocol` (a run-type job read from run.state / run.history)."""
    kind = 'run'

    def __init__(self, core, adapter, req, protocol_id, name, started_s, params):
        super().__init__(core, adapter, req, 'protocol %s on %s' % (protocol_id, req.instrument))
        self.protocol_id, self.name, self.run_started_s, self.params = protocol_id, name, started_s, params
        self.paused_s, self.pause_t = 0.0, None

    def poll(self, ctx):
        s = self.adapter.job_state(ctx, self)
        st = s['status']
        self.state = {'completed': 'succeeded', 'canceled': 'cancelled'}.get(st, st)
        return self.state, self.adapter.job_line(ctx, self, s), s

    def cancel(self, ctx):
        s = self.adapter.job_state(ctx, self)
        if s['status'] not in ACTIVE:
            raise refused('JOB_FINISHED', '%s is %s.' % (self.handle, s['status']), 'nothing to cancel.')
        ctx.call(self.adapter.c('cancel'), {}, 'cancel %s' % self.handle)
        self.state = 'cancelled'
        return 'run stopped'

    def max_wait_s(self):
        return 3600.0


class LiquidAdapter(ClassAdapter):
    CLASS = 'liquid'
    TITLE = 'liquid handling'
    ABOUT = ('Move liquid between named wells: tips, aspirate, dispense, transfer (splits volumes over the tip size), protocol runs '
             'as jobs, deck and robot maintenance.  Volumes in µL / mL / nL; locations LABWARE:WELL from the deck registry.')
    STANDARDS = 'Opentrons Python API (InstrumentContext) and HTTP API (/runs); PyLabRobot LiquidHandler; SiLA 2 has no liquid-handling feature'
    ALWAYS = ('status',)
    UNIT_KINDS = ('volume',)

    def __init__(self, instrument_id, config_entry):
        super().__init__(instrument_id, config_entry)
        self.deck = None
        self.tip_home = {}
        self.tip_ledger = {'tip_id': None, 'volume_m3': 0.0}             # commanded contents of the attached tip
        self.returns, self.own_events = {}, set()
        self.vmin = self.vmax = None

    # ------------------------------------------------------------------ binding: deck registry and pipette range
    def bind(self, core):
        first = self.lab is None
        lab = ClassAdapter.bind(self, core)
        if first:
            deck = self.config_entry.get('deck')
            if deck:
                self.deck = Deck(build_registry(deck, lab.contract), self.instrument_id)
                for lw in self.deck.reg['labware']:
                    if lw['kind'] == 'tiprack':
                        for w in lw['wells'].values():
                            if w['tip_id']:
                                self.tip_home[w['tip_id']] = (lw['name'], w['well'])
            m = self.vmap('aspirate')
            self.vmin, self.vmax = self.arg_range(m['cap'], m['volume_arg'])
        return lab

    def c(self, verb):
        return self.vmap(verb)['cap']

    @property
    def reg(self):
        return self.deck.reg if self.deck else None

    def need_deck(self, ctx):
        if self.deck is None:
            ctx.fail(refused('NO_DECK', 'no deck is registered for %s in this lab (no labware, tips or trash known).' % self.instrument_id,
                             'a lab or task config gives it a "deck"; without one only power, home, lights, protocols and admin verbs work.'))

    def new_shadow(self):
        return {'ledger': copy.deepcopy(self.tip_ledger), 'returns': {}}

    # ------------------------------------------------------------------ tips
    def _returns(self, ctx):
        r = dict(self.returns)
        if ctx.dry_run:
            r.update(ctx.shadow['returns'])
        return r

    def tip_locations(self, ctx):
        loc = {tip: ('rack',) + home for tip, home in self.tip_home.items()}
        returns = self._returns(ctx)
        for i, e in enumerate(ctx.view['tip.events']):
            if e['event'] == 'attach':
                loc[e['tip_id']] = ('pipette',)
            else:
                loc[e['tip_id']] = ('rack',) + returns[i] if i in returns else ('slot', e['slot_id'])
        return loc

    def rack_tips(self, ctx, rack):
        lw = self.deck.labware[rack]
        present = {(l[1], l[2]): tip for tip, l in self.tip_locations(ctx).items() if l[0] == 'rack' and l[1] == rack}
        order = ['%s%d' % ('ABCDEFGH'[r], c + 1) for c in range(lw['cols']) for r in range(lw['rows'])]
        return [(w, present[(rack, w)]) for w in order if (rack, w) in present]

    def tip_racks(self):
        return [lw['name'] for lw in self.reg['labware'] if lw['kind'] == 'tiprack']

    def tip_capacity(self, tip_id):
        rack = self.tip_home.get(tip_id, (None,))[0]
        return self.deck.labware[rack]['tip_capacity_m3'] if rack else None

    def ledger(self, ctx):
        led = ctx.shadow['ledger'] if ctx.dry_run else self.tip_ledger
        attached = ctx.view['tip.attached_id']
        if led['tip_id'] != attached:                                      # the tip changed without a chemlab verb
            led.update(tip_id=attached, volume_m3=None if attached else 0.0)
        return led

    def lost_tip_note(self, ctx):
        events = ctx.view['tip.events']
        if events and events[-1]['event'] == 'release' and (len(events) - 1) not in self.own_events and not self.current_job(ctx):
            e = events[-1]
            return ' The last tip (%s) was released in slot %s at t=%.1f s without a chemlab command - it may have fallen off.' % (
                self.tip_name(e['tip_id']), e['slot_id'], e['time_s'])
        return ''

    def tip_name(self, tip_id):
        home = self.tip_home.get(tip_id)
        return '%s:%s' % home if home else tip_id

    # ------------------------------------------------------------------ dry-run shadow
    def shadow(self, ctx, cid, args):
        v = ctx.view
        if cid == self.c('power'):
            v['power.enabled'] = args[self.vmap('power')['arg']]
        elif cid == self.c('move-to'):
            for k in ('x_m', 'y_m', 'z_m'):
                v['motion.' + k] = args[k]
        elif cid == self.c('home'):
            for k in ('x_m', 'y_m', 'z_m'):
                v['motion.' + k] = self.reg['home_m'][k] if self.reg else v['motion.' + k]
        elif cid == self.c('pick-up-tip'):
            v['tip.attached_id'] = args['tip_id']
            v['tip.events'] = v['tip.events'] + [{'event': 'attach', 'tip_id': args['tip_id'], 'slot_id': None, 'time_s': v['_time_s']}]
        elif cid == self.c('drop-tip'):
            v['tip.events'] = v['tip.events'] + [{'event': 'release', 'tip_id': v['tip.attached_id'], 'slot_id': args['target_slot_id'],
                                                  'time_s': v['_time_s']}]
            v['tip.attached_id'] = None
        elif cid in (self.c('aspirate'), self.c('dispense')):
            v['sample.transfer_count'] += 1
        elif cid == self.c('lights'):
            v['robot.lights_on'] = args[self.vmap('lights')['arg']]

    # ------------------------------------------------------------------ jobs and busy checks
    def my_jobs(self, ctx=None):
        from ..jobs import registry
        return [j for j in registry(self._core).values() if isinstance(j, RunJob) and j.instrument == self.instrument_id] if self._core else []

    _core = None

    def job_for_run(self, rs):
        if rs is None:
            return None
        for j in self.my_jobs():
            if j.protocol_id == rs['protocol_id'] and j.run_started_s is not None and j.run_started_s == rs.get('started_s'):
                return j
        return None

    def current_job(self, ctx):
        rs = ctx.view.get('run.state')
        j = self.job_for_run(rs)
        return j if j and rs['status'] in ACTIVE else None

    def step_s(self, text):
        m = re.match(r'Delaying ([0-9.]+) s', text or '')
        return float(m.group(1)) if m else (self.reg or {}).get('command_s', 1.0)

    def eta(self, rs, now=None):
        steps, idx = rs.get('steps') or [], rs.get('index') or 0
        listed = sum(self.step_s(t) for t in steps[idx:])
        start = rs.get('started_s')
        if start is None or now is None:
            return listed
        job = self.job_for_run(rs)
        paused = 0.0
        if job is not None:
            paused = job.paused_s + ((now - job.pause_t) if job.pause_t is not None else 0.0)
        end = start + sum(self.step_s(t) for t in steps) + paused
        return max(listed, end - now) if rs['status'] in ('running', 'paused') else listed

    def names(self, text):
        """Runtime ids in run step texts (reservoir_A2) -> deck names (reservoir:A2)."""
        if not text or not self.deck:
            return text
        return re.sub(r'\b([A-Za-z0-9.-]+(?:_[A-Za-z0-9.-]+)*)_([A-H]\d{1,2})\b',
                      lambda m: self.deck.name_of_id(m.group(0)) or m.group(0), text)

    def check_not_busy(self, ctx, run_command=False, allow_own_job=False):
        rs = ctx.view.get('run.state')
        if not rs or rs['status'] not in ACTIVE:
            return
        job = self.job_for_run(rs)
        if job is not None and job.caller == ctx.caller:
            if allow_own_job:
                return
            ctx.fail(refused('BUSY_OWN_JOB', 'your %s (protocol %r) is %s; direct commands would interfere with it.' % (job.handle, rs['name'], rs['status']),
                             'wait for it (`chemlab wait %s`), or pause or cancel it first.' % job.handle, detail={'job': job.handle}))
        if rs['status'] == 'setup' and not run_command:
            return
        owner = ('%s' % job.handle) if job else 'another user'
        left = self.eta(rs, ctx.now())
        step = '' if rs['status'] == 'setup' else ', step %d/%d' % (min(rs['index'] + 1, rs['n_steps']), rs['n_steps'])
        ctx.fail(refused('BUSY', 'the %s is busy: protocol run %r started by %s is %s%s, about %s left.' % (
            self.instrument_id, rs['name'], owner, rs['status'].replace('_', ' '), step, fmt_s(left)),
            'wait until it has finished (`chemlab wait 60`, then `chemlab run %s status` shows run: none or completed), then retry. '
            'Do not switch the power off: that would abort their run.' % self.instrument_id, detail={'run': rs['name'], 'status': rs['status'], 'eta_s': left}))

    def check_power(self, ctx):
        if not ctx.view['power.enabled']:
            ctx.fail(refused('POWER_OFF', 'the %s is switched off.' % self.instrument_id, 'run `chemlab run %s power on` first.' % self.instrument_id))
        ctx.checks.append('power on')

    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code == 'ACCESS_DENIED':
            ctx.refresh()
            try:
                self.check_not_busy(ctx, run_command=cap.startswith('run.'))
            except Exception as exc:                                       # noqa: BLE001 - the busy explanation is the answer
                return exc
            return refused('ACCESS_DENIED', 'the instrument refused %s for your identity.' % cap, 'check `chemlab run %s status`; another user may hold it.' % self.instrument_id)
        if code in ('PRECONDITION_FAILED', 'BUSY'):
            return refused('DEVICE_REFUSED', 'the instrument refused %s (%s).' % (cap, code),
                           'check `chemlab run %s status` and the verb help; the state may differ from what you expect.' % self.instrument_id)
        if code == 'INVALID_ARGUMENT':
            return invalid('BACKEND_INVALID_ARGUMENT', 'the instrument rejected the arguments of %s.' % cap, 'check the values against the verb help.')
        return unexpected('BACKEND_%s' % code, 'the instrument call %s failed (%s: %s).' % (cap, code, (response.get('error') or {}).get('message')),
                          'check `chemlab run %s status` before going on; the effect of the call is not known.' % self.instrument_id)

    # ------------------------------------------------------------------ primitives
    def move(self, ctx, loc):
        pos = {k: ctx.view['motion.' + k] for k in ('x_m', 'y_m', 'z_m')}
        if _same(pos, loc.position):
            return False
        ctx.call(self.c('move-to'), dict(loc.position), 'move to %s' % loc.name,
                 readback=lambda v: None if _same({k: v['motion.' + k] for k in ('x_m', 'y_m', 'z_m')}, loc.position) else 'a different position')
        return True

    def need_tip(self, ctx, doing):
        tip = ctx.view['tip.attached_id']
        if tip is None:
            ctx.fail(refused('NO_TIP', 'no tip is attached, so the pipette cannot %s.%s' % (doing, self.lost_tip_note(ctx)),
                             'run `chemlab run %s pick-up-tip` first.' % self.instrument_id))
        return tip

    def pick(self, ctx, tip=None, rack=None):
        if ctx.view['tip.attached_id'] is not None:
            ctx.fail(refused('TIP_ALREADY_ATTACHED', 'a tip (%s) is already attached.' % self.tip_name(ctx.view['tip.attached_id']),
                             'drop it (`drop-tip`) or put it back (`return-tip`) first.'))
        if tip is not None:
            present = dict(self.rack_tips(ctx, tip.labware['name']))
            if tip.well not in present:
                nxt = self.rack_tips(ctx, tip.labware['name'])
                ctx.fail(refused('NO_TIP_AT_LOCATION', 'there is no tip at %s (already used).' % tip.name,
                                 ('the next tip in %s is at %s:%s.' % (tip.labware['name'], tip.labware['name'], nxt[0][0])) if nxt else
                                 '%s is empty; use another rack: %s.' % (tip.labware['name'], ', '.join(self.tip_racks()))))
            tip_id, loc = present[tip.well], tip
        else:
            racks = [rack] if rack else self.tip_racks()
            avail = [(r, t) for r, t in ((r, self.rack_tips(ctx, r)) for r in racks) if t]
            if not avail:
                others = [r for r in self.tip_racks() if r not in racks and self.rack_tips(ctx, r)]
                ctx.fail(refused('RACK_EMPTY' if rack else 'OUT_OF_TIPS', 'no tips left in %s.' % ', '.join(racks),
                                 ('use another rack: --rack %s.' % others[0]) if others else 'ask the operator to refill the tip racks.'))
            r, tips = avail[0]
            well, tip_id = tips[0]
            loc = self.deck.parse('%s:%s' % (r, well), ('tip',))
        self.move(ctx, loc)
        m = self.vmap('pick-up-tip')
        ctx.call(m['cap'], {m['arg']: tip_id}, 'pick up tip %s' % loc.name,
                 readback=lambda v: None if v['tip.attached_id'] == tip_id else 'attached tip %r' % v['tip.attached_id'])
        if not ctx.dry_run:
            self.own_events.add(len(ctx.view['tip.events']) - 1)
        (ctx.shadow['ledger'] if ctx.dry_run else self.tip_ledger).update(tip_id=tip_id, volume_m3=0.0)
        return tip_id, loc

    def drop(self, ctx, to=None):
        tip = self.need_tip(ctx, 'drop a tip')
        led = self.ledger(ctx)
        if to is None or to.kind == 'trash':
            slot = self.reg['trash']['slot']
            fixture = (ctx.view.get('deck.fixtures') or {}).get(slot)
            if fixture not in TRASH_FIXTURES:
                ctx.fail(refused('NO_TRASH', 'no trash bin is configured in slot %s (deck shows %s).' % (slot, fixture or 'nothing'),
                                 'configure it (`chemlab run %s deck-config %s trash_bin`) or return the tip to a rack.' % (self.instrument_id, slot)))
            target, ret = Loc('trash', 'trash', self.reg['trash']['position_m'], slot=slot), None
        else:
            if to.well in dict(self.rack_tips(ctx, to.labware['name'])):
                ctx.fail(refused('WELL_OCCUPIED', '%s already holds a tip.' % to.name, 'choose an empty rack well, or drop the tip in the trash.'))
            target, slot, ret = to, to.slot, (to.labware['name'], to.well)
        self.move(ctx, target)
        m = self.vmap('drop-tip')
        ctx.call(m['cap'], {m['arg']: slot}, 'drop tip %s %s' % (self.tip_name(tip), 'in the trash' if ret is None else 'into ' + target.name),
                 readback=lambda v: None if v['tip.attached_id'] is None else 'tip %r still attached' % v['tip.attached_id'])
        index = len(ctx.view['tip.events']) - 1
        if ret is not None:
            (ctx.shadow['returns'] if ctx.dry_run else self.returns)[index] = ret
        if not ctx.dry_run:
            self.own_events.add(index)
        left = led['volume_m3']
        if left:
            ctx.notes.append('the tip still held %s; it went with the tip.' % fmt_ul(left))
        led.update(tip_id=None, volume_m3=0.0)
        return tip, target

    def check_volume(self, ctx, v_m3, text):
        if not self.vmin - 1e-18 <= v_m3 <= self.vmax + 1e-18:
            ctx.fail(invalid('VOLUME_OUT_OF_RANGE', '%s is outside the pipette range %s-%s.' % (text, fmt_ul(self.vmin), fmt_ul(self.vmax)),
                             'use %s to %s per aspirate/dispense; for more, use several steps (`transfer` splits volumes for you).'
                             % (fmt_ul(self.vmin), fmt_ul(self.vmax))))

    def aspirate(self, ctx, loc, v_m3):
        tip = self.need_tip(ctx, 'aspirate')
        led = self.ledger(ctx)
        cap = self.tip_capacity(tip)
        if led['volume_m3'] is not None and cap is not None and _nl(led['volume_m3']) + _nl(v_m3) > _nl(cap):
            room = cap - led['volume_m3']
            ctx.fail(refused('TIP_CAPACITY', 'the attached %s tip holds %s already; %s more would not fit (room for %s).' % (
                fmt_ul(cap), fmt_ul(led['volume_m3']), fmt_ul(v_m3), fmt_ul(room)),
                ('aspirate at most %s now, or dispense first, or use a bigger tip; `transfer` splits volumes over the tip size.' % fmt_ul(room))
                if room >= self.vmin else 'the tip is full: dispense first, or use a bigger tip; `transfer` splits volumes over the tip size.',
                detail={'tip_capacity_ul': ul(cap), 'room_ul': ul(room)}))
        self.move(ctx, loc)
        before = ctx.view['sample.transfer_count']
        m = self.vmap('aspirate')

        def refusal(resp):
            if (resp.get('error') or {}).get('code') == 'PRECONDITION_FAILED':
                return refused('INSUFFICIENT_LIQUID', 'the instrument refused to aspirate %s from %s: the well does not hold that much liquid '
                               '(a run reports this as "no liquid detected").' % (fmt_ul(v_m3), loc.name),
                               'aspirate less, or take the rest from another source. This twin has no liquid-level sensing, so the remaining '
                               'volume is not reported.')
            return None
        ctx.call(m['cap'], {m['container_arg']: loc.container_id, m['volume_arg']: v_m3}, 'aspirate %s from %s' % (fmt_ul(v_m3), loc.name),
                 refusal=refusal, readback=lambda v: None if v['sample.transfer_count'] == before + 1 else 'no new transfer')
        if led['volume_m3'] is not None:
            led['volume_m3'] = _m3(_nl(led['volume_m3']) + _nl(v_m3))

    def dispense(self, ctx, loc, v_m3):
        self.need_tip(ctx, 'dispense')
        led = self.ledger(ctx)
        if led['volume_m3'] is not None and _nl(v_m3) > _nl(led['volume_m3']):
            ctx.fail(refused('TIP_HOLDS_LESS', 'the tip holds only %s; it cannot dispense %s.' % (fmt_ul(led['volume_m3']), fmt_ul(v_m3)),
                             'dispense at most %s, or aspirate more first.' % fmt_ul(led['volume_m3'])))
        self.move(ctx, loc)
        before = ctx.view['sample.transfer_count']
        m = self.vmap('dispense')

        def refusal(resp):
            if (resp.get('error') or {}).get('code') == 'PRECONDITION_FAILED':
                return refused('DEST_FULL', 'the instrument refused to dispense %s into %s: the well would overflow (capacity %s).' % (
                    fmt_ul(v_m3), loc.name, fmt_ul(loc.capacity_m3)), 'dispense less, or into another well.')
            return None
        ctx.call(m['cap'], {m['container_arg']: loc.container_id, m['volume_arg']: v_m3}, 'dispense %s into %s' % (fmt_ul(v_m3), loc.name),
                 refusal=refusal, readback=lambda v: None if v['sample.transfer_count'] == before + 1 else 'no new transfer')
        if led['volume_m3'] is not None:
            led['volume_m3'] = _m3(max(0, _nl(led['volume_m3']) - _nl(v_m3)))

    def position_name(self, view):
        pos = {k: view['motion.' + k] for k in ('x_m', 'y_m', 'z_m')}
        if self.reg:
            if _same(pos, self.reg['trash']['position_m']):
                return 'trash'
            if _same(pos, self.reg['home_m']):
                return 'home'
            for lw in self.reg['labware']:
                for w in lw['wells'].values():
                    if _same(pos, w['position_m']):
                        return '%s:%s' % (lw['name'], w['well'])
        return 'x=%.1f y=%.1f z=%.1f mm (not over a registered location)' % (pos['x_m'] * 1e3, pos['y_m'] * 1e3, pos['z_m'] * 1e3)

    # ------------------------------------------------------------------ status and jobs
    def summary(self, values):
        v = values if isinstance(values, dict) else values.view
        tip = v.get('tip.attached_id')
        rs = v.get('run.state')
        run = 'none'
        if rs:
            run = 'protocol %r %s' % (rs.get('name'), JOB_STATUS.get(rs.get('status'), rs.get('status')))
        return (['power %s · robot %s · lights %s' % ('on' if v.get('power.enabled') else 'off', v.get('robot.status'), 'on' if v.get('robot.lights_on') else 'off'),
                 'tip: %s · run: %s' % (self.tip_name(tip) if tip else 'none', run)],
                {'power': v.get('power.enabled'), 'tip': tip, 'run': rs and {'name': rs.get('name'), 'status': rs.get('status')}})

    def job_state(self, ctx, j):
        rs = ctx.view.get('run.state')
        if rs and self.job_for_run(rs) is j:
            st = JOB_STATUS.get(rs['status'], rs['status'])
            cur = self.names(rs.get('current_step')) or (('finishing (last step: %s)' % rs['steps'][-1]) if rs.get('steps') and rs['status'] in ('running', 'paused') else None)
            return {'job': j.handle, 'status': st, 'protocol_id': rs['protocol_id'], 'name': rs['name'], 'step': rs['index'], 'n_steps': rs['n_steps'],
                    'current_step': cur, 'eta_s': self.eta(rs, ctx.now()) if rs['status'] in ACTIVE else 0.0, 'started_s': rs.get('started_s'),
                    'ended_s': rs.get('ended_s'), 'error': rs.get('error'), 'recovery_options': rs.get('recovery_options') or []}
        for h in reversed(ctx.view.get('run.history') or []):
            if h['protocol_id'] == j.protocol_id and h['started_s'] == j.run_started_s:
                return {'job': j.handle, 'status': JOB_STATUS.get(h['status'], h['status']), 'protocol_id': h['protocol_id'], 'name': h['name'],
                        'step': h['steps_done'], 'n_steps': h['n_steps'], 'current_step': None, 'eta_s': 0.0, 'started_s': h['started_s'],
                        'ended_s': h['ended_s'], 'error': (h['errors'] or [None])[-1], 'recovery_options': []}
        return {'job': j.handle, 'status': 'unknown', 'protocol_id': j.protocol_id, 'name': j.name, 'step': None, 'n_steps': None,
                'current_step': None, 'eta_s': None, 'started_s': j.run_started_s, 'ended_s': None, 'error': None, 'recovery_options': []}

    def job_line(self, ctx, j, s):
        st = s['status']
        if st in ('running', 'paused', 'setup'):
            return 'protocol %r · step %d/%d%s · about %s left' % (s['name'], min((s['step'] or 0) + 1, s['n_steps'] or 0), s['n_steps'] or 0,
                                                                 (': ' + s['current_step']) if s['current_step'] else '', fmt_s(s['eta_s'] or 0))
        if st == 'awaiting_recovery':
            e = s['error'] or {}
            return 'needs recovery: error %s at step %d (%s); options: %s; fix: chemlab run %s recover %s --action ACTION' % (
                e.get('type'), (e.get('index') or 0) + 1, e.get('step'), ', '.join(s['recovery_options']), self.instrument_id, j.handle)
        if st == 'completed':
            return 'completed · protocol %r · %s/%s steps · ended at t=%.1f s' % (s['name'], s['step'], s['n_steps'], s['ended_s'] or 0)
        return '%s · protocol %r%s' % (st, s['name'], (' · last error: %s' % s['error']['type']) if s.get('error') else '')

    def get_job(self, ctx, job_id):
        from ..jobs import registry
        j = registry(ctx.core).get(job_id)
        if not isinstance(j, RunJob) or j.instrument != self.instrument_id:
            ctx.fail(invalid('UNKNOWN_JOB', 'there is no job %r on %s.' % (job_id, self.instrument_id),
                             'your jobs: %s.' % (', '.join(x.handle for x in self.my_jobs() if x.caller == ctx.caller) or 'none')))
        if j.caller != ctx.caller:
            ctx.fail(invalid('NOT_YOUR_JOB', '%s belongs to another user.' % job_id, 'you can only control your own jobs.'))
        return j

    # ------------------------------------------------------------------ the verb table
    def dispatch_run(self, core, req, args):
        self._core = core
        return ClassAdapter.dispatch_run(self, core, req, args)

    def intercept(self, core, req):
        self._core = core
        return ClassAdapter.intercept(self, core, req)

    def build_verbs(self):
        return build_verbs(self)


# ==================================================================== verb handlers
def build_verbs(f):
    V = {}

    def verb(name, group, summary, handler, **kw):
        v = Verb(name, summary, handler, group=group, reference=kw.pop('reference', REF), **kw)
        V[name] = v
        return v

    def vol(ctx, text):
        from ..units import parse
        m3 = parse(text, 'volume', what='volume')
        f.check_volume(ctx, m3, fmt_ul(m3))
        return m3

    # ---- status
    def h_status(ctx, p):
        v = ctx.view
        tip = v['tip.attached_id']
        tip_text = 'none'
        if tip:
            led = f.ledger(ctx)
            held = 'unknown contents' if led['volume_m3'] is None else 'holding %s' % fmt_ul(led['volume_m3'])
            cap = f.tip_capacity(tip) if f.deck else None
            tip_text = '%s (%s tip), %s' % (f.tip_name(tip), fmt_ul(cap) if cap else '?', held)
        rs = v.get('run.state')
        run_text, busy = 'none', False
        if rs:
            job = f.job_for_run(rs)
            who = ('your %s' % job.handle) if job and job.caller == ctx.caller else ('%s of another user' % job.handle if job else 'another user')
            run_text = 'protocol %r (%s) %s' % (rs['name'], who, JOB_STATUS.get(rs['status'], rs['status']))
            if rs['status'] in ACTIVE:
                busy = True
                left = f.eta(rs, ctx.now())
                run_text += ', step %d/%d, about %s left' % (min(rs['index'] + 1, rs['n_steps']), rs['n_steps'], fmt_s(left))
        mounts = v.get('instruments.mounts') or {}
        left_m = mounts.get('left')
        pip = ('left: %s, %s' % (left_m['model'], 'calibrated' if left_m['calibrated'] else 'NOT calibrated')) if left_m else 'no pipette on the left mount'
        jobs = ['%s %s' % (j.handle, f.job_state(ctx, j)['status']) for j in f.my_jobs() if j.caller == ctx.caller]
        lines = ['%s  (lab time %.1f s)' % (f.instrument_id, ctx.now()),
                 'power: %s · robot: %s · lights: %s' % ('on' if v['power.enabled'] else 'off', v.get('robot.status'), 'on' if v.get('robot.lights_on') else 'off'),
                 'pipette: %s' % pip, 'position: %s' % f.position_name(v), 'tip: %s' % tip_text, 'run: %s' % run_text,
                 'jobs: %s' % (', '.join(jobs) or 'none'), 'deck: %s' % ('registered (`labware`)' if f.deck else 'none registered')]
        note = f.lost_tip_note(ctx) if not tip else ''
        if note:
            lines.append('note:' + note)
        js = {'power': v['power.enabled'], 'robot_status': v.get('robot.status'), 'busy': busy, 'lights': v.get('robot.lights_on'),
              'position': f.position_name(v), 'tip': None if not tip else {'tip': f.tip_name(tip)}, 'jobs': jobs,
              'run': None if not rs else {'name': rs['name'], 'status': JOB_STATUS.get(rs['status'], rs['status']), 'step': rs['index'], 'n_steps': rs['n_steps']},
              'pipette': left_m}
        return {'text': '\n'.join(lines) + '\n', 'json': js}
    verb('status', 'work', 'power, position, tip and its contents, active run, jobs', h_status, readonly=True, example='chemlab run INST status')

    # ---- power / home / labware / lights
    def h_power(ctx, p):
        state = p.pos[0]
        if state == 'off':
            rs = ctx.view.get('run.state')
            job = f.job_for_run(rs) if rs else None
            if rs and rs['status'] in ('running', 'paused', 'awaiting_recovery'):
                if job is None or job.caller != ctx.caller:
                    f.check_not_busy(ctx, run_command=True)
                elif not p.get('force'):
                    ctx.fail(refused('BUSY_OWN_JOB', 'switching off now would abort your %s (protocol %r).' % (job.handle, rs['name']),
                                     'wait for it, cancel it (`chemlab cancel %s`), or add --force to abort it by power-off.' % job.handle))
        want = state == 'on'
        if ctx.view['power.enabled'] == want:
            return {'text': 'ok: power is already %s\n' % state, 'json': {'power': want}, 'checks': ['power already %s' % state]}
        m = f.vmap('power')
        ctx.call(m['cap'], {m['arg']: want}, 'power %s' % state, readback=lambda v: None if v['power.enabled'] == want else 'power %s' % v['power.enabled'])
        return {'text': 'ok: power %s\n' % state, 'json': {'power': want}}
    verb('power', 'work', 'switch the robot on or off', h_power, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
         options={'force': Opt('with off: abort your own running job', 'flag')},
         preconditions=['off is refused while a run is active (your own job: needs --force; another user\'s run: refused)'],
         errors=['BUSY, BUSY_OWN_JOB (refused)'], example='chemlab run INST power on')

    def h_home(ctx, p):
        f.check_not_busy(ctx)
        f.check_power(ctx)
        home = f.reg['home_m'] if f.reg else None
        ctx.call(f.c('home'), {}, 'home the gantry',
                 readback=(lambda v: None if _same({k: v['motion.' + k] for k in home}, home) else 'not at home') if home else None)
        return {'text': 'ok: gantry homed\n', 'json': {'position': 'home'}}
    verb('home', 'work', 'home the gantry (back right, Z up)', h_home, preconditions=['power on', 'no run active'],
         errors=['POWER_OFF, BUSY (refused)'], example='chemlab run INST home')

    def h_labware(ctx, p):
        f.need_deck(ctx)
        lines, js = ['deck (slot, labware, wells, capacity):'], []
        for lw in f.reg['labware']:
            if lw['kind'] == 'tiprack':
                tips = f.rack_tips(ctx, lw['name'])
                extra = '%s tips; %d left%s' % (fmt_ul(lw['tip_capacity_m3']), len(tips), (', next %s:%s' % (lw['name'], tips[0][0])) if tips else '')
                js.append({'name': lw['name'], 'slot': lw['slot'], 'kind': 'tiprack', 'type': lw['type'], 'tip_capacity_ul': ul(lw['tip_capacity_m3']),
                           'tips_left': len(tips), 'next_tip': ('%s:%s' % (lw['name'], tips[0][0])) if tips else None})
            else:
                extra = 'wells %s, %s each' % (f.deck.well_span(lw['name']), fmt_ul(lw['well_capacity_m3']))
                js.append({'name': lw['name'], 'slot': lw['slot'], 'kind': lw['kind'], 'type': lw['type'], 'wells': f.deck.well_span(lw['name']),
                           'well_capacity_ul': ul(lw['well_capacity_m3'])})
            lines.append('  %-3s %-12s %-10s %s (%s)' % (lw['slot'], lw['name'], lw['kind'], extra, lw['display']))
        t = f.reg['trash']
        fixture = (ctx.view.get('deck.fixtures') or {}).get(t['slot'])
        lines.append('  %-3s %-12s %-10s %s' % (t['slot'], 'trash', 'fixture', fixture or 'NOT configured'))
        lines.append('liquid contents are not tracked by the instrument; your task notes say what is in each well.')
        return {'text': '\n'.join(lines) + '\n', 'json': {'labware': js, 'trash': {'slot': t['slot'], 'fixture': fixture}}}
    verb('labware', 'work', 'what is on the deck: labware names, slots, wells, capacities, tips left', h_labware, readonly=True,
         example='chemlab run INST labware')

    def h_lights(ctx, p):
        state = p.pos[0]
        f.check_power(ctx)
        m = f.vmap('lights')
        ctx.call(m['cap'], {m['arg']: state == 'on'}, 'lights %s' % state,
                 readback=lambda v: None if v['robot.lights_on'] == (state == 'on') else 'lights %s' % v['robot.lights_on'])
        return {'text': 'ok: deck lights %s\n' % state, 'json': {'lights': state == 'on'}}
    verb('lights', 'work', 'deck lights on or off', h_lights, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
         preconditions=['power on'], errors=['POWER_OFF (refused)'], example='chemlab run INST lights on')

    # ---- motion and tips
    def h_move(ctx, p):
        f.need_deck(ctx)
        loc = f.deck.parse(p.pos[0], ('liquid', 'tip', 'trash', 'home'))
        f.check_not_busy(ctx)
        f.check_power(ctx)
        moved = f.move(ctx, loc)
        return {'text': 'ok: %s %s\n' % ('moved to' if moved else 'already at', loc.name), 'json': {'position': loc.name}}
    verb('move-to', 'work', 'move the pipette to a named location', h_move, positionals=[Arg('LOCATION', 'LABWARE:WELL, trash or home')],
         preconditions=['power on', 'no run active', 'location is on the deck'], errors=['UNKNOWN_LOCATION/LABWARE/WELL (usage)', 'POWER_OFF, BUSY (refused)'],
         example='chemlab run INST move-to plate:A1')

    def h_pick(ctx, p):
        f.need_deck(ctx)
        if p.get('tip') and p.get('rack'):
            ctx.fail(invalid('CONFLICTING_OPTIONS', 'give --tip or --rack, not both.', 'use one of them.'))
        tip = f.deck.parse(p.get('tip'), ('tip',), '--tip') if p.get('tip') else None
        rack = p.get('rack')
        if rack and (rack not in f.deck.labware or f.deck.labware[rack]['kind'] != 'tiprack'):
            ctx.fail(invalid('UNKNOWN_RACK', 'there is no tip rack %r.' % rack, 'tip racks here: %s.' % ', '.join(f.tip_racks())))
        f.check_not_busy(ctx)
        f.check_power(ctx)
        tip_id, loc = f.pick(ctx, tip, rack)
        cap = f.tip_capacity(tip_id)
        return {'text': 'ok: picked up tip %s (%s)\n' % (loc.name, fmt_ul(cap)), 'json': {'tip': loc.name, 'capacity_ul': ul(cap)}}
    verb('pick-up-tip', 'work', 'pick up a tip (default: the next unused tip)', h_pick,
         options={'tip': Opt('a specific tip-rack well', metavar='RACK:WELL'), 'rack': Opt('take the next tip from this rack', metavar='RACK')},
         preconditions=['power on', 'no run active', 'no tip attached', 'a tip is present'],
         errors=['TIP_ALREADY_ATTACHED, NO_TIP_AT_LOCATION, RACK_EMPTY, OUT_OF_TIPS, POWER_OFF, BUSY (refused)'],
         example='chemlab run INST pick-up-tip --rack tiprack200')

    def h_drop(ctx, p):
        f.need_deck(ctx)
        to = None
        if p.get('to') and p.get('to') != 'trash':
            to = f.deck.parse(p.get('to'), ('tip', 'trash'), '--to')
        f.check_not_busy(ctx)
        f.check_power(ctx)
        tip, target = f.drop(ctx, to)
        return {'text': 'ok: dropped tip %s %s\n' % (f.tip_name(tip), 'in the trash' if target.kind == 'trash' else 'into ' + target.name),
                'json': {'tip': f.tip_name(tip), 'to': target.name}}
    verb('drop-tip', 'work', 'drop the tip (default: in the trash)', h_drop, options={'to': Opt('trash or an empty tip-rack well', metavar='trash|RACK:WELL')},
         preconditions=['power on', 'no run active', 'a tip is attached', 'trash bin configured (for trash)'],
         errors=['NO_TIP, NO_TRASH, WELL_OCCUPIED, POWER_OFF, BUSY (refused)'], example='chemlab run INST drop-tip')

    def h_return(ctx, p):
        f.need_deck(ctx)
        tip = f.need_tip(ctx, 'return a tip')
        home = f.tip_home.get(tip)
        if home is None:
            ctx.fail(refused('NO_HOME_WELL', 'the attached tip has no rack well to go back to.', 'drop it in the trash instead (`drop-tip`).'))
        f.check_not_busy(ctx)
        f.check_power(ctx)
        loc = f.deck.parse('%s:%s' % home, ('tip',))
        f.drop(ctx, loc)
        return {'text': 'ok: returned tip to %s\n' % loc.name, 'json': {'tip': loc.name}}
    verb('return-tip', 'work', 'put the attached tip back where it came from', h_return,
         preconditions=['power on', 'no run active', 'a tip is attached'], errors=['NO_TIP, WELL_OCCUPIED (refused)'], example='chemlab run INST return-tip')

    # ---- liquid handling
    def h_aspirate(ctx, p):
        f.need_deck(ctx)
        v = vol(ctx, p.get('volume'))
        loc = f.deck.parse(p.get('from'), ('liquid',), '--from')
        f.check_not_busy(ctx)
        f.check_power(ctx)
        f.aspirate(ctx, loc, v)
        led = f.ledger(ctx)
        held = '' if led['volume_m3'] is None else ' (tip now holds %s)' % fmt_ul(led['volume_m3'])
        return {'text': 'ok: aspirated %s from %s%s\n' % (fmt_ul(v), loc.name, held),
                'json': {'aspirated_ul': ul(v), 'from': loc.name, 'tip_volume_ul': None if led['volume_m3'] is None else ul(led['volume_m3'])},
                'unchecked': ['liquid available in %s' % loc.name]}
    verb('aspirate', 'work', 'draw liquid into the attached tip', h_aspirate,
         options={'volume': Opt('volume (µL by default; mL, nL)', metavar='V', required=True), 'from': Opt('source well', metavar='LABWARE:WELL', required=True)},
         preconditions=['volume within the pipette range', 'power on', 'no run active', 'a tip is attached', 'the volume fits in the tip'],
         errors=['VOLUME_OUT_OF_RANGE, UNIT_NOT_ALLOWED, UNKNOWN_* (usage)', 'NO_TIP, TIP_CAPACITY, INSUFFICIENT_LIQUID, POWER_OFF, BUSY (refused)'],
         example='chemlab run INST aspirate --volume 50uL --from reservoir:A1')

    def h_dispense(ctx, p):
        f.need_deck(ctx)
        v = vol(ctx, p.get('volume'))
        loc = f.deck.parse(p.get('to'), ('liquid',), '--to')
        f.check_not_busy(ctx)
        f.check_power(ctx)
        f.dispense(ctx, loc, v)
        led = f.ledger(ctx)
        held = '' if led['volume_m3'] is None else ' (tip now holds %s)' % fmt_ul(led['volume_m3'])
        return {'text': 'ok: dispensed %s into %s%s\n' % (fmt_ul(v), loc.name, held),
                'json': {'dispensed_ul': ul(v), 'to': loc.name, 'tip_volume_ul': None if led['volume_m3'] is None else ul(led['volume_m3'])},
                'unchecked': ['room in %s' % loc.name]}
    verb('dispense', 'work', 'push liquid out of the tip into a well', h_dispense,
         options={'volume': Opt('volume (µL by default)', metavar='V', required=True), 'to': Opt('destination well', metavar='LABWARE:WELL', required=True)},
         preconditions=['volume within the pipette range', 'power on', 'no run active', 'a tip is attached', 'the tip holds that much'],
         errors=['VOLUME_OUT_OF_RANGE (usage)', 'NO_TIP, TIP_HOLDS_LESS, DEST_FULL, POWER_OFF, BUSY (refused)'],
         example='chemlab run INST dispense --volume 50uL --to plate:A1')

    def h_transfer(ctx, p):
        from ..units import parse
        f.need_deck(ctx)
        srcs = f.deck.parse_many(p.get('from'), ('liquid',), '--from')
        dsts = f.deck.parse_many(p.get('to'), ('liquid',), '--to')
        vols = [parse(x, 'volume', what='volume') for x in p.get('volume').split(',') if x.strip()]
        if len(srcs) == len(dsts):
            pairs = list(zip(srcs, dsts))
        elif len(srcs) == 1:
            pairs = [(srcs[0], d) for d in dsts]
        elif len(dsts) == 1:
            pairs = [(s, dsts[0]) for s in srcs]
        else:
            ctx.fail(invalid('PAIRING', '%d sources and %d destinations cannot be paired.' % (len(srcs), len(dsts)),
                             'give equal counts, or one source, or one destination.'))
        if len(vols) == 1:
            vols = vols * len(pairs)
        if len(vols) != len(pairs):
            ctx.fail(invalid('PAIRING', '%d volumes for %d transfers.' % (len(vols), len(pairs)), 'give one volume, or one per transfer.'))
        new_tip = p.get('new-tip') or 'once'
        rack = p.get('rack')
        if rack and (rack not in f.deck.labware or f.deck.labware[rack]['kind'] != 'tiprack'):
            ctx.fail(invalid('UNKNOWN_RACK', 'there is no tip rack %r.' % rack, 'tip racks here: %s.' % ', '.join(f.tip_racks())))
        f.check_not_busy(ctx)
        f.check_power(ctx)
        attached = ctx.view['tip.attached_id']
        if new_tip == 'never':
            if attached is None:
                ctx.fail(refused('NO_TIP', 'no tip is attached and --new-tip never was given.%s' % f.lost_tip_note(ctx),
                                 'pick up a tip first, or use --new-tip once.'))
            cap = f.tip_capacity(attached) or f.vmax
            racks_used = []
        else:
            if attached is not None:
                ctx.fail(refused('TIP_ALREADY_ATTACHED', 'a tip (%s) is already attached; --new-tip %s picks up a fresh one.' % (f.tip_name(attached), new_tip),
                                 'drop or return it first, or use --new-tip never to keep using it.'))
            racks = [rack] if rack else [r for r in f.tip_racks() if f.rack_tips(ctx, r)]
            if not racks or not f.rack_tips(ctx, racks[0]):
                ctx.fail(refused('OUT_OF_TIPS', 'no tips left%s.' % (' in %s' % rack if rack else ''), 'use another rack or ask the operator to refill.'))
            cap = f.deck.labware[racks[0]]['tip_capacity_m3']
            need = 1 if new_tip == 'once' else len(pairs)
            have = len(f.rack_tips(ctx, racks[0]))
            if have < need:
                ctx.fail(refused('OUT_OF_TIPS', '%s has %d tips left; this transfer needs %d.' % (racks[0], have, need), 'use another rack (--rack) or --new-tip once.'))
            racks_used = racks[:1]
        trip = min(f.vmax, cap)
        plan = []
        for (s, d), v in zip(pairs, vols):
            n = max(1, math.ceil(_nl(v) / _nl(trip) - 1e-12))
            base, extra = divmod(_nl(v), n)
            chunks = [_m3(base + (1 if i < extra else 0)) for i in range(n)]
            for c in chunks:
                f.check_volume(ctx, c, '%s (one trip of %s)' % (fmt_ul(c), fmt_ul(v)))
            plan.append((s, d, v, chunks))
        tips_used = []
        for i, (s, d, v, chunks) in enumerate(plan):
            if new_tip == 'always' and i > 0:
                f.drop(ctx, None)
            if (new_tip == 'once' and i == 0) or new_tip == 'always':     # a tip lost on the way is reported, never silently replaced
                tip_id, _ = f.pick(ctx, None, racks_used[0])
                tips_used.append(f.tip_name(tip_id))
            for c in chunks:
                f.aspirate(ctx, s, c)
                f.dispense(ctx, d, c)
        if new_tip != 'never':
            f.drop(ctx, None)
        moved = ', '.join('%s %s -> %s%s' % (fmt_ul(v), s.name, d.name, (' (%d trips)' % len(ch)) if len(ch) > 1 else '') for s, d, v, ch in plan)
        return {'text': 'ok: transferred %s; tips: %s\n' % (moved, ', '.join(tips_used) or 'the attached tip'),
                'json': {'transfers': [{'from': s.name, 'to': d.name, 'volume_ul': ul(v), 'trips': len(ch)} for s, d, v, ch in plan], 'tips': tips_used},
                'checks': ['volumes split to fit %s per trip' % fmt_ul(trip), 'enough tips'],
                'unchecked': ['liquid available in the sources', 'room in the destinations']}
    verb('transfer', 'work', 'move liquid between wells (picks tips, splits volumes over the tip size, drops tips)', h_transfer,
         options={'volume': Opt('volume per transfer (one value or a comma list; µL by default)', metavar='V', required=True),
                  'from': Opt('source well(s)', metavar='LOC[,LOC..]', required=True), 'to': Opt('destination well(s)', metavar='LOC[,LOC..]', required=True),
                  'new-tip': Opt('once: one tip for everything; always: a fresh tip per pair; never: use the attached tip', choices=['once', 'always', 'never'], metavar='MODE'),
                  'rack': Opt('take tips from this rack', metavar='RACK')},
         preconditions=['all locations exist and are liquid wells', 'power on', 'no run active', 'tip state matches --new-tip', 'enough tips',
                        'every trip within the pipette range and tip size'],
         errors=['PAIRING, VOLUME_OUT_OF_RANGE (usage)', 'NO_TIP, TIP_ALREADY_ATTACHED, OUT_OF_TIPS, INSUFFICIENT_LIQUID, DEST_FULL, BUSY (refused)'],
         example='chemlab run INST transfer --volume 50 --from reservoir:A1 --to plate:A1-A6',
         details='One source and many destinations, many sources and one destination, or equal lists (paired in order). Volumes larger than '
                 'one tip are split into equal trips. Tips picked up by transfer are dropped in the trash at the end (or between pairs with --new-tip always).')

    # ---- not modelled (verbs of the class vocabulary this twin has no function for; reasons in the member mapping)
    for name, summary in (('mix', 'mix a well by repeated aspirate/dispense'), ('detect-liquid', 'sense the liquid level in a well'),
                          ('set-flow-rate', 'set the aspirate/dispense flow rate'), ('blow-out', 'blow out the tip'),
                          ('touch-tip', 'touch the tip to the well wall'), ('air-gap', 'aspirate an air gap'),
                          ('move-labware', 'move labware with the gripper'), ('distribute', 'one source to many wells'),
                          ('consolidate', 'many wells to one destination')):
        verb(name, 'work', summary, None)

    # ---- protocols and jobs
    def h_protocols(ctx, p):
        rows, js = [], []
        for x in ctx.view.get('protocols.list') or []:
            params = ', '.join('%s (%s%s, default %s)' % (y['name'], y.get('type'), (' %s-%s' % (y['min'], y['max'])) if 'min' in y else '', y.get('default'))
                               for y in x.get('parameters') or [])
            rows.append('  %-14s %-24s %3d steps  %s%s' % (x['protocol_id'], x['name'], x['n_steps'], 'ready' if x['ready'] else 'NOT ready',
                                                          ('  parameters: ' + params) if params else ''))
            js.append({k: x.get(k) for k in ('protocol_id', 'name', 'n_steps', 'ready', 'parameters', 'description', 'fixtures')})
        return {'text': ('stored protocols:\n' + '\n'.join(rows) + '\n') if rows else 'no stored protocols\n', 'json': {'protocols': js}}
    verb('protocols', 'work', 'list the protocols stored on the robot', h_protocols, readonly=True, example='chemlab run INST protocols')

    def param_value(ctx, par, text):
        t = par.get('type')
        try:
            if t == 'int':
                v = int(text)
            elif t == 'float':
                v = float(text)
            elif t == 'bool':
                if text.lower() not in ('true', 'false', 'on', 'off', '1', '0'):
                    raise ValueError
                v = text.lower() in ('true', 'on', '1')
            else:
                v = text
        except ValueError:
            ctx.fail(invalid('BAD_PARAMETER', 'parameter %s takes a %s, not %r.' % (par['name'], t, text), 'give a %s value.' % t))
        if par.get('choices'):
            allowed = [c['value'] if isinstance(c, dict) else c for c in par['choices']]
            if v not in allowed:
                ctx.fail(invalid('BAD_PARAMETER', 'parameter %s must be one of %s.' % (par['name'], allowed), 'pick one of them.'))
        if t in ('int', 'float') and not (par.get('min', -math.inf) <= v <= par.get('max', math.inf)):
            ctx.fail(invalid('PARAMETER_OUT_OF_RANGE', 'parameter %s=%s is outside %s-%s.' % (par['name'], text, par.get('min'), par.get('max')),
                             'give a value from %s to %s.' % (par.get('min'), par.get('max'))))
        return v

    def h_run_protocol(ctx, p):
        pid = p.pos[0]
        proto = next((x for x in ctx.view.get('protocols.list') or [] if x['protocol_id'] == pid), None)
        if proto is None:
            ctx.fail(invalid('UNKNOWN_PROTOCOL', 'there is no stored protocol %r.' % pid, 'stored: %s (see `protocols`).' % (
                ', '.join(x['protocol_id'] for x in ctx.view.get('protocols.list') or []) or 'none')))
        params = {}
        for item in p.get('param') or []:
            name, eq, val = item.partition('=')
            par = next((x for x in proto.get('parameters') or [] if x['name'] == name), None)
            if not eq or par is None:
                ctx.fail(invalid('BAD_PARAMETER', 'unknown parameter %r for %s.' % (item, pid), 'parameters: %s; write --param NAME=VALUE.' % (
                    ', '.join(x['name'] for x in proto.get('parameters') or []) or 'none')))
            params[name] = param_value(ctx, par, val)
        f.check_not_busy(ctx, run_command=True)
        f.check_power(ctx)
        if ctx.view['tip.attached_id'] is not None:
            ctx.fail(refused('TIP_ATTACHED', 'a tip is attached; a protocol run starts with an empty pipette.', 'drop it (`drop-tip`) or return it (`return-tip`) first.'))
        mounts = ctx.view.get('instruments.mounts') or {}
        mt = mounts.get(proto.get('pipette_mount') or 'left') if isinstance(mounts, dict) else None
        if not mt or not mt.get('calibrated'):
            ctx.fail(refused('RUN_NOT_READY', 'protocol %s needs a calibrated pipette on the left mount.' % pid, 'attach and calibrate it (see `chemlab run %s` admin verbs).' % f.instrument_id))
        fixtures = ctx.view.get('deck.fixtures') or {}
        conflicts = {s: fx for s, fx in (proto.get('fixtures') or {}).items() if fixtures.get(s) != fx}
        if conflicts and not p.get('update-deck'):
            ctx.fail(refused('RUN_NOT_READY', 'protocol %s needs deck fixtures %s.' % (pid, ', '.join('%s=%s' % kv for kv in conflicts.items())),
                             'add --update-deck to set them, or use `deck-config`.'))
        m = f.vmap('run-protocol')
        steps = [(m['setup_cap'], {'protocol_id': pid}, 'set up run of %s' % pid)]
        steps += [(m['parameter_cap'], {'name': k, 'value': v}, 'set %s=%s' % (k, v)) for k, v in params.items()]
        if proto.get('parameters'):
            steps.append((m['confirm_cap'], {}, 'confirm parameters'))
        if conflicts:
            steps.append((m['update_deck_cap'], {}, 'update deck fixtures'))
        if p.get('apply-offsets'):
            steps.append((m['offsets_cap'], {}, 'apply stored labware offsets'))
        steps.append((m['play_cap'], {}, 'start the run'))
        try:
            for cid, a, text in steps:
                ctx.call(cid, a, text)
        except Exception as exc:
            if not ctx.dry_run and ctx.changed and (ctx.view.get('run.state') or {}).get('status') == 'setup':
                ctx.call(m['stop_cap'], {}, 'roll back the run setup')
                ctx.changed = False
                exc.changed, exc.done = 'no (the run setup was rolled back)', list(ctx.done)
            raise
        if ctx.dry_run:
            return {'text': '', 'json': {}, 'checks': ['protocol stored', 'parameters valid', 'no tip attached', 'pipette calibrated', 'deck fixtures']}
        rs = ctx.view.get('run.state') or {}
        job = RunJob(ctx.core, f, ctx.req, pid, proto['name'], rs.get('started_s'), params)
        left = f.eta(rs, ctx.now()) if rs.get('status') in ACTIVE else 0.0
        return {'text': 'ok: %s started: protocol %r (%d steps, about %s of lab time)\nnext: `chemlab status %s` / `chemlab wait %s` to follow it\n' % (
            job.handle, proto['name'], rs.get('n_steps', 0), fmt_s(left), job.handle, job.handle),
            'json': {'job': job.handle, 'protocol_id': pid, 'n_steps': rs.get('n_steps'), 'eta_s': left, 'status': JOB_STATUS.get(rs.get('status'), rs.get('status'))}}
    verb('run-protocol', 'work', 'start a stored protocol as a job (returns job-N)', h_run_protocol, long_action=True,
         positionals=[Arg('PROTOCOL', 'protocol id (see `protocols`)')],
         options={'param': Opt('runtime parameter, repeatable', kind='multi', metavar='NAME=VALUE'), 'update-deck': Opt('set the deck fixtures the protocol needs', 'flag'),
                  'apply-offsets': Opt('apply the labware offsets stored from an earlier Labware Position Check', 'flag')},
         preconditions=['protocol stored', 'parameters known and in range', 'power on', 'no other run active', 'no tip attached', 'pipette calibrated',
                        'deck fixtures match (or --update-deck)'],
         errors=['UNKNOWN_PROTOCOL, BAD_PARAMETER, PARAMETER_OUT_OF_RANGE (usage)', 'BUSY, TIP_ATTACHED, RUN_NOT_READY, POWER_OFF (refused)'],
         example='chemlab run INST run-protocol plate_prep --param volume_ul=80')

    def h_job(ctx, p):
        j = f.get_job(ctx, p.pos[0])
        s = f.job_state(ctx, j)
        return {'text': '%s %s\n' % (j.handle, f.job_line(ctx, j, s)), 'json': s}
    verb('job', 'work', 'progress of a job (same as `chemlab status JOB`)', h_job, positionals=[Arg('JOB', 'job id')], readonly=True,
         example='chemlab run INST job job-1')

    def job_action(action):
        def h(ctx, p):
            j = f.get_job(ctx, p.pos[0])
            s = f.job_state(ctx, j)
            st = s['status']
            need = {'pause': ('running',), 'resume': ('paused',), 'cancel': ('setup', 'running', 'paused', 'awaiting_recovery')}[action]
            if st not in need:
                ctx.fail(refused('JOB_FINISHED' if st in ('completed', 'failed', 'canceled') else 'JOB_NOT_%s' % need[0].upper(),
                                 '%s is %s; cannot %s it.' % (j.handle, st, action), 'check `chemlab status %s`.' % j.handle))
            ctx.call(f.c(action), {}, '%s %s' % (action, j.handle))
            if not ctx.dry_run and action == 'pause':
                j.pause_t = ctx.now()
            elif not ctx.dry_run and action == 'resume' and j.pause_t is not None:
                j.paused_s += ctx.now() - j.pause_t
                j.pause_t = None
            s = f.job_state(ctx, j) if not ctx.dry_run else s
            return {'text': 'ok: %s %s (now %s)\n' % (action, j.handle, s['status']), 'json': {'job': j.handle, 'status': s['status']}}
        return h
    for action, summary in (('pause', 'pause a running job'), ('resume', 'resume a paused job'), ('cancel', 'cancel a job (same as `chemlab cancel JOB`)')):
        verb(action, 'work', summary, job_action(action), positionals=[Arg('JOB', 'job id')],
             preconditions=['the job is yours', 'the job is in a state that allows it'], errors=['UNKNOWN_JOB, NOT_YOUR_JOB (usage)', 'JOB_FINISHED (refused)'],
             example='chemlab run INST %s job-1' % action)

    def h_recover(ctx, p):
        j = f.get_job(ctx, p.pos[0])
        s = f.job_state(ctx, j)
        if s['status'] != 'awaiting_recovery':
            ctx.fail(refused('NOT_AWAITING_RECOVERY', '%s is %s, not waiting for error recovery.' % (j.handle, s['status']), 'check `chemlab status %s`.' % j.handle))
        if p.get('action') not in s['recovery_options']:
            ctx.fail(refused('ACTION_NOT_ALLOWED', '%s is not offered for error %s.' % (p.get('action'), (s['error'] or {}).get('type')),
                             'choose one of: %s.' % ', '.join(s['recovery_options'])))
        m = f.vmap('recover')
        ctx.call(m['cap'], {m['arg']: p.get('action')}, 'recover %s with %s' % (j.handle, p.get('action')))
        s = f.job_state(ctx, j) if not ctx.dry_run else s
        return {'text': 'ok: %s recovered with %s (now %s)\n' % (j.handle, p.get('action'), s['status']), 'json': {'job': j.handle, 'status': s['status']}}
    verb('recover', 'work', 'answer the error-recovery question of a job (retry, skip, new tips, cancel...)', h_recover,
         positionals=[Arg('JOB', 'job id')], options={'action': Opt('recovery action (see `chemlab status JOB`)', metavar='ACTION', required=True)},
         preconditions=['the job waits for recovery', 'the action is offered for its error'], errors=['NOT_AWAITING_RECOVERY, ACTION_NOT_ALLOWED (refused)'],
         example='chemlab run INST recover job-1 --action retry_same_tips')

    # ---- maintenance / admin
    def simple(verb_name, args, text, check_busy=True, extra=None):
        def h(ctx, p):
            m = f.vmap(verb_name)
            a = args(ctx, p) if callable(args) else dict(args)
            if check_busy:
                f.check_not_busy(ctx, run_command=True)
            f.check_power(ctx)
            if extra:
                extra(ctx, a)
            ctx.call(m['cap'], a, text(a) if callable(text) else text)
            return {'text': 'ok: %s\n' % (text(a) if callable(text) else text), 'json': {'capability': m['cap'], 'arguments': a}}
        return h

    def enum_of(verb_name, arg, path=None):
        m = f.vmap(verb_name) if f.lab else None
        if not m:
            return None
        s = f.cap_schema(m['cap'], arg)
        for k in path or []:
            s = s[k]
        return s.get('enum')

    def h_pipettes(ctx, p):
        mt = ctx.view.get('instruments.mounts') or {}
        lines = ['  %-9s %s' % (k, ('%s, %s' % (v['model'], 'calibrated' if v['calibrated'] else 'NOT calibrated')) if v else 'empty') for k, v in mt.items()]
        return {'text': 'mounts:\n' + '\n'.join(lines) + '\n', 'json': {'mounts': mt}}
    verb('pipettes', 'admin', 'what is on each mount and whether it is calibrated', h_pipettes, readonly=True)

    def need_mounted(ctx, a):
        if not (ctx.view.get('instruments.mounts') or {}).get(a['mount']):
            ctx.fail(refused('NO_PIPETTE', 'no pipette on the %s mount.' % a['mount'], 'attach one first (`attach-pipette`).'))

    def no_tip(ctx, a):
        need_mounted(ctx, a)
        if ctx.view['tip.attached_id']:
            ctx.fail(refused('TIP_ATTACHED', 'a tip is attached.', 'drop it before detaching the pipette.'))

    def mount_free(ctx, a):
        if any((ctx.view.get('instruments.mounts') or {}).get(k) for k in ('left', 'right')):
            ctx.fail(refused('MOUNT_TAKEN', 'a pipette is already attached (this Flex has one 1-channel pipette).', 'detach it first.'))

    def mount_arg(ctx, p):
        mt = p.get('mount') or 'left'
        allowed = enum_of('calibrate-pipette', 'mount') or ['left', 'right']
        if mt not in allowed:
            ctx.fail(invalid('BAD_CHOICE', 'mount %r is not one of %s.' % (mt, ', '.join(allowed)), 'use --mount left or right.'))
        return mt

    verb('calibrate-pipette', 'admin', 'run the automatic pipette calibration',
         simple('calibrate-pipette', lambda c, p: {'mount': mount_arg(c, p)}, lambda a: 'calibrated the %s pipette' % a['mount'], extra=need_mounted),
         options={'mount': Opt('mount', metavar='MOUNT')}, preconditions=['power on', 'no run active', 'a pipette on that mount'])

    def attach_args(ctx, p):
        models = enum_of('attach-pipette', 'model') or []
        model = p.get('model') or (models[0] if models else None)
        if models and model not in models:
            ctx.fail(invalid('BAD_CHOICE', 'model %r is not one of %s.' % (model, ', '.join(models)), 'use --model with one of them.'))
        return {'mount': mount_arg(ctx, p), 'model': model}
    verb('attach-pipette', 'admin', 'record a pipette attached to a mount (needs calibration after)',
         simple('attach-pipette', attach_args, lambda a: 'attached %s on the %s mount' % (a['model'], a['mount']), extra=mount_free),
         options={'mount': Opt('mount', metavar='MOUNT', required=True), 'model': Opt('model', metavar='MODEL')},
         preconditions=['power on', 'no run active', 'mount free'])
    verb('detach-pipette', 'admin', 'record the pipette as removed (destructive: --yes)',
         simple('detach-pipette', lambda c, p: {'mount': mount_arg(c, p)}, lambda a: 'detached the %s pipette' % a['mount'], extra=no_tip),
         options={'mount': Opt('mount', metavar='MOUNT', required=True)}, destructive=True, preconditions=['power on', 'no run active', 'no tip attached'])

    def h_deck(ctx, p):
        if not p.pos:
            fx = ctx.view.get('deck.fixtures') or {}
            return {'text': 'deck fixtures:\n' + ''.join('  %s  %s\n' % kv for kv in sorted(fx.items())), 'json': {'fixtures': fx}}
        if len(p.pos) != 2:
            ctx.fail(invalid('MISSING_ARGUMENT', 'deck-config needs SLOT and FIXTURE (or none).', 'example: deck-config A1 trash_bin'))
        m = f.vmap('deck-config')
        cutouts = f.cap_schema(m['cap'], 'slot').get('enum') or []
        fixture_enum = (f.cap_schema(m['cap'], 'fixture').get('anyOf') or [{}])[0].get('enum') or []
        fixture_slots = f.param('fixture_slots', {})
        slot, fixture = p.pos[0].upper(), p.pos[1]
        if slot not in cutouts:
            ctx.fail(invalid('BAD_CHOICE', 'fixtures go in %s, not %s.' % (', '.join(cutouts), slot), 'pick one of those slots.'))
        fixture = None if fixture == 'none' else fixture
        if fixture is not None and fixture not in fixture_enum:
            ctx.fail(invalid('BAD_CHOICE', 'unknown fixture %r.' % p.pos[1], 'fixtures: %s, or none.' % ', '.join(fixture_enum)))
        if fixture is not None and slot not in fixture_slots.get(fixture, [slot]):
            ctx.fail(invalid('FIXTURE_NOT_ALLOWED', '%s cannot go in %s.' % (fixture, slot), '%s goes in %s.' % (fixture, ', '.join(fixture_slots[fixture]))))
        f.check_not_busy(ctx, run_command=True)
        f.check_power(ctx)
        ctx.call(m['cap'], {'slot': slot, 'fixture': fixture}, 'set %s to %s' % (slot, fixture or 'empty'))
        return {'text': 'ok: %s is now %s\n' % (slot, fixture or 'empty'), 'json': {'slot': slot, 'fixture': fixture}}
    verb('deck-config', 'admin', 'show or change the deck fixtures (trash bin, staging area, waste chute)', h_deck,
         positionals=[Arg('SLOT', 'cutout', required=False), Arg('FIXTURE', 'fixture or none', required=False)],
         preconditions=['fixture allowed in that slot (Flex deck fixtures table)', 'power on', 'no run active'])

    def h_settings(ctx, p):
        settings = ctx.view.get('robot.settings') or {}
        if not p.pos:
            return {'text': 'settings:\n' + ''.join('  %-24s %s\n' % kv for kv in sorted(settings.items())), 'json': {'settings': settings}}
        m = f.vmap('settings')
        keys = f.cap_schema(m['cap'], 'key').get('enum') or []
        key = p.pos[0]
        if key not in keys:
            ctx.fail(invalid('BAD_CHOICE', 'unknown setting %r.' % key, 'settings: %s.' % ', '.join(keys)))
        if len(p.pos) == 1:
            return {'text': '%s = %s\n' % (key, settings.get(key)), 'json': {key: settings.get(key)}}
        raw = p.pos[1]
        allowed = f.param('setting_values', {}).get(key, [True, False])
        if allowed == [True, False]:
            if raw.lower() not in ('on', 'off', 'true', 'false'):
                ctx.fail(invalid('BAD_CHOICE', '%s is on or off.' % key, 'write on or off.'))
            value = raw.lower() in ('on', 'true')
        else:
            value = int(raw) if raw.isdigit() else raw
            if value not in allowed:
                ctx.fail(invalid('BAD_CHOICE', '%s cannot be %r.' % (key, raw), 'allowed: %s.' % ', '.join(map(str, allowed))))
        f.check_not_busy(ctx, run_command=True)
        f.check_power(ctx)
        ctx.call(m['cap'], {'key': key, 'value': value}, 'set %s to %s' % (key, value))
        return {'text': 'ok: %s = %s\n' % (key, value), 'json': {key: value}}
    verb('settings', 'admin', 'show or change robot settings', h_settings, positionals=[Arg('KEY', 'setting name', required=False), Arg('VALUE', 'new value', required=False)],
         preconditions=['value allowed (Flex robot settings)', 'power on', 'no run active'])
    verb('set-name', 'admin', 'rename the robot', simple('set-name', lambda c, p: {'name': p.pos[0]}, lambda a: 'robot renamed to %r' % a['name']),
         positionals=[Arg('NAME', '1-35 characters')])
    verb('restart', 'admin', 'restart the robot software (destructive: --yes)', simple('restart', {}, 'restart the robot'), destructive=True,
         preconditions=['power on', 'no run active'])
    verb('shutdown', 'admin', 'shut the robot software down (destructive: --yes)', simple('shutdown', {}, 'shut the robot down'), destructive=True,
         preconditions=['power on', 'no run active'])

    def proto_exists(ctx, a):
        if not any(x['protocol_id'] == a['protocol_id'] for x in ctx.view.get('protocols.list') or []):
            ctx.fail(invalid('UNKNOWN_PROTOCOL', 'there is no stored protocol %r.' % a['protocol_id'], 'see `protocols`.'))

    def pin_args(ctx, p):
        if p.pos[1] not in ('on', 'off'):
            ctx.fail(invalid('BAD_CHOICE', 'protocol-pin takes on or off.', 'write protocol-pin ID on|off.'))
        return {'protocol_id': p.pos[0], 'pinned': p.pos[1] == 'on'}
    verb('protocol-pin', 'admin', 'pin or unpin a protocol on the touchscreen',
         simple('protocol-pin', pin_args, lambda a: '%s pinned=%s' % (a['protocol_id'], a['pinned']), extra=proto_exists),
         positionals=[Arg('PROTOCOL', 'protocol id'), Arg('on|off', 'pin or unpin')])
    verb('protocol-delete', 'admin', 'delete a stored protocol and its run history (destructive: --yes)',
         simple('protocol-delete', lambda c, p: {'protocol_id': p.pos[0]}, lambda a: 'deleted protocol %s' % a['protocol_id'], extra=proto_exists),
         positionals=[Arg('PROTOCOL', 'protocol id')], destructive=True)

    def reset_args(ctx, p):
        m = f.vmap('device-reset')
        items_enum = (f.cap_schema(m['cap'], 'items').get('items') or {}).get('enum') or []
        items = [x for x in p.pos[0].split(',') if x]
        bad = [x for x in items if x not in items_enum]
        if bad or not items:
            ctx.fail(invalid('BAD_CHOICE', 'unknown reset item(s) %s.' % ', '.join(bad or ['(none)']), 'items: %s.' % ', '.join(items_enum)))
        return {'items': items}
    verb('device-reset', 'admin', 'erase calibration, protocols, run history or offsets (destructive: --yes)',
         simple('device-reset', reset_args, lambda a: 'reset %s' % ', '.join(a['items'])), positionals=[Arg('ITEMS', 'comma list of reset items')],
         destructive=True)
    return V

"""Opentrons Flex verb table and handlers (the hand-written part of the gate).

Verb names follow the Opentrons Python API (InstrumentContext.pick_up_tip / drop_tip / return_tip / aspirate / dispense /
transfer / move_to / home; protocol runs and actions after the HTTP API /runs and /runs/{id}/actions).  Ranges and enums
come from the asset contract at start-up; the deck registry comes from the backend's host side (names, wells, joint
targets, capacities - never liquid volumes).

Manual references (saved under references/manuals/opentrons_flex_single_5_1000ul/, internal):
  Python API reference, Instruments (InstrumentContext) and Labware; Complex commands (transfer); Pipettes, loading and
  characteristics; Flex manual: deck fixtures, protocol run (error recovery), robot settings; HTTP API reference (/runs).
"""
import copy
import difflib
import math
import re

from .core import Opt, Verb
from .errors import ChemlabError, DefinedExecutionError, UndefinedExecutionError, ValidationError
from .units import fmt_s, fmt_ul, parse_volume, ul

ACTIVE = ('setup', 'running', 'paused', 'awaiting_recovery')
JOB_STATUS = {'setup': 'setup', 'running': 'running', 'paused': 'paused', 'awaiting_recovery': 'awaiting_recovery',
              'succeeded': 'completed', 'failed': 'failed', 'stopped': 'canceled'}
ROWS = 'ABCDEFGH'
TRASH_FIXTURES = ('trash_bin', 'waste_chute', 'waste_chute_staging')
FIXTURE_SLOTS = {'trash_bin': ('A1', 'B1', 'C1', 'D1', 'A3', 'B3', 'C3', 'D3'), 'staging_area': ('A3', 'B3', 'C3', 'D3'),
                 'waste_chute': ('D3',), 'waste_chute_staging': ('D3',)}             # Flex manual, deck fixtures
SETTING_VALUES = {'brightness_level': [1, 2, 3, 4, 5, 6], 'sleep_min': ['never', 3, 5, 10, 15, 30, 60], 'language': ['English', 'Chinese'],
                  'update_channel': ['stable', 'beta']}                              # Flex manual, robot settings; others on/off
UNSUPPORTED = {
    'mix': ('sample.mix', 'mixing is not modelled in this twin', 'repeated aspirate/dispense in one well only moves volume; mixing is assumed ideal'),
    'detect-liquid': ('liquid_level.detect', 'there is no liquid-level sensing in this twin',
                      'the remaining volume of a well is not reported; a refused aspirate tells you the source is short'),
    'liquid-level': ('liquid_level.detect', 'there is no liquid-level sensing in this twin', 'see detect-liquid'),
    'set-speed': ('velocity.set', 'speeds and accelerations are not modelled', 'leave the defaults'),
    'set-flow-rate': ('velocity.set', 'flow rates are not modelled', 'leave the defaults'),
    'blow-out': (None, 'blow-out is not modelled (the volume ledger leaves no residue in the tip)', 'dispense the full volume'),
    'touch-tip': (None, 'touch-tip is not modelled', 'not needed in this twin'),
    'air-gap': (None, 'air gaps are not modelled', 'not needed in this twin'),
    'move-labware': (None, 'this Flex has no gripper', 'labware stays where the deck registry says (see `labware`)'),
    'distribute': (None, 'use transfer with one source and several destinations', 'chemlab run flex transfer --from reservoir:A1 --to plate:A1-A6 --volume 50'),
    'consolidate': (None, 'use transfer with several sources and one destination', 'chemlab run flex transfer --from plate:A1,plate:A2 --to tuberack:A1 --volume 50'),
}


class Loc:
    def __init__(self, kind, name, position, labware=None, well=None, container_id=None, capacity_m3=None, slot=None):
        self.kind, self.name, self.position = kind, name, position
        self.labware, self.well, self.container_id, self.capacity_m3, self.slot = labware, well, container_id, capacity_m3, slot


def _same(a, b):
    return all(abs(a[k] - b[k]) <= 1e-9 for k in ('x_m', 'y_m', 'z_m'))


def _nl(m3):
    return int(round(m3 * 1e12))


def _m3(nl):
    return float('%de-12' % nl)


def _defined(code, message, fix, **kw):
    return DefinedExecutionError(code, message, fix, **kw)


class FlexInstrument:
    name = 'flex'
    title = 'Opentrons Flex, 1-channel 5-1000 µL pipette (fast simulation backend)'

    def __init__(self, core):
        self.core, self.backend, self.contract = core, core.backend, core.contract
        self.reg = self.backend.registry()
        self.labware = {lw['name']: lw for lw in self.reg['labware']}
        self.tip_home = {}
        for lw in self.reg['labware']:
            if lw['kind'] == 'tiprack':
                for w in lw['wells'].values():
                    if w['tip_id']:
                        self.tip_home[w['tip_id']] = (lw['name'], w['well'])
        vol = self.contract.capabilities['sample.aspirate'].input_schema['properties']['volume_m3']
        self.vmin, self.vmax = vol['minimum'], vol['maximum']              # pipette range from the contract
        self.jobs, self.job_n = {}, 0
        self.tip_ledger = {'tip_id': None, 'volume_m3': 0.0}                  # commanded contents of the attached tip
        self.returns = {}                                                  # tip.events index -> (rack, well) of a return-tip
        self.own_events = set()                                            # tip.events indexes caused by chemlab verbs
        self.verbs = build_verbs(self)

    # ---------------------------------------------------------------- start-up check against the contract
    def check_against_contract(self):
        caps = self.contract.capabilities
        for v in self.verbs.values():
            for cid in v.maps_to:
                if cid not in caps or not caps[cid].agent_usable:
                    raise RuntimeError('verb %s maps to %s, which the contract does not offer to agents' % (v.name, cid))
                if cid in self.contract.hidden():
                    raise RuntimeError('verb %s maps to hidden capability %s' % (v.name, cid))

    # ---------------------------------------------------------------- locations
    def parse_location(self, text, allow, what='location'):
        t = (text or '').strip()
        kinds = {'liquid': 'a well of a reservoir, plate or tube rack (e.g. plate:A1)', 'tip': 'a tip-rack well (e.g. tiprack1000:A1)',
                 'trash': 'trash', 'home': 'home'}
        wanted = ' or '.join(kinds[k] for k in allow)
        if t in ('trash', 'home'):
            if t not in allow:
                raise ValidationError('WRONG_LOCATION_KIND', '%s cannot be %s here.' % (what, t), 'give %s.' % wanted)
            if t == 'trash':
                return Loc('trash', 'trash', self.reg['trash']['position_m'], slot=self.reg['trash']['slot'])
            return Loc('home', 'home', self.reg['home_m'])
        name, sep, well = t.partition(':')
        names = ', '.join(sorted(self.labware))
        if not sep:
            if t in self.labware:
                raise ValidationError('WELL_MISSING', '%r is a labware; name one of its wells.' % t, 'write %s:A1 (wells %s).' % (t, self._well_span(t)))
            raise ValidationError('UNKNOWN_LOCATION', 'unknown %s %r.' % (what, t), 'locations are LABWARE:WELL, trash or home; labware here: %s.' % names)
        lw = self.labware.get(name)
        if lw is None:
            close = difflib.get_close_matches(name, list(self.labware), 1)
            raise ValidationError('UNKNOWN_LABWARE', 'there is no labware %r on the deck.' % name,
                                  ('did you mean %s? ' % close[0] if close else '') + 'labware here: %s (see `chemlab run flex labware`).' % names)
        w = lw['wells'].get(well.upper())
        if w is None:
            raise ValidationError('UNKNOWN_WELL', '%s has no well %r.' % (name, well), '%s has wells %s.' % (name, self._well_span(name)))
        kind = 'tip' if lw['kind'] == 'tiprack' else 'liquid'
        if kind not in allow:
            raise ValidationError('WRONG_LOCATION_KIND', '%s:%s is %s, but %s must be %s.' % (name, w['well'], kinds[kind].split(' (')[0], what, wanted),
                                  'give %s.' % wanted)
        return Loc(kind, '%s:%s' % (name, w['well']), w['position_m'], labware=lw, well=w['well'], container_id=w.get('container_id'),
                   capacity_m3=w.get('capacity_m3'), slot=lw['slot'])

    def parse_locations(self, text, allow, what):
        out = []
        for part in [p for p in (text or '').split(',') if p.strip()]:
            m = re.fullmatch(r'\s*([A-Za-z0-9_.-]+):([A-Ha-h])(\d{1,2})-([A-Ha-h])(\d{1,2})\s*', part)
            if m:
                lw, r1, c1, r2, c2 = m.group(1), ROWS.index(m.group(2).upper()), int(m.group(3)), ROWS.index(m.group(4).upper()), int(m.group(5))
                for c in range(min(c1, c2), max(c1, c2) + 1):                   # Opentrons order: down each column
                    for r in range(min(r1, r2), max(r1, r2) + 1):
                        out.append(self.parse_location('%s:%s%d' % (lw, ROWS[r], c), allow, what))
            else:
                out.append(self.parse_location(part, allow, what))
        if not out:
            raise ValidationError('MISSING_VALUE', '%s is empty.' % what, 'give one or more locations, e.g. plate:A1,plate:A2 or plate:A1-A6.')
        return out

    def _well_span(self, name):
        lw = self.labware[name]
        return 'A1-%s%d' % (ROWS[lw['rows'] - 1], lw['cols'])

    def position_name(self, view):
        pos = {k: view['motion.' + k] for k in ('x_m', 'y_m', 'z_m')}
        if _same(pos, self.reg['trash']['position_m']):
            return 'trash'
        if _same(pos, self.reg['home_m']):
            return 'home'
        for lw in self.reg['labware']:
            for w in lw['wells'].values():
                if _same(pos, w['position_m']):
                    return '%s:%s' % (lw['name'], w['well'])
        return 'x=%.1f y=%.1f z=%.1f mm (not over a registered location)' % (pos['x_m'] * 1e3, pos['y_m'] * 1e3, pos['z_m'] * 1e3)

    # ---------------------------------------------------------------- tips
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
        """Tips present in a rack, in pick order (down each column)."""
        lw = self.labware[rack]
        present = {(l[1], l[2]): tip for tip, l in self.tip_locations(ctx).items() if l[0] == 'rack' and l[1] == rack}
        order = ['%s%d' % (ROWS[r], c + 1) for c in range(lw['cols']) for r in range(lw['rows'])]
        return [(w, present[(rack, w)]) for w in order if (rack, w) in present]

    def tip_racks(self):
        return [lw['name'] for lw in self.reg['labware'] if lw['kind'] == 'tiprack']

    def tip_capacity(self, tip_id):
        rack = self.tip_home.get(tip_id, (None,))[0]
        return self.labware[rack]['tip_capacity_m3'] if rack else None

    def ledger(self, ctx):
        led = ctx.shadow['ledger'] if ctx.dry_run else self.tip_ledger
        attached = ctx.view['tip.attached_id']
        if led['tip_id'] != attached:                                      # the tip changed without a chemlab verb
            led.update(tip_id=attached, volume_m3=None if attached else 0.0)
        return led

    def _lost_tip_note(self, ctx):
        events = ctx.view['tip.events']
        if events and events[-1]['event'] == 'release' and (len(events) - 1) not in self.own_events and not self._current_job(ctx):
            e = events[-1]
            return ' The last tip (%s) was released in slot %s at t=%.1f s without a chemlab command - it may have fallen off.' % (
                self._tip_name(e['tip_id']), e['slot_id'], e['time_s'])
        return ''

    def _tip_name(self, tip_id):
        home = self.tip_home.get(tip_id)
        return '%s:%s' % home if home else tip_id

    # ---------------------------------------------------------------- shadow for dry-run
    def new_shadow(self):
        return {'ledger': copy.deepcopy(self.tip_ledger), 'returns': {}}

    def apply_shadow(self, ctx, cid, args):
        v = ctx.view
        if cid == 'power.set':
            v['power.enabled'] = args['enabled']
        elif cid == 'motion.move':
            for k in ('x_m', 'y_m', 'z_m'):
                v['motion.' + k] = args[k]
        elif cid == 'robot.home':
            for k in ('x_m', 'y_m', 'z_m'):
                v['motion.' + k] = self.reg['home_m'][k]
        elif cid == 'tip.attach':
            v['tip.attached_id'] = args['tip_id']
            v['tip.events'] = v['tip.events'] + [{'event': 'attach', 'tip_id': args['tip_id'], 'slot_id': None, 'time_s': v['_time_s']}]
        elif cid == 'tip.release':
            v['tip.events'] = v['tip.events'] + [{'event': 'release', 'tip_id': v['tip.attached_id'], 'slot_id': args['target_slot_id'],
                                                  'time_s': v['_time_s']}]
            v['tip.attached_id'] = None
        elif cid in ('sample.aspirate', 'sample.dispense'):
            v['sample.transfer_count'] += 1
        elif cid == 'robot.set_lights':
            v['robot.lights_on'] = args['on']

    # ---------------------------------------------------------------- jobs and busy checks
    def _job_for_run(self, rs):
        if rs is None:
            return None
        for j in self.jobs.values():
            if j['protocol_id'] == rs['protocol_id'] and j['started_s'] is not None and j['started_s'] == rs.get('started_s'):
                return j
        return None

    def _current_job(self, ctx):
        rs = ctx.view.get('run.state')
        j = self._job_for_run(rs)
        return j if j and rs['status'] in ACTIVE else None

    def has_active_job(self, caller):
        r = self.backend.read(caller, ['run.state'])
        rs = r['observations']['run.state']['value'] if r.get('status') == 'succeeded' else None
        j = self._job_for_run(rs)
        return bool(j and j['caller'] == caller and rs['status'] in ACTIVE)

    def names(self, text):
        """Runtime ids in run step texts (reservoir_A2) -> deck names (reservoir:A2)."""
        if not text:
            return text
        return re.sub(r'\b([A-Za-z0-9.-]+(?:_[A-Za-z0-9.-]+)*)_([A-H]\d{1,2})\b',
                      lambda m: '%s:%s' % (m.group(1), m.group(2)) if self.backend.location_of(m.group(0)) else m.group(0), text)

    def _step_s(self, text):
        m = re.match(r'Delaying ([0-9.]+) s', text or '')
        return float(m.group(1)) if m else self.reg['command_s']

    def eta(self, rs, now=None):
        """Simulated seconds left: the schedule after the run's start (one command per command_s, delays as written) plus the
        pauses the core saw for its own jobs; never less than the steps still listed."""
        steps, idx = rs.get('steps') or [], rs.get('index') or 0
        listed = sum(self._step_s(t) for t in steps[idx:])
        start = rs.get('started_s')
        if start is None or now is None:
            return listed
        job = self._job_for_run(rs)
        paused = 0.0
        if job is not None:
            paused = job.get('paused_s', 0.0) + ((now - job['pause_t']) if job.get('pause_t') is not None else 0.0)
        end = start + sum(self._step_s(t) for t in steps) + paused
        return max(listed, end - now) if rs['status'] in ('running', 'paused') else listed

    def wall(self, sim_s):
        sp = getattr(self.backend, 'speed', None)
        return (' (about %s of wall time at %g x sim speed)' % (fmt_s(sim_s / sp), sp)) if sp else ''

    def check_not_busy(self, ctx, run_command=False, allow_own_job=False):
        rs = ctx.view.get('run.state')
        if not rs or rs['status'] not in ACTIVE:
            return
        job = self._job_for_run(rs)
        if job is not None and job['caller'] == ctx.caller:
            if allow_own_job:
                return
            raise _defined('BUSY_OWN_JOB', 'your %s (protocol %r) is %s; direct commands would interfere with it.' % (job['job'], rs['name'], rs['status']),
                           'wait for it (`chemlab status %s`), or pause or cancel it first.' % job['job'], detail={'job': job['job']})
        if rs['status'] == 'setup' and not run_command:
            return                                                         # the instrument allows motion while another run is only set up
        owner = ('%r (%s)' % (job['caller'], job['job'])) if job else 'another user'
        left = self.eta(rs, ctx.view['_time_s'])
        step = '' if rs['status'] == 'setup' else ', step %d/%d' % (min(rs['index'] + 1, rs['n_steps']), rs['n_steps'])
        raise _defined('BUSY', 'the Flex is busy: protocol run %r started by %s is %s%s, about %s left%s.' % (
            rs['name'], owner, rs['status'].replace('_', ' '), step, fmt_s(left), self.wall(left)),
            'wait until it has finished (`chemlab status flex` shows robot: idle), then retry. Do not switch the power off: '
            'that would abort their run.', detail={'run': rs['name'], 'status': rs['status'], 'eta_s': left})

    def check_power(self, ctx):
        if not ctx.view['power.enabled']:
            raise _defined('POWER_OFF', 'the Flex is switched off.', 'run `chemlab run flex power on` first.')

    # ---------------------------------------------------------------- refusals the gate did not foresee
    def default_refusal(self, ctx, cid, response):
        code = (response.get('error') or {}).get('code')
        if code == 'ACCESS_DENIED':
            ctx.refresh()
            try:
                self.check_not_busy(ctx, run_command=cid.startswith('run.'))
            except DefinedExecutionError as exc:
                return exc
            return _defined('ACCESS_DENIED', 'the instrument refused %s for your identity.' % cid, 'check `chemlab status flex`; another user may hold it.')
        if code in ('PRECONDITION_FAILED', 'BUSY'):
            return _defined('DEVICE_REFUSED', 'the instrument refused %s (%s).' % (cid, code),
                            'check `chemlab status flex` and the verb help; the state may differ from what you expect.')
        if code == 'INVALID_ARGUMENT':
            return ValidationError('BACKEND_INVALID_ARGUMENT', 'the instrument rejected the arguments of %s.' % cid, 'check the values against the verb help.')
        return UndefinedExecutionError('BACKEND_' + str(code), 'the instrument call %s failed (%s: %s).' % (cid, code, (response.get('error') or {}).get('message')),
                                       'check `chemlab status flex` before going on; the effect of the call is not known.', changed='unknown')

    # ---------------------------------------------------------------- primitives (shared by verbs and transfer)
    def move(self, ctx, loc):
        pos = {k: ctx.view['motion.' + k] for k in ('x_m', 'y_m', 'z_m')}
        if _same(pos, loc.position):
            return False
        ctx.call('motion.move', dict(loc.position), 'move to %s' % loc.name,
                 readback=lambda v: None if _same({k: v['motion.' + k] for k in ('x_m', 'y_m', 'z_m')}, loc.position) else 'a different position')
        return True

    def need_tip(self, ctx, doing):
        tip = ctx.view['tip.attached_id']
        if tip is None:
            raise _defined('NO_TIP', 'no tip is attached, so the pipette cannot %s.%s' % (doing, self._lost_tip_note(ctx)),
                           'run `chemlab run flex pick-up-tip` first.')
        return tip

    def pick(self, ctx, tip=None, rack=None):
        if ctx.view['tip.attached_id'] is not None:
            raise _defined('TIP_ALREADY_ATTACHED', 'a tip (%s) is already attached.' % self._tip_name(ctx.view['tip.attached_id']),
                           'drop it (`drop-tip`) or put it back (`return-tip`) first.')
        if tip is not None:
            present = dict((w, t) for w, t in self.rack_tips(ctx, tip.labware['name']))
            if tip.well not in present:
                nxt = self.rack_tips(ctx, tip.labware['name'])
                raise _defined('NO_TIP_AT_LOCATION', 'there is no tip at %s (already used).' % tip.name,
                               ('the next tip in %s is at %s:%s.' % (tip.labware['name'], tip.labware['name'], nxt[0][0])) if nxt else
                               '%s is empty; use another rack: %s.' % (tip.labware['name'], ', '.join(self.tip_racks())))
            tip_id, loc = present[tip.well], tip
        else:
            racks = [rack] if rack else self.tip_racks()
            avail = [(r, self.rack_tips(ctx, r)) for r in racks]
            avail = [(r, t) for r, t in avail if t]
            if not avail:
                others = [r for r in self.tip_racks() if r not in racks and self.rack_tips(ctx, r)]
                raise _defined('RACK_EMPTY' if rack else 'OUT_OF_TIPS', 'no tips left in %s.' % ', '.join(racks),
                               ('use another rack: --rack %s.' % others[0]) if others else 'ask the operator to refill the tip racks.')
            r, tips = avail[0]
            well, tip_id = tips[0]
            loc = self.parse_location('%s:%s' % (r, well), ('tip',))
        self.move(ctx, loc)
        ctx.call('tip.attach', {'tip_id': tip_id}, 'pick up tip %s' % loc.name,
                 readback=lambda v: None if v['tip.attached_id'] == tip_id else 'attached tip %r' % v['tip.attached_id'])
        if not ctx.dry_run:
            self.own_events.add(len(ctx.view['tip.events']) - 1)
        led = ctx.shadow['ledger'] if ctx.dry_run else self.tip_ledger
        led.update(tip_id=tip_id, volume_m3=0.0)
        return tip_id, loc

    def drop(self, ctx, to=None):
        tip = self.need_tip(ctx, 'drop a tip')
        led = self.ledger(ctx)
        if to is None or to.kind == 'trash':
            slot = self.reg['trash']['slot']
            fixture = (ctx.view.get('deck.fixtures') or {}).get(slot)
            if fixture not in TRASH_FIXTURES:
                raise _defined('NO_TRASH', 'no trash bin is configured in slot %s (deck shows %s).' % (slot, fixture or 'nothing'),
                               'configure it (`chemlab run flex deck-config %s trash_bin`) or return the tip to a rack.' % slot)
            target = Loc('trash', 'trash', self.reg['trash']['position_m'], slot=slot)
            ret = None
        else:
            occupied = dict(self.rack_tips(ctx, to.labware['name']))
            if to.well in occupied:
                raise _defined('WELL_OCCUPIED', '%s already holds a tip.' % to.name, 'choose an empty rack well, or drop the tip in the trash.')
            target, slot, ret = to, to.slot, (to.labware['name'], to.well)
        self.move(ctx, target)
        ctx.call('tip.release', {'target_slot_id': slot}, 'drop tip %s %s' % (self._tip_name(tip), 'in the trash' if ret is None else 'into ' + target.name),
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

    def check_volume(self, v_m3, text):
        if not self.vmin - 1e-18 <= v_m3 <= self.vmax + 1e-18:
            raise ValidationError('VOLUME_OUT_OF_RANGE', '%s is outside the pipette range %s-%s.' % (text, fmt_ul(self.vmin), fmt_ul(self.vmax)),
                                  'use %s to %s per aspirate/dispense; for more, use several steps (`transfer` splits volumes for you).'
                                  % (fmt_ul(self.vmin), fmt_ul(self.vmax)))

    def aspirate(self, ctx, loc, v_m3):
        tip = self.need_tip(ctx, 'aspirate')
        led = self.ledger(ctx)
        cap = self.tip_capacity(tip)
        if led['volume_m3'] is not None and cap is not None and _nl(led['volume_m3']) + _nl(v_m3) > _nl(cap):
            room = cap - led['volume_m3']
            raise _defined('TIP_CAPACITY', 'the attached %s tip holds %s already; %s more would not fit (room for %s).' % (
                fmt_ul(cap), fmt_ul(led['volume_m3']), fmt_ul(v_m3), fmt_ul(room)),
                ('aspirate at most %s now, or dispense first, or use a bigger tip; `transfer` splits volumes over the tip size.' % fmt_ul(room))
                if room >= self.vmin else 'the tip is full: dispense first, or use a bigger tip; `transfer` splits volumes over the tip size.',
                detail={'tip_capacity_ul': ul(cap), 'room_ul': ul(room)})
        self.move(ctx, loc)
        before = ctx.view['sample.transfer_count']

        def refused(resp):
            if (resp.get('error') or {}).get('code') == 'PRECONDITION_FAILED':
                return _defined('INSUFFICIENT_LIQUID', 'the instrument refused to aspirate %s from %s: the well does not hold that much liquid '
                                '(a run reports this as "no liquid detected").' % (fmt_ul(v_m3), loc.name),
                                'aspirate less, or take the rest from another source. This twin has no liquid-level sensing, so the remaining '
                                'volume is not reported.')
            return None
        ctx.call('sample.aspirate', {'container_id': loc.container_id, 'volume_m3': v_m3}, 'aspirate %s from %s' % (fmt_ul(v_m3), loc.name),
                 refusal=refused, readback=lambda v: None if v['sample.transfer_count'] == before + 1 else 'no new transfer')
        if led['volume_m3'] is not None:
            led['volume_m3'] = _m3(_nl(led['volume_m3']) + _nl(v_m3))

    def dispense(self, ctx, loc, v_m3):
        tip = self.need_tip(ctx, 'dispense')
        led = self.ledger(ctx)
        if led['volume_m3'] is not None and _nl(v_m3) > _nl(led['volume_m3']):
            raise _defined('TIP_HOLDS_LESS', 'the tip holds only %s; it cannot dispense %s.' % (fmt_ul(led['volume_m3']), fmt_ul(v_m3)),
                           'dispense at most %s, or aspirate more first.' % fmt_ul(led['volume_m3']))
        self.move(ctx, loc)
        before = ctx.view['sample.transfer_count']

        def refused(resp):
            if (resp.get('error') or {}).get('code') == 'PRECONDITION_FAILED':
                return _defined('DEST_FULL', 'the instrument refused to dispense %s into %s: the well would overflow (capacity %s).' % (
                    fmt_ul(v_m3), loc.name, fmt_ul(loc.capacity_m3)), 'dispense less, or into another well.')
            return None
        ctx.call('sample.dispense', {'container_id': loc.container_id, 'volume_m3': v_m3}, 'dispense %s into %s' % (fmt_ul(v_m3), loc.name),
                 refusal=refused, readback=lambda v: None if v['sample.transfer_count'] == before + 1 else 'no new transfer')
        if led['volume_m3'] is not None:
            led['volume_m3'] = _m3(max(0, _nl(led['volume_m3']) - _nl(v_m3)))
        _ = tip

    # ---------------------------------------------------------------- describe / help
    def describe(self, show_all=False):
        work = [v for v in self.verbs.values() if v.group == 'work']
        admin = [v for v in self.verbs.values() if v.group == 'admin']
        lines = ['flex: %s' % self.title, '',
                 'units: volumes in uL (also mL, nL; a bare number is uL). Times are simulated seconds.',
                 'locations: LABWARE:WELL (plate:A1), lists (plate:A1,plate:B1), ranges (plate:A1-A6), trash, home.',
                 '           see `chemlab run flex labware` for what is on the deck.',
                 'errors: error[CLASS/CODE] + fix + whether the instrument changed; exit 2 invalid, 3 refused, 4 unexpected.',
                 'add --dry-run to any verb to check it without moving anything; --help for details.', '', 'verbs:']
        lines += ['  %-14s %s' % (v.name, v.summary) for v in work]
        if show_all:
            lines += ['', 'maintenance and admin verbs (destructive ones need --yes):']
            lines += ['  %-18s %s' % (v.name, v.summary) for v in admin]
            lines += ['', 'not offered (instrument capabilities without a verb):']
            lines += ['  %-22s %s' % (cid, why) for cid, why in sorted(self.contract.hidden().items())]
        else:
            lines += ['', 'more: `chemlab describe flex --all` (maintenance/admin verbs), `chemlab help flex VERB`.']
        js = {'instrument': self.name, 'title': self.title, 'units': {'volume': 'uL'},
              'verbs': [{'name': v.name, 'group': v.group, 'summary': v.summary, 'usage': v.usage(self.name), 'maps_to': v.maps_to,
                         'destructive': v.destructive, 'preconditions': v.preconditions, 'errors': v.errors}
                        for v in (work + admin if show_all else work)]}
        if show_all:
            js['not_offered'] = self.contract.hidden()
        return {'text': '\n'.join(lines) + '\n', 'json': js}

    def help(self, args):
        if not args or args[0] != self.name:
            if args and args[0] in self.verbs:
                return self.verbs[args[0]].help_text(self.name)
            return 'usage: chemlab help flex [VERB|admin]\n'
        if len(args) == 1:
            return self.describe()['text']
        if args[1] == 'admin':
            admin = [v for v in self.verbs.values() if v.group == 'admin']
            return 'maintenance and admin verbs (destructive ones need --yes; without it they only show what they would do):\n' + \
                ''.join('  %s\n      %s\n' % (v.usage(self.name), v.summary) for v in admin)
        verb = self.verbs.get(args[1])
        if verb is None:
            raise self.unknown_verb(args[1])
        return verb.help_text(self.name)

    def unknown_verb(self, name):
        if name in UNSUPPORTED:
            cid, why, fix = UNSUPPORTED[name]
            return ValidationError('NOT_SUPPORTED', '%s: %s%s.' % (name, why, ' (%s: %s)' % (cid, self.contract.capabilities[cid].availability) if cid else ''),
                                   fix + '.')
        close = difflib.get_close_matches(name, list(self.verbs), 2)
        return ValidationError('UNKNOWN_VERB', 'flex has no verb %r.' % name,
                               ('did you mean %s? ' % ' or '.join(close) if close else '') + 'see `chemlab describe flex`.')

    # ---------------------------------------------------------------- status views
    def status(self, ctx):
        v = ctx.view
        led = self.ledger(ctx)
        tip = v['tip.attached_id']
        tip_text = 'none'
        if tip:
            held = 'unknown contents' if led['volume_m3'] is None else 'holding %s' % fmt_ul(led['volume_m3'])
            tip_text = '%s (%s tip), %s' % (self._tip_name(tip), fmt_ul(self.tip_capacity(tip)) if self.tip_capacity(tip) else '?', held)
        rs = v.get('run.state')
        run_text, busy = 'none', False
        if rs:
            job = self._job_for_run(rs)
            who = ('your %s' % job['job']) if job and job['caller'] == ctx.caller else ('%s by %r' % (job['job'], job['caller']) if job else 'another user')
            run_text = 'protocol %r (%s) %s' % (rs['name'], who, JOB_STATUS.get(rs['status'], rs['status']))
            if rs['status'] in ACTIVE:
                busy = True
                left = self.eta(rs, v['_time_s'])
                run_text += ', step %d/%d, about %s left%s' % (min(rs['index'] + 1, rs['n_steps']), rs['n_steps'], fmt_s(left), self.wall(left))
        mounts = v.get('instruments.mounts') or {}
        left = mounts.get('left')
        pip = ('left: %s, %s' % (left['model'], 'calibrated' if left['calibrated'] else 'NOT calibrated')) if left else 'no pipette on the left mount'
        jobs = ['%s %s' % (j['job'], self._job_state(ctx, j)['status']) for j in self.jobs.values() if j['caller'] == ctx.caller]
        lines = ['flex  (sim time %.1f s)' % v['_time_s'],
                 'power: %s · robot: %s · lights: %s' % ('on' if v['power.enabled'] else 'off', v.get('robot.status'), 'on' if v.get('robot.lights_on') else 'off'),
                 'pipette: %s' % pip, 'position: %s' % self.position_name(v), 'tip: %s' % tip_text, 'run: %s' % run_text,
                 'jobs: %s' % (', '.join(jobs) or 'none'), 'lock: %s' % self.core.lock.view(ctx.caller)]
        note = self._lost_tip_note(ctx) if not tip else ''
        if note:
            lines.append('note:' + note)
        if getattr(self.backend, 'clock_errors', None):
            lines.append('WARNING: the simulation clock hit an error (%s); runs may be stuck - report this.' % self.backend.clock_errors[-1])
        js = {'power': v['power.enabled'], 'robot_status': v.get('robot.status'), 'busy': busy, 'lights': v.get('robot.lights_on'),
              'position': self.position_name(v), 'tip': None if not tip else {'tip': self._tip_name(tip), 'capacity_ul': ul(self.tip_capacity(tip) or 0),
                                                                              'volume_ul': None if led['volume_m3'] is None else ul(led['volume_m3'])},
              'run': None if not rs else {'name': rs['name'], 'status': JOB_STATUS.get(rs['status'], rs['status']), 'step': rs['index'], 'n_steps': rs['n_steps'],
                                          'eta_s': self.eta(rs, v['_time_s']) if rs['status'] in ACTIVE else 0.0},
              'jobs': {j['job']: self._job_state(ctx, j)['status'] for j in self.jobs.values() if j['caller'] == ctx.caller},
              'pipette': left, 'sim_time_s': v['_time_s'], 'lock': self.core.lock.view(ctx.caller)}
        return {'text': '\n'.join(lines) + '\n', 'json': js}

    def _job_state(self, ctx, j):
        rs = ctx.view.get('run.state')
        if rs and self._job_for_run(rs) is j:
            st = JOB_STATUS.get(rs['status'], rs['status'])
            out = {'job': j['job'], 'status': st, 'protocol_id': rs['protocol_id'], 'name': rs['name'], 'step': rs['index'], 'n_steps': rs['n_steps'],
                   'current_step': self.names(rs.get('current_step')) or (('finishing (last step: %s)' % rs['steps'][-1]) if rs.get('steps') and rs['status'] in ('running', 'paused') else None),
                   'eta_s': self.eta(rs, ctx.view['_time_s']) if rs['status'] in ACTIVE else 0.0, 'started_s': rs.get('started_s'),
                   'ended_s': rs.get('ended_s'), 'error': rs.get('error'), 'recovery_options': rs.get('recovery_options') or []}
            return out
        for h in reversed(ctx.view.get('run.history') or []):
            if h['protocol_id'] == j['protocol_id'] and h['started_s'] == j['started_s']:
                return {'job': j['job'], 'status': JOB_STATUS.get(h['status'], h['status']), 'protocol_id': h['protocol_id'], 'name': h['name'],
                        'step': h['steps_done'], 'n_steps': h['n_steps'], 'current_step': None, 'eta_s': 0.0, 'started_s': h['started_s'],
                        'ended_s': h['ended_s'], 'error': (h['errors'] or [None])[-1], 'recovery_options': []}
        return {'job': j['job'], 'status': 'unknown', 'protocol_id': j['protocol_id'], 'name': j['name'], 'step': None, 'n_steps': None,
                'current_step': None, 'eta_s': None, 'started_s': j['started_s'], 'ended_s': None, 'error': None, 'recovery_options': []}

    def get_job(self, ctx, job_id):
        j = self.jobs.get(job_id)
        if j is None:
            raise ValidationError('UNKNOWN_JOB', 'there is no job %r.' % job_id, 'your jobs: %s.' % (
                ', '.join(k for k, x in self.jobs.items() if x['caller'] == ctx.caller) or 'none'))
        if j['caller'] != ctx.caller:
            raise ValidationError('NOT_YOUR_JOB', '%s belongs to %r.' % (job_id, j['caller']), 'you can only control your own jobs.')
        return j

    def job_status(self, ctx, job_id):
        j = self.get_job(ctx, job_id)
        s = self._job_state(ctx, j)
        st = s['status']
        if st in ('running', 'paused', 'setup'):
            text = '%s %s · protocol %r · step %d/%d%s · about %s left%s' % (
                j['job'], st, s['name'], min(s['step'] + 1, s['n_steps']), s['n_steps'], (': ' + s['current_step']) if s['current_step'] else '',
                fmt_s(s['eta_s']), self.wall(s['eta_s']))
        elif st == 'awaiting_recovery':
            e = s['error'] or {}
            text = '%s needs recovery: error %s at step %d (%s).\noptions: %s\nfix: chemlab run flex recover %s --action ACTION' % (
                j['job'], e.get('type'), (e.get('index') or 0) + 1, e.get('step'), ', '.join(s['recovery_options']), j['job'])
        elif st == 'completed':
            text = '%s completed · protocol %r · %d/%d steps · ended at t=%.1f s' % (j['job'], s['name'], s['step'], s['n_steps'], s['ended_s'])
        else:
            text = '%s %s · protocol %r%s' % (j['job'], st, s['name'], (' · last error: %s' % s['error']['type']) if s.get('error') else '')
        return {'text': text + '\n', 'json': s}


# ==================================================================== verb handlers
def build_verbs(f):
    V = {}

    def verb(*a, **k):
        v = Verb(*a, **k)
        V[v.name] = v
        return v

    # ---- power / home / status / labware / lights
    def h_power(ctx, pos, opts):
        state = pos[0].lower()
        if state not in ('on', 'off'):
            raise ValidationError('BAD_CHOICE', 'power takes on or off, not %r.' % pos[0], 'write `power on` or `power off`.')
        if state == 'off':
            rs = ctx.view.get('run.state')
            job = f._job_for_run(rs) if rs else None
            if rs and rs['status'] in ('running', 'paused', 'awaiting_recovery'):
                if job is None or job['caller'] != ctx.caller:
                    f.check_not_busy(ctx, run_command=True)
                elif not ctx.flags.get('force'):
                    raise _defined('BUSY_OWN_JOB', 'switching off now would abort your %s (protocol %r).' % (job['job'], rs['name']),
                                   'wait for it, cancel it (`chemlab cancel %s`), or add --force to abort it by power-off.' % job['job'])
        want = state == 'on'
        if ctx.view['power.enabled'] == want:
            return {'text': 'ok: power is already %s\n' % state, 'json': {'power': want}, 'checks': ['power already %s' % state]}
        ctx.call('power.set', {'enabled': want}, 'power %s' % state,
                 readback=lambda v: None if v['power.enabled'] == want else 'power %s' % v['power.enabled'])
        return {'text': 'ok: power %s\n' % state, 'json': {'power': want}}
    verb('power', 'work', 'switch the robot on or off', h_power, positionals=[('on|off', 'on or off', True)], options={'force': Opt('with off: abort your own running job', 'flag')},
         maps_to=['power.set'], preconditions=['off is refused while a run is active (your own job: needs --force; another user\'s run: refused)'],
         errors=['BUSY, BUSY_OWN_JOB (defined)'], example='chemlab run flex power on', reference='Flex manual, robot power switch; HTTP API /health')

    def h_home(ctx, pos, opts):
        f.check_not_busy(ctx)
        f.check_power(ctx)
        home = f.reg['home_m']
        ctx.call('robot.home', {}, 'home the gantry', readback=lambda v: None if _same({k: v['motion.' + k] for k in home}, home) else 'not at home')
        return {'text': 'ok: gantry homed\n', 'json': {'position': 'home'}, 'checks': ['power on', 'not busy']}
    verb('home', 'work', 'home the gantry (back right, Z up)', h_home, maps_to=['robot.home'], preconditions=['power on', 'no run active'],
         errors=['POWER_OFF, BUSY (defined)'], example='chemlab run flex home', reference='Python API ProtocolContext.home(); HTTP API POST /robot/home')

    def h_status(ctx, pos, opts):
        return f.status(ctx)
    verb('status', 'work', 'power, position, tip and its contents, active run, jobs', h_status, readonly=True,
         example='chemlab run flex status --json')

    def h_labware(ctx, pos, opts):
        lines, js = ['deck (slot, labware, wells, capacity):'], []
        for lw in f.reg['labware']:
            if lw['kind'] == 'tiprack':
                tips = f.rack_tips(ctx, lw['name'])
                extra = '%s tips; %d left%s' % (fmt_ul(lw['tip_capacity_m3']), len(tips), (', next %s:%s' % (lw['name'], tips[0][0])) if tips else '')
                js.append({'name': lw['name'], 'slot': lw['slot'], 'kind': 'tiprack', 'type': lw['type'], 'tip_capacity_ul': ul(lw['tip_capacity_m3']),
                           'tips_left': len(tips), 'next_tip': ('%s:%s' % (lw['name'], tips[0][0])) if tips else None})
            else:
                extra = 'wells %s, %s each' % (f._well_span(lw['name']), fmt_ul(lw['well_capacity_m3']))
                js.append({'name': lw['name'], 'slot': lw['slot'], 'kind': lw['kind'], 'type': lw['type'], 'wells': f._well_span(lw['name']),
                           'well_capacity_ul': ul(lw['well_capacity_m3'])})
            lines.append('  %-3s %-12s %-10s %s (%s)' % (lw['slot'], lw['name'], lw['kind'], extra, lw['display']))
        t = f.reg['trash']
        fixture = (ctx.view.get('deck.fixtures') or {}).get(t['slot'])
        lines.append('  %-3s %-12s %-10s %s' % (t['slot'], 'trash', 'fixture', fixture or 'NOT configured'))
        lines.append('liquid contents are not tracked by the instrument; your task notes say what is in each well.')
        return {'text': '\n'.join(lines) + '\n', 'json': {'labware': js, 'trash': {'slot': t['slot'], 'fixture': fixture}}}
    verb('labware', 'work', 'what is on the deck: labware names, slots, wells, capacities, tips left', h_labware, readonly=True,
         example='chemlab run flex labware', reference='Python API Labware; Flex manual, labware concepts')

    def h_lights(ctx, pos, opts):
        state = pos[0].lower()
        if state not in ('on', 'off'):
            raise ValidationError('BAD_CHOICE', 'lights takes on or off.', 'write `lights on` or `lights off`.')
        f.check_power(ctx)
        ctx.call('robot.set_lights', {'on': state == 'on'}, 'lights %s' % state,
                 readback=lambda v: None if v['robot.lights_on'] == (state == 'on') else 'lights %s' % v['robot.lights_on'])
        return {'text': 'ok: deck lights %s\n' % state, 'json': {'lights': state == 'on'}, 'checks': ['power on']}
    verb('lights', 'work', 'deck lights on or off', h_lights, positionals=[('on|off', 'on or off', True)], maps_to=['robot.set_lights'],
         preconditions=['power on'], errors=['POWER_OFF (defined)'], example='chemlab run flex lights on', reference='HTTP API POST /robot/lights')

    # ---- motion and tips
    def h_move(ctx, pos, opts):
        loc = f.parse_location(pos[0], ('liquid', 'tip', 'trash', 'home'))
        f.check_not_busy(ctx)
        f.check_power(ctx)
        moved = f.move(ctx, loc)
        return {'text': 'ok: %s %s\n' % ('moved to' if moved else 'already at', loc.name), 'json': {'position': loc.name}, 'checks': ['power on', 'not busy']}
    verb('move-to', 'work', 'move the pipette to a named location', h_move, positionals=[('LOCATION', 'LABWARE:WELL, trash or home', True)],
         maps_to=['motion.move'], preconditions=['power on', 'no run active', 'location is on the deck'],
         errors=['UNKNOWN_LOCATION/UNKNOWN_LABWARE/UNKNOWN_WELL (validation)', 'POWER_OFF, BUSY (defined)'], example='chemlab run flex move-to plate:A1',
         reference='Python API InstrumentContext.move_to()')

    def h_pick(ctx, pos, opts):
        if opts.get('tip') and opts.get('rack'):
            raise ValidationError('CONFLICTING_OPTIONS', 'give --tip or --rack, not both.', 'use one of them.')
        tip = f.parse_location(opts['tip'], ('tip',), '--tip') if opts.get('tip') else None
        rack = opts.get('rack')
        if rack and (rack not in f.labware or f.labware[rack]['kind'] != 'tiprack'):
            raise ValidationError('UNKNOWN_RACK', 'there is no tip rack %r.' % rack, 'tip racks here: %s.' % ', '.join(f.tip_racks()))
        f.check_not_busy(ctx)
        f.check_power(ctx)
        tip_id, loc = f.pick(ctx, tip, rack)
        cap = f.tip_capacity(tip_id)
        return {'text': 'ok: picked up tip %s (%s)\n' % (loc.name, fmt_ul(cap)), 'json': {'tip': loc.name, 'capacity_ul': ul(cap)},
                'checks': ['power on', 'no tip attached', 'tip present at %s' % loc.name]}
    verb('pick-up-tip', 'work', 'pick up a tip (default: the next unused tip)', h_pick,
         options={'tip': Opt('a specific tip-rack well', metavar='RACK:WELL'), 'rack': Opt('take the next tip from this rack', metavar='RACK')},
         maps_to=['motion.move', 'tip.attach'], preconditions=['power on', 'no run active', 'no tip attached', 'a tip is present'],
         errors=['TIP_ALREADY_ATTACHED, NO_TIP_AT_LOCATION, RACK_EMPTY, OUT_OF_TIPS, POWER_OFF, BUSY (defined)'],
         example='chemlab run flex pick-up-tip --rack tiprack200', reference='Python API InstrumentContext.pick_up_tip()')

    def h_drop(ctx, pos, opts):
        to = None
        if opts.get('to') and opts['to'] != 'trash':
            to = f.parse_location(opts['to'], ('tip', 'trash'), '--to')
        f.check_not_busy(ctx)
        f.check_power(ctx)
        tip, target = f.drop(ctx, to)
        return {'text': 'ok: dropped tip %s %s\n' % (f._tip_name(tip), 'in the trash' if target.kind == 'trash' else 'into ' + target.name),
                'json': {'tip': f._tip_name(tip), 'to': target.name}, 'checks': ['power on', 'tip attached']}
    verb('drop-tip', 'work', 'drop the tip (default: in the trash)', h_drop, options={'to': Opt('trash or an empty tip-rack well', metavar='trash|RACK:WELL')},
         maps_to=['motion.move', 'tip.release'], preconditions=['power on', 'no run active', 'a tip is attached', 'trash bin configured (for trash)'],
         errors=['NO_TIP, NO_TRASH, WELL_OCCUPIED, POWER_OFF, BUSY (defined)'], example='chemlab run flex drop-tip',
         reference='Python API InstrumentContext.drop_tip(); Flex manual, deck fixtures (trash bin)')

    def h_return(ctx, pos, opts):
        tip = f.need_tip(ctx, 'return a tip')
        home = f.tip_home.get(tip)
        if home is None:
            raise _defined('NO_HOME_WELL', 'the attached tip has no rack well to go back to.', 'drop it in the trash instead (`drop-tip`).')
        f.check_not_busy(ctx)
        f.check_power(ctx)
        loc = f.parse_location('%s:%s' % home, ('tip',))
        f.drop(ctx, loc)
        return {'text': 'ok: returned tip to %s\n' % loc.name, 'json': {'tip': loc.name}, 'checks': ['power on', 'tip attached', '%s empty' % loc.name]}
    verb('return-tip', 'work', 'put the attached tip back where it came from', h_return, maps_to=['motion.move', 'tip.release'],
         preconditions=['power on', 'no run active', 'a tip is attached'], errors=['NO_TIP, WELL_OCCUPIED (defined)'],
         example='chemlab run flex return-tip', reference='Python API InstrumentContext.return_tip()')

    # ---- liquid handling
    def _vol(text):
        m3, v_ul = parse_volume(text)
        f.check_volume(m3, '%s' % fmt_ul(m3))
        return m3

    def h_aspirate(ctx, pos, opts):
        v = _vol(opts['volume'])
        loc = f.parse_location(opts['from'], ('liquid',), '--from')
        f.check_not_busy(ctx)
        f.check_power(ctx)
        f.aspirate(ctx, loc, v)
        led = f.ledger(ctx)
        held = '' if led['volume_m3'] is None else ' (tip now holds %s)' % fmt_ul(led['volume_m3'])
        return {'text': 'ok: aspirated %s from %s%s\n' % (fmt_ul(v), loc.name, held), 'json': {'aspirated_ul': ul(v), 'from': loc.name,
                'tip_volume_ul': None if led['volume_m3'] is None else ul(led['volume_m3'])},
                'checks': ['power on', 'tip attached', '%s within %s-%s' % (fmt_ul(v), fmt_ul(f.vmin), fmt_ul(f.vmax)), 'fits in the tip'],
                'unchecked': ['liquid available in %s' % loc.name]}
    verb('aspirate', 'work', 'draw liquid into the attached tip', h_aspirate,
         options={'volume': Opt('volume, uL (5-1000)', metavar='V', required=True), 'from': Opt('source well', metavar='LABWARE:WELL', required=True)},
         maps_to=['motion.move', 'sample.aspirate'], preconditions=['volume within the pipette range 5-1000 uL', 'power on', 'no run active', 'a tip is attached',
                                                                    'the volume fits in the tip (tip size minus what it holds)'],
         errors=['VOLUME_OUT_OF_RANGE, UNIT_NOT_ALLOWED, UNKNOWN_* (validation)', 'NO_TIP, TIP_CAPACITY, INSUFFICIENT_LIQUID, POWER_OFF, BUSY (defined)'],
         example='chemlab run flex aspirate --volume 50uL --from reservoir:A1', reference='Python API InstrumentContext.aspirate()')

    def h_dispense(ctx, pos, opts):
        v = _vol(opts['volume'])
        loc = f.parse_location(opts['to'], ('liquid',), '--to')
        f.check_not_busy(ctx)
        f.check_power(ctx)
        f.dispense(ctx, loc, v)
        led = f.ledger(ctx)
        held = '' if led['volume_m3'] is None else ' (tip now holds %s)' % fmt_ul(led['volume_m3'])
        return {'text': 'ok: dispensed %s into %s%s\n' % (fmt_ul(v), loc.name, held), 'json': {'dispensed_ul': ul(v), 'to': loc.name,
                'tip_volume_ul': None if led['volume_m3'] is None else ul(led['volume_m3'])},
                'checks': ['power on', 'tip attached', 'tip holds enough'], 'unchecked': ['room in %s' % loc.name]}
    verb('dispense', 'work', 'push liquid out of the tip into a well', h_dispense,
         options={'volume': Opt('volume, uL (5-1000)', metavar='V', required=True), 'to': Opt('destination well', metavar='LABWARE:WELL', required=True)},
         maps_to=['motion.move', 'sample.dispense'], preconditions=['volume within 5-1000 uL', 'power on', 'no run active', 'a tip is attached', 'the tip holds that much'],
         errors=['VOLUME_OUT_OF_RANGE (validation)', 'NO_TIP, TIP_HOLDS_LESS, DEST_FULL, POWER_OFF, BUSY (defined)'],
         example='chemlab run flex dispense --volume 50uL --to plate:A1', reference='Python API InstrumentContext.dispense()')

    def h_transfer(ctx, pos, opts):
        srcs = f.parse_locations(opts['from'], ('liquid',), '--from')
        dsts = f.parse_locations(opts['to'], ('liquid',), '--to')
        vols = [parse_volume(x)[0] for x in opts['volume'].split(',') if x.strip()]
        if len(srcs) == len(dsts):
            pairs = list(zip(srcs, dsts))
        elif len(srcs) == 1:
            pairs = [(srcs[0], d) for d in dsts]
        elif len(dsts) == 1:
            pairs = [(s, dsts[0]) for s in srcs]
        else:
            raise ValidationError('PAIRING', '%d sources and %d destinations cannot be paired.' % (len(srcs), len(dsts)),
                                  'give equal counts, or one source, or one destination.')
        if len(vols) == 1:
            vols = vols * len(pairs)
        if len(vols) != len(pairs):
            raise ValidationError('PAIRING', '%d volumes for %d transfers.' % (len(vols), len(pairs)), 'give one volume, or one per transfer.')
        new_tip = opts.get('new-tip', 'once')
        rack = opts.get('rack')
        if rack and (rack not in f.labware or f.labware[rack]['kind'] != 'tiprack'):
            raise ValidationError('UNKNOWN_RACK', 'there is no tip rack %r.' % rack, 'tip racks here: %s.' % ', '.join(f.tip_racks()))
        f.check_not_busy(ctx)
        f.check_power(ctx)
        attached = ctx.view['tip.attached_id']
        if new_tip == 'never':
            if attached is None:
                raise _defined('NO_TIP', 'no tip is attached and --new-tip never was given.%s' % f._lost_tip_note(ctx),
                               'pick up a tip first, or use --new-tip once.')
            cap = f.tip_capacity(attached) or f.vmax
            racks_used = []
        else:
            if attached is not None:
                raise _defined('TIP_ALREADY_ATTACHED', 'a tip (%s) is already attached; --new-tip %s picks up a fresh one.' % (f._tip_name(attached), new_tip),
                               'drop or return it first, or use --new-tip never to keep using it.')
            racks = [rack] if rack else [r for r in f.tip_racks() if f.rack_tips(ctx, r)]
            if not racks or not f.rack_tips(ctx, racks[0]):
                raise _defined('OUT_OF_TIPS', 'no tips left%s.' % (' in %s' % rack if rack else ''), 'use another rack or ask the operator to refill.')
            cap = f.labware[racks[0]]['tip_capacity_m3']
            need = 1 if new_tip == 'once' else len(pairs)
            have = len(f.rack_tips(ctx, racks[0]))
            if have < need:
                raise _defined('OUT_OF_TIPS', '%s has %d tips left; this transfer needs %d.' % (racks[0], have, need), 'use another rack (--rack) or --new-tip once.')
            racks_used = racks[:1]
        trip = min(f.vmax, cap)
        plan = []
        for s, d, v in zip([p[0] for p in pairs], [p[1] for p in pairs], vols):
            n = max(1, math.ceil(_nl(v) / _nl(trip) - 1e-12))
            base, extra = divmod(_nl(v), n)
            chunks = [_m3(base + (1 if i < extra else 0)) for i in range(n)]
            for c in chunks:
                f.check_volume(c, '%s (one trip of %s)' % (fmt_ul(c), fmt_ul(v)))
            plan.append((s, d, v, chunks))
        tips_used = []
        for i, (s, d, v, chunks) in enumerate(plan):
            if new_tip == 'always' and i > 0:
                f.drop(ctx, None)
            if (new_tip == 'once' and i == 0) or new_tip == 'always':     # a tip lost on the way is reported, never silently replaced
                tip_id, _ = f.pick(ctx, None, racks_used[0])
                tips_used.append(f._tip_name(tip_id))
            for c in chunks:
                f.aspirate(ctx, s, c)
                f.dispense(ctx, d, c)
        if new_tip != 'never':
            f.drop(ctx, None)
        moved = ', '.join('%s %s -> %s%s' % (fmt_ul(v), s.name, d.name, (' (%d trips)' % len(ch)) if len(ch) > 1 else '') for s, d, v, ch in plan)
        return {'text': 'ok: transferred %s; tips: %s\n' % (moved, ', '.join(tips_used) or 'the attached tip'),
                'json': {'transfers': [{'from': s.name, 'to': d.name, 'volume_ul': ul(v), 'trips': len(ch)} for s, d, v, ch in plan], 'tips': tips_used},
                'checks': ['power on', 'not busy', 'volumes split to fit %s per trip' % fmt_ul(trip), 'enough tips'],
                'unchecked': ['liquid available in the sources', 'room in the destinations']}
    verb('transfer', 'work', 'move liquid between wells (picks tips, splits volumes over the tip size, drops tips)', h_transfer,
         options={'volume': Opt('volume per transfer, uL (one value or a comma list)', metavar='V', required=True),
                  'from': Opt('source well(s)', metavar='LOC[,LOC..]', required=True), 'to': Opt('destination well(s)', metavar='LOC[,LOC..]', required=True),
                  'new-tip': Opt('once: one tip for everything; always: a fresh tip for each source->destination pair; never: use the attached tip',
                                 choices=['once', 'always', 'never'], metavar='MODE'),
                  'rack': Opt('take tips from this rack', metavar='RACK')},
         maps_to=['motion.move', 'tip.attach', 'sample.aspirate', 'sample.dispense', 'tip.release'],
         preconditions=['all locations exist and are liquid wells', 'power on', 'no run active', 'tip state matches --new-tip', 'enough tips',
                        'every trip within the pipette range and tip size'],
         errors=['PAIRING, VOLUME_OUT_OF_RANGE (validation)', 'NO_TIP, TIP_ALREADY_ATTACHED, OUT_OF_TIPS, INSUFFICIENT_LIQUID, DEST_FULL, BUSY (defined)'],
         example='chemlab run flex transfer --volume 50 --from reservoir:A1 --to plate:A1-A6',
         reference='Python API InstrumentContext.transfer() (complex commands; new_tip once/always/never)',
         details='One source and many destinations, many sources and one destination, or equal lists (paired in order). Volumes larger than '
                 'one tip are split into equal trips. Tips picked up by transfer are dropped in the trash at the end (or between pairs with --new-tip always).')

    # ---- protocols and jobs
    def h_protocols(ctx, pos, opts):
        rows, js = [], []
        for p in ctx.view.get('protocols.list') or []:
            params = ', '.join('%s (%s%s, default %s)' % (x['name'], x.get('type'), (' %s-%s' % (x['min'], x['max'])) if 'min' in x else '',
                                                           x.get('default')) for x in p.get('parameters') or [])
            rows.append('  %-14s %-24s %3d steps  %s%s' % (p['protocol_id'], p['name'], p['n_steps'], 'ready' if p['ready'] else 'NOT ready',
                                                          ('  parameters: ' + params) if params else ''))
            js.append({k: p.get(k) for k in ('protocol_id', 'name', 'n_steps', 'ready', 'parameters', 'description', 'fixtures')})
        return {'text': ('stored protocols:\n' + '\n'.join(rows) + '\n') if rows else 'no stored protocols\n', 'json': {'protocols': js}}
    verb('protocols', 'work', 'list the protocols stored on the robot', h_protocols, readonly=True, example='chemlab run flex protocols',
         reference='HTTP API GET /protocols')

    def _param_value(par, text):
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
            raise ValidationError('BAD_PARAMETER', 'parameter %s takes a %s, not %r.' % (par['name'], t, text), 'give a %s value.' % t)
        if par.get('choices'):
            allowed = [c['value'] if isinstance(c, dict) else c for c in par['choices']]
            if v not in allowed:
                raise ValidationError('BAD_PARAMETER', 'parameter %s must be one of %s.' % (par['name'], allowed), 'pick one of them.')
        if t in ('int', 'float') and not (par.get('min', -math.inf) <= v <= par.get('max', math.inf)):
            raise ValidationError('PARAMETER_OUT_OF_RANGE', 'parameter %s=%s is outside %s-%s.' % (par['name'], text, par.get('min'), par.get('max')),
                                  'give a value from %s to %s.' % (par.get('min'), par.get('max')))
        return v

    def h_run_protocol(ctx, pos, opts):
        pid = pos[0]
        proto = next((p for p in ctx.view.get('protocols.list') or [] if p['protocol_id'] == pid), None)
        if proto is None:
            raise ValidationError('UNKNOWN_PROTOCOL', 'there is no stored protocol %r.' % pid, 'stored: %s (see `protocols`).' % (
                ', '.join(p['protocol_id'] for p in ctx.view.get('protocols.list') or []) or 'none'))
        params = {}
        for item in opts.get('param', []):
            name, eq, val = item.partition('=')
            par = next((x for x in proto.get('parameters') or [] if x['name'] == name), None)
            if not eq or par is None:
                raise ValidationError('BAD_PARAMETER', 'unknown parameter %r for %s.' % (item, pid), 'parameters: %s; write --param NAME=VALUE.' % (
                    ', '.join(x['name'] for x in proto.get('parameters') or []) or 'none'))
            params[name] = _param_value(par, val)
        f.check_not_busy(ctx, run_command=True)
        f.check_power(ctx)
        if ctx.view['tip.attached_id'] is not None:
            raise _defined('TIP_ATTACHED', 'a tip is attached; a protocol run starts with an empty pipette.', 'drop it (`drop-tip`) or return it (`return-tip`) first.')
        mounts = ctx.view.get('instruments.mounts') or {}
        m = mounts.get(proto.get('pipette_mount') or 'left') if isinstance(mounts, dict) else None
        if not m or not m.get('calibrated'):
            raise _defined('RUN_NOT_READY', 'protocol %s needs a calibrated pipette on the left mount.' % pid,
                           'attach and calibrate it (`chemlab help flex admin`).')
        fixtures = ctx.view.get('deck.fixtures') or {}
        conflicts = {s: fx for s, fx in (proto.get('fixtures') or {}).items() if fixtures.get(s) != fx}
        if conflicts and not opts.get('update-deck'):
            raise _defined('RUN_NOT_READY', 'protocol %s needs deck fixtures %s.' % (pid, ', '.join('%s=%s' % kv for kv in conflicts.items())),
                           'add --update-deck to set them, or use `deck-config`.')
        steps = [('run.setup', {'protocol_id': pid}, 'set up run of %s' % pid)]
        steps += [('run.set_parameter', {'name': k, 'value': v}, 'set %s=%s' % (k, v)) for k, v in params.items()]
        if proto.get('parameters'):
            steps.append(('run.confirm_parameters', {}, 'confirm parameters'))
        if conflicts:
            steps.append(('run.update_deck', {}, 'update deck fixtures'))
        if opts.get('apply-offsets'):
            steps.append(('run.apply_offsets', {}, 'apply stored labware offsets'))
        steps.append(('run.play', {}, 'start the run'))
        try:
            for cid, a, text in steps:
                ctx.call(cid, a, text)
        except ChemlabError as exc:
            if not ctx.dry_run and ctx.changed and (ctx.view.get('run.state') or {}).get('status') == 'setup':
                ctx.call('run.stop', {}, 'roll back the run setup')
                ctx.changed = False
                exc.changed, exc.done = 'no (the run setup was rolled back)', list(ctx.done)
            raise
        if ctx.dry_run:
            return {'text': '', 'json': {}, 'checks': ['protocol stored', 'parameters valid', 'power on', 'not busy', 'no tip attached', 'pipette calibrated', 'deck fixtures']}
        rs = ctx.view.get('run.state') or {}
        f.job_n += 1
        job = {'job': 'job-%d' % f.job_n, 'caller': ctx.caller, 'protocol_id': pid, 'name': proto['name'], 'started_s': rs.get('started_s'),
               'params': params}
        f.jobs[job['job']] = job
        left = f.eta(rs, ctx.view['_time_s']) if rs.get('status') in ACTIVE else 0.0
        return {'text': 'ok: %s started: protocol %r (%d steps, about %s of sim time%s)\nnext: `chemlab status %s` to follow it\n' % (
            job['job'], proto['name'], rs.get('n_steps', 0), fmt_s(left), f.wall(left), job['job']),
            'json': {'job': job['job'], 'protocol_id': pid, 'n_steps': rs.get('n_steps'), 'eta_s': left, 'status': JOB_STATUS.get(rs.get('status'), rs.get('status'))}}
    verb('run-protocol', 'work', 'start a stored protocol as a job (returns a job id)', h_run_protocol,
         positionals=[('PROTOCOL', 'protocol id (see `protocols`)', True)],
         options={'param': Opt('runtime parameter, repeatable', kind='multi', metavar='NAME=VALUE'), 'update-deck': Opt('set the deck fixtures the protocol needs', 'flag'),
                  'apply-offsets': Opt('apply the labware offsets stored from an earlier Labware Position Check', 'flag')},
         maps_to=['run.setup', 'run.set_parameter', 'run.confirm_parameters', 'run.update_deck', 'run.apply_offsets', 'run.play', 'run.stop'],
         preconditions=['protocol stored', 'parameters known and in range', 'power on', 'no other run active', 'no tip attached', 'pipette calibrated',
                        'deck fixtures match (or --update-deck)'],
         errors=['UNKNOWN_PROTOCOL, BAD_PARAMETER, PARAMETER_OUT_OF_RANGE (validation)', 'BUSY, TIP_ATTACHED, RUN_NOT_READY, POWER_OFF (defined)'],
         example='chemlab run flex run-protocol plate_prep --param volume_ul=80', reference='HTTP API POST /runs, POST /runs/{id}/actions (play)')

    def h_job(ctx, pos, opts):
        return f.job_status(ctx, pos[0])
    verb('job', 'work', 'progress of a job (same as `chemlab status JOB`)', h_job, positionals=[('JOB', 'job id', True)], readonly=True,
         example='chemlab run flex job job-1')

    def _job_action(action):
        def h(ctx, pos, opts):
            j = f.get_job(ctx, pos[0])
            s = f._job_state(ctx, j)
            st = s['status']
            need = {'pause': ('running',), 'resume': ('paused',), 'cancel': ('setup', 'running', 'paused', 'awaiting_recovery')}[action]
            if st not in need:
                raise _defined('JOB_FINISHED' if st in ('completed', 'failed', 'canceled') else 'JOB_NOT_%s' % need[0].upper(),
                               '%s is %s; cannot %s it.' % (j['job'], st, action), 'check `chemlab status %s`.' % j['job'])
            cid = {'pause': 'run.pause', 'resume': 'run.play', 'cancel': 'run.stop'}[action]
            ctx.call(cid, {}, '%s %s' % (action, j['job']))
            if not ctx.dry_run and action == 'pause':
                j['pause_t'] = ctx.view['_time_s']
            elif not ctx.dry_run and action == 'resume' and j.get('pause_t') is not None:
                j['paused_s'] = j.get('paused_s', 0.0) + ctx.view['_time_s'] - j.pop('pause_t')
            s = f._job_state(ctx, j) if not ctx.dry_run else s
            return {'text': 'ok: %s %s (now %s)\n' % (action, j['job'], s['status']), 'json': {'job': j['job'], 'status': s['status']},
                    'checks': ['%s is yours and %s' % (j['job'], st)]}
        return h
    for action, summary, cid in (('pause', 'pause a running job', 'run.pause'), ('resume', 'resume a paused job', 'run.play'),
                                 ('cancel', 'cancel a job (same as `chemlab cancel JOB`)', 'run.stop')):
        verb(action, 'work', summary, _job_action(action), positionals=[('JOB', 'job id', True)], maps_to=[cid],
             preconditions=['the job is yours', 'the job is in a state that allows it'], errors=['UNKNOWN_JOB, NOT_YOUR_JOB (validation)', 'JOB_FINISHED (defined)'],
             example='chemlab run flex %s job-1' % action, reference='HTTP API POST /runs/{id}/actions (pause, play, stop)')

    recovery_actions = f.contract.capabilities['run.recover'].input_schema['properties']['action']['enum']

    def h_recover(ctx, pos, opts):
        j = f.get_job(ctx, pos[0])
        s = f._job_state(ctx, j)
        if s['status'] != 'awaiting_recovery':
            raise _defined('NOT_AWAITING_RECOVERY', '%s is %s, not waiting for error recovery.' % (j['job'], s['status']), 'check `chemlab status %s`.' % j['job'])
        if opts['action'] not in s['recovery_options']:
            raise _defined('ACTION_NOT_ALLOWED', '%s is not offered for error %s.' % (opts['action'], (s['error'] or {}).get('type')),
                           'choose one of: %s.' % ', '.join(s['recovery_options']))
        ctx.call('run.recover', {'action': opts['action']}, 'recover %s with %s' % (j['job'], opts['action']))
        s = f._job_state(ctx, j) if not ctx.dry_run else s
        return {'text': 'ok: %s recovered with %s (now %s)\n' % (j['job'], opts['action'], s['status']), 'json': {'job': j['job'], 'status': s['status']}}
    verb('recover', 'work', 'answer the error-recovery question of a job (retry, skip, new tips, cancel...)', h_recover,
         positionals=[('JOB', 'job id', True)], options={'action': Opt('recovery action', choices=recovery_actions, metavar='ACTION', required=True)},
         maps_to=['run.recover'], preconditions=['the job waits for recovery', 'the action is offered for its error'],
         errors=['NOT_AWAITING_RECOVERY, ACTION_NOT_ALLOWED (defined)'], example='chemlab run flex recover job-1 --action retry_same_tips',
         reference='Flex manual, protocol run: error recovery; HTTP API /runs/{id}/actions (resume-from-recovery)')

    # ---- maintenance / admin
    def _simple(cid, args, text, check_busy=True, extra=None):
        def h(ctx, pos, opts):
            a = args(pos, opts) if callable(args) else dict(args)
            if check_busy:
                f.check_not_busy(ctx, run_command=True)
            f.check_power(ctx)
            if extra:
                extra(ctx, a)
            ctx.call(cid, a, text(a) if callable(text) else text)
            return {'text': 'ok: %s\n' % (text(a) if callable(text) else text), 'json': {'capability': cid, 'arguments': a}, 'checks': ['power on', 'not busy']}
        return h

    mounts = f.contract.capabilities['instrument.calibrate'].input_schema['properties']['mount']['enum']
    models = f.contract.capabilities['instrument.attach'].input_schema['properties']['model']['enum']

    def h_pipettes(ctx, pos, opts):
        m = ctx.view.get('instruments.mounts') or {}
        lines = ['  %-9s %s' % (k, ('%s, %s' % (v['model'], 'calibrated' if v['calibrated'] else 'NOT calibrated')) if v else 'empty') for k, v in m.items()]
        return {'text': 'mounts:\n' + '\n'.join(lines) + '\n', 'json': {'mounts': m}}
    verb('pipettes', 'admin', 'what is on each mount and whether it is calibrated', h_pipettes, readonly=True)

    def _need_mounted(ctx, a):
        if not (ctx.view.get('instruments.mounts') or {}).get(a['mount']):
            raise _defined('NO_PIPETTE', 'no pipette on the %s mount.' % a['mount'], 'attach one first (`attach-pipette`).')

    def _no_tip(ctx, a):
        _need_mounted(ctx, a)
        if ctx.view['tip.attached_id']:
            raise _defined('TIP_ATTACHED', 'a tip is attached.', 'drop it before detaching the pipette.')

    def _mount_free(ctx, a):
        if any((ctx.view.get('instruments.mounts') or {}).get(k) for k in ('left', 'right')):
            raise _defined('MOUNT_TAKEN', 'a pipette is already attached (this Flex has one 1-channel pipette).', 'detach it first.')

    verb('calibrate-pipette', 'admin', 'run the automatic pipette calibration', _simple('instrument.calibrate', lambda p, o: {'mount': o.get('mount', 'left')},
         lambda a: 'calibrated the %s pipette' % a['mount'], extra=_need_mounted),
         options={'mount': Opt('mount', choices=mounts, metavar='MOUNT')}, maps_to=['instrument.calibrate'],
         preconditions=['power on', 'no run active', 'a pipette on that mount'], reference='Flex manual, instrument installation and calibration')
    verb('attach-pipette', 'admin', 'record a pipette attached to a mount (needs calibration after)', _simple(
        'instrument.attach', lambda p, o: {'mount': o['mount'], 'model': o.get('model', models[0])}, lambda a: 'attached %s on the %s mount' % (a['model'], a['mount']),
        extra=_mount_free), options={'mount': Opt('mount', choices=mounts, metavar='MOUNT', required=True), 'model': Opt('model', choices=models, metavar='MODEL')},
         maps_to=['instrument.attach'], preconditions=['power on', 'no run active', 'mount free'])
    verb('detach-pipette', 'admin', 'record the pipette as removed (destructive: --yes)', _simple(
        'instrument.detach', lambda p, o: {'mount': o['mount']}, lambda a: 'detached the %s pipette' % a['mount'], extra=_no_tip),
         options={'mount': Opt('mount', choices=mounts, metavar='MOUNT', required=True)}, maps_to=['instrument.detach'], destructive=True,
         preconditions=['power on', 'no run active', 'no tip attached'])

    fixture_enum = f.contract.capabilities['deck.configure'].input_schema['properties']['fixture']['anyOf'][0]['enum']
    cutouts = f.contract.capabilities['deck.configure'].input_schema['properties']['slot']['enum']

    def h_deck(ctx, pos, opts):
        if not pos:
            fx = ctx.view.get('deck.fixtures') or {}
            return {'text': 'deck fixtures:\n' + ''.join('  %s  %s\n' % kv for kv in sorted(fx.items())), 'json': {'fixtures': fx}}
        if len(pos) != 2:
            raise ValidationError('MISSING_ARGUMENT', 'deck-config needs SLOT and FIXTURE (or none).', 'example: deck-config A1 trash_bin')
        slot, fixture = pos[0].upper(), pos[1]
        if slot not in cutouts:
            raise ValidationError('BAD_CHOICE', 'fixtures go in %s, not %s.' % (', '.join(cutouts), slot), 'pick one of those slots.')
        fixture = None if fixture == 'none' else fixture
        if fixture is not None and fixture not in fixture_enum:
            raise ValidationError('BAD_CHOICE', 'unknown fixture %r.' % pos[1], 'fixtures: %s, or none.' % ', '.join(fixture_enum))
        if fixture is not None and slot not in FIXTURE_SLOTS.get(fixture, ()):
            raise ValidationError('FIXTURE_NOT_ALLOWED', '%s cannot go in %s.' % (fixture, slot), '%s goes in %s.' % (fixture, ', '.join(FIXTURE_SLOTS[fixture])))
        f.check_not_busy(ctx, run_command=True)
        f.check_power(ctx)
        ctx.call('deck.configure', {'slot': slot, 'fixture': fixture}, 'set %s to %s' % (slot, fixture or 'empty'))
        return {'text': 'ok: %s is now %s\n' % (slot, fixture or 'empty'), 'json': {'slot': slot, 'fixture': fixture}}
    verb('deck-config', 'admin', 'show or change the deck fixtures (trash bin, staging area, waste chute)', h_deck,
         positionals=[('SLOT', 'cutout: ' + ', '.join(cutouts), False), ('FIXTURE', ', '.join(fixture_enum) + ' or none', False)], maps_to=['deck.configure'],
         preconditions=['fixture allowed in that slot (Flex deck fixtures table)', 'power on', 'no run active'], reference='Flex manual, deck configuration')

    keys = f.contract.capabilities['robot.configure'].input_schema['properties']['key']['enum']

    def h_settings(ctx, pos, opts):
        settings = ctx.view.get('robot.settings') or {}
        if not pos:
            return {'text': 'settings:\n' + ''.join('  %-24s %s\n' % kv for kv in sorted(settings.items())), 'json': {'settings': settings}}
        key = pos[0]
        if key not in keys:
            raise ValidationError('BAD_CHOICE', 'unknown setting %r.' % key, 'settings: %s.' % ', '.join(keys))
        if len(pos) == 1:
            return {'text': '%s = %s\n' % (key, settings.get(key)), 'json': {key: settings.get(key)}}
        raw = pos[1]
        allowed = SETTING_VALUES.get(key, [True, False])
        if allowed == [True, False]:
            if raw.lower() not in ('on', 'off', 'true', 'false'):
                raise ValidationError('BAD_CHOICE', '%s is on or off.' % key, 'write on or off.')
            value = raw.lower() in ('on', 'true')
        else:
            value = int(raw) if raw.isdigit() else raw
            if value not in allowed:
                raise ValidationError('BAD_CHOICE', '%s cannot be %r.' % (key, raw), 'allowed: %s.' % ', '.join(map(str, allowed)))
        f.check_not_busy(ctx, run_command=True)
        f.check_power(ctx)
        ctx.call('robot.configure', {'key': key, 'value': value}, 'set %s to %s' % (key, value))
        return {'text': 'ok: %s = %s\n' % (key, value), 'json': {key: value}}
    verb('settings', 'admin', 'show or change robot settings', h_settings, positionals=[('KEY', 'setting name', False), ('VALUE', 'new value', False)],
         maps_to=['robot.configure'], preconditions=['value allowed (Flex robot settings)', 'power on', 'no run active'], reference='Flex manual, robot settings')
    verb('set-name', 'admin', 'rename the robot', _simple('robot.set_name', lambda p, o: {'name': p[0]}, lambda a: 'robot renamed to %r' % a['name']),
         positionals=[('NAME', '1-35 characters', True)], maps_to=['robot.set_name'])
    verb('restart', 'admin', 'restart the robot software (destructive: --yes)', _simple('robot.restart', {}, 'restart the robot'), maps_to=['robot.restart'],
         destructive=True, preconditions=['power on', 'no run active'])
    verb('shutdown', 'admin', 'shut the robot software down (destructive: --yes)', _simple('robot.shutdown', {}, 'shut the robot down'), maps_to=['robot.shutdown'],
         destructive=True, preconditions=['power on', 'no run active'])

    def _proto_exists(ctx, a):
        if not any(p['protocol_id'] == a['protocol_id'] for p in ctx.view.get('protocols.list') or []):
            raise ValidationError('UNKNOWN_PROTOCOL', 'there is no stored protocol %r.' % a['protocol_id'], 'see `protocols`.')

    def _pin_args(p, o):
        if p[1] not in ('on', 'off'):
            raise ValidationError('BAD_CHOICE', 'protocol-pin takes on or off.', 'write protocol-pin ID on|off.')
        return {'protocol_id': p[0], 'pinned': p[1] == 'on'}
    verb('protocol-pin', 'admin', 'pin or unpin a protocol on the touchscreen', _simple('protocol.pin', _pin_args, lambda a: '%s pinned=%s' % (a['protocol_id'], a['pinned']),
         extra=_proto_exists), positionals=[('PROTOCOL', 'protocol id', True), ('on|off', 'pin or unpin', True)], maps_to=['protocol.pin'])
    verb('protocol-delete', 'admin', 'delete a stored protocol and its run history (destructive: --yes)', _simple(
        'protocol.delete', lambda p, o: {'protocol_id': p[0]}, lambda a: 'deleted protocol %s' % a['protocol_id'], extra=_proto_exists),
         positionals=[('PROTOCOL', 'protocol id', True)], maps_to=['protocol.delete'], destructive=True)
    reset_items = f.contract.capabilities['robot.device_reset'].input_schema['properties']['items']['items']['enum']

    def _reset_args(p, o):
        items = [x for x in p[0].split(',') if x]
        bad = [x for x in items if x not in reset_items]
        if bad or not items:
            raise ValidationError('BAD_CHOICE', 'unknown reset item(s) %s.' % ', '.join(bad or ['(none)']), 'items: %s.' % ', '.join(reset_items))
        return {'items': items}
    verb('device-reset', 'admin', 'erase calibration, protocols, run history or offsets (destructive: --yes)', _simple(
        'robot.device_reset', _reset_args, lambda a: 'reset %s' % ', '.join(a['items'])), positionals=[('ITEMS', 'comma list: ' + ', '.join(reset_items), True)],
         maps_to=['robot.device_reset'], destructive=True, reference='Flex manual, robot settings: device reset')
    return V

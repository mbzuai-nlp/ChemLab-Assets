"""Enclosure class ("enclosure"): an inert-atmosphere glovebox, a biological safety cabinet and a chemical fume hood - devices whose
job is a protected workspace.

Verbs: sash close|safe|open|HEIGHT [--hold], fan on|off|standby|SPEED [--wait], light on|off, uv on|off, mode NAME,
cycle [start|stop] [--cycles N] [--wait], read, open / close PART (the glovebox antechamber doors), status.  power, ack, estop come
from the shared layer; the glovebox's box pressure and gas purification are gas_fluid verbs (setpoint, start / stop).  Members and
mappings: configs/adapters/enclosure.json.

The value of the class is the safety logic, checked before anything is sent and explained in plain words:

* sash: named positions per member (closed, the safe working height, fully open); a hood sash above its stop needs --hold and is
  refused while the key lock limits it; the cabinet's motorised sash moves stop by stop and the verb waits until it has arrived;
* fan: the cabinet's blower runs only with the sash at a working height; --wait waits for the warm-up (airflow OK);
* uv: the cabinet's UV lamp only with the sash closed and the blower off (EN 12469 / NSF 49 practice);
* antechamber doors: never both open, never during a cycle, and the inner door only after the antechamber has been evacuated and
  refilled since the outer door was last opened (glovebox practice; tracked by this layer for doors opened through it);
* cycle: the antechamber evacuate / refill cycles as a job (job-N), with both doors closed.

Vendor-neutral terms: EN 12469 / NSF 49 (biosafety cabinets: inflow, downflow, safe sash height, UV interlock), EN 14175 / ASHRAE 110
(fume hoods: face velocity, sash stop, night setback), ISO 10648-2 (gloveboxes: antechamber transfer); no command standard exists.
"""
import re

from .. import units
from ..base import ClassAdapter, finite
from ..errors import invalid, refused, unsupported
from ..jobs import StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt
from .gas_fluid import fmt_value

def mm(h):
    return '%.0f mm' % (h * 1000.0) if finite(h) else 'n/a'


REF = ('Esco Airstream / Labculture AC2 manual: sash positions, blower start and warm-up, UV lamp interlock, operating modes; '
       'Esco Frontier Acela manual: sash stop and key lock, airflow monitor, night setback, light and fan switches; Inert PureLab HE '
       'manual: antechamber doors and cycles, blower, lighting')
ON_OFF = ['on', 'off']


class EnclosureAdapter(ClassAdapter):
    CLASS = 'enclosure'
    TITLE = 'protected workspace (glovebox, biosafety cabinet, fume hood)'
    ABOUT = ('Enclosures that keep a protected workspace: move the sash to a named position or height, run the fan / blower, '
             'switch the light and the UV lamp, choose the operating mode, cycle the glovebox antechamber, open and close its doors, '
             'and read airflow / atmosphere.  The safety interlocks are checked before anything is sent.')
    STANDARDS = 'EN 12469 / NSF 49 (biosafety cabinets), EN 14175 / ASHRAE 110 (fume hoods), ISO 10648-2 (gloveboxes); no command standard'
    ALWAYS = ('status',)
    UNIT_KINDS = ('length', 'fraction', 'duration')
    CODES = {
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'ACCESS_DENIED': ('NOT_YOURS', 'what is running on {inst} was started by someone else; only they can stop, interrupt or change it.',
                          'wait until it has ended (`chemlab run {inst} status`), or ask whoever started it.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab (the room / exhaust is not declared).', 'ask the lab operator.'),
        'SASH_CLOSED': ('SASH_CLOSED', 'the sash of {inst} is closed (below the safe height).', 'raise it: `chemlab run {inst} sash safe`.'),
        'SASH_NOT_CLOSED': ('SASH_NOT_CLOSED', 'the UV lamp of {inst} works only with the sash fully closed.', 'close it: `chemlab run {inst} sash close`.'),
        'FAN_RUNNING': ('FAN_RUNNING', 'the blower of {inst} is running (or purging).', 'switch it off and let the post-purge end: `chemlab run {inst} fan off`.'),
        'STANDBY': ('STANDBY', '{inst} is in standby: only the fan key works.', 'switch the fan on: `chemlab run {inst} fan on`.'),
        'WARMING_UP': ('WARMING_UP', '{inst} is warming up (only fan and light keys work).', 'wait for the warm-up: `chemlab run {inst} fan on --wait`.'),
        'POST_PURGE': ('POST_PURGE', '{inst} is post-purging after the fan was switched off.', 'wait until it has finished (`chemlab run {inst} status`).'),
        'SASH_AT_LIMIT': ('SASH_AT_LIMIT', 'the sash of {inst} is already at its end stop in that direction.', 'see `chemlab run {inst} status`.'),
        'SASH_LOCKED': ('SASH_LOCKED', 'the sash of {inst} is locked closed by its key lock.', 'ask the lab operator to unlock it.'),
        'ABOVE_STOP_LOCKED': ('ABOVE_STOP_LOCKED', 'the key lock of {inst} limits the sash to its stop height.', 'stay at or below the stop (`sash safe`).'),
        'MAINTENANCE_MODE': ('MAINTENANCE_MODE', '{inst} is in maintenance mode.', 'leave maintenance mode first (needs the admin PIN).'),
        'MENU_CLOSED': ('MENU_CLOSED', 'the menu of {inst} is not open.', 'see `chemlab describe {inst}` (menu.enter).'),
    }

    # ------------------------------------------------------------------ helpers
    def check_mapping(self, lab):
        """The base check; a 'slot#key' (a key of an object slot) is fine when the slot itself is visible."""
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

    def show(self, values, sid):
        base = sid.split('#', 1)[0]
        u = (self.lab.contract['state_slots'].get(base) or {}).get('unit') if '#' not in sid else None
        v = self.sv(values, sid)
        if base.endswith('mole_fraction') and finite(v):
            return '%.3g ppm' % (v * 1e6)
        return fmt_value(v, u)

    def summary(self, values):
        lines, js = [], {}
        p = self.slot('power')
        if p:
            js['power'] = bool(self.sv(values, p))
            if not js['power']:
                return ['power off'], js
        for sid in (self.vmap('read') or {}).get('reads') or []:
            lines.append('%s = %s' % (sid, self.show(values, sid)))
            js[sid] = self.sv(values, sid)
        return (lines or ['(see `chemlab read %s`)' % self.instrument_id]), js

    def m_check(self, ctx, verb):
        self.requirements(ctx, verb)

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        part = [Arg('PART', 'which door (see `chemlab help INST open`)', required=False)]
        return {
            'sash': Verb('sash', 'move the sash: close, safe (the working height), open, or a height such as 400mm', self.h_sash,
                         positionals=[Arg('POSITION', 'close | safe | open | a height (e.g. 400mm)')],
                         options={'hold': Opt('hold the sash above its stop (it falls back to the stop when released)', kind='flag')},
                         preconditions=['height within the sash range', 'above the stop only with --hold and no key-lock limit', 'not locked closed'],
                         errors=['OUT_OF_RANGE, UNKNOWN_POSITION (usage)', 'NEEDS_HOLD, SASH_LOCKED, ABOVE_STOP_LOCKED (refused)'],
                         example='chemlab run INST sash safe', reference=REF, long_action=True),
            'fan': Verb('fan', 'switch the fan / blower on, off or to standby, or set the blower speed (e.g. 60%)', self.h_fan,
                        positionals=[Arg('on|off|standby|SPEED', 'on, off, standby, or a speed in %')],
                        options={'wait': Opt('with on: wait until the warm-up has finished and the airflow is OK', kind='flag'),
                                 'max': Opt('with --wait: the longest wait (default 10 min)', kind='duration', metavar='TIME')},
                        preconditions=['sash at a working height (cabinet)'], errors=['SASH_CLOSED, STANDBY (refused)', 'NOT_READY (refused: --wait ran out)'],
                        example='chemlab run INST fan on --wait', reference=REF),
            'light': Verb('light', 'switch the work-area light on or off', self.h_light, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                          preconditions=['sash not closed (cabinet)'], errors=['SASH_CLOSED (refused)'], example='chemlab run INST light on', reference=REF),
            'uv': Verb('uv', 'switch the UV lamp on or off', self.h_uv, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                       preconditions=['sash fully closed', 'blower off and post-purge ended'], errors=['SASH_NOT_CLOSED, FAN_RUNNING (refused)'],
                       example='chemlab run INST uv on', reference=REF),
            'mode': Verb('mode', 'choose the operating mode (cabinet: normal | quickstart; hood: normal | night)', self.h_mode,
                         positionals=[Arg('MODE', 'the member\'s mode name')], errors=['UNKNOWN_MODE (usage)'], example='chemlab run INST mode night', reference=REF),
            'cycle': Verb('cycle', 'run the antechamber evacuate / refill cycles (job-N), or stop them', self.h_cycle,
                          positionals=[Arg('start|stop', 'start (default) or stop', required=False, choices=['start', 'stop'])],
                          options={'cycles': Opt('number of evacuate / refill cycles (default: the one set)', metavar='N'),
                                   'wait': Opt('wait until the cycles have finished', kind='flag'),
                                   'max': Opt('with --wait: the longest wait (default 30 min)', kind='duration', metavar='TIME')},
                          preconditions=['both antechamber doors closed', 'no cycle running', 'power on'],
                          errors=['DOOR_OPEN, BUSY (refused)', 'BAD_CYCLES (usage)'], long_action=True,
                          example='chemlab run INST cycle --cycles 3 --wait', reference=REF),
            'read': Verb('read', 'airflow / atmosphere / pressure readings in plain units', self.h_read, readonly=True, options={'save-to': save_to_opt()},
                         example='chemlab run INST read'),
            'open': Verb('open', 'open an antechamber door (outer or inner), with the interlocks checked', self.h_door('open'), positionals=part,
                         preconditions=['the other door closed', 'no cycle running', 'inner door: antechamber cycled since the outer door was opened'],
                         errors=['OTHER_DOOR_OPEN, CYCLE_RUNNING, ANTECHAMBER_NOT_PURGED (refused)'], example='chemlab run INST open outer', reference=REF),
            'close': Verb('close', 'close an antechamber door (outer or inner)', self.h_door('close'), positionals=part,
                          example='chemlab run INST close outer', reference=REF),
        }

    # ------------------------------------------------------------------ sash
    def h_sash(self, ctx, p):
        m = self.vmap('sash')
        text = p.raw['POSITION'].strip().lower()
        stops = m.get('stops') or {}
        words = {'closed': 'close', 'shut': 'close', 'down': 'close', 'working': 'safe', 'work': 'safe', 'full': 'open', 'up': 'open'}
        name = words.get(text, text)
        if name in stops:
            h = float(stops[name])
        elif m.get('style') == 'height':
            try:
                h = units.parse(text, 'length', what='height')
            except Exception:
                ctx.fail(invalid('UNKNOWN_POSITION', '%r is not a sash position of %s.' % (p.raw['POSITION'], self.instrument_id),
                                 'positions: %s, or a height such as 400mm.' % ', '.join(stops)))
            name = None
        else:
            ctx.fail(invalid('UNKNOWN_POSITION', '%s has a motorised sash with fixed stops; %r is not one.' % (self.instrument_id, p.raw['POSITION']),
                             'positions: %s.' % ', '.join(stops)))
        if m.get('style') == 'height':
            return self.sash_height(ctx, m, h, name, p.get('hold'))
        return self.sash_stops(ctx, m, h, name)

    def sash_height(self, ctx, m, h, name, hold):
        lo, hi = self.arg_range_si(m['cap'], m['arg'])
        if (lo is not None and h < lo - 1e-9) or (hi is not None and h > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'sash height %s is outside %s..%s on %s.' % (mm(h), mm(lo), mm(hi),
                                                                                       self.instrument_id), 'give a height from %s to %s.' % (mm(lo), mm(hi))))
        stop = float(m.get('stop_m', hi or h))
        lock = self.sv(ctx, self.slot('keylock')) if self.slot('keylock') else None
        ctx.check(not (lock == 'closed' and h > 0), 'SASH_LOCKED', 'the sash of %s is locked closed by its key lock.' % self.instrument_id,
                  'ask the lab operator to unlock it.', label='not locked closed')
        above = h > stop + 1e-9
        ctx.check(not (above and lock == 'limit'), 'ABOVE_STOP_LOCKED', 'the key lock of %s limits the sash to its stop at %s.' % (self.instrument_id, mm(stop)),
                  'stay at or below the stop: `chemlab run %s sash safe`.' % self.instrument_id, label='no key-lock limit above the stop')
        ctx.check(not above or hold, 'NEEDS_HOLD', '%s is above the sash stop of %s (%s): the sash falls back unless it is held.' % (
            mm(h), self.instrument_id, mm(stop)),
            'add --hold (e.g. to load large equipment), and lower it to the stop afterwards (`sash safe`).', label='held above the stop', kind='usage')
        self.requirements(ctx, 'sash')
        args = {m['arg']: round(self.arg_value(m['cap'], m['arg'], h), 6)}
        if m.get('hold_arg'):
            args[m['hold_arg']] = bool(above and hold)
        before = self.sv(ctx, m['slot'])
        ctx.call(m['cap'], args, 'move the sash to %s' % mm(h))
        now = self.sv(ctx, m['slot'])
        notes = []
        if above:
            notes.append('the sash is held above its stop; lower it to the working height (`sash safe`) when done.')
        return {'text': 'ok: %s sash at %s%s (was %s)\n' % (self.instrument_id, mm(now) if finite(now) else 'n/a', (' (%s)' % name) if name else '',
                                                           mm(before) if finite(before) else 'n/a'),
                'json': {'height_m': now, 'was_m': before, 'position': name, 'held': bool(above and hold)}, 'notes': notes}

    def sash_stops(self, ctx, m, h, name):
        self.requirements(ctx, 'sash')
        tol = float(m.get('tolerance_m', 0.005))
        cur = self.sv(ctx, m['slot'])
        if finite(cur) and abs(cur - h) <= tol and self.sv(ctx, m['status_slot']) != 'moving':
            return {'text': 'ok: %s sash is already %s (%s)\n' % (self.instrument_id, name, mm(cur)), 'json': {'position': name, 'changed': False}}
        t0 = ctx.now()
        for _ in range(4):
            cur = self.sv(ctx, m['slot'])
            if finite(cur) and abs(cur - h) <= tol:
                break
            direction = 'up' if (not finite(cur) or cur < h) else 'down'
            ctx.call(m['cap'], {m['dir_arg']: direction}, 'press sash %s (to the next stop)' % direction)
            if ctx.dry_run:
                ctx.plan.append({'capability': None, 'arguments': {}, 'step': 'wait until the sash has stopped, then continue to %s' % name})
                break
            ctx.advance(float(m.get('max_move_s', 60.0)), until=lambda v: self.sv(ctx, m['status_slot']) != 'moving', step='wait for the sash', poll_s=1.0)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        cur = self.sv(ctx, m['slot'])
        if not (finite(cur) and abs(cur - h) <= tol):
            ctx.fail(refused('SASH_NOT_THERE', 'the sash of %s stopped at %s, not at %s.' % (self.instrument_id, mm(cur) if finite(cur) else 'n/a', name),
                             'check `chemlab run %s status` (sash sensor, motor lock).' % self.instrument_id))
        return {'text': 'ok: %s sash %s (%s) after %s; status %s\n' % (self.instrument_id, name, mm(cur), units.fmt_s(ctx.now() - t0),
                                                                     self.sv(ctx, m['status_slot'])),
                'json': {'position': name, 'height_m': cur, 'changed': True, 'status': self.sv(ctx, m['status_slot'])}}

    # ------------------------------------------------------------------ fan
    def h_fan(self, ctx, p):
        m = self.vmap('fan')
        text = p.raw['on|off|standby|SPEED'].strip().lower()
        if text in ('on', 'off', 'standby'):
            vals = m.get('values') or {}
            if text not in vals:
                ctx.fail(invalid('BAD_FAN_SETTING', '%s takes %s for its fan.' % (self.instrument_id, ' or '.join(list(vals) + (['a speed in %'] if m.get('speed_arg') else []))),
                                 m.get('hint', 'see `chemlab help INST fan`.').replace('INST', self.instrument_id)))
            self.requirements(ctx, 'fan:' + text)
            slot = self.slot('fan')
            if slot and self.sv(ctx, slot) in (m.get('already') or {}).get(text, []):
                return {'text': 'ok: %s fan is already %s (%s)\n' % (self.instrument_id, text, self.sv(ctx, slot)), 'json': {'fan': text, 'changed': False}}
            if m.get('toggle_cap'):
                ctx.call(m['toggle_cap'], {}, 'press the fan key')
            else:
                ctx.call(m['cap'], {m['arg']: vals[text]}, 'fan %s' % text)
            st = self.sv(ctx, slot) if slot else None
            text_out = 'ok: %s fan %s%s' % (self.instrument_id, text, (' (%s)' % st) if slot and not isinstance(st, bool) else '')
            js = {'fan': text, 'state': self.sv(ctx, slot) if slot else None}
            ready = m.get('ready')
            if p.get('wait') and text == 'on' and not ctx.dry_run:
                if not ready:
                    ctx.fail(unsupported('NO_READY_STATE', '%s has no warm-up to wait for.' % self.instrument_id, 'leave out --wait.'))
                t0 = ctx.now()
                ok = (lambda v: self.sv(ctx, ready['slot']) in ready['values'])
                if not ok(ctx.view):
                    ctx.advance(p.get('max') or 600.0, until=ok, step='wait for the warm-up', poll_s=5.0)
                if not ok(ctx.view):
                    ctx.fail(refused('NOT_READY', '%s is not ready after %s (%s = %s).' % (self.instrument_id, units.fmt_s(ctx.now() - t0), ready['slot'],
                                                                                            self.sv(ctx, ready['slot'])),
                                     'check `chemlab run %s status` (sash at the safe height? airflow alarm?).' % self.instrument_id))
                text_out += '; ready after %s (%s = %s)' % (units.fmt_s(ctx.now() - t0), ready['slot'], self.sv(ctx, ready['slot']))
                js['ready'] = True
            return {'text': text_out + '\n', 'json': js}
        if not m.get('speed_arg'):
            ctx.fail(invalid('BAD_FAN_SETTING', '%s has no fan speed setting.' % self.instrument_id, 'write on, off%s.' % (' or standby' if 'standby' in (m.get('values') or {}) else '')))
        try:
            frac = units.parse(text, 'fraction', what='speed')
        except Exception:
            ctx.fail(invalid('BAD_FAN_SETTING', '%r is not on, off or a speed in %%.' % text, 'e.g. `chemlab run %s fan 60%%`.' % self.instrument_id))
        cap, arg = m['speed_cap'], m['speed_arg']
        lo, hi = self.arg_range(cap, arg)
        pct = frac * 100.0
        if (lo is not None and pct < lo) or (hi is not None and pct > hi):
            ctx.fail(invalid('OUT_OF_RANGE', 'blower speed %g %% is outside %s..%s %% on %s.' % (pct, lo, hi, self.instrument_id), 'give a speed from %s to %s %%.' % (lo, hi)))
        self.requirements(ctx, 'fan:speed')
        ctx.call(cap, {arg: round(pct, 6)}, 'set the blower speed to %g %%' % pct)
        notes = list((m.get('notes') or []))
        return {'text': 'ok: %s blower speed %g %%\n' % (self.instrument_id, pct), 'json': {'speed_pct': pct}, 'notes': notes}

    # ------------------------------------------------------------------ light / uv / mode
    def switch(self, ctx, verb, want):
        m = self.vmap(verb)
        self.requirements(ctx, '%s:%s' % (verb, 'on' if want else 'off'))
        slot = m.get('slot')
        cur = self.sv(ctx, slot) if slot else None
        if slot and bool(cur) == want:
            return {'text': 'ok: %s %s is already %s\n' % (self.instrument_id, verb, 'on' if want else 'off'), 'json': {verb: want, 'changed': False}}
        if m.get('toggle_cap'):
            ctx.call(m['toggle_cap'], {}, 'press the %s key' % verb,
                     readback=(lambda v: None if bool(self.sv(ctx, slot)) == want else '%s %s' % (slot, self.sv(ctx, slot))) if slot else None)
        else:
            ctx.call(m['cap'], {m['arg']: want}, '%s %s' % (verb, 'on' if want else 'off'))
        extra = ''
        if verb == 'uv' and want and m.get('timer_slot') and finite(self.sv(ctx, m['timer_slot'])):
            extra = ' (switches itself off after %s)' % units.fmt_s(self.sv(ctx, m['timer_slot']))
        return {'text': 'ok: %s %s %s%s\n' % (self.instrument_id, verb, 'on' if want else 'off', extra), 'json': {verb: want, 'changed': True}}

    def h_light(self, ctx, p):
        return self.switch(ctx, 'light', p.pos[0] == 'on')

    def h_uv(self, ctx, p):
        return self.switch(ctx, 'uv', p.pos[0] == 'on')

    def h_mode(self, ctx, p):
        m = self.vmap('mode')
        name = p.raw['MODE'].strip().lower()
        vals = m.get('values') or {}
        if name not in vals:
            hint = ((m.get('other') or {}).get(name) or '').replace('INST', self.instrument_id)
            ctx.fail(invalid('UNKNOWN_MODE', '%s has no mode %r%s.' % (self.instrument_id, name, (' here: ' + hint) if hint else ''),
                             'modes: %s.' % ', '.join(vals)))
        self.requirements(ctx, 'mode')
        slot = m.get('slot')
        if slot and self.sv(ctx, slot) == vals[name]:
            return {'text': 'ok: %s is already in %s mode\n' % (self.instrument_id, name), 'json': {'mode': name, 'changed': False}}
        args = dict(m.get('args') or {})
        args[m['arg']] = vals[name]
        ctx.call(m['cap'], args, 'mode %s' % name)
        return {'text': 'ok: %s mode %s\n' % (self.instrument_id, name), 'json': {'mode': name, 'changed': True}}

    # ------------------------------------------------------------------ antechamber
    def h_cycle(self, ctx, p):
        m = self.vmap('cycle')
        action = p.pos[0] if p.pos else 'start'
        if action == 'stop':
            ctx.check(bool(self.sv(ctx, m['active_slot'])), 'NO_CYCLE', 'no antechamber cycle is running on %s.' % self.instrument_id,
                      'nothing to stop.', label='a cycle running')
            ctx.call(m['stop_cap'], {}, 'stop the antechamber cycle')
            return {'text': 'ok: %s antechamber cycle stopped (phase %s)\n' % (self.instrument_id, self.sv(ctx, m['state']['slot'])), 'json': {'stopped': True}}
        n = None
        if p.get('cycles') is not None:
            try:
                n = int(p.raw['cycles'])
            except ValueError:
                ctx.fail(invalid('BAD_CYCLES', '--cycles takes a whole number.', 'e.g. --cycles 3.'))
            lo, hi = self.arg_range(m['cycles_cap'], m['cycles_arg'])
            if (lo is not None and n < lo) or (hi is not None and n > hi):
                ctx.fail(invalid('BAD_CYCLES', '%s runs %s to %s antechamber cycles, not %d.' % (self.instrument_id, lo, hi, n), 'give --cycles from %s to %s.' % (lo, hi)))
        self.requirements(ctx, 'cycle')
        if n is not None and self.sv(ctx, m.get('target_slot')) != n:
            ctx.call(m['cycles_cap'], {m['cycles_arg']: n}, 'set %d cycles' % n)
        ctx.call(m['start_cap'], {}, 'start the antechamber cycles')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        sh = self.new_shadow_state()
        sh['needs_purge'] = False
        st = m['state']
        ad = self
        final = {}

        def state(c):
            if final:
                return final['r']
            v = ad.sv(c, st['slot'])
            prog = '%s = %s, %s of %s cycles done' % (st['slot'], v, ad.sv(c, m.get('progress_slot')), ad.sv(c, m.get('target_slot')))
            js = {'phase': v, 'completed_cycles': ad.sv(c, m.get('progress_slot'))}
            if v in st['done']:
                final['r'] = ('succeeded', prog, js)
            elif v in st['failed']:
                final['r'] = ('failed', prog, js)
            return final.get('r', ('running', prog, js))

        def stop(c):
            c.call(m['stop_cap'], {}, 'stop the antechamber cycle')
            return 'stopped with %s' % m['stop_cap']
        job = StateJob(ctx.core, self, ctx.req, 'antechamber cycles on %s' % self.instrument_id, state, stop=stop, max_s=7200.0, poll_s=2.0)
        if p.get('wait'):
            r = job.wait_reply(ctx.core, ctx.req, p.get('max') or 1800.0)
            ctx.refresh()
            if r['json'].get('status') != 'succeeded':
                ctx.fail(refused('CYCLE_NOT_DONE', 'the antechamber cycles on %s ended as %s (%s).' % (self.instrument_id, r['json'].get('status'), r['json'].get('phase')),
                                 'see `chemlab run %s status`.' % self.instrument_id))
            return {'text': 'ok: antechamber cycles done (%s)\n%s' % (job.handle, r['text']), 'json': dict(r['json'], job=job.handle)}
        return {'text': 'started %s: antechamber cycles\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, job.handle, job.handle, job.handle), 'json': {'job': job.handle}}

    def new_shadow_state(self):
        if not hasattr(self, '_ante'):
            self._ante = {'needs_purge': False}
        return self._ante

    def h_door(self, action):
        def h(ctx, p):
            parts = (self.vmap('open') or {}).get('parts') or {}
            name = (p.pos[0] if p.pos else None)
            aliases = {'outer door': 'outer', 'outside': 'outer', 'inner door': 'inner', 'inside': 'inner'}
            name = aliases.get(name, name)
            if name is None:
                ctx.fail(invalid('WHICH_PART', '%s has several doors: %s.' % (self.instrument_id, ', '.join(parts)), 'name one, e.g. `%s outer`.' % action))
            if name not in parts:
                ctx.fail(invalid('UNKNOWN_PART', '%s has no door %r.' % (self.instrument_id, name), 'doors: %s.' % ', '.join(parts)))
            spec = parts[name]
            want = action == 'open'
            if bool(self.sv(ctx, spec['slot'])) == want:
                return {'text': 'ok: %s %s door is already %s\n' % (self.instrument_id, name, 'open' if want else 'closed'), 'json': {'part': name, 'changed': False}}
            ante = self.new_shadow_state()
            if want:
                self.requirements(ctx, 'open')
                ctx.check(not self.sv(ctx, spec['other_slot']), 'OTHER_DOOR_OPEN', 'the other antechamber door of %s is open; both doors are never open together.' % self.instrument_id,
                          'close it first: `chemlab run %s close %s`.' % (self.instrument_id, spec['other']), label='the other door closed')
                ctx.check(not self.sv(ctx, spec['cycle_slot']), 'CYCLE_RUNNING', 'an antechamber cycle is running on %s.' % self.instrument_id,
                          'wait for it (`chemlab run %s status`) or stop it: `chemlab run %s cycle stop`.' % (self.instrument_id, self.instrument_id),
                          label='no cycle running')
                if spec.get('needs_purge'):
                    phase = self.sv(ctx, spec['phase_slot'])
                    ctx.check(not ante.get('needs_purge') and phase not in ('stopped', 'evacuating', 'backfilling'), 'ANTECHAMBER_NOT_PURGED',
                              'the antechamber of %s has not been evacuated and refilled since its outer door was opened (phase %s): opening the inner door '
                              'would let air into the box.' % (self.instrument_id, phase),
                              'close the outer door and run the cycles first: `chemlab run %s cycle --cycles 3 --wait`.' % self.instrument_id,
                              label='antechamber purged since the outer door was opened')
            ctx.call(spec['cap'], {spec['arg']: spec[action]}, '%s the %s door' % (action, name),
                     readback=lambda v: None if bool(self.sv(ctx, spec['slot'])) == want else '%s = %s' % (spec['slot'], self.sv(ctx, spec['slot'])))
            if want and spec.get('opens_to_air') and not ctx.dry_run:
                ante['needs_purge'] = True
            return {'text': 'ok: %s %s door %s\n' % (self.instrument_id, name, 'opened' if want else 'closed'), 'json': {'part': name, 'action': action}}
        return h

    # ------------------------------------------------------------------ read
    def h_read(self, ctx, p):
        m = self.vmap('read')
        rows = {}
        lines = ['%s at t = %.1f s:' % (self.instrument_id, ctx.now())]
        for sid in m.get('reads') or []:
            rows[sid] = self.sv(ctx, sid)
            lines.append('  %-36s %s' % (sid, self.show(ctx, sid)))
        return {'text': '\n'.join(lines) + '\n', 'json': {'readings': rows}}

    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code == 'PRECONDITION_FAILED':
            ctx.refresh()
            p = self.slot('power')
            if p and not self.sv(ctx, p):
                return refused('POWER_OFF', '%s is switched off (it answered %s).' % (self.instrument_id, code), 'switch it on: `chemlab run %s power on`.' % self.instrument_id)
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)

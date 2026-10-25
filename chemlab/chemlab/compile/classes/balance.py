"""Balance class: zero, tare, weigh [--stable], unit, door, power (+ status).

Members and their mappings: configs/adapters/balance.json (mt_xpr204e, ohaus_px224e, ohaus_spx2201).  The verbs follow the
vendor-neutral weighing commands: MT-SICS level 0/1 (Z zero, T tare, S stable weight, SI immediate weight; reference manual
pp. 274, 254, 225) and the OHAUS ASCII set (Z, T, SP stable print, IP immediate print, xU unit; PX manual section 9.4.1,
Scout RS232 kit manual p.7); SiLA 2 has the same split (Tare, Zero, StableWeightValue, ImmediateWeightValue).

Class checks before anything is sent: power on; a stable reading for zero and tare (or wait for one with --wait); zero only
within the zero range (contract parameter zero_capture_fraction, 2 % of capacity) - otherwise the fix is tare; tare not of a
negative load; display units the balance offers and has enabled.  Masses are shown in grams to the balance's readability.
"""
import math

from ..base import ClassAdapter, finite
from ..errors import invalid, refused
from ..verbs import Arg, Opt, Verb

REF = ('MT-SICS reference manual: Z p.274, T p.254, S p.225, SI; XPR reference manual pp.18-22 (weighing screen, doors); '
       'OHAUS PX manual EN-8..9 and section 9.4.1 (Z, T, SP, IP); Scout SPX manual EN-4..8, RS232 kit manual p.7')


class BalanceAdapter(ClassAdapter):
    CLASS = 'balance'
    TITLE = 'balance (weighing)'
    ABOUT = ('Laboratory balances: zero, tare, weigh (immediate or stable), display unit, draft-shield doors and power, the same '
             'commands on every balance; masses in g / mg / kg.')
    STANDARDS = ('MT-SICS level 0/1 (Z, T, S, SI); OHAUS ASCII command set (Z, T, SP, IP, xU); SiLA 2 weighing service '
                 '(Tare, Zero, StableWeightValue, ImmediateWeightValue)')
    UNIT_KINDS = ('mass', 'duration')
    CODES = {
        'UNSTABLE': ('UNSTABLE', 'the reading of {inst} is not stable.',
                     'wait a few seconds (`chemlab wait 5`) and retry, or add --wait 30s; close the draft-shield doors if they are open.'),
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'NO_POWER': ('NO_POWER', '{inst} has no mains power (the power adapter is not connected).',
                     'connect it (`chemlab call {inst} power.connect_adapter --connected true`), then `chemlab run {inst} power on`.'),
        'ZERO_RANGE': ('ZERO_RANGE', 'the load on {inst} is outside its zero range (about 2 % of the capacity).',
                       'to make a container read 0, use `chemlab run {inst} tare`; zero is for an empty pan.'),
        'TARE_RANGE': ('TARE_RANGE', '{inst} cannot tare a negative load.', 'zero the empty pan first (`chemlab run {inst} zero`).'),
        'NOT_WEIGHING': ('NOT_WEIGHING', '{inst} is not weighing (overload, underload, start-up zero out of range or transport lock).',
                         'check `chemlab run {inst} status`; take the load off or unlock the pan, then retry.'),
        'UNIT_DISABLED': ('UNIT_NOT_ENABLED', 'that unit is switched off in the unit menu of {inst}.',
                          'use one of the enabled units (`chemlab run {inst} unit`).'),
        'SECURITY_LOCKED': ('LOCKED', '{inst} is locked by its security switch.', 'ask the lab operator to unlock it.'),
        'STANDBY': ('STANDBY', '{inst} is in standby.', 'wake it up: `chemlab run {inst} power on`.'),
    }

    # ------------------------------------------------------------------ helpers
    def readability_kg(self):
        p = self.param('readability_kg')
        if p is None:
            c = self.contract_param('readability_kg')
            p = c['value'] if c else 1e-7
        return float(p)

    def capacity_kg(self):
        p = self.param('capacity_kg')
        if p is None:
            c = self.contract_param('capacity_kg')
            p = c['value'] if c else None
        return p

    def zero_fraction(self):
        p = self.param('zero_capture_fraction')
        if p is None:
            c = self.contract_param('zero_capture_fraction')
            p = c['value'] if c else 0.02
        return float(p)

    def g(self, kg, sign=False):
        """kg -> 'x.xxxx g' to the balance readability."""
        if not finite(kg):
            return 'n/a'
        d_g = self.readability_kg() * 1000.0
        dec = max(0, int(round(-math.log10(d_g)))) if d_g > 0 else 4
        return ('%+.*f g' if sign else '%.*f g') % (dec, kg * 1000.0)

    def val(self, values, role):
        s = self.slot(role)
        if s is None:
            return None
        if isinstance(values, dict):
            if s in values:
                return values[s]
            low = {k.lower(): k for k in values}
            return values.get(low.get(s.lower()))
        return values.v(s)

    def doors_open(self, values):
        slots = self.slot('doors') or []
        if not slots:
            return None
        vals = [values.get(s) if isinstance(values, dict) else values.v(s) for s in slots]
        return any(finite(x) and x > 1e-6 for x in vals)

    def need_power(self, ctx):
        on = ctx.v(self.slot('power'))
        ctx.check(bool(on), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                  'switch it on: `chemlab run %s power on` (give it a few seconds to settle).' % self.instrument_id, label='power on')

    def unstable_reason(self, ctx):
        if self.doors_open(ctx):
            return 'a draft-shield door is open'
        st = ctx.v(self.slot('status'))
        if isinstance(st, str) and st.lower() not in ('stable', 'unstable'):
            return 'it reports %s' % st
        return 'the load was just changed or the balance is still settling'

    def need_stable(self, ctx, wait_s, doing, settle=False):
        """A stable reading: the balance's own stability flag; with a wait (--wait, weigh --stable) also settled - two readings
        one second apart within 2 readability steps (the twin's flag can rise while the filtered value still creeps)."""
        stable_slot, net_slot = self.slot('stable'), self.slot('net')
        d = self.readability_kg()
        if ctx.v(stable_slot) and not (settle and wait_s):
            ctx.checks.append('reading stable')
            return
        if wait_s and wait_s > 0 and not ctx.dry_run:
            last = {'net': None}

            def settled(v):
                if not ctx.v(stable_slot):
                    last['net'] = None
                    return False
                n, prev = ctx.v(net_slot), last['net']
                last['net'] = n
                return prev is not None and finite(n) and finite(prev) and abs(n - prev) <= 2 * d
            settled(ctx.view)
            ctx.advance(wait_s, until=settled, step='wait for a stable reading', poll_s=1.0)
            if ctx.v(stable_slot):
                ctx.checks.append('reading stable after %.0f s' % ctx.waited_s)
                return
        if ctx.dry_run and wait_s:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': wait_s}, 'step': 'wait up to %.0f s for a stable reading' % wait_s})
            ctx.unchecked.append('stable reading within %.0f s' % wait_s)
            return
        door = self.doors_open(ctx)
        fix = ('close the doors (`chemlab run %s door close`), then retry with --wait 30s.' % self.instrument_id if door else
               'wait a few seconds and retry, or add --wait 30s so chemlab waits for a stable reading.')
        ctx.fail(refused('UNSTABLE', 'the reading of %s is not stable%s (%s), so it cannot %s.' % (
            self.instrument_id, ' after %.0f s' % ctx.waited_s if ctx.waited_s else '', self.unstable_reason(ctx), doing), fix,
            detail={'doors_open': door}))

    def explain_refusal(self, ctx, cap, args, response):
        """The runtime may only say PRECONDITION_FAILED (the XPR cell does): find the reason in the state."""
        code = (response.get('error') or {}).get('code')
        if code == 'PRECONDITION_FAILED' and response.get('status') == 'rejected':
            ctx.refresh()
            if not ctx.v(self.slot('power')):
                return refused('POWER_OFF', '%s is switched off (it answered %s).' % (self.instrument_id, code),
                               'switch it on: `chemlab run %s power on`.' % self.instrument_id)
            if self.slot('stable') and not ctx.v(self.slot('stable')):
                return refused('UNSTABLE', 'the reading of %s is not stable (%s; it answered %s).' % (self.instrument_id, self.unstable_reason(ctx), code),
                               'wait a few seconds and retry, or add --wait 30s.')
            gross = ctx.v(self.slot('gross'))
            cap_kg = self.capacity_kg()
            if cap == (self.vmap('zero') or {}).get('cap') and finite(gross) and cap_kg and abs(gross) > self.zero_fraction() * cap_kg:
                return refused('ZERO_RANGE', 'the load on %s (%s) is outside its zero range (it answered %s).' % (self.instrument_id, self.g(gross), code),
                               'use `chemlab run %s tare` to make a container read 0.' % self.instrument_id)
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)

    # ------------------------------------------------------------------ summary
    def summary(self, values):
        on = self.val(values, 'power')
        net, gross, tare = self.val(values, 'net'), self.val(values, 'gross'), self.val(values, 'tare')
        stable = self.val(values, 'stable')
        unit = self.val(values, 'unit')
        door = self.doors_open(values)
        if not on:
            return ['power off'], {'power': False}
        line = 'net %s (%s) · gross %s · tare %s' % (self.g(net), 'stable' if stable else 'not stable', self.g(gross), self.g(tare))
        more = 'power on · display unit %s%s' % (unit, '' if door is None else ' · doors %s' % ('open' if door else 'closed'))
        js = {'power': True, 'net_kg': net, 'gross_kg': gross, 'tare_kg': tare, 'stable': stable, 'display_unit': unit,
              'doors_open': door, 'readability_kg': self.readability_kg(), 'capacity_kg': self.capacity_kg()}
        return [line, more], js

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        wait = Opt('if the reading is not stable yet, wait up to this long for it (default: do not wait)', kind='duration', metavar='TIME')
        return {
            'power': Verb('power', 'switch the balance on or off', self.h_power, positionals=[Arg('on|off', 'on or off', choices=['on', 'off'])],
                          preconditions=['the balance has mains power'], errors=['NO_POWER (refused)'], example='chemlab run INST power on',
                          reference=REF),
            'zero': Verb('zero', 'set the zero point with the pan empty (clears the tare)', self.h_zero, options={'wait': wait},
                         preconditions=['power on', 'reading stable (or --wait)', 'load within the zero range (2 % of capacity)'],
                         errors=['POWER_OFF, UNSTABLE, ZERO_RANGE (refused)'], example='chemlab run INST zero --wait 20s', reference=REF),
            'tare': Verb('tare', 'take the current load (e.g. an empty container) as tare, so the net reads 0', self.h_tare, options={'wait': wait},
                         preconditions=['power on', 'reading stable (or --wait)', 'load not negative'],
                         errors=['POWER_OFF, UNSTABLE, TARE_RANGE (refused)'], example='chemlab run INST tare --wait 30s', reference=REF),
            'weigh': Verb('weigh', 'read the net mass now, or with --stable once the reading is stable', self.h_weigh,
                          options={'stable': Opt('wait for a stable reading (like MT-SICS S / OHAUS SP)', kind='flag'),
                                   'wait': Opt('longest wait for --stable (default 30 s)', kind='duration', metavar='TIME')},
                          readonly=True, preconditions=['power on', 'with --stable: stable within the wait'], errors=['POWER_OFF, UNSTABLE (refused)'],
                          example='chemlab run INST weigh --stable', reference=REF),
            'unit': Verb('unit', 'show the display unit and the units on offer, or set the display unit', self.h_unit,
                         positionals=[Arg('UNIT', 'g, mg, kg, ct, ... (see the list without UNIT)', required=False)],
                         preconditions=['power on', 'the unit is offered and enabled on this balance'],
                         errors=['UNIT_NOT_OFFERED (usage)', 'UNIT_NOT_ENABLED, POWER_OFF (refused)'], example='chemlab run INST unit mg',
                         details='Only the panel display changes; `weigh` always reports g, mg and kg.', reference=REF),
            'door': Verb('door', 'open or close the draft-shield doors', self.h_door,
                         positionals=[Arg('open|close', 'open or close', choices=['open', 'close'])],
                         options={'side': Opt('which door (balances with left/right doors)', metavar='SIDE', choices=['left', 'right', 'both'])},
                         preconditions=['the balance has doors'], errors=['NOT_SUPPORTED (no draft shield)'],
                         example='chemlab run INST door close', reference=REF),
        }

    def h_power(self, ctx, p):
        m = self.vmap('power')
        want = p.pos[0] == 'on'
        cur = bool(ctx.v(self.slot('power')))
        if cur == want:
            return {'text': 'ok: %s is already %s\n' % (self.instrument_id, p.pos[0]), 'json': {'power': want, 'changed': False}}
        ctx.call(m['cap'], {m['arg']: want}, 'switch %s' % p.pos[0],
                 readback=lambda v: None if bool(ctx.v(self.slot('power'))) == want else 'power %s' % ctx.v(self.slot('power')))
        note = ['a balance needs a few seconds after switching on before the reading is stable (`weigh --stable` waits).'] if want else []
        return {'text': 'ok: %s switched %s\n' % (self.instrument_id, p.pos[0]), 'json': {'power': want, 'changed': True}, 'notes': note}

    def h_zero(self, ctx, p):
        m = self.vmap('zero')
        self.need_power(ctx)
        self.need_stable(ctx, p.get('wait'), 'set the zero')
        gross, cap_kg = ctx.v(self.slot('gross')), self.capacity_kg()
        if finite(gross) and cap_kg:
            lim = self.zero_fraction() * cap_kg
            ctx.check(abs(gross) <= lim, 'ZERO_RANGE', 'the load on %s is %s, outside its zero range (±%s).' % (self.instrument_id, self.g(gross), self.g(lim)),
                      'to make a container read 0 use `chemlab run %s tare`; zero is for the empty pan.' % self.instrument_id, label='load within the zero range')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'set zero')
        return {'text': 'ok: zero set on %s (net %s, tare cleared)\n' % (self.instrument_id, self.g(ctx.v(self.slot('net')))),
                'json': {'net_kg': ctx.v(self.slot('net')), 'gross_kg': ctx.v(self.slot('gross')), 'tare_kg': ctx.v(self.slot('tare'))}}

    def h_tare(self, ctx, p):
        m = self.vmap('tare')
        self.need_power(ctx)
        self.need_stable(ctx, p.get('wait'), 'tare')
        gross = ctx.v(self.slot('gross'))
        if finite(gross):
            ctx.check(gross >= -self.readability_kg(), 'TARE_RANGE', '%s reads a negative load (%s); it cannot be tared.' % (self.instrument_id, self.g(gross)),
                      'zero the empty pan first: `chemlab run %s zero`.' % self.instrument_id, label='load not negative')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'tare')
        tare = ctx.v(self.slot('tare'))
        return {'text': 'ok: tared %s on %s (net now %s)\n' % (self.g(tare), self.instrument_id, self.g(ctx.v(self.slot('net')))),
                'json': {'tare_kg': tare, 'net_kg': ctx.v(self.slot('net'))}}

    def h_weigh(self, ctx, p):
        m = self.vmap('weigh')
        self.need_power(ctx)
        if p.get('stable'):
            self.need_stable(ctx, p.get('wait') if p.get('wait') is not None else 30.0, 'give a stable weight', settle=True)
        net, gross, stable = ctx.v(self.slot('net')), ctx.v(self.slot('gross')), ctx.v(self.slot('stable'))
        if m.get('cap'):
            r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the weight', query=True)
            res = r.get('result') or {}
            f = m.get('result') or {}
            if res:
                net, gross, stable = res.get(f.get('net', 'net_kg'), net), res.get(f.get('gross', 'gross_kg'), gross), res.get(f.get('stable', 'stable'), stable)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        tare = ctx.v(self.slot('tare'))
        js = {'net_kg': net, 'gross_kg': gross, 'tare_kg': tare, 'stable': bool(stable),
              'net_g': None if not finite(net) else net * 1e3, 'net_mg': None if not finite(net) else net * 1e6,
              'readability_kg': self.readability_kg(), 'display_unit': ctx.v(self.slot('unit'))}
        text = '%s %s%s · gross %s · tare %s\n' % ('net', self.g(net), '' if stable else ' (NOT stable - use --stable for a stable value)',
                                                    self.g(gross), self.g(tare))
        return {'text': text, 'json': js}

    def h_unit(self, ctx, p):
        m = self.vmap('unit')
        cur = ctx.v(m.get('slot') or self.slot('unit'))
        offered = self.arg_enum(m['cap'], m['arg'])
        enabled = ctx.v(m['allowed_slot']) if m.get('allowed_slot') else offered
        enabled = [u for u in (enabled or []) if u in offered] if offered else list(enabled or [])
        if not p.pos:
            return {'text': 'display unit: %s; enabled now: %s%s\n' % (cur, ', '.join(enabled), ('; offered by this balance: %s' % ', '.join(offered)) if offered != enabled else ''),
                    'json': {'unit': cur, 'enabled': enabled, 'offered': offered}}
        want = {'ug': 'µg', 'μg': 'µg', 'microgram': 'µg', 'gram': 'g', 'milligram': 'mg', 'kilogram': 'kg'}.get(p.raw['UNIT'], p.raw['UNIT'])
        if offered and want not in offered:
            low = {u.lower(): u for u in offered}
            want = low.get(want.lower(), want)
        if offered and want not in offered:
            ctx.fail(invalid('UNIT_NOT_OFFERED', '%s does not offer the unit %r.' % (self.instrument_id, p.raw['UNIT']),
                             'units it offers: %s.' % ', '.join(offered)))
        self.need_power(ctx)
        ctx.check(want in enabled, 'UNIT_NOT_ENABLED', '%s offers %s but it is not enabled now.' % (self.instrument_id, want),
                  'use one of %s, or enable it in the unit menu (`chemlab describe %s`).' % (', '.join(enabled), self.instrument_id), label='unit enabled')
        if cur == want:
            return {'text': 'ok: the display unit of %s is already %s\n' % (self.instrument_id, want), 'json': {'unit': want, 'changed': False}}
        ctx.call(m['cap'], {m['arg']: want}, 'set the display unit to %s' % want,
                 readback=lambda v: None if ctx.v(m.get('slot') or self.slot('unit')) == want else 'unit %s' % ctx.v(m.get('slot') or self.slot('unit')))
        return {'text': 'ok: display unit of %s is now %s (was %s)\n' % (self.instrument_id, want, cur), 'json': {'unit': want, 'was': cur, 'changed': True}}

    def h_door(self, ctx, p):
        m = self.vmap('door')
        action = p.pos[0]
        side = p.get('side')
        style = m.get('style', 'operate')
        if style == 'operate':
            sides = m.get('sides') or ['both']
            side = side or m.get('default_side', sides[-1])
            if side not in sides:
                ctx.fail(invalid('BAD_SIDE', '%s has doors %s, not %s.' % (self.instrument_id, ', '.join(sides), side), 'use --side %s.' % '|'.join(sides)))
            args = {m.get('side_arg', 'side'): side, m.get('action_arg', 'action'): action}
        else:
            if side and side != 'both':
                ctx.fail(invalid('BAD_SIDE', '%s opens and closes its doors together.' % self.instrument_id, 'leave out --side.'))
            args = {m['arg']: 1.0 if action == 'open' else 0.0}
        before = self.doors_open(ctx)
        ctx.call(m['cap'], args, '%s the door%s' % (action, '' if style != 'operate' or side != 'both' else 's'),
                 readback=lambda v: None if (self.doors_open(ctx) == (action == 'open')) or side in ('left', 'right') else 'doors %s' % ('open' if self.doors_open(ctx) else 'closed'))
        note = ['the reading needs a few seconds to settle after the doors move (`weigh --stable` or `--wait` waits).'] if before != self.doors_open(ctx) else []
        return {'text': 'ok: %s door%s %s\n' % (self.instrument_id, '' if side in ('left', 'right') else 's', 'opened' if action == 'open' else 'closed'),
                'json': {'action': action, 'side': side or 'all', 'doors_open': self.doors_open(ctx)}, 'notes': note}

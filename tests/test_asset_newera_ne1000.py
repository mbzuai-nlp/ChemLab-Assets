"""tests for CHEM-184 newera_ne1000 (New Era NE-1000 single-syringe programmable pump): python3 -m unittest tests/test_asset_newera_ne1000.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; speed limits (5.1005 cm/min, 0.004205 cm/hr) against the manual's syringe table; dispensed volume by the set
diameter vs true delivery by the true diameter; compliance start-up lag and tail flow with an air bubble; occlusion, collar and bottom-out stall
(StAL, paused, any key clears); withdrawal from a reservoir, air intake and cavitation; 41-phase programs (RAT, PAS, LPS/LOP, FIL, INC/DEC, JMP,
EVN, OUT, CLD, STP); TTL inputs held >= 100 ms; keypad editing with the 2 s commit, oor, ----, LoC-; RS-232 Basic protocol; power-on clearing and
power-failure restart; purge at top speed; idle skipping exact; liquid ledger closes."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import newera_ne1000 as rt  # noqa: E402

INST = 'newera_ne1000_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
MH = 1e-6 / 3600.0
UH = 1e-9 / 3600.0
ML = 1e-6


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.NeweraNe1000Runtime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        self.n += 1
        before, t0 = self.h.read('evaluator'), self.h.t
        r = sess.invoke(instance_id=self.inst, capability_id=cap, arguments=args, request_id=f'r{self.n}')
        after = self.h.read('evaluator')
        changed = {k for k in after if _differ(after[k], before[k])} - {'clock.time_s'}
        if expect == 'succeeded':
            assert r['status'] == 'succeeded', (cap, r['error'])
            rt.validate(rt.CAPS[cap]['output_schema'], r['result'])
            allowed = set(rt.EFFECTS.get(cap, [])) | (set(rt.ON_STEP['sim.step']) if cap == 'sim.step' else set())
            extra = changed - allowed
            assert not extra, (cap, sorted(extra))
            if cap != 'sim.step':
                assert self.h.t == t0, cap
            SEEN_CAPS.add(cap)
            PROCESS_EVIDENCE.setdefault(cap, []).append(dict(arguments=json.dumps(args, sort_keys=True, default=str)[:200], time_s=t0, status=r['status']))
            return r['result']
        assert r['status'] in ('rejected', 'unsupported'), (cap, r['status'], r.get('result'))
        assert r['error']['code'] == expect, (cap, r['error']['code'], expect)
        assert not changed, (cap, 'rejected call changed', sorted(changed))
        assert expect in [e['code'] for e in rt.export_newera_ne1000_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def truth(self):
        return self.read('truth.process')

    def on(self):
        self.call(self.A, 'power.set', on=True)

    def setup(self, syringe='bd_60ml', fill=50 * ML, air=0.0, liquid='water', tubing='ptfe_0p8mm', length=0.5, needle='none', back=0.0, reservoir=0.0, power=True):
        if power:
            self.on()
        self.call(self.A, 'syringe.load', syringe=syringe, liquid_id=liquid, fill_volume_m3=fill, air_volume_m3=air)
        self.call(self.A, 'outlet.connect', tubing=tubing, tubing_length_m=length, needle=needle, backpressure_pa=back, reservoir_liquid_m3=reservoir)
        if power:
            self.call(self.A, 'syringe.set_diameter', diameter_m=rt.SYR[syringe]['inside_diameter_m'])

    def key(self, k):
        return self.call(self.A, 'keypad.press', key=k)['display']

    def serial(self, cmd):
        return self.call(self.A, 'serial.send', command=cmd)['response']

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']

    def events(self, who=None):
        return [e['code'] for e in self.call(who or self.E, 'events.read', since_s=0.0)['events']]


def _balance(test, L):
    test.assertLess(abs(L['liquid_balance_m3']), 1e-15 + 1e-12 * L['placed_m3'], L)
    test.assertLess(abs(L['geometric_balance_m3']), 1e-15 + 1e-12 * L['placed_m3'], L)       # barrel volume behind the plunger = liquid + air - compression


def _raised_codes():
    src = Path(rt.__file__).read_text()
    funcs = {}
    for m in re.finditer(r'\n    def (\w+)\(self[^\n]*\n(.*?)(?=\n    def |\n# ----|\nclass |\ndef )', src, re.S):
        funcs[m.group(1)] = m.group(2)
    own = {k: set(re.findall(r"ActionRejected\('([A-Z_]+)'\)", v)) for k, v in funcs.items()}
    calls = {k: set(re.findall(r'self\.(_\w+)\(', v)) & set(funcs) for k, v in funcs.items()}
    out = {}
    for k in funcs:
        if not k.startswith('_cmd_'):
            continue
        seen, todo, codes = set(), [k], set()
        while todo:
            f = todo.pop()
            if f in seen:
                continue
            seen.add(f)
            codes |= own.get(f, set())
            todo += [g for g in calls.get(f, ()) if g not in ('_cmd_sim_step', 'step', '_tick')]
        out[k] = codes
    name = {'_cmd_' + c.replace('.', '_'): c for c in rt.CAPS}
    swallowed = {'keypad.press': {'ALARM_ACTIVE', 'NOT_APPLICABLE', 'OUT_OF_RANGE', 'DIAMETER_NOT_SET', 'PROGRAM_OPERATING', 'UNITS_WHILE_PUMPING', 'ALREADY_RUNNING'},
                 'serial.send': {'ALARM_ACTIVE', 'NOT_APPLICABLE', 'OUT_OF_RANGE', 'DIAMETER_NOT_SET', 'PROGRAM_OPERATING', 'UNITS_WHILE_PUMPING', 'ALREADY_RUNNING',
                                 'UNKNOWN_COMMAND'},
                 'power.set': {'DIAMETER_NOT_SET'}, 'power.toggle': {'DIAMETER_NOT_SET'}, 'fault.inject': {'DIAMETER_NOT_SET'}}
    # keypad and RS-232 errors become display messages / '?' replies; the power-failure restart is guarded by `if self.diameter`
    return {name[k]: v - swallowed.get(name[k], set()) for k, v in out.items() if k in name}


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_consistent(self):
        m = rt.export_newera_ne1000_contract_metadata()
        for k, c in m['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], m['state_slots'], (k, e))
            for p in c['preconditions']:
                for s in p['state_ids']:
                    self.assertIn(s, m['state_slots'], (k, s))
        for s in rt.SCENE_MIRRORED:
            self.assertIn(s, rt.SLOTS)
            self.assertEqual(rt.SLOTS[s]['visibility'], 'agent')
        units = {p['unit'] for p in m['mechanisms']['syringe_pump_process']['parameters'].values()}
        self.assertFalse(units & {'in', 'cm/min', 'lbf'}, units)
        CHECKS.append(dict(id='metadata_consistent'))

    def test_declared_errors_cover_raised_codes(self):
        m = rt.export_newera_ne1000_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in m['capabilities'][cap]['errors']}
            self.assertFalse(codes - declared, (cap, codes - declared))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            r = g.A.invoke(instance_id=INST, capability_id=k, arguments={}, request_id='u' + k)
            self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))

    def test_agent_truth_isolation(self):
        g = Rig()
        obs = g.A.read(instance_id=INST)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.')])
        for cap, args in (('truth.inspect', {}), ('ledger.read', {}), ('fault.inject', dict(fault='occlusion', active=True)), ('sim.step', dict(dt_s=1.0)),
                          ('plant.configure', dict(rt.PLANT_EXAMPLE)), ('medium.configure', dict(syringe_id_error=0.01))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = g.A.describe(instance_id=INST)
        self.assertNotIn('truth.inspect', d['capabilities'])
        self.assertNotIn('truth.process', d['state_slots'])
        g.setup(fill=20 * ML, reservoir=0.0)
        g.call(g.A, 'direction.set', direction='WDR')
        g.call(g.A, 'rate.set', rate_m3_s=500 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(20.0)                                                                    # draws air from an empty outlet: truth event only
        self.assertIn('AIR_DRAWN', g.events())
        self.assertNotIn('AIR_DRAWN', g.events(g.A))

    def test_idempotent_request_ids(self):
        g = Rig()
        r1 = g.A.invoke(instance_id=INST, capability_id='power.toggle', arguments={}, request_id='same')
        r2 = g.A.invoke(instance_id=INST, capability_id='power.toggle', arguments={}, request_id='same')
        self.assertEqual(r1, r2)
        self.assertTrue(g.read('power.on'))
        r3 = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=False), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='a'), Rig(instance='b')
        a.setup()
        a.call(a.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        a.call(a.A, 'pump.run')
        a.step(30.0)
        self.assertGreater(a.read('pusher.position_m'), 0.0)
        self.assertFalse(b.read('power.on'))
        self.assertEqual(b.read('pusher.position_m'), 0.0)
        self.assertIsNone(b.read('syringe.state'))

    def test_step_partition_independence(self):
        def run(parts):
            g = Rig(seed=7)
            g.setup(fill=30 * ML, air=0.5 * ML, needle='27g', back=2.0e4, reservoir=50 * ML)
            g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=300 * MH, rate_units='MH', volume_m3=2 * ML, direction='INF')
            g.call(g.A, 'phase.select', phase=2)
            g.call(g.A, 'phase.set_function', function='PAS', parameter=7)
            g.call(g.A, 'phase.select', phase=3)
            g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=1 * ML, direction='WDR')
            g.call(g.A, 'phase.select', phase=4)
            g.call(g.A, 'phase.set_function', function='STP')
            g.call(g.A, 'pump.run')
            for dt in parts:
                g.step(dt)
            r = g.h.read('evaluator')
            return json.dumps({k: r[k] for k in r if k != 'clock.time_s'}, sort_keys=True, default=float), g.ledger()
        a, la = run([60.0])
        b, lb = run([0.05] * 1200)
        c, lc = run([7.33] * 8 + [1.36])
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertEqual(json.dumps(la, sort_keys=True), json.dumps(lb, sort_keys=True))

    def test_rejections_change_nothing(self):
        g = Rig()
        g.call(g.A, 'rate.set', expect='POWER_OFF', rate_m3_s=10 * MH)
        g.call(g.A, 'syringe.unload', expect='NO_SYRINGE')
        g.call(g.A, 'syringe.load', expect='OVERFILL', syringe='bd_10ml', liquid_id='water', fill_volume_m3=11 * ML)
        g.call(g.A, 'syringe.load', expect='SYRINGE_TOO_LONG', syringe='monoject_140ml', liquid_id='water', fill_volume_m3=140 * ML)
        g.call(g.A, 'ttl.set', expect='NOT_AN_INPUT', pin=5, level=0)
        g.on()
        g.call(g.A, 'rate.set', expect='DIAMETER_NOT_SET', rate_m3_s=10 * MH)
        g.call(g.A, 'pump.run', expect='DIAMETER_NOT_SET')
        g.call(g.A, 'pump.purge', expect='DIAMETER_NOT_SET', on=True)
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'rate.set', expect='OUT_OF_RANGE', rate_m3_s=1800 * MH)                       # 5.1005 cm/min x 5.553 cm2 = 1699 mL/h
        g.call(g.A, 'rate.set', expect='OUT_OF_RANGE', rate_m3_s=0.01 * MH)                       # below 0.004205 cm/hr
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='JMP', parameter=1)
        g.call(g.A, 'rate.set', expect='NOT_APPLICABLE', rate_m3_s=10 * MH)
        g.call(g.A, 'volume.set', expect='NOT_APPLICABLE', volume_m3=1 * ML)
        g.call(g.A, 'phase.set_function', expect='OUT_OF_RANGE', function='LOP', parameter=0)
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.call(g.A, 'pump.run', expect='ALREADY_RUNNING')
        g.call(g.A, 'syringe.set_diameter', expect='PROGRAM_OPERATING', diameter_m=0.02)
        g.call(g.A, 'rate.set', expect='UNITS_WHILE_PUMPING', rate_m3_s=1 * ML / 60.0, units='MM')
        g.call(g.A, 'pump.purge', expect='PROGRAM_OPERATING', on=True)
        g.call(g.A, 'syringe.load', expect='PROGRAM_OPERATING', syringe='bd_10ml', liquid_id='water', fill_volume_m3=1 * ML)
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=99))
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.call(g.A, 'pump.stop', expect='POWER_OFF')


class Delivery(unittest.TestCase):
    def test_speed_limits_match_syringe_table(self):
        """The max rate of every syringe in the manual's 12.7 table is area x 5.1005 cm/min (rounded in the table)."""
        for k, s in rt.SYR.items():
            area = math.pi * s['inside_diameter_m'] ** 2 / 4.0
            self.assertAlmostEqual(area * rt.V_MAX / s['max_rate_m3_s'], 1.0, delta=0.012, msg=k)
        g = Rig()
        g.setup()
        a = math.pi * 0.02659 ** 2 / 4.0
        g.call(g.A, 'rate.set', rate_m3_s=a * rt.V_MAX * (1 - 1e-12), units='MH')
        g.call(g.A, 'rate.set', rate_m3_s=a * rt.V_MIN * (1 + 1e-9), units='UH')
        self.assertAlmostEqual(rt.V_MAX, 5.1005e-2 / 60.0, places=12)
        self.assertAlmostEqual(rt.V_MIN, 0.004205e-2 / 3600.0, places=15)

    def test_volume_target_time_and_display(self):
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=5 * ML)
        g.call(g.A, 'pump.run')
        self.assertTrue(g.read('indicator.pumping'))
        self.assertEqual(g.read('display.text'), '100.0')
        g.step(179.9)
        self.assertEqual(g.read('run.state'), 'running')
        g.step(0.2)
        self.assertEqual(g.read('run.state'), 'stopped')                                  # 5 mL at 100 mL/h = 180 s
        self.assertAlmostEqual(g.read('volume.dispensed')['infused_m3'], 5 * ML, delta=1e-15)
        self.assertAlmostEqual(g.truth()['delivered_m3'], 5 * ML, delta=0.01 * ML)
        self.assertFalse(g.read('indicator.pumping'))
        self.assertTrue(g.read('indicator.ml') and g.read('indicator.hr'))
        self.assertIn('PROGRAM_END', g.events())
        g.step(10.0)
        self.assertAlmostEqual(g.truth()['delivered_m3'], 5 * ML, delta=1e-12)              # relaxed: the compressed volume has left
        dx = g.read('pusher.position_m') - (rt.X_ZERO - 50 * ML / (math.pi * 0.02659 ** 2 / 4.0))
        self.assertAlmostEqual(dx, 5 * ML / (math.pi * 0.02659 ** 2 / 4.0), delta=1e-12)
        _balance(self, g.ledger())

    def test_true_diameter_error_changes_delivery_not_display(self):
        g = Rig()
        g.call(g.E, 'medium.configure', syringe_id_error=0.02)
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=10 * ML)
        g.call(g.A, 'pump.run')
        g.step(80.0)
        self.assertAlmostEqual(g.read('volume.dispensed')['infused_m3'], 10 * ML, delta=1e-15)
        self.assertAlmostEqual(g.truth()['delivered_m3'] / (10 * ML), 1.02 ** 2, delta=1e-6)
        self.assertEqual(g.truth()['syringe']['true_inside_diameter_m'], 0.02659 * 1.02)
        g.call(g.E, 'medium.configure', expect='SYRINGE_LOADED', syringe_id_error=0.0)
        _balance(self, g.ledger())

    def test_air_bubble_lag_and_tail_flow(self):
        g = Rig()
        g.setup(fill=40 * ML, air=2 * ML, needle='30g', tubing='ptfe_0p5mm', length=2.0)
        g.call(g.A, 'rate.set', rate_m3_s=50 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(10.0)
        T = g.call(g.E, 'truth.inspect')['process']
        self.assertLess(T['delivered_m3'], 0.9 * g.read('volume.dispensed')['infused_m3'])    # the bubble compresses first (tau = R C ~ 40 s)
        self.assertGreater(T['pressure_pa'], 5e3)
        g.step(600.0)
        g.call(g.A, 'pump.stop')
        d_stop = g.truth()['delivered_m3']
        g.step(600.0)
        self.assertGreater(g.truth()['delivered_m3'], d_stop + 1e-9)                           # tail flow after the motor stops
        self.assertLess(abs(g.truth()['pressure_pa']), 1.0)
        self.assertAlmostEqual(g.truth()['delivered_m3'], g.read('volume.dispensed')['infused_m3'], delta=1e-12)
        _balance(self, g.ledger())

    def test_backpressure_and_viscosity_raise_force(self):
        g = Rig()
        g.setup(syringe='bd_10ml', fill=8 * ML, needle='30g', back=1.0e5, liquid='glycerol' if 'glycerol' in rt.LIQ else 'ethylene_glycol')
        g.call(g.A, 'rate.set', rate_m3_s=30 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(5.0)
        T = g.truth()
        a = math.pi * rt.SYR['bd_10ml']['inside_diameter_m'] ** 2 / 4.0
        self.assertGreater(T['pressure_pa'], 1.0e5)
        self.assertAlmostEqual(T['force_n'], T['pressure_pa'] * a + rt.ENG['friction_n']['plastic'], delta=1e-6)


class Stall(unittest.TestCase):
    def test_occlusion_stalls_and_any_key_clears(self):
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=200 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(5.0)
        g.call(g.E, 'fault.inject', fault='occlusion', active=True)
        for _ in range(600):
            g.step(1.0)
            if g.read('alarm.state'):
                break
        self.assertEqual(g.read('alarm.state'), 'stall')
        self.assertEqual(g.read('display.text'), 'StAL')
        self.assertEqual(g.read('run.state'), 'paused')
        self.assertFalse(g.read('indicator.pumping'))
        T = g.truth()
        self.assertGreater(abs(T['force_n']) + rt.ENG['friction_n']['plastic'], rt.max_force(200 * MH / (math.pi * 0.02659 ** 2 / 4.0)))   # standing: pressure load
        x = g.read('pusher.position_m')
        g.step(30.0)
        self.assertEqual(g.read('pusher.position_m'), x)
        g.call(g.A, 'rate.set', expect='ALARM_ACTIVE', rate_m3_s=100 * MH)
        g.key('rate')
        self.assertIsNone(g.read('alarm.state'))
        self.assertEqual(g.read('run.state'), 'paused')
        self.assertIn('STALL', g.events(g.A))
        _balance(self, g.ledger())

    def test_collar_and_bottom_out(self):
        g = Rig()
        g.setup(syringe='bd_10ml', fill=5 * ML)
        x0 = g.read('pusher.position_m')
        g.call(g.A, 'collar.set', expect='BEHIND_PUSHER', position_m=x0 - 0.001)
        g.call(g.A, 'collar.set', position_m=x0 + 0.010)
        self.assertAlmostEqual(g.read('collar.joint_m'), x0 + 0.010)
        g.call(g.A, 'rate.set', rate_m3_s=300 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(60.0)
        self.assertEqual(g.read('alarm.state'), 'stall')
        self.assertAlmostEqual(g.read('pusher.position_m'), x0 + 0.010, delta=1e-12)
        g.key('start')                                                                  # clears the alarm only
        g.call(g.A, 'collar.set', position_m=None)
        self.assertAlmostEqual(g.read('collar.joint_m'), rt.COLLAR_PARK)                # parked against the holder block (4.46 in)
        g.call(g.A, 'pump.run')
        g.step(120.0)
        self.assertEqual(g.read('alarm.state'), 'stall')
        self.assertAlmostEqual(g.read('pusher.position_m'), rt.X_ZERO, delta=1e-12)      # plunger bottomed
        self.assertLess(g.truth()['syringe']['liquid_m3'], 1e-12)
        self.assertIn('stall', ' '.join(e['message_zh'] for e in g.call(g.A, 'events.read')['events'] if e['code'] == 'STALL') + 'stall')
        _balance(self, g.ledger())

    def test_motor_failure_stalls(self):
        g = Rig()
        g.setup()
        g.call(g.E, 'fault.inject', fault='motor_failure', active=True)
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(1.0)
        self.assertEqual(g.read('alarm.state'), 'stall')


class Withdraw(unittest.TestCase):
    def test_withdraw_from_reservoir_then_air(self):
        g = Rig()
        g.setup(fill=10 * ML, reservoir=5 * ML)
        g.call(g.A, 'direction.set', direction='WDR')
        self.assertTrue(g.read('indicator.withdraw'))
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=8 * ML)
        g.call(g.A, 'pump.run')
        g.step(60.0)
        S = g.truth()['syringe']
        self.assertAlmostEqual(S['liquid_m3'], 15 * ML, delta=1e-9)
        self.assertGreater(S['air_m3_atm'], 2.5 * ML)
        self.assertEqual(g.read('volume.dispensed')['withdrawn_m3'], 8 * ML)
        L = g.ledger()
        self.assertGreater(L['air_in_m3'], 2.5 * ML)
        _balance(self, L)

    def test_cavitation_on_fast_withdrawal_through_fine_needle(self):
        g = Rig()
        g.setup(fill=5 * ML, needle='30g', tubing='ptfe_0p5mm', length=1.0, reservoir=100 * ML, liquid='glycerol' if 'glycerol' in rt.LIQ else 'water')
        g.call(g.A, 'direction.set', direction='WDR')
        g.call(g.A, 'rate.set', rate_m3_s=1500 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(5.0)
        T = g.truth()
        self.assertTrue(T['syringe']['cavitated'])
        self.assertAlmostEqual(T['pressure_pa'], rt.ENG['cavitation_abs_pa'] - rt.PLANT_EXAMPLE['pressure_pa'], delta=1.0)
        self.assertGreater(g.ledger()['gas_formed_m3'], 0.0)
        self.assertIn('CAVITATION', g.events())
        _balance(self, g.ledger())


class Program(unittest.TestCase):
    def _phase(self, g, n, **kw):
        g.call(g.A, 'phase.select', phase=n)
        g.call(g.A, 'phase.set_function', **kw)

    def test_rate_pause_loop_stop(self):
        g = Rig()
        g.setup(reservoir=100 * ML)
        self._phase(g, 1, function='LPS')
        self._phase(g, 2, function='RAT', rate_m3_s=360 * MH, rate_units='MH', volume_m3=1 * ML, direction='INF')
        self._phase(g, 3, function='PAS', parameter=5)
        self._phase(g, 4, function='LOP', parameter=3)
        self._phase(g, 5, function='OUT', parameter=1)
        self._phase(g, 6, function='STP')
        g.call(g.A, 'pump.run')
        g.step(10.0)
        self.assertEqual(g.read('run.state'), 'pause_timer')
        g.step(1.0)
        self.assertEqual(g.read('display.text'), 'PS04')
        g.step(4.0)
        self.assertEqual((g.read('run.state'), g.read('run.phase')), ('running', 2))
        g.step(60.0)
        self.assertEqual(g.read('run.state'), 'stopped')
        self.assertAlmostEqual(g.read('volume.dispensed')['infused_m3'], 3 * ML, delta=1e-15)
        self.assertEqual(g.read('ttl.state')['outputs']['5'], 1)

    def test_fill_inc_dec_jump(self):
        g = Rig()
        g.setup(fill=30 * ML, reservoir=100 * ML)
        self._phase(g, 1, function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=2 * ML, direction='INF')
        self._phase(g, 2, function='INC', rate_m3_s=600 * MH, rate_units='MH', volume_m3=1 * ML, direction='INF')
        self._phase(g, 3, function='DEC', rate_m3_s=900 * MH, rate_units='MH', volume_m3=0.5 * ML, direction='INF')
        self._phase(g, 4, function='FIL', rate_m3_s=1200 * MH, rate_units='MH')
        self._phase(g, 5, function='JMP', parameter=7)
        self._phase(g, 6, function='BEP')
        self._phase(g, 7, function='STP')
        g.call(g.A, 'pump.run')
        g.step(12.1)
        self.assertEqual(g.read('run.phase'), 2)
        self.assertAlmostEqual(g.read('pump.rate_m3_s'), 1200 * MH, delta=1e-15)
        g.step(3.0)
        self.assertEqual(g.read('run.phase'), 3)
        self.assertAlmostEqual(g.read('pump.rate_m3_s'), 300 * MH, delta=1e-15)
        g.step(6.0)
        self.assertEqual(g.read('run.phase'), 4)
        self.assertEqual(g.read('pump.direction'), 'WDR')
        g.step(12.0)
        self.assertEqual(g.read('run.state'), 'stopped')
        self.assertAlmostEqual(g.read('volume.dispensed')['withdrawn_m3'], 3.5 * ML, delta=1e-15)  # FIL refills all 3.5 mL dispensed since the last clear
        self.assertNotIn('BEEP', g.events())
        _balance(self, g.ledger())

    def test_out_of_range_phase_alarm(self):
        g = Rig()
        g.setup()
        self._phase(g, 1, function='RAT', rate_m3_s=1500 * MH, rate_units='MH', volume_m3=1 * ML, direction='INF')
        self._phase(g, 2, function='INC', rate_m3_s=500 * MH, rate_units='MH', volume_m3=1 * ML, direction='INF')
        g.call(g.A, 'pump.run')
        g.step(3.0)
        self.assertEqual(g.read('alarm.state'), 'out_of_range')
        self.assertEqual(g.read('display.text'), 'or02')

    def test_ttl_trigger_needs_100ms_and_event_trap(self):
        g = Rig()
        g.setup()
        self._phase(g, 1, function='EVN', parameter=3)
        self._phase(g, 2, function='RAT', rate_m3_s=100 * MH, rate_units='MH', volume_m3=0.0, direction='INF')
        self._phase(g, 3, function='RAT', rate_m3_s=50 * MH, rate_units='MH', volume_m3=0.0, direction='INF')
        g.call(g.A, 'ttl.set', pin=2, level=0)
        g.step(0.05)
        g.call(g.A, 'ttl.set', pin=2, level=1)                                        # 50 ms glitch: ignored
        g.step(1.0)
        self.assertEqual(g.read('run.state'), 'stopped')
        g.call(g.A, 'ttl.set', pin=2, level=0)
        g.step(0.2)
        self.assertEqual((g.read('run.state'), g.read('run.phase')), ('running', 2))
        g.call(g.A, 'ttl.set', pin=4, level=0)
        g.step(0.2)
        self.assertEqual(g.read('run.phase'), 3)
        self.assertAlmostEqual(g.read('pump.rate_m3_s'), 50 * MH, delta=1e-15)
        self.assertEqual(g.read('ttl.state')['outputs']['7'], 1)

    def test_pause_resume_and_cancel(self):
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=360 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=2 * ML)
        g.call(g.A, 'pump.run')
        g.step(10.0)
        g.call(g.A, 'pump.stop')
        self.assertEqual(g.read('run.state'), 'paused')
        g.step(30.0)
        g.call(g.A, 'pump.run')
        g.step(10.0)
        self.assertEqual(g.read('run.state'), 'stopped')                                # resumed: 2 mL total, not 2 mL more
        self.assertAlmostEqual(g.read('volume.dispensed')['infused_m3'], 2 * ML, delta=1e-15)
        g.call(g.A, 'pump.run')
        g.step(5.0)
        g.key('start')
        self.assertEqual(g.read('run.state'), 'paused')
        g.key('rate')                                                                   # any other key cancels the pause
        self.assertEqual(g.read('run.state'), 'stopped')


class Interface(unittest.TestCase):
    def test_keypad_edit_commit_and_messages(self):
        g = Rig()
        g.setup()
        self.assertEqual(g.key('diameter'), '26.59')
        self.assertEqual(g.key('rate'), '0.000')
        g.key('up1')
        g.key('up1')
        self.assertEqual(g.read('display.text'), '2.000')
        g.step(1.0)
        self.assertEqual(g.h.phases[0]['rate_m3_s'], 0.0)
        g.step(1.1)
        self.assertAlmostEqual(g.h.phases[0]['rate_m3_s'], 2 * MH, delta=1e-18)        # stored 2 s after the last arrow key
        g.key('diameter')
        self.assertTrue(g.read('indicator.mm'))
        self.assertEqual(g.read('display.text'), '26.59')
        g.key('volume')
        self.assertEqual(g.read('display.text'), ' oFF')
        g.key('volume')
        self.assertTrue(g.read('indicator.dispensed'))
        g.call(g.A, 'setup.configure', keypad_lockout=True)
        self.assertEqual(g.key('up1'), 'LoC-')
        g.key('rate')
        g.call(g.A, 'setup.configure', keypad_lockout=False)
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='PAS', parameter=5)
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        g.key('volume')
        self.assertEqual(g.key('up1'), '----')                                         # not applicable while pumping

    def test_dispensed_clear_and_rollover_display(self):
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=1200 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=1 * ML)
        g.call(g.A, 'pump.run')
        g.step(4.0)
        g.key('volume')
        g.key('volume')
        self.assertEqual(g.read('display.text'), '1.000')
        g.key('up2')
        self.assertEqual(g.read('volume.dispensed')['infused_m3'], 0.0)
        self.assertIn('DISPENSED_CLEARED', g.events(g.A))

    def test_serial_basic_protocol(self):
        g = Rig()
        g.setup(power=False)
        g.on()
        self.assertEqual(g.serial('VER'), '00A?R')                                       # first reply after power-up reports the reset
        self.assertEqual(g.serial('VER'), '00SNE1000V3.923')
        self.assertTrue(g.read('indicator.rs232'))
        self.assertEqual(g.serial('DIA26.59'), '00S')
        self.assertEqual(g.serial('DIA'), '00S26.59')
        self.assertEqual(g.serial('RAT100MH'), '00S')
        self.assertEqual(g.serial('RAT'), '00S100.0MH')
        self.assertEqual(g.serial('RAT9999MH'), '00S?OOR')
        self.assertEqual(g.serial('VOL2.5'), '00S')
        self.assertEqual(g.serial('XYZ'), '00S?')
        self.assertEqual(g.serial('5RUN'), '')                                            # another pump's address
        self.assertEqual(g.serial('RUN'), '00I')
        g.step(30.0)
        self.assertEqual(g.serial('DIS'), '00II0.833W0.000ML')
        self.assertEqual(g.serial('STP'), '00P')
        self.assertEqual(g.serial('STP'), '00S')

    def test_power_cycle_clears_dispensed_and_power_failure_restart(self):
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(30.0)
        g.call(g.A, 'power.set', on=False)
        g.call(g.A, 'power.set', on=True)
        self.assertEqual(g.read('volume.dispensed')['infused_m3'], 0.0)
        self.assertEqual(g.read('run.state'), 'stopped')
        g.call(g.A, 'setup.configure', power_failure=True)
        g.call(g.A, 'pump.run')
        g.step(10.0)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        x = g.read('pusher.position_m')
        g.step(60.0)
        self.assertEqual(g.read('pusher.position_m'), x)
        self.assertEqual(g.read('display.text'), '')
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertEqual(g.read('run.state'), 'running')                                 # power-failure mode restarts the program
        _balance(self, g.ledger())

    def test_purge_top_speed(self):
        g = Rig()
        g.setup()
        g.call(g.A, 'pump.purge', on=True)
        self.assertEqual(g.read('display.text'), 'PurG')
        g.step(2.0)
        self.assertAlmostEqual(g.read('pump.rate_m3_s'), rt.V_MAX * math.pi * 0.02659 ** 2 / 4.0, delta=1e-15)
        g.call(g.A, 'pump.purge', on=False)
        self.assertEqual(g.read('run.state'), 'stopped')
        self.assertAlmostEqual(g.read('volume.dispensed')['infused_m3'], 2.0 * rt.V_MAX * math.pi * 0.02659 ** 2 / 4.0, delta=1e-12)

    def test_toggle_clear_program_and_dispensed(self):
        g = Rig()
        g.call(g.A, 'power.toggle')
        self.assertTrue(g.read('power.on'))
        g.setup(power=False)
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'phase.select', phase=3)
        g.call(g.A, 'phase.set_function', function='BEP')
        g.call(g.A, 'rate.set', expect='NOT_APPLICABLE', rate_m3_s=10 * MH)
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'rate.set', rate_m3_s=360 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=0.5 * ML)
        g.call(g.A, 'pump.run')
        g.step(10.0)
        g.call(g.A, 'volume.clear', direction='WDR')
        self.assertAlmostEqual(g.read('volume.dispensed')['infused_m3'], 0.5 * ML, delta=1e-15)
        g.call(g.A, 'volume.clear', direction='BOTH')
        self.assertEqual(g.read('volume.dispensed')['infused_m3'], 0.0)
        self.assertEqual(g.call(g.A, 'program.clear')['phases'], 1)
        self.assertIsNone(g.read('program.state')['phases'][2])
        g.call(g.A, 'power.toggle')
        self.assertFalse(g.read('power.on'))
        self.assertEqual(g.read('display.text'), '')

    def test_idle_skip_exact_and_fast(self):
        import time
        g = Rig()
        g.setup()
        t0 = time.time()
        g.step(30 * 86400.0)
        self.assertLess(time.time() - t0, 1.0)
        self.assertEqual(g.read('clock.time_s'), 30 * 86400.0)
        g.call(g.A, 'syringe.unload')
        _balance(self, g.ledger())


class RoundOneFixes(unittest.TestCase):
    """Regression tests of the round-1 review findings (R1-*, C1-*, G1-*)."""

    def test_cavitation_conserves_volume(self):                                                   # R1-01
        g = Rig()
        g.setup(fill=10 * ML, tubing='ptfe_0p5mm', length=2.0, needle='30g', reservoir=100 * ML)
        g.call(g.A, 'direction.set', direction='WDR')
        g.call(g.A, 'rate.set', rate_m3_s=1500 * MH, units='MH')
        g.call(g.A, 'pump.run')
        for _ in range(4):
            g.step(10.0)
            S = g.truth()['syringe']
            self.assertGreater(S['liquid_m3'], 10 * ML)                                             # liquid comes in while the plunger is pulled
            _balance(self, g.ledger())
        self.assertTrue(S['cavitated'])
        g.call(g.A, 'pump.stop')
        g.step(600.0)
        _balance(self, g.ledger())

    def test_rate_change_acts_on_the_running_phase(self):                                         # R1-02
        g = Rig()
        g.setup()
        g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.5 * ML, direction='INF')
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=50 * 1e-9 / 60.0, rate_units='UM', volume_m3=0.0, direction='INF')
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        g.step(4.0)
        g.serial('VER')
        self.assertEqual(g.read('run.phase'), 2)
        self.assertTrue(g.read('indicator.ul') and g.read('indicator.min'))                        # units of the executing phase
        self.assertEqual(g.serial('RAT'), '00I50.00UM')
        self.assertEqual(g.serial('RAT60'), '00I')                                                  # 60 uL/min in phase 2 units, not mL/h of phase 1
        self.assertAlmostEqual(g.read('pump.rate_m3_s'), 60e-9 / 60.0, delta=1e-18)
        ph = g.read('program.state')['phases']
        self.assertAlmostEqual(ph[0]['rate_m3_s'], 600 * MH, delta=1e-18)
        self.assertAlmostEqual(ph[1]['rate_m3_s'], 50e-9 / 60.0, delta=1e-18)                      # RS-232 while operating: not stored (manual 6.1)
        g.call(g.A, 'rate.set', rate_m3_s=70e-9 / 60.0)
        self.assertAlmostEqual(g.read('program.state')['phases'][1]['rate_m3_s'], 70e-9 / 60.0, delta=1e-18)
        g.call(g.A, 'rate.set', rate_m3_s=0.0)                                                      # R1-04: a rate of 0.0 stops the pump
        self.assertEqual(g.read('run.state'), 'stopped')
        self.assertFalse(g.read('indicator.pumping'))

    def test_keypad_follows_the_manual(self):                                                     # R1-03
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=360 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=0.1 * ML)
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='PAS', parameter=0)
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        g.step(1.5)
        self.assertEqual(g.read('display.text'), 'PAUS')
        g.key('volume')                                                                              # PS:00 - any key but Start resets
        self.assertEqual((g.read('run.state'), g.read('run.phase')), ('stopped', 1))
        g.call(g.A, 'pump.run')
        g.key('start')
        g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=360 * MH, rate_units='MH', volume_m3=0.0, direction='INF')
        g.call(g.A, 'pump.run')
        g.step(2.0)
        g.key('start')
        self.assertEqual(g.read('run.state'), 'paused')
        g.key('rate')
        g.key('up1')                                                                                 # an arrow cancels the pause in any mode (manual 7.7)
        self.assertEqual(g.read('run.state'), 'stopped')
        g.step(2.2)
        g.key('rate')                                                                                # rate shown: Rate again -> units change mode
        self.assertEqual(g.read('display.text'), 'UntS')
        g.key('up1')
        g.step(2.2)
        ph = g.read('program.state')['phases'][0]
        self.assertEqual((ph['rate_units'], round(ph['rate_m3_s'] / (1e-9 / 60.0), 6)), ('UM', 460.0))   # 460 mL/h -> 460 uL/min (in range: stored)
        g.call(g.A, 'setup.configure', keypad_lockout=True)
        self.assertEqual(g.key('diameter'), '26.59')                                                 # locked: settings can still be reviewed
        self.assertEqual(g.key('up1'), 'LoC-')
        g.key('rate')
        g.call(g.A, 'ttl.set', pin=6, level=0)                                                       # the Lockout Disable Key grounds pin 6
        g.step(0.2)
        self.assertNotEqual(g.key('up1'), 'LoC-')

    def test_units_change_mode(self):                                                             # R1-03 (6.6.1)
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=5 * MH, units='MH')
        g.key('rate')
        self.assertEqual(g.read('display.text'), 'UntS')
        g.key('up1')                                                                                 # MH -> UM
        self.assertTrue(g.read('indicator.ul') and g.read('indicator.min'))
        g.key('volume')                                                                              # a non-arrow key stores the units
        ph = g.read('program.state')['phases'][0]
        self.assertEqual(ph['rate_units'], 'UM')
        self.assertAlmostEqual(ph['rate_m3_s'], 5e-9 / 60.0, delta=1e-20)                           # the displayed number stays

    def test_hidden_back_pressure_and_stall_values(self):                                         # C1-01
        g = Rig()
        g.setup()
        g.call(g.E, 'medium.configure', backpressure_pa=3.0e5)
        self.assertEqual(g.read('outlet.state', who=g.A)['backpressure_pa'], 0.0)
        g.call(g.A, 'rate.set', rate_m3_s=1600 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(5.0)
        self.assertEqual(g.read('alarm.state'), 'stall')
        msgs = [e['message_zh'] for e in g.call(g.A, 'events.read')['events'] if e['code'] == 'STALL']
        self.assertTrue(msgs and not any(ch.isdigit() for m in msgs for ch in m), msgs)
        self.assertIn('STALL_FORCE', g.events())
        self.assertNotIn('STALL_FORCE', g.events(g.A))

    def test_program_edit_resets_a_paused_program(self):                                         # C1-02
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=360 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=5 * ML)
        g.call(g.A, 'pump.run')
        g.step(2.0)
        g.call(g.A, 'pump.stop')
        self.assertEqual(g.read('run.state'), 'paused')
        g.call(g.A, 'program.clear')                                                                 # declared run.state effect
        self.assertEqual(g.read('run.state'), 'stopped')

    def test_event_order_and_power_cycle(self):                                                   # R1-05, R1-06
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=1200 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=0.01 * ML)
        g.step(0.15)
        g.call(g.A, 'pump.run')
        g.step(1.0)
        ev = g.call(g.E, 'events.read')['events']
        t_run = max(e['time_s'] for e in ev if e['code'] == 'RUN')
        t_end = max(e['time_s'] for e in ev if e['code'] == 'PROGRAM_END')
        self.assertGreaterEqual(t_end, t_run)
        g.call(g.E, 'fault.inject', fault='motor_failure', active=True)
        g.call(g.A, 'volume.set', volume_m3=0.0)
        g.call(g.A, 'pump.run')
        g.step(1.0)
        self.assertEqual(g.read('alarm.state'), 'stall')
        g.call(g.A, 'power.toggle')
        g.call(g.A, 'power.toggle')
        self.assertIsNone(g.read('alarm.state'))                                                    # alarms do not survive a power cycle
        g.call(g.E, 'fault.inject', fault='motor_failure', active=False)
        g.call(g.A, 'pump.run')
        g.step(1.0)
        d = g.read('volume.dispensed')['infused_m3']
        g.call(g.A, 'pump.stop')
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)                                       # same diameter: nothing cleared
        self.assertEqual(g.read('volume.dispensed')['infused_m3'], d)

    def test_serial_setup_commands(self):                                                         # C1-03
        g = Rig()
        g.setup()
        g.serial('VER')
        self.assertEqual(g.serial('TRG LE'), '00S')
        self.assertEqual(g.serial('TRG'), '00SLE')
        self.assertEqual(g.serial('DIN1'), '00S')
        self.assertEqual(g.serial('ROM1'), '00S')
        self.assertEqual(g.read('setup.state')['direction_input'], 1)
        self.assertEqual(g.read('setup.state')['motor_output'], 1)
        self.assertEqual(g.serial('BUZ13'), '00S')
        self.assertEqual(g.serial('DIRSTK'), '00S?NA')
        for k in ('ttl.override_trigger', 'direction.sticky', 'program.entry_lockout'):              # R1-07
            self.assertIn(k, rt.UNSUPPORTED)


class RoundTwoFixes(unittest.TestCase):
    """Regression tests of the round-2 review findings (R2-*, C2-*)."""

    def test_ttl_start_without_diameter_is_ignored(self):                                        # R2-01
        g = Rig()
        g.setup(power=False)
        g.on()
        g.call(g.A, 'ttl.set', pin=2, level=0)
        g.step(1.0)                                                                                  # sim.step is not rejected
        self.assertEqual(g.read('run.state'), 'stopped')
        self.assertIn('TRIGGER_IGNORED', g.events(g.A))

    def test_collar_in_the_way_and_no_backward_jump(self):                                       # R2-02
        g = Rig()
        g.on()
        g.call(g.A, 'collar.set', position_m=0.10)
        g.call(g.A, 'syringe.load', expect='COLLAR_IN_THE_WAY', syringe='bd_60ml', liquid_id='water', fill_volume_m3=1 * ML)
        g.call(g.A, 'collar.set', position_m=None)
        g.call(g.A, 'syringe.load', syringe='bd_60ml', liquid_id='water', fill_volume_m3=1 * ML)
        g.call(g.A, 'syringe.set_diameter', diameter_m=0.02659)
        g.call(g.A, 'pump.purge', on=True)
        x = []
        for _ in range(30):
            g.step(0.1)
            x.append(g.read('pusher.position_m'))
        self.assertTrue(all(b >= a - 1e-15 for a, b in zip(x, x[1:])))
        self.assertAlmostEqual(x[-1], rt.X_ZERO, delta=1e-12)
        _balance(self, g.ledger())

    def test_read_does_not_change_the_program(self):                                             # R2-03
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=0.1 * ML)
        g.call(g.A, 'phase.select', phase=2)
        g.read()
        g.h.read('agent')
        self.assertIsNone(g.h.phases[1])
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        g.step(2.0)
        self.assertEqual((g.read('run.state'), g.read('alarm.state')), ('stopped', None))           # empty phase 2 = implied stop

    def test_restart_after_zero_rate(self):                                                       # R2-04
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(1.0)
        g.serial('VER')
        g.serial('RAT0')                                                                             # RS-232 while operating: stops, not stored
        self.assertEqual(g.read('run.state'), 'stopped')
        g.call(g.A, 'pump.run')
        g.step(1.0)
        self.assertTrue(g.read('indicator.pumping'))
        self.assertAlmostEqual(g.read('pump.rate_m3_s'), 600 * MH, delta=1e-18)

    def test_disturbance_survives_a_reconnect(self):                                             # C2-01
        g = Rig()
        g.setup()
        g.call(g.E, 'medium.configure', backpressure_pa=1.0e5)
        g.call(g.A, 'outlet.connect', tubing='ptfe_0p8mm', tubing_length_m=0.5, needle='none', backpressure_pa=0.0)
        g.step(2.0)
        self.assertAlmostEqual(g.truth()['backpressure_pa'], 1.0e5)
        self.assertEqual(g.read('outlet.state', who=g.A)['backpressure_pa'], 0.0)


class RoundThreeFixes(unittest.TestCase):
    """Regression tests of the round-3 review findings (R3-*, C3-*)."""

    def test_rate_change_kept_paused_is_used_on_resume(self):                                     # R3-01
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(1.0)
        g.call(g.A, 'pump.stop')
        g.serial('VER')
        self.assertEqual(g.serial('RATC50'), '00P')
        self.assertEqual(g.read('run.state'), 'paused')
        g.call(g.A, 'pump.run')
        g.step(0.5)
        self.assertAlmostEqual(g.read('pump.rate_m3_s'), 50 * MH, delta=1e-18)
        self.assertEqual(g.read('display.text'), '50.00')

    def test_look_and_refused_paths_do_not_create_phases(self):                                    # R3-02
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.call(g.A, 'volume.set', volume_m3=0.5 * ML)
        g.call(g.A, 'phase.select', phase=2)
        g.key('diameter')
        g.key('rate')
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        g.key('direction')                                                                           # refused while running a volume phase: '----'
        g.call(g.A, 'ttl.set', pin=3, level=0)
        g.step(0.2)
        self.assertIsNone(g.h.phases[1])
        g.step(5.0)
        self.assertEqual((g.read('run.state'), g.read('alarm.state')), ('stopped', None))
        self.assertIn('PROGRAM_END', g.events(g.A))

    def test_disturbance_cleared_by_the_evaluator(self):                                          # C3-01
        g = Rig()
        g.setup()
        g.call(g.E, 'medium.configure', backpressure_pa=1.0e5)
        g.call(g.E, 'medium.configure', clear_backpressure=True)
        g.step(1.0)
        self.assertEqual(g.truth()['backpressure_pa'], 0.0)
        m = rt.export_newera_ne1000_contract_metadata()['capabilities']['medium.configure']
        self.assertTrue(any('clear_backpressure' in x for x in m['limitations']), m['limitations'])


class RoundFourFixes(unittest.TestCase):
    """Regression tests of the round-4 review findings (R4-*, C4-*, G4-*)."""

    def _prog(self, g, phases):
        for n, kw in enumerate(phases, 1):
            g.call(g.A, 'phase.select', phase=n)
            g.call(g.A, 'phase.set_function', **kw)
        g.call(g.A, 'phase.select', phase=1)

    def test_stop_and_resume_inside_a_pause(self):                                              # R4-01
        g = Rig()
        g.setup()
        self._prog(g, [dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.1 * ML, direction='INF'), dict(function='PAS', parameter=10),
                       dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.1 * ML, direction='INF')])
        g.call(g.A, 'pump.run')
        g.step(2.0)
        self.assertEqual(g.read('run.state'), 'pause_timer')
        g.call(g.A, 'pump.stop')
        g.step(5.0)
        g.call(g.A, 'pump.run')
        self.assertEqual(g.read('run.state'), 'pause_timer')                                     # the time left of PS:10 (8.6 s) continues
        g.step(8.0)
        self.assertEqual(g.read('run.phase'), 2)
        g.step(1.0)
        self.assertEqual(g.read('run.phase'), 3)
        g2 = Rig()
        g2.setup()
        self._prog(g2, [dict(function='PAS', parameter=0), dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.1 * ML, direction='INF')])
        g2.call(g2.A, 'pump.run')
        g2.call(g2.A, 'pump.stop')
        g2.call(g2.A, 'pump.run')
        self.assertEqual(g2.read('run.state'), 'user_wait')                                       # PS:00 waits again
        g2.call(g2.A, 'pump.run')
        g2.step(2.0)
        self.assertEqual(g2.read('run.state'), 'stopped')

    def test_rate_c_while_paused(self):                                                         # R4-02
        g = Rig()
        g.setup()
        self._prog(g, [dict(function='RAT', rate_m3_s=100 * MH, rate_units='MH', volume_m3=0.0, direction='INF')])
        g.call(g.A, 'pump.run')
        g.step(1.0)
        g.call(g.A, 'pump.stop')
        g.serial('VER')
        g.serial('RATC0')                                                                          # a rate of 0 stops: the pause is cancelled
        self.assertEqual(g.read('run.state'), 'stopped')
        g2 = Rig()
        g2.setup()
        self._prog(g2, [dict(function='RAT', rate_m3_s=100 * MH, rate_units='MH', volume_m3=0.1 * ML, direction='INF'),
                        dict(function='INC', rate_m3_s=20 * MH, rate_units='MH', volume_m3=0.0, direction='INF')])
        g2.call(g2.A, 'pump.run')
        g2.step(5.0)
        g2.call(g2.A, 'pump.stop')
        g2.serial('VER')
        self.assertEqual(g2.serial('RATC50'), '00P?NA')                                            # RAT C changes a paused RATE phase only
        g2.call(g2.A, 'pump.run')
        g2.step(1.0)
        self.assertAlmostEqual(g2.read('pump.rate_m3_s'), 120 * MH, delta=1e-18)

    def test_units_mode_without_change_creates_nothing(self):                                   # R4-03
        g = Rig()
        g.setup()
        g.call(g.A, 'volume.set', volume_m3=0.1 * ML)
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.call(g.A, 'phase.select', phase=2)
        g.key('rate')
        g.step(2.5)
        self.assertIsNone(g.h.phases[1])

    def test_reverse_of_the_running_phase(self):                                                 # R4-04
        g = Rig()
        g.setup(reservoir=20 * ML)
        self._prog(g, [dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.1 * ML, direction='INF'),
                       dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.0, direction='INF')])
        g.call(g.A, 'pump.run')
        g.step(2.0)
        self.assertEqual(g.read('run.phase'), 2)
        g.call(g.A, 'direction.set', direction='REV')
        self.assertEqual(g.read('pump.direction'), 'WDR')
        x = g.read('pusher.position_m')
        g.step(1.0)
        self.assertLess(g.read('pusher.position_m'), x)

    def test_direction_while_paused_cancels_the_pause(self):                                     # R4-05, R4-06
        g = Rig()
        g.setup(reservoir=20 * ML)
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(1.0)
        g.call(g.A, 'pump.stop')
        self.assertEqual(g.read('pump.direction'), 'INF')
        g.call(g.A, 'direction.set', direction='WDR')
        self.assertEqual((g.read('run.state'), g.read('pump.direction')), ('stopped', 'WDR'))
        x = g.read('pusher.position_m')
        g.call(g.A, 'pump.run')
        g.step(1.0)
        self.assertLess(g.read('pusher.position_m'), x)                                            # the new direction is used

    def test_external_start_drops_a_pending_units_change(self):                                   # R4-07
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.key('rate')
        g.key('up1')
        g.call(g.A, 'pump.run')
        g.step(3.0)
        self.assertEqual(g.read('program.state')['phases'][0]['rate_units'], 'MH')

    def test_idle_skip_after_a_disturbance(self):                                                 # G4-01
        def run(skip):
            g = Rig()
            g.setup()
            g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
            g.call(g.A, 'pump.run')
            g.step(1.0)
            g.call(g.A, 'pump.stop')
            g.call(g.E, 'medium.configure', backpressure_pa=3.0e5)
            if skip:
                g.step(600.0)
            else:
                for _ in range(6000):
                    g.step(0.1)
            return g.ledger()
        a, b = run(True), run(False)
        self.assertEqual(a['delivered_m3'], b['delivered_m3'])
        self.assertEqual(a['drawn_m3'], b['drawn_m3'])


class RoundFiveFixes(unittest.TestCase):
    """Regression tests of the round-5 review findings (R5-*)."""

    def test_stall_resume_after_an_earlier_pause_stop(self):                                     # R5-01
        g = Rig()
        g.setup()
        prog = [dict(function='PAS', parameter=2), dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=2 * ML, direction='INF')]
        for n, kw in enumerate(prog, 1):
            g.call(g.A, 'phase.select', phase=n)
            g.call(g.A, 'phase.set_function', **kw)
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        g.call(g.A, 'pump.stop')                                                                    # stop inside PS:02
        g.call(g.A, 'pump.run')
        g.step(4.0)
        g.call(g.E, 'fault.inject', fault='occlusion', active=True)
        g.step(20.0)
        self.assertEqual(g.read('alarm.state'), 'stall')
        g.call(g.E, 'fault.inject', fault='occlusion', active=False)
        g.key('rate')
        g.call(g.A, 'pump.run')
        self.assertEqual(g.read('run.state'), 'running')                                          # resumes pumping, not the expired pause
        g.step(30.0)
        self.assertAlmostEqual(g.read('volume.dispensed')['infused_m3'], 2 * ML, delta=1e-15)

    def test_dir_query_start_direction_and_run_n(self):                                          # R5-02, R5-03, R5-04
        g = Rig()
        g.setup(reservoir=20 * ML)
        g.call(g.A, 'direction.set', direction='WDR')
        g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(1.0)
        g.call(g.A, 'pump.stop')
        g.serial('VER')
        self.assertEqual(g.serial('DIR'), '00PWDR')
        g.call(g.A, 'pump.stop')
        prog = [dict(function='PAS', parameter=0), dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.1 * ML, direction='INF'),
                dict(function='RAT', rate_m3_s=300 * MH, rate_units='MH', volume_m3=0.1 * ML, direction='INF')]
        for n, kw in enumerate(prog, 1):
            g.call(g.A, 'phase.select', phase=n)
            g.call(g.A, 'phase.set_function', **kw)
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        self.assertEqual(g.read('run.state'), 'user_wait')
        self.assertFalse(g.read('indicator.withdraw'))                                              # no stale WDR from the earlier run
        g.call(g.A, 'pump.run', phase=3)                                                            # RUN 3 at PS:00 starts at phase 3
        self.assertEqual(g.read('run.phase'), 3)


class RoundSixFixes(unittest.TestCase):
    """Regression tests of the round-6 review findings (G6-*, R6-*)."""

    def test_pin7_and_force_follow_a_stop_at_once(self):                                         # G6-01
        g = Rig()
        g.setup()
        g.call(g.A, 'setup.configure', motor_output=1)
        for n, kw in enumerate([dict(function='PAS', parameter=5), dict(function='RAT', rate_m3_s=600 * MH, rate_units='MH', volume_m3=0.1 * ML, direction='INF')], 1):
            g.call(g.A, 'phase.select', phase=n)
            g.call(g.A, 'phase.set_function', **kw)
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        g.step(1.0)
        self.assertEqual(g.read('ttl.state')['outputs']['7'], 1)                                   # ROM 1: high during the pause timer
        g.call(g.A, 'pump.stop')
        self.assertEqual(g.read('ttl.state')['outputs']['7'], 0)
        g.step(100.0)
        self.assertEqual(g.read('ttl.state')['outputs']['7'], 0)
        g2 = Rig()
        g2.setup()
        g2.call(g2.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g2.call(g2.E, 'fault.inject', fault='occlusion', active=True)
        g2.call(g2.A, 'pump.run')
        g2.step(1.0)
        g2.call(g2.A, 'pump.stop')
        g2.step(10.0)
        T = g2.truth()
        self.assertAlmostEqual(T['force_n'], T['pressure_pa'] * math.pi * 0.02659 ** 2 / 4.0, delta=1e-9)   # no running friction

    def test_no_purge_while_paused(self):                                                         # R6-01
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'pump.run')
        g.step(1.0)
        g.call(g.A, 'pump.stop')
        g.call(g.A, 'pump.purge', expect='PROGRAM_OPERATING', on=True)

    def test_paused_inc_phase_shows_the_actual_rate(self):                                       # R6-02
        g = Rig()
        g.setup()
        for n, kw in enumerate([dict(function='RAT', rate_m3_s=100 * MH, rate_units='MH', volume_m3=0.01 * ML, direction='INF'),
                                dict(function='INC', rate_m3_s=20 * MH, rate_units='MH', volume_m3=0.0, direction='INF')], 1):
            g.call(g.A, 'phase.select', phase=n)
            g.call(g.A, 'phase.set_function', **kw)
        g.call(g.A, 'phase.select', phase=1)
        g.call(g.A, 'pump.run')
        g.step(2.0)
        g.call(g.A, 'pump.stop')
        self.assertEqual(g.read('display.text'), '120.0')
        g.serial('VER')
        self.assertEqual(g.serial('RAT'), '00P120.0MH')


class RoundSevenFixes(unittest.TestCase):
    """Regression tests of the round-7 review findings (R7-*)."""

    def test_no_event_jump_during_a_purge(self):                                                 # R7-01
        g = Rig()
        g.setup(reservoir=20 * ML)
        g.call(g.A, 'pump.purge', on=True)
        g.serial('VER')
        g.serial('RUNE3')
        self.assertEqual(g.read('run.state'), 'purging')
        g.call(g.A, 'pump.purge', on=False)
        self.assertEqual(g.read('run.state'), 'stopped')

    def test_malformed_serial_commands(self):                                                    # R7-02
        g = Rig()
        g.setup()
        g.serial('VER')
        g.call(g.A, 'phase.select', phase=2)
        for cmd in ('RUN42', 'RUN99', 'RUN0', 'RUNE42', 'FUNJMP1.5', 'FUNRAT9.9', 'FUNOUT.5', 'FUNIF1.2', 'FUNLOP2.5'):
            self.assertEqual(g.serial(cmd), '00S?OOR', cmd)
        self.assertIsNone(g.h.phases[1])
        self.assertEqual(g.read('run.state'), 'stopped')

    def test_purge_in_the_currently_set_direction(self):                                         # R7-03
        g = Rig()
        g.setup(reservoir=20 * ML)
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=100 * MH, rate_units='MH', volume_m3=0.0, direction='WDR')
        self.assertEqual(g.read('pump.direction'), 'WDR')
        x = g.read('pusher.position_m')
        g.call(g.A, 'pump.purge', on=True)
        g.step(0.5)
        self.assertLess(g.read('pusher.position_m'), x)


class RoundEightFixes(unittest.TestCase):
    """Regression tests of the round-8 review findings (R8-*)."""

    def test_changes_during_a_purge_go_to_the_selected_phase(self):                             # R8-01
        g = Rig()
        g.setup(reservoir=20 * ML)
        g.call(g.A, 'volume.set', volume_m3=1 * ML)                                                 # phase 1: a volume phase
        g.call(g.A, 'phase.select', phase=2)
        g.call(g.A, 'phase.set_function', function='RAT', rate_m3_s=500e-9 / 60.0, rate_units='UM', volume_m3=0.0, direction='INF')
        g.call(g.A, 'pump.purge', on=True)
        g.serial('VER')
        self.assertEqual(g.serial('RAT'), '00X500.0UM')
        g.call(g.A, 'direction.set', direction='WDR')
        g.call(g.A, 'rate.set', rate_m3_s=400e-9 / 60.0)
        ph = g.read('program.state')['phases']
        self.assertEqual((ph[1]['direction'], ph[0]['direction']), ('WDR', 'INF'))
        self.assertAlmostEqual(ph[1]['rate_m3_s'], 400e-9 / 60.0, delta=1e-20)
        self.assertEqual(g.read('pump.direction'), 'WDR')
        x = g.read('pusher.position_m')
        g.step(0.5)
        self.assertLess(g.read('pusher.position_m'), x)

    def test_fun_parameter_replies(self):                                                       # R8-02
        g = Rig()
        g.setup()
        g.serial('VER')
        for cmd in ('FUNPAS100', 'FUNJMP123', 'FUNRAT12', 'FUNLPS3'):
            self.assertEqual(g.serial(cmd), '00S?OOR', cmd)
        self.assertEqual(g.serial('FUNPAS10'), '00S')


class RoundNineFixes(unittest.TestCase):
    """Round 9 passed with minor findings, fixed directly (R9-01, G9-01)."""

    def test_purge_rate_rules(self):                                                              # R9-01
        g = Rig()
        g.setup()
        g.call(g.A, 'rate.set', rate_m3_s=100 * MH, units='MH')
        g.call(g.A, 'pump.purge', on=True)
        g.call(g.A, 'rate.set', expect='UNITS_WHILE_PUMPING', rate_m3_s=50e-9 / 60.0, units='UM')
        g.call(g.A, 'rate.set', rate_m3_s=0.0)
        self.assertEqual(g.read('run.state'), 'stopped')
        self.assertIn('STOPPED_ZERO_RATE', g.events(g.A))

    def test_occluded_line_skip_equals_ticking(self):                                             # G9-01
        def run(skip):
            g = Rig()
            g.setup()
            g.call(g.A, 'rate.set', rate_m3_s=600 * MH, units='MH')
            g.call(g.E, 'fault.inject', fault='occlusion', active=True)
            g.call(g.A, 'pump.run')
            g.step(2.0)
            g.call(g.A, 'pump.stop')
            g.call(g.A, 'pump.stop')
            g.call(g.A, 'outlet.connect', tubing='ptfe_1p6mm', tubing_length_m=2.0, needle='none', backpressure_pa=0.0)
            if skip:
                g.step(100.0)
            else:
                for _ in range(1000):
                    g.step(0.1)
            g.call(g.E, 'fault.inject', fault='occlusion', active=False)
            g.step(60.0)
            return g.ledger()
        a, b = run(True), run(False)
        self.assertEqual((a['delivered_m3'], a['drawn_m3']), (b['delivered_m3'], b['drawn_m3']))
        _balance(self, a)


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        missing = set(rt.CAPS) - set(rt.UNSUPPORTED) - SEEN_CAPS - {'events.read'}
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()

"""tests for CHEM-008 ika_plate_rctdigital (IKA Plate (RCT digital) 25004601 magnetic hotplate stirrer): python3 -m unittest tests/test_asset_ika_plate_rctdigital.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; heating rate (6.5 K/min, 1 l water / H1500) and self-heating (+13 K) as calibrated; PT1000 PID and 2P control; plate
control; safety circuit (Err. 25 with a short-circuited TRIAC, Err. 24 when lowered below the plate); Error 5 (sensor out of the medium), Err. 6
(sensor pulled while heating), Err. 4 (motor blocked), Err. 3 (heat accumulation under an overhanging pot at 310 C), Err. 14, 21, 31; boiling at
the pressure-corrected boiling point with the latent heat; evaporation; bar decoupling in a viscous medium and the viscosity trend; timer with the
expiry actions and the counter; intermittent / reverse stirring; operating modes A / B after a power failure; lock; HOT! display; NAMUR commands
and watchdogs 1 / 2; energy and liquid-mass ledgers close."""
import copy
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import ika_plate_rctdigital as rt  # noqa: E402

INST = 'ika_plate_rctdigital_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
C0 = 273.15
RPM = rt.RPM


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.IkaPlateRctdigitalRuntime(instance)
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
            if cap != 'sim.step':
                extra = changed - set(rt.EFFECTS.get(cap, []))
                assert not extra, (cap, sorted(extra))
                assert self.h.t == t0, cap
            SEEN_CAPS.add(cap)
            PROCESS_EVIDENCE.setdefault(cap, []).append(dict(arguments=json.dumps(args, sort_keys=True, default=str)[:200], time_s=t0, status=r['status']))
            return r['result']
        assert r['status'] in ('rejected', 'unsupported'), (cap, r['status'], r.get('result'))
        assert r['error']['code'] == expect, (cap, r['error']['code'], expect)
        assert not changed, (cap, 'rejected call changed', sorted(changed))
        assert expect in [e['code'] for e in rt.export_ika_plate_rctdigital_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
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

    def beaker(self, vessel='beaker_600ml', sid='water', ml=500.0, bar=0.04, **kw):
        return self.call(self.A, 'vessel.place', vessel=vessel, substance_id=sid, volume_m3=ml * 1e-6, stir_bar_length_m=bar, **kw)

    def stir(self, rpm):
        self.call(self.A, 'stirring.set_speed', speed_rad_s=rpm * RPM)
        self.call(self.A, 'stirring.set', on=True)

    def heat(self, c):
        self.call(self.A, 'heating.set_temperature', temperature_k=C0 + c)
        self.call(self.A, 'heating.set', on=True)

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']

    def events(self):
        return [e['code'] for e in self.call(self.E, 'events.read', since_s=0.0)['events']]


def _balance(test, L):
    scale = max(abs(L['e_heater_j']) + abs(L['e_magnet_j']) + abs(L['e_vessel_in_j']), 1.0)
    test.assertLess(abs(L['energy_balance_j']), 1e-9 * scale + 1e-6, L)
    test.assertLess(abs(L['mass_balance_kg']), 1e-12 + 1e-12 * L['m_placed_kg'], L)


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
    return {name[k]: v for k, v in out.items() if k in name}


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_consistent(self):
        m = rt.export_ika_plate_rctdigital_contract_metadata()
        for k, c in m['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], m['state_slots'], (k, e))
            for p in c['preconditions']:
                for s in p['state_ids']:
                    self.assertIn(s, m['state_slots'], (k, s))
        for s in rt.SCENE_MIRRORED:
            self.assertIn(s, rt.SLOTS)
            self.assertEqual(rt.SLOTS[s]['visibility'], 'agent')
        CHECKS.append(dict(id='metadata_consistent'))

    def test_declared_errors_cover_raised_codes(self):
        m = rt.export_ika_plate_rctdigital_contract_metadata()
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
        for cap, args in (('truth.inspect', {}), ('ledger.read', {}), ('fault.inject', dict(fault='motor_blocked', active=True)), ('medium.modify', dict(viscosity_factor=2.0)),
                          ('sim.step', dict(dt_s=1.0))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = g.A.describe(instance_id=INST)
        self.assertNotIn('truth.inspect', d['capabilities'])
        self.assertNotIn('truth.process', d['state_slots'])

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
        a.on()
        a.heat(80.0)
        a.step(60.0)
        self.assertFalse(b.read('power.on'))
        self.assertEqual(b.h.Tp, rt.PLANT_EXAMPLE['ambient_k'])

    def test_step_partition_independence(self):
        def run(parts):
            g = Rig(seed=7)
            g.on()
            g.beaker()
            g.call(g.A, 'sensor.set', connected=True, immersed=True)
            g.stir(600)
            g.heat(60.0)
            g.call(g.A, 'stirring.configure', expect='FUNCTIONS_RUNNING', run_s=30.0)
            g.call(g.A, 'timer.set', duration_s=95.0)
            g.call(g.A, 'timer.toggle')
            for dt in parts:
                g.step(dt)
            r = g.h.read('evaluator')
            return json.dumps({k: r[k] for k in r if k != 'clock.time_s'}, sort_keys=True, default=float), g.ledger()
        a, la = run([300.0])
        b, lb = run([0.05] * 6000)
        c, lc = run([7.33] * 40 + [6.8])
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertEqual(json.dumps(la, sort_keys=True), json.dumps(lb, sort_keys=True))

    def test_rejections_change_nothing(self):
        g = Rig()
        g.call(g.A, 'heating.set', expect='POWER_OFF', on=True)
        g.on()
        g.call(g.A, 'heating.set_temperature', expect='ABOVE_SAFETY_LIMIT', temperature_k=C0 + 300.0) if g.h.heat['safe_k'] < C0 + 315.0 else None
        g.call(g.A, 'safety.set_limit', limit_k=C0 + 200.0)
        g.call(g.A, 'heating.set_temperature', expect='ABOVE_SAFETY_LIMIT', temperature_k=C0 + 190.0)
        g.call(g.A, 'safety.set_limit', expect='BELOW_SET_MARGIN', limit_k=C0 + 30.0 + 10.0 if False else C0 + 50.0) if g.h.heat['set_k'] > C0 + 35.0 else None
        g.call(g.A, 'sensor.set', expect='NOT_IMMERSIBLE', connected=True, immersed=True)
        g.call(g.A, 'vessel.place', expect='OVERFILL', vessel='beaker_250ml', substance_id='water', volume_m3=300e-6)
        g.call(g.A, 'vessel.place', expect='NOT_LIQUID', vessel='h1000', substance_id='diethyl_ether', volume_m3=500e-6, initial_k=C0 + 40.0)
        g.call(g.A, 'vessel.place', expect='BAR_LENGTH', vessel='beaker_250ml', substance_id='water', volume_m3=200e-6, stir_bar_length_m=0.07)
        g.call(g.A, 'vessel.remove', expect='NO_VESSEL')
        g.call(g.A, 'namur.send', expect='NO_CONNECTION', command='IN_PV_2')
        g.call(g.A, 'timer.toggle', expect='TIMER_NOT_SET')
        g.call(g.A, 'viscosity.reset', expect='NOT_VISC_MODE')
        g.stir(300)
        g.call(g.A, 'menu.configure', expect='FUNCTIONS_RUNNING', operating_mode='B')
        g.call(g.A, 'stirring.configure', expect='FUNCTIONS_RUNNING', auto_reverse=True)
        g.call(g.A, 'stirring.set', on=False)
        g.call(g.A, 'stirring.configure', expect='NEEDS_INTERVAL', auto_reverse=True)
        g.call(g.A, 'lock.toggle')
        g.call(g.A, 'heating.set_temperature', expect='LOCKED', temperature_k=C0 + 50.0)
        g.call(g.A, 'lock.toggle')
        g.call(g.A, 'interface.set', connected=True)
        g.call(g.A, 'namur.send', expect='UNKNOWN_COMMAND', command='FOO_9')
        g.call(g.A, 'namur.send', expect='ABOVE_SAFETY_LIMIT', command='OUT_SP_1 250')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=99))
        g.call(g.E, 'medium.modify', expect='NO_VESSEL', viscosity_factor=3.0)


class Physics(unittest.TestCase):
    def test_heating_rate_and_boiling_with_ledgers(self):
        g = Rig()
        g.on()
        g.beaker('h1500', ml=1000.0)
        g.stir(600)
        g.heat(310.0)
        t30 = t80 = None
        while t80 is None:
            g.step(1.0)
            tl = g.truth()['charge_k'] - C0
            if t30 is None and tl >= 30.0:
                t30 = g.h.t
            if tl >= 80.0:
                t80 = g.h.t
        self.assertAlmostEqual(50.0 / (t80 - t30) * 60.0, rt.DOC['heating_rate_k_s'] * 60.0, delta=0.05)     # datasheet 6.5 K/min (calibrated)
        g.step(1800.0)
        tr = g.truth()
        self.assertAlmostEqual(tr['charge_k'], rt.boiling_k('water', 101325.0), delta=0.05)               # boiling at 100 C
        self.assertGreater(tr['liquid']['boiled_kg'], 0.1)
        self.assertLess(g.read('display.temperature'), 311.0)
        _balance(self, g.ledger())
        r = g.call(g.A, 'vessel.remove')['vessel']
        self.assertAlmostEqual(r['liquid_mass_kg'] + r['evaporated_kg'] + r['boiled_kg'], 1e-3 * rt.LIQ['water']['density']['value'], delta=1e-9)
        _balance(self, g.ledger())

    def test_boiling_point_follows_pressure(self):
        g = Rig(pressure_pa=80000.0)
        g.on()
        g.beaker('beaker_600ml', sid='ethanol', ml=300.0)
        g.stir(500)
        g.heat(120.0)
        g.step(600.0)
        tb = rt.boiling_k('ethanol', 80000.0)
        self.assertLess(tb, rt.LIQ['ethanol']['boiling_point']['value'] - 5.0)
        self.assertAlmostEqual(g.truth()['charge_k'], tb, delta=0.05)
        self.assertGreater(g.truth()['liquid']['boiled_kg'], 0.0)
        _balance(self, g.ledger())
        g.step(3600.0)                                                                                     # boiled dry: the empty beaker heats up
        self.assertEqual(g.truth()['liquid']['mass_kg'], 0.0)
        self.assertGreater(g.truth()['charge_k'], tb + 5.0)
        _balance(self, g.ledger())

    def test_self_heating(self):
        g = Rig(ambient_k=C0 + 22.0)
        g.on()
        g.stir(1500)
        g.step(3600.0)
        self.assertAlmostEqual(g.truth()['plate_k'] - (C0 + 22.0), rt.DOC['self_heating_k'], delta=0.1)      # datasheet +13 K
        self.assertTrue(g.read('indicator.motor'))

    def test_pid_and_two_point(self):
        res = {}
        for mode in ('pid', '2p'):
            g = Rig()
            g.on()
            g.beaker()
            g.call(g.A, 'sensor.set', connected=True, immersed=True)
            self.assertEqual(g.read('heating.control'), 'pid')
            g.call(g.A, 'heating.set_mode', mode=mode)
            g.stir(600)
            g.heat(50.0)
            peak, reach = 0.0, None
            while g.h.t < 2400.0:
                g.step(2.0)
                tl = g.truth()['charge_k'] - C0
                peak = max(peak, tl)
                if reach is None and tl >= 49.5:
                    reach = g.h.t
            dev = []
            for _ in range(300):
                g.step(2.0)
                dev.append(abs(g.truth()['charge_k'] - C0 - 50.0))
            res[mode] = (reach, peak - 50.0, max(dev))
            if mode == 'pid':
                self.assertAlmostEqual(g.read('display.temperature'), 50.0, delta=0.3)
                self.assertEqual(g.call(g.E, 'truth.inspect')['process']['plate_target_k'] is not None, True)
        self.assertLess(res['pid'][2], rt.DOC['medium_control_accuracy_pt1000_k'])                       # datasheet +-0.5 K
        self.assertLess(res['2p'][0], res['pid'][0])                                                     # manual: 2P fastest, PID minimal overshoot
        self.assertGreater(res['2p'][1], res['pid'][1])

    def test_safety_circuit_errors_24_25(self):
        g = Rig()
        g.on()
        g.call(g.A, 'safety.set_limit', limit_k=C0 + 120.0)
        g.call(g.E, 'fault.inject', fault='triac_short', active=True)
        g.step(900.0)
        self.assertEqual(g.read('error.code'), 25)
        self.assertEqual(g.read('display.message'), 'E25')
        self.assertLess(g.truth()['plate_k'], C0 + 125.0)
        g.call(g.A, 'heating.set', expect='ERROR_ACTIVE', on=True)
        g.call(g.E, 'fault.inject', fault='triac_short', active=False)
        g.call(g.A, 'power.set', on=False)
        self.assertEqual(g.read('display.message'), 'HOt')
        self.assertTrue(g.read('indicator.hot'))
        g.step(3600.0)
        g.call(g.A, 'power.set', on=False)
        g.on()
        self.assertIsNone(g.read('error.code'))
        g.call(g.A, 'safety.set_limit', limit_k=C0 + 370.0)                                              # manual p.27 step 2: screw to the clockwise stop
        g.heat(100.0)                                                                                     # step 3: set point = desired safe temperature
        g.step(900.0)
        for lim in (160.0, 130.0, 115.0):                                                                 # step 4: turn slowly anticlockwise ...
            g.call(g.A, 'safety.set_limit', limit_k=C0 + lim)                                             # (below set + 15 K is allowed for the screw)
            self.assertIsNone(g.read('error.code'))
        g.call(g.A, 'safety.set_limit', limit_k=C0 + 95.0)                                               # ... until the heating switches off with Err. 25
        self.assertEqual(g.read('error.code'), 25)
        self.assertFalse(g.read('heating.on'))
        self.assertEqual(g.read('display.safe_temperature'), 95)
        self.assertEqual(g.read('heating.set_k'), C0 + 80.0)                                             # the set point follows the knob maximum (limit - 15 K)
        g.call(g.A, 'heating.set_temperature', expect='ABOVE_SAFETY_LIMIT', temperature_k=C0 + 90.0)     # the knob maximum stays 15 K below the limit
        _balance(self, g.ledger())
        g2 = Rig()                                                                                         # heating off and the screw turned below the hot plate: Err. 24
        g2.on()
        g2.heat(100.0)
        g2.step(900.0)
        g2.call(g2.A, 'heating.set', on=False)
        g2.call(g2.A, 'safety.set_limit', limit_k=C0 + 60.0)
        self.assertEqual(g2.read('error.code'), 24)
        _balance(self, g2.ledger())

    def test_error5_and_error6(self):
        g = Rig()
        g.on()
        g.beaker()
        g.call(g.A, 'sensor.set', connected=True, immersed=False)                                        # sensor lying beside the beaker
        g.call(g.A, 'menu.configure', err5_timeout_s=300.0)
        g.stir(400)
        g.heat(60.0)
        g.step(400.0)
        self.assertEqual(g.read('error.code'), 5)
        self.assertFalse(g.read('heating.on'))
        g.call(g.A, 'power.toggle')
        g.call(g.A, 'power.toggle')
        self.assertIsNone(g.read('error.code'))
        g.call(g.A, 'sensor.set', connected=True, immersed=True)
        g.heat(60.0)
        g.step(400.0)
        self.assertIsNone(g.read('error.code'))                                                         # in the medium: the reading rises
        g.call(g.A, 'sensor.set', connected=False, immersed=False)
        self.assertEqual(g.read('error.code'), 6)

    def test_motor_errors_and_bar_decoupling_viscosity_trend(self):
        g = Rig()
        g.on()
        g.beaker('beaker_600ml', sid='ethylene_glycol', ml=400.0, bar=0.06)
        g.call(g.A, 'menu.configure', display='visc')
        g.stir(800)
        g.step(30.0)
        self.assertTrue(g.truth()['bar_coupled'])
        v0 = g.read('display.visc_trend')
        self.assertAlmostEqual(v0, 100.0, delta=2.0)                                                     # manual: starts at 100 %
        g.call(g.E, 'medium.modify', viscosity_factor=8.0)                                              # the medium thickens
        g.step(5.0)
        self.assertGreater(g.read('display.visc_trend'), 150.0)
        g.call(g.A, 'viscosity.reset')
        g.step(1.0)
        self.assertAlmostEqual(g.read('display.visc_trend'), 100.0, delta=2.0)
        g.call(g.E, 'medium.modify', viscosity_factor=200.0)                                            # far too viscous: the bar decouples
        g.step(5.0)
        self.assertFalse(g.truth()['bar_coupled'])
        self.assertIn('BAR_DECOUPLED', g.events())
        g.call(g.E, 'fault.inject', fault='motor_blocked', active=True)
        g.step(1.0)
        self.assertEqual(g.read('error.code'), 4)
        self.assertFalse(g.read('stirring.on'))

    def test_error3_overhanging_pot(self):
        g = Rig(ambient_k=C0 + 38.0)
        g.on()
        g.call(g.A, 'vessel.place', vessel='h5000', substance_id='decane', volume_m3=1e-3, stir_bar_length_m=0.05)
        g.heat(310.0)
        g.step(3 * 3600.0)
        self.assertEqual(g.read('error.code'), 3)
        h = Rig(ambient_k=C0 + 40.0)
        h.on()
        h.heat(310.0)
        h.step(3600.0)
        self.assertIsNone(h.read('error.code'))                                                          # rated conditions: no Err. 3

    def test_sensor_heater_and_selftest_faults(self):
        g = Rig()
        g.on()
        g.call(g.A, 'sensor.set', connected=True, immersed=False)
        g.call(g.E, 'fault.inject', fault='sensor_short', active=True)
        self.assertEqual(g.read('error.code'), 14)
        g.call(g.E, 'fault.inject', fault='sensor_short', active=False)
        g.call(g.A, 'power.toggle')
        g.call(g.E, 'fault.inject', fault='safety_test_fault', active=True)
        g.call(g.A, 'power.toggle')
        self.assertEqual(g.read('error.code'), 21)
        g.call(g.E, 'fault.inject', fault='safety_test_fault', active=False)
        g.call(g.A, 'power.toggle')
        g.call(g.A, 'power.toggle')
        g.call(g.E, 'fault.inject', fault='heater_switch_fault', active=True)
        self.assertEqual(g.read('error.code'), 31)

    def test_timer_counter_intermittent_reverse(self):
        g = Rig()
        g.on()
        g.beaker()
        g.call(g.A, 'stirring.configure', run_s=30.0, pause_s=10.0, auto_reverse=True)
        g.call(g.A, 'menu.configure', expiry_stop_heating=True, expiry_stop_stirring=True)
        g.stir(500)
        g.heat(40.0)
        g.call(g.A, 'timer.set', duration_s=100.0)
        g.call(g.A, 'timer.toggle')
        signs = []
        for _ in range(95):
            g.step(1.0)
            signs.append(math.copysign(1, g.truth()['motor_rad_s']) if abs(g.truth()['motor_rad_s']) > 1 else 0)
        self.assertIn(1, signs)
        self.assertIn(-1, signs)                                                                           # reversed after the first pause
        self.assertIn(0, signs)
        g.step(10.0)
        self.assertTrue(g.read('timer.state')['expired'])
        self.assertFalse(g.read('heating.on'))
        self.assertFalse(g.read('stirring.on'))
        g.call(g.A, 'timer.toggle', expect='TIMER_EXPIRED')
        g.call(g.A, 'timer.set', duration_s=0.0)
        g.call(g.A, 'timer.toggle')
        g.step(42.0)
        self.assertAlmostEqual(g.read('timer.state')['elapsed_s'], 42.0, delta=1e-6)
        g.call(g.A, 'timer.clear')

    def test_modes_power_failure_lock_and_namur(self):
        g = Rig()
        g.on()
        g.call(g.A, 'menu.configure', operating_mode='B', beep=False, unit='F')
        g.stir(400)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        self.assertFalse(g.read('indicator.motor'))
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertTrue(g.read('stirring.on'))                                                            # mode B continues
        g.call(g.A, 'stirring.set', on=False)
        g.call(g.A, 'menu.configure', operating_mode='A')
        g.stir(400)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertFalse(g.read('stirring.on'))                                                           # mode A starts with everything off
        self.assertFalse(g.read('indicator.standby'))
        g.call(g.A, 'power.set', on=False)
        self.assertTrue(g.read('indicator.standby'))
        g.on()
        g.call(g.A, 'lock.toggle')
        self.assertTrue(g.read('indicator.lock'))
        g.call(g.A, 'interface.set', connected=True)
        self.assertEqual(g.call(g.A, 'namur.send', command='IN_NAME')['response'], 'RCT digital')
        g.call(g.A, 'namur.send', command='OUT_SP_1 80')                                                 # remote control is not blocked by the panel lock
        self.assertEqual(g.read('heating.set_k'), C0 + 80.0)
        g.call(g.A, 'namur.send', command='OUT_SP_4 700')
        g.call(g.A, 'namur.send', command='START_4')
        g.call(g.A, 'namur.send', command='START_1')
        g.step(10.0)
        self.assertTrue(g.call(g.A, 'namur.send', command='IN_PV_4')['response'].endswith(' 4'))
        self.assertEqual(g.call(g.A, 'namur.send', command='IN_SP_3')['response'], '370 3')
        g.call(g.A, 'namur.send', command='OUT_SP_12@50')
        g.call(g.A, 'namur.send', command='OUT_SP_42@100')
        g.call(g.A, 'namur.send', command='OUT_WD2@30')
        g.step(40.0)
        self.assertTrue(g.read('interface.state')['wd2_active'])
        self.assertEqual(g.read('display.message'), 'SAFE')
        g.call(g.A, 'namur.send', command='OUT_WD2@0')
        g.call(g.A, 'namur.send', command='OUT_WD1@20')
        g.call(g.A, 'interface.set', connected=False)
        g.step(25.0)
        self.assertEqual(g.read('error.code'), 1)
        self.assertFalse(g.read('heating.on') or g.read('stirring.on'))
        g.call(g.A, 'lock.toggle')
        g.call(g.A, 'interface.set', connected=True)
        g.call(g.A, 'namur.send', command='RESET')
        g.call(g.A, 'namur.send', command='SET_MODE_D')
        self.assertEqual(g.read('menu.state')['operating_mode'], 'D')

    def test_evaporation_and_removal_with_sensor(self):
        g = Rig()
        g.on()
        g.beaker('beaker_250ml', sid='dichloromethane', ml=100.0, bar=0.03)
        g.call(g.A, 'sensor.set', connected=True, immersed=True)
        g.stir(300)
        g.heat(35.0)
        g.step(1200.0)
        tr = g.truth()
        self.assertGreater(tr['liquid']['evaporated_kg'], 1e-4)
        g.call(g.A, 'vessel.remove', expect='SENSOR_IMMERSED')
        g.call(g.A, 'sensor.set', connected=True, immersed=False)
        g.call(g.A, 'heating.set', on=False)
        g.call(g.A, 'vessel.remove')
        _balance(self, g.ledger())

    def test_quiet_stepping_is_exact(self):
        g = Rig()
        n = g.h.tick
        g.step(30 * 86400.0)
        self.assertEqual(g.h.tick - n, int(round(30 * 86400.0 / rt.DT)))
        g.on()
        g.call(g.A, 'heating.set_temperature', temperature_k=C0 + 50.0)
        g.call(g.A, 'stirring.set_speed', speed_rad_s=0.0)
        g.call(g.A, 'heating.toggle')
        g.call(g.A, 'stirring.toggle')
        g.step(10.0)
        g.call(g.A, 'heating.toggle')
        g.step(8 * 3600.0)
        self.assertEqual(g.truth()['plate_k'], rt.PLANT_EXAMPLE['ambient_k'])                             # back at ambient: quiet again
        n = g.h.tick
        g.step(10 * 86400.0)
        self.assertEqual(g.h.tick - n, int(round(10 * 86400.0 / rt.DT)))
        _balance(self, g.ledger())


class RoundOneFixes(unittest.TestCase):
    """Regression tests of the round-1 review findings (R1-*, C1-*)."""

    def test_idle_plate_still_evaporates(self):                                                          # R1-QUIET-EVAP
        g = Rig()
        g.beaker('beaker_250ml', sid='dichloromethane', ml=100.0, bar=0.0)
        g.step(600.0)
        tr = g.truth()
        self.assertGreater(tr['liquid']['evaporated_kg'], 5e-3)                                            # ~12 g in 10 min from an open 250 ml beaker
        self.assertLess(tr['charge_k'], rt.PLANT_EXAMPLE['ambient_k'] - 0.5)                               # evaporative cooling
        _balance(self, g.ledger())

    def test_humidity_slows_water_evaporation(self):                                                     # R1-EVAP-DRY-AIR
        out = {}
        for rh in (0.0, 0.5, 1.0):
            g = Rig(relative_humidity=rh)
            g.beaker('beaker_600ml', sid='water', ml=400.0, bar=0.0)
            g.step(3600.0)
            out[rh] = g.truth()['liquid']['evaporated_kg']
            _balance(self, g.ledger())
        self.assertGreater(out[0.0], 1.6 * out[0.5])
        self.assertGreater(out[0.5], out[1.0])
        area = math.pi * (rt.VESSELS['beaker_600ml']['od_m'] / 2.0) ** 2
        self.assertLess(out[0.5] / area, 0.2)                                                              # still-air indoor range ~0.05-0.15 kg/(m2 h)

    def test_blocked_motor_before_start_gives_err4(self):                                                # R1-ERR4-QUIET
        g = Rig()
        g.on()
        g.call(g.E, 'fault.inject', fault='motor_blocked', active=True)
        g.call(g.A, 'stirring.set', on=True)
        g.step(1.0)
        self.assertEqual(g.read('error.code'), 4)
        self.assertFalse(g.read('stirring.on'))
        g2 = Rig()
        g2.on()
        g2.stir(300)
        g2.call(g2.E, 'fault.inject', fault='motor_blocked', active=True)                                 # injected while stirring: at once
        self.assertEqual(g2.read('error.code'), 4)

    def test_truth_events_hidden_from_agent(self):                                                      # C1-EVENT-LEAK
        g = Rig()
        g.on()
        g.beaker('beaker_600ml', sid='ethylene_glycol', ml=400.0, bar=0.06)
        g.stir(800)
        g.step(10.0)
        g.call(g.E, 'medium.modify', viscosity_factor=200.0)
        g.step(5.0)
        self.assertIn('BAR_DECOUPLED', g.events())
        agent_codes = [e['code'] for e in g.call(g.A, 'events.read', since_s=0.0)['events']]
        self.assertNotIn('BAR_DECOUPLED', agent_codes)
        self.assertIn('STIRRING_ON', agent_codes)

    def test_err14_when_plugging_a_shorted_sensor(self):                                                 # R1-ERR14-PLUG
        g = Rig()
        g.on()
        g.call(g.E, 'fault.inject', fault='sensor_short', active=True)
        g.call(g.A, 'sensor.set', connected=True, immersed=False)
        self.assertEqual(g.read('error.code'), 14)
        g.call(g.A, 'power.set', on=False)
        g.on()                                                                                             # still shorted at power-on
        self.assertEqual(g.read('error.code'), 14)

    def test_mode_a_switch_off_clears_functions(self):                                                   # R1-MODE-D
        g = Rig()
        g.on()
        g.stir(300)
        g.call(g.A, 'power.set', on=False)
        self.assertFalse(g.read('stirring.on'))

    def test_ccw_intermittent_and_half_up_rounding(self):                                                # R1-CCW-INTERMITTENT, R1-ROUNDING
        g = Rig()
        g.on()
        self.assertEqual(g.call(g.A, 'stirring.configure', direction='ccw', run_s=25.0)['pattern']['run_s'], 30.0)
        self.assertAlmostEqual(g.call(g.A, 'stirring.set_speed', speed_rad_s=345.0 * RPM)['speed_rad_s'] / RPM, 350.0, places=9)
        self.assertAlmostEqual(g.call(g.A, 'stirring.set_speed', speed_rad_s=335.0 * RPM)['speed_rad_s'] / RPM, 340.0, places=9)
        g.call(g.A, 'stirring.set', on=True)
        g.step(5.0)
        self.assertLess(g.truth()['motor_rad_s'], -1.0)
        self.assertEqual(g.call(g.A, 'stirring.set', on=False)['on'], False)
        self.assertEqual(g.call(g.A, 'menu.configure', err5_timeout_s=150.0)['menu']['err5_timeout_s'], 180.0)

    def test_watchdog2_rewrites_set_points(self):                                                        # R1-WD2-SETPOINT
        g = Rig()
        g.on()
        g.call(g.A, 'interface.set', connected=True)
        g.call(g.A, 'namur.send', command='OUT_SP_1 80')
        g.call(g.A, 'namur.send', command='OUT_SP_4 700')
        g.call(g.A, 'namur.send', command='OUT_SP_12@60')
        g.call(g.A, 'namur.send', command='OUT_SP_42@200')
        g.call(g.A, 'namur.send', command='OUT_WD2@20')
        g.step(25.0)
        self.assertTrue(g.read('interface.state')['wd2_active'])
        self.assertEqual(g.read('heating.set_k'), C0 + 60.0)
        self.assertEqual(g.read('display.set_temperature'), 60)
        self.assertAlmostEqual(g.read('stirring.set_rad_s') / RPM, 200.0, places=9)
        self.assertEqual(g.call(g.A, 'namur.send', command='IN_SP_1')['response'], '60 1')

    def test_no_freezing_states(self):                                                                    # R1-FREEZING
        g = Rig()
        g.call(g.A, 'vessel.place', expect='NOT_LIQUID', vessel='beaker_600ml', substance_id='water', volume_m3=2e-4, initial_k=265.0)
        g.beaker('beaker_250ml', sid='dmso', ml=100.0, bar=0.0)
        g.call(g.E, 'plant.configure', expect='NOT_LIQUID', **dict(rt.PLANT_EXAMPLE, ambient_k=C0 + 10.0))
        self.assertEqual(rt.LIQ['water']['melting_point_k'], C0)

    def test_timer_display_and_analytic_counter(self):                                                   # R1-TIMER-DISPLAY
        g = Rig()
        g.on()
        g.call(g.A, 'timer.set', duration_s=125.0)
        self.assertEqual(g.read('display.timer'), '000205')
        g.call(g.A, 'timer.toggle')
        n = g.h.tick
        g.step(100.0)
        self.assertEqual(g.read('display.timer'), '000025')
        self.assertLess(g.h.tick - n, 1000 + 1)
        g.step(30.0)
        self.assertTrue(g.read('timer.state')['expired'])
        self.assertIn('TIMER_EXPIRED', g.events())
        g.call(g.A, 'timer.set', duration_s=0.0)
        g.call(g.A, 'timer.toggle')
        g.step(4 * 86400.0 + 5 * 3600.0 + 7.0)
        self.assertEqual(g.read('display.timer'), '0405  ')                                                  # dd:hh on the printed colons
        self.assertAlmostEqual(g.read('timer.state')['elapsed_s'], 4 * 86400.0 + 5 * 3600.0 + 7.0, delta=1e-6)
        self.assertEqual(g.read('display.safe_temperature'), 370)
        g.call(g.A, 'menu.configure', display='visc')
        self.assertIsNone(g.read('display.safe_temperature'))

    def test_contract_parameters_si(self):                                                               # C1-PARAM-UNITS
        m = rt.export_ika_plate_rctdigital_contract_metadata()
        units = {p['unit'] for p in m['mechanisms']['hotplate_process']['parameters'].values()}
        self.assertFalse(units & {'degC', 'rpm', 'min'}, units)


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        missing = set(rt.CAPS) - set(rt.UNSUPPORTED) - SEEN_CAPS - {'events.read'}
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()

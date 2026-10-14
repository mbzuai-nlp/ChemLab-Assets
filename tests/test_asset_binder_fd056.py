"""tests for CHEM-011 binder_fd056 (BINDER FD 56 drying and heating chamber with forced convection): python3 -m unittest tests/test_asset_binder_fd056.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; heating-up 15 min, 290 W at 150 C and 4 min recovery as calibrated; heating and fan stop with the door open;
fluctuation and spatial variation; air flap air change; drying of wet samples (constant and falling rate, boiling, chamber vapour, room
humidity) to constant mass; class 2 safety controller (latched, reset with OK) and class 1 overtemperature device (330 C, reset after >= 10 s
without mains below 230 C); Delayed Off timer with the fan selection; ramp; USB recording (interval, standby, outage, stick removal); racks and
loads; Pt100 rupture (1999); fan failure; standby skipping exact; energy, liquid-mass and vapour ledgers close."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import binder_fd056 as rt  # noqa: E402

INST = 'binder_fd056_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
C0 = 273.15
DOOR_90 = math.pi / 2.0


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.BinderFd056Runtime(instance)
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
        assert expect in [e['code'] for e in rt.export_binder_fd056_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
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

    def setp(self, c):
        self.call(self.A, 'temperature.set', temperature_k=C0 + c)

    def door(self, open_):
        self.call(self.A, 'door.set', angle_rad=DOOR_90 if open_ else 0.0)

    def sample(self, sid='s1', level=1, position='front_left', ware='weighing_bottle_50x30', dry=0.005, liquid='water', liq_kg=0.002, **kw):
        was_open = self.h._door_open()
        if not was_open:
            self.door(True)
        r = self.call(self.A, 'sample.place', sample_id=sid, level=level, position=position, ware=ware, dry_mass_kg=dry, liquid_id=liquid, liquid_mass_kg=liq_kg, **kw)
        if not was_open:
            self.door(False)
        return r

    def remove(self, sid):
        self.door(True)
        r = self.call(self.A, 'sample.remove', sample_id=sid)['sample']
        self.door(False)
        return r

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']

    def events(self, who=None):
        return [e['code'] for e in self.call(who or self.E, 'events.read', since_s=0.0)['events']]


def _balance(test, L):
    scale = max(abs(L['e_heater_j']) + abs(L['e_fan_j']) + abs(L['e_samples_in_j']), 1.0)
    test.assertLess(abs(L['energy_balance_j']), 1e-9 * scale + 1e-6, L)
    test.assertLess(abs(L['mass_balance_kg']), 1e-12 + 1e-12 * L['m_placed_kg'], L)
    test.assertLess(abs(L['vapour_balance_kg']), 1e-12 + 1e-12 * L['m_placed_kg'], L)


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
        m = rt.export_binder_fd056_contract_metadata()
        for k, c in m['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], m['state_slots'], (k, e))
            for p in c['preconditions']:
                for s in p['state_ids']:
                    self.assertIn(s, m['state_slots'], (k, s))
        for s in rt.SCENE_MIRRORED:
            self.assertIn(s, rt.SLOTS)
            self.assertEqual(rt.SLOTS[s]['visibility'], 'agent')
        units = {p['unit'] for p in m['mechanisms']['drying_oven_process']['parameters'].values()}
        self.assertFalse(units & {'degC', 'min', 'deg', 'K/min'}, units)
        CHECKS.append(dict(id='metadata_consistent'))

    def test_declared_errors_cover_raised_codes(self):
        m = rt.export_binder_fd056_contract_metadata()
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
        for cap, args in (('truth.inspect', {}), ('ledger.read', {}), ('fault.inject', dict(fault='fan_failure', active=True)), ('sim.step', dict(dt_s=1.0)),
                          ('plant.configure', dict(rt.PLANT_EXAMPLE))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = g.A.describe(instance_id=INST)
        self.assertNotIn('truth.inspect', d['capabilities'])
        self.assertNotIn('truth.process', d['state_slots'])
        g.on()
        g.setp(280.0)
        g.sample('p', ware='ptfe_dish_100', dry=0.01, liquid=None, liq_kg=0.0)
        g.step(7200.0)
        self.assertIn('WARE_OVERHEATED', g.events())                                                     # PTFE above its service limit: evaluator truth only
        self.assertNotIn('WARE_OVERHEATED', g.events(g.A))

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
        a.setp(80.0)
        a.step(60.0)
        self.assertFalse(b.read('power.on'))
        self.assertEqual(b.h.Ta, rt.PLANT_EXAMPLE['ambient_k'])

    def test_step_partition_independence(self):
        def run(parts):
            g = Rig(seed=7)
            g.call(g.A, 'usb.set_stick', inserted=True)
            g.on()
            g.call(g.A, 'usb.record', on=True, interval_min=1)
            g.setp(105.0)
            g.sample('w', liq_kg=0.003)
            g.call(g.A, 'timer.set', days=0, hours=0, minutes=4, fan_after='off')
            for dt in parts:
                g.step(dt)
            r = g.h.read('evaluator')
            return json.dumps({k: r[k] for k in r if k != 'clock.time_s'}, sort_keys=True, default=float), g.ledger(), g.call(g.A, 'usb.read')['rows']
        a, la, ua = run([300.0])
        b, lb, ub = run([0.05] * 6000)
        c, lc, uc = run([7.33] * 40 + [6.8])
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertEqual(json.dumps(la, sort_keys=True), json.dumps(lb, sort_keys=True))
        self.assertEqual(ua, ub)
        self.assertEqual(ua, uc)

    def test_rejections_change_nothing(self):
        g = Rig()
        g.call(g.A, 'temperature.set', expect='STANDBY', temperature_k=C0 + 100.0)
        g.call(g.A, 'usb.read', expect='NO_STICK')
        g.call(g.A, 'sample.place', expect='DOOR_CLOSED', sample_id='a', level=1, position='front_left', ware='petri_dish_100', dry_mass_kg=0.01)
        g.call(g.A, 'rack.set', expect='DOOR_CLOSED', level=2, present=True)
        g.on()
        g.call(g.A, 'usb.record', expect='NO_STICK', on=True)
        g.door(True)
        g.call(g.A, 'sample.place', expect='NO_RACK', sample_id='a', level=2, position='front_left', ware='petri_dish_100', dry_mass_kg=0.01)
        g.call(g.A, 'sample.place', expect='OVERFILL', sample_id='a', level=1, position='front_left', ware='crucible_porcelain_30ml', dry_mass_kg=0.0,
               liquid_id='water', liquid_mass_kg=0.05)
        g.call(g.A, 'sample.place', expect='NOT_LIQUID', sample_id='a', level=1, position='front_left', ware='beaker_250ml', dry_mass_kg=0.0, liquid_id='diethyl_ether',
               liquid_mass_kg=0.05, initial_k=C0 + 40.0)
        g.call(g.A, 'sample.place', expect='OVERLOAD', sample_id='a', level=1, position='front_left', ware='steel_tray_300x200', dry_mass_kg=29.5,
               dry_density_kg_m3=22000.0)
        g.call(g.A, 'sample.place', expect='OVERFILL', sample_id='a', level=1, position='front_left', ware='weighing_bottle_50x30', dry_mass_kg=29.0)
        g.call(g.A, 'sample.place', expect='INVALID_ARGUMENT', sample_id='a', level=1, position='front_left', ware='beaker_250ml', dry_mass_kg=0.0, liquid_mass_kg=0.05)
        g.call(g.A, 'sample.place', sample_id='a', level=1, position='front_left', ware='beaker_250ml', dry_mass_kg=0.05)
        g.call(g.A, 'sample.place', expect='DUPLICATE_ID', sample_id='a', level=3, position='front_left', ware='beaker_250ml', dry_mass_kg=0.05)
        g.call(g.A, 'sample.place', expect='POSITION_OCCUPIED', sample_id='b', level=1, position='front_left', ware='beaker_250ml', dry_mass_kg=0.05)
        g.call(g.A, 'rack.set', expect='RACK_LOADED', level=1, present=False)
        g.call(g.A, 'sample.remove', expect='NO_SAMPLE', sample_id='zz')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=99))
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.call(g.A, 'power.set', expect='NO_MAINS', on=False)
        g.call(g.A, 'flap.set', expect='NO_MAINS', position=2)


class Physics(unittest.TestCase):
    def test_heating_up_energy_and_fluctuation(self):
        g = Rig(ambient_k=C0 + 22.0)
        g.on()
        g.setp(150.0)
        g.step(890.0)
        self.assertLess(g.h.Tsens, C0 + 149.0)
        g.step(20.0)
        self.assertGreaterEqual(g.h.Tsens, C0 + 149.0)                                                    # 15 min to 150 C (datasheet; calibrated)
        g.step(8 * 3600.0)
        L0, t0 = g.ledger(), g.h.t
        dev = 0.0
        for _ in range(360):
            g.step(10.0)
            dev = max(dev, abs(g.h.Tsens - (C0 + 150.0)))
        L1 = g.ledger()
        self.assertAlmostEqual((L1['e_electric_total_j'] - L0['e_electric_total_j']) / (g.h.t - t0), 290.0, delta=2.0)   # 290 Wh/h at 150 C
        self.assertLess(dev, 0.3)                                                                          # +-0.3 K fluctuation
        self.assertEqual(g.read('display.temperature'), 150.0)
        self.assertEqual(g.read('display.text'), 'SP   150')
        _balance(self, L1)

    def test_door_stops_heating_and_recovery(self):
        g = Rig(ambient_k=C0 + 22.0)
        g.on()
        g.setp(150.0)
        g.step(3 * 3600.0)
        g.door(True)
        g.step(5.0)
        self.assertFalse(g.read('fan.running'))
        self.assertEqual(g.truth()['heater_w'], 0.0)
        g.step(25.0)
        self.assertLess(g.truth()['air_k'], C0 + 110.0)                                                    # fresh room air in, liner still hot
        g.door(False)
        g.step(230.0)
        self.assertLess(g.h.Tsens, C0 + 149.0)
        g.step(20.0)
        self.assertGreaterEqual(g.h.Tsens, C0 + 149.0)                                                    # 4 min recovery (datasheet; calibrated)
        _balance(self, g.ledger())

    def test_spatial_variation_and_flap(self):
        g = Rig(ambient_k=C0 + 22.0)
        g.on()
        g.setp(150.0)
        g.step(3 * 3600.0)
        offs = [g.h._offset(lv, p) for lv in rt.LEVELS for p in rt.POSITIONS]
        self.assertAlmostEqual(max(abs(x) for x in offs), 1.7, delta=0.03)
        e0, t0 = g.ledger()['e_electric_total_j'], g.h.t
        g.step(1800.0)
        p_closed = (g.ledger()['e_electric_total_j'] - e0) / (g.h.t - t0)
        g.call(g.A, 'flap.set', position=4)
        self.assertEqual(g.read('flap.angle_rad'), math.radians(60.0))
        g.step(3 * 3600.0)
        e0, t0 = g.ledger()['e_electric_total_j'], g.h.t
        g.step(1800.0)
        p_open = (g.ledger()['e_electric_total_j'] - e0) / (g.h.t - t0)
        self.assertGreater(p_open, p_closed + 150.0)                                                       # 80 /h of fresh air to heat
        self.assertGreater(max(abs(g.h._offset(lv, p)) for lv in rt.LEVELS for p in rt.POSITIONS), 2.0)  # flap open: worse uniformity
        _balance(self, g.ledger())

    def test_drying_to_constant_mass(self):
        g = Rig()
        g.on()
        g.setp(105.0)
        g.sample('w', dry=0.005, liq_kg=0.002)
        masses = []
        for _ in range(12):
            g.step(600.0)
            masses.append(g.truth()['samples']['w']['total_mass_kg'])
        self.assertTrue(all(b <= a for a, b in zip(masses, masses[1:])))
        self.assertLess(g.truth()['samples']['w']['liquid_kg'], 1e-6)                                     # dry within 2 h at 105 C
        g.step(3600.0)
        r = g.remove('w')
        self.assertAlmostEqual(r['total_mass_kg'], rt.WARES['weighing_bottle_50x30']['mass_kg'] + 0.005, delta=1e-6)   # constant mass
        self.assertAlmostEqual(r['liquid_lost_kg'], 0.002, delta=1e-9)
        L = g.ledger()
        self.assertEqual(g.call(g.E, 'truth.inspect')['process']['samples'], {})
        self.assertGreater(L['m_vented_kg'] + L['m_condensed_kg'], 0.0019)
        _balance(self, L)

    def test_falling_rate_and_humidity(self):
        def loss(rh, dry):
            g = Rig(relative_humidity=rh)
            g.on()
            g.setp(45.0)
            g.sample('w', ware='petri_dish_100', dry=dry, liq_kg=0.010)
            g.step(1800.0)
            _balance(self, g.ledger())
            return 0.010 - g.truth()['samples']['w']['liquid_kg']
        self.assertGreater(loss(0.0, 0.0), loss(0.9, 0.0) * 1.05)                                          # humid room air slows drying at 45 C
        self.assertGreater(loss(0.5, 0.0), loss(0.5, 0.12) * 1.5)                                          # 10 g on 120 g solid: below the critical moisture

    def test_boiling_limits_sample_temperature(self):
        g = Rig()
        g.on()
        g.setp(150.0)
        g.sample('e', ware='beaker_250ml', dry=0.0, liquid='ethanol', liq_kg=0.050)
        tb = rt.boiling_k('ethanol', rt.PLANT_EXAMPLE['pressure_pa'])
        tmax = 0.0
        for _ in range(60):
            g.step(30.0)
            s = g.truth()['samples']['e']
            if s['liquid_kg'] > 0:
                tmax = max(tmax, s['temperature_k'])
        self.assertLessEqual(tmax, tb + 1e-6)
        self.assertGreater(g.truth()['samples']['e']['boiled_kg'] + g.truth()['samples']['e']['evaporated_kg'], 0.01)
        _balance(self, g.ledger())

    def test_safety_controller_class2(self):
        g = Rig()
        g.on()
        g.setp(100.0)
        g.call(g.A, 'safety.set_limit', limit_k=C0 + 105.0)
        g.step(3600.0)
        g.call(g.E, 'fault.inject', fault='ssr_stuck_on', active=True)
        for _ in range(600):
            g.step(1.0)
            if g.read('alarm.state'):
                break
        self.assertEqual(g.read('alarm.state'), 'limit_active')
        self.assertEqual(g.read('display.text'), 'ALAr tLI')
        self.assertTrue(g.read('indicator.alarm'))
        self.assertLess(g.truth()['air_k'], C0 + 125.0)
        g.call(g.A, 'alarm.acknowledge')
        self.assertEqual(g.read('alarm.state'), 'limit_active')                                           # still above: no reset
        g.call(g.E, 'fault.inject', fault='ssr_stuck_on', active=False)
        g.call(g.A, 'temperature.set', temperature_k=C0 + 95.0)
        g.step(1800.0)
        self.assertEqual(g.read('alarm.state'), 'limit_latched')
        self.assertEqual(g.truth()['heater_w'], 0.0)
        g.call(g.A, 'alarm.acknowledge')
        self.assertIsNone(g.read('alarm.state'))
        g.step(1800.0)
        self.assertAlmostEqual(g.h.Tsens, C0 + 95.0, delta=0.3)
        _balance(self, g.ledger())

    def test_overtemperature_class1(self):
        g = Rig()
        g.on()
        g.setp(250.0)
        g.call(g.E, 'fault.inject', fault='safety_controller_failure', active=True)
        g.call(g.E, 'fault.inject', fault='ssr_stuck_on', active=True)
        g.step(3 * 3600.0)
        self.assertEqual(g.read('alarm.state'), 'overtemperature')
        self.assertLess(g.truth()['air_k'], C0 + 345.0)
        self.assertEqual(g.read('display.text'), 'ALAr OtP')
        g.call(g.E, 'fault.inject', fault='ssr_stuck_on', active=False)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.step(5.0)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertEqual(g.read('alarm.state'), 'overtemperature')                                         # too short and still hot
        g.step(4 * 3600.0)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.step(12.0)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertNotEqual(g.read('alarm.state'), 'overtemperature')                                      # >= 10 s off and below 230 C
        _balance(self, g.ledger())

    def test_timer_delayed_off_and_fan_selection(self):
        for fan_after in ('on', 'off'):
            g = Rig()
            g.on()
            g.setp(80.0)
            g.call(g.A, 'timer.set', days=0, hours=0, minutes=20, fan_after=fan_after)
            self.assertTrue(g.read('indicator.timer'))
            g.call(g.A, 'display.cycle')
            g.call(g.A, 'display.cycle')
            g.call(g.A, 'display.cycle')
            self.assertEqual(g.read('display.text'), 't 00020')
            g.step(1199.0)
            self.assertFalse(g.read('timer.state')['expired'])
            g.step(2.0)
            self.assertTrue(g.read('timer.state')['expired'])
            self.assertEqual(g.read('display.text'), 't   End')
            self.assertEqual(g.read('fan.running'), fan_after == 'on')
            g.step(60.0)
            self.assertEqual(g.truth()['heater_w'], 0.0)
            g.call(g.A, 'alarm.acknowledge')                                                                # OK turns the expired timer off
            self.assertFalse(g.read('timer.state')['set'])
            self.assertTrue(g.read('fan.running'))
            _balance(self, g.ledger())

    def test_ramp(self):
        g = Rig()
        g.on()
        g.setp(50.0)
        g.step(3600.0)
        g.call(g.A, 'ramp.set', gradient_k_min=2, target_k=C0 + 90.0)
        g.step(600.0)
        self.assertAlmostEqual(g.read('ramp.state')['effective_k'], C0 + 70.0, delta=0.05)
        self.assertAlmostEqual(g.h.Tsens, C0 + 70.0, delta=1.5)
        g.step(1200.0)
        self.assertFalse(g.read('ramp.state')['active'])
        self.assertEqual(g.read('temperature.set_k'), C0 + 90.0)
        g.call(g.A, 'ramp.set', gradient_k_min=3, target_k=C0 + 60.0)                                     # target below the actual value: set directly
        self.assertFalse(g.read('ramp.state')['active'])
        self.assertEqual(g.read('temperature.set_k'), C0 + 60.0)

    def test_usb_recording_standby_outage(self):
        g = Rig()
        g.call(g.A, 'usb.set_stick', inserted=True)
        g.on()
        g.setp(60.0)
        g.call(g.A, 'usb.record', on=True, interval_min=2)
        g.step(600.0)
        rows = g.call(g.A, 'usb.read')['rows']
        self.assertEqual(len(rows), 5)
        self.assertEqual([r['time_s'] for r in rows], [0.1, 120.1, 240.1, 360.1, 480.1])
        g.call(g.A, 'power.set', on=False)                                                                  # recording continues in standby
        g.step(1200.0)
        self.assertEqual(len(g.call(g.A, 'usb.read')['rows']), 15)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)                                    # interrupted by an outage
        g.step(600.0)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        n = len(g.call(g.A, 'usb.read')['rows'])
        self.assertEqual(n, 15)
        g.step(240.0)
        self.assertGreater(len(g.call(g.A, 'usb.read')['rows']), n)
        g.call(g.A, 'usb.set_stick', inserted=False)
        self.assertIn('USB_REMOVED_WHILE_RECORDING', g.events())
        self.assertFalse(g.read('usb.state')['recording'])

    def test_sensor_rupture_fan_failure_unit(self):
        g = Rig()
        g.on()
        g.setp(100.0)
        g.call(g.A, 'unit.set', unit='F')
        g.step(3600.0)
        self.assertEqual(g.read('display.temperature'), 212.0)
        self.assertEqual(g.read('display.text'), 'SP   212')
        g.call(g.E, 'fault.inject', fault='sensor_rupture', active=True)
        g.step(10.0)
        self.assertEqual(g.read('display.temperature'), 1999.0)
        self.assertEqual(g.truth()['heater_w'], 0.0)
        g2, g3 = Rig(), Rig()
        for x in (g2, g3):
            x.on()
            x.setp(150.0)
            x.sample('d', ware='beaker_250ml', dry=0.1, liquid=None, liq_kg=0.0)
        g3.call(g3.E, 'fault.inject', fault='fan_failure', active=True)
        g2.step(1800.0)
        g3.step(1800.0)
        t2, t3 = g2.truth(), g3.truth()
        self.assertLess(t3['samples']['d']['temperature_k'], t2['samples']['d']['temperature_k'] - 10.0)   # no forced convection: the load heats slower
        self.assertGreater(t3['heater_k'], t2['heater_k'] + 20.0)                                          # and the element runs hotter
        self.assertFalse(g3.read('fan.running'))
        _balance(self, g3.ledger())

    def test_standby_skipping_is_exact(self):
        g = Rig()
        n = g.h.tick
        g.step(30 * 86400.0)
        self.assertEqual(g.h.tick - n, int(round(30 * 86400.0 / rt.DT)))
        g.on()
        g.setp(60.0)
        g.step(600.0)
        g.call(g.A, 'power.set', on=False)
        g.step(24 * 3600.0)
        self.assertEqual(g.truth()['air_k'], rt.PLANT_EXAMPLE['ambient_k'])
        self.assertTrue(g.read('indicator.standby'))
        self.assertIsNone(g.read('display.temperature'))
        n = g.h.tick
        g.step(10 * 86400.0)
        self.assertEqual(g.h.tick - n, int(round(10 * 86400.0 / rt.DT)))
        _balance(self, g.ledger())

    def test_racks_and_keys(self):
        g = Rig()
        g.on()
        g.door(True)
        g.call(g.A, 'rack.set', level=2, present=True)
        self.assertEqual(g.read('racks.levels'), [1, 2, 3])
        g.call(g.A, 'rack.set', level=2, present=False)
        g.door(False)
        g.call(g.A, 'temperature.step', delta_k=1.0)
        g.call(g.A, 'temperature.step', delta_k=-1.0)
        g.call(g.A, 'temperature.step', delta_k=1.0)
        self.assertEqual(g.read('temperature.set_k'), C0 + 26.0)
        g.call(g.A, 'power.toggle')
        self.assertFalse(g.read('power.on'))


class RoundOneFixes(unittest.TestCase):
    """Regression tests of the round-1 review findings (R1-*)."""

    def test_usb_rows_independent_of_standby_stepping(self):                                          # R1-01
        def run(parts):
            g = Rig(seed=3)
            g.call(g.A, 'usb.set_stick', inserted=True)
            g.on()
            g.call(g.A, 'usb.record', on=True, interval_min=1)
            g.call(g.A, 'timer.set', days=0, hours=0, minutes=10, fan_after='on')
            g.call(g.A, 'power.set', on=False)
            for dt in parts:
                g.step(dt)
            return g.call(g.A, 'usb.read')['rows']
        a = run([1800.0])
        self.assertEqual(a, run([0.1] * 18000))
        self.assertEqual(a, run([33.0] * 54 + [18.0]))
        self.assertEqual([r['timer_s'] for r in a[1:4]], [540.0 - 0.1, 480.0 - 0.1, 420.0 - 0.1])

    def test_vapour_bounded_by_ambient_pressure(self):                                               # R1-02
        g = Rig()
        g.on()
        g.setp(200.0)
        g.sample('wet', ware='steel_tray_300x200', dry=0.2, liquid='water', liq_kg=1.0)
        g.call(g.A, 'flap.set', position=0)
        pmax = 0.0
        for _ in range(40):
            g.step(60.0)
            v = g.truth()['vapour_kg']
            pmax = max(pmax, sum(m * rt.R_GAS * g.h.Ta / (rt.LIQ[k]['molar_mass_kg_mol'] * rt.V_AIR) for k, m in v.items()))
        self.assertLessEqual(pmax, rt.PLANT_EXAMPLE['pressure_pa'] * (1 + 1e-9))
        _balance(self, g.ledger())

    def test_class1_clears_automatically_after_cooling(self):                                        # R1-03
        g = Rig()
        g.on()
        g.setp(250.0)
        g.call(g.E, 'fault.inject', fault='safety_controller_failure', active=True)
        g.call(g.E, 'fault.inject', fault='ssr_stuck_on', active=True)
        for _ in range(240):
            g.step(60.0)
            if g.read('alarm.state') == 'overtemperature':
                break
        self.assertEqual(g.read('alarm.state'), 'overtemperature')
        g.call(g.E, 'fault.inject', fault='ssr_stuck_on', active=False)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.step(12.0)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        self.assertGreater(g.truth()['air_k'], rt.CLASS1[1])
        self.assertEqual(g.read('alarm.state'), 'overtemperature')                                         # still above 230 C
        g.step(6 * 3600.0)
        self.assertNotEqual(g.read('alarm.state'), 'overtemperature')                                      # cleared once below 230 C
        self.assertIn('OVERTEMP_RESET', g.events())
        _balance(self, g.ledger())

    def test_door_cuts_heater_immediately(self):                                                     # R1-04
        g = Rig()
        g.on()
        g.setp(150.0)
        g.step(120.0)
        self.assertGreater(g.truth()['duty'], 0.5)
        g.door(True)
        self.assertEqual(g.truth()['heater_w'], 0.0)
        self.assertFalse(g.read('indicator.heating'))
        self.assertFalse(g.read('fan.running'))


class RoundTwoFixes(unittest.TestCase):
    """Regression tests of the round-2 review findings (R2-*)."""

    def test_no_usb_rows_inside_an_outage(self):                                                    # R2-01
        for active in (False, True):
            g = Rig(seed=4)
            g.call(g.A, 'usb.set_stick', inserted=True)
            g.on()
            g.setp(50.0)
            g.call(g.A, 'usb.record', on=True, interval_min=1)
            g.call(g.A, 'timer.set', days=0, hours=0, minutes=30, fan_after='on')
            if not active:
                g.call(g.A, 'power.set', on=False)
            g.step(300.0)
            g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
            t0 = g.h.t
            g.step(400.0)
            t1 = g.h.t
            g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
            g.step(300.0)
            rows = g.call(g.A, 'usb.read')['rows']
            self.assertFalse([r['time_s'] for r in rows if t0 < r['time_s'] <= t1 + 1e-9], active)
            ts = [r['timer_s'] for r in rows if r['timer_s'] is not None]
            self.assertTrue(all(b <= a for a, b in zip(ts, ts[1:])) and max(ts) <= 1800.0, ts)

    def test_vapour_never_above_saturation(self):                                                   # R2-02
        g = Rig()
        g.on()
        g.setp(120.0)
        g.sample('wet', ware='steel_tray_300x200', dry=0.2, liquid='water', liq_kg=1.0)
        g.step(1800.0)
        g.call(g.A, 'power.set', on=False)                                                                  # warm wet tray in a cooling chamber
        worst = 0.0
        for _ in range(60):
            g.step(60.0)
            m = g.truth()['vapour_kg'].get('water', 0.0)
            p = m * rt.R_GAS * g.h.Ta / (rt.LIQ['water']['molar_mass_kg_mol'] * rt.V_AIR) + g.h._room_water_pa()
            worst = max(worst, p / rt.p_vap('water', g.h.Ta))
        self.assertLessEqual(worst, 1.0 + 1e-9)
        L = g.ledger()
        self.assertGreater(L['m_condensed_kg'], 0.0)
        _balance(self, L)


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        missing = set(rt.CAPS) - set(rt.UNSUPPORTED) - SEEN_CAPS - {'events.read'}
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()

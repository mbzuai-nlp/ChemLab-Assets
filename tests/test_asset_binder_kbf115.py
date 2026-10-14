"""tests for CHEM-198 binder_kbf115 (BINDER KBF 115 constant climate chamber): python3 -m unittest tests/test_asset_binder_kbf115.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; 40 C / 75 %RH steady state (fluctuation, 470 Wh/h, about 100 mL water a day, calibrated); recovery after 30 s door
opening (6 / 7 min, calibrated) and the door-open behaviour (fan to minimum, all off after 60 s, door alarm after 5 min); 200 W heat compensation
at 40 C; climate chart regions (A, B corner reached, outside not reached, dry mode); time programs (ramp / step, tolerance band pause, section
repetition, power-failure resume); safety controller class 3.1, tolerance range alarms, reset and buzzer, the humidity-system alarm that cannot be
acknowledged; freshwater tap / can and wastewater; sample exposure with measured templates (NaCl and KCl deliquescence humidities, solution
branch, adsorbent isotherm, condensation on cold samples, degradation law, mean kinetic temperature); glass-door fog when the door heating
fails; icing below +5 C and the manual defrost; idle mode, humidity off and the 10 min module warm-up, sensor failures; energy and water ledgers
closing exactly."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import binder_kbf115 as rt  # noqa: E402

INST = 'binder_kbf115_01'
T0 = 273.15
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.BinderKbf115Runtime(instance)
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
        assert expect in [e['code'] for e in rt.export_binder_kbf115_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def run(self, T_c, rh, settle=6 * 3600.0, fan=1.0):
        self.call(self.A, 'power.set', on=True)
        self.call(self.A, 'setpoint.set', temperature_k=T0 + T_c, relative_humidity=rh, fan_fraction=fan)
        self.step(settle)

    def open(self):
        self.call(self.A, 'door.move', angle_rad=1.6)
        self.call(self.A, 'glass_door.move', angle_rad=1.5)

    def close(self):
        self.call(self.A, 'glass_door.move', angle_rad=0.0)
        self.call(self.A, 'door.move', angle_rad=0.0)

    def until(self, pred, limit, every=10.0):
        t = 0.0
        while not pred() and t < limit:
            self.step(every)
            t += every
        return t

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']

    def climate(self):
        return self.read('truth.climate')


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


def _balance(test, L):
    E = (L['e_initial_j'] + L['e_heater_in_j'] + L['e_fan_in_j'] + L['e_door_heating_in_j'] + L['e_wall_in_j'] + L['e_glass_in_j'] + L['e_exchange_in_j']
         + L['e_steam_in_j'] + L['e_sample_heat_in_j'] - L['e_refrigeration_out_j'] - L['e_drain_out_j'] + L['e_samples_added_j'] - L['e_samples_removed_j'] + L['e_reconfigure_in_j'])
    scale = max(abs(L['e_initial_j']), abs(L['e_refrigeration_out_j']), abs(L['e_heater_in_j']), 1.0)
    test.assertAlmostEqual(L['u_chamber_j'], E, delta=1e-9 * scale + 1e-3)
    W = (L['w_initial_kg'] + L['w_supply_in_kg'] + L['w_can_refill_kg'] + L['w_exchange_in_kg'] - L['w_pumped_out_kg'] - L['w_overflow_kg']
         + L['w_samples_added_kg'] - L['w_samples_removed_kg'])
    test.assertAlmostEqual(L['w_system_kg'], W, delta=1e-12 + 1e-9 * (L['w_supply_in_kg'] + L['w_system_kg']))


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_consistent(self):
        meta = rt.export_binder_kbf115_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['climate_chamber_process']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_binder_kbf115_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            g.call(g.A, k, expect='UNSUPPORTED')

    def test_agent_truth_isolation(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        g.step(600.0)
        self.assertFalse(any(k.startswith('truth.') for k in g.read(who=g.A)))
        for cap, a in (('truth.inspect', {}), ('ledger.read', {}), ('fault.inject', dict(fault='mains_outage', active=True)), ('sim.step', dict(dt_s=1.0))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=a, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='heater_failure', active=True)
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in g.call(g.A, 'events.read', since_s=0.0)['events']))
        g.call(g.E, 'truth.inspect')

    def test_idempotent_request_ids(self):
        g = Rig()
        a = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=True), request_id='same')
        b = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=True), request_id='same')
        self.assertEqual(a, b)
        c = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=False), request_id='same')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='i1'), Rig(instance='i2')
        a.call(a.A, 'power.set', on=True)
        self.assertFalse(b.read('power.on'))

    def test_step_partition_independence(self):
        runs = []
        for parts in ([3000.0], [0.3, 7.7, 400.0, 2592.0], [1.0] * 40 + [2960.0]):
            g = Rig(seed=7)
            g.call(g.A, 'power.set', on=True)
            g.call(g.A, 'setpoint.set', temperature_k=T0 + 40.0, relative_humidity=0.75)
            for p in parts:
                g.step(p)
            v = g.read()
            v.pop('clock.time_s')
            runs.append(json.dumps(v, sort_keys=True, default=str))
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0], runs[2])

    def test_rejections_change_nothing(self):
        r = rt.BinderKbf115Runtime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g = Rig()
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=2))
        g.call(g.E, 'plant.configure', expect='SUPPLY_FIXED', **dict(rt.PLANT_EXAMPLE, water_supply='can'))
        g.call(g.A, 'setpoint.set', expect='POWER_OFF', temperature_k=T0 + 30.0)
        g.call(g.A, 'alarm.reset', expect='POWER_OFF')
        g.call(g.A, 'glass_door.move', expect='OUTER_DOOR_CLOSED', angle_rad=1.0)
        g.call(g.A, 'sample.place', expect='OUTER_DOOR_CLOSED', **rt.SAMPLE_EXAMPLE)
        g.call(g.A, 'sample.remove', expect='UNKNOWN_SAMPLE', sample_id='nothing')
        g.call(g.A, 'can.refill', expect='NO_CAN')
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'setpoint.set', expect='INVALID_ARGUMENT', relative_humidity=0.9)
        g.call(g.A, 'setpoint.set', expect='INVALID_ARGUMENT', temperature_k=T0 + 75.0)
        g.call(g.A, 'safety.configure', expect='OUT_OF_LIMITS', mode='limit', value_k=50.0)
        g.call(g.A, 'tolerance.configure', expect='INVALID_ARGUMENT', delay_s=60.0)
        g.call(g.A, 'program.start', expect='UNKNOWN_PROGRAM', program=5)
        g.call(g.A, 'program.write', expect='INVALID_REPEAT', **dict(rt.PROGRAM_EXAMPLE, repeat=dict(from_section=2, to_section=2, count=1)))
        g.call(g.A, 'program.write', **rt.PROGRAM_EXAMPLE)
        g.call(g.A, 'program.start', program=2)
        g.call(g.A, 'program.write', expect='PROGRAM_RUNNING', **rt.PROGRAM_EXAMPLE)
        g.open()
        g.call(g.A, 'door.move', expect='GLASS_DOOR_OPEN', angle_rad=0.0)
        g.call(g.A, 'sample.place', expect='NO_SHELF', **dict(rt.SAMPLE_EXAMPLE, shelf=3))
        g.call(g.A, 'sample.place', expect='SHELF_OVERLOAD', **dict(rt.SAMPLE_EXAMPLE, dry_mass_kg=29.0, container_mass_kg=2.0))
        g.call(g.A, 'sample.place', expect='UNQUANTIFIED_SOLUTION', **dict(rt.SAMPLE_EXAMPLE, substance_id='lithium_chloride', initial_water_kg=0.01))
        g.call(g.A, 'sample.place', **rt.SAMPLE_EXAMPLE)
        g.call(g.A, 'sample.place', expect='DUPLICATE_SAMPLE', **rt.SAMPLE_EXAMPLE)
        for i, sh in enumerate((1, 1, 2, 2)):
            g.call(g.A, 'sample.place', **dict(rt.SAMPLE_EXAMPLE, sample_id=f'w{i}', substance_id='inert', dry_mass_kg=24.0, container_mass_kg=0.0, shelf=sh) if i % 2 == 0
                   else dict(rt.SAMPLE_EXAMPLE, sample_id=f'w{i}', substance_id='inert', dry_mass_kg=1.0, shelf=sh))
        g.call(g.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, shelves=4))
        g.call(g.A, 'sample.place', **dict(rt.SAMPLE_EXAMPLE, sample_id='big', substance_id='inert', dry_mass_kg=29.0, container_mass_kg=0.0, shelf=3))
        g.call(g.A, 'sample.place', expect='LOAD_LIMIT', **dict(rt.SAMPLE_EXAMPLE, sample_id='big2', substance_id='inert', dry_mass_kg=29.0, container_mass_kg=0.0, shelf=4))
        g.call(g.E, 'plant.configure', expect='SHELF_OCCUPIED', **dict(rt.PLANT_EXAMPLE, shelves=2))
        g.call(g.A, 'sample.remove', sample_id='big')
        g.call(g.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, shelves=2))
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)


class Physics(unittest.TestCase):
    def test_steady_40_75_energy_water_and_fluctuation(self):
        g = Rig()
        g.run(40.0, 0.75)
        self.assertEqual(g.read('state.current'), 'fixed')
        self.assertEqual(g.read('chart.region'), 'A')
        L0 = g.ledger()
        Ts, RHs = [], []
        for _ in range(240):
            g.step(15.0)
            c = g.climate()
            Ts.append(c['air_k'])
            RHs.append(g.read('humidity.chamber'))
        L1 = g.ledger()
        self.assertLessEqual(max(abs(t - (T0 + 40.0)) for t in Ts), 0.1 + 0.05)                       # +-0.1 K fluctuation
        self.assertLessEqual(max(abs(r - 0.75) for r in RHs), rt.DOC['humidity_fluctuation'])          # <= +-2 %RH
        self.assertAlmostEqual((L1['electric_energy_j'] - L0['electric_energy_j']) / 3600.0, rt.DOC['energy_40_75_w'], delta=15.0)
        g.step(86400.0 - 3600.0)
        L2 = g.ledger()
        self.assertAlmostEqual(L2['w_supply_in_kg'] - L0['w_supply_in_kg'], rt.DOC['water_consumption_kg_day']['at_40_75_closed'], delta=0.015)
        self.assertAlmostEqual(g.read('display.temperature'), 40.0, delta=0.15)
        self.assertLessEqual(g.read('truth.power')['mains_current_a'], rt.DOC['unit_fuse_a'])
        _balance(self, L2)

    def test_door_recovery_and_door_behaviour(self):
        for T_c, rh, target in ((25.0, 0.60, rt.DOC['recovery_25_60_s']), (40.0, 0.75, rt.DOC['recovery_40_75_s'])):
            g = Rig()
            g.run(T_c, rh)
            g.open()
            g.step(30.0)
            self.assertIn('DOOR_OPEN', g.read('notifications.active'))
            self.assertTrue(g.read('indicator.door_open'))
            self.assertLess(g.read('humidity.chamber'), rh - 0.03)
            g.close()
            last = 0.0
            for k in range(1, 1801):
                g.step(1.0)
                if abs(g.read('temperature.chamber_k') - (T0 + T_c)) > 0.5 or abs(g.read('humidity.chamber') - rh) > 0.03:
                    last = float(k)
            self.assertAlmostEqual(last, target, delta=0.25 * target, msg=(T_c, rh))  # one calibrated parameter for both targets
            _balance(self, g.ledger())
        g = Rig()
        g.run(40.0, 0.75)
        g.call(g.A, 'door.move', angle_rad=1.6)
        g.step(30.0)
        self.assertEqual(g.read('state.current'), 'fixed')
        self.assertAlmostEqual(g.read('truth.power')['fan'], rt.ENG['fan_power_w'] * rt.FAN_LO ** 3, delta=1e-9)   # fan at minimum speed
        g.step(31.0)
        self.assertEqual(g.read('state.current'), 'door_off')                                          # all off after 60 s
        p = g.read('truth.power')
        self.assertEqual((p['heater'], p['fan'], p['compressor']), (0.0, 0.0, 0.0))
        self.assertNotIn('DOOR_OPEN', g.read('alarms.active'))
        g.step(rt.DOC['door_open_alarm_s'] - 61.0 + 1.0)
        self.assertIn('DOOR_OPEN', g.read('alarms.active'))
        self.assertTrue(g.read('alarm.audible'))
        g.call(g.A, 'door.move', angle_rad=0.0)
        g.step(1.0)
        self.assertIn('DOOR_OPEN', g.read('alarms.active'))                                            # not acknowledged: stays after the door closed
        self.assertEqual(g.call(g.A, 'alarm.reset')['cleared'], ['DOOR_OPEN'])
        self.assertFalse(g.read('alarm.audible'))
        _balance(self, g.ledger())

    def test_heat_compensation_200w(self):
        res = {}
        for load in (200.0, 300.0):
            g = Rig()
            g.call(g.A, 'power.set', on=True)
            g.call(g.A, 'function.set', humidity_control=False)
            g.call(g.A, 'setpoint.set', temperature_k=T0 + 40.0)
            g.open()
            g.call(g.A, 'sample.place', sample_id='dut', substance_id='inert', shelf=1, dry_mass_kg=2.0, initial_k=T0 + 40.0, exposed_area_m2=0.2, heat_output_w=load)
            g.close()
            g.step(5 * 3600.0)
            res[load] = g.climate()['air_k'] - (T0 + 40.0)
            self.assertEqual(g.read('chart.region'), 'dry')
            _balance(self, g.ledger())
        self.assertLess(abs(res[200.0]), 0.15)
        self.assertGreater(res[300.0], 1.0)

    def test_climate_chart_regions(self):
        g = Rig()
        g.run(10.0, 0.40, settle=12 * 3600.0)
        self.assertEqual(g.read('chart.region'), 'B')
        self.assertIn('CLIMATE_CHART_B', g.read('notifications.active'))
        self.assertAlmostEqual(g.read('humidity.chamber'), 0.40, delta=0.03)
        g.step(13 * 3600.0)
        self.assertIn('CLIMATE_CHART_B_24H', g.read('notifications.active'))
        g = Rig()
        g.run(20.0, 0.10, settle=12 * 3600.0)
        self.assertEqual(g.read('chart.region'), 'outside')
        self.assertIn('CLIMATE_CHART_OUTSIDE', g.read('notifications.active'))
        self.assertGreater(g.read('humidity.chamber'), 0.10 + 0.03)                                     # below the reachable dew point
        self.assertLessEqual(g.climate()['dehumidification_evaporator_k'], rt.ENG['evaporator_min_k'] + 0.5)
        _balance(self, g.ledger())

    def test_program_ramp_step_repeat_pause_and_power_failure(self):
        g = Rig()
        g.run(25.0, 0.60, settle=4 * 3600.0)
        secs = [dict(duration_s=1800.0, temperature_k=T0 + 25.0, relative_humidity=0.60, transition='step'),
                dict(duration_s=3600.0, temperature_k=T0 + 25.0, relative_humidity=0.60, transition='ramp'),
                dict(duration_s=3600.0, temperature_k=T0 + 25.0, relative_humidity=0.60, transition='ramp'),
                dict(duration_s=600.0, temperature_k=T0 + 35.0, relative_humidity=0.70, transition='step'),
                dict(duration_s=600.0, temperature_k=T0 + 45.0, relative_humidity=0.70, transition='step', tolerance_k=1.0),
                dict(duration_s=1.0, temperature_k=T0 + 45.0, relative_humidity=0.70, transition='step')]
        r = g.call(g.A, 'program.write', program=4, sections=secs, repeat=dict(from_section=1, to_section=2, count=1))
        self.assertEqual(r['duration_s'], sum(x['duration_s'] for x in secs) + 1800.0 + 3600.0)
        g.call(g.A, 'program.start', program=4)
        g.step(1800.0 + 3600.0 + 1800.0 + 3600.0 + 1800.0)                                          # sections 1, 2, repeated 1, 2, half of 3 (ramp)
        st = g.read('program.status')['running']
        self.assertEqual((st['section'], st['reps_done']), (3, 1))
        self.assertAlmostEqual(st['elapsed'], 1800.0, delta=1.0)
        self.assertAlmostEqual(g.read('setpoint.current')['temperature_k'], T0 + 30.0, delta=0.01)      # ramp 25 C -> 35 C: half way 30 C
        self.assertAlmostEqual(g.read('setpoint.current')['relative_humidity'], 0.65, delta=1e-4)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.step(1200.0)
        self.assertEqual(g.read('state.current'), 'outage')
        self.assertEqual(g.read('alarms.active'), [])                                                   # the panel is dead
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        g.step(1.0)
        self.assertAlmostEqual(g.read('program.status')['running']['elapsed'], 1801.0, delta=1.5)     # program time held during the outage
        self.assertIn('POWER_FAILURE', g.read('alarms.active'))
        g.step(1799.0 + 600.0 + 60.0)
        self.assertEqual(g.read('program.status')['running']['section'], 5)
        self.assertEqual(g.read('state.current'), 'program_paused')                                    # step to 45 C outside the 1 K band
        self.assertLess(g.read('program.status')['running']['elapsed'], 120.0)
        g.until(lambda: g.read('program.status')['running'] is None, 6 * 3600.0, 60.0)
        self.assertEqual(g.read('setpoint.current')['source'], 'fixed')
        self.assertTrue(any(e['code'] == 'PROGRAM_PAUSE' for e in g.call(g.A, 'events.read', since_s=0.0)['events']))
        g.call(g.A, 'program.start', program=4)
        self.assertTrue(g.call(g.A, 'program.stop')['stopped'])
        _balance(self, g.ledger())

    def test_safety_tolerance_alarms_reset_and_buzzer(self):
        g = Rig()
        g.run(40.0, 0.75)
        g.call(g.A, 'tolerance.configure', temperature_k=2.0, relative_humidity=0.05, delay_s=900.0)
        g.call(g.A, 'safety.configure', mode='offset', value_k=2.0)
        self.assertAlmostEqual(g.read('safety.settings')['limit_k'], T0 + 42.0)
        g.open()
        g.call(g.A, 'sample.place', sample_id='hot', substance_id='inert', shelf=2, dry_mass_kg=3.0, initial_k=T0 + 40.0, exposed_area_m2=0.3, heat_output_w=450.0)
        g.close()
        t = g.until(lambda: 'SAFETY_CONTROLLER' in g.read('alarms.active'), 3 * 3600.0, 10.0)
        self.assertLess(t, 3 * 3600.0)
        self.assertEqual(g.read('truth.power')['heater'], 0.0)
        self.assertTrue(g.read('indicator.alarm_header'))
        g.step(960.0)
        self.assertIn('TEMPERATURE_RANGE', g.read('alarms.active'))
        r = g.call(g.A, 'alarm.reset')
        self.assertIn('SAFETY_CONTROLLER', r['muted'])
        self.assertFalse(g.read('alarm.audible'))
        g.open()
        g.call(g.A, 'sample.remove', sample_id='hot')
        g.close()
        g.until(lambda: 'SAFETY_CONTROLLER' not in g.read('alarms.active'), 4 * 3600.0, 30.0)
        self.assertNotIn('SAFETY_CONTROLLER', g.read('alarms.active'))                               # acknowledged: clears by itself
        g.step(3 * 3600.0)
        self.assertNotIn('TEMPERATURE_RANGE', g.read('alarms.active'))
        g.call(g.A, 'buzzer.set', on=False)
        g.call(g.E, 'fault.inject', fault='humidity_module_failure', active=True)
        g.step(5.0)
        self.assertIn('HUMIDITY_SYSTEM', g.read('alarms.active'))
        self.assertFalse(g.read('alarm.audible'))                                                       # buzzer off
        g.call(g.A, 'buzzer.set', on=True)
        self.assertNotIn('HUMIDITY_SYSTEM', g.call(g.A, 'alarm.reset')['muted'])                       # must not be acknowledged
        self.assertTrue(g.read('alarm.audible'))
        g.call(g.E, 'fault.inject', fault='humidity_module_failure', active=False)
        for f, code in (('temperature_sensor_failure', 'TEMPERATURE_SENSOR'), ('humidity_sensor_failure', 'HUMIDITY_SENSOR'), ('safety_sensor_failure', 'SAFETY_SENSOR'),
                        ('door_sensor_failure', 'DOOR_SENSOR')):
            g.call(g.E, 'fault.inject', fault=f, active=True)
            g.step(2.0)
            self.assertIn(code, g.read('alarms.active'))
        self.assertIsNone(g.read('display.temperature'))
        self.assertIsNone(g.read('display.humidity'))
        _balance(self, g.ledger())

    def test_water_supply_can_and_wastewater(self):
        g = Rig()
        g.run(40.0, 0.75)
        g.call(g.A, 'water.supply', open=False)
        t = g.until(lambda: 'FRESHWATER_SUPPLY' in g.read('alarms.active'), 48 * 3600.0, 600.0)
        self.assertLess(t, 3 * 3600.0)                                                                # the next fill attempt fails (level about 10 % down)
        self.assertTrue(g.read('indicator.water'))
        g.step(36 * 3600.0)                                                                           # the 0.07 L container runs dry (about 0.1 L a day)
        self.assertLess(g.read('humidity.chamber'), 0.70)
        g.call(g.A, 'water.supply', open=True)
        g.step(3600.0)
        self.assertAlmostEqual(g.read('humidity.chamber'), 0.75, delta=0.02)
        _balance(self, g.ledger())
        c = Rig(water_supply='can')
        self.assertEqual(c.read('water.can_kg'), rt.ENG['can_capacity_kg'])
        c.run(25.0, 0.60, settle=3600.0)
        kg = c.read('water.can_kg')
        self.assertLess(kg, rt.ENG['can_capacity_kg'])
        c.call(c.A, 'can.refill')
        self.assertEqual(c.read('water.can_kg'), rt.ENG['can_capacity_kg'])
        c.h.can_kg = 0.0                                                                               # an empty can (evaluator shortcut)
        c.h.hum_kg = 0.0
        c.step(10.0)
        self.assertIn('FRESHWATER_CAN_EMPTY', c.read('alarms.active'))
        w = Rig()
        w.run(25.0, 0.80, settle=2 * 3600.0)
        w.call(w.E, 'fault.inject', fault='wastewater_blocked', active=True)
        old = w.h.waste
        w.h.waste = 0.49                                                                              # a nearly full can (evaluator shortcut)
        w.h.led['w_initial_kg'] += 0.49 - old                                                         # keep the water ledger consistent
        w.call(w.A, 'setpoint.set', relative_humidity=0.30)
        t = w.until(lambda: 'WASTEWATER' in w.read('alarms.active'), 48 * 3600.0, 300.0)
        self.assertLess(t, 48 * 3600.0)
        self.assertGreaterEqual(w.climate()['wastewater_kg'], rt.DOC['wastewater_tank_kg'] - 1e-9)
        _balance(self, w.ledger())

    def test_sample_exposure_templates(self):
        g = Rig()
        g.run(25.0, 0.60)
        g.open()
        g.call(g.A, 'sample.place', sample_id='nacl', substance_id='sodium_chloride', shelf=1, dry_mass_kg=0.010, exposed_area_m2=0.02, initial_k=T0 + 25.0, container_mass_kg=0.05)
        g.call(g.A, 'sample.place', sample_id='kcl', substance_id='potassium_chloride', shelf=1, dry_mass_kg=0.010, exposed_area_m2=0.005, initial_k=T0 + 25.0)
        g.call(g.A, 'sample.place', sample_id='mof', substance_id='uio_66', shelf=2, dry_mass_kg=0.005, exposed_area_m2=0.003, initial_k=T0 + 25.0)
        g.call(g.A, 'sample.place', sample_id='cold', substance_id='inert', shelf=2, dry_mass_kg=0.5, exposed_area_m2=0.02, initial_k=T0 + 5.0, heat_capacity_j_kg_k=900.0)
        g.call(g.A, 'sample.place', sample_id='drug', substance_id='inert', shelf=2, dry_mass_kg=0.02, initial_k=T0 + 25.0,
               degradation=dict(rate_ref_per_s=1e-7, activation_j_mol=100000.0, humidity_exponent=2.0))
        g.close()
        self.assertEqual(len(g.call(g.A, 'samples.read')['samples']), 5)
        g.step(1800.0)
        tr = g.read('truth.samples')
        self.assertGreater(tr['cold']['exposure']['condensation_events'], 0)                           # cold item below the dew point
        g.step(2 * 86400.0)
        tr = g.read('truth.samples')
        self.assertEqual(tr['nacl']['water_kg'], 0.0)                                                   # 60 %RH < NaCl 75.3 %
        self.assertEqual(tr['nacl']['state'], 'dry')
        q_eq = rt._interp(rt.SORPTION['uio_66']['adsorption']['relative_humidity'], rt.SORPTION['uio_66']['adsorption']['uptake_kg_kg'], 0.60)
        self.assertAlmostEqual(tr['mof']['water_kg'] / 0.005, q_eq, delta=0.1 * q_eq)
        self.assertEqual(tr['cold']['film_kg'], 0.0)                                                    # the film evaporated after warming
        g.call(g.A, 'setpoint.set', relative_humidity=0.80)
        g.step(4 * 86400.0)
        tr = g.read('truth.samples')
        self.assertGreater(tr['nacl']['water_kg'], 0.0)                                                 # 80 %RH > 75.3 %: deliquesced
        self.assertIn(tr['nacl']['state'], ('solution', 'saturated_solution', 'solution_extrapolated'))
        self.assertEqual(tr['kcl']['water_kg'], 0.0)                                                    # KCl 84.3 %: still dry
        g.step(10 * 86400.0)
        tr = g.read('truth.samples')
        m = 0.010 / rt.SORPTION['sodium_chloride']['solution_branch']['solute_molar_mass_kg_mol'] / tr['nacl']['water_kg']
        aw, q = rt.branch_aw('sodium_chloride', m)
        self.assertAlmostEqual(aw, 0.80, delta=0.02)                                                    # solution in equilibrium with 80 %RH
        g.open()
        rep = g.call(g.A, 'sample.remove', sample_id='nacl')['sample']
        self.assertEqual(rep['appearance'], 'liquid')
        self.assertAlmostEqual(rep['total_mass_kg'], 0.06 + tr['nacl']['water_kg'], delta=1e-3)
        self.assertNotIn('exposure', rep)                                                               # hidden process values only in the truth
        g.call(g.A, 'sample.remove', sample_id='drug')
        g.close()
        rm = {r['sample_id']: r for r in g.call(g.E, 'truth.inspect')['removed_samples']}
        self.assertAlmostEqual(rm['nacl']['exposure']['mean_kinetic_temperature_k'], T0 + 25.0, delta=0.6)  # shelf offset and the latent heat of uptake
        self.assertGreater(rm['nacl']['exposure']['time_above_deliquescence_s'], 13 * 86400.0)
        self.assertGreater(rm['drug']['degraded_fraction'], 0.0)
        self.assertLess(rm['drug']['degraded_fraction'], 1.0)
        _balance(self, g.ledger())

    def test_glass_fog_when_door_heating_fails(self):
        g = Rig(ambient_k=T0 + 15.0)
        g.run(40.0, 0.80, settle=4 * 3600.0)
        self.assertFalse(g.read('door.glass_fogged'))
        g.call(g.E, 'fault.inject', fault='door_heating_failure', active=True)
        g.step(4 * 3600.0)
        self.assertTrue(g.read('door.glass_fogged'))
        self.assertLess(g.climate()['glass_k'], g.climate()['dew_point_k'])
        g.call(g.E, 'fault.inject', fault='door_heating_failure', active=False)
        g.step(8 * 3600.0)
        self.assertFalse(g.read('door.glass_fogged'))
        _balance(self, g.ledger())

    def test_icing_below_5c_and_manual_defrost(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'function.set', humidity_control=False)
        g.call(g.A, 'setpoint.set', temperature_k=T0 + 2.0)
        g.step(86400.0)
        self.assertAlmostEqual(g.read('temperature.chamber_k'), T0 + 2.0, delta=0.3)
        g.step(2 * 86400.0)
        ice = g.climate()['evaporator_ice_kg']
        self.assertGreater(ice, 0.0)
        g.call(g.A, 'function.set', humidity_control=True)                                               # manual defrost: 0 %RH, 40 C, 30 min
        g.call(g.A, 'setpoint.set', temperature_k=T0 + 40.0, relative_humidity=0.0)
        g.step(1800.0 + 3600.0)
        self.assertLess(g.climate()['evaporator_ice_kg'], 0.05 * ice)
        _balance(self, g.ledger())

    def test_idle_humidity_off_warmup_and_logger(self):
        g = Rig()
        g.run(30.0, 0.70, settle=3 * 3600.0)
        g.call(g.A, 'logger.configure', interval_s=120.0)
        g.call(g.A, 'function.set', humidity_control=False)
        self.assertIsNone(g.read('setpoint.current')['relative_humidity'])
        self.assertIn('HUMIDITY_OFF', g.read('notifications.active'))
        g.step(1200.0)
        self.assertEqual(g.climate()['humidifier_ready'], 0.0)
        g.call(g.A, 'function.set', humidity_control=True)
        g.step(300.0)
        self.assertLess(g.climate()['humidifier_ready'], 1.0)                                          # up to 10 min until fully available
        g.step(400.0)
        self.assertEqual(g.climate()['humidifier_ready'], 1.0)
        g.call(g.A, 'function.set', idle_mode=True)
        g.step(600.0)
        self.assertEqual(g.read('state.current'), 'idle')
        self.assertEqual(g.read('truth.power')['heater'], 0.0)
        g.call(g.A, 'function.set', idle_mode=False)
        recs = g.call(g.A, 'log.read', last=20)['records']
        self.assertEqual(len(recs), 20)
        self.assertAlmostEqual(recs[-1]['time_s'] - recs[-2]['time_s'], 120.0, delta=1e-6)
        self.assertEqual(g.read('logger.settings')['interval_s'], 120.0)
        g.call(g.A, 'power.set', on=False)
        self.assertEqual(g.read('state.current'), 'off')
        g.call(g.A, 'power.toggle')
        self.assertTrue(g.read('power.on'))
        _balance(self, g.ledger())


    def test_review_r1_fixes(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'setpoint.set', temperature_k=T0 + 25.0, relative_humidity=0.60)
        g.step(900.0)
        self.assertEqual(g.read('truth.climate')['steam_kg_s'], 0.0)                                # E7: about 20 min before humidification
        g.step(900.0)
        self.assertGreater(g.read('truth.climate')['steam_kg_s'] + g.read('truth.climate')['humidifier_ready'], 0.0)
        g.call(g.A, 'safety.configure', mode='limit', value_k=T0 + 45.0)                                # absolute limit accepted
        g.call(g.A, 'safety.configure', mode='offset', value_k=40.0, expect='OUT_OF_LIMITS')
        g.call(g.A, 'safety.configure', mode='offset', value_k=2.0)
        g.call(g.A, 'setpoint.set', temperature_k=T0 + 40.0, relative_humidity=0.75, fan_fraction=0.4)  # lowest documented fan speed
        g.step(10 * 3600.0)
        self.assertAlmostEqual(g.read('truth.climate')['relative_humidity'], 0.75, delta=0.035)
        g.call(g.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, ambient_k=T0 + 35.0, pressure_pa=80000.0))  # re-referenced energy stays balanced
        g.step(600.0)
        _balance(self, g.call(g.E, 'ledger.read')['ledger'])
        g.open()
        g.call(g.A, 'sample.place', sample_id='h1', substance_id='inert', shelf=1, dry_mass_kg=1.0, initial_k=T0 + 40.0, heat_output_w=400.0)
        g.call(g.A, 'sample.place', sample_id='h2', substance_id='inert', shelf=1, dry_mass_kg=1.0, initial_k=T0 + 40.0, heat_output_w=200.0, expect='HEAT_LOAD_LIMIT')
        g.call(g.A, 'sample.remove', sample_id='h1')
        g.call(g.A, 'sample.place', sample_id='suc', substance_id='d_sucrose', shelf=1, dry_mass_kg=0.010, initial_k=T0 + 25.0,
               initial_water_kg=0.010 / rt.SORPTION['d_sucrose']['solution_branch']['solute_molar_mass_kg_mol'])   # 1 mol/kg
        g.close()
        g.call(g.A, 'setpoint.set', temperature_k=T0 + 25.0, relative_humidity=0.30, fan_fraction=1.0)
        g.step(2 * 86400.0)
        b = rt.SORPTION['d_sucrose']['solution_branch']
        w_min = 0.010 / b['solute_molar_mass_kg_mol'] / b['molality_mol_kg'][-1]
        self.assertGreater(g.read('truth.samples')['suc']['water_kg'], 0.9 * w_min)                     # stops at the most concentrated measured point
        g.call(g.A, 'setpoint.set', temperature_k=T0 + 3.0, relative_humidity=0.60)
        g.step(60.0)
        self.assertIn('HUMIDITY_OFF', g.read('notifications.active'))                                          # E7: humidity control off below 5 C


    def test_review_r2_fixes(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'function.set', humidity_control=False)
        g.call(g.A, 'setpoint.set', temperature_k=T0 + 60.0, fan_fraction=1.0)                        # high heater load: integral released after the hold
        g.step(6 * 3600.0)
        self.assertAlmostEqual(g.read('truth.climate')['air_k'], T0 + 60.0, delta=0.1)
        self.assertEqual(g.call(g.A, 'setpoint.set', temperature_k=T0 + 3.0)['chart_region'], 'dry')        # same rule as chart.region
        g.call(g.A, 'setpoint.set', temperature_k=T0 + 40.0, relative_humidity=0.5)
        g.call(g.A, 'function.set', humidity_control=True)                                              # after dry running: no second 20 min wait
        g.step(700.0)
        self.assertGreater(g.read('truth.climate')['humidifier_ready'], 0.9)
        d = Rig()
        d.run(50.0, 0.10)                                                                               # dehumidification without temperature swings
        xs = []
        for _ in range(180):
            d.step(10.0)
            xs.append(d.read('truth.climate')['air_k'])
        self.assertLess(max(xs) - min(xs), 0.2)
        w = Rig()
        w.call(w.A, 'power.set', on=True)
        w.call(w.A, 'setpoint.set', temperature_k=T0 + 30.0, relative_humidity=0.65, fan_fraction=0.4)  # no overshoot trip at minimum fan
        w.step(3600.0)
        self.assertNotIn('SAFETY_CONTROLLER', w.read('alarms.active'))
        c = Rig()
        c.call(c.A, 'power.set', on=True)
        c.call(c.A, 'function.set', humidity_control=False)
        c.call(c.A, 'setpoint.set', temperature_k=T0 + 59.0)
        c.step(6 * 3600.0)
        c.call(c.A, 'setpoint.set', temperature_k=T0 + 61.0)                                            # compressor stops: bumpless, no safety trip
        peak = 0.0
        for _ in range(120):
            c.step(10.0)
            peak = max(peak, c.read('truth.climate')['air_k'] - T0 - 61.0)
        self.assertLess(peak, 0.8)
        self.assertNotIn('SAFETY_CONTROLLER', c.read('alarms.active'))
        s = Rig()
        s.run(40.0, 0.50, settle=3 * 3600.0)                                                            # start-up from the room: no false humidity alarm
        self.assertNotIn('HUMIDITY_RANGE', s.read('alarms.active'))


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()

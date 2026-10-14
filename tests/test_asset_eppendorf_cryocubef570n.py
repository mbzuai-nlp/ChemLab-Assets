"""tests for CHEM-197 eppendorf_cryocubef570n (Eppendorf CryoCube F570n ULT freezer): python3 -m unittest tests/test_asset_eppendorf_cryocubef570n.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; pull-down 3 h 53 min and 8.3 kWh/day (calibrated) with the probe in the middle compartment and the integer display;
door handle, outer / inner doors, vacuum after closing and the auto vent; door-opening recovery and frost; alarm limits, on-site / remote delays,
mute and test, BMS relay; mains outage on the battery back-up circuit; lock code and programming mode; loading warm items (latent heat,
compartment limits, inventory from the host); gasket / vent icing, de-icing and seal cleaning; defrost and drying; filter dust, condenser fan
failure (E-04), sensor errors; energy, water and air ledgers closing exactly."""
import copy
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import eppendorf_cryocubef570n as rt  # noqa: E402

INST = 'eppendorf_cryocubef570n_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
SET = 193.15


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.EppendorfCryocubef570nRuntime(instance)
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
        assert expect in [e['code'] for e in rt.export_eppendorf_cryocubef570n_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def cold(self, settle=6 * 3600.0):
        """Switched on (battery circuit on) and pulled down to -80 C."""
        self.call(self.A, 'battery.set', on=True)
        self.call(self.A, 'power.set', on=True)
        self.step(rt.DOC['pull_down_s'] + settle)

    def open_all(self, angle=1.6):
        self.call(self.A, 'handle.set', locked=False)
        self.call(self.A, 'door.move', angle_rad=angle)
        for c in range(1, 6):
            self.call(self.A, 'inner_door.move', compartment=c, angle_rad=angle)

    def close_all(self):
        for c in range(1, 6):
            self.call(self.A, 'inner_door.move', compartment=c, angle_rad=0.0)
        self.call(self.A, 'door.move', angle_rad=0.0)
        self.call(self.A, 'handle.set', locked=True)

    def until(self, pred, limit, every=10.0):
        t = 0.0
        while not pred() and t < limit:
            self.step(every)
            t += every
        return t

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']


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
    U = L['e_initial_j'] + L['e_conduction_in_j'] + L['e_exchange_in_j'] - L['e_refrigeration_out_j'] + L['e_items_added_j'] - L['e_items_removed_j'] - L['e_meltwater_wiped_j']
    test.assertAlmostEqual(L['u_cabinet_j'], U, delta=1e-9 * max(abs(L['e_initial_j']), abs(L['e_refrigeration_out_j']), 1.0) + 1e-3)
    test.assertAlmostEqual(L['w_deposited_kg'], L['frost_kg'] + L['melt_water_kg'] + L['w_wiped_kg'] + L['w_deiced_kg'], delta=1e-12 + 1e-9 * L['w_deposited_kg'])
    test.assertAlmostEqual(L['n_interior_mol'], L['n_initial_mol'] + L['n_door_mol'] + L['n_vent_mol'], delta=1e-9 * L['n_initial_mol'])


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_consistent(self):
        meta = rt.export_eppendorf_cryocubef570n_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['ult_freezer_thermal']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_eppendorf_cryocubef570n_contract_metadata()
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
        for cap, a in (('truth.inspect', {}), ('ledger.read', {}), ('fault.inject', dict(fault='mains_outage', active=True))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=a, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='compressor_failure', active=True)
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in g.call(g.A, 'events.read', since_s=0.0)['events']))

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
            for p in parts:
                g.step(p)
            v = g.read()
            v.pop('clock.time_s')
            runs.append(json.dumps(v, sort_keys=True, default=str))
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0], runs[2])

    def test_rejections_change_nothing(self):
        r = rt.EppendorfCryocubef570nRuntime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g = Rig()
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=2))
        g.call(g.A, 'settings.configure', expect='POWER_OFF', set_point_k=193.15)
        g.call(g.A, 'alarm.mute', expect='POWER_OFF')
        g.call(g.A, 'door.move', expect='HANDLE_LOCKED', angle_rad=1.0)
        g.call(g.A, 'inner_door.move', expect='OUTER_DOOR_CLOSED', compartment=1, angle_rad=1.0)
        g.call(g.A, 'lock.set', locked=True)
        g.call(g.A, 'handle.set', expect='CYLINDER_LOCKED', locked=False)
        g.call(g.A, 'lock.set', locked=False)
        g.call(g.A, 'vent.deice', expect='OUTER_DOOR_CLOSED')
        g.call(g.A, 'load.place', expect='OUTER_DOOR_CLOSED', **rt.ITEM_EXAMPLE)
        g.call(g.A, 'load.remove', expect='UNKNOWN_ITEM', item_id='nothing')
        g.call(g.A, 'log.read', last=5)
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'settings.configure', expect='INVALID_ARGUMENT', set_point_k=277.15)          # not a 4 C fridge
        g.call(g.A, 'settings.configure', expect='OUT_OF_LIMITS', high_alarm_k=SET + 2.0)
        g.call(g.A, 'settings.configure', expect='LIMITS_CONFLICT', set_point_k=223.15)
        g.call(g.A, 'interior.dry', expect='POWER_ON')
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)
        h = Rig(monitoring=False)
        h.call(h.A, 'log.read', expect='NO_MONITORING', last=5)


class Physics(unittest.TestCase):
    def test_pull_down_energy_and_display(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        g.step(60.0)
        self.assertEqual(g.read('state.current'), 'pull_down')
        t = g.until(lambda: g.read('truth.thermal')['probe_k'] <= SET, 6 * 3600.0, 30.0)
        self.assertAlmostEqual(t, rt.DOC['pull_down_s'], delta=150.0)
        g.step(12 * 3600.0)
        self.assertEqual(g.read('state.current'), 'running')
        self.assertEqual(g.read('display.temperature'), -80.0)
        e0 = g.ledger()['electric_energy_j']
        g.step(86400.0)
        self.assertAlmostEqual((g.ledger()['electric_energy_j'] - e0) / 3.6e6, rt.DOC['energy_kwh_day'], delta=0.1)
        th = g.read('truth.thermal')
        self.assertLess(th['condenser_k'], rt.ENG['condenser_e04_k'])
        self.assertLessEqual(g.read('truth.power')['mains_current_a'], rt.DOC['current_a'])
        _balance(self, g.ledger())

    def test_door_vacuum_auto_vent_and_recovery(self):
        g = Rig()
        g.cold()
        g.open_all()
        g.step(60.0)
        peak = g.read('truth.thermal')['probe_k']
        self.assertGreater(peak, SET + 20.0)
        g.close_all()
        g.step(20.0)
        dp = g.read('truth.thermal')['pressure_deficit_pa']
        self.assertGreater(dp, rt.ENG['door_open_dp_pa'])
        g.call(g.A, 'handle.set', locked=False)
        g.call(g.A, 'door.move', expect='NEGATIVE_PRESSURE', angle_rad=1.0)              # the vacuum holds the door
        g.call(g.A, 'vent.press')
        t = g.until(lambda: g.read('truth.thermal')['pressure_deficit_pa'] <= rt.ENG['door_open_dp_pa'], 600.0, 5.0)
        self.assertLessEqual(t, 120.0)                                                    # auto vent: 1-2 min (manual)
        g.call(g.A, 'handle.set', locked=True)
        t = g.until(lambda: g.read('truth.thermal')['probe_k'] <= SET + 0.5, 3 * 3600.0, 10.0)
        self.assertAlmostEqual(t + 160.0, rt.DOC['recovery_s'], delta=240.0)
        self.assertGreater(g.read('truth.thermal')['frost_kg']['liner'], 0.005)          # humid room air froze out
        _balance(self, g.ledger())

    def test_inner_door_rules_and_partial_opening(self):
        g = Rig()
        g.cold()
        g.call(g.A, 'handle.set', locked=False)
        g.call(g.A, 'door.move', angle_rad=0.8)
        g.call(g.A, 'inner_door.move', expect='OUTER_DOOR_CLOSED', compartment=3, angle_rad=1.5)
        g.call(g.A, 'door.move', angle_rad=1.6)
        g.call(g.A, 'inner_door.move', compartment=3, angle_rad=1.5)
        g.call(g.A, 'door.move', expect='INNER_DOOR_OPEN', angle_rad=0.0)
        g.call(g.A, 'handle.set', expect='DOOR_OPEN', locked=True)
        g.step(60.0)
        air = g.read('truth.thermal')['air_k']
        self.assertGreater(air[2], air[0] + 10.0)                                         # only compartment 3 inner door open
        g.call(g.A, 'inner_door.move', compartment=3, angle_rad=0.0)
        g.call(g.A, 'door.move', angle_rad=0.0)

    def test_alarm_delays_mute_relay_and_resound(self):
        g = Rig()
        g.cold()
        g.call(g.A, 'settings.configure', onsite_delay_s=0.0, remote_delay_s=600.0)
        g.open_all()
        t = g.until(lambda: 'TEMP_HIGH' in g.read('alarms.active'), 1800.0, 5.0)
        self.assertLess(t, 300.0)
        self.assertTrue(g.read('indicator.temp_alarm') and g.read('alarm.audible'))
        self.assertFalse(g.read('alarm.remote_relay'))
        g.call(g.A, 'alarm.mute')
        self.assertFalse(g.read('alarm.audible'))
        g.step(700.0)
        self.assertTrue(g.read('alarm.remote_relay'))                                     # remote delay elapsed
        g.close_all()
        g.until(lambda: 'TEMP_HIGH' not in g.read('alarms.active'), 4 * 3600.0, 30.0)
        self.assertFalse(g.read('alarm.audible'))
        r = g.call(g.A, 'alarm.mute')                                                     # no alarm: tone test
        self.assertTrue(r['test'] and g.read('alarm.audible'))
        g.step(10.0)
        self.assertFalse(g.read('alarm.audible'))
        _balance(self, g.ledger())

    def test_outage_battery_and_display_flashing(self):
        g = Rig()
        g.cold(settle=3600.0)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.step(5.0)
        self.assertEqual(g.read('state.current'), 'outage')
        self.assertTrue(g.read('indicator.power_fail') and g.read('alarm.remote_relay') and g.read('display.flashing'))
        g.step(6 * 3600.0)
        T6 = g.read('truth.thermal')['probe_k']
        self.assertGreater(T6, SET + 5.0)                                                 # warms without the compressor
        self.assertLess(T6, SET + 50.0)                                                   # 130 mm walls: hours, not minutes
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        g.step(5.0)
        self.assertNotIn('POWER_FAIL', g.read('alarms.active'))
        g.call(g.A, 'battery.set', on=False)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.step(5.0)
        self.assertIsNone(g.read('display.temperature'))                                  # battery circuit off: dark panel, no alarm
        self.assertEqual(g.read('alarms.active'), [])
        h = Rig()
        h.cold(settle=600.0)
        h.call(h.E, 'fault.inject', fault='battery_aged', active=True)
        h.call(h.E, 'fault.inject', fault='mains_outage', active=True)
        h.step(8 * 3600.0)
        self.assertEqual(h.read('truth.power')['battery_charge'], 0.0)                     # aged battery: 10 % of 72 h
        self.assertIsNone(h.read('display.temperature'))

    def test_lock_code_programming_mode(self):
        g = Rig()
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'code.change', code='4711')
        self.assertTrue(g.read('panel.code_active'))
        g.call(g.A, 'panel.lock')
        g.call(g.A, 'settings.configure', expect='PANEL_LOCKED', set_point_k=203.15)
        g.call(g.A, 'panel.unlock', expect='WRONG_CODE', code='1234')
        g.call(g.A, 'panel.unlock', code='4711')
        self.assertTrue(g.read('indicator.lock'))
        g.call(g.A, 'settings.configure', set_point_k=203.15, high_alarm_k=213.15, low_alarm_k=193.15)
        g.call(g.A, 'code.change', code='0000')
        g.call(g.A, 'panel.lock')
        self.assertFalse(g.read('panel.code_active'))
        g.call(g.A, 'settings.configure', onsite_delay_s=600.0)

    def test_loading_warm_samples_latent_heat_and_inventory(self):
        g = Rig()
        g.cold()
        g.open_all()
        g.call(g.A, 'load.place', **rt.ITEM_EXAMPLE)
        g.call(g.A, 'load.place', item_id='warm_box', compartment=3, kind='box', label='LIMS:B-17', water_kg=0.5, polymer_kg=0.2, cardboard_kg=0.15, initial_k=293.15)
        g.call(g.A, 'load.place', expect='DUPLICATE_ITEM', **rt.ITEM_EXAMPLE)
        g.call(g.A, 'load.place', expect='SHELF_OVERLOAD', item_id='heavy', compartment=3, kind='other', steel_kg=64.0, initial_k=193.15)
        for i in range(4):
            g.call(g.A, 'load.place', item_id=f'r{i}', compartment=5, kind='rack', steel_kg=2.0, initial_k=193.15)
        g.call(g.A, 'load.place', item_id='r4', compartment=5, kind='rack', steel_kg=2.0, initial_k=193.15)
        g.call(g.A, 'load.place', expect='COMPARTMENT_FULL', item_id='r5', compartment=5, kind='rack', steel_kg=2.0, initial_k=193.15)
        inv = g.call(g.A, 'inventory.read')['items']
        self.assertEqual({x['item_id'] for x in inv} >= {'rack_A1', 'warm_box'}, True)
        self.assertEqual(next(x for x in inv if x['item_id'] == 'warm_box')['label'], 'LIMS:B-17')
        g.close_all()
        g.step(1800.0)
        it = g.read('truth.items')['warm_box']
        self.assertGreater(it['temperature_k'], 260.0)                                    # still freezing (latent heat) after 30 min
        g.step(24 * 3600.0)
        it = g.read('truth.items')['warm_box']
        self.assertLess(it['temperature_k'], SET + 3.0)
        self.assertEqual(it['liquid_fraction'], 0.0)
        self.assertLess(g.read('truth.items')['rack_A1']['equivalent_storage_s_at_m80'], 2.0 * (g.h.t - 0.0))
        g.open_all()
        g.call(g.A, 'load.remove', item_id='warm_box')
        g.close_all()
        self.assertEqual(g.read('inventory.count'), 6)
        _balance(self, g.ledger())

    def test_gasket_vent_icing_deice_and_seals(self):
        g = Rig(relative_humidity=0.8)
        g.cold(settle=600.0)
        g.call(g.E, 'fault.inject', fault='door_gasket_damage', active=True)
        g.step(20 * 86400.0)
        fr = g.read('truth.thermal')['frost_kg']
        self.assertGreater(fr['vent'], rt.ENG['vent_iced_kg'])
        self.assertGreater(fr['gasket'], 0.05)
        g.call(g.E, 'fault.inject', fault='door_gasket_damage', active=False)    # an intact (but iced) gasket: the iced vent sets the pace
        g.open_all(1.6)
        g.step(30.0)
        g.close_all()
        g.call(g.A, 'vent.press')
        g.step(600.0)
        self.assertGreater(g.read('truth.thermal')['pressure_deficit_pa'], rt.ENG['door_open_dp_pa'])    # iced vent: 1-2 h
        t = g.until(lambda: g.read('truth.thermal')['pressure_deficit_pa'] <= rt.ENG['door_open_dp_pa'], 4 * 3600.0, 60.0)
        self.assertGreater(t, 1800.0)
        g.call(g.A, 'handle.set', locked=False)
        g.call(g.A, 'door.move', angle_rad=1.6)
        r = g.call(g.A, 'vent.deice')
        self.assertGreater(r['removed_kg'], 0.0)
        self.assertGreater(g.call(g.A, 'seals.clean')['removed_kg'], 0.0)
        g.call(g.A, 'door.move', angle_rad=0.0)
        _balance(self, g.ledger())

    def test_defrost_and_dry(self):
        g = Rig(relative_humidity=0.7)
        g.cold(settle=600.0)
        for _ in range(3):
            g.open_all()
            g.step(60.0)
            g.close_all()
            g.step(1800.0)
        frost = sum(g.read('truth.thermal')['frost_kg'].values())
        self.assertGreater(frost, 0.02)
        g.call(g.A, 'battery.toggle')
        g.call(g.A, 'power.toggle')
        self.assertFalse(g.read('power.on') or g.read('battery.on'))
        g.step(600.0)
        g.call(g.A, 'handle.set', locked=False)
        g.call(g.A, 'door.move', angle_rad=1.6)
        g.call(g.A, 'interior.dry', expect='FROST_REMAINING')
        for c in range(1, 6):
            g.call(g.A, 'inner_door.move', compartment=c, angle_rad=1.6)
        g.step(2 * 86400.0)
        self.assertEqual(sum(g.read('truth.thermal')['frost_kg'].values()), 0.0)
        r = g.call(g.A, 'interior.dry')
        self.assertGreaterEqual(r['wiped_kg'], frost)                                     # humid room air keeps freezing out on the still cold walls
        L = g.ledger()
        self.assertAlmostEqual(L['w_deposited_kg'], L['w_wiped_kg'] + L['w_deiced_kg'], delta=1e-9)
        self.assertEqual(g.call(g.E, 'truth.inspect')['thermal']['melt_water_kg'], 0.0)
        _balance(self, g.ledger())

    def test_filter_dust_e04_and_sensor_errors(self):
        g = Rig(ambient_k=303.15)
        g.cold(settle=600.0)
        for _ in range(4):
            g.step(23 * 86400.0)
        self.assertTrue(g.read('indicator.filter_clean'))
        g.call(g.A, 'filter.clean')
        self.assertFalse(g.read('indicator.filter_clean'))
        g.call(g.E, 'fault.inject', fault='condenser_fan_failure', active=True)
        g.step(3600.0)
        self.assertIn('E-04', g.read('alarms.active'))
        self.assertTrue(g.read('indicator.fault'))
        g.call(g.E, 'fault.inject', fault='condenser_fan_failure', active=False)
        g.call(g.E, 'fault.inject', fault='interior_sensor_failure', active=True)
        g.step(60.0)
        self.assertIsNone(g.read('display.temperature'))
        self.assertEqual(g.read('display.code'), 'E-01')
        self.assertTrue(g.read('alarm.audible'))
        _balance(self, g.ledger())


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()

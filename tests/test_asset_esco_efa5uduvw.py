"""tests for CHEM-199 esco_efa5uduvw (Esco Frontier Acela EFA-5UDUVW-9 fume hood with the Sentinel XL monitor): python3 -m unittest tests/test_asset_esco_efa5uduvw.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition and idle-skip exactness; rejected calls leave the state unchanged; every output validated against its schema and every
success changes only declared slots; the six documented operating points (flow per face velocity and static pressure); building exhaust with a
damper, dedicated blower on the fan switch, constant volume, blower failure and duct blockage; sash stop, Creep Down, hold and key lock;
Sentinel XL: uncalibrated gain and field calibration, low / high alarm with delay, mute, night setback, self test, menu, PIN, units; vapour
sources: evaporation ordering, drying, NIOSH REL, containment against velocity and cross draft (ASHRAE tracer at the design point); gas and
water fixtures; lighting; mains outage; liquid and vapour ledgers close."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import esco_efa5uduvw as rt  # noqa: E402

INST = 'esco_efa5uduvw_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
CMH = 1.0 / 3600.0


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.EscoEfa5uduvwRuntime(instance)
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
        assert expect in [e['code'] for e in rt.export_esco_efa5uduvw_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def truth(self):
        return self.read('truth.process')

    def cv(self, v=0.31, h=0.457):
        """Constant-volume exhaust giving face velocity v at sash h."""
        self.call(self.E, 'exhaust.configure', mode='constant_volume', flow_m3_s=v * rt.a_eff(h))
        self.call(self.A, 'sash.set', height_m=h, hold=h > rt.H_STOP)

    def settle(self):
        self.step(30.0)

    def calibrate(self):
        v = self.call(self.A, 'anemometer.measure')['face_velocity_m_s']                          # the agent's own reference (no truth)
        r = self.call(self.A, 'monitor.calibrate', reference_m_s=round(v, 3))
        assert set(r) == {'calibrated', 'reading_m_s'}, r
        self.step(10.0)

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']

    def events(self, who=None):
        return [e['code'] for e in self.call(who or self.E, 'events.read', since_s=0.0)['events']]


def _balance(test, L):
    test.assertLess(L['worst_liquid_balance_kg'], 1e-12, L)
    test.assertLess(L['worst_vapour_balance_kg'], 1e-12, L)


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
        m = rt.export_esco_efa5uduvw_contract_metadata()
        for k, c in m['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], m['state_slots'], (k, e))
            for p in c['preconditions']:
                for s in p['state_ids']:
                    self.assertIn(s, m['state_slots'], (k, s))
        for s in rt.SCENE_MIRRORED:
            self.assertIn(s, rt.SLOTS)
            self.assertEqual(rt.SLOTS[s]['visibility'], 'agent')
        units = {p['unit'] for p in m['mechanisms']['fume_hood_process']['parameters'].values()}
        self.assertFalse(units & {'deg', 'degC', 'mm', 'cmh'}, units)
        CHECKS.append(dict(id='metadata_consistent'))

    def test_declared_errors_cover_raised_codes(self):
        m = rt.export_esco_efa5uduvw_contract_metadata()
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
        for cap, args in (('truth.inspect', {}), ('ledger.read', {}), ('fault.inject', dict(fault='blower_failure', active=True)), ('sim.step', dict(dt_s=1.0)),
                          ('plant.configure', dict(rt.PLANT_EXAMPLE)), ('tracer.release', dict(rate_l_min=4.0)), ('disturbance.configure', dict(cross_draft_m_s=0.2))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        g.cv(0.1)
        g.call(g.A, 'source.place', source_id='tol', liquid_id='toluene' if 'toluene' in rt.LIQ else 'benzene', mass_kg=1.0, area_m2=0.1)
        g.step(5.0)
        self.assertIn('BZ_OVER_REL', g.events())                                                     # truth-only event
        self.assertNotIn('BZ_OVER_REL', g.events(g.A))

    def test_idempotent_request_ids(self):
        g = Rig()
        r1 = g.A.invoke(instance_id=INST, capability_id='light.toggle', arguments={}, request_id='same')
        r2 = g.A.invoke(instance_id=INST, capability_id='light.toggle', arguments={}, request_id='same')
        self.assertEqual(r1, r2)
        self.assertTrue(g.read('light.on'))
        r3 = g.A.invoke(instance_id=INST, capability_id='light.set', arguments=dict(on=False), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='a'), Rig(instance='b')
        a.cv()
        a.step(10.0)
        self.assertGreater(a.truth()['exhaust_flow_m3_s'], 0.1)
        self.assertEqual(b.truth()['exhaust_flow_m3_s'], 0.0)
        self.assertEqual(b.read('sash.height_m'), 0.0)

    def test_step_partition_and_skip_exact(self):
        def run(parts):
            g = Rig(seed=7)
            g.call(g.E, 'exhaust.configure', mode='building', suction_pa=250.0, duct_k=3000.0, damper_open=0.6)
            g.call(g.A, 'sash.set', height_m=0.62)
            g.call(g.A, 'source.place', source_id='a', liquid_id='ethanol', mass_kg=0.01, area_m2=0.003)
            g.call(g.A, 'source.place', source_id='b', liquid_id='water', mass_kg=0.05, area_m2=0.004)
            g.call(g.A, 'fixture.set', fixture='gas', open_fraction=0.2)
            g.call(g.A, 'monitor.press', key='test')
            g.call(g.E, 'disturbance.configure', walk_by_s=4.0)
            for dt in parts:
                g.step(dt)
            r = g.h.read('evaluator')
            return json.dumps({k: r[k] for k in r if k != 'clock.time_s'}, sort_keys=True, default=float), g.ledger()
        a, la = run([300.0])
        b, lb = run([0.1] * 3000)
        c, lc = run([7.33] * 40 + [6.8])
        self.assertEqual(a, b)
        self.assertEqual(a, c)
        self.assertEqual(json.dumps(la, sort_keys=True), json.dumps(lb, sort_keys=True))
        _balance(self, la)

    def test_rejections_change_nothing(self):
        g = Rig()
        g.call(g.A, 'source.place', source_id='w', liquid_id='water', mass_kg=0.01, area_m2=0.001)
        g.call(g.A, 'source.place', expect='DUPLICATE_ID', source_id='w', liquid_id='water', mass_kg=0.01, area_m2=0.001)
        g.call(g.A, 'source.remove', expect='NO_SOURCE', source_id='zz')
        if 'diethyl_ether' in rt.LIQ:
            g2 = Rig(ambient_k=310.15)
            g2.call(g2.A, 'source.place', expect='NOT_LIQUID', source_id='e', liquid_id='diethyl_ether', mass_kg=0.01, area_m2=0.001)
        g.call(g.A, 'keylock.set', mode='closed')
        g.call(g.A, 'sash.set', expect='SASH_LOCKED', height_m=0.2)
        g.call(g.A, 'keylock.set', mode='limit')
        g.call(g.A, 'sash.set', expect='ABOVE_STOP_LOCKED', height_m=0.6)
        g.call(g.A, 'sash.set', height_m=0.3)
        g.call(g.A, 'keylock.set', expect='SASH_NOT_CLOSED', mode='closed')
        g.call(g.A, 'keylock.set', mode='off')
        g.call(g.A, 'sash.set', height_m=0.6, hold=True)
        g.call(g.A, 'keylock.set', expect='SASH_ABOVE_STOP', mode='limit')
        g.call(g.A, 'monitor.configure', expect='OUT_OF_RANGE', low_alarm_m_s=0.6, high_alarm_m_s=0.5)
        g.call(g.A, 'monitor.calibrate', expect='NOT_CALIBRATABLE', reference_m_s=0.3)                 # no exhaust: the sensor reads below range
        g.call(g.A, 'monitor.configure', new_pin='1234')
        g.call(g.A, 'monitor.configure', expect='PIN_INVALID', low_alarm_m_s=0.25)
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=99))
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        g.call(g.A, 'monitor.press', expect='MONITOR_OFF', key='test')


class Airflow(unittest.TestCase):
    def test_documented_operating_points(self):
        for key in ('exhaust_design_30', 'exhaust_design_40', 'exhaust_design_50', 'exhaust_full_30', 'exhaust_full_40', 'exhaust_full_50'):
            p = rt.DOC[key]
            g = Rig()
            g.call(g.E, 'exhaust.configure', mode='constant_volume', flow_m3_s=p['flow_m3_s'])
            g.call(g.A, 'sash.set', height_m=p['sash_m'], hold=p['sash_m'] > rt.H_STOP)
            g.settle()
            T = g.truth()
            self.assertAlmostEqual(T['face_velocity_m_s'] / p['face_velocity_m_s'], 1.0, delta=0.09, msg=key)   # least-squares fit of the six points
            self.assertAlmostEqual(T['hood_static_pa'] / p['static_pa'], 1.0, delta=0.08, msg=key)

    def test_building_exhaust_damper_and_sash(self):
        g = Rig()
        g.call(g.E, 'exhaust.configure', mode='building', suction_pa=250.0, duct_k=3000.0, damper_open=0.5)
        g.call(g.A, 'sash.set', height_m=0.457)
        g.settle()
        q1 = g.truth()['exhaust_flow_m3_s']
        g.call(g.A, 'damper.set', open_fraction=1.0)
        g.settle()
        q2 = g.truth()['exhaust_flow_m3_s']
        self.assertGreater(q2, q1)
        v457 = g.truth()['face_velocity_m_s']
        g.call(g.A, 'sash.set', height_m=0.2)
        g.settle()
        self.assertGreater(g.truth()['face_velocity_m_s'], v457)                                     # smaller opening: faster face velocity
        g.call(g.E, 'fault.inject', fault='duct_blockage', active=True)
        g.settle()
        self.assertLess(g.truth()['exhaust_flow_m3_s'], 0.2 * q2)

    def test_blower_needs_the_fan_switch(self):
        g = Rig()
        g.call(g.E, 'exhaust.configure', mode='blower', duct_k=2000.0, blower_speed=0.8)
        g.call(g.A, 'sash.set', height_m=0.457)
        g.settle()
        self.assertEqual(g.truth()['exhaust_flow_m3_s'], 0.0)
        g.call(g.A, 'fan.set', on=True)
        g.step(1.0)
        q1 = g.truth()['exhaust_flow_m3_s']
        g.settle()
        q = g.truth()['exhaust_flow_m3_s']
        self.assertGreater(q, q1)                                                                    # first-order response
        g.call(g.A, 'blower.set_speed', speed=0.4)
        g.settle()
        g.settle()
        self.assertAlmostEqual(g.truth()['exhaust_flow_m3_s'] / q, 0.5, delta=1e-6)
        g.call(g.E, 'fault.inject', fault='blower_failure', active=True)
        g.settle()
        self.assertLess(g.truth()['exhaust_flow_m3_s'], 1e-6)                                         # spinning down (tau 2 s)
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        self.assertFalse(g.read('outlets.powered'))

    def test_sash_stop_creep_down_hold_and_key_lock(self):
        g = Rig()
        g.call(g.A, 'sash.set', height_m=0.67)
        g.step(5.0)
        self.assertAlmostEqual(g.read('sash.height_m'), 0.67 - 5.0 * rt.ENG['creep_speed_m_s'], delta=1e-9)
        g.step(10.0)
        self.assertEqual(g.read('sash.height_m'), rt.H_STOP)                                        # back on the 457 mm stop
        self.assertIn('SASH_CREPT_DOWN', g.events(g.A))
        g.call(g.A, 'sash.set', height_m=0.67, hold=True)
        g.step(20.0)
        self.assertEqual(g.read('sash.height_m'), 0.67)
        g.call(g.A, 'sash.release')
        g.step(1.0)
        self.assertLess(g.read('sash.height_m'), 0.67)
        g.step(20.0)
        g.call(g.A, 'keylock.set', mode='limit')
        g.call(g.A, 'sash.set', height_m=0.0)
        g.call(g.A, 'keylock.set', mode='closed')
        self.assertEqual(g.read('keylock.mode'), 'closed')


class Monitor(unittest.TestCase):
    def test_uncalibrated_gain_and_field_calibration(self):
        g = Rig(seed=1)
        g.cv(0.31)
        g.settle()
        T = g.truth()
        self.assertNotAlmostEqual(T['sensor_gain'], 1.0, delta=0.005)
        g.calibrate()
        self.assertAlmostEqual(g.read('monitor.reading_m_s'), 0.31, delta=0.02)                      # anemometer +-2 % and 0.005 m/s, display noise
        self.assertTrue(g.read('monitor.settings')['calibrated'])
        self.assertTrue(g.read('indicator.safe'))
        self.assertEqual(g.read('display.text'), 'SAFE')

    def test_low_alarm_delay_mute_and_clear(self):
        g = Rig()
        g.cv(0.4)
        g.settle()
        g.calibrate()
        g.call(g.E, 'exhaust.configure', mode='constant_volume', flow_m3_s=0.15 * rt.a_eff(0.457))
        g.step(4.0)
        self.assertIsNone(g.read('monitor.alarm'))                                                   # still within the alarm delay
        g.step(10.0)
        self.assertEqual(g.read('monitor.alarm'), 'low')
        self.assertTrue(g.read('indicator.unsafe') and g.read('buzzer.on'))
        self.assertEqual((g.read('display.text'), g.read('display.lcd_cells')), ('LOW AIRFLOW', 'Lo FLo'))
        g.call(g.A, 'monitor.press', key='mute')
        self.assertTrue(g.read('indicator.mute') and not g.read('buzzer.on'))
        g.call(g.E, 'exhaust.configure', mode='constant_volume', flow_m3_s=0.4 * rt.a_eff(0.457))
        g.settle()
        self.assertIsNone(g.read('monitor.alarm'))
        self.assertFalse(g.read('monitor.muted'))
        g.call(g.E, 'exhaust.configure', mode='constant_volume', flow_m3_s=0.85 * rt.a_eff(0.457))
        g.settle()
        self.assertEqual(g.read('monitor.alarm'), 'high')

    def test_night_setback_self_test_menu_units_pin(self):
        g = Rig()
        g.cv(0.1)
        g.settle()
        self.assertEqual(g.read('monitor.alarm'), 'low')
        g.call(g.A, 'monitor.night_setback', closed=True)
        self.assertTrue(g.read('indicator.mute') and not g.read('buzzer.on'))
        g.call(g.A, 'monitor.night_setback', closed=False)
        g.call(g.A, 'monitor.press', key='test')
        self.assertEqual(g.read('display.text'), 'SELF TEST')
        self.assertTrue(g.read('indicator.safe') and g.read('indicator.unsafe') and g.read('indicator.mute') and g.read('buzzer.on'))
        g.step(3.5)
        self.assertNotEqual(g.read('display.text'), 'SELF TEST')
        g.call(g.A, 'monitor.press', key='menu')
        self.assertEqual(g.read('monitor.menu'), 'low')
        for _ in range(5):
            g.call(g.A, 'monitor.press', key='down')
        g.call(g.A, 'monitor.press', key='set')
        self.assertAlmostEqual(g.read('monitor.settings')['low_alarm_m_s'], 0.25, delta=1e-9)
        g.call(g.A, 'monitor.press', key='menu')
        g.call(g.A, 'monitor.press', key='menu')
        self.assertEqual(g.read('monitor.menu'), 'units')
        g.call(g.A, 'monitor.press', key='up')
        g.call(g.A, 'monitor.press', key='set')
        self.assertEqual(g.read('monitor.settings')['units'], 'imperial')
        g.step(31.0)
        self.assertIsNone(g.read('monitor.menu'))                                                    # menu timeout
        self.assertAlmostEqual(g.read('display.velocity'), g.read('monitor.reading_m_s') / rt.FPM, delta=1.1)   # fpm, whole numbers
        g.call(g.A, 'monitor.configure', new_pin='42', units='metric')
        g.call(g.A, 'monitor.press', key='menu')
        self.assertEqual(g.read('display.text'), 'PIN LOCKED')
        g.call(g.A, 'monitor.configure', pin='42', high_alarm_m_s=0.8)
        self.assertEqual(g.read('monitor.settings')['high_alarm_m_s'], 0.8)


class Vapours(unittest.TestCase):
    def test_evaporation_drying_and_ledger(self):
        g = Rig()
        g.cv(0.4)
        for sid, liq in (('a', 'acetone'), ('e', 'ethanol'), ('w', 'water')):
            if liq in rt.LIQ:
                g.call(g.A, 'source.place', source_id=sid, liquid_id=liq, mass_kg=0.002, area_m2=0.003)
        g.step(60.0)
        S = g.truth()['sources']
        if 'a' in S and 'e' in S:
            self.assertGreater(S['a']['evaporation_kg_s'], S['e']['evaporation_kg_s'])
        self.assertGreater(S['e']['evaporation_kg_s'], S['w']['evaporation_kg_s'])
        g.step(3 * 3600.0)
        self.assertIn('SOURCE_DRY', g.events(g.A))
        r = g.call(g.A, 'source.remove', source_id='w')['source']
        self.assertGreaterEqual(r['remaining_kg'], 0.0)
        _balance(self, g.ledger())

    def test_containment_tracer_against_velocity_and_cross_draft(self):
        res = {}
        for v, cross in ((0.31, 0.05), (0.2, 0.05), (0.31, 0.3)):
            g = Rig()
            g.cv(v)
            g.call(g.E, 'disturbance.configure', cross_draft_m_s=cross)
            g.call(g.E, 'tracer.release', rate_l_min=4.0)
            g.settle()
            res[(v, cross)] = g.call(g.E, 'truth.inspect')['process']['concentrations']['sulfur_hexafluoride']['breathing_zone_ppm']
        self.assertLess(res[(0.31, 0.05)], 0.010)                                                    # ASHRAE 110 certificate: < 0.010 ppm
        self.assertGreater(res[(0.2, 0.05)], 100 * res[(0.31, 0.05)])
        self.assertGreater(res[(0.31, 0.3)], 10 * res[(0.31, 0.05)])
        g = Rig()
        g.call(g.A, 'sash.set', height_m=0.457)
        g.call(g.E, 'tracer.release', rate_l_min=4.0)
        g.settle()
        self.assertEqual(g.truth()['escape_fraction'], 1.0)                                         # no exhaust: everything escapes

    def test_gas_and_water_fixtures(self):
        g = Rig()
        g.cv(0.3)
        g.call(g.A, 'fixture.set', fixture='cold_water', open_fraction=1.0)
        g.call(g.A, 'fixture.set', fixture='gas', open_fraction=1.0)
        g.step(60.0)
        L = g.ledger()
        self.assertAlmostEqual(L['water_m3'], 60.0 * rt.ENG['water_supply']['max_flow_m3_s'], delta=1e-12)
        self.assertGreater(L['substances']['methane']['exhausted_kg'], 0.0)
        T = g.truth()
        lfl = (1.0 - T['escape_fraction']) * T['gas_flow_m3_s'] / T['exhaust_flow_m3_s'] / rt.GAS['methane']['lower_flammable_limit']
        self.assertAlmostEqual(T['methane_exhaust_lfl_fraction'], lfl, delta=1e-9)                     # unlit gas: about 1 % of the LFL in the exhaust
        self.assertLess(T['methane_exhaust_lfl_fraction'], 0.25)
        self.assertNotIn('EXHAUST_FLAMMABLE', g.events())
        g.call(g.E, 'facility.configure', water_supply_pa=0.0)
        self.assertEqual(g.truth()['water_flow_m3_s'], 0.0)
        _balance(self, g.ledger())


class Utilities(unittest.TestCase):
    def test_light_and_mains_outage(self):
        g = Rig()
        g.call(g.A, 'light.set', on=True)
        self.assertEqual((g.read('lamp.lit'), g.read('lighting.illuminance_lux')), (True, 915.0))
        g.call(g.E, 'fault.inject', fault='lamp_failure', active=True)
        self.assertFalse(g.read('lamp.lit'))
        g.call(g.E, 'fault.inject', fault='lamp_failure', active=False)
        g.cv(0.4)
        g.settle()
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        self.assertEqual((g.read('lamp.lit'), g.read('display.text'), g.read('monitor.reading_m_s')), (False, '', None))
        g.step(10.0)
        self.assertGreater(g.truth()['exhaust_flow_m3_s'], 0.0)                                      # the external exhaust keeps running
        g.call(g.E, 'fault.inject', fault='mains_outage', active=False)
        g.step(1.0)
        self.assertTrue(g.read('lamp.lit'))
        g.call(g.A, 'light.toggle')
        g.call(g.A, 'fan.toggle')
        g.call(g.E, 'facility.configure', gas_supply_pa=1000.0)

    def test_idle_skip_fast(self):
        import time
        g = Rig()
        g.cv(0.4)
        g.call(g.A, 'source.place', source_id='w', liquid_id='water', mass_kg=0.5, area_m2=0.01)
        t0 = time.time()
        g.step(30 * 86400.0)
        self.assertLess(time.time() - t0, 2.0)
        _balance(self, g.ledger())


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        missing = set(rt.CAPS) - set(rt.UNSUPPORTED) - SEEN_CAPS - {'events.read'}
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))



class ReviewR1(unittest.TestCase):
    def test_calibration_hides_the_gain_and_uses_the_anemometer(self):
        g = Rig(seed=3)
        g.cv(0.31)
        g.settle()
        v_true = g.truth()['face_velocity_m_s']
        m = g.call(g.A, 'anemometer.measure')
        self.assertEqual(m['points'], 18)
        self.assertLess(abs(m['face_velocity_m_s'] / v_true - 1.0), 0.02 + 0.005 / v_true + 1e-9)
        r = g.call(g.A, 'monitor.calibrate', reference_m_s=0.5)                                    # a guessed reference is taken at face value
        self.assertEqual(r, dict(calibrated=True, reading_m_s=0.5))
        self.assertNotIn('gain', json.dumps(g.read(who=g.A)))
        g.call(g.A, 'sash.set', height_m=0.05)
        g.call(g.A, 'anemometer.measure', expect='SASH_TOO_LOW')

    def test_alarm_outside_the_display_range(self):
        g = Rig()
        g.call(g.A, 'monitor.configure', low_alarm_m_s=0.2, high_alarm_m_s=0.9)
        g.call(g.A, 'sash.set', height_m=0.457)
        g.step(20.0)                                                                                 # no exhaust: the signal is below 0.2 m/s
        self.assertEqual(g.read('display.velocity'), 0.2)
        self.assertEqual(g.read('monitor.alarm'), 'low')
        g.cv(1.2)
        g.step(30.0)
        self.assertEqual(g.read('display.velocity'), 0.9)
        self.assertEqual(g.read('monitor.alarm'), 'high')

    def test_mains_outage_darkens_the_monitor_at_once(self):
        g = Rig()
        g.call(g.A, 'sash.set', height_m=0.457)
        g.step(20.0)
        self.assertEqual(g.read('monitor.alarm'), 'low')
        g.call(g.E, 'fault.inject', fault='mains_outage', active=True)
        self.assertIsNone(g.read('monitor.alarm'))
        self.assertFalse(g.read('buzzer.on') or g.read('indicator.unsafe'))
        self.assertIsNone(g.read('monitor.reading_m_s'))

    def test_exhaust_configure_keeps_an_omitted_damper(self):
        g = Rig()
        g.call(g.E, 'exhaust.configure', mode='building', damper_open=0.5)
        g.call(g.E, 'exhaust.configure', mode='building', suction_pa=300.0)
        self.assertEqual(g.read('exhaust.controls')['damper_open'], 0.5)
        g.call(g.E, 'exhaust.configure', mode='building', damper_open=None)
        self.assertIsNone(g.read('exhaust.controls')['damper_open'])
        self.assertNotIn('exhaust.configure', g.A.describe(instance_id=INST)['capabilities'])

    def test_bypass_keeps_the_closed_sash_below_the_high_alarm(self):
        g = Rig()
        g.call(g.E, 'exhaust.configure', mode='building')
        for h in (0.0, 0.1, 0.2, 0.457):
            g.call(g.A, 'sash.set', height_m=h)
            g.step(30.0)
            self.assertIsNone(g.read('monitor.alarm'), h)
            self.assertLess(g.truth()['face_velocity_m_s'], 0.7)

    def test_containment_degrades_gradually(self):
        e = [rt.escape_fraction(v - 0.025, 0.2) for v in (0.1, 0.15, 0.2, 0.25, 0.3)]                 # face velocity with the default 0.05 m/s cross draft
        self.assertTrue(all(a > b for a, b in zip(e, e[1:])))
        self.assertTrue(0.02 < e[1] < 0.5 and 1e-3 < e[2] < 0.02 and e[3] < 1e-3)



class ReviewR2(unittest.TestCase):
    def test_nullable_arguments_are_validated(self):
        g = Rig()
        for bad in (0.0, 0.01, -3.0, 'abc', [1], {'a': 1}):
            g.call(g.E, 'exhaust.configure', expect='INVALID_ARGUMENT', mode='building', damper_open=bad)
        for bad in (1234, {'p': 1}, 'x' * 500):
            g.call(g.A, 'monitor.configure', expect='INVALID_ARGUMENT', pin=bad)
        g.call(g.E, 'exhaust.configure', mode='building', damper_open=None)
        g.step(5.0)

    def test_agent_cannot_see_the_facility_exhaust(self):
        g = Rig()
        g.call(g.E, 'exhaust.configure', mode='building', suction_pa=250.0, duct_k=3000.0, damper_open=0.6)
        obs = g.read(who=g.A)
        self.assertNotIn('exhaust.state', obs)
        self.assertEqual(obs['exhaust.controls'], dict(damper_open=0.6, blower_speed=1.0))
        self.assertNotIn('EXHAUST_CONFIGURED', g.events(g.A))
        self.assertIn('EXHAUST_CONFIGURED', g.events(g.E))
        g.call(g.A, 'damper.set', open_fraction=0.4)
        self.assertEqual(g.read('exhaust.controls', who=g.A)['damper_open'], 0.4)



class ReviewR3(unittest.TestCase):
    def test_branch_suction_and_duct_are_seeded(self):
        vals = set()
        for seed in (1, 2, 3):
            g = Rig(seed=seed)
            e = g.read('exhaust.state')
            self.assertTrue(200.0 <= e['suction_pa'] <= 300.0 and 2100.0 <= e['duct_k'] <= 3900.0, e)
            self.assertNotEqual((e['suction_pa'], e['duct_k']), (rt.ENG['building_suction_pa'], rt.ENG['duct_k']))
            vals.add(round(e['suction_pa'], 6))
        self.assertEqual(len(vals), 3)

    def test_damper_set_needs_a_damper(self):
        g = Rig()
        g.call(g.E, 'exhaust.configure', mode='building')
        g.call(g.A, 'damper.set', expect='NO_DAMPER', open_fraction=0.3)
        g.call(g.E, 'exhaust.configure', mode='building', damper_open=1.0)
        g.call(g.A, 'damper.set', open_fraction=0.3)


if __name__ == '__main__':
    unittest.main()

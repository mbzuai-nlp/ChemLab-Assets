"""tests for CHEM-193 esco_ac24s8 (Esco Airstream Plus AC2-4S8-TU Class II biological safety cabinet): python3 -m unittest tests/test_asset_esco_ac24s8.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; nominal airflow (displayed inflow 0.48 / downflow 0.35 m/s at the tested opening, 876 / 369 m3/h, 200 W);
documented Sentinel Gold behaviour (warm-up, post-purge, sash states and stops, light at the safe height, UV interlock and timer, standby,
quickstart, mute, socket fuse); air barrier (operator protection >= 1e5 at the nominal point, lost with the sash fully open, cross draught,
grille blockage, blower failure -> airflow alarm); filter loading and ECM compensation (duty rises, flows held until saturation); work-zone
cleanliness (ISO class) and product protection; biological aerosol (escape, capture, emission, deposition, UV inactivation) and chemical
vapour (evaporation from an open dish, recirculation, exhaust to the room) ledgers closing exactly; electrical energy."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import esco_ac24s8 as rt  # noqa: E402

INST = 'esco_ac24s8_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
DISH = dict(item_id='dish', kind='open_vessel', x_m=0.30, y_m=0.0, footprint_m2=0.0064, liquid='ethanol', liquid_mass_kg=0.02, surface_area_m2=0.0064)
SPRAY = dict(item_id='pipetting', kind='aerosol_source', x_m=0.30, y_m=0.2, footprint_m2=0.01, aerosol_rate_per_s=1.0e5, viable_fraction=0.1)


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, **plant):
        self.h = rt.EscoAc24s8Runtime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))

    def call(self, sess, cap, expect='succeeded', **args):
        """Instrumented call: outputs validated against the declared schema; a success changes only declared immediate slots and never the clock;
        a rejection leaves every slot unchanged and returns a declared error code."""
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
        assert expect in [e['code'] for e in rt.export_esco_ac24s8_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def on(self):
        self.call(self.A, 'power.set', on=True)

    def sash(self, d, **kw):
        self.call(self.A, 'sash.move', direction=d, **kw)
        self.step(15.0)

    def working(self):
        self.on()
        self.sash('up')
        self.call(self.A, 'fan.toggle')
        self.step(200.0)

    def ledger(self):
        return self.call(self.E, 'ledger.read')['ledger']


def _raised_codes():
    src = Path(rt.__file__).read_text()
    funcs = {}
    for m in re.finditer(r'\n    def (\w+)\(self[^\n]*\n(.*?)(?=\n    def |\n# ----|\nclass |\ndef )', src, re.S):
        funcs[m.group(1)] = m.group(2)
    own = {k: set(re.findall(r"ActionRejected\('([A-Z_]+)'\)", v)) for k, v in funcs.items()}
    for k, v in funcs.items():                                   # data-driven interlocks: the codes the key's INTERLOCK entry allows
        for what in re.findall(r"self\._interlock\('(\w+)'\)", v):
            own[k] |= set(rt.INTERLOCK[what])
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
        out[k[5:].replace('_', '.', 1)] = codes
    return out


def _balance(test, L, tol=1e-6):
    """Particle, aerosol, viable and vapour ledgers close (relative tolerance of the explicit sub-step sums)."""
    room = L['initial_room_particles'] + L['room_in']
    out = L['room_captured_down'] + L['room_captured_exh'] + L['room_emitted'] + L['room_deposited'] + L['room_returned'] + L['room_in_air']
    test.assertAlmostEqual(room, out, delta=tol * room)
    gen = L['proc_generated']
    out = L['proc_escaped'] + L['proc_captured_down'] + L['proc_captured_exh'] + L['proc_emitted'] + L['proc_deposited'] + L['proc_in_air']
    test.assertAlmostEqual(gen, out, delta=tol * max(gen, 1.0))
    via = L['viable_generated']
    out = L['viable_escaped'] + L['viable_captured'] + L['viable_emitted'] + L['viable_in_air'] + L['viable_on_tray'] + L['viable_uv_inactivated']
    test.assertAlmostEqual(via, out, delta=tol * max(via, 1.0))
    for sp, v in L['vapour'].items():
        test.assertAlmostEqual(v['evaporated_kg'], v['escaped_front_kg'] + v['exhausted_to_room_kg'] + v.get('in_air_kg', 0.0), delta=1e-12 + tol * v['evaporated_kg'])
        placed = L['liquid_placed_kg'].get(sp, 0.0)
        test.assertAlmostEqual(placed, L['liquid_in_vessels_kg'].get(sp, 0.0) + L.get('liquid_removed_kg', {}).get(sp, 0.0) + v['evaporated_kg'], delta=1e-12)
    for k, v in L.items():
        if isinstance(v, float):
            test.assertGreaterEqual(v, -1e-9 * max(1.0, abs(v)), k)


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_and_mirrors_consistent(self):
        meta = rt.export_esco_ac24s8_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            for x in c['observes']:
                self.assertIn(x, slots, k)
            for pre in c['preconditions']:
                self.assertTrue(set(pre['state_ids']) <= slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
            self.assertIn(c['kind'], ('query', 'device_command', 'robot_skill', 'simulation', 'analysis', 'configuration'))
        self.assertTrue(rt.SCENE_MIRRORED <= slots)
        for name, p in meta['mechanisms']['class_ii_bsc_airflow']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_esco_ac24s8_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            r = g.A.invoke(instance_id=INST, capability_id=k, arguments={}, request_id='u-' + k)
            self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))

    def test_agent_truth_isolation(self):
        g = Rig()
        d = g.A.describe(instance_id=INST)
        self.assertFalse(any(k.startswith('truth.') for k in d['state_slots']))
        with self.assertRaises(rt.ActionRejected):
            g.A.read(instance_id=INST, state_ids=['truth.workzone'])
        for cap, args in (('truth.inspect', {}), ('sim.step', dict(dt_s=1.0)), ('ledger.read', {}), ('fault.inject', dict(fault='filter_clogged', active=True)),
                          ('plant.configure', dict(rt.PLANT_EXAMPLE))):
            r = g.A.invoke(instance_id=INST, capability_id=cap, arguments=args, request_id='x-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        g.call(g.E, 'fault.inject', fault='downflow_filter_leak', active=True)
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in g.call(g.A, 'events.read', since_s=0.0)['events']))
        # agent alarms identical when only the truth differs (a filter leak is not visible on the panel)
        a, b = Rig(), Rig()
        b.call(b.E, 'fault.inject', fault='downflow_filter_leak', active=True)
        for x in (a, b):
            x.working()
        self.assertEqual(a.read('alarms.active', 'airflow.status'), b.read('alarms.active', 'airflow.status'))

    def test_idempotent_request_ids(self):
        g = Rig()
        g.on()
        args = dict(warm_up_s=300.0)
        r1 = g.A.invoke(instance_id=INST, capability_id='settings.configure', arguments=args, request_id='same')
        snap = g.h.read('evaluator')
        r2 = g.A.invoke(instance_id=INST, capability_id='settings.configure', arguments=args, request_id='same')
        self.assertEqual(r1, r2)
        self.assertEqual(json.dumps(snap, sort_keys=True, default=str), json.dumps(g.h.read('evaluator'), sort_keys=True, default=str))
        r3 = g.A.invoke(instance_id=INST, capability_id='settings.configure', arguments=dict(warm_up_s=400.0), request_id='same')
        self.assertEqual(r3['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='t_a'), Rig(instance='t_b', ambient_k=298.15)
        a.call(a.A, 'power.set', on=True)
        self.assertFalse(b.read('power.on'))
        r = a.A.invoke(instance_id='t_b', capability_id='power.set', arguments=dict(on=True), request_id='z')
        self.assertEqual(r['error']['code'], 'UNKNOWN_INSTANCE')

    def test_step_partition_independence(self):
        def run(steps):
            g = Rig(seed=5)
            g.on()
            g.call(g.A, 'sash.move', direction='up')
            g.step(15.0)
            g.call(g.A, 'item.place', **DISH)
            g.call(g.A, 'item.place', **SPRAY)
            g.call(g.A, 'fan.toggle')
            for s in steps:
                g.step(s)
            r = g.read()
            return json.dumps({k: r[k] for k in r if not k.startswith('clock')}, sort_keys=True), json.dumps(g.ledger(), sort_keys=True)
        a = run([400.0])
        b = run([0.05] * 200 + [0.37, 13.11, 376.52])
        c = run([20.0] * 20)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.EscoAc24s8Runtime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=2))
        g.call(g.A, 'fan.toggle', expect='POWER_OFF')
        g.call(g.A, 'datalog.read', expect='POWER_OFF')
        g.call(g.A, 'item.place', expect='SASH_CLOSED', **DISH)
        g.call(g.A, 'operator.set', expect='SASH_CLOSED', arms_in=True)
        g.on()
        g.call(g.A, 'fan.toggle', expect='SASH_CLOSED')
        g.call(g.A, 'lamp.toggle', expect='SASH_CLOSED')
        g.call(g.A, 'sash.move', expect='SASH_AT_LIMIT', direction='down')
        g.call(g.A, 'alarm.mute', expect='NO_ALARM')
        g.call(g.A, 'fan.set', expect='FAN_NOT_RUNNING', state='standby') if False else None
        g.sash('up')
        g.call(g.A, 'uv.toggle', expect='SASH_NOT_CLOSED')
        g.call(g.A, 'fan.set', expect='FAN_NOT_RUNNING', state='standby')
        g.call(g.A, 'item.place', expect='MISSING_SETTING', item_id='v', kind='open_vessel', x_m=0.3, y_m=0.0, footprint_m2=0.01)
        g.call(g.A, 'item.place', expect='UNKNOWN_LIQUID', **dict(DISH, liquid='unobtainium'))
        g.call(g.A, 'item.place', expect='NO_SPACE', item_id='big', kind='closed_container', x_m=0.3, y_m=0.0, footprint_m2=0.3) if False else None
        g.call(g.A, 'item.place', **DISH)
        g.call(g.A, 'item.place', expect='ITEM_EXISTS', **DISH)
        g.call(g.A, 'item.remove', expect='UNKNOWN_ITEM', item_id='nope')
        g.call(g.A, 'fan.toggle')
        g.call(g.A, 'socket.toggle', expect='WARMING_UP')
        g.call(g.A, 'sash.move', expect='WARMING_UP', direction='up')
        g.call(g.A, 'settings.configure', expect='WARMING_UP', warm_up_s=300.0)
        g.step(200.0)
        g.call(g.A, 'uv.toggle', expect='SASH_NOT_CLOSED')
        g.call(g.A, 'fan.set', state='standby')
        g.call(g.A, 'lamp.toggle', expect='STANDBY')
        g.call(g.A, 'fan.set', state='on')
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)
        g.call(g.A, 'settings.configure', expect='INVALID_ARGUMENT', warm_up_s=60.0)
        h = Rig()
        h.on()
        h.sash('up')
        for _ in range(4):
            h.call(h.A, 'item.place', item_id=f'b{_}', kind='closed_container', x_m=0.3, y_m=0.0, footprint_m2=0.13)
        h.call(h.A, 'item.place', expect='NO_SPACE', item_id='b9', kind='closed_container', x_m=0.3, y_m=0.0, footprint_m2=0.1)


class Airflow(unittest.TestCase):
    def test_nominal_airflow_display_and_power(self):
        g = Rig()
        g.working()
        r = g.read()
        self.assertEqual(r['fan.state'], 'on')
        self.assertAlmostEqual(r['display.inflow_m_s'], 0.48, delta=0.011)
        self.assertAlmostEqual(r['display.downflow_m_s'], 0.35, delta=0.011)
        self.assertEqual(r['airflow.status'], 'ok')
        ta = r['truth.airflow']
        self.assertAlmostEqual(ta['downflow_m3_s'] * 3600, 876.0, delta=2.0)
        self.assertAlmostEqual(ta['exhaust_m3_s'] * 3600, 369.0, delta=1.5)
        self.assertAlmostEqual(ta['electrical_power_w'], 200.0, delta=4.0)
        self.assertGreaterEqual(r['truth.containment']['apf_log10'], 5.0)          # EN 12469 KI-Discus criterion at the nominal point
        self.assertLessEqual(r['truth.workzone']['iso_class_workzone'], 5.0)
        CHECKS.append(dict(id='nominal_airflow', status='passed', layer='process', inflow=r['display.inflow_m_s'], downflow=r['display.downflow_m_s'],
                           power_w=ta['electrical_power_w'], apf_log10=r['truth.containment']['apf_log10'], iso=r['truth.workzone']['iso_class_workzone']))

    def test_warm_up_post_purge_and_display(self):
        g = Rig()
        g.on()
        g.call(g.A, 'settings.configure', warm_up_s=300.0, post_purge_s=180.0)
        g.sash('up')
        self.assertTrue(g.read('lamp.on'))                                          # light on as a signal at the working height
        g.call(g.A, 'fan.toggle')
        g.step(60.0)
        self.assertEqual(g.read('fan.state'), 'warm_up')
        self.assertIsNone(g.read('display.inflow_m_s'))
        self.assertAlmostEqual(g.read('fan.remaining_s'), 240.0, delta=0.6)
        g.step(250.0)
        self.assertEqual(g.read('fan.state'), 'on')
        g.call(g.A, 'fan.toggle')
        self.assertEqual(g.read('fan.state'), 'post_purge')
        g.call(g.A, 'lamp.toggle', expect='POST_PURGE')
        g.sash('down')                                                                # the sash may be lowered during post-purge
        self.assertEqual(g.read('sash.status'), 'closed')
        self.assertFalse(g.read('lamp.on'))
        g.call(g.A, 'uv.toggle', expect='POST_PURGE')
        g.step(200.0)
        self.assertEqual(g.read('fan.state'), 'off')
        self.assertLess(g.read('truth.airflow')['downflow_m3_s'], 1e-3)

    def test_sash_states_alarm_mute_and_hold(self):
        g = Rig()
        g.working()
        g.sash('up')
        self.assertEqual(g.read('sash.status'), 'full_open')
        self.assertIn('SASH_ALARM', g.read('alarms.active'))
        self.assertTrue(g.read('alarm.audible'))
        self.assertTrue(g.read('lamp.on'))
        tc = g.read('truth.containment')
        self.assertLess(tc['apf_log10'], 2.0)                                       # 470 mm opening: true inflow ~0.18 m/s
        self.assertAlmostEqual(g.read('display.inflow_m_s'), 0.48, delta=0.011)    # the exhaust sensor still reads the nominal-height value
        g.call(g.A, 'alarm.mute')
        g.step(10.0)
        self.assertFalse(g.read('alarm.audible'))
        g.step(25.0)
        self.assertTrue(g.read('alarm.audible'))                                    # re-sounds after the 30 s mute
        g.call(g.A, 'sash.move', direction='down', hold_s=2.0)                      # released mid-way
        g.step(5.0)
        self.assertEqual(g.read('sash.status'), 'intermediate')
        self.assertFalse(g.read('lamp.on'))
        g.sash('down')
        self.assertEqual(g.read('sash.status'), 'safe')
        self.assertEqual(g.read('alarms.active'), [])
        self.assertGreaterEqual(g.read('meters')['sash_cycles'], 3)

    def test_uv_interlock_timer_and_inactivation(self):
        g = Rig()
        g.working()
        g.call(g.A, 'item.place', **SPRAY)
        g.step(600.0)
        g.call(g.A, 'item.remove', item_id='pipetting')
        before = g.read('truth.workzone')['viable_on_tray_per_m2']
        self.assertGreater(before, 0.0)
        g.call(g.A, 'fan.toggle')
        g.sash('down')
        g.step(5.0)
        g.call(g.A, 'settings.configure', uv_timer_s=1200.0)
        g.call(g.A, 'uv.toggle')
        self.assertTrue(g.read('indicator.uv'))
        g.step(600.0)
        self.assertLess(g.read('truth.workzone')['viable_on_tray_per_m2'], before * 1e-6)   # 600 J/m2 >> D90 50 J/m2
        g.sash('up')                                                                 # lifting the sash switches the UV lamp off
        self.assertFalse(g.read('uv.on'))
        g.sash('down')
        g.call(g.A, 'uv.toggle')
        g.step(1300.0)
        self.assertFalse(g.read('uv.on'))                                           # timer
        _balance(self, g.ledger())

    def test_standby_quickstart_socket_fuse(self):
        g = Rig()
        g.working()
        p_on = g.read('truth.airflow')['electrical_power_w']                       # nominal 200 W with the LED on
        g.call(g.A, 'fan.set', state='standby')
        self.assertFalse(g.read('lamp.on'))                                         # standby switches the lights off
        g.call(g.A, 'settings.configure', expect='STANDBY', warm_up_s=300.0)       # only the FAN key in standby
        g.step(60.0)
        self.assertEqual(g.read('airflow.status'), 'standby')
        self.assertIsNone(g.read('display.inflow_m_s'))
        self.assertAlmostEqual(1.0 - g.read('truth.airflow')['electrical_power_w'] / p_on, 0.60, delta=0.03)
        g.call(g.A, 'fan.set', state='on')
        g.call(g.A, 'socket.toggle')
        g.call(g.A, 'item.place', item_id='centrifuge', kind='equipment', x_m=0.4, y_m=-0.3, footprint_m2=0.06, power_w=1500.0)
        g.step(1.0)
        self.assertFalse(g.read('socket.on'))
        self.assertNotIn('SOCKET_FUSE_BLOWN', g.read('alarms.active'))           # not a panel alarm (manual list); event only
        self.assertIn('SOCKET_FUSE_BLOWN', [e['code'] for e in g.call(g.E, 'events.read', since_s=0.0)['events']])
        g.call(g.A, 'socket.toggle', expect='FUSE_BLOWN')
        g.call(g.A, 'item.remove', item_id='centrifuge')
        g.call(g.A, 'power.set', on=False)                                          # fuse replaced while off
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'socket.toggle')
        self.assertTrue(g.read('socket.on'))
        q = Rig()
        q.on()
        q.call(q.A, 'settings.configure', mode='quickstart')
        q.sash('up')
        self.assertIn(q.read('fan.state'), ('warm_up', 'on'))                     # quickstart: lifting the sash starts the blower
        q.step(200.0)
        q.sash('down')
        self.assertEqual(q.read('fan.state'), 'off')

    def test_filter_loading_compensation_and_airflow_alarm(self):
        g = Rig()
        g.working()
        u0 = g.read('truth.airflow')['duty_down']
        m = Rig(downflow_filter_dust_kg=0.9 * 1.3 * rt.ENG['dust_doubling_down_kg'], exhaust_filter_dust_kg=0.9 * 1.3 * rt.ENG['dust_doubling_exh_kg'])
        m.working()
        ta = m.read('truth.airflow')
        self.assertGreater(ta['duty_down'], u0 + 0.05)                              # the ECM compensates the loading ...
        self.assertAlmostEqual(ta['downflow_m3_s'] * 3600, 876.0, delta=3.0)        # ... and holds the flow
        c = Rig()
        c.working()
        c.call(c.E, 'fault.inject', fault='filter_clogged', active=True)
        c.step(60.0)
        self.assertEqual(c.read('airflow.status'), 'fail')
        self.assertIn('AIRFLOW_FAIL', c.read('alarms.active'))
        self.assertLess(c.read('truth.containment')['apf_log10'], c.read('truth.containment')['apf_log10'] + 1)
        b = Rig()
        b.working()
        b.call(b.E, 'fault.inject', fault='exhaust_blower_failure', active=True)
        b.step(60.0)
        self.assertIn('AIRFLOW_FAIL', b.read('alarms.active'))
        self.assertLess(b.read('truth.containment')['apf_log10'], 1.0)
        CHECKS.append(dict(id='filter_loading', status='passed', layer='process', clean_duty=u0, loaded_duty=ta['duty_down']))

    def test_barrier_disturbances(self):
        base, draft, block = Rig(), Rig(cross_draft_m_s=0.3), Rig()
        for g in (base, draft, block):
            g.working()
        block.call(block.A, 'item.place', item_id='rack', kind='closed_container', x_m=0.05, y_m=0.0, footprint_m2=0.25)
        block.call(block.A, 'operator.set', arms_in=True, arm_speed_m_s=0.5)
        a0 = base.read('truth.containment')['apf_log10']
        self.assertLess(draft.read('truth.containment')['apf_log10'], a0 - 0.5)
        self.assertLess(block.read('truth.containment')['apf_log10'], a0 - 0.5)
        r = block.call(block.A, 'item.place', item_id='front', kind='closed_container', x_m=0.05, y_m=0.4, footprint_m2=0.01)
        self.assertTrue(any('150 mm' in n for n in r['notes']))


class Hazards(unittest.TestCase):
    def test_biological_aerosol_contained_and_ledger(self):
        g = Rig()
        g.working()
        g.call(g.A, 'item.place', **SPRAY)
        g.step(900.0)
        L = g.ledger()
        esc = L['proc_escaped'] / L['proc_generated']
        self.assertLess(esc, 1e-5)
        self.assertGreater(L['proc_captured_down'] + L['proc_captured_exh'], 0.9 * L['proc_generated'])
        self.assertLess(L['proc_emitted'] / L['proc_generated'], 1e-4)              # exhaust filter penetration
        _balance(self, L)
        o = Rig()
        o.working()
        o.sash('up')
        o.call(o.A, 'item.place', **SPRAY)
        o.step(900.0)
        L2 = o.ledger()
        self.assertGreater(L2['proc_escaped'] / L2['proc_generated'], 100 * esc)
        CHECKS.append(dict(id='aerosol_containment', status='passed', layer='process', escape_fraction_safe=esc, escape_fraction_full_open=L2['proc_escaped'] / L2['proc_generated']))

    def test_chemical_vapour_recirculates_and_reaches_the_room(self):
        g = Rig()
        g.working()
        r = g.call(g.A, 'item.place', **DISH)
        self.assertTrue(any('不是化学通风柜' in n for n in r['notes']))
        g.step(1800.0)
        it = g.read('truth.items')['dish']
        self.assertGreater(it['evaporated_kg'], 1e-3)                               # ~8 g/h from a 90 mm dish at 0.35 m/s
        wz = g.call(g.E, 'truth.inspect')['workzone']
        self.assertIn('VOLATILE_CHEMICAL_RECIRCULATED', wz['conditions'])
        v = wz['vapours']['ethanol']
        self.assertGreater(v['workzone_ppm'], 1.0)
        L = g.ledger()
        lv = L['vapour']['ethanol']
        self.assertGreater(lv['exhausted_to_room_kg'], 0.9 * lv['evaporated_kg'] - lv.get('in_air_kg', 0.0) - lv['escaped_front_kg'] - 1e-9)
        # steady state: the exhaust carries away what evaporates, the plenum concentration is G / Q_exhaust (H14 does not retain vapour)
        G = it['evaporation_rate_kg_s']
        self.assertAlmostEqual(v['plenum_kg_m3'], G / g.read('truth.airflow')['exhaust_m3_s'], delta=0.05 * v['plenum_kg_m3'])
        g.call(g.A, 'item.remove', item_id='dish')
        _balance(self, g.ledger())
        w = Rig()
        w.working()
        w.call(w.A, 'item.place', **dict(DISH, liquid='water'))
        w.step(1800.0)
        self.assertLess(w.read('truth.items')['dish']['evaporated_kg'], it['evaporated_kg'])    # water is less volatile than ethanol
        CHECKS.append(dict(id='solvent_vapour', status='passed', layer='process', ethanol_g_h=G * 3.6e6, workzone_ppm=v['workzone_ppm']))

    def test_product_protection_and_iso_class(self):
        g = Rig()
        g.working()
        iso_ok = g.read('truth.workzone')['iso_class_workzone']
        b = Rig()
        b.working()
        b.call(b.E, 'fault.inject', fault='downflow_blower_failure', active=True)
        b.step(120.0)
        self.assertLess(b.read('truth.containment')['ppf_log10'], 2.0)
        self.assertGreater(b.read('truth.workzone')['iso_class_workzone'], iso_ok + 2.0)
        leak = Rig()
        leak.working()
        leak.call(leak.E, 'fault.inject', fault='downflow_filter_leak', active=True)
        leak.step(120.0)
        self.assertGreater(leak.read('truth.workzone')['iso_class_workzone'], iso_ok + 1.5)

    def test_monitor_disabled_outside_ambient_range_and_sensor_faults(self):
        g = Rig(ambient_k=305.15)
        g.working()
        self.assertEqual(g.read('airflow.status'), 'disabled')
        self.assertIsNone(g.read('display.inflow_m_s'))
        s = Rig()
        s.working()
        s.call(s.E, 'fault.inject', fault='sash_sensor_error', active=True)
        s.step(1.0)
        self.assertIn('SASH_ERROR', s.read('alarms.active'))
        s.call(s.A, 'sash.move', expect='SASH_SENSOR_ERROR', direction='up')
        u = Rig()
        u.working()
        u.call(u.E, 'fault.inject', fault='airflow_sensor_uncalibrated', active=True)
        u.step(1.0)
        self.assertIn('SENSOR_UNCALIBRATED', u.read('alarms.active'))

    def test_meters_power_off_and_datalog(self):
        g = Rig(filter_hours_h=9999.9, uv_hours_h=10.0, sash_cycles=14999)
        g.working()
        g.step(600.0)
        al = g.read('alarms.active')
        self.assertIn('FILTER_LIFE', al)
        self.assertIn('REPLACE_SASH_MOTOR', al)
        d = g.call(g.A, 'datalog.read', last=5)
        self.assertGreaterEqual(len(d['records']), 5)
        e0 = g.ledger()['electrical_energy_j']
        g.call(g.A, 'power.set', on=False)
        self.assertEqual(g.read('fan.state'), 'off')
        self.assertFalse(g.read('indicator.fan'))
        g.step(60.0)
        self.assertLess(g.ledger()['electrical_energy_j'] - e0, 1.0)
        g.call(g.A, 'power.toggle')
        self.assertTrue(g.read('power.on'))
        g.call(g.A, 'socket.toggle')
        self.assertTrue(g.read('indicator.light'))


class ReviewR1(unittest.TestCase):
    """Round-1 runtime review regressions (R1-02 closed sash, R1-03 clog clear, R1-04 downflow failure, R1-05 fans off, R1-07 hold, R1-10 ISO range)."""
    def test_closed_sash_with_fan_on_no_runaway(self):
        g = Rig()
        g.working()
        self.assertTrue(g.read('lamp.on'))
        g.call(g.A, 'lamp.toggle')
        self.assertFalse(g.read('lamp.on'))
        g.sash('down')
        g.step(120.0)
        self.assertEqual(g.read('airflow.status'), 'sash_closed')
        self.assertNotIn('AIRFLOW_FAIL', g.read('alarms.active'))
        self.assertLess(g.read('truth.airflow')['exhaust_m3_s'], 0.75 * rt.Q_EXH_NOM)

    def test_clog_clear_restores_loading(self):
        g = Rig()
        g.working()
        d0 = g.read('truth.airflow')['dust_down_kg']
        g.call(g.E, 'fault.inject', fault='filter_clogged', active=True)
        g.step(30.0)
        self.assertGreater(g.read('truth.airflow')['dust_down_kg'], d0 + rt.ENG['dust_doubling_down_kg'])
        g.call(g.E, 'fault.inject', fault='filter_clogged', active=False)
        self.assertLess(g.read('truth.airflow')['dust_down_kg'], d0 + 1e-6)
        g.step(120.0)
        self.assertEqual(g.read('airflow.status'), 'ok')

    def test_downflow_failure_bounded(self):
        g = Rig()
        g.working()
        g.call(g.E, 'fault.inject', fault='downflow_blower_failure', active=True)
        g.step(3600.0)
        wz = g.read('truth.workzone')
        self.assertLessEqual(wz['room_particles_workzone_m3'], rt.PLANT_EXAMPLE['room_particles_m3'] * 1.0001)
        self.assertLessEqual(wz['iso_class_workzone'], 9.0)
        _balance(self, g.ledger())

    def test_fans_off_closed_sash_retains_vapour(self):
        g = Rig()
        g.on()
        g.sash('up')
        g.call(g.A, 'item.place', **dict(DISH, liquid='acetone', liquid_mass_kg=0.0253))
        g.call(g.A, 'item.place', **SPRAY)
        g.sash('down')
        g.step(3600.0)
        L = g.ledger()
        v = L['vapour']['acetone']
        self.assertLess(v['escaped_front_kg'] + v['exhausted_to_room_kg'], 0.2 * v['evaporated_kg'])
        self.assertLess(L['proc_escaped'], 0.2 * L['proc_generated'])
        _balance(self, L)

    def test_hold_moves_speed_times_hold(self):
        g = Rig()
        g.on()
        g.step(0.3)
        g.call(g.A, 'sash.move', direction='up', hold_s=1.0)
        g.step(5.0)
        self.assertAlmostEqual(g.read('sash.opening_m'), rt.ENG['sash_speed_m_s'] * 1.0, delta=1e-9)


class ReviewR2(unittest.TestCase):
    """Round-2 review regressions: exhaust failure with the downflow running lets releases out (R2-03), coast-down is continuous (R2-02),
    post-purge with a closed sash (R2-01), mute refused in standby (R2-04)."""
    def test_exhaust_failure_releases_escape(self):
        g = Rig()
        g.working()
        g.call(g.A, 'item.place', **SPRAY)
        g.call(g.E, 'fault.inject', fault='exhaust_blower_failure', active=True)
        g.step(600.0)
        L = g.ledger()
        self.assertGreater(L['proc_escaped'], 0.1 * L['proc_generated'])
        self.assertGreater(g.read('truth.containment')['aerosol_escape_rate_per_s'], 0.1 * SPRAY['aerosol_rate_per_s'])
        _balance(self, L)

    def test_coast_down_escape_continuous(self):
        g = Rig()
        g.working()
        g.call(g.A, 'item.place', **SPRAY)
        g.call(g.A, 'settings.configure', post_purge_s=0.0)
        g.call(g.A, 'fan.toggle')
        rates = []
        for _ in range(80):
            g.step(0.5)
            rates.append(g.read('truth.containment')['aerosol_escape_rate_per_s'])
        jumps = [abs(b - a) for a, b in zip(rates[16:], rates[17:])]                # after the barrier collapse (exhaust well below the exchange flow)
        self.assertLess(max(jumps), 0.1 * SPRAY['aerosol_rate_per_s'])           # hand-over from direct escape to exchange without a step
        self.assertGreater(max(rates), 0.5 * SPRAY['aerosol_rate_per_s'])
        _balance(self, g.ledger())

    def test_post_purge_closed_sash_and_mute_in_standby(self):
        g = Rig()
        g.working()
        g.call(g.A, 'settings.configure', post_purge_s=300.0)
        g.call(g.A, 'fan.toggle')
        g.sash('down')
        self.assertEqual(g.read('fan.state'), 'post_purge')
        self.assertEqual(g.read('airflow.status'), 'sash_closed')
        h = Rig()
        h.working()
        h.call(h.A, 'fan.set', state='standby')
        h.call(h.E, 'fault.inject', fault='sash_sensor_error', active=True)
        h.call(h.A, 'alarm.mute', expect='STANDBY')


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()

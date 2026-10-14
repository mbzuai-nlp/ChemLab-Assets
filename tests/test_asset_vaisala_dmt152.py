"""tests for CHEM-195 vaisala_dmt152 (Vaisala DRYCAP dew point transmitter DMT152, ISO G1/2"): python3 -m unittest tests/test_asset_vaisala_dmt152.py

Themes: contract consistency (slots, effects, errors raised vs declared); agent / evaluator truth isolation; idempotent request ids; two instances
isolated; step-partition independence; rejected calls leave the state unchanged; every output validated against its schema and every success
changes only declared slots; Hardy ITS-90 frost / dew point and ppmv; asymmetric response (wet -> dry T63 0.5 min / T90 7.5 min, dry -> wet
T63 2 s); accuracy within the spec over many units; pressure: the dew point at the sensor follows the sensor pressure, ppm needs the right PRES;
frost point below 0 C and condensation; power-up and weekly purge (frozen output), XPUR off; auto-calibration and the Autocal error; sampling
line wall sorption (wet new tubing dries slowly), polymer permeation, leak; unsuitable gases; analog outputs, limit / purge / malfunction alarm
levels and the 21 V current-output supply; calibration service; operating-temperature error; water ledger closing exactly."""
import json
import math
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from asset_runtime import vaisala_dmt152 as rt  # noqa: E402

INST = 'vaisala_dmt152_01'
CHECKS = []
SEEN_CAPS = set()
PROCESS_EVIDENCE = {}
N2_DRY = dict(gas='nitrogen', water_umol_mol=1.0, pressure_pa=101325.0, temperature_k=293.15)
N2_WET = dict(gas='nitrogen', water_umol_mol=1000.0, pressure_pa=101325.0, temperature_k=293.15)


def _differ(x, y):
    if isinstance(x, float) and isinstance(y, float):
        return abs(x - y) > 1e-12 * max(1.0, abs(x), abs(y))
    return json.dumps(x, sort_keys=True, default=str) != json.dumps(y, sort_keys=True, default=str)


class Rig:
    def __init__(self, seed=1, instance=INST, fresh=False, **plant):
        self.h = rt.VaisalaDmt152Runtime(instance)
        self.h.register_principal('agent', 'agent')
        self.h.register_principal('eval', 'evaluator')
        self.A = self.h.session('agent')
        self.E = self.h.session('eval')
        self.n = 0
        self.inst = instance
        self.call(self.E, 'plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
        if not fresh:                                                        # default: cap removed and screwed directly into the process line
            self.call(self.A, 'cap.remove')
            self.call(self.A, 'installation.set', mode='direct')

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
        assert expect in [e['code'] for e in rt.export_vaisala_dmt152_contract_metadata()['capabilities'][cap]['errors']], (cap, expect)
        return r

    def step(self, s):
        self.call(self.E, 'sim.step', dt_s=float(s))

    def read(self, *ids, who=None):
        sess = who or self.E
        o = sess.read(instance_id=self.inst, state_ids=list(ids) if ids else None)['observations']
        return {k: v['value'] for k, v in o.items()} if not ids or len(ids) > 1 else o[ids[0]]['value']

    def ready(self, process=N2_DRY, settle=3600.0):
        self.call(self.E, 'process.set', **process)
        self.call(self.A, 'power.set', on=True, voltage_v=24.0)
        self.step(settle)

    def until(self, pred, limit, every=0.5):
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
        out[k[5:].replace('_', '.', 1)] = codes
    return out


def _balance(test, L):
    lhs = L['water_in_mol'] + L['water_permeated_mol'] + L['water_leaked_mol'] + L['water_desorbed_mol']        # gas phase (quasi-steady)
    rhs = L['water_out_mol'] + L['water_adsorbed_mol']
    test.assertAlmostEqual(lhs, rhs, delta=1e-9 * max(lhs, 1e-12))
    walls = L['water_installed_with_tubing_mol'] - L['water_removed_with_tubing_mol'] + L['water_adsorbed_mol'] - L['water_desorbed_mol']
    test.assertAlmostEqual(walls, L['water_on_walls_mol'], delta=1e-9 * max(L['water_installed_with_tubing_mol'] + L['water_adsorbed_mol'], 1e-15))


class Contract(unittest.TestCase):
    def test_metadata_slots_effects_consistent(self):
        meta = rt.export_vaisala_dmt152_contract_metadata()
        slots = set(meta['state_slots'])
        self.assertEqual(slots, set(rt.SLOTS))
        for k, c in meta['capabilities'].items():
            for e in c['state_effects']:
                self.assertIn(e['state_id'], slots, k)
            for x in c['observes']:
                self.assertIn(x, slots, k)
            self.assertTrue({'INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE'} <= {e['code'] for e in c['errors']}, k)
        for name, p in meta['mechanisms']['trace_moisture_measurement']['parameters'].items():
            self.assertEqual(name, name.lower())
            self.assertIn(p['evidence'], ('documented', 'assumption'), name)

    def test_declared_errors_cover_raised_codes(self):
        meta = rt.export_vaisala_dmt152_contract_metadata()
        for cap, codes in _raised_codes().items():
            declared = {e['code'] for e in meta['capabilities'][cap]['errors']}
            self.assertTrue(codes <= declared, (cap, sorted(codes - declared)))

    def test_unsupported_capabilities(self):
        g = Rig()
        for k in rt.UNSUPPORTED:
            g.call(g.A, k, expect='UNSUPPORTED')

    def test_agent_truth_isolation(self):
        g = Rig()
        g.ready(settle=10.0)
        self.assertFalse(any(k.startswith('truth.') for k in g.read(who=g.A)))
        r = g.A.invoke(instance_id=INST, capability_id='truth.inspect', arguments={}, request_id='x')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = g.A.invoke(instance_id=INST, capability_id='process.set', arguments=N2_WET, request_id='y')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        g.call(g.E, 'fault.inject', fault='sensor_oil_contamination', active=True)
        ev = g.call(g.A, 'events.read', since_s=0.0)['events']
        self.assertFalse(any(e['kind'] in ('fault', 'truth') for e in ev))

    def test_idempotent_request_ids(self):
        g = Rig()
        a = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=True), request_id='same')
        b = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=True), request_id='same')
        self.assertEqual(a, b)
        c = g.A.invoke(instance_id=INST, capability_id='power.set', arguments=dict(on=False), request_id='same')
        self.assertEqual(c['error']['code'], 'REQUEST_CONFLICT')

    def test_two_instances_isolated(self):
        a, b = Rig(instance='i1'), Rig(instance='i2')
        a.ready(process=N2_WET, settle=600.0)
        self.assertFalse(b.read('power.on'))
        self.assertIsNone(b.read('reading.tdf_k'))

    def test_step_partition_independence(self):
        runs = []
        for parts in ([1800.0], [0.3, 7.7, 400.0, 1392.0], [0.5] * 60 + [1770.0]):
            g = Rig(seed=7)
            g.call(g.E, 'process.set', **N2_DRY)
            g.call(g.A, 'installation.set', **rt.INSTALL_EXAMPLE)
            g.call(g.A, 'power.set', on=True)
            for p in parts:
                g.step(p)
            v = g.read()
            v.pop('clock.time_s')
            runs.append(json.dumps(v, sort_keys=True, default=str))
        self.assertEqual(runs[0], runs[1])
        self.assertEqual(runs[0], runs[2])

    def test_rejections_change_nothing(self):
        g = Rig()
        r = rt.VaisalaDmt152Runtime('fresh')
        r.register_principal('e', 'evaluator')
        self.assertEqual(r.session('e').invoke(instance_id='fresh', capability_id='power.set', arguments=dict(on=True), request_id='p')['error']['code'], 'PLANT_REQUIRED')
        g.call(g.E, 'plant.configure', expect='SEED_FIXED', **dict(rt.PLANT_EXAMPLE, noise_seed=2))
        g.call(g.A, 'measurement.send', expect='POWER_OFF')
        g.call(g.A, 'errors.read', expect='POWER_OFF')
        g.call(g.A, 'settings.configure', expect='POWER_OFF', pressure_pa=1e5)
        g.call(g.A, 'output.configure', expect='POWER_OFF', channel=1, mode='0-5V')
        g.call(g.E, 'process.set', expect='PRESSURE_TOO_HIGH', **dict(N2_DRY, pressure_pa=55e5))
        g.call(g.A, 'installation.set', expect='MISSING_SETTING', mode='sampling_line', tubing_length_m=1.0)
        g.call(g.E, 'process.set', **N2_DRY)
        g.call(g.A, 'power.set', on=True)
        g.call(g.A, 'calibration.service', expect='POWER_ON')
        g.call(g.A, 'settings.configure', expect='PRESSURE_TOO_LOW', xpressure_pa=5000.0)
        g.call(g.A, 'output.configure', expect='RANGE_ORDER', channel=1, lo=250.0, hi=200.0)
        g.call(g.A, 'output.configure', expect='ERROR_LEVEL_OUT_OF_RANGE', channel=2, error_level=50.0)
        g.call(g.A, 'installation.set', **dict(rt.INSTALL_EXAMPLE, sensor_pressure_pa=101325.0))
        g.call(g.E, 'process.set', **dict(N2_DRY, pressure_pa=55e5))                 # sampling line: the line may exceed 50 bar
        g.call(g.A, 'installation.set', expect='PRESSURE_TOO_HIGH', mode='direct')
        g.call(g.E, 'fault.inject', expect='INVALID_ARGUMENT', fault='alien', active=True)


class Physics(unittest.TestCase):
    def test_hardy_frost_point_and_ppm(self):
        self.assertAlmostEqual(rt.e_water(273.16), 611.657, delta=0.05)
        self.assertAlmostEqual(rt.e_ice(273.16), 611.657, delta=0.05)
        x10 = 10e-6 / (1 + 10e-6)
        self.assertAlmostEqual(rt.dewpoint(x10, 101325.0) - 273.15, -60.5, delta=0.3)          # 10 ppmv ~ -60.5 C frost point (1 atm)
        self.assertAlmostEqual(rt.ppmv_from(rt.dewpoint(x10, 101325.0), 101325.0), 10.0, delta=0.01)
        self.assertGreater(rt.dewpoint(x10, 7e5), rt.dewpoint(x10, 1e5) + 15.0)                # pressure raises the dew point (tens of degrees)
        self.assertLess(rt.e_ice(263.15), rt.e_water(263.15))                                   # frost point below 0 C

    def test_asymmetric_response_matches_datasheet(self):
        g = Rig()
        g.ready(process=dict(N2_DRY, water_umol_mol=1000.0 * math.exp(0)), settle=4 * 3600.0)
        wet = rt.dewpoint(1000e-6 / (1 + 1000e-6), 101325.0)
        dry_x = 0.55e-6
        dry = rt.dewpoint(dry_x / (1 + dry_x), 101325.0)
        tr = lambda: g.read('truth.sensor')    # noqa: E731
        y = lambda s: s['y_k']  # noqa: E731
        g.call(g.E, 'process.set', **dict(N2_DRY, water_umol_mol=0.55))
        start = y(tr())
        t63 = g.until(lambda: start - y(tr()) >= 0.63 * (start - dry), 3600.0)
        t90 = g.until(lambda: start - y(tr()) >= 0.90 * (start - dry), 7200.0) + t63
        self.assertAlmostEqual(t63, 30.0, delta=10.0)
        self.assertAlmostEqual(t90, 450.0, delta=120.0)
        g.step(4 * 3600.0)
        start = y(tr())
        g.call(g.E, 'process.set', **dict(N2_DRY, water_umol_mol=1000.0))
        t63w = g.until(lambda: y(tr()) - start >= 0.63 * (wet - start), 600.0)
        self.assertLessEqual(t63w, 5.5)                                                         # 2 s at 20 C (+ line update granularity)
        CHECKS.append(dict(t63_drying_s=t63, t90_drying_s=t90, t63_wetting_s=t63w))

    def test_accuracy_within_spec_over_units(self):
        within = 0
        n = 24
        for seed in range(n):
            g = Rig(seed=100 + seed)
            g.ready(process=dict(N2_DRY, water_umol_mol=10.0), settle=2.5 * 3600.0)
            err = g.read('reading.tdf_k') - g.read('truth.gas')['dew_point_sensor_k']
            within += abs(err) <= rt.DOC['accuracy_dry_k']
        self.assertGreaterEqual(within, int(0.85 * n))

    def test_sampling_pressure_and_ppm_setting(self):
        g = Rig()
        hp = dict(N2_DRY, water_umol_mol=20.0, pressure_pa=7e5)
        g.call(g.E, 'process.set', **hp)
        g.call(g.A, 'installation.set', **dict(rt.INSTALL_EXAMPLE, tubing_length_m=0.3, pre_dried=True, sensor_pressure_pa=101325.0))
        g.call(g.A, 'power.set', on=True)
        g.step(6 * 3600.0)
        tg = g.call(g.E, 'truth.inspect')['gas']
        self.assertLess(tg['dew_point_sensor_k'], tg['dew_point_line_k'] - 15.0)               # several tens of degrees between 7 bar and 1 bar
        ppm_ok = g.read('reading.h2o_umol_mol')
        g.call(g.A, 'settings.configure', pressure_pa=7e5)                                       # wrong PRES (line pressure, not the sensor pressure)
        ppm_bad = g.read('reading.h2o_umol_mol')
        self.assertLess(abs(ppm_ok - 20.0), 0.2 + 0.4 * 20.0)
        self.assertLess(ppm_bad, 0.25 * ppm_ok)
        g.call(g.A, 'settings.configure', xpressure_pa=101325.0)                                 # XPRES overrides PRES
        self.assertAlmostEqual(g.read('reading.h2o_umol_mol'), ppm_ok, delta=1e-9)
        g.call(g.A, 'settings.configure', molar_mass_kg_mol=0.028014)
        self.assertAlmostEqual(g.read('reading.h2o_mg_kg') / g.read('reading.h2o_umol_mol'), rt.M_W / 0.028014, delta=1e-9)

    def test_power_up_purge_weekly_purge_and_xpur(self):
        g = Rig()
        g.call(g.E, 'process.set', **N2_DRY)
        g.call(g.A, 'power.set', on=True)
        g.step(1.0)
        self.assertEqual(g.read('state.current'), 'purge')
        self.assertIsNone(g.read('reading.tdf_k'))                                              # nothing measured yet
        self.assertTrue(g.read('reading.frozen'))
        g.call(g.A, 'output.configure', channel=1, mode='U-alarm', purge_status=True)       # purge status only in an ON/OFF alarm mode (AMODE 8)
        g.step(1.0)
        self.assertEqual(g.read('output.analog')['ch1']['state'], 'purge_status')
        self.assertAlmostEqual(g.read('output.analog')['ch1']['value'], 10.0, delta=1e-12)
        g.step(rt.DOC['startup_approx_reading_s'])
        self.assertIsNotNone(g.read('reading.tdf_k'))                                         # approximated reading after one minute
        self.assertTrue(g.read('reading.frozen'))
        g.call(g.A, 'output.configure', channel=1, mode='4-20mA')
        g.step(rt.DOC['startup_normal_s'][0])                                                 # start-up: normal operation after about 10 min
        self.assertEqual(g.read('state.current'), 'measuring')
        g.step(rt.DOC['purge_interval_s'] + 1.0)                                                 # weekly purge, counted from the end of the last one
        self.assertEqual(g.read('state.current'), 'purge')
        held = g.read('reading.tdf_k')
        g.call(g.E, 'process.set', **N2_WET)
        g.step(60.0)
        self.assertEqual(g.read('reading.tdf_k'), held)                                        # frozen to the last measured value
        g.step(rt.ENG['purge_duration_s'])
        self.assertGreater(g.read('reading.tdf_k'), held + 20.0)
        d0 = g.read('truth.sensor')['drift_k']
        g.call(g.A, 'settings.configure', purge_enabled=False)
        g.step(7 * 86400.0)
        self.assertGreater(g.read('truth.sensor')['drift_k'] - d0, 0.15)                       # XPUR OFF: extra wet-end drift (about 1 K / month)
        self.assertNotEqual(g.read('state.current'), 'purge')

    def test_autocal_error_after_failures(self):
        g = Rig()
        g.ready(settle=10.0)
        g.call(g.E, 'fault.inject', fault='autocal_disturbed', active=True)
        g.step(6 * 3600.0)
        self.assertIn('Autocal error', g.read('diagnostics.errors'))
        self.assertIn('malfunction', g.read('alarms.active'))
        self.assertIsNotNone(g.read('reading.tdf_k'))                                           # measurement continues
        g.call(g.E, 'fault.inject', fault='autocal_disturbed', active=False)
        g.step(3600.0)
        self.assertNotIn('Autocal error', g.read('diagnostics.errors'))

    def test_sampling_line_materials_leak_and_ledger(self):
        res = {}
        for mat in ('ss316l_ep', 'ptfe', 'pa6'):
            g = Rig()
            g.call(g.E, 'process.set', **dict(N2_DRY, water_umol_mol=0.5))
            r = g.call(g.A, 'installation.set', **dict(rt.INSTALL_EXAMPLE, tubing_material=mat, tubing_length_m=5.0))
            if mat != 'ss316l_ep':
                self.assertTrue(any('塑料' in n for n in r['notes']))
            g.call(g.A, 'power.set', on=True)
            g.step(1800.0)
            early = g.read('truth.gas')['dew_point_sensor_k']
            g.step(48 * 3600.0)
            res[mat] = (early, g.read('truth.gas')['dew_point_sensor_k'])
            _balance(self, g.ledger())
        d = Rig()
        d.call(d.E, 'process.set', **dict(N2_DRY, water_umol_mol=0.5))
        d.step(10.0)
        direct = d.read('truth.gas')['dew_point_sensor_k']
        self.assertGreater(res['ss316l_ep'][0], direct + 5.0)                                  # wet new tubing: wetter than the gas after 30 min
        self.assertLess(res['ss316l_ep'][1], res['ss316l_ep'][0] - 5.0)                        # and dries down over hours
        self.assertLess(abs(res['ss316l_ep'][1] - direct), 0.5)
        self.assertGreater(res['ptfe'][1], res['ss316l_ep'][1] + 1.0)                          # polymer permeation keeps it wetter
        self.assertGreater(res['pa6'][1], res['ss316l_ep'][1] + 1.0)                           # (per mm of wall the CMC film data rank PTFE above PA6)
        g = Rig()
        g.call(g.E, 'process.set', **dict(N2_DRY, water_umol_mol=0.5))
        g.call(g.A, 'installation.set', **dict(rt.INSTALL_EXAMPLE, tubing_length_m=1.0, pre_dried=True))
        g.step(3600.0)
        t0 = g.read('truth.gas')['dew_point_sensor_k']
        g.call(g.E, 'fault.inject', fault='sample_line_leak', active=True)
        g.step(4 * 3600.0)
        self.assertGreater(g.read('truth.gas')['dew_point_sensor_k'], t0 + 5.0)
        _balance(self, g.ledger())

    def test_unsuitable_gases_and_condensation(self):
        g = Rig()
        r = g.call(g.E, 'process.set', **dict(N2_DRY, gas='hydrogen', water_umol_mol=10.0))
        self.assertTrue(any('不适用' in n for n in r['notes']))
        g.call(g.A, 'power.set', on=True)
        g.step(4 * 3600.0)
        self.assertGreater(g.read('truth.sensor')['gas_bias_k'], 1.5)
        r = g.call(g.E, 'process.set', **dict(N2_DRY, gas='ammonia', water_umol_mol=10.0))
        self.assertTrue(any('咨询厂家' in n for n in r['notes']))
        g.call(g.E, 'process.set', expect='CONDENSING', **dict(N2_DRY, water_umol_mol=40000.0, temperature_k=288.15))
        g.call(g.E, 'process.set', **dict(N2_DRY, water_umol_mol=40000.0, temperature_k=313.15))   # hot humid gas, sampled at room temperature
        g.call(g.A, 'installation.set', **dict(rt.INSTALL_EXAMPLE, sensor_pressure_pa=N2_DRY['pressure_pa']))
        g.step(600.0)
        Ts = g.read('truth.sensor')['sensor_temperature_k'] if 'sensor_temperature_k' in g.read('truth.sensor') else 295.15
        self.assertLessEqual(g.read('truth.sensor')['target_k'], Ts + rt.ENG['condensation_margin_k'] + 1e-9)   # condensation: saturates at the sensor temperature
        self.assertGreater(g.read('reading.tdf_k'), 273.15)                                     # above 0 C: dew point
        self.assertFalse(g.read('reading.in_calibrated_range'))

    def test_outputs_alarms_and_supply(self):
        g = Rig()
        g.ready(process=dict(N2_DRY, water_umol_mol=10.0), settle=4 * 3600.0)
        td = g.read('reading.tdf_k')
        ch1 = g.read('output.analog')['ch1']
        self.assertAlmostEqual(ch1['value'], 0.004 + 0.016 * (td - 193.15) / 100.0, delta=1e-12)
        g.call(g.A, 'output.configure', channel=1, limit_alarm=dict(lo=150.0, hi=td - 5.0))
        g.step(1.0)
        self.assertNotIn('ch1_limit_alarm', g.read('alarms.active'))                           # AMODE 1-5: limit alarm disabled
        g.call(g.A, 'output.configure', channel=1, mode='I-alarm')
        g.step(1.0)
        self.assertIn('ch1_limit_alarm', g.read('alarms.active'))
        self.assertAlmostEqual(g.read('output.analog')['ch1']['value'], 0.020, delta=1e-12)
        g.call(g.A, 'output.configure', channel=1, mode='4-20mA', limit_alarm=None)
        g.step(1.0)
        self.assertNotIn('ch1_limit_alarm', g.read('alarms.active'))
        g.call(g.A, 'output.configure', channel=2, mode='0-5V', quantity='ppm', lo=0.0, hi=100.0, error_level=0.0)
        g.step(1.0)
        self.assertAlmostEqual(g.read('output.analog')['ch2']['value'], 5.0 * g.read('reading.h2o_umol_mol') / 100.0, delta=1e-9)
        g.call(g.A, 'power.set', on=True, voltage_v=18.0)
        g.step(1.0)
        self.assertIn('Voltage too low for mA output error', g.read('diagnostics.errors'))
        g.call(g.A, 'power.set', on=True, voltage_v=24.0)
        g.call(g.E, 'fault.inject', fault='sensor_damage', active=True)
        g.step(1.0)
        self.assertIsNone(g.read('reading.tdf_k'))
        self.assertEqual(g.read('output.analog')['ch1']['state'], 'malfunction')
        self.assertAlmostEqual(g.read('output.analog')['ch1']['value'], 0.020, delta=1e-12)
        self.assertIn('T meas error', g.call(g.A, 'errors.read')['errors'])
        s = g.call(g.A, 'measurement.send')
        self.assertIsNone(s['tdf_k'])

    def test_drift_calibration_service_and_temperature_error(self):
        g = Rig(seed=5, calibration_age_s=1.8 * rt.YEAR_S)
        g.ready(process=dict(N2_DRY, water_umol_mol=300.0), settle=3600.0)
        self.assertGreater(g.read('calibration.age_s'), 1.8 * rt.YEAR_S)
        d0 = g.read('truth.sensor')['drift_k']
        g.call(g.A, 'power.set', on=False)
        g.call(g.A, 'calibration.service', expect='INSTALLED')
        g.call(g.A, 'installation.remove')
        r = g.call(g.A, 'calibration.service')
        self.assertTrue(r['calibrated'])
        self.assertLess(abs(g.read('truth.sensor')['drift_k']), abs(d0) + 1e-12)
        g.call(g.A, 'installation.set', mode='direct')
        g.call(g.A, 'power.set', on=True)
        g.call(g.E, 'process.set', **dict(N2_DRY, temperature_k=353.15))
        g.step(rt.DOC['startup_normal_s'][0] + 10.0)
        self.assertIn('Ambient temperature error', g.read('diagnostics.errors'))
        self.assertIsNone(g.read('reading.tdf_k'))


class ReviewR1(unittest.TestCase):
    """Round-1 review fixes: process truth hidden from agent events, polymer bulk water uptake, H2 bias gated / capped / reset, auto-calibration
    period start to start, setup checks, tick anchoring."""
    def test_agent_events_hide_the_process_truth(self):
        g = Rig()
        g.ready(process=dict(N2_DRY, water_umol_mol=7.3), settle=60.0)
        ev = g.call(g.A, 'events.read', since_s=0.0)['events']
        self.assertFalse([e for e in ev if e['code'] == 'PROCESS_SET'])

    def test_nylon_line_dries_much_slower_than_stainless(self):
        out = {}
        for mat in ('pa6', 'ss316l_ep'):
            g = Rig()
            g.ready(process=dict(N2_DRY, water_umol_mol=10.0), settle=1.0)
            g.call(g.A, 'installation.set', **dict(rt.INSTALL_EXAMPLE, tubing_material=mat, sensor_pressure_pa=N2_DRY['pressure_pa']))
            g.step(3600.0)
            out[mat] = g.read('truth.gas')['water_sensor_umol_mol'] if 'water_sensor_umol_mol' in g.read('truth.gas') else g.read('reading.h2o_umol_mol')
            out[mat + '_wall'] = g.read('truth.line')['wall_water_mol']
            _balance(self, g.ledger())
        self.assertGreater(out['pa6'], 5 * out['ss316l_ep'], out)
        self.assertGreater(out['pa6_wall'], 50 * out['ss316l_ep_wall'], out)

    def test_h2_bias_only_when_exposed_and_capped(self):
        g = Rig(fresh=True)                                                   # capped, not installed
        g.call(g.E, 'process.set', **dict(N2_DRY, gas='hydrogen'))
        g.step(10 * 86400.0)
        self.assertEqual(g.read('truth.sensor')['gas_bias_k'], 0.0)
        h = Rig()
        h.ready(process=dict(N2_DRY, gas='hydrogen'), settle=1.0)
        h.step(14 * 86400.0)
        self.assertLessEqual(h.read('truth.sensor')['gas_bias_k'], rt.ENG['h2_bias_max_k'] + 1e-9)

    def test_autocal_every_45_min_and_setup_checks(self):
        g = Rig()
        g.ready(process=dict(N2_DRY, water_umol_mol=300.0), settle=1.0)
        g.step(4 * 3600.0)
        ok = [e['time_s'] for e in g.call(g.A, 'events.read', since_s=0.0)['events'] if e['code'] in ('AUTOCAL_OK', 'AUTOCAL_FAILED')]
        gaps = {round(b - a) for a, b in zip(ok, ok[1:])}
        self.assertEqual(gaps, {int(rt.DOC['autocal_interval_s'])}, ok)
        g.call(g.A, 'installation.set', expect='INVALID_SETUP', **dict(rt.INSTALL_EXAMPLE, sensor_pressure_pa=40e5))

    def test_tick_anchored_when_stepping_before_the_plant(self):
        h = rt.VaisalaDmt152Runtime('x')
        h.step(0.5)
        self.assertEqual(h.tick, 1)


class ReviewR2(unittest.TestCase):
    """Round-2 minors fixed directly: persistent H2 bias, condensing line noted, consistent pressure tolerance, no reading from a damaged sensor in the purge."""
    def test_h2_bias_persists_after_switching_gas(self):
        g = Rig()
        g.ready(process=dict(N2_DRY, gas='hydrogen'), settle=10 * 3600.0)
        b = g.read('truth.sensor')['gas_bias_k']
        g.call(g.E, 'process.set', **N2_DRY)
        self.assertAlmostEqual(g.read('truth.sensor')['gas_bias_k'], b, delta=1e-9)

    def test_condensing_line_noted_and_pressure_tolerance(self):
        g = Rig()
        g.ready(process=N2_DRY, settle=60.0)
        g.call(g.A, 'installation.set', **dict(rt.INSTALL_EXAMPLE, sensor_pressure_pa=N2_DRY['pressure_pa'] * 1.00005))
        g.call(g.E, 'process.set', **N2_DRY)                                                     # same tolerance as installation.set
        r = g.call(g.E, 'process.set', **dict(N2_DRY, water_umol_mol=40000.0, temperature_k=333.15))
        self.assertTrue(any('冷凝' in n for n in r['notes']), r['notes'])

    def test_damaged_sensor_no_reading_during_startup(self):
        g = Rig()
        g.call(g.E, 'process.set', **N2_DRY)
        g.call(g.E, 'fault.inject', fault='sensor_damage', active=True)
        g.call(g.A, 'power.set', on=True, voltage_v=24.0)
        g.step(120.0)
        self.assertIsNone(g.call(g.A, 'measurement.send')['tdf_k'])


class TransportCap(unittest.TestCase):
    def test_cap_keeps_sensor_dry_and_blocks_installation(self):
        g = Rig(fresh=True)
        self.assertTrue(g.read('cap.fitted'))
        self.assertEqual(g.read('cap.offset_m'), 0.0)
        g.call(g.A, 'installation.set', expect='CAP_ON', mode='direct')
        g.call(g.A, 'power.set', on=True)
        g.step(3600.0)
        self.assertAlmostEqual(g.read('truth.sensor')['y_k'], rt.ENG['cap_storage_dew_point_k'], delta=1e-9)   # sealed under the cap
        g.call(g.A, 'cap.remove')
        self.assertAlmostEqual(g.read('cap.offset_m'), rt.CAP_TRAVEL, delta=1e-12)
        g.step(60.0)
        self.assertGreater(g.read('truth.sensor')['y_k'], rt.ENG['cap_storage_dew_point_k'] + 20.0)     # wets quickly in room air
        g.call(g.A, 'installation.set', mode='direct')
        g.call(g.A, 'cap.fit', expect='INSTALLED')
        g.call(g.A, 'installation.remove')
        g.call(g.A, 'installation.remove', expect='NOT_INSTALLED')
        g.call(g.A, 'cap.fit')
        g.call(g.A, 'cap.fit', expect='CAP_ALREADY_FITTED')


class ZCoverage(unittest.TestCase):
    def test_all_capabilities_exercised(self):
        implemented = {k for k in rt.CAPS if k not in rt.UNSUPPORTED}
        missing = implemented - SEEN_CAPS
        if len(SEEN_CAPS) > 5:
            self.assertFalse(missing, sorted(missing))


if __name__ == '__main__':
    unittest.main()

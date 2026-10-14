"""tests for CHEM-150 ika_c6000: python3 -m unittest tests/test_asset_ika_c6000.py

Topics (pipeline.md): set points only write targets, read/describe are pure, rejected preconditions leave the state unchanged,
two instances are isolated, agent/evaluator truth isolation, conservation (energy, water, oxygen), real return values validated
against the declared schemas (asset_tool lint_schema / validate_value), asynchronous completion and cancellation remnants; plus the
instrument behaviour documented by IKA (power-up sequence, working modes vs. cooling water, measurement sequence, 1 g benzoic acid
about 3 K, 4 / 6 measurements per hour, reproducibility, error codes, evaluation with acid corrections and Hu).
"""
import copy
import math
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "skills/chemistry-lab-assets/scripts"))
from asset_runtime import ika_c6000 as rt  # noqa: E402
import asset_tool  # noqa: E402

CHECKS, PROCESS_EVIDENCE = [], {}
_META = {}


def _meta():
    if not _META:
        _META.update(rt.export_ika_c6000_contract_metadata())
    return _META


_orig_invoke = rt.IkaC6000Runtime.invoke


def _instrumented_invoke(self, capability_id, **a):
    """Every successful call is checked against its declared output schema and immediate state effects (read does not advance time)."""
    before = self.read('evaluator') if capability_id != 'sim.step' else None
    out = _orig_invoke(self, capability_id, **a)
    meta = _meta()['capabilities'][capability_id]
    errs = asset_tool.validate_value(out, meta['output_schema'])
    if errs:
        raise AssertionError(f'{capability_id} output violates its schema: {errs[:3]}')
    if before is not None:
        after = self.read('evaluator')
        undeclared = {k for k in after if after[k] != before[k]} - set(rt.CAPS[capability_id]['effects'])
        if undeclared:
            raise AssertionError(f'{capability_id} changed undeclared state immediately: {sorted(undeclared)}')
    PROCESS_EVIDENCE.setdefault(capability_id, []).append(dict(arguments=copy.deepcopy(a), result_keys=sorted(out)))
    return out


rt.IkaC6000Runtime.invoke = _instrumented_invoke
RFID = 'C6012-0001'
THREAD = [dict(aid_name='IKA cotton wool thread', mass_kg=None)]


def powered(mode='isoperibol_25', seed=1, warm=3600.0, reference=26434.0e3, **plant):
    h = rt.IkaC6000Runtime()
    h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, noise_seed=seed, **plant))
    h.invoke('power.set', enabled=True)
    h.step(rt.SOFTWARE_LOAD_S + rt.SYSTEM_TEST_S + 60.0)
    if mode in h._available_modes() and h.state == 'wait':
        h.invoke('mode.set', mode=mode)
        h.step(rt.MODE_TEST_S + 1.0)                                  # manual 11.4: a mode change runs a system test
    # the benzoic acid in the library is NIST SRM 39j: enter its certificate value (the factory setting is 26461 J/g)
    h.invoke('settings.configure', o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=reference)
    h.invoke('vessel.scan', rfid=RFID)
    h.invoke('vessel.register', name='V1', serial_number='0012345678', previous_ignitions=0, platinum_wire=False)
    h.step(warm)
    return h


def start(h, sub='benzoic_acid', m=1.0e-3, form='pellet', kind='calibration', hang=True, aids=None, vaids=None, prepare=True):
    mid = h.invoke('measurement.create', name='run', sample_mass_kg=m, kind=kind, vessel_name=None, aids=aids or THREAD)['measurement_id']
    if prepare:
        bal = h.invoke('vessel.prepare', rfid=RFID, sample=dict(substance_id=sub, mass_kg=m, form=form) if sub else None, aids=vaids or [], absorption_water_kg=0.0)
        if bal['balance_sample_kg']:
            h.records[mid]['sample_mass_kg'] = bal['balance_sample_kg']
    op = h.invoke('measurement.start', measurement_id=mid)['operation_id']
    h.invoke('vessel.scan', rfid=RFID)
    if hang:
        h.invoke('vessel.hang', rfid=RFID)
    h.invoke('measurement.confirm')
    return mid, op


def finish(h, limit=4000.0, dt=1.0):
    t = h.time_s
    while h.run is not None and h.time_s - t < limit:
        h.step(dt)
    return h.time_s - t


def take_out(h):
    if h.error:
        h.invoke('error.acknowledge')
    if h.hung:
        h.invoke('vessel.remove')
    h.invoke('vessel.clean', rfid=RFID)


def calibrate(h, n=3, mode=None):
    ids = []
    for _ in range(n):
        mid, _ = start(h)
        finish(h)
        ids.append(mid)
        take_out(h)
    return ids, h.invoke('vessel.select_calibrations', vessel_name='V1', mode=mode or h.mode, measurement_ids=ids)


def snapshot(h):
    keep = {k: copy.deepcopy(v) for k, v in h.__dict__.items() if k not in ('rng', 'led', '_requests', 'revision')}
    keep['led'] = {k: v.value for k, v in h.led.items()}
    return keep


class IkaC6000RuntimeTests(unittest.TestCase):
    # ------------------------------------------------------------ identity, library, metadata
    def test01_module_identity(self):
        self.assertEqual(rt.ASSET_TYPE_ID, "instrument.ika_c6000")
        self.assertEqual(rt.CATALOG_ID, "CHEM-150")

    def test02_library_has_at_least_50_real_substances(self):
        lib = rt.library()
        subs = list(lib['by_id'].values())
        self.assertGreaterEqual(len(subs), 50)
        for s in subs:
            self.assertIn(s['evidence'], ('derived', 'measured_literature'))
            self.assertTrue(s['source']['file'] and s['source']['sha256'])
            self.assertTrue(3000.0 < s['gross_calorific_value_J_g'] < 50000.0)
        self.assertEqual(lib['by_id']['benzoic_acid']['certified_calorific_value']['value_J_g'], 26434.0)

    def test03_metadata_complete_and_schemas_lint_clean(self):
        meta = rt.export_ika_c6000_contract_metadata()
        for k, c in meta['capabilities'].items():
            self.assertTrue(c['source_ids'], k)
            for e in c['errors']:
                self.assertIn(e['code'], rt.ERROR_ZH, k)
            self.assertEqual(asset_tool.lint_schema(c['input_schema']), [], k)
            self.assertEqual(asset_tool.lint_schema(c['output_schema']), [], k)
            if c['availability'] == 'implemented':
                self.assertEqual(asset_tool.validate_value(c['example_arguments'], c['input_schema']), [], k)
        for k, s in meta['state_slots'].items():
            self.assertEqual(asset_tool.lint_schema(s['value_schema']), [], k)
            self.assertFalse(s['origin'] == 'process_truth' and s['visibility'] == 'agent', k)

    # ------------------------------------------------------------ purity, set points, preconditions
    def test04_step_rejects_negative_and_read_is_pure(self):
        h = powered(warm=10.0)
        with self.assertRaises(rt.ActionRejected):
            h.step(-1.0)
        t, rev = h.time_s, h.revision
        a = h.read('evaluator')
        b = h.read('evaluator')
        self.assertEqual(a, b)
        self.assertEqual((h.time_s, h.revision), (t, rev))
        h.register_principal('ag', 'agent')
        d1 = h.session('ag').describe(instance_id=h.instance_id)
        self.assertEqual((h.time_s, h.revision), (t, rev))
        self.assertTrue(d1['capabilities'])

    def test05_rejected_call_leaves_state_unchanged(self):
        h = powered(warm=10.0)
        before = snapshot(h)
        for cap, args, code in (('mode.set', dict(mode='isoperibol_30'), 'RUN_MODE_NOT_POSSIBLE'), ('plant.configure', dict(rt.PLANT_EXAMPLE, cooling_pressure_pa=2.0e5),
                                'COOLING_PRESSURE_TOO_HIGH'), ('vessel.register', dict(name='V1', serial_number='0012345678', previous_ignitions=0, platinum_wire=False),
                                'VESSEL_ALREADY_REGISTERED'), ('measurement.start', dict(measurement_id='m-99'), 'UNKNOWN_MEASUREMENT'),
                               ('aid.define', dict(list_no=1, name='IKA cotton wool thread', specific_energy_j_kg=None, absolute_energy_j=60.0), 'AID_FIXED'),
                               ('aid.define', dict(list_no=2, name='x', specific_energy_j_kg=1e7, absolute_energy_j=60.0), 'INVALID_ARGUMENT')):
            with self.assertRaises(rt.ActionRejected) as cm:
                h.invoke(cap, **args)
            self.assertEqual(str(cm.exception), code)
        self.assertEqual(snapshot(h), before)

    def test06_mode_set_writes_the_target_only(self):
        h = powered(warm=1800.0, cooling_water_k=293.15)          # 20 C cooling water: both 22 C and 25 C modes are offered
        self.assertIn('isoperibol_22', h._available_modes())
        tj = h.Tj
        out = h.invoke('mode.set', mode='isoperibol_22')
        self.assertAlmostEqual(out['jacket_setpoint_k'], 295.15)
        self.assertEqual(h.Tj, tj)                                  # no instantaneous temperature change
        h.step(3600.0)
        self.assertLess(abs(h.Tj - 295.15), 0.01)

    def test07_power_up_sequence_opens_cover_and_runs_system_test(self):
        h = rt.IkaC6000Runtime()
        h.invoke('plant.configure', **rt.PLANT_EXAMPLE)
        self.assertEqual(h.lift, 0.0)
        h.invoke('power.set', enabled=True)
        self.assertEqual(h.state, 'booting')
        h.step(rt.SOFTWARE_LOAD_S)
        self.assertEqual(h.state, 'system_test')
        h.step(20.0)
        self.assertGreater(h.lift, 0.0)
        h.step(rt.SYSTEM_TEST_S)
        self.assertEqual(h.state, 'wait')
        self.assertEqual(h.test_result, 'yellow')                  # preset Isoperibol 22 C does not fit 21.5 C cooling water
        self.assertEqual(h.lift, rt.LIFT_TRAVEL_M)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('measurement.start', measurement_id=h.invoke('measurement.create', name='a', sample_mass_kg=1e-3, kind='calibration',
                                                                  vessel_name=None, aids=THREAD)['measurement_id'])
        h.invoke('mode.set', mode='dynamic_25')
        self.assertEqual((h.state, h.test_result), ('system_test', None))   # manual 11.4: the mode change runs a system test
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('vessel.register', name='V1', serial_number='0012345678', previous_ignitions=0, platinum_wire=False)
        self.assertEqual(str(cm.exception), 'NOT_READY')
        h.step(rt.MODE_TEST_S)
        self.assertEqual((h.state, h.test_result), ('wait', 'green'))

    def test08_system_test_red_and_flow_errors(self):
        for plant, err in ((dict(cooling_water_k=303.15), 'SYSTEM_TEST_RED'), (dict(cooling_flow_m3_s=1e-6), 'E32'), (dict(cooling_pressure_pa=0.1e5), 'E31')):
            h = rt.IkaC6000Runtime()
            h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, **plant))
            h.invoke('power.set', enabled=True)
            h.step(400.0)
            self.assertEqual((h.state, h.error), ('error', err))
            with self.assertRaises(rt.ActionRejected):
                h.invoke('cover.close')

    def test09_working_modes_follow_the_cooling_water_table(self):
        h = rt.IkaC6000Runtime()
        h.invoke('plant.configure', **rt.PLANT_EXAMPLE)
        for t5, src, modes in ((289.15, 'tap_water', {22}), (289.15, 'chiller', set()), (291.15, 'chiller', {22}), (294.65, 'tap_water', {25}),
                               (298.15, 'tap_water', {30}), (293.15, 'tap_water', {22, 25}), (300.65, 'tap_water', set())):
            h.plant.update(cooling_water_k=t5, cooling_source=src)
            self.assertEqual({rt.mode_parts(m)[1] for m in h._available_modes()}, modes, (t5, src))

    # ------------------------------------------------------------ measurement sequence and physics
    def test10_calibration_sequence_phases_and_throughput(self):
        h = powered()
        mid, op = start(h)
        seen = []
        t0 = h.time_s
        while h.run is not None:
            h.step(1.0)
            if h.run and (not seen or seen[-1] != h.run['phase']):
                seen.append(h.run['phase'])
        cycle = h.time_s - t0
        self.assertEqual(seen, ['closing', 'filling', 'fill_check', 'o2_filling', 'equalising', 'pre_trial', 'main_trial', 'after_trial', 'degassing', 'draining', 'opening'])
        rec = h.records[mid]
        self.assertEqual(rec['status'], 'completed')
        self.assertEqual(h.operations[op]['phase'], 'succeeded')
        self.assertTrue(2.9 < rec['delta_t_k'] < 3.2)                # manual: approx. 1 g benzoic acid = 3 K
        self.assertTrue(8000.0 < rec['c_value_j_k'] < 9000.0)
        self.assertLess(abs(3600.0 / cycle - 4.0), 0.5)             # data sheet: 4 measurements per hour (isoperibol)
        self.assertEqual((h.lift, h.water_kg, h.vessels[RFID]['state']), (rt.LIFT_TRAVEL_M, 0.0, 'burned_degassed'))
        self.assertTrue(40000.0 / rec['c_value_j_k'] < 5.0)          # manual: 40 000 J approx. 5 K

    def test11_dynamic_mode_is_faster(self):
        h = powered('dynamic_25')
        mid, _ = start(h)
        cycle = finish(h)
        self.assertEqual(h.records[mid]['status'], 'completed')
        self.assertLess(abs(3600.0 / cycle - 6.0), 0.8)             # data sheet: 6 measurements per hour (dynamic)
        self.assertAlmostEqual(h.records[mid]['main_end_time_s'] - h.records[mid]['ignition_time_s'], rt.MAIN_MIN_S['dynamic'])
        self.assertIsNone(h.records[mid]['main_end_time_s'] and None)

    def test12_reproducibility_matches_the_data_sheet_ordering(self):
        rsd = {}
        for mode in ('isoperibol_25', 'dynamic_25'):
            h = powered(mode)
            cs = []
            for _ in range(8):
                mid, _ = start(h)
                finish(h)
                cs.append(h.records[mid]['c_value_j_k'])
                take_out(h)
            rsd[mode] = statistics.stdev(cs) / statistics.mean(cs)
        self.assertLess(rsd['isoperibol_25'], 0.0005)                # 0.05 % RSD (1 g benzoic acid)
        self.assertLess(rsd['dynamic_25'], 0.0015)                   # 0.15 % RSD
        self.assertGreater(rsd['dynamic_25'], rsd['isoperibol_25'])

    def test13_measurement_recovers_library_values_after_calibration(self):
        h = powered()
        calibrate(h, 3)
        for sub, m in (('naphthalene', 0.65e-3), ('sucrose', 1.6e-3), ('polymer_pe', 0.55e-3)):
            mid, _ = start(h, sub=sub, m=m, kind='measurement')
            finish(h)
            take_out(h)
            ref = rt._lib()['by_id'][sub]['gross_calorific_value_J_g'] * 1000.0
            self.assertLess(abs(h.records[mid]['gross_calorific_value_j_kg'] / ref - 1.0), 0.001, sub)

    def test14_acid_corrections_and_net_value(self):
        h = powered()
        calibrate(h, 2)
        for sub, m in (('urea', 2.0e-3), ('thianthrene', 0.8e-3)):
            mid, _ = start(h, sub=sub, m=m, kind='measurement')
            finish(h)
            h.invoke('vessel.remove')
            tit = h.invoke('washings.titrate', rfid=RFID)
            lib = rt._lib()['by_id'][sub]
            ev = h.invoke('measurement.evaluate', measurement_id=mid, sulfur_mass_fraction=lib['mass_fraction']['S'], total_acid_mol=tit['total_acid_mol'],
                          hydrogen_mass_fraction=lib['mass_fraction']['H'], moisture_mass_fraction=0.0)
            ref = lib['gross_calorific_value_J_g'] * 1000.0
            self.assertGreater(ev['gross_uncorrected_j_kg'], ref * 1.01, sub)          # acids add heat
            self.assertLess(abs(ev['gross_calorific_value_j_kg'] / ref - 1.0), 0.003, sub)
            self.assertAlmostEqual(ev['net_calorific_value_j_kg'], ev['gross_calorific_value_j_kg'] - rt.h_vap_water_j_kg() * rt.water_per_hydrogen() * lib['mass_fraction']['H'],
                                   delta=1e-6)
            with self.assertRaises(rt.ActionRejected):
                h.invoke('washings.titrate', rfid=RFID)
            h.invoke('vessel.clean', rfid=RFID)

    def test15_incomplete_combustion_and_volatile_losses(self):
        h = powered()
        h.invoke('settings.configure', o2_flush=True, auto_vessel_detection=True, reference_value_j_kg=26434.0e3)
        calibrate(h, 2)
        mid, _ = start(h, form='powder', kind='measurement')          # loose benzoic acid sprays (manual 6.3)
        finish(h)
        h.invoke('vessel.remove')
        self.assertEqual(h.invoke('vessel.inspect', rfid=RFID)['residue'], 'soot')
        self.assertLess(h.records[mid]['gross_calorific_value_j_kg'], 0.995 * 26434.0e3)
        h.invoke('vessel.clean', rfid=RFID)
        mid, _ = start(h, sub='pentane', m=0.5e-3, form='liquid', kind='measurement')
        finish(h)
        self.assertAlmostEqual(h.vessels[RFID]['vapour_lost_kg'], 0.5e-3, places=12)   # two flush vents (manual 11.4.2) carry all the vapour away
        self.assertEqual((h.records[mid]['status'], h.records[mid]['error_code']), ('failed', 'E04'))   # only the thread burns: no rise
        take_out(h)
        h.invoke('aid.define', list_no=2, name='Gelatine capsule C 9', specific_energy_j_kg=rt.AIDS['gelatine_capsule'], absolute_energy_j=None)
        mid, _ = start(h, sub='pentane', m=0.5e-3, form='capsule', kind='measurement', aids=THREAD + [dict(aid_name='Gelatine capsule C 9', mass_kg=0.12e-3)],
                       vaids=[dict(kind='gelatine_capsule', mass_kg=0.12e-3)])
        finish(h)
        take_out(h)
        ref = rt._lib()['by_id']['pentane']['gross_calorific_value_J_g'] * 1000.0
        self.assertLess(abs(h.records[mid]['gross_calorific_value_j_kg'] / ref - 1.0), 0.002)

    def test16_low_oxygen_pressure_gives_incomplete_combustion(self):
        h = powered()
        calibrate(h, 2)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, oxygen_supply_pa=1.2e6))
        mid, _ = start(h, kind='measurement')
        finish(h)
        h.invoke('vessel.remove')
        self.assertIn(h.invoke('vessel.inspect', rfid=RFID)['residue'], ('soot', 'unburnt_sample'))
        self.assertLess(h.records[mid]['gross_calorific_value_j_kg'], 0.9 * 26434.0e3)

    def test17_error_codes_E11_E04_E07(self):
        h = powered()
        mid, op = start(h, hang=False)
        finish(h)
        self.assertEqual((h.records[mid]['status'], h.records[mid]['error_code'], h.state, h.operations[op]['phase']), ('failed', 'E11', 'error', 'failed'))
        with self.assertRaises(rt.ActionRejected):
            h.invoke('cover.close')                                  # ERROR_PENDING
        h.invoke('error.acknowledge')
        self.assertFalse(h.records[mid]['ignited'])
        h.invoke('vessel.clean', rfid=RFID)
        mid, _ = start(h, sub=None)
        finish(h)
        self.assertEqual((h.records[mid]['error_code'], h.records[mid]['ignited']), ('E04', True))
        take_out(h)
        mid, _ = start(h)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, cooling_water_k=299.15))   # cooling water warmer than the start temperature
        finish(h)
        self.assertEqual(h.records[mid]['error_code'], 'E07')
        self.assertFalse(h.records[mid]['ignited'])

    def test18_cancel_before_and_after_ignition(self):
        h = powered()
        mid, op = start(h)
        h.step(100.0)
        h.register_principal('ag', 'agent')
        h._owners[op] = 'ag'
        h._owners[mid] = 'ag'
        s = h.session('ag')
        r = s.cancel(op, 'c1')
        self.assertEqual(r['status'], 'succeeded')
        finish(h)
        self.assertEqual((h.records[mid]['status'], h.operations[op]['phase']), ('cancelled_before_ignition', 'cancelled'))
        self.assertEqual((h.lift, h.water_kg, h.vessels[RFID]['state']), (rt.LIFT_TRAVEL_M, 0.0, 'prepared'))
        h.invoke('vessel.remove')
        op2 = h.invoke('measurement.start', measurement_id=mid)['operation_id']      # restartable
        h.invoke('vessel.scan', rfid=RFID)
        h.invoke('vessel.hang', rfid=RFID)
        h.invoke('measurement.confirm')
        while h.run['phase'] != 'main_trial':
            h.step(1.0)
        h._cancel_run(h.operations[op2])
        finish(h)
        self.assertEqual((h.records[mid]['status'], h.records[mid]['ignited']), ('cancelled_after_ignition', True))
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('measurement.start', measurement_id=mid)
        self.assertEqual(str(cm.exception), 'NOT_STARTABLE')

    def test19_power_loss_mid_run_and_degas(self):
        h = powered()
        mid, op = start(h)
        while h.run['phase'] != 'equalising':
            h.step(1.0)
        h.invoke('power.set', enabled=False)
        self.assertEqual((h.records.get(mid, {}).get('status', 'lost'), h.operations[op]['phase']), ('lost', 'failed'))
        self.assertEqual(h.vessels[RFID]['state'], 'pressurized')
        self.assertGreater(h.water_kg, 1.0)
        h.invoke('power.set', enabled=True)
        h.step(400.0)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('vessel.remove')                                 # VESSEL_PRESSURIZED
        h.invoke('maintenance.run', program='o2_degas')
        h.invoke('maintenance.run', program='empty_water')
        h.step(60.0)
        self.assertEqual((h.vessels[RFID]['state'], h.water_kg), ('prepared', 0.0))
        h.invoke('vessel.remove')

    def test20_shutdown_saves_records_power_off_without_it_loses_them(self):
        h = powered(warm=10.0)
        h.invoke('measurement.create', name='a', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=THREAD)
        h.invoke('system.shutdown')
        h.step(20.0)
        self.assertEqual(h.lift, 0.0)
        h.invoke('power.set', enabled=False)
        self.assertEqual(len(h.records), 1)
        h.invoke('power.set', enabled=True)
        h.step(400.0)
        h.invoke('measurement.create', name='b', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=THREAD)
        h.invoke('power.set', enabled=False)
        self.assertEqual(len(h.records), 1)
        self.assertEqual(h.events[-2]['code'], 'DATA_LOST')

    def test21_calibration_statistics_formulas(self):
        h = powered()
        ids, st = calibrate(h, 4)
        cs = [h.records[m]['c_value_j_k'] for m in ids]
        mean, sd = statistics.mean(cs), statistics.stdev(cs)
        self.assertAlmostEqual(st['c_value_j_k'], mean)
        self.assertAlmostEqual(st['rsd'], sd / mean)
        self.assertAlmostEqual(st['ucl_j_k'], mean + 3 * sd / 2.0)
        self.assertAlmostEqual(st['lwl_j_k'], mean - 2 * sd / 2.0)
        self.assertEqual(h.invoke('vessel.statistics', vessel_name='V1', mode=h.mode), st)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('vessel.select_calibrations', vessel_name='V1', mode='dynamic_25', measurement_ids=ids)   # CALIBRATION_MISMATCH

    def test22_simulation_recomputes_without_changing_records(self):
        h = powered()
        ids, st = calibrate(h, 2)
        rec = copy.deepcopy(h.records[ids[0]])
        out = h.invoke('measurement.simulate', measurement_id=ids[0], delta_t_k=3.0, ignition_energy_j=30.0, c_value_j_k=None, reference_value_j_kg=26000.0e3)
        q = 30.0 + 50.0
        self.assertAlmostEqual(out['c_value_j_k'], (26000.0e3 * rec['sample_mass_kg'] + q) / 3.0)
        self.assertEqual(h.records[ids[0]], rec)

    def test23_energy_limit_and_rise_flags(self):
        h = powered()
        calibrate(h, 2)
        mid, _ = start(h, sub='polymer_pe', m=0.9e-3, kind='measurement')         # ~43 kJ
        finish(h)
        take_out(h)
        self.assertIn('energy_above_40kJ', h.records[mid]['quality_flags'])
        self.assertTrue(h.vessels[RFID]['inspection_required'])
        self.assertIn('ENERGY_ABOVE_40KJ', [e['code'] for e in h.events])
        mid, _ = start(h, sub='benzoic_acid', m=0.3e-3, kind='measurement')
        finish(h)
        take_out(h)
        self.assertIn('rise_far_from_calibration', h.records[mid]['quality_flags'])

    def test24_ledgers_close(self):
        h = powered()
        calibrate(h, 2)
        mid, _ = start(h, sub='urea', m=1.5e-3, kind='measurement')
        h.step(500.0)
        led = h.ledger()
        self.assertLess(abs(led['energy_residual_j']), 1e-5 * (led['jacket_heater_j'] + led['sample_j']))
        self.assertLess(abs(led['water_residual_kg']), 1e-12)
        self.assertLess(abs(led['o2_residual_mol']), 1e-12)
        finish(h)
        led = h.ledger()
        self.assertLess(abs(led['energy_residual_j']), 1e-5 * (led['jacket_heater_j'] + led['sample_j']))
        self.assertAlmostEqual(led['water_in_vessel_kg'], 0.0)

    def test25_host_step_size_independence(self):
        res = []
        for dt in (1.0, 7.3):
            h = powered(seed=5)
            mid, _ = start(h)
            finish(h, dt=dt)
            res.append(h.records[mid]['c_value_j_k'])
        self.assertEqual(res[0], res[1])

    def test26_two_instances_are_isolated(self):
        a, b = powered(seed=2, warm=10.0), powered(seed=2, warm=10.0)
        mid, _ = start(a)
        a.step(300.0)
        self.assertIsNone(b.run)
        self.assertEqual(b.vessels[RFID]['state'], 'clean')
        self.assertNotEqual(a.time_s, b.time_s)
        self.assertEqual(b.records, {})

    def test27_agent_truth_isolation_and_roles(self):
        h = powered(warm=10.0)
        h.register_principal('ag', 'agent')
        h.register_principal('ev', 'evaluator')
        ag, ev = h.session('ag'), h.session('ev')
        obs = ag.read(instance_id=h.instance_id)['observations']
        self.assertFalse([k for k in obs if k.startswith('truth.') or k in ('lift.position_m', 'stirrer.angle_rad')])
        self.assertIn('truth.water_k', ev.read(instance_id=h.instance_id)['observations'])
        with self.assertRaises(rt.ActionRejected):
            ag.read(instance_id=h.instance_id, state_ids=['truth.c_total_j_k'])
        for cap, args in (('vessel.prepare', rt.PREPARE_EXAMPLE), ('sim.step', dict(dt_s=1.0)), ('ledger.read', {}), ('plant.configure', rt.PLANT_EXAMPLE)):
            r = ag.invoke(instance_id=h.instance_id, capability_id=cap, arguments=args, request_id='x-' + cap)
            self.assertEqual((r['status'], r['error']['code']), ('rejected', 'ACCESS_DENIED'), cap)
        d = ag.describe(instance_id=h.instance_id)
        self.assertNotIn('ledger.read', d['capabilities'])
        self.assertNotIn('truth.water_k', d['state_slots'])

    def test28_request_binding_and_foreign_agents(self):
        h = powered(warm=10.0)
        for p in ('a1', 'a2'):
            h.register_principal(p, 'agent')
        a1, a2 = h.session('a1'), h.session('a2')
        args = dict(name='x', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=THREAD)
        r1 = a1.invoke(instance_id=h.instance_id, capability_id='measurement.create', arguments=args, request_id='r1')
        self.assertEqual(a1.invoke(instance_id=h.instance_id, capability_id='measurement.create', arguments=args, request_id='r1'), r1)
        r = a1.invoke(instance_id=h.instance_id, capability_id='measurement.create', arguments=dict(args, name='y'), request_id='r1')
        self.assertEqual(r['error']['code'], 'REQUEST_CONFLICT')
        mid = r1['result']['measurement_id']
        h.invoke('vessel.prepare', **rt.PREPARE_EXAMPLE)
        st = a1.invoke(instance_id=h.instance_id, capability_id='measurement.start', arguments=dict(measurement_id=mid), request_id='s1')
        self.assertEqual(st['status'], 'accepted')
        for cap, a in (('measurement.confirm', {}), ('cover.close', {}), ('measurement.result', dict(measurement_id=mid)), ('vessel.scan', dict(rfid=RFID))):
            r = a2.invoke(instance_id=h.instance_id, capability_id=cap, arguments=a, request_id='f-' + cap)
            self.assertEqual(r['error']['code'], 'ACCESS_DENIED', cap)
        with self.assertRaises(rt.ActionRejected):
            a2.cancel(st['operation_id'], 'c2')
        self.assertTrue(a2.read(instance_id=h.instance_id)['observations']['measurement.started_by_other_agent']['value'])
        self.assertIsNone(a2.read(instance_id=h.instance_id)['observations']['measurement.id']['value'])
        lst = a2.invoke(instance_id=h.instance_id, capability_id='archive.list', arguments={}, request_id='l2')['result']['measurements']
        self.assertEqual(lst, [])
        self.assertEqual(a1.poll(st['operation_id'])['status'], 'running')

    def test29_return_values_validate_against_declared_schemas(self):
        h = powered()
        meta = rt.export_ika_c6000_contract_metadata()['capabilities']
        ids, st = calibrate(h, 2)
        mid, _ = start(h, sub='urea', m=1.5e-3, kind='measurement')
        finish(h)
        h.invoke('vessel.remove')
        outs = dict(stats=('vessel.statistics', dict(vessel_name='V1', mode=h.mode)), res=('measurement.result', dict(measurement_id=mid)),
                    tit=('washings.titrate', dict(rfid=RFID)), insp=('vessel.inspect', dict(rfid=RFID)), arch=('archive.list', {}), ev=('events.read', dict(since_s=0.0)),
                    led=('ledger.read', {}), prov=('provenance.read', {}), sim=('measurement.simulate', dict(measurement_id=mid, delta_t_k=3.0, ignition_energy_j=30.0,
                                                                                                                  c_value_j_k=None, reference_value_j_kg=None)),
                    evl=('measurement.evaluate', dict(measurement_id=mid, sulfur_mass_fraction=0.0, total_acid_mol=0.002, hydrogen_mass_fraction=0.067,
                                                      moisture_mass_fraction=0.0)), mnt=('maintenance.run', dict(program='flow_outer')))
        for key, (cap, args) in outs.items():
            val = h.invoke(cap, **args)
            self.assertEqual(asset_tool.validate_value(val, meta[cap]['output_schema']), [], cap)
        values = h.read('evaluator')
        slots = rt.export_ika_c6000_contract_metadata()['state_slots']
        for k, v in values.items():
            self.assertEqual(asset_tool.validate_value(v, slots[k]['value_schema']), [], k)

    def test30_unsupported_capabilities_return_unsupported(self):
        h = powered(warm=10.0)
        h.register_principal('ag', 'agent')
        before = snapshot(h)
        for k in rt.UNSUPPORTED:
            r = h.session('ag').invoke(instance_id=h.instance_id, capability_id=k, arguments={}, request_id='u-' + k)
            self.assertEqual((r['status'], r['error']['code']), ('unsupported', 'UNSUPPORTED'))
        self.assertIn('mode.adiabatic', rt.UNSUPPORTED)
        self.assertEqual(snapshot(h), before)

    def test31_lift_and_stirrer_mirrors(self):
        h = powered(warm=10.0)
        h.invoke('cover.close')
        h.step(5.0)
        self.assertAlmostEqual(h.lift, rt.LIFT_TRAVEL_M - 5 * rt.LIFT_SPEED_M_S, places=9)
        h.step(20.0)
        self.assertEqual(h.read()['cover.state'], 'closed')
        h.invoke('maintenance.run', program='fill_water')
        h.step(60.0)
        out = h.invoke('maintenance.run', program='stirrer')
        self.assertTrue(out['running'] and out['stirrer_rpm'] == rt.STIRRER_RPM and out['flow_m3_s'] is None)
        a0 = h.stir_angle
        h.step(1.0)
        self.assertAlmostEqual((h.stir_angle - a0) % (2 * math.pi), 2 * math.pi * rt.STIRRER_RPM / 60.0 % (2 * math.pi), places=9)
        self.assertTrue(0.0 <= h.stir_angle < rt.STIRRER_JOINT_MAX_RAD)
        h.invoke('maintenance.run', program='stirrer')
        h.invoke('maintenance.run', program='empty_water')
        h.step(60.0)
        self.assertEqual(h.water_kg, 0.0)

    def test32_readings_resolution_and_power_off(self):
        h = powered(warm=10.0)
        t1 = h.read()['inner.indicated_k']
        self.assertAlmostEqual(t1 / rt.RESOLUTION_K, round(t1 / rt.RESOLUTION_K), places=6)
        h.invoke('power.set', enabled=False)
        h.step(2.0)
        self.assertIsNone(h.read()['inner.indicated_k'])

    def test33_vessel_registry_limits_and_pressure_test_warning(self):
        h = powered(warm=10.0)
        for i in range(2, 5):
            h.invoke('vessel.provide', rfid=f'X{i}', model='C 6010')
            h.invoke('vessel.scan', rfid=f'X{i}')
            h.invoke('vessel.register', name=f'V{i}', serial_number=f'00000000{i:02d}', previous_ignitions=0, platinum_wire=False)
        h.invoke('vessel.provide', rfid='X5', model='C 6010')
        h.invoke('vessel.scan', rfid='X5')
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('vessel.register', name='V5', serial_number='0000000005', previous_ignitions=0, platinum_wire=False)
        self.assertEqual(str(cm.exception), 'TOO_MANY_VESSELS')
        h2 = rt.IkaC6000Runtime()
        h2.invoke('plant.configure', **rt.PLANT_EXAMPLE)
        h2.invoke('power.set', enabled=True)
        h2.step(400.0)
        h2.invoke('mode.set', mode='isoperibol_25')
        h2.step(rt.MODE_TEST_S + 1.0)
        h2.invoke('settings.configure', o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=26434.0e3)
        h2.invoke('vessel.scan', rfid=RFID)
        h2.invoke('vessel.register', name='V1', serial_number='0012345678', previous_ignitions=999, platinum_wire=False)
        h2.step(3600.0)
        mid, _ = start(h2)
        finish(h2)
        self.assertIn('PRESSURE_TEST_DUE', [e['code'] for e in h2.events])
        self.assertIn('pressure_test_due', h2.records[mid]['quality_flags'])

    def test34_create_and_confirm_preconditions(self):
        h = powered(warm=10.0)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('measurement.create', name='a', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=[dict(aid_name='IKA cotton wool thread', mass_kg=1e-4)])
        self.assertEqual(str(cm.exception), 'AID_MASS_MISMATCH')
        mid = h.invoke('measurement.create', name='a', sample_mass_kg=1e-3, kind='measurement', vessel_name=None, aids=THREAD)['measurement_id']
        h.invoke('vessel.prepare', **rt.PREPARE_EXAMPLE)
        h.invoke('measurement.start', measurement_id=mid)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('measurement.confirm')
        self.assertEqual(str(cm.exception), 'VESSEL_NOT_SCANNED')
        h.invoke('vessel.scan', rfid=RFID)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('measurement.confirm')
        self.assertEqual(str(cm.exception), 'C_VALUE_REQUIRED')     # a measurement needs the vessel's C-value in this mode

    def test35_prepare_rules(self):
        h = powered(warm=10.0)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('vessel.prepare', rfid=RFID, sample=dict(substance_id='chloroform', mass_kg=1e-3, form='liquid'), aids=[], absorption_water_kg=0.0)
        self.assertEqual(str(cm.exception), 'UNKNOWN_SUBSTANCE')    # halogen samples are not modelled
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('vessel.prepare', rfid=RFID, sample=dict(substance_id='ethanol', mass_kg=1e-3, form='pellet'), aids=[], absorption_water_kg=0.0)
        self.assertEqual(str(cm.exception), 'FORM_NOT_POSSIBLE')
        h.invoke('vessel.prepare', **rt.PREPARE_EXAMPLE)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('vessel.prepare', **rt.PREPARE_EXAMPLE)
        self.assertEqual(str(cm.exception), 'VESSEL_NOT_CLEAN')

    def test37_key_toggle_cover_open_sim_step_and_manual_c_value(self):
        h = powered(warm=10.0)
        self.assertEqual(h.invoke('power.toggle'), dict(enabled=False))
        self.assertEqual(h.state, 'off')
        self.assertEqual(h.invoke('power.toggle'), dict(enabled=True))
        t = h.time_s
        self.assertEqual(h.invoke('sim.step', dt_s=400.0)['time_s'], t + 400.0)
        h.invoke('cover.close')
        h.invoke('sim.step', dt_s=30.0)
        self.assertEqual(h.read()['cover.state'], 'closed')
        h.invoke('cover.open')
        h.invoke('sim.step', dt_s=30.0)
        self.assertEqual(h.read()['cover.state'], 'open')
        h.invoke('vessel.set_c_value', vessel_name='V1', mode=h.mode, c_value_j_k=8650.0)
        h.step(3600.0)
        mid, _ = start(h, sub='naphthalene', m=0.65e-3, kind='measurement')
        finish(h)
        rec = h.records[mid]
        self.assertEqual((rec['status'], rec['c_value_j_k']), ('completed', 8650.0))
        self.assertIn('manual_c_value', rec['quality_flags'])

    def test36_benzoic_condition_factor(self):
        self.assertAlmostEqual(rt.benzoic_condition_factor(3.0e6, 0.3e-3, 0.3e-3, 0.1e-3, 298.15), 1.0, places=12)
        self.assertAlmostEqual(rt.benzoic_condition_factor(3.1e6, 1.0e-3, 0.0, 0.22e-3, 298.15),
                               1 + 1e-6 * (200 * 0.1 + 42 * (1 / 0.22 - 3) + 30 * (0 - 3)), places=12)


    # ------------------------------------------------------------ independent runtime review round 1 (F01-F17)
    def test38_power_loss_after_ignition_keeps_the_vessel_pressurized(self):
        h = powered()
        mid, op = start(h)
        while h.run['phase'] != 'main_trial':
            h.step(1.0)
        h.invoke('power.set', enabled=False)
        v = h.vessels[RFID]
        self.assertEqual(v['state'], 'burned')
        self.assertGreater(h._vessel_pressure(v), 2.0e6)
        h.invoke('power.set', enabled=True)
        h.step(rt.SOFTWARE_LOAD_S + rt.SYSTEM_TEST_S + 60.0)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('vessel.remove')
        self.assertEqual(str(cm.exception), 'VESSEL_PRESSURIZED')
        h.invoke('maintenance.run', program='o2_degas')
        self.assertEqual(h.vessels[RFID]['state'], 'burned_degassed')
        h.invoke('vessel.remove')
        h.invoke('vessel.inspect', rfid=RFID)
        h.invoke('washings.titrate', rfid=RFID)
        h.invoke('vessel.clean', rfid=RFID)
        led = h.ledger()
        self.assertLess(abs(led['o2_residual_mol']), 1e-12)
        self.assertLess(abs(led['energy_residual_j']), 1e-6 * (led['jacket_heater_j'] + led['sample_j']))

    def test39_thread_and_aids_need_oxygen_too(self):
        h = powered()
        h.invoke('aid.define', list_no=2, name='Paraffin oil', specific_energy_j_kg=rt.AIDS['paraffin_oil'], absolute_energy_j=None)
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, oxygen_supply_pa=1.0e5))          # no oxygen added: only the enclosed air
        mid = h.invoke('measurement.create', name='o2', sample_mass_kg=1.0e-3, kind='calibration', vessel_name=None,
                       aids=THREAD + [dict(aid_name='Paraffin oil', mass_kg=0.5e-3)])['measurement_id']
        h.invoke('vessel.prepare', rfid=RFID, sample=dict(substance_id='benzoic_acid', mass_kg=1.0e-3, form='pellet'),
                 aids=[dict(kind='paraffin_oil', mass_kg=0.5e-3)], absorption_water_kg=0.0)
        h.invoke('measurement.start', measurement_id=mid)
        h.invoke('vessel.scan', rfid=RFID)
        h.invoke('vessel.hang', rfid=RFID)
        h.invoke('measurement.confirm')
        while h.run is not None and h.run['phase'] not in ('main_trial', 'degassing'):
            h.step(1.0)
        v = h.vessels[RFID]
        led = h.ledger()
        o2_available = led['o2_filled_mol']                              # air O2 only (plus nothing added)
        chemical = led['thread_j'] + led['aid_j'] + led['sample_j']
        self.assertLessEqual(chemical, o2_available * rt.Q_PER_MOL_O2 * 1.001)
        self.assertLess(chemical, 1000.0)                                # far below the 23 kJ label value of the aid alone
        self.assertLessEqual(v['gas']['CO2'], led['o2_consumed_mol'] + 1e-15)
        self.assertGreaterEqual(v['gas']['O2'], -1e-15)
        finish(h)
        self.assertLess(abs(h.ledger()['o2_residual_mol']), 1e-12)

    def test40_acid_corrected_calibrations_make_evaluation_consistent(self):
        h = powered()
        ids, acids = [], []
        for _ in range(3):
            mid, _ = start(h)
            finish(h)
            h.invoke('vessel.remove')
            acids.append(h.invoke('washings.titrate', rfid=RFID)['total_acid_mol'])
            h.invoke('vessel.clean', rfid=RFID)
            ids.append(mid)
        st0 = h.invoke('vessel.select_calibrations', vessel_name='V1', mode=h.mode, measurement_ids=ids)
        for mid, acid in zip(ids, acids):
            ev = h.invoke('measurement.evaluate', measurement_id=mid, sulfur_mass_fraction=0.0, total_acid_mol=acid, hydrogen_mass_fraction=None, moisture_mass_fraction=0.0)
            rec = h.records[mid]
            self.assertEqual(ev['kind'], 'calibration')
            self.assertAlmostEqual(ev['c_value_j_k'], ev['c_value_uncorrected_j_k'] + acid * rt.HNO3_J_MOL / rec['delta_t_k'], delta=1e-9)
            self.assertEqual((rec['c_value_j_k'], rec['acid_correction_j']), (ev['c_value_j_k'], acid * rt.HNO3_J_MOL))
        st = h.invoke('vessel.statistics', vessel_name='V1', mode=h.mode)
        self.assertAlmostEqual(st['c_value_j_k'], statistics.mean(h.records[m]['c_value_j_k'] for m in ids), delta=1e-9)
        self.assertGreater(st['c_value_j_k'], st0['c_value_j_k'])       # the acid heat of the calibrations is no longer lost
        mid, _ = start(h, kind='measurement')                            # benzoic acid measured as a sample
        finish(h)
        h.invoke('vessel.remove')
        tit = h.invoke('washings.titrate', rfid=RFID)
        ev = h.invoke('measurement.evaluate', measurement_id=mid, sulfur_mass_fraction=0.0, total_acid_mol=tit['total_acid_mol'], hydrogen_mass_fraction=None,
                      moisture_mass_fraction=0.0)
        self.assertLess(abs(ev['gross_calorific_value_j_kg'] / 26434.0e3 - 1.0), 0.0005)   # within the documented 0.05 % RSD
        res = h.invoke('measurement.result', measurement_id=ids[0])      # workbook output "total heat": the measured C dT
        self.assertAlmostEqual(res['total_heat_j'], res['c_value_j_k'] * res['delta_t_k'], delta=1e-9)
        rec0 = h.records[ids[0]]
        self.assertAlmostEqual(res['total_heat_j'], rec0['reference_value_j_kg'] * rec0['sample_mass_kg'] + rec0['q_ext_j'] + rec0['acid_correction_j'], delta=1e-6)

    def test41_no_truth_or_foreign_results_in_events_and_slots(self):
        h = powered()
        calibrate(h, 2)
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        a1, a2 = h.session('a1'), h.session('a2')
        mid = a1.invoke(instance_id=h.instance_id, capability_id='measurement.create', request_id='c1',
                        arguments=dict(name='pe', sample_mass_kg=0.9e-3, kind='measurement', vessel_name=None, aids=THREAD))['result']['measurement_id']
        h.invoke('vessel.prepare', rfid=RFID, sample=dict(substance_id='polymer_pe', mass_kg=0.9e-3, form='pellet'), aids=[], absorption_water_kg=0.0)
        a1.invoke(instance_id=h.instance_id, capability_id='measurement.start', arguments=dict(measurement_id=mid), request_id='s1')
        a1.invoke(instance_id=h.instance_id, capability_id='vessel.scan', arguments=dict(rfid=RFID), request_id='sc1')
        a1.invoke(instance_id=h.instance_id, capability_id='vessel.hang', arguments=dict(rfid=RFID), request_id='h1')
        a1.invoke(instance_id=h.instance_id, capability_id='measurement.confirm', arguments={}, request_id='cf1')
        finish(h)
        truth = h.vessels[RFID]['last_heat_j']
        self.assertGreater(truth, rt.MAX_ENERGY_J)
        msgs = [e['message_zh'] for e in h.invoke('events.read', since_s=0.0)['events']]
        self.assertFalse([m for m in msgs if f'{truth:.0f}' in m])       # the true heat never reaches the log
        warn = [e for e in h.events if e['code'] == 'ENERGY_ABOVE_40KJ']
        self.assertEqual(len(warn), 1)
        rec = h.records[mid]
        self.assertIn(f"{rec['c_value_j_k'] * rec['delta_t_k']:.0f}", warn[0]['message_zh'])   # the measured energy
        r2 = a2.read(instance_id=h.instance_id)['observations']
        self.assertIsNone(r2['result.gross_calorific_value_j_kg']['value'])
        self.assertIsNone(r2['result.delta_t_k']['value'])
        self.assertIsNotNone(a1.read(instance_id=h.instance_id)['observations']['result.gross_calorific_value_j_kg']['value'])
        ev2 = a2.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e2')['result']['events']
        ev1 = a1.invoke(instance_id=h.instance_id, capability_id='events.read', arguments=dict(since_s=0.0), request_id='e1')['result']['events']
        own = {(e['time_s'], e['code']) for e in h.events if e['_mid'] == mid}
        self.assertTrue(own and own <= {(e['time_s'], e['code']) for e in ev1})
        self.assertFalse(own & {(e['time_s'], e['code']) for e in ev2})
        self.assertFalse([e for e in ev2 if mid in e['message_zh'] or e['code'] == 'ENERGY_ABOVE_40KJ'])
        self.assertFalse([e for e in ev1 + ev2 if '_mid' in e])

    def test42_rejected_confirm_and_restart_bind_the_scanned_vessel(self):
        h = powered()
        h.invoke('vessel.provide', rfid='X2', model='C 6010')
        h.invoke('vessel.scan', rfid='X2')
        h.invoke('vessel.register', name='V2', serial_number='0000000002', previous_ignitions=0, platinum_wire=False)
        h.invoke('vessel.set_c_value', vessel_name='V2', mode=h.mode, c_value_j_k=8600.0)
        mid = h.invoke('measurement.create', name='m', sample_mass_kg=0.65e-3, kind='measurement', vessel_name=None, aids=THREAD)['measurement_id']
        h.invoke('vessel.prepare', rfid='X2', sample=dict(substance_id='naphthalene', mass_kg=0.65e-3, form='pellet'), aids=[], absorption_water_kg=0.0)
        h.invoke('measurement.start', measurement_id=mid)
        h.invoke('vessel.scan', rfid=RFID)                                # V1 has no C-value in this mode
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('measurement.confirm')
        self.assertEqual(str(cm.exception), 'C_VALUE_REQUIRED')
        self.assertIsNone(h.run['vessel_name'])                            # nothing bound by the rejected confirm
        h.invoke('vessel.scan', rfid='X2')
        h.invoke('vessel.hang', rfid='X2')
        h.invoke('measurement.confirm')
        self.assertEqual(h.records[mid]['vessel_name'], 'V2')
        finish(h)
        h.invoke('vessel.remove')
        h.invoke('vessel.clean', rfid='X2')
        # automatic detection: cancel before ignition, then restart with another vessel
        cal = h.invoke('measurement.create', name='c', sample_mass_kg=1.0e-3, kind='calibration', vessel_name=None, aids=THREAD)['measurement_id']
        h.invoke('vessel.prepare', **rt.PREPARE_EXAMPLE)
        op = h.invoke('measurement.start', measurement_id=cal)['operation_id']
        h.invoke('vessel.scan', rfid=RFID)
        h.invoke('vessel.hang', rfid=RFID)
        h.invoke('measurement.confirm')
        h.step(30.0)
        h._cancel_run(h.operations[op])
        finish(h)
        self.assertEqual((h.records[cal]['status'], h.records[cal]['vessel_name']), ('cancelled_before_ignition', None))
        h.invoke('vessel.remove')
        h.invoke('vessel.prepare', rfid='X2', sample=dict(substance_id='benzoic_acid', mass_kg=1.0e-3, form='pellet'), aids=[], absorption_water_kg=0.0)
        h.invoke('measurement.start', measurement_id=cal)
        self.assertIsNone(h.run['vessel_name'])                            # waits for a new scan
        h.invoke('vessel.scan', rfid='X2')
        h.invoke('vessel.hang', rfid='X2')
        h.invoke('measurement.confirm')
        finish(h)
        self.assertEqual((h.records[cal]['status'], h.records[cal]['vessel_name']), ('completed', 'V2'))
        self.assertEqual((h.registry['V1']['ignitions'], h.registry['V2']['ignitions']), (0, 2))

    def test43_records_can_be_deleted(self):
        h = powered()
        ids, _ = calibrate(h, 2)
        extra = h.invoke('measurement.create', name='x', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=THREAD)['measurement_id']
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('measurement.delete', measurement_ids=[ids[0]])
        self.assertEqual(str(cm.exception), 'RECORD_IN_USE')
        self.assertEqual(h.invoke('measurement.delete', measurement_ids=[extra]), dict(deleted=[extra]))
        self.assertNotIn(extra, h.records)
        mid, _ = start(h)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('measurement.delete', measurement_ids=[mid])
        self.assertEqual(str(cm.exception), 'MEASUREMENT_RUNNING')
        finish(h)
        take_out(h)
        h.register_principal('a1', 'agent')
        h.register_principal('a2', 'agent')
        own = h.session('a1').invoke(instance_id=h.instance_id, capability_id='measurement.create', request_id='c',
                                     arguments=dict(name='o', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=THREAD))['result']['measurement_id']
        r = h.session('a2').invoke(instance_id=h.instance_id, capability_id='measurement.delete', arguments=dict(measurement_ids=[own]), request_id='d2')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = h.session('a1').invoke(instance_id=h.instance_id, capability_id='measurement.delete', arguments=dict(measurement_ids=[own]), request_id='d1')
        self.assertEqual(r['status'], 'succeeded')

    def test44_immersion_is_latched_on_the_instrument_cycle(self):
        res = []
        for dt in (1.0, 0.3, 0.7):
            h = powered(seed=5)
            h.invoke('maintenance.run', program='fill_water')           # water already in the inner vessel while the cover closes
            h.step(60.0)
            mid, _ = start(h)
            finish(h, dt=dt)
            res.append(h.records[mid]['c_value_j_k'])
        self.assertEqual(res[0], res[1])
        self.assertEqual(res[0], res[2])

    def test45_cancel_refused_during_the_end_sequence(self):
        h = powered()
        h.register_principal('ag', 'agent')
        s = h.session('ag')
        for sub, final, code in (('benzoic_acid', 'completed', None), (None, 'failed', 'E04')):
            mid, op = start(h, sub=sub)
            h._owners[op] = 'ag'
            while h.run['phase'] != 'degassing':
                h.step(1.0)
            with self.assertRaises(rt.ActionRejected) as cm:
                s.cancel(op, 'c-' + str(sub))
            self.assertEqual(str(cm.exception), 'NOT_CANCELLABLE')
            finish(h)
            self.assertEqual((h.records[mid]['status'], h.records[mid]['error_code']), (final, code))
            self.assertEqual(h.error, code)
            take_out(h)

    def test46_power_loss_restores_saved_records_and_rebuilds_c_values(self):
        h = powered()
        ids, st = calibrate(h, 2)
        waiting = h.invoke('measurement.create', name='w', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=THREAD)['measurement_id']
        h.invoke('system.shutdown')
        h.step(20.0)
        h.invoke('power.set', enabled=False)
        h.invoke('power.set', enabled=True)
        h.step(rt.SOFTWARE_LOAD_S + rt.SYSTEM_TEST_S + 60.0)
        c_saved = h.registry['V1']['c'][h.mode]
        self.assertEqual(c_saved, st['c_value_j_k'])
        new, _ = start(h)                                                # a new, unsaved calibration
        finish(h)
        take_out(h)
        h.invoke('vessel.select_calibrations', vessel_name='V1', mode=h.mode, measurement_ids=[ids[1], new])
        h.invoke('measurement.delete', measurement_ids=[ids[0]])        # a saved record deleted after the shutdown
        h.invoke('vessel.prepare', **rt.PREPARE_EXAMPLE)                 # the saved, never-run record is run now
        h.invoke('measurement.start', measurement_id=waiting)
        h.invoke('vessel.scan', rfid=RFID)
        h.invoke('vessel.hang', rfid=RFID)
        h.invoke('measurement.confirm')
        finish(h)
        self.assertEqual(h.records[waiting]['status'], 'completed')
        spare = h.invoke('measurement.create', name='s', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=THREAD)['measurement_id']
        h.invoke('power.set', enabled=False)                             # no shutdown: changes since the last save are lost
        self.assertNotIn(new, h.records)
        self.assertNotIn(spare, h.records)
        self.assertEqual(h.records[waiting]['status'], 'created')        # back to the saved version
        self.assertEqual(h.registry['V1']['calibrations'][h.mode], ids)  # the saved selection and C-value
        self.assertEqual(h.registry['V1']['c'][h.mode], c_saved)

    def test47_documented_27C_cooling_water_is_not_red(self):
        self.assertEqual(rt.quantize(300.15, 0.01), 300.15)
        h = rt.IkaC6000Runtime()
        h.invoke('plant.configure', **dict(rt.PLANT_EXAMPLE, cooling_water_k=300.15, noise_seed=3))
        h.invoke('power.set', enabled=True)
        h.step(rt.SOFTWARE_LOAD_S + rt.SYSTEM_TEST_S + 1.0)
        self.assertEqual((h.test_result, h.error), ('yellow', None))    # the 30 C modes are offered, the preset 22 C is not
        self.assertIn('isoperibol_30', h.read()['mode.available'])

    def test48_absorption_water_adds_heat_capacity(self):
        cs = {}
        for water in (0.0, 10e-3):
            h = powered(warm=10.0)
            h.invoke('vessel.prepare', rfid=RFID, sample=dict(substance_id='benzoic_acid', mass_kg=1e-3, form='pellet'), aids=[], absorption_water_kg=water)
            h.invoke('vessel.hang', rfid=RFID)
            cs[water] = h.read('evaluator')['truth.c_total_j_k']
            h.invoke('vessel.remove')
            h.invoke('vessel.clean', rfid=RFID)
            led = h.ledger()
            self.assertLess(abs(led['energy_residual_j']), 1e-6 * max(1.0, led['jacket_heater_j']))
        self.assertAlmostEqual(cs[10e-3] - cs[0.0], 10e-3 * rt.CP_WATER, places=9)

    def test49_result_slots_describe_the_last_run(self):
        h = powered()
        calibrate(h, 1)
        v = h.read()
        self.assertIsNotNone(v['result.c_value_j_k'])
        self.assertIsNone(v['result.gross_calorific_value_j_kg'])
        start(h, kind='measurement')
        finish(h)
        take_out(h)
        v = h.read()
        self.assertIsNone(v['result.c_value_j_k'])
        self.assertIsNotNone(v['result.gross_calorific_value_j_kg'])
        start(h)
        finish(h)
        v = h.read()
        self.assertIsNone(v['result.gross_calorific_value_j_kg'])      # after a calibration no stale Ho

    def test50_metadata_matches_behaviour(self):
        meta = rt.export_ika_c6000_contract_metadata()
        caps, slots = meta['capabilities'], meta['state_slots']
        step_effects = {e['state_id'] for e in caps['sim.step']['state_effects']}
        self.assertTrue({'truth.water_k', 'lift.position_m', 'result.delta_t_k', 'measurement.phase', 'inner.indicated_k'} <= step_effects)
        props = caps['aid.define']['input_schema']['properties']
        self.assertEqual((props['specific_energy_j_kg']['anyOf'][0]['x-unit'] if 'anyOf' in props['specific_energy_j_kg'] else props['specific_energy_j_kg']['x-unit']), 'J/kg')
        out = caps['maintenance.run']['output_schema']['properties']
        self.assertNotIn('reading', out)
        self.assertEqual(caps['vessel.register']['input_schema']['properties']['previous_ignitions']['type'], 'integer')
        self.assertNotIn('E30', slots['system.error_code']['meaning_zh'])
        self.assertNotIn('E26', slots['system.error_code']['meaning_zh'])
        self.assertIn('SYSTEM_TEST_RED', slots['system.error_code']['meaning_zh'])
        h = powered(warm=10.0)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('vessel.register', name='V9', serial_number='0000000009', previous_ignitions=999.7, platinum_wire=False)
        self.assertEqual(str(cm.exception), 'INVALID_ARGUMENT')
        h.register_principal('ag', 'agent')
        d = h.session('ag').describe(instance_id=h.instance_id)
        visible = set(d['state_slots'])
        for k, c in d['capabilities'].items():
            refs = {e['state_id'] for e in c['state_effects']} | set(c['observes']) | set(c['execution']['completion_state_ids'])
            for pre in c['preconditions']:
                refs |= set(pre['state_ids'])
            self.assertTrue(refs <= visible, (k, refs - visible))

    def test51_manual_operating_details(self):
        h = rt.IkaC6000Runtime()
        self.assertEqual(h.settings['reference_value_j_kg'], 26461.0e3)   # manual factory setting
        h = powered()
        h.invoke('mode.set', mode='isoperibol_25')                         # manual 11.4: runs a system test
        with self.assertRaises(rt.ActionRejected) as cm:                  # manual 10.3: shutdown only from Wait
            h.invoke('system.shutdown')
        self.assertEqual(str(cm.exception), 'NOT_READY')
        h.step(rt.MODE_TEST_S + 1.0)
        h.invoke('settings.configure', o2_flush=True, auto_vessel_detection=True, reference_value_j_kg=26434.0e3)
        vented0 = h.ledger()['o2_vented_mol']
        mid, _ = start(h)
        while h.run['phase'] != 'equalising':
            h.step(1.0)
        V = rt.VESSEL_VOLUME_M3['C 6012']
        R = rt.GAS_CONSTANT * rt.T_REF
        n_air, added = rt.P_ATM * V / R, (h.plant['oxygen_supply_pa'] - rt.P_ATM) * V / R
        filled, keep = added * h.plant['oxygen_purity'], rt.P_ATM / h.plant['oxygen_supply_pa']
        o2, vent = 0.2095 * n_air + filled, 0.0
        for _ in range(2):                                                # manual 11.4.2: emptied twice before the actual filling
            vent += o2 * (1.0 - keep)
            o2 = o2 * keep + filled
        self.assertAlmostEqual(h.ledger()['o2_vented_mol'] - vented0, vent, delta=1e-12)
        self.assertAlmostEqual(h.vessels[RFID]['gas']['O2'], o2, delta=1e-12)
        finish(h)

    def test52_simulation_defaults_reproduce_the_record(self):
        h = powered()
        ids, _ = calibrate(h, 2)
        mid, _ = start(h, sub='naphthalene', m=0.65e-3, kind='measurement')
        finish(h)
        take_out(h)
        rec = h.records[mid]
        h.invoke('vessel.set_c_value', vessel_name='V1', mode=h.mode, c_value_j_k=9000.0)   # today's C-value differs
        out = h.invoke('measurement.simulate', measurement_id=mid, delta_t_k=rec['delta_t_k'], ignition_energy_j=rec['ignition_energy_j'], c_value_j_k=None,
                       reference_value_j_kg=None)
        self.assertEqual(out['gross_calorific_value_j_kg'], rec['gross_calorific_value_j_kg'])
        cal = h.records[ids[0]]
        h.invoke('settings.configure', o2_flush=False, auto_vessel_detection=True, reference_value_j_kg=26461.0e3)
        out = h.invoke('measurement.simulate', measurement_id=ids[0], delta_t_k=cal['delta_t_k'], ignition_energy_j=cal['ignition_energy_j'], c_value_j_k=None,
                       reference_value_j_kg=None)
        self.assertAlmostEqual(out['c_value_j_k'], cal['c_value_j_k'], delta=1e-9)


    # ------------------------------------------------------------ independent runtime review round 2 (F18-F21)
    def test53_record_changes_need_the_owner_and_no_foreign_run(self):
        h = powered()
        ids, _ = calibrate(h, 2)                                         # host records (no owner)
        for p in ('a1', 'a2'):
            h.register_principal(p, 'agent')
        a1, a2 = h.session('a1'), h.session('a2')
        r = a1.invoke(instance_id=h.instance_id, capability_id='measurement.evaluate', request_id='e0',
                      arguments=dict(measurement_id=ids[0], sulfur_mass_fraction=0.0, total_acid_mol=0.05, hydrogen_mass_fraction=None, moisture_mass_fraction=0.0))
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')             # not the agent's calibration
        r = a1.invoke(instance_id=h.instance_id, capability_id='measurement.delete', arguments=dict(measurement_ids=[ids[0]]), request_id='d0')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        own = []
        for k in range(2):                                              # a1's own calibrations, selected for V1
            mid = a1.invoke(instance_id=h.instance_id, capability_id='measurement.create', request_id=f'c{k}',
                            arguments=dict(name=f'cal{k}', sample_mass_kg=1e-3, kind='calibration', vessel_name=None, aids=THREAD))['result']['measurement_id']
            h.invoke('vessel.prepare', **rt.PREPARE_EXAMPLE)
            a1.invoke(instance_id=h.instance_id, capability_id='measurement.start', arguments=dict(measurement_id=mid), request_id=f's{k}')
            a1.invoke(instance_id=h.instance_id, capability_id='vessel.scan', arguments=dict(rfid=RFID), request_id=f'sc{k}')
            a1.invoke(instance_id=h.instance_id, capability_id='vessel.hang', arguments=dict(rfid=RFID), request_id=f'h{k}')
            a1.invoke(instance_id=h.instance_id, capability_id='measurement.confirm', arguments={}, request_id=f'cf{k}')
            finish(h)
            take_out(h)
            own.append(mid)
        a1.invoke(instance_id=h.instance_id, capability_id='vessel.select_calibrations', arguments=dict(vessel_name='V1', mode=h.mode, measurement_ids=own), request_id='sel')
        c0 = h.registry['V1']['c'][h.mode]
        mid = a2.invoke(instance_id=h.instance_id, capability_id='measurement.create', request_id='b',
                        arguments=dict(name='b', sample_mass_kg=0.65e-3, kind='measurement', vessel_name=None, aids=THREAD))['result']['measurement_id']
        h.invoke('vessel.prepare', rfid=RFID, sample=dict(substance_id='naphthalene', mass_kg=0.65e-3, form='pellet'), aids=[], absorption_water_kg=0.0)
        a2.invoke(instance_id=h.instance_id, capability_id='measurement.start', arguments=dict(measurement_id=mid), request_id='bs')
        a2.invoke(instance_id=h.instance_id, capability_id='vessel.scan', arguments=dict(rfid=RFID), request_id='bsc')
        a2.invoke(instance_id=h.instance_id, capability_id='vessel.hang', arguments=dict(rfid=RFID), request_id='bh')
        a2.invoke(instance_id=h.instance_id, capability_id='measurement.confirm', arguments={}, request_id='bcf')
        h.step(200.0)
        r = a1.invoke(instance_id=h.instance_id, capability_id='measurement.evaluate', request_id='e1',
                      arguments=dict(measurement_id=own[0], sulfur_mass_fraction=0.0, total_acid_mol=0.05, hydrogen_mass_fraction=None, moisture_mass_fraction=0.0))
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')             # a2's measurement runs with this C-value
        h.invoke('vessel.set_c_value', vessel_name='V1', mode=h.mode, c_value_j_k=10000.0)   # even the host changes it mid-run ...
        finish(h)
        self.assertEqual(h.records[mid]['c_value_j_k'], c0)              # ... the run uses the C-value valid at its confirmation
        ref = rt._lib()['by_id']['naphthalene']['gross_calorific_value_J_g'] * 1000.0
        self.assertLess(abs(h.records[mid]['gross_calorific_value_j_kg'] / ref - 1.0), 0.003)

    def test54_manual_c_value_releases_the_selected_calibrations(self):
        h = powered()
        ids, _ = calibrate(h, 2)
        with self.assertRaises(rt.ActionRejected) as cm:
            h.invoke('measurement.delete', measurement_ids=[ids[0]])
        self.assertEqual(str(cm.exception), 'RECORD_IN_USE')
        h.invoke('vessel.set_c_value', vessel_name='V1', mode=h.mode, c_value_j_k=8650.0)
        st = h.invoke('vessel.statistics', vessel_name='V1', mode=h.mode)
        self.assertEqual((st['c_value_j_k'], st['n']), (8650.0, 0))
        self.assertEqual(h.invoke('measurement.delete', measurement_ids=[ids[0]]), dict(deleted=[ids[0]]))
        meta = rt.export_ika_c6000_contract_metadata()
        self.assertIn('两次', meta['state_slots']['settings.o2_flush']['meaning_zh'])
        self.assertTrue(any('Q_acid' in eq for eq in meta['mechanisms']['c6000_isoperibol_bomb_calorimetry']['equations']))


    def test55_quality_flags_follow_the_c_value_basis_fixed_at_confirmation(self):
        h = powered()
        ids, _ = calibrate(h, 2)
        mid, _ = start(h, sub='naphthalene', m=0.65e-3, kind='measurement')   # C from calibrations at confirmation
        h.step(200.0)
        h.invoke('vessel.set_c_value', vessel_name='V1', mode=h.mode, c_value_j_k=9500.0)   # manual C mid-run
        finish(h)
        take_out(h)
        self.assertNotIn('manual_c_value', h.records[mid]['quality_flags'])
        mid, _ = start(h, sub='benzoic_acid', m=0.3e-3, kind='measurement')   # confirmed with the manual C, no calibration basis
        h.step(200.0)
        h.invoke('vessel.select_calibrations', vessel_name='V1', mode=h.mode, measurement_ids=ids)
        finish(h)
        take_out(h)
        rec = h.records[mid]
        self.assertEqual(rec['c_value_j_k'], 9500.0)
        self.assertIn('manual_c_value', rec['quality_flags'])
        self.assertNotIn('rise_far_from_calibration', rec['quality_flags'])  # no calibrations behind the C-value it used

if __name__ == "__main__":
    unittest.main()

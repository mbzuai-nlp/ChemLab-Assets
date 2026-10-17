"""CHEM-094 Agilent 1260 Infinity III SFC: API, physics, template fidelity, conservation and isolation tests.

python3 -m unittest tests/test_asset_agilent_1260infinity.py   (matterix python: numpy, openpyxl not needed)
"""
import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import agilent_1260infinity as rt  # noqa: E402
from asset_tool import lint_schema, validate_value  # noqa: E402

MIX = {'caffeine': 0.257, 'vanillin': 0.329, 'naringenin': 0.18, 'p_cymene': 0.37}


def snapshot(h):
    """Comparable dump of the runtime state (tuple keys, numpy generators and arrays made JSON-safe)."""
    import numpy as np

    def conv(o):
        if isinstance(o, dict):
            return {repr(k): conv(v) for k, v in o.items()}
        if isinstance(o, (list, tuple)):
            return [conv(v) for v in o]
        if isinstance(o, np.random.Generator):
            return conv(o.bit_generator.state)
        if isinstance(o, np.ndarray):
            return o.tolist()
        return o
    return json.dumps(conv({k: v for k, v in h.__dict__.items() if k != '_requests'}), default=str, sort_keys=True)


def prepared(instance='sfc_a', column='silica', comp=None, method=None, warm=True):
    h = rt.Agilent1260infinityRuntime(instance)
    h.invoke('supply.configure', **rt.SUPPLY_EXAMPLE)
    h.invoke('solvent.configure', **rt.BOTTLE_EXAMPLE)
    h.invoke('mct_door.set', angle_rad=1.2)
    h.invoke('column.install', position=1, column=column)
    h.invoke('mct_door.set', angle_rad=0.0)
    h.invoke('drawer_1.set', position_m=0.25)
    h.invoke('sample.register_vial', drawer=1, position=1, volume_m3=1e-6, composition_mol_m3=dict(comp or MIX))
    h.invoke('drawer_1.set', position_m=0.0)
    h.invoke('power.set', enabled=True)
    h.invoke('method.configure', **copy.deepcopy(method or rt.METHOD_EXAMPLE))
    if warm:
        h.step(600.0)
        h.invoke('pump.set', enabled=True)
        h.step(400.0)
    return h


def run_to_end(h, extra=0.0):
    h.invoke('run.start', drawer=1, position=1)
    h.step(rt.INJECT_CYCLE_S + h.method['run_time_s'] + extra + 0.5)
    return h.invoke('chromatogram.read', max_points=40000)


class SfcRuntimeTests(unittest.TestCase):
    def test_module_identity_and_library(self):
        self.assertEqual(rt.ASSET_TYPE_ID, 'instrument.agilent_1260infinity')
        self.assertEqual(rt.CATALOG_ID, 'CHEM-094')
        lib = rt.library()
        self.assertGreaterEqual(lib['n_with_any_elution'], 50)
        self.assertTrue(all(r['evidence'] == 'measured' and r['source']['licence'] == 'CC BY 4.0' for r in lib['templates']))

    def test_step_requires_positive_dt(self):
        for bad in (0, -1.0, float('nan'), True):
            with self.assertRaises(ValueError):
                rt.Agilent1260infinityRuntime().step(bad)

    def test_setpoints_only_write_targets(self):
        h = prepared(warm=False)
        t, col_k, flow = h.time_s, h.column_k, h.flow
        h.invoke('method.configure', **dict(rt.METHOD_EXAMPLE, column_temperature_k=333.15, bpr_pa=150e5))
        self.assertEqual((h.time_s, h.column_k, h.flow), (t, col_k, flow))
        self.assertEqual(h.read()['column.target_k'], 333.15)
        h.step(600.0)
        h.invoke('pump.set', enabled=True)
        self.assertEqual(h.flow, 0.0)
        h.step(30.0)
        self.assertGreater(h.flow, 0.9 * rt.REF_FLOW_M3_S)

    def test_read_describe_and_queries_are_pure(self):
        h = prepared()
        h.invoke('run.start', drawer=1, position=1)
        h.step(100.0)
        snap = snapshot(h)
        s = h.session()
        for _ in range(2):
            s.read(instance_id=h.instance_id)
            s.describe(instance_id=h.instance_id)
            h.invoke('chromatogram.read', max_points=100)
            h.invoke('template.read', substance_id='caffeine')
        self.assertEqual(snap, snapshot(h))

    def test_rejected_preconditions_are_atomic(self):
        h = prepared()
        cases = [('run.start', dict(drawer=2, position=1)),                       # no vial there
                 ('column.install', dict(position=2, column='diol')),             # pump running, door closed
                 ('method.configure', dict(rt.METHOD_EXAMPLE, injection_volume_m3=2.05e-9)),   # not a 0.1 uL multiple
                 ('method.configure', dict(rt.METHOD_EXAMPLE, program=[dict(time_s=10.0, modifier_fraction=0.1)])),
                 ('supply.configure', dict(rt.SUPPLY_EXAMPLE, supply_pa=80e5)),
                 ('sample.register_vial', dict(rt.VIAL_EXAMPLE, position=2)),     # drawer closed
                 ('flow.set', {}), ('chiral.separate', {})]
        for cap, args in cases:
            before = snapshot(h)
            with self.assertRaises(rt.ActionRejected):
                h.invoke(cap, **args)
            self.assertEqual(before, snapshot(h), cap)

    def test_interlocks(self):
        h = prepared()
        h.invoke('sampler_door.set', angle_rad=0.5)
        with self.assertRaises(rt.ActionRejected):
            h.invoke('run.start', drawer=1, position=1)
        h.invoke('sampler_door.set', angle_rad=0.0)
        h.invoke('run.start', drawer=1, position=1)
        for cap, arg in (('sampler_door.set', 'angle_rad'), ('drawer_1.set', 'position_m'), ('mct_door.set', 'angle_rad')):
            with self.assertRaises(rt.ActionRejected):
                h.invoke(cap, **{arg: 0.2})
        with self.assertRaises(rt.ActionRejected):
            h.invoke('method.configure', **rt.METHOD_EXAMPLE)

    def test_not_ready_rejects_run(self):
        h = prepared(warm=False)
        h.step(600.0)
        h.invoke('pump.set', enabled=True)
        h.step(0.5)
        self.assertFalse(h.read()['system.ready'])
        with self.assertRaises(rt.ActionRejected):
            h.invoke('run.start', drawer=1, position=1)

    def test_reference_method_reproduces_measured_retention(self):
        lib = rt.templates()
        for sid in ('caffeine', 'vanillin', 'naringenin', 'hesperidin', 'ibuprofen'):
            for col in ('silica', 'diol'):
                e = lib[sid]['retention'][col]['methanol']
                if e['status'] != 'eluted':
                    continue
                spec = rt.columns()[col]
                t0 = rt.void_time_s(spec['length_m'], spec['inner_diameter_m'], rt.REF_FLOW_M3_S)
                prog = [(0.0, 0.02), (60.0, 0.02), (300.0, 0.45), (360.0, 0.45), (361.0, 0.02), (450.0, 0.02), (1410.0, 0.02)]
                tr, _, _ = rt.migrate([e['model_fit']['log10_k_at_phi_1']], rt.LOGLOG_SLOPE_M, prog, t0, rt.REF_DWELL_S, 1500.0)
                self.assertAlmostEqual(float(tr[0]), e['t_r_first_s'], delta=0.5, msg=(sid, col))

    def test_chromatogram_peaks_follow_templates_and_beer_lambert(self):
        h = prepared()
        c = run_to_end(h)
        self.assertEqual(c['phase'], 'succeeded')
        self.assertTrue(c['complete'])
        bands = {b['sid']: b for b in h.bands}
        self.assertIsNone(bands['p_cymene']['t_r'])                       # not observed on silica/MeOH in the source data
        order = sorted((b['t_r'], s) for s, b in bands.items() if b['t_r'] is not None)
        lib = rt.templates()
        measured = sorted((lib[s]['retention']['silica']['methanol']['t_r_first_s'], s) for _, s in order)
        self.assertEqual([s for _, s in order], [s for _, s in measured])  # elution order of the measured data is kept
        self.assertEqual(len(c['peaks']), 3)
        caf = min(c['peaks'], key=lambda p: abs(p['retention_time_s'] - bands['caffeine']['t_r']))
        expected = bands['caffeine']['eps'] * rt.FLOW_CELL_PATH_M * bands['caffeine']['mol'] / h.method['flow_m3_s']
        self.assertAlmostEqual(caf['area_au_s'], expected, delta=0.03 * expected)
        self.assertEqual(bands['caffeine']['evidence'], 'measured_nist_uv')

    def test_flow_and_gradient_transfer_is_physical(self):
        fast = dict(rt.METHOD_EXAMPLE, flow_m3_s=2.5e-6 / 60)
        h1, h2 = prepared('a'), prepared('b', method=fast)
        run_to_end(h1)
        run_to_end(h2)
        t1 = {b['sid']: b['t_r'] for b in h1.bands if b['t_r']}
        t2 = {b['sid']: b['t_r'] for b in h2.bands if b['t_r']}
        for sid in t1:
            self.assertLess(t2[sid], t1[sid])                              # higher flow -> earlier elution
        self.assertGreater(h2.pump_pa, h1.pump_pa)                         # and higher pressure drop

    def test_density_correction_and_eos(self):
        rho = rt.co2_density(313.15, 13e6 + 101325)
        self.assertAlmostEqual(rho, 763.0, delta=0.12 * 763.0)             # NIST ~763 kg/m^3; PR is ~8 % low
        self.assertGreater(rt.co2_density(313.15, 30e6), rho)
        h1 = prepared('lo')
        h2 = prepared('hi', method=dict(rt.METHOD_EXAMPLE, bpr_pa=250e5))
        h2.step(60.0)
        run_to_end(h1)
        run_to_end(h2)
        t1 = {b['sid']: b['t_r'] for b in h1.bands if b['t_r']}
        t2 = {b['sid']: b['t_r'] for b in h2.bands if b['t_r']}
        self.assertTrue(all(t2[s] < t1[s] for s in t1))                    # denser CO2 elutes earlier

    def test_overpressure_trips_pump(self):
        hard = dict(rt.METHOD_EXAMPLE, flow_m3_s=rt.FLOW_MAX, bpr_pa=400e5,
                    program=[dict(time_s=0.0, modifier_fraction=0.9)], run_time_s=60.0)
        h = prepared(method=hard, warm=False)
        h.step(600.0)
        h.invoke('pump.set', enabled=True)
        h.step(60.0)
        self.assertEqual(h.read()['system.fault'], 'OVERPRESSURE')
        self.assertFalse(h.pump_enabled)

    def test_co2_supply_exhaustion_fails_run(self):
        h = prepared()
        h.supply['cylinder_co2_kg'] = 0.001
        h.invoke('run.start', drawer=1, position=1)
        h.step(200.0)
        self.assertEqual(h.phase, 'failed')
        self.assertEqual(h.fault, 'CO2_SUPPLY')
        self.assertGreaterEqual(h.supply['cylinder_co2_kg'], 0.0)

    def test_conservation_ledgers(self):
        h = prepared()
        run_to_end(h)
        h.invoke('run.start', drawer=1, position=1)
        h.step(150.0)
        h.invoke('run.abort')
        led = h.invoke('ledger.read')
        self.assertAlmostEqual(led['volume_residual_m3'], 0.0, delta=1e-15)
        self.assertAlmostEqual(led['species_residual_mol'], 0.0, delta=1e-15)
        self.assertAlmostEqual(led['modifier_residual_m3'], 0.0, delta=1e-12)
        self.assertAlmostEqual(led['co2_residual_kg'], 0.0, delta=1e-9)
        self.assertAlmostEqual(led['injected_mol'], led['column_mol'] + led['eluted_mol'], delta=1e-18)
        self.assertAlmostEqual(led['injected_volume_m3'], 4e-9, delta=1e-15)
        self.assertGreater(led['column_mol'], 0.0)                        # cancelled run keeps uneluted material on the column
        h.invoke('drawer_1.set', position_m=0.25)
        back = h.invoke('sample.remove_vial', drawer=1, position=1)
        self.assertAlmostEqual(back['returned_m3'], 1e-6 - 4e-9, delta=1e-15)
        self.assertAlmostEqual(h.invoke('ledger.read')['volume_residual_m3'], 0.0, delta=1e-15)

    def test_cancel_and_power_loss_keep_partial_data(self):
        h = prepared()
        h.invoke('run.start', drawer=1, position=1)
        h.step(120.0)
        n = len(h.run['t'])
        h.invoke('power.toggle', module='dad')
        self.assertEqual(h.phase, 'cancelled')
        self.assertTrue(h.pump_enabled)                                     # the detector's switch does not stop the pump
        c = h.invoke('chromatogram.read', max_points=40000)
        self.assertEqual(len(c['time_s']), n)
        self.assertFalse(c['complete'])
        self.assertEqual(h.read()['status.dad'], 'off')

    def test_quantify_with_external_calibration(self):
        h = prepared(comp={'caffeine': 0.4})
        run_to_end(h)
        area = h.run['peaks'][0]['area_au_s']
        per = area / 0.4
        out = h.invoke('peaks.quantify', calibration=[dict(concentration_mol_m3=0.1, area_au_s=0.1 * per), dict(concentration_mol_m3=0.8, area_au_s=0.8 * per)],
                       retention_window_s=[h.run['peaks'][0]['retention_time_s'] - 5, h.run['peaks'][0]['retention_time_s'] + 5])
        self.assertAlmostEqual(out['concentration_mol_m3'], 0.4, delta=1e-6)
        self.assertEqual(out['quality'], 'ok')
        with self.assertRaises(rt.ActionRejected):
            h.invoke('peaks.quantify', calibration=[dict(concentration_mol_m3=0.1, area_au_s=1.0)] * 2, retention_window_s=[0.0, 10.0])

    def test_empty_modifier_channel_or_column_rejected_while_pumping(self):
        h = prepared()
        before = snapshot(h)
        for bad in (dict(rt.METHOD_EXAMPLE, modifier_channel='B2'), dict(rt.METHOD_EXAMPLE, column_position=3)):
            with self.assertRaises(rt.ActionRejected):
                h.invoke('method.configure', **bad)
            self.assertEqual(before, snapshot(h))
        h.step(5.0)                                                         # no crash, pump still running
        self.assertTrue(h.pump_enabled)
        self.assertEqual(h.fault, '')

    def test_late_eluters_leave_the_column_and_carry_over(self):
        short = dict(rt.METHOD_EXAMPLE, run_time_s=120.0)
        h = prepared(method=short, comp={'vanillin': 0.3, 'caffeine': 0.257})
        c1 = run_to_end(h)
        self.assertEqual(len(c1['peaks']), 1)                               # caffeine (t_R ~186 s) not inside the 120 s window
        self.assertGreater(h.invoke('ledger.read')['column_mol'], 0.0)
        caf = next(b for b in h.bands if b['sid'] == 'caffeine')
        self.assertGreater(caf['t_r'], 135.0)
        h.invoke('run.start', drawer=1, position=1)                         # next injection: caffeine of run 1 elutes as a carry-over peak
        h.step(rt.INJECT_CYCLE_S + 120.5)
        c2 = h.invoke('chromatogram.read', max_points=40000)
        self.assertEqual(len(c2['peaks']), 2, [p['retention_time_s'] for p in c2['peaks']])
        led = h.invoke('ledger.read')
        self.assertAlmostEqual(led['injected_mol'], led['column_mol'] + led['eluted_mol'], delta=1e-18)
        h.step(600.0)                                                       # pumping on: everything with a retention time reaches the waste
        self.assertAlmostEqual(h.invoke('ledger.read')['column_mol'], 0.0, delta=1e-18)

    def test_module_power_is_independent(self):
        h = prepared()
        h.invoke('power.toggle', module='dad')
        self.assertTrue(h.pump_enabled)                                     # detector off does not stop the pump
        self.assertEqual(h.read()['status.dad'], 'off')
        h.invoke('power.toggle', module='dad')
        h.step(1.0)
        h.invoke('run.start', drawer=1, position=1)
        h.step(30.0)
        h.invoke('power.toggle', module='mct')
        self.assertEqual(h.phase, 'cancelled')                              # a run needs every module
        self.assertTrue(h.pump_enabled)
        h.invoke('power.toggle', module='control')
        self.assertFalse(h.pump_enabled)                                    # no CO2 conditioning / BPR -> pump target cleared

    def test_outside_reference_program_records_do_not_elute(self):
        e = rt.templates()['3_hydroxy_cinnamic_acid']['retention']['c18_hss_sb']['methanol']
        self.assertTrue(e.get('outside_reference_program'))
        self.assertNotIn('model_fit', e)
        h = prepared(column='c18_hss_sb', comp={'3_hydroxy_cinnamic_acid': 0.2})
        run_to_end(h)
        self.assertEqual(h.bands[0]['evidence'], 'outside_reference_program')
        self.assertIsNone(h.bands[0]['t_r'])

    def test_queries_are_not_cached_and_do_not_snapshot(self):
        h = prepared()
        run_to_end(h)
        s = h.session()
        for k in range(3):
            s.invoke(instance_id=h.instance_id, capability_id='chromatogram.read', arguments={'max_points': 40000}, request_id=f'q{k}')
        self.assertFalse(any(key[2].startswith('q') for key in h._requests))

    def test_two_instances_are_isolated(self):
        a, b = prepared('a'), prepared('b', comp={'vanillin': 0.2})
        run_to_end(a)
        self.assertEqual(b.phase, 'idle')
        self.assertEqual(b.run_count, 0)
        self.assertNotEqual(a.instance_id, b.instance_id)
        with self.assertRaises(rt.ActionRejected):
            a.session().read(instance_id='b')

    def test_agent_truth_isolation_and_session_protocol(self):
        h = prepared()
        h.register_principal('ev', visibility='evaluator')
        agent, ev = h.session(), h.session('ev')
        obs = agent.read(instance_id=h.instance_id)['observations']
        self.assertNotIn('truth.column_mol', obs)
        self.assertNotIn('pump.pressure_pa', obs)
        self.assertIn('pump.indicated_pressure_pa', obs)
        self.assertIn('truth.column_mol', ev.read(instance_id=h.instance_id)['observations'])
        r = agent.invoke(instance_id=h.instance_id, capability_id='ledger.read', arguments={}, request_id='x1')
        self.assertEqual(r['status'], 'rejected')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        r = agent.invoke(instance_id=h.instance_id, capability_id='sample.register_vial', arguments=rt.VIAL_EXAMPLE, request_id='x2')
        self.assertEqual(r['error']['code'], 'ACCESS_DENIED')
        d = agent.describe(instance_id=h.instance_id)
        self.assertNotIn('ledger.read', d['capabilities'])
        self.assertNotIn('truth.waste_m3', d['state_slots'])
        start = agent.invoke(instance_id=h.instance_id, capability_id='run.start', arguments={'drawer': 1, 'position': 1}, request_id='s1')
        self.assertEqual(start['status'], 'accepted')
        self.assertEqual(agent.invoke(instance_id=h.instance_id, capability_id='run.start', arguments={'drawer': 1, 'position': 1}, request_id='s1'), start)
        conflict = agent.invoke(instance_id=h.instance_id, capability_id='run.start', arguments={'drawer': 1, 'position': 2}, request_id='s1')
        self.assertEqual(conflict['error']['code'], 'REQUEST_CONFLICT')
        with self.assertRaises(rt.ActionRejected):
            ev.poll(start['operation_id'])                                  # not the owner
        h.step(rt.INJECT_CYCLE_S + 450.0)
        res = agent.poll(start['operation_id'])
        text = json.dumps(res)
        for sid in MIX:
            self.assertNotIn(sid, text)                                     # results never carry identities
        self.assertEqual(res['status'], 'succeeded')
        w = agent.write(instance_id=h.instance_id, state_id='sampler_door.angle_rad', value=0.4, request_id='w1')
        self.assertEqual(w['status'], 'succeeded')
        self.assertEqual(h.mech['sampler_door'], 0.4)

    def test_results_and_observations_match_contract_schemas(self):
        meta = rt.export_agilent_1260infinity_contract_metadata()
        for key, cap in meta['capabilities'].items():
            self.assertEqual(lint_schema(cap['input_schema']), [], key)
            self.assertEqual(lint_schema(cap['output_schema']), [], key)
            if cap['availability'] == 'implemented':
                self.assertEqual(validate_value(cap['example_arguments'], cap['input_schema']), [], key)
        h = prepared()
        c = run_to_end(h)
        self.assertEqual(validate_value(c, meta['capabilities']['chromatogram.read']['output_schema']), [])
        self.assertEqual(validate_value(h.invoke('ledger.read'), meta['capabilities']['ledger.read']['output_schema']), [])
        self.assertEqual(validate_value(h.invoke('template.read', substance_id='caffeine'), meta['capabilities']['template.read']['output_schema']), [])
        vals = h.read('evaluator')
        for key, slot in meta['state_slots'].items():
            self.assertEqual(lint_schema(slot['value_schema']), [], key)
            self.assertEqual(validate_value(vals[key], slot['value_schema']), [], key)
            self.assertTrue(slot['meaning_zh'], key)
        for key in ('pump.pressure_pa', 'bpr.target_pa', 'supply.pressure_pa'):
            self.assertIn('表压', meta['state_slots'][key]['reference_conditions'])

    def test_template_read_is_public_reference_only(self):
        h = rt.Agilent1260infinityRuntime()
        t = h.invoke('template.read', substance_id='caffeine')
        self.assertEqual(t['cas'], '58-08-2')
        self.assertEqual(t['licence'], 'CC BY 4.0')
        self.assertIn('silica', t['reference_retention_s'])
        with self.assertRaises(rt.ActionRejected):
            h.invoke('template.read', substance_id='not_a_compound')

    def test_column_oven_rates_follow_documented_times(self):
        h = rt.Agilent1260infinityRuntime()
        h.invoke('power.set', enabled=True)
        h.invoke('method.configure', **dict(rt.METHOD_EXAMPLE, column_temperature_k=313.15))
        h.step(300.0)
        self.assertLess(abs(h.column_k - 313.15), 0.6)                      # ambient -> 40 degC in ~5 min
        h.invoke('method.configure', **dict(rt.METHOD_EXAMPLE, column_temperature_k=293.15))
        h.step(600.0)
        self.assertLess(abs(h.column_k - 293.15), 0.6)                      # 40 -> 20 degC in ~10 min


if __name__ == '__main__':
    unittest.main()

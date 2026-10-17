"""CHEM-091 ECO PHYSICS nCLD 822 M: actual API, documented response, template-backed physics, isolation and schema regression tests."""
import copy
import hashlib
import json
import math
import statistics
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT / 'skills/chemistry-lab-assets/scripts'))
from asset_runtime import eco_ncld822 as rt  # noqa: E402
from asset_tool import lint_schema, validate_value  # noqa: E402

PPM = 1e-6


def snap(h):
    return json.dumps([h.debug_snapshot(), h._rng.getstate(), h.revision, h.time_s, h.powered, h.range_index, h.filter_tau, h.full_scales,
                       h.gain_no, h.gain_nox, h.zero_no, h.zero_nox, h.plant, h.source], sort_keys=True, default=str)


class EcoNcld822Tests(unittest.TestCase):
    def setUp(self):
        self.h = rt.EcoNcld822Runtime()
        self.h.register_principal('evaluator', visibility='evaluator')
        self.agent, self.evaluator = self.h.session(), self.h.session('evaluator')

    def warm(self, noise=False):
        self.h.invoke('plant.configure', noise_enabled=noise)
        self.h.invoke('power.set', enabled=True)
        self.h.step(900)
        self.assertEqual(self.h.read()['status.phase'], 'ready')

    def settle(self, s=15.0):
        self.h.step(s)
        return self.h.invoke('nox.measure', max_points=0)

    def ag(self, cap, args=None, rid=None):
        self._n = getattr(self, '_n', 0) + 1
        return self.agent.invoke(instance_id=self.h.instance_id, capability_id=cap, arguments=args or {}, request_id=rid or f'r{self._n}')

    # ------------------------------------------------------------------------------------------------ protocol semantics
    def test_setpoints_only_write_targets(self):
        self.warm()
        self.h.invoke('inlet.connect_standard', standard_id='srm_1686b')
        self.h.step(5)
        before = (self.h.time_s, list(self.h.ch), list(self.h.y))
        self.h.invoke('filter.set', time_constant_s=10.0)
        self.h.invoke('range.select', index=1)
        self.h.invoke('calibration.configure', span_no_mol_mol=500e-6, span_nox_mol_mol=500e-6, window_s=20.0)
        self.assertEqual(before, (self.h.time_s, list(self.h.ch), list(self.h.y)))
        self.assertEqual(self.h.filter_tau, 10.0)
        self.assertEqual(self.h.read()['range.active_full_scale_mol_mol'], 50e-6)

    def test_read_describe_measure_are_pure_and_detached(self):
        self.warm(noise=True)
        self.h.invoke('inlet.connect_standard', standard_id='srm_1686b')
        self.h.invoke('measurement.start')
        self.h.step(5)
        before = snap(self.h)
        for _ in range(2):
            r = self.agent.read(instance_id=self.h.instance_id)
            d = self.agent.describe(instance_id=self.h.instance_id)
            m = self.h.invoke('nox.measure', max_points=10)
            m['series'].clear()
            d['capabilities'].clear()
            r['observations'].clear()
        self.assertEqual(before, snap(self.h))

    def test_invalid_inputs_and_rejected_preconditions_are_atomic(self):
        cases = [('measurement.start', {}), ('calibration.zero', {}), ('calibration.span', {}), ('power.set', {'enabled': 1}),
                 ('filter.set', {'time_constant_s': math.nan}), ('filter.set', {'time_constant_s': 61.0}), ('range.select', {'index': 4}),
                 ('range.select', {'index': True}), ('range.configure', {'full_scales_mol_mol': [5e-6, 5e-6, 5e-4, 5e-3]}),
                 ('range.configure', {'full_scales_mol_mol': [1e-6, 5e-5, 5e-4, 5e-3]}), ('inlet.connect_standard', {'standard_id': 'secret'}),
                 ('inlet.connect', {'composition': {'unobtainium': 1e-3}, 'pressure_pa': 101325.0, 'temperature_k': 298.15, 'label': 'x'}),
                 ('inlet.connect', {'composition': {'nitric_oxide': 0.7, 'oxygen': 0.6}, 'pressure_pa': 101325.0, 'temperature_k': 298.15, 'label': 'x'}),
                 ('power.set', {'enabled': True, 'extra': 1}), ('calibration.valve_select', {}), ('data.export', {}), ('no.such', {})]
        for cap, args in cases:
            with self.subTest(cap=cap, args=args):
                before = snap(self.h)
                with self.assertRaises(rt.ActionRejected):
                    self.h.invoke(cap, **args)
                self.assertEqual(before, snap(self.h))
        with self.assertRaisesRegex(rt.ActionRejected, 'UNSUPPORTED_OPTION'):
            self.h.invoke('analog_output.configure')
        for dt in [0, -1, True, math.inf, math.nan, '1']:
            before = snap(self.h)
            with self.assertRaises(ValueError):
                self.h.step(dt)
            self.assertEqual(before, snap(self.h))

    def test_two_instances_are_isolated(self):
        other = rt.EcoNcld822Runtime('other')
        before = snap(other)
        self.warm(noise=True)
        self.h.invoke('inlet.connect_standard', standard_id='srm_1687b')
        self.h.invoke('measurement.start')
        self.h.step(20)
        self.assertEqual(before, snap(other))
        with self.assertRaises(rt.ActionRejected):
            self.agent.read(instance_id='other')

    def test_agent_and_evaluator_truth_isolation(self):
        self.warm()
        self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', arguments=rt.INLET_EXAMPLE, request_id='e1')
        self.h.step(10)
        public = self.agent.describe(instance_id=self.h.instance_id)
        for hidden in ('inlet.connect', 'plant.configure'):
            self.assertNotIn(hidden, public['capabilities'])
        for hidden in ('inlet.composition', 'chamber.no_mol_mol', 'converter.temperature_k', 'line.condensed_water_fraction'):
            self.assertNotIn(hidden, public['state_slots'])
            with self.assertRaises(rt.ActionRejected):
                self.agent.read(instance_id=self.h.instance_id, state_ids=[hidden])
        denied = self.ag('inlet.connect', rt.INLET_EXAMPLE)
        self.assertEqual(denied['error']['code'], 'ACCESS_DENIED')
        with self.assertRaises(TypeError):
            self.agent.read(instance_id=self.h.instance_id, visibility='evaluator')
        recs = self.h.invoke('nox.measure')['template_records']
        self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', request_id='e2',
                              arguments=dict(rt.INLET_EXAMPLE, composition={'ammonia': 1e-4, 'nitric_acid': 1e-5}))
        self.h.step(10)
        self.assertEqual(recs, self.h.invoke('nox.measure')['template_records'])     # provenance list does not reveal the sample
        text = json.dumps(self.agent.read(instance_id=self.h.instance_id)) + json.dumps(self.h.invoke('nox.measure'))
        self.assertNotIn('"ammonia"', text)
        self.assertNotIn('composition', text)
        self.assertNotIn('scr_outlet', text)                                         # host sample label is evaluator-only
        self.assertEqual(self.h.read('evaluator')['inlet.label'], 'scr_outlet')
        self.assertIn('inlet.composition', self.evaluator.read(instance_id=self.h.instance_id)['observations'])

    def test_retry_dedup_conflict_and_write(self):
        r = self.ag('power.set', {'enabled': True}, 'p')
        rev = self.h.revision
        self.assertEqual(r, self.ag('power.set', {'enabled': True}, 'p'))
        self.assertEqual(rev, self.h.revision)
        self.assertEqual(self.ag('power.set', {'enabled': False}, 'p')['error']['code'], 'REQUEST_CONFLICT')
        self.assertTrue(self.h.powered)
        w = self.agent.write(instance_id=self.h.instance_id, state_id='filter.time_constant_s', value=5.0, request_id='w')
        self.assertEqual(w['status'], 'succeeded')
        self.assertEqual(self.h.filter_tau, 5.0)
        with self.assertRaises(rt.ActionRejected):
            self.agent.write(instance_id=self.h.instance_id, state_id='display.no_mol_mol', value=1.0, request_id='w2')

    # ------------------------------------------------------------------------------------------------ documented response
    def test_lag_and_rise_time_within_brochure(self):
        self.warm()
        self.h.invoke('filter.set', time_constant_s=0.0)
        self.h.invoke('inlet.connect_standard', standard_id='zero_nitrogen')
        self.h.step(10)
        self.h.invoke('inlet.connect_standard', standard_id='srm_1686b')
        target = self.h.read('evaluator')['inlet.composition']['nitric_oxide']        # simulated cylinder content (certificate +- U95)
        t0, t10, t90 = self.h.time_s, None, None
        while self.h.time_s - t0 < 8:
            self.h.step(0.01)
            v = self.h._indicated()[0]
            if t10 is None and v > 0.1 * target:
                t10 = self.h.time_s - t0
            if t90 is None and v > 0.9 * target:
                t90 = self.h.time_s - t0
        self.assertLess(t10, 3.0)                                   # brochure: lag time < 3 s
        self.assertLess(t90 - rt.LAG_INT_S, 1.0)                    # brochure: rise time 0-90 % < 1 s (after the lag)
        self.assertAlmostEqual(self.h._indicated()[0], target, delta=0.002 * target)

    def test_zero_noise_matches_brochure_and_scales_with_filter(self):
        self.warm(noise=True)
        self.h.invoke('inlet.connect_standard', standard_id='zero_nitrogen')
        self.h.step(30)
        sig = {}
        for tau in (1.0, 10.0):
            self.h.invoke('filter.set', time_constant_s=tau)
            self.h.step(5 * tau)
            vals = []
            for _ in range(4000):
                self.h.step(max(0.5, tau / 2))
                vals.append(self.h._indicated()[0])
            sig[tau] = statistics.pstdev(vals)
        self.assertAlmostEqual(sig[1.0] / PPM, 0.06, delta=0.008)    # brochure: 0.06 ppm 1 sigma (reference filter assumed 1 s)
        self.assertAlmostEqual(2 * sig[1.0] / PPM, 0.12, delta=0.016)  # brochure: min. detectable 0.12 ppm
        self.assertLess(sig[10.0], 0.5 * sig[1.0])

    def test_ranges_over_range_flag_and_display_resolution(self):
        self.warm()
        self.h.invoke('range.configure', full_scales_mol_mol=[10e-6, 100e-6, 1000e-6, 5000e-6])
        self.h.invoke('range.select', index=1)
        self.h.invoke('inlet.connect_standard', standard_id='srm_1686b')
        m = self.settle()
        self.assertIn('over_range', m['flags'])
        self.assertAlmostEqual(m['no_mol_mol'] / 1e-7, round(m['no_mol_mol'] / 1e-7), places=6)
        self.h.invoke('range.select', index=2)
        self.assertNotIn('over_range', self.h.invoke('nox.measure')['flags'])

    # ------------------------------------------------------------------------------------------------ calibration (async)
    def test_zero_span_with_srm_then_quench_bias_on_flue_gas(self):
        self.warm()
        self.h.invoke('inlet.connect_standard', standard_id='zero_nitrogen')
        self.h.step(15)
        z = self.ag('calibration.zero')
        self.assertEqual(z['status'], 'accepted')
        self.assertEqual(self.agent.poll(z['operation_id'])['status'], 'running')
        self.h.step(31)
        self.assertEqual(self.agent.poll(z['operation_id'])['status'], 'succeeded')
        srm = rt.library()['by_id']['srm_2631a']['values']                       # valid certificate (1687b expired 2025)
        x = srm['certified_mole_fraction']['value']
        self.h.invoke('calibration.configure', span_no_mol_mol=x, span_nox_mol_mol=x, window_s=30.0)
        r = self.h.invoke('inlet.connect_standard', standard_id='srm_2631a')
        self.assertFalse(r['certificate_expired'])
        self.assertTrue(self.h.invoke('inlet.connect_standard', standard_id='srm_1687b')['certificate_expired'])
        self.h.invoke('inlet.connect_standard', standard_id='srm_2631a')
        self.h.step(15)
        s = self.ag('calibration.span')
        self.h.step(31)
        self.assertEqual(self.agent.poll(s['operation_id'])['status'], 'succeeded')
        self.h.invoke('range.select', index=3)
        m = self.settle()
        self.assertAlmostEqual(m['no_mol_mol'], x, delta=2e-7)
        comp = {'nitric_oxide': 250e-6, 'carbon_dioxide': 0.10, 'water': 0.02}
        self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', request_id='flue',
                              arguments=dict(composition=comp, pressure_pa=101325.0, temperature_k=298.15, label='flue'))
        m = self.settle()
        truth = rt.cylinder_truth(self.h.instance_id, 'srm_2631a')[1]
        expect = 250e-6 / (1 + 0.60 * 0.10 + 2.1 * 0.02) * x / truth  # NASA TM X-3229 factors; span traceable to the certificate value
        self.assertAlmostEqual(m['no_mol_mol'], expect, delta=0.003 * expect)
        self.assertIn('quench_if_water', m['template_records'])

    def test_span_without_span_gas_fails_and_cancel_keeps_factors(self):
        self.warm()
        self.h.invoke('inlet.connect_standard', standard_id='zero_nitrogen')
        self.h.step(15)
        s = self.ag('calibration.span')
        self.h.step(31)
        self.assertEqual(self.agent.poll(s['operation_id'])['status'], 'failed')
        self.assertEqual((self.h.gain_no, self.h.gain_nox), (1.0, 1.0))
        z = self.ag('calibration.zero')
        self.h.step(5)
        self.assertEqual(self.agent.cancel(z['operation_id'], 'cancel')['status'], 'cancelled')
        self.assertEqual((self.h.zero_no, self.h.zero_nox), (0.0, 0.0))
        z2 = self.ag('calibration.zero')
        self.h.step(5)
        self.h.invoke('power.set', enabled=False)
        self.assertEqual(self.agent.poll(z2['operation_id'])['status'], 'cancelled')
        self.assertEqual(self.h.read()['calibration.phase'], 'idle')
        self.h.register_principal('other_agent')
        with self.assertRaises(rt.ActionRejected):
            self.h.session('other_agent').poll(z2['operation_id'])

    # ------------------------------------------------------------------------------------------------ template-backed physics
    def test_converter_classes_and_efficiency(self):
        self.warm()
        conn = lambda comp, label: self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', request_id=label,  # noqa: E731
                                                         arguments=dict(composition=comp, pressure_pa=101325.0, temperature_k=298.15, label=label))
        conn({'nitrogen_dioxide': 100e-6}, 'no2')
        m = self.settle()
        self.assertAlmostEqual(m['no_mol_mol'], 0.0, delta=2e-7)
        self.assertAlmostEqual(m['nox_mol_mol'], 100e-6, delta=0.01 * 100e-6)
        self.assertAlmostEqual(m['no2_mol_mol'], m['nox_mol_mol'] - m['no_mol_mol'], delta=1.5e-7)
        self.h.invoke('plant.configure', converter_no2_efficiency=0.9)
        m = self.settle()
        self.assertAlmostEqual(m['nox_mol_mol'], 90e-6, delta=0.01 * 90e-6)
        self.h.invoke('plant.configure', converter_no2_efficiency=1.0)
        conn({'nitrous_oxide': 300e-6, 'hydrogen_cyanide': 50e-6}, 'n2o')          # Dunlea 2007: not efficiently reduced
        m = self.settle()
        self.assertAlmostEqual(m['nox_mol_mol'], 0.0, delta=2e-7)
        conn({'nitric_acid': 50e-6, 'methyl_nitrate': 20e-6}, 'noy')                # efficiently reduced
        m = self.settle()
        self.assertAlmostEqual(m['nox_mol_mol'], 70e-6, delta=0.01 * 70e-6)
        conn({'ammonia': 100e-6}, 'nh3')                                             # "lesser extent": explicit host assumption
        m = self.settle()
        self.assertTrue(self.h.read('evaluator')['converter.partial_assumption_applied'])
        self.assertNotIn('converter.partial_assumption_applied', self.h.read())          # composition-derived: evaluator only
        self.assertEqual(m['flags'], [])
        self.assertAlmostEqual(m['nox_mol_mol'], 50e-6, delta=0.01 * 50e-6)
        self.h.invoke('plant.configure', partial_conversion_fraction=0.2)
        m = self.settle()
        self.assertFalse(self.h.read('evaluator')['converter.partial_assumption_applied'])
        self.assertAlmostEqual(m['nox_mol_mol'], 20e-6, delta=0.01 * 20e-6)
        conn({'particulate_nitrate': 40e-6}, 'pn')                                   # particulate nitrate: partial class
        m = self.settle()
        self.assertAlmostEqual(m['nox_mol_mol'], 8e-6, delta=0.02 * 8e-6)

    def test_dark_chemistry_in_the_sample_path_conserves_nitrogen(self):
        for comp in [{'nitric_oxide': 1000e-6, 'oxygen': 0.2095}, {'nitric_oxide': 500e-6, 'ozone': 300e-6, 'oxygen': 0.2},
                     {'nitrogen_dioxide': 2000e-6, 'ozone': 500e-6, 'water': 0.01}]:
            out = rt.integrate_dark_chemistry(comp, 298.15, 101325.0, 2.0)
            self.assertAlmostEqual(rt.n_atoms(out), rt.n_atoms(comp), delta=1e-12 * rt.n_atoms(comp))
        out = rt.integrate_dark_chemistry({'nitric_oxide': 500e-6, 'ozone': 300e-6, 'oxygen': 0.2}, 298.15, 101325.0, 2.0)
        self.assertAlmostEqual(out['nitric_oxide'], 200e-6, delta=2e-6)              # NO + O3 titration (IUPAC row 54)
        out = rt.integrate_dark_chemistry({'nitric_oxide': 1000e-6, 'oxygen': 0.2095}, 298.15, 101325.0, 2.0)
        self.assertAlmostEqual(out['nitrogen_dioxide'], 9.8e-6, delta=0.5e-6)        # 2NO + O2 (row 53): ~1 % in 2 s at 1000 ppm in air
        self.warm()
        self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', request_id='gpt',
                              arguments=dict(composition={'nitric_oxide': 500e-6, 'ozone': 300e-6, 'oxygen': 0.2}, pressure_pa=101325.0,
                                             temperature_k=298.15, label='gpt'))
        m = self.settle()
        self.assertAlmostEqual(m['no_mol_mol'], 200e-6, delta=3e-6)
        self.assertAlmostEqual(m['nox_mol_mol'], 500e-6, delta=8e-6)

    def test_condensation_scrubs_soluble_species_unless_hot_tubing(self):
        self.warm()
        comp = {'nitric_oxide': 200e-6, 'nitric_acid': 50e-6, 'water': 0.12}       # dew point ~49 C, path at 25 C
        args = dict(composition=comp, pressure_pa=101325.0, temperature_k=393.15, label='wet')
        self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', arguments=args, request_id='wet')
        m = self.settle()
        ev = self.h.read('evaluator')
        self.assertGreater(ev['line.condensed_water_fraction'], 0.08)
        self.assertLess(m['nox_mol_mol'] - m['no_mol_mol'], 5e-6)                    # HNO3 (Hscp 2.1e3) dissolved in the condensate
        self.assertGreater(m['no_mol_mol'], 190e-6)                                   # NO (Hscp 1.9e-5) stays
        self.h.invoke('plant.configure', hot_tubing_installed=True)
        m = self.settle()
        self.assertEqual(self.h.read('evaluator')['line.condensed_water_fraction'], 0.0)
        self.assertGreater(m['nox_mol_mol'] - m['no_mol_mol'], 30e-6)

    def test_wall_memory_has_no_dip_and_decays_after_the_residence(self):
        self.warm()
        self.h.invoke('plant.configure', line_length_m=6.1, line_inner_diameter_m=0.0016, line_material='stainless')
        conn = lambda comp, label: self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', request_id=label,  # noqa: E731
                                                         arguments=dict(composition=comp, pressure_pa=101325.0, temperature_k=298.15, label=label))
        conn({'nitric_oxide': 100e-6}, 'a')
        self.h.step(600)
        base = self.h._indicated()[0]
        conn({'nitric_oxide': 500e-6}, 'b')
        res, vals = self.h._residence(), []
        while self.h.time_s < 600 + 900.0 + 6.0:
            self.h.step(0.05)
            vals.append(self.h._indicated()[0])
        self.assertGreaterEqual(min(vals), base - 1e-9)                               # up-step: no dip while the old gas is in transit
        conn({'nitric_oxide': 300e-6, 'nitrogen_dioxide': 200e-6}, 'c')
        self.h.step(600)
        steady = self.h._indicated()[1]
        conn({}, 'zero')
        t0 = self.h.time_s
        while self.h.time_s - t0 < res - 0.1:
            self.h.step(0.05)
            self.assertAlmostEqual(self.h._indicated()[1], steady, delta=1e-4 * steady)   # nothing changes before the zero gas arrives
        self.h.step(20)
        self.assertLess(self.h._indicated()[1], steady)

    def test_quench_relative_scale_is_an_explicit_assumption(self):
        self.warm()
        conn = lambda label: self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', request_id=label,  # noqa: E731
                                                   arguments=dict(composition={'nitric_oxide': 100e-6, 'argon': 0.5}, pressure_pa=101325.0,
                                                                  temperature_k=298.15, label=label))
        conn('ar')
        self.assertAlmostEqual(self.settle()['no_mol_mol'], 100e-6, delta=2e-7)      # default phi = 0: measured factors only
        self.h.invoke('plant.configure', quench_relative_scale=0.1)
        expect = 100e-6 / (1 + 0.1 * (0.5 / 0.9 - 1) * 0.5)
        self.assertAlmostEqual(self.settle()['no_mol_mol'], expect, delta=2e-7)

    def test_standard_certificate_and_cylinder_truth(self):
        self.warm()
        r = self.h.invoke('inlet.connect_standard', standard_id='srm_1686b')
        self.assertEqual(r['value_kind'], 'lot_value_printed_on_sample_certificate')
        x, u = r['certificate_mole_fraction'], r['expanded_uncertainty_U95']
        truth = self.h.read('evaluator')['inlet.composition']['nitric_oxide']
        self.assertNotEqual(truth, x)
        self.assertLess(abs(truth - x), 2 * u)
        r2 = self.h.invoke('inlet.connect_standard', standard_id='srm_2628a')
        self.assertEqual(r2['value_kind'], 'nominal_used_individually_certified')
        self.assertEqual(rt.EcoNcld822Runtime('x').invoke('inlet.connect_standard', standard_id='srm_1686b')['certificate_mole_fraction'], x)

    def test_hot_tubing_kinetics_are_clamped_to_evaluated_ranges(self):
        self.warm()
        self.h.invoke('plant.configure', hot_tubing_installed=True)
        self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', request_id='hot',
                              arguments=dict(composition={'nitric_oxide': 100e-6, 'ozone': 50e-6}, pressure_pa=101325.0, temperature_k=453.15, label='hot'))
        self.assertIn(54, self.h.debug_snapshot()['kinetics_clamped'])

    def test_line_memory_from_wall_adsorption(self):
        for material in ('ptfe', 'stainless'):                                       # PTFE maps to the report's Teflon records
            tr, td = rt.adsorption_tau('nitric_oxide', material, 0.03)
            self.assertGreater(tr, 0)
            self.assertGreater(td, 0)
        self.assertGreater(rt.adsorption_tau('nitric_oxide', 'ptfe', 0.03)[1], rt.adsorption_tau('nitric_oxide', 'stainless', 0.03)[1])
        self.warm()
        self.h.invoke('plant.configure', line_length_m=6.1, line_inner_diameter_m=0.0016, line_material='stainless')
        self.h.invoke('inlet.connect_standard', standard_id='srm_1686b')
        self.h.step(4)
        early = self.h._indicated()[0]
        self.h.step(300)
        late = self.h._indicated()[0]
        self.assertLess(early, 0.9 * late)                                            # wall uptake slows the rise (NASA TM X-3229 Table V)

    def test_inlet_pressure_flow_and_compensation(self):
        self.warm()
        base = dict(composition={'nitric_oxide': 100e-6}, temperature_k=298.15, label='p')
        self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', arguments=dict(base, pressure_pa=101325.0), request_id='p1')
        m1 = self.settle()
        f1 = self.h.read()['flow.indicated_sample_m3_s']
        self.evaluator.invoke(instance_id=self.h.instance_id, capability_id='inlet.connect', arguments=dict(base, pressure_pa=50000.0), request_id='p2')
        self.h.step(0.6)
        m2 = self.settle()
        self.assertIn('inlet_pressure_out_of_range', m2['flags'])                    # from the indicated (sensor) pressure
        self.assertEqual(m2['quality'], 'invalid')
        self.assertAlmostEqual(self.h.read()['flow.indicated_sample_m3_s'] / f1, 50000.0 / 101325.0, places=6)
        self.assertAlmostEqual(m2['no_mol_mol'], m1['no_mol_mol'], delta=2e-7)        # steady state: pressure compensated

    def test_measurement_series_and_template_provenance(self):
        self.warm()
        self.h.invoke('inlet.connect_standard', standard_id='srm_2629a')
        self.h.step(10)
        self.h.invoke('measurement.toggle')
        self.h.step(10)
        m = self.h.invoke('nox.measure', max_points=100)
        self.assertEqual(len(m['series']), 11)
        self.assertAlmostEqual(m['series'][-1]['time_s'] - m['series'][0]['time_s'], 10.0, places=6)
        self.h.invoke('measurement.toggle')
        self.h.step(5)
        self.assertEqual(len(self.h.invoke('nox.measure', max_points=100)['series']), 11)
        lib = ROOT / rt.TEMPLATE_REL
        self.assertEqual(m['library_sha256'], hashlib.sha256(lib.read_bytes()).hexdigest())
        ids = set(rt.library()['by_id'])
        self.assertTrue(set(m['template_records']) <= ids)
        self.assertGreaterEqual(rt.library()['data']['n_records'], 50)

    def test_power_loss_stops_measurement_and_blanks_display(self):
        self.warm()
        self.h.invoke('measurement.start')
        self.h.invoke('power.toggle')
        r = self.h.read()
        self.assertEqual((r['measurement.phase'], r['display.no_mol_mol'], r['status.phase']), ('idle', None, 'off'))
        self.h.step(600)
        self.assertLess(self.h.t_conv, 400.0)

    # ------------------------------------------------------------------------------------------------ schemas
    def test_contract_schemas_and_real_results(self):
        meta = rt.export_eco_ncld822_contract_metadata()
        for k, cap in meta['capabilities'].items():
            self.assertEqual(lint_schema(cap['input_schema']), [], k)
            self.assertEqual(lint_schema(cap['output_schema']), [], k)
        for k, slot in meta['state_slots'].items():
            self.assertEqual(lint_schema(slot['value_schema']), [], k)
            self.assertTrue(slot['meaning_zh'])
        self.warm()
        cases = [('range.configure', {'full_scales_mol_mol': rt.DEFAULT_FS}), ('range.select', {'index': 3}), ('filter.set', {'time_constant_s': 2.0}),
                 ('inlet.connect_standard', {'standard_id': 'srm_1683b'}), ('measurement.start', {}),
                 ('calibration.configure', {'span_no_mol_mol': 49.3e-6, 'span_nox_mol_mol': 49.3e-6, 'window_s': 10.0}),
                 ('nox.measure', {'max_points': 5}), ('calibration.zero', {}), ('calibration.cancel', {}), ('measurement.stop', {}),
                 ('inlet.disconnect', {}), ('power.toggle', {})]
        for cap, args in cases:
            with self.subTest(cap=cap):
                self.h.step(2)
                result = self.h.invoke(cap, **args)
                self.assertEqual(validate_value(result, meta['capabilities'][cap]['output_schema']), [])
        obs = self.evaluator.read(instance_id=self.h.instance_id)['observations']
        for k, o in obs.items():
            self.assertEqual(validate_value(o['value'], meta['state_slots'][k]['value_schema']), [], k)


if __name__ == '__main__':
    unittest.main()

"""Unit parsing and display of the compile layer (no lab needed)."""
import unittest

import helpers  # noqa: F401
from chemlab.compile import units
from chemlab.compile.errors import CompileError


class Parse(unittest.TestCase):
    def test_temperature(self):
        self.assertAlmostEqual(units.parse('80C', 'temperature'), 353.15)
        self.assertAlmostEqual(units.parse('80', 'temperature'), 353.15)                 # bare number: °C
        self.assertAlmostEqual(units.parse('80 °C', 'temperature'), 353.15)
        self.assertAlmostEqual(units.parse('353.15K', 'temperature'), 353.15)
        self.assertAlmostEqual(units.parse('212F', 'temperature'), 373.15)
        self.assertAlmostEqual(units.parse('-80C', 'temperature'), 193.15)
        self.assertEqual(units.fmt(353.15, 'temperature'), '80.0 °C')

    def test_volume_exact(self):
        self.assertEqual(units.parse('7.5uL', 'volume'), 7.5e-9)
        self.assertEqual(units.parse('50', 'volume'), 5e-8)
        self.assertEqual(units.parse('0.05mL', 'volume'), 5e-8)
        self.assertEqual(units.parse('50 µL', 'volume'), 5e-8)
        self.assertEqual(units.fmt(1.5e-7, 'volume'), '150 µL')
        self.assertEqual(units.fmt(2.5e-6, 'volume'), '2.5 mL')

    def test_mass_rotation_pressure_duration_flow(self):
        self.assertAlmostEqual(units.parse('12.5mg', 'mass'), 1.25e-5)
        self.assertAlmostEqual(units.parse('52.3', 'mass'), 0.0523)                    # bare number: g
        self.assertAlmostEqual(units.parse('300rpm', 'rotation'), 31.41592653589793)
        self.assertAlmostEqual(units.parse('200mbar', 'pressure'), 20000.0)
        self.assertAlmostEqual(units.parse('1bar', 'pressure'), 1e5)
        self.assertAlmostEqual(units.parse('5min', 'duration'), 300.0)
        self.assertAlmostEqual(units.parse('1.5h', 'duration'), 5400.0)
        self.assertAlmostEqual(units.parse('1mL/min', 'flow'), 1e-6 / 60)
        self.assertAlmostEqual(units.parse('50%', 'fraction'), 0.5)
        self.assertAlmostEqual(units.parse('0.5K', 'temperature_delta'), 0.5)
        self.assertEqual(units.fmt(31.41592653589793, 'rotation'), '300 rpm')
        self.assertEqual(units.fmt(0.0523, 'mass'), '52.3 g')
        self.assertEqual(units.fmt(20000.0, 'pressure'), '200 mbar')
        self.assertEqual(units.fmt(5400, 'duration'), '1 h 30 min')

    def test_rejects_are_usage_errors(self):
        for text, kind in (('80X', 'temperature'), ('abc', 'volume'), ('', 'mass'), ('-5uL', 'volume'), ('-300K', 'temperature'),
                           ('5 parsecs', 'duration'), ('nan', 'mass'), ('1e999', 'pressure')):
            with self.assertRaises(CompileError, msg=text) as c:
                units.parse(text, kind)
            self.assertEqual(c.exception.exit_code, 2, text)
            self.assertTrue(c.exception.fix, text)

    def test_friendly_contract_units(self):
        self.assertEqual(units.friendly(353.15, 'K'), '80.0 °C')
        self.assertEqual(units.friendly(5e-8, 'm^3'), '50 µL')
        self.assertEqual(units.friendly(31.41592653589793, 'rad/s'), '300 rpm')
        self.assertIsNone(units.friendly(True, 'K'))
        self.assertIsNone(units.friendly(10.0, 's'))


if __name__ == '__main__':
    unittest.main()


class RealArguments(unittest.TestCase):
    """The agent's value lands in the unit each real contract argument declares (°C / degC, g, mL, min, h, nm, rpm-as-rad/s,
    m3 / m^3, m3/s, K/min, g/cm3, %), and passes that argument's schema."""

    CASES = [  # slug, capability, argument, typed, expected value in the declared unit, kind override
        ('ohaus_px224e', 'density.configure', 'water_temp_c', '21C', 21.0, None),               # degC
        ('ohaus_px224e', 'density.configure', 'water_temp_c', '294.15K', 21.0, None),
        ('antonpaar_monowave400', 'experiment.insert_step', 'temperature_c', '120', 120.0, None),  # °C, bare number = °C
        ('miele_plw6111', 'programmes.set_block', 'temperature_C', '140F', 60.0, None),
        ('mt_mp70', 'substance.save', 'tol_c', '0.5K', 0.5, 'temperature_delta'),                # a °C difference
        ('agilent_7890b_ssl_fid', 'thermal.set_temperature', 'temperature_k', '80C', 353.15, None),
        ('ohaus_px224e', 'density.configure', 'liquid_density', '998kg/m3', 0.998, None),        # g/cm3
        ('ohaus_px224e', 'density.configure', 'sinker_volume_ml', '0.01L', 10.0, None),          # mL
        ('ohaus_px224e', 'density.configure', 'porous_weight_g', '500mg', 0.5, None),            # g
        ('miele_plw6111', 'programmes.set_block', 'time_min', '2h', 120.0, None),                # min
        ('vacuubrand_pc3001_vario_select', 'settings.configure', 'maintenance_h', '2d', 48.0, None),   # h
        ('thermo_ibrightfl1500', 'dye.define', 'excitation_nm', '0.488um', 488.0, None),         # nm
        ('biotage_selekt', 'flush.start', 'flow_ml_min', '0.05mL/s', 3.0, None),                 # mL/min
        ('agilent_hplc1260', 'chromatography.set_method', 'injection_volume_m3', '10uL', 1e-8, None),   # m3
        ('multiwave_7101', 'digestion.load_template', 'acid_volume_m3', '5mL', 5e-6, None),     # m^3
        ('agilent_7890b_ssl_fid', 'inlet.configure', 'split_flow_m3_s', '50mL/min', 5e-5 / 60, None),   # m3/s
        ('linseis_lsr3', 'thermal.set_temperature', 'ramp_rate_k_min', '0.5K/s', 30.0, None),   # K/min
        ('parr_4591', 'stirrer.set_speed', 'speed_rad_s', '300rpm', 31.41592653589793, None),   # rad/s
        ('binder_vd56', 'rinsing.set_percent', 'percent', '50%', 50.0, None),                   # %
    ]

    @classmethod
    def setUpClass(cls):
        import glob
        import json
        import os
        from helpers import ROOT
        cls.contracts = {}
        for f in glob.glob(os.path.join(ROOT, '..', 'assets', '**', 'asset.contract.json'), recursive=True):
            with open(f, encoding='utf-8') as fh:
                d = json.load(fh)
            cls.contracts[d['asset_type_id'].split('.', 1)[1]] = d

    def test_values_land_in_the_declared_unit(self):
        from chemlab.contract import validate
        for slug, cap, arg, text, want, kind in self.CASES:
            schema = self.contracts[slug]['capabilities'][cap]['input_schema']['properties'][arg]
            got = units.parse_for(text, schema, kind)
            self.assertAlmostEqual(got, want, delta=1e-9 * max(1.0, abs(want)), msg=(slug, arg, text, schema.get('x-unit')))
            self.assertEqual(validate(schema, got, arg), [], (slug, arg, got))

    def test_spellings_and_round_trips(self):
        self.assertEqual(units.declared({'x-unit': 'degC'}), '°C')
        self.assertEqual(units.declared({'x-unit': 'm3/s'}), 'm^3/s')
        self.assertEqual(units.declared({'x-unit': '1'}), None)
        self.assertEqual(units.declared({'anyOf': [{'type': 'number', 'x-unit': 'K'}, {'type': 'null'}]}), 'K')
        for u, v in (('°C', 25.0), ('degC', 25.0), ('g', 52.3), ('min', 5.0), ('rpm', 300.0), ('mL', 2.5), ('mbar', 200.0), ('nm', 450.0),
                     ('d', 2.0), ('m3', 1e-6), ('K/min', 10.0), ('g/cm3', 0.998), ('%', 50.0)):
            self.assertAlmostEqual(units.to_declared(units.from_declared(v, u), u), v, places=9, msg=u)
        self.assertAlmostEqual(units.from_declared(25.0, 'degC'), 298.15)
        self.assertEqual(units.fmt_declared(60.3, 'degC'), '60.3 °C')
        self.assertEqual(units.fmt_declared(300.0, 'rpm'), '300 rpm')
        with self.assertRaises(CompileError):
            units.parse_for('5 apples', {'x-unit': 'kg'})
        self.assertEqual(units.parse_for('7', {'type': 'integer'}), 7.0)              # unitless: a plain number
        with self.assertRaises(CompileError):
            units.parse_for('7mL', {'type': 'integer'})


class SpectroscopyUnits(unittest.TestCase):
    """Units the spectroscopy class needs: wavenumber, magnetic field, fast times, GHz, uM / nM, count rate,
    signed frequency offset; and the generic `wait` with units; and callable expectations in the lowering harness."""

    def test_new_kinds(self):
        self.assertAlmostEqual(units.parse('1650cm-1', 'wavenumber'), 165000.0)
        self.assertAlmostEqual(units.parse_for('1650cm-1', {'x-unit': 'm^-1'}), 165000.0)
        self.assertAlmostEqual(units.parse_for('1650cm-1', {'x-unit': '1/m'}), 165000.0)
        self.assertAlmostEqual(units.parse_for('1650cm-1', {'x-unit': '/cm'}), 1650.0)
        self.assertEqual(units.fmt(165000.0, 'wavenumber'), '1650 cm⁻¹')
        self.assertAlmostEqual(units.parse('340mT', 'magnetic_field'), 0.34)
        self.assertAlmostEqual(units.parse('3400G', 'magnetic_field'), 0.34)
        self.assertEqual(units.friendly(0.34, 'T'), '340 mT')
        self.assertAlmostEqual(units.parse('5ns', 'duration'), 5e-9)
        self.assertAlmostEqual(units.parse('120fs', 'duration'), 1.2e-13)
        self.assertAlmostEqual(units.parse('2us', 'duration'), 2e-6)
        self.assertAlmostEqual(units.parse('9.4GHz', 'frequency'), 9.4e9)
        self.assertEqual(units.fmt(9.4e9, 'frequency'), '9.4 GHz')
        self.assertAlmostEqual(units.parse('50uM', 'concentration'), 0.05)
        self.assertAlmostEqual(units.parse('20nM', 'concentration'), 2e-5)
        self.assertAlmostEqual(units.parse_for('50uM', {'x-unit': 'mol/m3'}), 0.05)
        self.assertAlmostEqual(units.parse('-120Hz', 'frequency_offset'), -120.0)
        self.assertAlmostEqual(units.parse('50kcps', 'count_rate'), 50000.0)
        with self.assertRaises(CompileError):
            units.parse('-5Hz', 'frequency')                                     # a frequency is not negative; an offset may be

    def test_generic_wait_takes_units(self):
        from chemlab.generic import CmdError, _seconds
        self.assertEqual(_seconds('60'), 60.0)
        self.assertEqual(_seconds('60s'), 60.0)
        self.assertEqual(_seconds('5min'), 300.0)
        self.assertEqual(_seconds('1.5h'), 5400.0)
        for bad in ('0', '-5', '5 parsecs', 'abc', '0min'):
            with self.assertRaises(CmdError, msg=bad):
                _seconds(bad)

    def test_lowering_callable_expectations(self):
        from chemlab.compile.lowering import calls_match
        got = [('analysis.fit', {'x': [1.0, 2.0, 3.0], 'model': 'lorentz'})]
        self.assertTrue(calls_match(got, None))
        self.assertTrue(calls_match(got, [('analysis.fit', lambda a: len(a['x']) == 3 and a['model'] == 'lorentz')]))
        self.assertFalse(calls_match(got, [('analysis.fit', lambda a: len(a['x']) == 4)]))
        self.assertTrue(calls_match(got, lambda calls: calls[0][0] == 'analysis.fit'))
        self.assertTrue(calls_match(got, [('analysis.fit', {'x': [1.0, 2.0, 3.0], 'model': 'lorentz'})]))
        self.assertFalse(calls_match(got, [('analysis.other', {})]))

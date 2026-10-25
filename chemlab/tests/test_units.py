import math
import unittest

import helpers  # noqa: F401  (sets sys.path)
from chemlab.errors import ValidationError
from chemlab.units import fmt_s, fmt_ul, parse_duration, parse_length, parse_temperature, parse_volume


class Volume(unittest.TestCase):
    def test_units_and_bare_numbers(self):
        for text, ul in (('50', 50), ('50uL', 50), ('50 uL', 50), ('50ul', 50), ('50µL', 50), ('50μL', 50), ('0.05mL', 50),
                         ('500nL', 0.5), ('1e3uL', 1000), ('.5uL', 0.5)):
            m3, got = parse_volume(text)
            self.assertAlmostEqual(got, ul, places=9, msg=text)
            self.assertAlmostEqual(m3, ul * 1e-9, delta=1e-21, msg=text)

    def test_exact_decimal_conversion(self):
        self.assertEqual(parse_volume('7.5uL')[0], float('7.5e-9'))
        self.assertEqual(parse_volume('0.1mL')[0], float('1e-7'))

    def test_rejects(self):
        for text, code in (('5mg', 'UNIT_NOT_ALLOWED'), ('5e-9m3', 'UNIT_NOT_ALLOWED'), ('-5uL', 'VOLUME_NOT_POSITIVE'), ('0', 'VOLUME_NOT_POSITIVE'),
                           ('abc', 'BAD_NUMBER'), ('', 'MISSING_VALUE'), ('nan', 'BAD_NUMBER'), ('1e999mL', 'BAD_NUMBER')):
            with self.assertRaises(ValidationError, msg=text) as cm:
                parse_volume(text)
            self.assertEqual(cm.exception.code, code, text)
            self.assertTrue(cm.exception.fix)


class OtherUnits(unittest.TestCase):
    def test_length_time_temperature(self):
        self.assertAlmostEqual(parse_length('5'), 0.005)
        self.assertAlmostEqual(parse_length('2cm'), 0.02)
        self.assertEqual(parse_duration('2min'), 120.0)
        self.assertEqual(parse_duration('30'), 30.0)
        self.assertAlmostEqual(parse_temperature('37C'), 310.15)
        self.assertAlmostEqual(parse_temperature('37°C'), 310.15)
        self.assertAlmostEqual(parse_temperature('300K'), 300.0)
        with self.assertRaises(ValidationError):
            parse_temperature('-300C')
        with self.assertRaises(ValidationError):
            parse_duration('5kg')

    def test_formatting(self):
        self.assertEqual(fmt_ul(7.5e-9), '7.5 µL')
        self.assertEqual(fmt_ul(1e-6), '1000 µL')
        self.assertEqual(fmt_s(30), '30 s')
        self.assertEqual(fmt_s(314), '5 min 14 s')
        self.assertTrue(math.isfinite(parse_volume('1000')[0]))


if __name__ == '__main__':
    unittest.main()

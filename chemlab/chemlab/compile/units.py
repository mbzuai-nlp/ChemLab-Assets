"""Units of the compile layer.

Two sides:

* the agent types a value with a unit (50uL, 80C, 300rpm, 5min, 200mbar, 12.5mg); ``parse(text, kind)`` gives SI;
* every contract argument and state slot declares its own unit (``x-unit`` / ``unit``).  Most are SI, but many are not (°C or
  degC on 78 arguments, g, min, rpm, mL, mbar, nm, h, days, ...), and one unit is often spelled two ways (m^3 / m3, °C / degC,
  1/s / s^-1, ohm / Ω).  ``DECLARED`` is the alias table: a declared unit -> (kind, factor, offset) with SI = value*factor +
  offset.  The compile layer converts the agent's value into whatever unit the argument declares:

    parse_for('80C', {'type': 'number', 'x-unit': 'K'})          -> 353.15
    parse_for('80C', {'type': 'number', 'x-unit': 'degC'})       -> 80.0
    parse_for('300rpm', {'type': 'number', 'x-unit': 'rad/s'})   -> 31.4159...
    parse_for('5min', {'type': 'number', 'x-unit': 'min'})       -> 5.0
    parse_for('2h', {'type': 'number', 'x-unit': 'min'})         -> 120.0
    from_declared(80.0, 'degC') -> 353.15 (K);   fmt(353.15, 'temperature') -> '80.0 °C'

Decimal arithmetic avoids binary round-off for scaled units (7.5 uL becomes exactly float('7.5e-9')).  A bare number takes the
kind's default unit (uL, °C, s, rpm, mbar, g, ...).  Errors are usage errors (exit 2, nothing sent) with a fix.
Gauge and absolute pressure are not converted into each other: the argument's own name says which one it is.
"""
import math
import re
from decimal import Decimal, InvalidOperation

from .errors import invalid

_NUM = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'
D = Decimal
PI = D('3.14159265358979323846264338327950288')


class Kind:
    """One physical quantity: SI unit, the units the agent may type (factor to SI), default unit, display ladder."""

    def __init__(self, name, si, units, default, display, allow_negative=True, example='1'):
        self.name, self.si, self.units, self.default = name, si, dict(units), default
        self.display = display                   # [(threshold_si, label, unit key, decimals)]: first whose threshold <= |value|
        self.allow_negative = allow_negative
        self.example = example

    def unit_names(self):
        seen, out = set(), []
        for u, f in self.units.items():
            if f not in seen:
                seen.add(f)
                out.append(u)
        return out


def _k(name, si, units, default, display, allow_negative=True, example='1'):
    conv = (lambda v: v) if name == 'temperature' else (lambda v: v if isinstance(v, Decimal) else D(v))   # temperature: C/K/F markers
    return Kind(name, si, {u: conv(v) for u, v in units.items()}, default, display, allow_negative, example)


KINDS = {
    'volume': _k('volume', 'm^3', {'ul': '1e-9', 'µl': '1e-9', 'μl': '1e-9', 'nl': '1e-12', 'ml': '1e-6', 'cm3': '1e-6', 'l': '1e-3',
                                   'm3': 1, 'm^3': 1}, 'ul', [(1e-3, 'L', 'l', 4), (1e-6, 'mL', 'ml', 4), (0, 'µL', 'ul', 3)], False, '50uL'),
    'mass': _k('mass', 'kg', {'kg': 1, 'g': '1e-3', 'mg': '1e-6', 'ug': '1e-9', 'µg': '1e-9', 'μg': '1e-9'}, 'g',
               [(1.0, 'kg', 'kg', 5), (1e-3, 'g', 'g', 4), (0, 'mg', 'mg', 3)], True, '12.5mg'),
    'length': _k('length', 'm', {'m': 1, 'cm': '1e-2', 'mm': '1e-3', 'um': '1e-6', 'µm': '1e-6', 'μm': '1e-6', 'nm': '1e-9', 'pm': '1e-12'},
                 'mm',
                 [(1.0, 'm', 'm', 3), (1e-3, 'mm', 'mm', 2), (0, 'µm', 'um', 2)], True, '5mm'),
    'wavelength': _k('wavelength', 'm', {'nm': '1e-9', 'um': '1e-6', 'µm': '1e-6', 'm': 1}, 'nm', [(1e-6, 'µm', 'um', 3), (0, 'nm', 'nm', 1)],
                     False, '450nm'),
    'duration': _k('duration', 's', {'fs': '1e-15', 'ps': '1e-12', 'ns': '1e-9', 'us': '1e-6', 'µs': '1e-6', 'μs': '1e-6', 'ms': '1e-3',
                                     's': 1, 'sec': 1, 'min': 60, 'h': 3600, 'hr': 3600, 'd': 86400, 'day': 86400, 'days': 86400}, 's', [],
                   False, '30s or 5min'),
    'temperature': _k('temperature', 'K', {'c': 'C', '°c': 'C', 'degc': 'C', 'k': 'K', 'f': 'F', '°f': 'F', 'degf': 'F'}, 'c',
                      [(0, '°C', 'c', 1)], True, '80C'),
    'temperature_delta': _k('temperature_delta', 'K', {'k': 1, 'c': 1, '°c': 1, 'degc': 1}, 'k', [(0, 'K', 'k', 2)], True, '0.5K'),
    'heating_rate': _k('heating_rate', 'K/s', {'k/s': 1, 'k/min': D(1) / 60, 'c/min': D(1) / 60, '°c/min': D(1) / 60, 'k/h': D(1) / 3600,
                                               'c/h': D(1) / 3600}, 'k/min', [(0, 'K/min', 'k/min', 2)], True, '10K/min'),
    'rotation': _k('rotation', 'rad/s', {'rpm': D(2) * PI / 60, 'rad/s': 1, 'hz': D(2) * PI}, 'rpm', [(0, 'rpm', 'rpm', 0)], False, '300rpm'),
    'angle': _k('angle', 'rad', {'rad': 1, 'mrad': '1e-3', 'urad': '1e-6', 'µrad': '1e-6', 'deg': PI / 180, '°': PI / 180}, 'deg', [(0, '°', 'deg', 1)], True, '45deg'),
    'pressure': _k('pressure', 'Pa', {'pa': 1, 'hpa': 100, 'kpa': 1000, 'mpa': 1000000, 'mbar': 100, 'bar': 100000,
                                      'psi': '6894.757293168', 'torr': '133.3223684210526', 'mmhg': '133.322387415', 'atm': 101325},
                  'mbar', [(1e5, 'bar', 'bar', 3), (0, 'mbar', 'mbar', 1)], True, '200mbar'),
    'flow': _k('flow', 'm^3/s', {'ul/min': D('1e-9') / 60, 'ml/min': D('1e-6') / 60, 'l/min': D('1e-3') / 60, 'ml/h': D('1e-6') / 3600,
                                 'ul/s': '1e-9', 'ml/s': '1e-6', 'm3/s': 1, 'm^3/s': 1, 'm3/h': D(1) / 3600, 'm^3/h': D(1) / 3600,
                                 'l/h': D('1e-3') / 3600,
                                 # standard volumetric flow (mass-flow controllers): gas volume at the device's standard conditions
                                 'sccm': D('1e-6') / 60, 'slpm': D('1e-3') / 60, 'slm': D('1e-3') / 60, 'sccs': '1e-6',
                                 'ccm': D('1e-6') / 60, 'smlpm': D('1e-6') / 60, 'sml/min': D('1e-6') / 60, 'lpm': D('1e-3') / 60},
                'ml/min', [(0, 'mL/min', 'ml/min', 4)], False, '1mL/min'),
    'mass_flow': _k('mass_flow', 'kg/s', {'kg/s': 1, 'g/min': D('1e-3') / 60, 'mg/min': D('1e-6') / 60, 'ug/min': D('1e-9') / 60,
                                          'µg/min': D('1e-9') / 60, 'g/h': D('1e-3') / 3600}, 'g/min', [(0, 'g/min', 'g/min', 4)], False, '2g/min'),
    'fraction': _k('fraction', '1', {'%': '0.01', 'pct': '0.01', 'frac': 1, '': 1, 'ppm': '1e-6', 'ppb': '1e-9', 'ppt': '1e-12'}, '%', [(0, '%', '%', 1)],
                   True, '50%'),
    'frequency': _k('frequency', 'Hz', {'hz': 1, 'khz': 1000, 'mhz': 1000000, 'ghz': 1000000000, '1/s': 1, 's^-1': 1}, 'hz',
                    [(1e9, 'GHz', 'ghz', 4), (1e6, 'MHz', 'mhz', 4), (0, 'Hz', 'hz', 3)], False, '50Hz'),
    'power': _k('power', 'W', {'w': 1, 'kw': 1000, 'mw': '1e-3', 'uw': '1e-6', 'µw': '1e-6', 'μw': '1e-6', 'nw': '1e-9',
                               'cal/s': '4.184', 'mcal/s': '4.184e-3', 'ucal/s': '4.184e-6', 'µcal/s': '4.184e-6', 'μcal/s': '4.184e-6'},
                'w', [(0, 'W', 'w', 1)], True, '100W'),
    'voltage': _k('voltage', 'V', {'v': 1, 'mv': '1e-3', 'kv': 1000, 'uv': '1e-6', 'µv': '1e-6', 'μv': '1e-6', 'nv': '1e-9'}, 'v', [(1, 'V', 'v', 4), (0, 'mV', 'mv', 3)],
                  True, '1.5V'),
    'current': _k('current', 'A', {'a': 1, 'ma': '1e-3', 'ua': '1e-6', 'µa': '1e-6', 'μa': '1e-6', 'na': '1e-9', 'pa': '1e-12'}, 'ma',
                  [(1, 'A', 'a', 4), (1e-3, 'mA', 'ma', 3), (0, 'µA', 'ua', 3)], True, '10mA'),
    'force': _k('force', 'N', {'n': 1, 'mn': '1e-3', 'kn': 1000, 'un': '1e-6', 'µn': '1e-6', 'μn': '1e-6', 'nn': '1e-9', 'pn': '1e-12'}, 'n', [(1000, 'kN', 'kn', 3), (0, 'N', 'n', 3)], True, '50N'),
    'energy': _k('energy', 'J', {'j': 1, 'kj': 1000, 'mj': '1e-3', 'ev': '1.602176634e-19', 'kev': '1.602176634e-16'}, 'j', [(0, 'J', 'j', 3)], True, '10J'),
    'speed': _k('speed', 'm/s', {'m/s': 1, 'mm/s': '1e-3', 'mm/min': D('1e-3') / 60, 'cm/s': '1e-2', 'um/s': '1e-6'}, 'mm/s',
                [(0, 'mm/s', 'mm/s', 3)], True, '5mm/s'),
    'area': _k('area', 'm^2', {'m2': 1, 'm^2': 1, 'cm2': '1e-4', 'mm2': '1e-6'}, 'cm2', [(0, 'cm²', 'cm2', 3)], False, '2cm2'),
    'concentration': _k('concentration', 'mol/m^3', {'mol/m3': 1, 'mol/m^3': 1, 'mol/l': 1000, 'm': 1000, 'mmol/l': 1, 'mm': 1,
                                                     'umol/l': '1e-3', 'um': '1e-3', 'µm': '1e-3', 'μm': '1e-3', 'nmol/l': '1e-6',
                                                     'nm': '1e-6', 'pmol/l': '1e-9', 'pm': '1e-9'}, 'mol/l', [(1, 'mol/L', 'mol/l', 4), (1e-3, 'mM', 'mm', 4), (0, 'µM', 'um', 3)],
                        False, '0.1mol/L or 50uM'),
    # density and mass concentration share kg/m^3: >= 100 kg/m^3 shows as g/cm³ (liquids, solids), below as g/L, mg/L or µg/L
    'density': _k('density', 'kg/m^3', {'kg/m3': 1, 'kg/m^3': 1, 'g/cm3': 1000, 'g/ml': 1000, 'g/l': 1, 'mg/l': '1e-3', 'ug/l': '1e-6',
                                        'µg/l': '1e-6', 'μg/l': '1e-6', 'ng/l': '1e-9', 'mg/ml': 1, 'ug/ml': '1e-3', 'µg/ml': '1e-3',
                                        'g/m3': '1e-3', 'mg/m3': '1e-6', 'ug/m3': '1e-9', 'µg/m3': '1e-9'}, 'g/cm3',
                  [(100, 'g/cm³', 'g/cm3', 4), (1, 'g/L', 'g/l', 4), (1e-3, 'mg/L', 'mg/l', 4), (1e-15, 'µg/L', 'ug/l', 3),
                   (0, 'g/cm³', 'g/cm3', 4)], False, '0.998g/cm3 or 5mg/L'),
    'scan_speed': _k('scan_speed', 'm/s', {'nm/min': D('1e-9') / 60, 'nm/s': '1e-9', 'um/s': '1e-6', 'µm/s': '1e-6', 'mm/s': '1e-3',
                                           'mm/min': D('1e-3') / 60, 'm/s': 1}, 'nm/min', [(0, 'nm/min', 'nm/min', 1)], False, '50nm/min'),
    'xray_energy': _k('xray_energy', 'J', {'kev': '1.602176634e-16', 'ev': '1.602176634e-19', 'j': 1}, 'kev', [(0, 'keV', 'kev', 3)],
                      False, '20keV'),
    'field_rate': _k('field_rate', 'T/s', {'t/s': 1, 'mt/s': '1e-3', 't/min': D(1) / 60, 'mt/min': D('1e-3') / 60}, 't/s',
                     [(0, 'mT/s', 'mt/s', 4)], True, '10mT/s'),
    'force_rate': _k('force_rate', 'N/s', {'n/s': 1, 'mn/s': '1e-3', 'n/min': D(1) / 60, 'mn/min': D('1e-3') / 60}, 'n/s',
                     [(0, 'N/s', 'n/s', 4)], True, '0.1N/s'),
    'resistance': _k('resistance', 'ohm', {'ohm': 1, 'mohm': '1e-3', 'kohm': 1000, 'megohm': 1000000, 'gohm': 1000000000, 'ω': 1,
                                           'kω': 1000, 'mω': '1e-3'}, 'ohm',
                     [(1e9, 'GΩ', 'gohm', 4), (1e6, 'MΩ', 'megohm', 4), (1e3, 'kΩ', 'kohm', 4), (0, 'Ω', 'ohm', 4)], False, '100ohm'),
    'sheet_resistance': _k('sheet_resistance', 'ohm/sq', {'ohm/sq': 1, 'mohm/sq': '1e-3', 'kohm/sq': 1000, 'megohm/sq': 1000000}, 'ohm/sq',
                           [(1e6, 'MΩ/sq', 'megohm/sq', 4), (1e3, 'kΩ/sq', 'kohm/sq', 4), (0, 'Ω/sq', 'ohm/sq', 4)], False, '100ohm/sq'),
    'moment': _k('moment', 'A*m^2', {'a*m^2': 1, 'a*m2': 1, 'am2': 1, 'am^2': 1, 'emu': '1e-3', 'memu': '1e-6'}, 'emu',
                 [(0, 'emu', 'emu', 4)], True, '3.47emu'),
    'heat_capacity': _k('heat_capacity', 'J/K', {'j/k': 1, 'kj/k': 1000}, 'j/k', [(0, 'J/K', 'j/k', 4)], True, '8700J/K'),
    'number': _k('number', '1', {'': 1}, '', [(0, '', '', 6)], True, '1.0'),
    'amount': _k('amount', 'mol', {'mol': 1, 'mmol': '1e-3', 'umol': '1e-6', 'µmol': '1e-6', 'μmol': '1e-6', 'nmol': '1e-9',
                                   'pmol': '1e-12', 'fmol': '1e-15'}, 'umol',
                 [(1, 'mol', 'mol', 4), (1e-3, 'mmol', 'mmol', 4), (1e-6, 'µmol', 'umol', 4), (1e-9, 'nmol', 'nmol', 4),
                  (1e-18, 'pmol', 'pmol', 4), (0, 'mol', 'mol', 4)], False, '2mmol'),
    'conductivity': _k('conductivity', 'S/m', {'s/m': 1, 'ms/cm': '0.1', 'us/cm': '1e-4', 'µs/cm': '1e-4'}, 'ms/cm',
                       [(0, 'mS/cm', 'ms/cm', 4)], False, '1.41mS/cm'),
    'wavenumber': _k('wavenumber', 'm^-1', {'cm-1': 100, 'cm^-1': 100, '1/cm': 100, '/cm': 100, 'm-1': 1, 'm^-1': 1, '1/m': 1},
                     'cm-1', [(0, 'cm⁻¹', 'cm-1', 2)], False, '1650cm-1'),
    'magnetic_field': _k('magnetic_field', 'T', {'t': 1, 'mt': '1e-3', 'ut': '1e-6', 'µt': '1e-6', 'g': '1e-4', 'kg': '0.1'}, 'mt',
                         [(1, 'T', 't', 4), (0, 'mT', 'mt', 3)], True, '340mT'),
    'frequency_offset': _k('frequency_offset', 'Hz', {'hz': 1, 'khz': 1000, 'mhz': 1000000}, 'hz', [(0, 'Hz', 'hz', 3)], True, '-120Hz'),
    'count_rate': _k('count_rate', '1/s', {'/s': 1, '1/s': 1, 's^-1': 1, 's-1': 1, 'cps': 1, 'kcps': 1000, 'mcps': 1000000, 'hz': 1,
                                           'khz': 1000}, '/s',
                     [(0, '/s', '/s', 1)], False, '50000/s'),
}

# Declared contract units: normalised spelling -> (kind, factor, offset): SI = value * factor + offset.
_DECL = {
    'K': ('temperature', 1, 0), '°C': ('temperature', 1, D('273.15')), '°F': ('temperature', D(5) / 9, D('255.3722222222222222')),
    'kg': ('mass', 1, 0), 'g': ('mass', D('1e-3'), 0), 'mg': ('mass', D('1e-6'), 0), 'ug': ('mass', D('1e-9'), 0),
    'm^3': ('volume', 1, 0), 'L': ('volume', D('1e-3'), 0), 'mL': ('volume', D('1e-6'), 0), 'uL': ('volume', D('1e-9'), 0),
    'cm^3': ('volume', D('1e-6'), 0), 'nL': ('volume', D('1e-12'), 0),
    'm': ('length', 1, 0), 'mm': ('length', D('1e-3'), 0), 'cm': ('length', D('1e-2'), 0), 'um': ('length', D('1e-6'), 0),
    'nm': ('wavelength', D('1e-9'), 0),
    's': ('duration', 1, 0), 'ms': ('duration', D('1e-3'), 0), 'min': ('duration', 60, 0), 'h': ('duration', 3600, 0), 'd': ('duration', 86400, 0),
    'K/s': ('heating_rate', 1, 0), 'K/min': ('heating_rate', D(1) / 60, 0), 'K/h': ('heating_rate', D(1) / 3600, 0),
    '°C/min': ('heating_rate', D(1) / 60, 0),
    'rad/s': ('rotation', 1, 0), 'rpm': ('rotation', D(2) * PI / 60, 0),
    'rad': ('angle', 1, 0), 'deg': ('angle', PI / 180, 0),
    'Pa': ('pressure', 1, 0), 'hPa': ('pressure', 100, 0), 'kPa': ('pressure', 1000, 0), 'mbar': ('pressure', 100, 0), 'bar': ('pressure', 100000, 0),
    'm^3/s': ('flow', 1, 0), 'mL/min': ('flow', D('1e-6') / 60, 0), 'uL/min': ('flow', D('1e-9') / 60, 0), 'L/min': ('flow', D('1e-3') / 60, 0),
    'kg/s': ('mass_flow', 1, 0), 'ug/min': ('mass_flow', D('1e-9') / 60, 0), 'g/min': ('mass_flow', D('1e-3') / 60, 0),
    '%': ('fraction', D('0.01'), 0),
    'Hz': ('frequency', 1, 0), 's^-1': ('frequency', 1, 0),
    'W': ('power', 1, 0), 'V': ('voltage', 1, 0), 'mV': ('voltage', D('1e-3'), 0), 'A': ('current', 1, 0), 'mA': ('current', D('1e-3'), 0),
    'N': ('force', 1, 0), 'J': ('energy', 1, 0), 'm/s': ('speed', 1, 0), 'm^2': ('area', 1, 0),
    'mol/m^3': ('concentration', 1, 0), 'mol/L': ('concentration', 1000, 0),
    'kg/m^3': ('density', 1, 0), 'g/cm^3': ('density', 1000, 0),
    'S/m': ('conductivity', 1, 0), 'mS/cm': ('conductivity', D('0.1'), 0), 'uS/cm': ('conductivity', D('1e-4'), 0),
    'm^-1': ('wavenumber', 1, 0), 'cm^-1': ('wavenumber', 100, 0),
    'T': ('magnetic_field', 1, 0), 'mT': ('magnetic_field', D('1e-3'), 0), 'G': ('magnetic_field', D('1e-4'), 0),
    'us': ('duration', D('1e-6'), 0), 'ns': ('duration', D('1e-9'), 0), 'ps': ('duration', D('1e-12'), 0), 'fs': ('duration', D('1e-15'), 0),
    'kHz': ('frequency', 1000, 0), 'MHz': ('frequency', 1000000, 0), 'GHz': ('frequency', 1000000000, 0),
    'uM': ('concentration', D('1e-3'), 0), 'nM': ('concentration', D('1e-6'), 0), 'pM': ('concentration', D('1e-9'), 0),
    'uW': ('power', D('1e-6'), 0), 'mW': ('power', D('1e-3'), 0), 'ucal/s': ('power', D('4.184e-6'), 0), 'cal/s': ('power', D('4.184'), 0),
    'cm^3/min': ('flow', D('1e-6') / 60, 0), 'mM': ('concentration', 1, 0), 'M': ('concentration', 1000, 0),
    '/s': ('count_rate', 1, 0), 'cps': ('count_rate', 1, 0),
    'm^3/h': ('flow', D(1) / 3600, 0), 'L/h': ('flow', D('1e-3') / 3600, 0), 'mL/h': ('flow', D('1e-6') / 3600, 0),
    'sccm': ('flow', D('1e-6') / 60, 0), 'slpm': ('flow', D('1e-3') / 60, 0), 'sccs': ('flow', D('1e-6'), 0),
    'ppm': ('fraction', D('1e-6'), 0), 'ppb': ('fraction', D('1e-9'), 0),
    'mN': ('force', D('1e-3'), 0), 'uN': ('force', D('1e-6'), 0), 'nN': ('force', D('1e-9'), 0), 'pN': ('force', D('1e-12'), 0),
    'kN': ('force', 1000, 0),
    'eV': ('energy', D('1.602176634e-19'), 0), 'keV': ('energy', D('1.602176634e-16'), 0),
    'T/s': ('field_rate', 1, 0), 'N/s': ('force_rate', 1, 0), 'ohm': ('resistance', 1, 0), 'ohm/sq': ('sheet_resistance', 1, 0),
    'A*m^2': ('moment', 1, 0), 'J/K': ('heat_capacity', 1, 0),
    'mol': ('amount', 1, 0), 'mmol': ('amount', D('1e-3'), 0), 'umol': ('amount', D('1e-6'), 0), 'nmol': ('amount', D('1e-9'), 0),
    'pmol': ('amount', D('1e-12'), 0),
    'g/L': ('density', 1, 0), 'mg/L': ('density', D('1e-3'), 0), 'ug/L': ('density', D('1e-6'), 0), 'mg/mL': ('density', 1, 0),
    'ug/mL': ('density', D('1e-3'), 0), 'g/m^3': ('density', D('1e-3'), 0), 'mg/m^3': ('density', D('1e-6'), 0),
    'ug/m^3': ('density', D('1e-9'), 0),
}
# other spellings met in the 203 contracts -> the normalised one
ALIASES = {'degC': '°C', 'deg C': '°C', 'celsius': '°C', 'C°': '°C', 'degF': '°F', 'm3': 'm^3', 'm3/s': 'm^3/s', 'mol/m3': 'mol/m^3',
           'kg/m3': 'kg/m^3', 'm2': 'm^2', 'cm3': 'cm^3', 'g/cm3': 'g/cm^3', '1/s': 's^-1', '°': 'deg', 'days': 'd', 'day': 'd',
           'hr': 'h', 'µL': 'uL', 'μL': 'uL', 'ul': 'uL', 'ml': 'mL', 'µg': 'ug', 'μg': 'ug', 'µg/min': 'ug/min', 'μg/min': 'ug/min',
           'µm': 'um', 'μm': 'um', 'µS/cm': 'uS/cm', 'ml/min': 'mL/min', 'µL/min': 'uL/min', 'Ω': 'ohm', 'J/(kg K)': 'J/(kg*K)',
           '1/m': 'm^-1', 'm-1': 'm^-1', '/cm': 'cm^-1', '1/cm': 'cm^-1', 'cm-1': 'cm^-1', 'm2/s': 'm^2/s', 'm3/kg': 'm^3/kg',
           'mol/m2': 'mol/m^2', 'sec': 's', 'l': 'L', 'mol/l': 'mol/L', 'µs': 'us', 'μs': 'us', 'µM': 'uM', 'μM': 'uM', 'gauss': 'G',
           'tesla': 'T', 'counts/s': '/s', 'SCCM': 'sccm', 'SLPM': 'slpm', 'SLM': 'slpm', 'slm': 'slpm', 'SCCS': 'sccs',
           'µmol': 'umol', 'μmol': 'umol', 'g/l': 'g/L', 'mg/l': 'mg/L', 'µg/L': 'ug/L', 'μg/L': 'ug/L', 'ug/l': 'ug/L',
           'µg/mL': 'ug/mL', 'mg/ml': 'mg/mL', 'g/m3': 'g/m^3', 'mg/m3': 'mg/m^3', 'µg/m3': 'ug/m^3', 'ug/m3': 'ug/m^3',
           'µW': 'uW', 'μW': 'uW', 'µcal/s': 'ucal/s', 'μcal/s': 'ucal/s', 'cm3/min': 'cm^3/min',
           '1/min': 'rpm',        # declared on the Linari RT Advanced rpm arguments (rotation per minute)
           'A m^2': 'A*m^2', 'A*m2': 'A*m^2', 'A m2': 'A*m^2', 'Am^2': 'A*m^2', 'Ω/sq': 'ohm/sq', 'J K^-1': 'J/K',
           'm3/h': 'm^3/h', 'l/h': 'L/h', 'ml/h': 'mL/h', 'µN': 'uN', 'μN': 'uN', 'PPM': 'ppm', 'PPB': 'ppb'}
# contract unit strings that mean "no unit"
UNITLESS = ('1', '', None)


def norm_unit(u):
    if u is None:
        return None
    u = str(u).strip()
    return ALIASES.get(u, u)


def declared(schema_or_unit):
    """The normalised declared unit of an argument schema / slot spec / unit string (None when unitless)."""
    if isinstance(schema_or_unit, dict):
        s = schema_or_unit
        if 'anyOf' in s and 'x-unit' not in s:
            s = next((x for x in s['anyOf'] if isinstance(x, dict) and x.get('x-unit')), s)
        u = s.get('x-unit', s.get('unit'))
    else:
        u = schema_or_unit
    u = norm_unit(u)
    return None if u in UNITLESS else u


def unit_kind(u):
    spec = _DECL.get(norm_unit(u))
    return spec[0] if spec else None


def from_declared(value, u):
    """A value in a declared unit -> SI (of its kind).  Unknown units are returned unchanged."""
    spec = _DECL.get(norm_unit(u))
    if spec is None or not _finite(value):
        return value
    _, f, off = spec
    return float(D(repr(float(value))) * D(f) + D(off))


def to_declared(si, u):
    """SI -> the declared unit."""
    spec = _DECL.get(norm_unit(u))
    if spec is None or not _finite(si):
        return si
    _, f, off = spec
    return float((D(repr(float(si))) - D(off)) / D(f))


def kind_of(name):
    if name not in KINDS:
        raise KeyError('unknown quantity kind %r' % name)
    return KINDS[name]


def example(kind):
    return KINDS[kind].example if kind in KINDS else '1'


def _split(text, kind):
    k = kind_of(kind)
    if not isinstance(text, str) or not text.strip():
        raise invalid('MISSING_VALUE', 'a %s is missing.' % kind.replace('_', ' '),
                      'give a number with a unit, for example %s.' % example(kind))
    t = text.strip().replace(' ', '')
    m = re.fullmatch(_NUM + r'(.*)', t)
    if not m:
        raise invalid('BAD_NUMBER', '%r is not a number with a unit.' % text,
                      'write the %s like %s (units: %s).' % (kind.replace('_', ' '), example(kind), ', '.join(n for n in k.unit_names() if n)))
    try:
        value = Decimal(m.group(1))
    except InvalidOperation:
        raise invalid('BAD_NUMBER', '%r is not a number.' % text, 'write a plain decimal number.')
    if not value.is_finite():
        raise invalid('BAD_NUMBER', '%r is not a finite number.' % text, 'write a plain decimal number.')
    unit = (m.group(2) or k.default).lower()
    return value, unit


def parse(text, kind, *, what=None):
    """Agent text -> SI float.  Raises a usage error (exit 2) with a fix."""
    k = kind_of(kind)
    what = what or kind.replace('_', ' ')
    value, unit = _split(text, kind)
    if kind == 'temperature':
        u = k.units.get(unit)
        if u is None:
            raise invalid('UNIT_NOT_ALLOWED', '%r is not a temperature unit.' % unit, 'give temperatures in °C (80C), K (353.15K) or °F.')
        si = value + D('273.15') if u == 'C' else (value - 32) * D(5) / D(9) + D('273.15') if u == 'F' else value
        if si < 0:
            raise invalid('BELOW_ABSOLUTE_ZERO', '%s %s is below absolute zero.' % (what, text), 'check the value and the unit.')
        return float(si)
    f = k.units.get(unit)
    if f is None:
        raise invalid('UNIT_NOT_ALLOWED', '%r is not a %s unit.' % (unit, kind.replace('_', ' ')),
                      'give the %s in %s, for example %s.' % (what, ', '.join(n for n in k.unit_names() if n), example(kind)))
    if value < 0 and not k.allow_negative:
        raise invalid('NEGATIVE_VALUE', '%s %s must not be negative.' % (what, text), 'give a value >= 0.')
    out = float(value * f)
    if not math.isfinite(out):
        raise invalid('BAD_NUMBER', '%s %s is too large to represent.' % (what, text), 'give a smaller value.')
    return out


def parse_for(text, schema_or_unit, kind=None, *, what=None):
    """Agent text -> the value in the unit the argument declares (K, °C, g, min, rpm, mL, ...).  ``kind`` overrides the kind
    implied by the declared unit (e.g. 'temperature_delta' for a K argument that is a difference)."""
    u = declared(schema_or_unit)
    spec = _DECL.get(u) if u else None
    if spec is None:                                   # unitless or a unit outside the table: a plain number (or the same unit)
        t = (text or '').strip().replace(' ', '')
        m = re.fullmatch(_NUM + r'(.*)', t)
        if not m or (m.group(2) and (u is None or norm_unit(m.group(2)) != u)):
            raise invalid('BAD_NUMBER', '%r is not a plain number%s.' % (text, ' in %s' % u if u else ''),
                          'write a number%s.' % (' in %s' % u if u else ''))
        return float(m.group(1))
    kind = kind or spec[0]
    si = parse(text, kind, what=what)
    if kind == 'temperature_delta':
        return si / float(spec[1]) if spec[0] == 'temperature' else to_declared(si, u)
    return to_declared(si, u)


# Smaller units for values that would print as 0 in the display ladder (5 pN is not "0 N"): (factor to SI, label), largest first.
SMALL = {
    'pressure': [(1, 'Pa'), (D('1e-3'), 'mPa')],
    'force': [(D('1e-3'), 'mN'), (D('1e-6'), 'µN'), (D('1e-9'), 'nN'), (D('1e-12'), 'pN')],
    'mass': [(D('1e-9'), 'µg'), (D('1e-12'), 'ng')],
    'length': [(D('1e-9'), 'nm'), (D('1e-12'), 'pm')],
    'wavelength': [(D('1e-12'), 'pm')],
    'volume': [(D('1e-12'), 'nL'), (D('1e-15'), 'pL')],
    'current': [(D('1e-9'), 'nA'), (D('1e-12'), 'pA')],
    'voltage': [(D('1e-6'), 'µV'), (D('1e-9'), 'nV')],
    'power': [(D('1e-3'), 'mW'), (D('1e-6'), 'µW'), (D('1e-9'), 'nW')],
    'energy': [(D('1e-3'), 'mJ'), (D('1e-6'), 'µJ'), (D('1.602176634e-16'), 'keV'), (D('1.602176634e-19'), 'eV')],   # X-ray lines in keV
    'flow': [(D('1e-9') / 60, 'µL/min'), (D('1e-12') / 60, 'nL/min')],
    'mass_flow': [(D('1e-6') / 60, 'mg/min'), (D('1e-9') / 60, 'µg/min')],
    'fraction': [(D('1e-6'), 'ppm'), (D('1e-9'), 'ppb')],
    'speed': [(D('1e-6'), 'µm/s'), (D('1e-9'), 'nm/s')],
    'concentration': [(D('1e-6'), 'nM'), (D('1e-9'), 'pM')],
    'conductivity': [(D('1e-4'), 'µS/cm')],
    'frequency': [(D('1e-3'), 'mHz')],
    'angle': [(PI / 180 / 3600, 'arcsec')],
}


def sig(x, digits=15):
    """A float kept to ``digits`` significant digits (never round(x, n) on small-unit values: 0.02° is 3.49e-4 rad)."""
    if isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) or x == 0:
        return x
    return float('%.*g' % (digits, x))


def _nonzero(si, kind, label, v):
    """Text for a non-zero value that its display rung rounds to 0: a smaller unit of SMALL, or 3 significant digits."""
    for f, lab in SMALL.get(kind, []):
        if abs(si) >= float(f) * 0.9995:
            return '%s %s' % ('%.3g' % (si / float(f)), lab)
    return '%s %s' % ('%.3g' % v, label)


def fmt(si, kind, decimals=None):
    """SI float -> friendly text with the kind's display unit ('80.0 °C', '52.3 g', '150 µL', '300 rpm').  A non-zero value is
    never shown as 0: it moves to a smaller unit (5e-12 N -> '5 pN', 20 ms -> '20 ms'); pressures below 1 mbar keep mbar with 3
    significant digits (5 Pa -> '0.05 mbar')."""
    if si is None:
        return 'n/a'
    if isinstance(si, bool) or not isinstance(si, (int, float)) or not math.isfinite(si):
        return str(si)
    k = kind_of(kind)
    if kind == 'temperature':
        return '%.*f °C' % (1 if decimals is None else decimals, si - 273.15)
    if kind == 'duration':
        return fmt_s(si)
    if kind == 'pressure' and decimals is None and 0 < abs(si) < 100:     # below 1 mbar: 3 significant digits (5 Pa = 0.05 mbar)
        return '%s mbar' % ('%.3g' % (si / 100.0))
    for threshold, label, key, dec in k.display:
        if abs(si) >= threshold:
            v = si / float(k.units[key])
            s = '%.*f' % (dec if decimals is None else decimals, v)
            if si != 0 and float(s) == 0 and decimals is None:
                return _nonzero(si, kind, label, v)
            if '.' in s and decimals is None and kind not in ('rotation', 'temperature_delta', 'fraction'):
                s = s.rstrip('0').rstrip('.')
            return ('%s %s' % (s, label)).strip()
    return '%g %s' % (si, k.si)


def fmt_declared(value, u, decimals=None):
    """A value in a declared unit -> friendly text (via SI)."""
    kind = unit_kind(u)
    if kind is None:
        return '%s %s' % (value, u) if u else str(value)
    return fmt(from_declared(value, u), kind, decimals)


def to_unit(si, kind, unit):
    """SI -> value in a named agent unit (float), e.g. to_unit(0.0523, 'mass', 'mg') -> 52300.0."""
    k = kind_of(kind)
    if kind == 'temperature':
        u = k.units.get(unit.lower(), unit)
        return si - 273.15 if u == 'C' else (si - 273.15) * 9 / 5 + 32 if u == 'F' else si
    return si / float(k.units[unit.lower()])


def fmt_s(seconds):
    seconds = max(0.0, float(seconds))
    if 0 < seconds < 0.05:                                     # never "0 s" for a short time: ms / µs / ns
        for f, lab in ((1e-3, 'ms'), (1e-6, 'µs'), (1e-9, 'ns')):
            if seconds >= f * 0.9995:
                return '%s %s' % ('%.3g' % (seconds / f), lab)
        return '%.3g s' % seconds
    if seconds < 90:
        return ('%.1f s' % seconds).replace('.0 s', ' s')
    m, s = divmod(int(round(seconds)), 60)
    if m < 90:
        return '%d min %02d s' % (m, s)
    h, m = divmod(m, 60)
    return '%d h %02d min' % (h, m)


_DIFF = re.compile(r'(diff|delta|tdiff|(^|[._])dt([._]|$)|difference|温差|差值|温升)', re.I)


def is_difference(slot_id=None, about=None):
    """True when a K slot holds a temperature difference (its id or description says diff / delta / tdiff)."""
    return bool(_DIFF.search(slot_id or '') or _DIFF.search(about or ''))


def friendly(value, contract_unit, slot_id=None, about=None):
    """A contract value with its declared unit -> friendly text, or None when nothing nicer exists (used by `read`).  A K slot
    whose id or description marks a temperature difference (``safety.tdiff_max_k``) is a difference, never shown in °C."""
    if not _finite(value):
        return None
    u = norm_unit(contract_unit)
    kind = unit_kind(u)
    if kind == 'temperature' and u == 'K' and is_difference(slot_id, about):
        return None                                                   # 50 K stays 50 K (not "-223.1 °C")
    if kind is None or u in ('°C', 'rpm', '%', 'mbar', 'mL', 'g', 'nm', 'min'):
        return None
    si = from_declared(value, u)
    if kind == 'duration' and abs(si) < 90:
        return None
    if kind == 'mass' and abs(si) >= 1.0:
        return None
    if kind in ('length', 'angle', 'voltage', 'current', 'force', 'energy', 'power', 'frequency', 'speed', 'area', 'count_rate',
                'frequency_offset') and u == KINDS[kind].si:
        return None
    return fmt(si, kind)


def describe_kinds(kinds):
    """'temperature: C (default), K, F; rotation: rpm (default), rad/s, hz' for describe."""
    out = []
    for kind in kinds:
        k = kind_of(kind)
        names = ['C', 'K', 'F'] if kind == 'temperature' else [n for n in k.unit_names() if n]
        sep = ', ' if any('/' in n for n in names) else '/'                  # 'flow ul/min, ml/min, ...' stays readable
        out.append('%s %s (default %s)' % (kind.replace('_', ' '), sep.join(names), k.default))
    return '; '.join(out)


def _finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ------------------------------------------------------------------ human-readable output (the --json form is never touched)
def tidy(value, sig=6):
    """Floats rounded to ``sig`` significant digits, recursively through dicts and lists: for human-readable text only."""
    if isinstance(value, float):
        return float('%.*g' % (sig, value)) if math.isfinite(value) and value != 0 else value
    if isinstance(value, dict):
        return {k: tidy(v, sig) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [tidy(v, sig) for v in value]
    return value


_LONG_FLOAT = re.compile(r'(?<![\w.])(-?\d+\.\d{7,}(?:[eE][+-]?\d+)?)(?![\w.])')
# KEY_<unit>=VALUE UNIT: the key's own unit suffix (SI names such as temperature_k, elapsed_s, volume_m3) followed by a value that is
# printed with an explicit unit, e.g. "temperature_k=25.0 °C"
_UNIT_KEY = re.compile(r'(?<![\w.])([A-Za-z][A-Za-z0-9_]*?)_(?:mol_m3|m3_s|m3_kg|kg_s|kg_m3|rad_s|n_m|j_k|w_k|m2|m3|kg|k|pa|v|a|hz|m|s|w|j|n|c|'
                       r'ohm|rad)(\s?=\s?)(?=[+-]?\d[\d.]*(?:[eE][+-]?\d+)? (?:°C|[A-Za-zµμΩ°%]))', re.I)


def _g6(x):
    s = '%.6g' % x
    return '%.0f' % x if 'e+' in s and abs(x) < 1e15 else s


# lines that echo arguments the agent types back (`method total_lift_m=1.2mm`): their keys keep the unit suffix
_ARG_LINE = re.compile(r"\bmethod\b[^=\n]*[:=]|kept from |from the contract's example|fixed by the mapping")


def tidy_text(text):
    """Human-readable output of the compile layer: long float literals (binary noise such as -0.0019000000000000128 or
    423.1499999999983) are shown with 6 significant digits, and a KEY_<unit>=VALUE label loses the key's unit suffix when the
    value is printed with its own unit ("temperature_k=25.0 °C" -> "temperature=25.0 °C"), except on lines that echo method
    arguments, whose keys are the names to type."""
    if not text or not isinstance(text, str):
        return text
    text = _LONG_FLOAT.sub(lambda m: _g6(float(m.group(1))), text)
    return ''.join(line if _ARG_LINE.search(line) else _UNIT_KEY.sub(lambda m: m.group(1) + m.group(2), line)
                   for line in text.splitlines(keepends=True))

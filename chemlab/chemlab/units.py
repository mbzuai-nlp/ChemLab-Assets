"""Units the agent types (uL, mL, mm, s, min, degC) <-> SI values the runtime takes (m^3, m, s, K).

Decimal arithmetic avoids binary round-off: 7.5 uL becomes exactly float('7.5e-9').
"""
import math
import re
from decimal import Decimal, InvalidOperation

from .errors import ValidationError

_NUM = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'
_VOLUME = {'nl': Decimal('1e-12'), 'ul': Decimal('1e-9'), 'µl': Decimal('1e-9'), 'μl': Decimal('1e-9'), 'ml': Decimal('1e-6'),
           'l': Decimal('1e-3')}
_LENGTH = {'um': Decimal('1e-6'), 'µm': Decimal('1e-6'), 'mm': Decimal('1e-3'), 'cm': Decimal('1e-2'), 'm': Decimal(1)}
_TIME = {'ms': Decimal('1e-3'), 's': Decimal(1), 'sec': Decimal(1), 'min': Decimal(60), 'h': Decimal(3600)}
_TEMP = ('c', '°c', 'degc', 'k')


def _split(text, what, allowed, default_unit):
    if not isinstance(text, str) or not text.strip():
        raise ValidationError('MISSING_VALUE', '%s is missing.' % what, 'give a number with a unit, for example 50uL.')
    m = re.fullmatch(_NUM + r'\s*([^\d\s][^\s]*)?', text.strip())
    if not m:
        raise ValidationError('BAD_NUMBER', '%r is not a number with a unit.' % text,
                              'write the %s like 50uL (allowed units: %s).' % (what, ', '.join(allowed)))
    try:
        value = Decimal(m.group(1))
    except InvalidOperation:
        raise ValidationError('BAD_NUMBER', '%r is not a number.' % text, 'write a plain decimal number.')
    unit = (m.group(2) or default_unit).lower()
    if not value.is_finite():
        raise ValidationError('BAD_NUMBER', '%r is not a finite number.' % text, 'write a plain decimal number.')
    return value, unit


def parse_volume(text):
    """'50', '50uL', '50 µL', '0.05mL', '500nL' -> (m^3 float, uL float).  A bare number is uL (Opentrons convention)."""
    value, unit = _split(text, 'volume', ['uL', 'mL', 'nL'], 'ul')
    if unit not in _VOLUME:
        raise ValidationError('UNIT_NOT_ALLOWED', 'unit %r is not a volume unit.' % unit,
                              'give volumes in uL (default), mL or nL, for example 50uL.')
    if value <= 0:
        raise ValidationError('VOLUME_NOT_POSITIVE', 'volume %s must be greater than zero.' % text, 'give a positive volume.')
    si = value * _VOLUME[unit]
    if not math.isfinite(float(si)) or float(si) == 0.0:
        raise ValidationError('BAD_NUMBER', 'volume %s is too large or too small to represent.' % text, 'give a volume in uL.')
    return float(si), float(si / _VOLUME['ul'])


def parse_length(text, default_unit='mm'):
    value, unit = _split(text, 'length', ['mm', 'cm', 'um', 'm'], default_unit)
    if unit not in _LENGTH:
        raise ValidationError('UNIT_NOT_ALLOWED', 'unit %r is not a length unit.' % unit, 'give lengths in mm, for example 5mm.')
    return float(value * _LENGTH[unit])


def parse_duration(text, default_unit='s'):
    value, unit = _split(text, 'duration', ['s', 'min', 'h', 'ms'], default_unit)
    if unit not in _TIME:
        raise ValidationError('UNIT_NOT_ALLOWED', 'unit %r is not a time unit.' % unit, 'give times in s or min, for example 30s.')
    if value < 0:
        raise ValidationError('DURATION_NEGATIVE', 'duration %s is negative.' % text, 'give a duration >= 0.')
    return float(value * _TIME[unit])


def parse_temperature(text, default_unit='c'):
    """'37', '37C', '37°C', '310.15K' -> kelvin."""
    value, unit = _split(text, 'temperature', ['C', 'K'], default_unit)
    if unit not in _TEMP:
        raise ValidationError('UNIT_NOT_ALLOWED', 'unit %r is not a temperature unit.' % unit, 'give temperatures in °C, for example 37C.')
    kelvin = value if unit == 'k' else value + Decimal('273.15')
    if kelvin < 0:
        raise ValidationError('BELOW_ABSOLUTE_ZERO', 'temperature %s is below absolute zero.' % text, 'check the value and unit.')
    return float(kelvin)


def ul(m3):
    """m^3 -> uL (rounded to the nL)."""
    return round(m3 * 1e9, 3)


def fmt_ul(m3):
    v = ul(m3)
    s = ('%.3f' % v).rstrip('0').rstrip('.')
    return '%s µL' % s


def fmt_s(seconds):
    seconds = max(0.0, float(seconds))
    if seconds < 90:
        return '%.0f s' % seconds
    m, s = divmod(int(round(seconds)), 60)
    if m < 90:
        return '%d min %02d s' % (m, s)
    h, m = divmod(m, 60)
    return '%d h %02d min' % (h, m)


def fmt_mm(m):
    return '%.1f mm' % (m * 1e3)


def is_finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

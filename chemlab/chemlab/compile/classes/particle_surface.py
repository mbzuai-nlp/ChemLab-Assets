"""Particle, pore and surface characterisation class ("particle_surface"): particle sizers (DLS / zeta, laser diffraction,
dynamic image analysis), dispersion stability, gas sorption and mercury / capillary-flow porometry, gas pycnometry,
chemisorption, vapour sorption, streaming potential, tensiometry, contact angle, Langmuir troughs and gas permeation.

Verbs (members and mappings: configs/adapters/particle_surface.json):

    load [--cell N] [--volume V]      bring the sample the operator placed into the measuring position and close the access, in
                                      the instrument's order (lid, cuvette, cup and locks, guards, clamps); on the contact-angle
                                      meter and the Langmuir trough: deposit the sample (a drop, a spread film) of --volume
    unload [--cell N]                 open the access / bring the holder out so the operator can put in or take out a sample
    prepare [--temp T --hold T --ramp R]  the step the instrument needs before a measurement: background, degassing, wetting,
                                      taring, leak check (a job where it runs on its own)
    method KEY=VALUE ... [--cell N]   the measurement method; keys not given keep their current value (the instrument's method
                                      display where it has one, else the last method set with chemlab, else the contract's
                                      example method), so a change needs only the keys that change.  `method` alone shows it.
    calibrate [KIND] KEY=VALUE ...    enter a calibration (size / pixel / pressure / volume / blank / sensor / detector / balance /
                                      pH / conductivity / camera / adjustment ...); data arrays as comma lists with units
    set-temp T [--ramp R] [--wait]    the cell / chamber / bath temperature, where the instrument controls it on its own
    atmosphere rh=50% flow=30mL/min gas=on|off   humidity, gas flow and the gas valve for sorption and permeation
    start [--cell N]                  start the measurement; returns job-N (chemlab status|wait|cancel job-N)
    stop [--cell N]                   stop it
    result [--cell N] [--save-to F]   the result in plain units (diameters in nm / µm, BET in m²/g, density in g/cm³, tension in
                                      mN/m, zeta in mV, angles in °), the full record with --save-to
    analyze MODEL [KEY=VALUE ...]     the instrument's own evaluation (BJH, TSI, porometry, dispersion, hysteresis, CMC, isotherm,
                                      OTR ...), fed from the last result where the record has the data; other inputs as KEY=VALUE
    status                            the class view

Method keys of the class (bare numbers in the kind's default unit; native argument names also work, a bare number then in the
argument's own declared unit):  mode=  temp=25C  time=30s  interval=8s  runs=3  mass=50mg  angle=90deg  steps=LIST (the
measuring points of the run: relative pressures, pressures, RH steps or pH targets, e.g. steps=0.05,0.1,0.2 or steps=10%,40%,80%).

Shared verbs (power, open / close of named parts, ack, estop, wait) come from the shared layer; this class redefines method,
start, stop and result because these members need it: every method.configure of the class requires all its arguments (the
shared `method` sends only the given ones), the Autosorb and the OX-TRAN start, stop and read results per station / cell
(--cell), and `result` summarises the record in plain units.

Class checks before anything is sent (from the member mapping "requires": slot value, minimum or maximum): power, the sample in
place, the access closed for measuring (every `load` step at its end position), warm-up times, calibrations and tare, the gas
valve open, the method set.  A refused native call is explained from the member's "codes", the class CODES, or the contract
preconditions with the state already read.
"""
import copy
import json
import math
import re

from ...contract import validate
from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused, unexpected
from ..jobs import OperationJob, StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt

REF = ('Anton Paar Litesizer DLS / DIA, PSA, Autosorb, PoreMaster, Ultrapyc and SurPASS manuals; Microtrac TURBISCAN LAB manual; '
       'POROLUX Revo manual; Micromeritics AutoChem III manual; SMS DVS Resolution manual; Biolin Sigma 700, Theta Flex and KSV NIMA '
       'trough manuals; AMETEK MOCON OX-TRAN 2/22 manual (operation chapters: sample loading, method, calibration, measurement, results)')

PI = math.pi

# units the shared table does not have (declared unit -> {typed unit: factor to the declared unit}); bare numbers = declared unit
LOCAL_UNITS = {
    'N/m': {'n/m': 1.0, 'mn/m': 1e-3, 'dyn/cm': 1e-3},
    'Pa*s': {'pa*s': 1.0, 'pas': 1.0, 'mpa*s': 1e-3, 'mpas': 1e-3, 'cp': 1e-3},
    'mol': {'mol': 1.0, 'mmol': 1e-3, 'umol': 1e-6, 'µmol': 1e-6, 'nmol': 1e-9},
    'V/m': {'v/m': 1.0, 'v/cm': 100.0, 'kv/m': 1000.0},
    'm^3/kg': {'m3/kg': 1.0, 'm^3/kg': 1.0, 'ml/g': 1e-3, 'cm3/g': 1e-3},
    'Pa/s': {'pa/s': 1.0, 'kpa/s': 1e3, 'mbar/s': 100.0, 'psi/s': 6894.757293168},
    'm^2/s': {'m2/s': 1.0, 'm^2/s': 1.0},
    'm^3/s^2': {'m3/s2': 1.0, 'm^3/s^2': 1.0},
    'm^2/kg': {'m2/kg': 1.0, 'm2/g': 1e3},
    'F': {'f': 1.0, 'pf': 1e-12, 'nf': 1e-9},
    'J/mol': {'j/mol': 1.0, 'kj/mol': 1e3},
    'kg/mol': {'kg/mol': 1.0, 'g/mol': 1e-3},
    'V*s': {'v*s': 1.0, 'vs': 1.0, 'mv*s': 1e-3},
    'mol/(m^2*s)': {'mol/(m^2*s)': 1.0, 'mol/m2/s': 1.0},
    'm^2/(V*s)': {'m^2/(v*s)': 1.0, 'm2/vs': 1.0},
    'J/m^2': {'j/m2': 1.0, 'mj/m2': 1e-3},
    'S/m': {'s/m': 1.0, 'ms/cm': 0.1, 'us/cm': 1e-4, 'µs/cm': 1e-4},
}

# display units of result figures: name -> (factor: shown = value * factor + offset, offset, label)
SHOW = {
    'nm': (1e9, 0, 'nm'), 'um': (1e6, 0, 'µm'), 'mm': (1e3, 0, 'mm'), 'm': (1, 0, 'm'),
    'm2/g': (1e-3, 0, 'm²/g'), 'g/cm3': (1e-3, 0, 'g/cm³'), 'cm3': (1e6, 0, 'cm³'), 'cm3/g': (1e3, 0, 'cm³/g'),
    'mN/m': (1e3, 0, 'mN/m'), 'mV': (1e3, 0, 'mV'), 'deg': (180 / PI, 0, '°'), 'umol': (1e6, 0, 'µmol'), '%': (100, 0, '%'),
    'kPa': (1e-3, 0, 'kPa'), 'C': (1, -273.15, '°C'), 'mg': (1e6, 0, 'mg'), 'mm2': (1e6, 0, 'mm²'), 'cm2': (1e4, 0, 'cm²'),
    'um/s': (1e6, 0, 'µm/s'), 'uL': (1e9, 0, 'µL'), 's': (1, 0, 's'), '': (1, 0, ''),
}

ON = ('on', 'true', 'yes', '1', 'open')
OFF = ('off', 'false', 'no', '0', 'close', 'closed')
RUNNING = ('running', 'zero_start', 'conditioning', 'testing', 'zero_end')


def fmt_num(x):
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return str(x)
    if not math.isfinite(x):
        return str(x)
    if x == 0:
        return '0'
    a = abs(x)
    if 1e-3 <= a < 1e6:
        s = '%.4g' % x
    else:
        s = '%.3e' % x
    return s


def show_value(v, how):
    if v is None:
        return 'n/a'
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return short(v, 60)
    f, off, label = SHOW.get(how or '', SHOW[''])
    return ('%s %s' % (fmt_num(v * f + off), label)).strip()


def snap(num, schema):
    """A converted number that is a contract limit or enum value up to unit round-off becomes exactly that value."""
    if not isinstance(num, (int, float)) or isinstance(num, bool) or not math.isfinite(num):
        return num
    s = schema
    if 'anyOf' in s:
        s = next((x for x in s['anyOf'] if isinstance(x, dict) and x.get('type') in ('number', 'integer')), s)
    for e in list(s.get('enum') or []) + [s.get('minimum'), s.get('maximum')]:
        if isinstance(e, (int, float)) and not isinstance(e, bool) and abs(num - e) <= 1e-9 * max(1e-12, abs(e)):
            return e
    return num


def resolve(obj, path, tv=None):
    """A value in a result record: dotted path with [i], [-1], [*] (a list over items), [key=value] (the items with that value) and
    {var} from the --cell variables."""
    if tv:
        for k, v in tv.items():
            path = path.replace('{%s}' % k, str(v))
    toks = re.findall(r'[^.\[\]]+|\[[^\]]*\]', path)

    def walk(o, ts):
        if not ts:
            return o
        t, rest = ts[0], ts[1:]
        if t.startswith('['):
            idx = t[1:-1]
            if not isinstance(o, list):
                return None
            if idx == '*':
                return [walk(x, rest) for x in o]
            if '=' in idx:                                                # [key=value]: the items whose key has that value
                fk, _, fv = idx.partition('=')
                return [walk(x, rest) for x in o if isinstance(x, dict) and str(x.get(fk)) == fv]
            try:
                i = int(idx)
                return walk(o[i], rest) if -len(o) <= i < len(o) else None
            except ValueError:
                return None
        if isinstance(o, dict):
            return walk(o.get(t), rest)
        return None
    return walk(obj, toks)


def subst(o, tv):
    """Replace "{var}" strings (whole string keeps the variable's type) in a mapping structure."""
    if isinstance(o, str):
        m = re.fullmatch(r'\{(\w+)\}', o)
        if m and m.group(1) in tv:
            return copy.deepcopy(tv[m.group(1)])
        return re.sub(r'\{(\w+)\}', lambda mm: str(tv.get(mm.group(1), mm.group(0))), o)
    if isinstance(o, dict):
        return {k: subst(v, tv) for k, v in o.items()}
    if isinstance(o, list):
        return [subst(v, tv) for v in o]
    return o


class PsJob(OperationJob):
    """An instrument operation (acquisition.start, test.start, background.start ...) with a member-specific poll interval (long
    permeation tests are polled every minute of lab time, short runs every second)."""

    def __init__(self, *a, poll_s=1.0, **kw):
        super().__init__(*a, **kw)
        self._poll = float(poll_s)

    def poll_s(self):
        return self._poll

    def poll(self, ctx):
        st, text, js = super().poll(ctx)
        res = js.get('result') if isinstance(js.get('result'), dict) else {}
        keep = [(k, v) for k, v in res.items() if not isinstance(v, (dict, list)) and not (isinstance(v, str) and len(v) > 60)]
        text = ', '.join('%s=%s' % (k, fmt_num(v) if isinstance(v, float) else v) for k, v in keep[:6])
        if js.get('error'):
            text += ' error %s' % js['error']
        return st, text, js


class ParticleSurfaceAdapter(ClassAdapter):
    CLASS = 'particle_surface'
    TITLE = 'particle, pore and surface characterisation'
    ABOUT = ('Sizing, porosity, density, sorption and interfacial instruments: load the sample into the measuring position, prepare '
             '(background, degas, wet, tare, leak check), set the method in plain units (only the keys that change), calibrate, set '
             'the temperature or the atmosphere where the instrument controls them, start a measurement as a job, read the result '
             'in plain units, and run the instrument\'s own evaluation on it.')
    STANDARDS = ('ISO 22412 (DLS), ISO 13099 (zeta potential), ISO 13320 (laser diffraction), ISO 13322-2 (dynamic image analysis), '
                 'ISO/TR 13097 (dispersion stability), ISO 9277 / ISO 15901-2 (BET, gas sorption), ISO 15901-1 (mercury porosimetry), '
                 'ASTM F316 (capillary flow porometry), ISO 12154 (gas pycnometry), ISO 19403 (contact angle), ASTM D1331 (surface '
                 'tension), ASTM D3985 (oxygen transmission rate); SiLA 2 / OPC UA LADS run model (method, start, stop, result)')
    ALWAYS = ('status',)
    UNIT_KINDS = ('temperature', 'duration', 'volume', 'mass', 'length', 'pressure', 'fraction', 'flow', 'heating_rate', 'angle')
    PATH_GROUPS = (('start', 'stop', 'result'),)
    CODES = {
        'BUSY': ('BUSY', '{inst} is busy with another operation (a measurement, a move or a programme is running).',
                 'wait for it (`chemlab run {inst} status`; `chemlab wait job-N`), or stop it (`chemlab run {inst} stop`).'),
        'NOT_RUNNING': ('NOT_RUNNING', 'nothing is running on {inst}, so there is nothing to stop.', 'see `chemlab run {inst} status`.'),
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'POWER_INTERLOCK': ('POWER_OFF', '{inst} is switched off (or not ready after switching on).',
                            'switch it on: `chemlab run {inst} power on`, then check `chemlab run {inst} status`.'),
        'NO_SAMPLE': ('NO_SAMPLE', 'there is no sample in {inst}.', 'the lab operator must put the sample in (`chemlab run {inst} unload` '
                      'opens the access for it); then `chemlab run {inst} load`.'),
        'SAMPLE_ACCESS': ('ACCESS_OPEN', 'the sample access of {inst} is open (or the holder is not in the measuring position).',
                          'close it: `chemlab run {inst} load`.'),
        'SAMPLE_ACCESS_INTERLOCK': ('ACCESS_LOCKED', 'the sample access of {inst} is locked: the lid must be released and opened first.',
                                    '`chemlab run {inst} unload` opens it in the right order.'),
        'LID_OPEN': ('ACCESS_OPEN', 'the lid of {inst} is open.', 'close it: `chemlab run {inst} load`.'),
        'CHAMBER_OPEN': ('ACCESS_OPEN', 'the measuring chamber of {inst} is open.', 'close it: `chemlab run {inst} load`.'),
        'CLOSURE_INTERLOCK': ('ORDER', '{inst} closes its access only in order (holder down before the lid).',
                              '`chemlab run {inst} load` closes everything in the right order.'),
        'LID_NOT_RELEASED': ('ORDER', 'the lid of {inst} must be released before it opens.', '`chemlab run {inst} unload` does it in order.'),
        'NOT_TARED': ('NOT_TARED', 'the balance of {inst} is not tared.', 'tare it: `chemlab run {inst} prepare`.'),
        'NO_BACKGROUND': ('NO_BACKGROUND', '{inst} has no valid background.', 'measure it: `chemlab run {inst} prepare`.'),
        'TEMPERATURE_NOT_READY': ('TEMP_NOT_READY', 'the sample of {inst} is not at the method temperature yet.',
                                  'wait until it is: `chemlab run {inst} set-temp T --wait`, or `chemlab wait 60` and retry.'),
        'FINITE_GAS_INVENTORY': ('GAS_CLOSED', 'the gas supply of {inst} is closed (or empty).',
                                 'open it: `chemlab run {inst} atmosphere gas=on`; if it is empty ask the lab operator.'),
        'FINITE_INVENTORY': ('GAS_CLOSED', 'the gas (or vapour) supply of {inst} is closed or not enough for the run.',
                             'open it: `chemlab run {inst} atmosphere gas=on`; if a supply is empty ask the lab operator.'),
        'ACQUISITION_NOT_READY': ('NOT_READY', '{inst} is not ready to measure: it needs the carrier gas flowing, the detector '
                                  'calibrated, the method set and the furnace closed.',
                                  'check `chemlab run {inst} status`; `chemlab run {inst} atmosphere gas=on`; `calibrate detector ...`.'),
        'GAS_NOT_READY': ('GAS_CLOSED', 'the gas of {inst} is not flowing.', '`chemlab run {inst} atmosphere gas=on`.'),
        'DEWAR_INTERLOCK': ('DEWAR_UP', 'the liquid-nitrogen dewar of {inst} is raised, so the sample ports cannot be opened.',
                            '`chemlab run {inst} unload --cell N` lowers it first.'),
        'HOT_INTERLOCK': ('HOT', '{inst} is still hot (or degassing).', 'wait until it has cooled (`chemlab wait 600`), then retry.'),
        'HOT_OR_FLOWING': ('HOT', 'the furnace of {inst} is hot or gas is flowing.', 'close the gas (`atmosphere gas=off`) and let it '
                           'cool (`set-temp 25C --wait`), then retry.'),
        'PRESSURIZED': ('PRESSURIZED', 'the sample cell of {inst} is under pressure.',
                        'vent it first (`chemlab call {inst} pressure.vent`, then `chemlab wait 10`).'),
        'CALIBRATION_REQUIRED': ('NOT_CALIBRATED', '{inst} needs its calibrations first.', 'see `chemlab help {inst} calibrate`.'),
        'INVALID_CALIBRATION': ('BAD_CALIBRATION', 'the calibration data are not accepted by {inst} (out of range or inconsistent).',
                                'check the values (`chemlab help {inst} calibrate`).'),
        'NO_DROP_OR_CALIBRATION': ('NO_DROP', 'there is no drop on the substrate of {inst} (or its camera is not calibrated).',
                                   'place one: `chemlab run {inst} load --volume 0.2uL`; calibrate the camera if needed.'),
        'SAMPLE_ALIGNMENT': ('NOT_ALIGNED', 'the sample stage of {inst} is not in the measuring position.', '`chemlab run {inst} load`.'),
        'CELL_NOT_CLAMPED': ('NOT_CLAMPED', 'the test cell of {inst} is not clamped.', '`chemlab run {inst} load --cell A|B`.'),
        'METHOD_REQUIRED': ('NO_METHOD', 'the cell of {inst} has no test method.', '`chemlab run {inst} method --cell A|B KEY=VALUE ...`.'),
        'TEST_ACTIVE': ('BUSY', 'a test is running on that cell of {inst}.', 'wait for it or stop it (`chemlab run {inst} stop --cell A|B`).'),
        'NO_COMPLETED_TEST': ('NO_RESULT', 'that cell of {inst} has no completed test.', 'run one first (`start --cell A|B`, then wait).'),
        'NOT_CONFIGURED': ('NO_METHOD', '{inst} has no method (or the lab has not set it up).', 'set one: `chemlab run {inst} method KEY=VALUE`.'),
        'REMOVE_SAMPLE_FIRST': ('SAMPLE_PRESENT', 'this step needs the cup of {inst} empty.', 'take the sample out first (`unload`, the operator removes it).'),
        'STABILITY_TIMEOUT': ('UNSTABLE', 'a pressure or flow point of {inst} did not become stable in time.', 'raise the dwell times in the method.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab.', 'ask the lab operator.'),
    }

    def __init__(self, instrument_id, config_entry):
        super().__init__(instrument_id, config_entry)
        self.memory = {}                               # (capability, target) -> the last method arguments sent with chemlab

    # ------------------------------------------------------------------ small helpers
    def val(self, values, slot):
        if slot is None:
            return None
        if hasattr(values, 'v'):
            return values.v(slot)
        if slot in values:
            return values[slot]
        low = {k.lower(): k for k in values}
        return values.get(low.get(slot.lower()))

    def schema_at(self, cap, path):
        """The argument schema of ``cap`` at a dotted path (``steps.0.temperature_k`` walks into array items / properties)."""
        s = {'properties': (self.lab.contract['capabilities'][cap].get('input_schema') or {}).get('properties') or {}}
        for t in path.split('.'):
            if 'anyOf' in s and 'properties' not in s and 'items' not in s:
                s = next((x for x in s['anyOf'] if isinstance(x, dict) and x.get('type') in ('object', 'array')), s)
            if t.isdigit() or t == '*':
                s = s.get('items') or {}
            else:
                s = (s.get('properties') or {}).get(t) or {}
        return s

    def required(self, cap):
        return list((self.lab.contract['capabilities'][cap].get('input_schema') or {}).get('required') or [])

    def props(self, cap):
        return (self.lab.contract['capabilities'][cap].get('input_schema') or {}).get('properties') or {}

    def example(self, cap):
        return copy.deepcopy(self.lab.contract['capabilities'][cap].get('example_arguments') or {})

    def bound(self, cap, arg, which):
        lo, hi = self.arg_range(cap, arg)
        return hi if which == 'max' else lo

    def slot_bound(self, slot, which):
        s = (self.lab.contract['state_slots'].get(slot) or {}).get('value_schema') or {}
        if 'anyOf' in s:
            s = next((x for x in s['anyOf'] if isinstance(x, dict) and x.get('type') in ('number', 'integer')), s)
        return s.get('maximum' if which == 'slot_max' else 'minimum')

    def check_args(self, ctx, cap, args, what):
        """Arguments against the contract before sending: a value outside its range is a usage error (nothing sent)."""
        probs = validate(self.lab.contract['capabilities'][cap].get('input_schema') or {'type': 'object', 'properties': {}}, args, cap)
        if probs:
            props = self.props(cap)

            def unit_of(pr):
                m = re.match(r'%s\.(\w+)' % re.escape(cap), pr)
                u = units.declared(props.get(m.group(1)) or {}) if m else None
                return ' (%s)' % u if u else ''
            ctx.fail(invalid('OUT_OF_RANGE', '%s on %s: %s.' % (what, self.instrument_id, '; '.join(pr + unit_of(pr) for pr in probs[:4])),
                             'change the value (see `chemlab help %s %s` for ranges and units).' % (self.instrument_id, cap)))

    # ------------------------------------------------------------------ value parsing
    def parse_scalar(self, text, schema, kind=None, what='value', native=False):
        """Agent text -> the value in the unit the argument declares.  ``kind``: the class key's quantity kind (bare number = the
        kind's default unit); ``native``: a native argument name (bare number = the declared unit)."""
        t = (text or '').strip()
        types = schema.get('type')
        types = set(types if isinstance(types, list) else [types] if types else [])
        if 'anyOf' in schema:
            for x in schema['anyOf']:
                if isinstance(x, dict) and x.get('type'):
                    types.add(x['type'])
        if t.lower() in ('null', 'none') and ('null' in types or not types):
            return None
        if 'boolean' in types and not ({'number', 'integer'} & types):
            if t.lower() in ON:
                return True
            if t.lower() in OFF:
                return False
            raise invalid('BAD_VALUE', '%s=%s is not on/off (true/false).' % (what, text), 'write %s=on or %s=off.' % (what, what))
        if ('string' in types and not ({'number', 'integer'} & types)) or (not types and not re.match(r'^[+-]?[\d.]', t)):
            enum = schema.get('enum')
            if enum and t not in [str(e) for e in enum]:
                close = [e for e in enum if str(e).lower() == t.lower()]
                if close:
                    return close[0]
                raise invalid('BAD_CHOICE', '%s=%s is not allowed.' % (what, text), 'use one of: %s.' % ', '.join(map(str, enum)))
            return t
        if {'array', 'object'} & types and (t.startswith('[') or t.startswith('{')):
            try:
                return json.loads(t)
            except ValueError:
                raise invalid('BAD_VALUE', '%s=%s is not valid JSON.' % (what, text), 'write a JSON list or object, or a comma list.')
        u = units.declared(schema)
        num = None
        if u is None and t.endswith('%'):                                 # a unitless fraction typed in percent (rh=40%, p/p0)
            try:
                num = float(t[:-1]) / 100.0
            except ValueError:
                raise invalid('BAD_NUMBER', '%s=%s is not a number.' % (what, text), 'e.g. %s=40%%' % what)
        elif u and units.unit_kind(u):
            m = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*', t)
            if m and native:
                num = float(m.group(1))
            else:
                num = units.parse_for(t, schema, kind, what=what)
        elif u in LOCAL_UNITS:
            m = re.fullmatch(r'\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*(\S*)\s*', t)
            if not m:
                raise invalid('BAD_NUMBER', '%s=%s is not a number with a unit.' % (what, text), 'e.g. %s=%s' % (what, '1' + u))
            f = 1.0 if not m.group(2) else LOCAL_UNITS[u].get(m.group(2).lower().replace(' ', ''))
            if f is None:
                raise invalid('UNIT_NOT_ALLOWED', '%r is not a unit for %s (declared %s).' % (m.group(2), what, u),
                              'units: %s.' % ', '.join(sorted(LOCAL_UNITS[u])))
            num = float(m.group(1)) * f
        else:
            num = units.parse_for(t, schema, None, what=what)
        bare = re.fullmatch(r'\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*', t) is not None
        if isinstance(num, float) and math.isfinite(num) and num != 0 and not (native and bare):
            num = float('%.15g' % num)                                    # unit round-off only (15deg -> rad -> deg)
        num = snap(num, schema)
        if 'integer' in types and 'number' not in types:
            if abs(num - round(num)) > 1e-9:
                raise invalid('BAD_VALUE', '%s=%s must be a whole number.' % (what, text), 'give an integer.')
            num = int(round(num))
        return num

    def parse_value(self, text, schema, kind=None, what='value', native=False):
        """Scalars, or a list (comma-separated, each with its unit, or JSON) for array arguments."""
        types = schema.get('type')
        types = set(types if isinstance(types, list) else [types] if types else [])
        if 'anyOf' in schema:
            types |= {x.get('type') for x in schema['anyOf'] if isinstance(x, dict)}
        if 'array' in types:
            t = (text or '').strip()
            if t.startswith('['):
                try:
                    return json.loads(t)
                except ValueError:
                    raise invalid('BAD_VALUE', '%s=%s is not a JSON list.' % (what, text), 'write a comma list, e.g. %s=1,2,3.' % what)
            item = schema.get('items') or next((x.get('items') for x in schema.get('anyOf') or [] if isinstance(x, dict) and x.get('items')), {}) or {}
            if item.get('type') in ('object', 'array'):
                raise invalid('BAD_VALUE', '%s needs a JSON list of %ss.' % (what, item.get('type')), "write %s='[...]' (JSON)." % what)
            return [self.parse_scalar(x, item, kind, what, native) for x in t.split(',') if x.strip()]
        return self.parse_scalar(text, schema, kind, what, native)

    def pairs(self, ctx, p):
        out = []
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write KEY=VALUE (see `chemlab help %s %s`).' % (self.instrument_id, ctx.verb.name)))
            out.append((k.strip(), v.strip()))
        return out

    # ------------------------------------------------------------------ targets (--cell)
    def cell_vars(self, ctx, p, verb):
        """--cell -> the member's variables for that cell / station ({"station": 0} / {"cell": "A", "c": "a"}); a list of stations
        for verbs that take several ("stations": [0, 1])."""
        t = (self.map.get('targets') or {}).get('cell')
        v = p.get('cell') if p is not None else None
        if not t:
            if v is not None:
                ctx.fail(invalid('NO_CELL', '%s has only one measuring position; leave out --cell.' % self.instrument_id, 'leave out --cell.'))
            return {}
        vals = t['values']
        if v is None:
            if t.get('default') is not None:
                v = str(t['default'])
            else:
                ctx.fail(invalid('MISSING_CELL', '%s %s needs --cell (%s).' % (self.instrument_id, verb, '|'.join(vals)),
                                 'give --cell %s.' % '|'.join(vals)))
        names = [x.strip() for x in str(v).split(',') if x.strip()]
        if len(names) > 1 and verb not in (t.get('multi') or []):
            ctx.fail(invalid('ONE_CELL', '%s %s takes one cell.' % (self.instrument_id, verb), 'give one of: %s.' % ', '.join(vals)))
        for n in names:
            if n not in vals:
                ctx.fail(invalid('UNKNOWN_CELL', '%s has no cell %r.' % (self.instrument_id, n), 'cells of %s: %s.' % (self.instrument_id, ', '.join(vals))))
        tv = dict(vals[names[0]])
        tv['cell_name'] = names[0]
        tv['cell_names'] = ','.join(names)
        if t.get('list_var'):
            tv[t['list_var']] = [vals[n][t.get('list_of', t['list_var'])] for n in names]
        return tv

    # ------------------------------------------------------------------ requirements
    def requirements(self, ctx, verb, tv=None):
        """The member's own preconditions for a verb: "requires": {verb: [{"slot", "value" | "not" | "min" | "max", "cell",
        "code", "message", "fix", "label"}]}; entries with "cell" apply to that --cell only."""
        tv = tv or {}
        for r in (self.map.get('requires') or {}).get(verb, []):
            if r.get('cell') is not None and str(r['cell']) != str(tv.get('cell_name')):
                continue
            got = ctx.v(r['slot'])
            if 'value' in r:
                ok = got == r['value']
            elif 'not' in r:
                ok = got not in r['not']
            elif 'min' in r:
                lim = self.slot_bound(r['slot'], r['min']) if r['min'] in ('slot_max', 'slot_min') else r['min']
                ok = finite(got) and got >= lim - 1e-9
            elif 'max' in r:
                lim = self.slot_bound(r['slot'], r['max']) if r['max'] in ('slot_max', 'slot_min') else r['max']
                ok = finite(got) and got <= lim + 1e-9
            else:
                ok = True
            fill = dict(inst=self.instrument_id, value=short(got, 40), cell=tv.get('cell_name', ''))
            ctx.check(ok, r.get('code', 'NOT_READY'), r['message'].format(**fill), r['fix'].format(**fill), label=r.get('label', r['slot']))

    def need_power(self, ctx):
        s = self.slot('power')
        if s:
            ctx.check(bool(ctx.v(s)), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                      'switch it on: `chemlab run %s power on`.' % self.instrument_id, label='power on')

    # ------------------------------------------------------------------ step lists (load / unload)
    def step_target(self, st):
        if 'to' not in st:
            return None
        to = st['to']
        if to in ('max', 'min'):
            return self.bound(st['cap'], st['arg'], to)
        return to

    @staticmethod
    def same(got, want):
        if isinstance(want, bool) or isinstance(got, bool):
            return got is want or got == want
        if finite(want) and finite(got):
            return abs(got - want) <= 1e-9 + 1e-6 * abs(want)
        return got == want

    def step_args(self, st, tv):
        args = self.example(st['cap']) if st.get('args') == 'example' else subst(dict(st.get('args') or {}), tv)
        if 'arg' in st:
            args[st['arg']] = self.step_target(st)
        return args

    def step_done(self, ctx, st):
        """True when the step's slot already shows its end value (the step is skipped)."""
        if st.get('done_args'):                                           # several slots, each equal to one of the step's arguments
            args = self.step_args(st, {})
            return all(self.same(ctx.v(sl), args.get(a)) for sl, a in st['done_args'].items())
        if st.get('slot') is None:
            return False
        want = st.get('done', self.step_target(st))
        if want in ('max', 'min'):
            want = self.bound(st['cap'], st['arg'], want)
        elif want in ('slot_max', 'slot_min'):
            want = self.slot_bound(st['slot'], want)
        return self.same(ctx.v(st['slot']), want)

    def step_applies(self, ctx, st):
        c = st.get('if')
        if not c:
            return True
        got = ctx.v(c['slot'])
        if 'value' in c:
            return got == c['value']
        if 'in' in c:
            return got in c['in']
        if 'not' in c:
            return got not in c['not']
        if 'max' in c:
            return finite(got) and got <= c['max']
        if 'min' in c:
            return finite(got) and got >= c['min']
        return True

    def step_list(self, m, tv):
        if m.get('cells'):
            sub = m['cells'].get(str(tv.get('cell_name')))
            return list((sub or {}).get('steps') or [])
        return list(m.get('steps') or [])

    def run_steps(self, ctx, steps, tv, verb):
        done = []
        for st in steps:
            if not self.step_applies(ctx, st) or self.step_done(ctx, st):
                continue
            args = self.step_args(st, tv)
            label = st.get('label') or '%s %s' % (st['cap'], short(args, 60))
            ctx.call(st['cap'], args, label)
            done.append(label)
            if st.get('wait'):
                self.wait_idle(ctx, st['wait'], label)
        return done

    def wait_idle(self, ctx, w, label):
        """An asynchronous move of the instrument: let lab time pass until it has ended (the verb says it waits)."""
        slot, busy = w.get('slot', 'acquisition.phase'), w.get('while', ['running'])
        if 'near' in w:                                                   # a pressure / position that settles to a value
            def ended(v):
                x = ctx.v(slot)
                return finite(x) and abs(x - w['near']) <= w.get('tol', 0)
        else:
            def ended(v):
                return ctx.v(slot) not in busy
        if not ended(ctx.view):
            ctx.advance(float(w.get('max_s', 60)), until=ended, step='wait for %s' % label, poll_s=float(w.get('poll_s', 0.5)))
        if not ctx.dry_run and not ended(ctx.view):
            ctx.fail(unexpected('MOVE_TIMEOUT', '%s did not finish %s in %s.' % (self.instrument_id, label, units.fmt_s(float(w.get('max_s', 60)))),
                                'check `chemlab run %s status`.' % self.instrument_id, changed='yes', done=ctx.done))
        if not ctx.dry_run and w.get('failed') and ctx.v(slot) in w['failed']:
            ctx.fail(refused('MOVE_FAILED', '%s could not %s (%s = %s).' % (self.instrument_id, label, slot, ctx.v(slot)),
                             'check `chemlab run %s status`.' % self.instrument_id))

    def settle(self, ctx, st):
        """Wait (lab time) until a reading exists and two readings ``dt`` apart differ by at most ``tol`` - a tare or zero must not
        be taken on a signal that is still settling (e.g. right after switching on)."""
        slot, tol, dt, mx = st['slot'], float(st.get('tol', 0)), float(st.get('dt', 0.5)), float(st.get('max_s', 30))
        if ctx.dry_run:
            ctx.plan.append({'capability': None, 'arguments': {'seconds': mx}, 'step': 'wait until %s is steady' % slot})
            return
        last = {'v': None}

        def steady(view):
            v = ctx.v(slot)
            ok = finite(v) and finite(last['v']) and abs(v - last['v']) <= tol
            last['v'] = v
            return ok
        steady(ctx.view)
        ctx.advance(mx, until=steady, step='wait until %s is steady' % slot, poll_s=dt)
        if not (finite(ctx.v(slot)) and finite(last['v'])):
            ctx.fail(refused('SETTLING', 'the %s reading of %s is not there yet (%s = %s).' % (st.get('word', 'signal'), self.instrument_id, slot, ctx.v(slot)),
                             'wait a few seconds after switching on (`chemlab wait 5`), then retry.'))
        ctx.checks.append('%s steady' % slot)

    def access_open(self, ctx, tv):
        """The load steps that are not at their end position (the access is open for the operator)."""
        m = self.vmap('load') or {}
        if m.get('check', True) is False:
            return []
        out = []
        for st in self.step_list(m, tv):
            if self.step_applies(ctx, st) and (st.get('slot') or st.get('done_args')) and not self.step_done(ctx, st):
                sl = st.get('slot') or next(iter(st['done_args']))
                out.append('%s = %s' % (sl, short(ctx.v(sl), 20)))
        return out

    # ------------------------------------------------------------------ summary (status, read)
    def summary(self, values):
        sl = self.map.get('slots') or {}
        lines, js = [], {}
        p = sl.get('power')
        if p:
            on = bool(self.val(values, p))
            js['power'] = on
            if not on:
                lines.append('power off')
        for ph in (sl.get('phase') if isinstance(sl.get('phase'), list) else [sl.get('phase')] if sl.get('phase') else []):
            v = self.val(values, ph)
            js[ph] = v
            stage = self.val(values, sl.get('stage')) if sl.get('stage') else None
            el = self.val(values, sl.get('elapsed')) if sl.get('elapsed') else None
            lines.append('%s: %s%s%s' % (ph.split('.')[0] if '.' in ph and ph.split('.')[0] != 'acquisition' else 'measurement', v,
                                          (' (%s)' % stage) if stage and v == 'running' else '',
                                          (', %s elapsed' % units.fmt_s(el)) if finite(el) and v == 'running' else ''))
        sam = sl.get('sample')
        for s in (sam if isinstance(sam, list) else [sam] if sam else []):
            js[s] = self.val(values, s)
            lines.append('%s (%s)' % ('in place' if self.val(values, s) else 'none', s) if isinstance(sam, list)
                         else 'sample: %s' % ('in place' if self.val(values, s) else 'none'))
        t, setp = sl.get('temp'), sl.get('set')
        if t:
            tk, sk = self.val(values, t), self.val(values, setp) if setp else None
            js['temperature_k'], js['set_point_k'] = tk, sk
            lines.append('temperature %s%s' % (show_value(tk, 'C'), (' (set point %s)' % show_value(sk, 'C')) if sk is not None else ''))
        for sh in self.map.get('show') or []:
            v = self.val(values, sh['slot'])
            js[sh['slot']] = v
            lines.append('%s: %s' % (sh.get('label', sh['slot']), show_value(v, sh.get('show'))))
        return (lines or ['(see `chemlab read %s`)' % self.instrument_id]), js

    def _h_status(self, ctx, p):
        lines, js = self.summary(ctx.view)
        acc = self.access_open(ctx, {}) if self.vmap('load') and not (self.vmap('load') or {}).get('cells') else []
        if self.vmap('load') and not (self.vmap('load') or {}).get('cells') and (self.vmap('load') or {}).get('check', True) is not False:
            lines.append('sample access: %s' % ('closed for measuring' if not acc else 'open (%s; `load` closes it)' % ', '.join(acc[:3])))
            js['access_closed'] = not acc
        head = '%s (%s) at t = %.1f s' % (self.instrument_id, self.TITLE, ctx.now())
        return {'text': '\n'.join([head] + ['  ' + x for x in lines]) + '\n', 'json': {'state': js}}

    # ------------------------------------------------------------------ help with this member's keys
    def member_help(self, name):
        """Lines for `chemlab help INST VERB`: what the verb takes on this member (keys, kinds of calibration, evaluations, steps)."""
        m = self.vmap(name) or {}
        out = []

        def arg_desc(cap, a):
            sch = self.schema_at(cap, a) if '.' in a else (self.props(cap).get(a) or {})
            u = units.declared(sch) or (units.declared(sch.get('items') or {}) if sch.get('type') == 'array' else None)
            return '%s%s' % (a, (' [%s%s]' % ('list of ' if sch.get('type') == 'array' else '', u)) if u else '')
        if name == 'method' and m:
            out.append('keys on %s: %s' % (self.instrument_id, ', '.join('%s= -> %s' % (k, arg_desc(m['cap'], v.get('arg') or v['path']))
                                                                     for k, v in sorted((m.get('keys') or {}).items())) or 'none'))
            if m.get('extra'):
                out.append('separate settings: %s' % ', '.join('%s= -> %s %s' % (k, v['cap'], v['arg']) for k, v in sorted(m['extra'].items())))
            out.append('native names (%s): %s' % (m['cap'], ', '.join(arg_desc(m['cap'], a) for a in self.props(m['cap']))))
            if (self.map.get('targets') or {}).get('cell'):
                out.append('--cell %s selects the %s' % ('|'.join(self.map['targets']['cell']['values']), 'station / cell'))
        elif name == 'calibrate' and m:
            for kind, k in (m.get('kinds') or {}).items():
                al = k.get('keys') or {}
                inv = {v: kk for kk, v in al.items()}
                need = [a for a in self.required(k['cap']) if a not in (k.get('args') or {})]
                out.append('calibrate %s (%s%s): %s' % (kind, k['cap'], ', per --cell' if k.get('per_cell') else '',
                                                        ', '.join('%s= (%s)' % (inv.get(a, a), arg_desc(k['cap'], a)) for a in need)))
        elif name == 'analyze' and m:
            for model, sp in (m.get('models') or {}).items():
                fr = sp.get('from_result') or {}
                al = sp.get('keys') or {}
                inv = {v: kk for kk, v in al.items()}
                give = [a for a in self.required(sp['cap']) if a not in fr and a not in (sp.get('defaults') or {})]
                out.append('analyze %s (%s): from the last result: %s; you give: %s%s' % (
                    model, sp['cap'], ', '.join(sorted(fr)) or 'nothing', ', '.join('%s=' % inv.get(a, a) for a in give) or 'nothing',
                    ('; defaults: %s' % ', '.join('%s=%s' % kv for kv in sp['defaults'].items())) if sp.get('defaults') else ''))
        elif name == 'atmosphere' and m:
            out.append('keys on %s: %s' % (self.instrument_id, ', '.join('%s= -> %s %s' % (k, v['cap'], v['arg']) for k, v in (m.get('keys') or {}).items())))
        elif name in ('load', 'unload') and m:
            if m.get('cells'):
                for c, sub in m['cells'].items():
                    out.append('--cell %s: %s' % (c, '; '.join(st.get('label', st['cap']) for st in sub.get('steps') or [])))
            elif m.get('steps'):
                out.append('steps on %s: %s' % (self.instrument_id, '; '.join(st.get('label', st['cap']) for st in m['steps'])))
            if m.get('deposit'):
                out.append('then: %s --volume (%s)' % (m['deposit'].get('word', 'deposit'), m['deposit']['cap']))
        elif name == 'prepare' and m:
            out.append('on %s: %s (%s)%s' % (self.instrument_id, m.get('word', m['cap']), m['cap'],
                                             ', a job' if m.get('state') or (self.lab.contract['capabilities'][m['cap']].get('execution') or {}).get('mode') != 'synchronous' else ''))
        for r in (self.map.get('requires') or {}).get(name, []):
            lab = r.get('label', r['slot'])
            out.append('checks: %s%s' % (lab, (' (cell %s)' % r['cell']) if r.get('cell') and ('cell %s' % r['cell']) not in lab else ''))
        return out

    def verb_help(self, name):
        r = ClassAdapter.verb_help(self, name)
        extra = self.member_help(name) if name not in self.unsupported_reasons() else []
        if extra:
            r['text'] = r['text'].rstrip('\n') + '\n\non this instrument:\n' + '\n'.join('  ' + x for x in extra) + '\n'
            r['json']['member'] = extra
        return r

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        cell = Opt('which cell / station (instruments with several measuring positions)', metavar='CELL')
        return {
            'load': Verb('load', 'bring the sample into the measuring position and close the access (or deposit it: drop, film)',
                         self.h_load, options={'cell': cell, 'volume': Opt('drop / spreading volume (contact-angle meter, trough)',
                                                                            kind='volume', metavar='VOLUME')},
                         preconditions=['power on where the instrument needs it for its moving parts', 'the order of the instrument '
                                        '(holder before lid, cup before locks)'],
                         errors=['ORDER, ACCESS_LOCKED, BUSY (refused)'], reference=REF, example='chemlab run INST load',
                         details='Steps already at their end position are skipped; the reply lists what moved.'),
            'unload': Verb('unload', 'open the access / bring the holder out so the operator can put in or take out a sample', self.h_unload,
                           options={'cell': cell}, errors=['BUSY, HOT, DEWAR_UP (refused)'], reference=REF, example='chemlab run INST unload'),
            'prepare': Verb('prepare', 'the step this instrument needs before a measurement (background, degas, wetting, tare, leak check)',
                            self.h_prepare, long_action=True,
                            options={'temp': Opt('degas temperature (degassing instruments)', kind='temperature', metavar='TEMP'),
                                     'hold': Opt('degas hold time', kind='duration', metavar='TIME'),
                                     'ramp': Opt('degas heating rate', kind='heating_rate', metavar='RATE')},
                            preconditions=['power on', 'the member\'s own conditions (liquid filled, plate in the subphase, ...)'],
                            errors=['POWER_OFF, NOT_READY (refused)'], reference=REF, example='chemlab run INST prepare'),
            'method': Verb('method', 'set the measurement method: KEY=VALUE ... (only the keys that change; alone: show it)', self.h_method,
                           variadic=('KEY=VALUE', 'a setting'),
                           options={'cell': cell, 'show': Opt('show the method that is in effect (nothing is sent)', kind='flag')},
                           preconditions=['no measurement running', 'values within the contract ranges (checked by the instrument)'],
                           errors=['UNKNOWN_SETTING, BAD_VALUE (usage)', 'BUSY (refused)'], reference=REF,
                           details=('class keys: mode=  temp=  time=  interval=  runs=  mass=  angle=  steps=LIST (relative pressures, '
                                    'pressures, RH steps or pH targets); any native argument name of the method capability also works '
                                    '(a bare number is then in its declared unit).'),
                           example='chemlab run INST method temp=25C time=30s'),
            'calibrate': Verb('calibrate', 'enter a calibration: [KIND] KEY=VALUE ... (reference / measured data, lists as a,b,c)',
                              self.h_calibrate, positionals=[Arg('KIND', 'which calibration (see `chemlab help INST calibrate`)', required=False)],
                              variadic=('KEY=VALUE', 'calibration data'), options={'cell': cell},
                              errors=['MISSING_SETTING (usage)', 'BAD_CALIBRATION (refused)'], reference=REF,
                              example='chemlab run INST calibrate size reference=100nm measured=102nm'),
            'set-temp': Verb('set-temp', 'set the cell / chamber temperature', self.h_set_temp,
                             positionals=[Arg('TEMP', 'set point, e.g. 25C', kind='temperature')],
                             options={'ramp': Opt('heating rate (furnaces that ramp)', kind='heating_rate', metavar='RATE'),
                                      'wait': Opt('then wait until the temperature is there', kind='flag'),
                                      'max': Opt('with --wait: the longest wait (default 1 h)', kind='duration', metavar='TIME')},
                             preconditions=['set point within the instrument range (contract)', 'power on'],
                             errors=['OUT_OF_RANGE (usage)', 'POWER_OFF (refused)', 'NOT_REACHED (refused, with --wait)'], reference=REF,
                             example='chemlab run INST set-temp 25C --wait'),
            'atmosphere': Verb('atmosphere', 'humidity, gas flow and gas valve: rh=50% flow=30mL/min gas=on|off', self.h_atmosphere,
                               variadic=('KEY=VALUE', 'rh=, flow=, gas='), preconditions=['power on', 'values within the instrument range'],
                               errors=['OUT_OF_RANGE, UNKNOWN_SETTING (usage)', 'POWER_OFF (refused)'], reference=REF,
                               example='chemlab run INST atmosphere rh=40% gas=on'),
            'start': Verb('start', 'start the measurement (returns job-N: chemlab status|wait|cancel job-N)', self.h_start, long_action=True,
                          options={'cell': cell},
                          preconditions=['power on', 'sample in place', 'access closed for measuring (`load`)', 'the member\'s own '
                                         'conditions: warm-up, background, tare, calibration, gas, method'],
                          errors=['POWER_OFF, NO_SAMPLE, ACCESS_OPEN, WARMING_UP, NOT_TARED, NO_BACKGROUND, GAS_CLOSED, ... (refused)'],
                          reference=REF, example='chemlab run INST start'),
            'stop': Verb('stop', 'stop the running measurement', self.h_stop, options={'cell': cell}, errors=['NOT_RUNNING (refused)'],
                         reference=REF, example='chemlab run INST stop'),
            'result': Verb('result', 'the result of the last measurement in plain units (full record: --save-to FILE)', self.h_result,
                           readonly=True, options={'cell': cell, 'save-to': save_to_opt()}, reference=REF,
                           example='chemlab run INST result --save-to result.json'),
            'analyze': Verb('analyze', 'the instrument\'s own evaluation of the last result: MODEL [KEY=VALUE ...]', self.h_analyze,
                            readonly=True, positionals=[Arg('MODEL', 'which evaluation (see `chemlab help INST analyze`)', required=False)],
                            variadic=('KEY=VALUE', 'inputs the result does not hold'), options={'cell': cell, 'save-to': save_to_opt()},
                            errors=['MISSING_SETTING (usage)', 'NO_RESULT (refused)'], reference=REF,
                            example='chemlab run INST analyze bjh --save-to bjh.json'),
        }

    # ------------------------------------------------------------------ load / unload
    def h_load(self, ctx, p):
        m = self.vmap('load')
        tv = self.cell_vars(ctx, p, 'load')
        self.requirements(ctx, 'load', tv)
        steps = self.step_list(m, tv)
        if p.get('volume') is not None and not m.get('deposit'):
            ctx.fail(invalid('NO_VOLUME', '%s load takes no --volume (the operator places the sample).' % self.instrument_id, 'leave out --volume.'))
        dep = m.get('deposit')
        vol = None
        if dep:                                                           # the volume is checked before anything moves
            vol = p.get('volume')
            if vol is None:
                vol = units.from_declared(self.example(dep['cap']).get(dep['arg']), self.arg_unit(dep['cap'], dep['arg']))
            lo, hi = self.arg_range_si(dep['cap'], dep['arg'])
            if not finite(vol) or vol <= 0 or (hi is not None and vol > hi * (1 + 1e-9)):
                ctx.fail(invalid('OUT_OF_RANGE', 'volume %s is outside 0..%s on %s.' % (units.fmt(vol, 'volume'), units.fmt(hi, 'volume'), self.instrument_id),
                                 'give --volume up to %s.' % units.fmt(hi, 'volume')))
        done = self.run_steps(ctx, steps, tv, 'load')
        if dep:
            done += self.run_steps(ctx, dep.get('before') or [], tv, 'load')
            label = '%s %s' % (dep.get('word', 'deposit'), units.fmt(vol, 'volume'))
            dargs = {dep['arg']: snap(self.arg_value(dep['cap'], dep['arg'], vol), self.cap_schema(dep['cap'], dep['arg']))}
            self.check_args(ctx, dep['cap'], dargs, 'load --volume')
            ctx.call(dep['cap'], dargs, label)
            done.append(label)
            if dep.get('wait'):
                self.wait_idle(ctx, dep['wait'], label)
            done += self.run_steps(ctx, dep.get('after') or [], tv, 'load')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        text = 'ok: %s loaded%s: %s\n' % (self.instrument_id, (' (cell %s)' % tv['cell_name']) if tv else '',
                                          '; '.join(done) if done else 'already in the measuring position')
        return {'text': text, 'json': {'steps': done, 'cell': tv.get('cell_name')}}

    def h_unload(self, ctx, p):
        m = self.vmap('unload')
        tv = self.cell_vars(ctx, p, 'unload')
        self.requirements(ctx, 'unload', tv)
        done = self.run_steps(ctx, self.step_list(m, tv), tv, 'unload')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        text = 'ok: %s open for the operator%s: %s\n' % (self.instrument_id, (' (cell %s)' % tv['cell_name']) if tv else '',
                                                         '; '.join(done) if done else 'it was already open')
        return {'text': text, 'json': {'steps': done, 'cell': tv.get('cell_name')}}

    # ------------------------------------------------------------------ prepare
    def h_prepare(self, ctx, p):
        m = self.vmap('prepare')
        self.need_power(ctx)
        self.requirements(ctx, 'prepare')
        opts = m.get('options') or {}
        for o in ('temp', 'hold', 'ramp'):
            if p.get(o) is not None and o not in opts:
                ctx.fail(invalid('UNKNOWN_OPTION', 'prepare on %s takes no --%s.' % (self.instrument_id, o),
                                 'options here: %s.' % (', '.join('--' + x for x in opts) or 'none')))
        done = self.run_steps(ctx, m.get('steps') or [], {}, 'prepare')
        if m.get('settle'):
            self.settle(ctx, m['settle'])
        args = self.example(m['cap']) if m.get('use_example') else dict(m.get('args') or {})
        for o, path in opts.items():
            if p.get(o) is None:
                continue
            sch = self.schema_at(m['cap'], path)
            u = units.declared(sch)
            v = units.to_declared(p.get(o), u) if u else p.get(o)
            cur = args
            ks = path.split('.')
            for k in ks[:-1]:
                cur = cur[int(k)] if k.isdigit() else cur[k]
            cur[int(ks[-1]) if ks[-1].isdigit() else ks[-1]] = round(v, 10)
        self.check_args(ctx, m['cap'], args, 'prepare')
        r = ctx.call(m['cap'], args, m.get('word', 'prepare (%s)' % m['cap']))
        done.append(m.get('word', m['cap']))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = None
        if r.get('operation_id'):
            job = PsJob(ctx.core, self, ctx.req, '%s %s' % (self.instrument_id, m.get('word', m['cap'])), m['cap'], r['operation_id'],
                        poll_s=float(m.get('poll_s', 1.0)))
        elif m.get('state'):                                            # a programme whose progress is only in a slot (degassing)
            st = m['state']
            ad = self

            def state(c):
                v = c.v(st['slot'])
                word = 'succeeded' if v in st.get('done', []) else 'failed' if v in st.get('failed', []) else 'running'
                return word, '%s = %s' % (st['slot'], v), {'state': v}
            def stop_call(c):
                c.call(st['stop_cap'], {}, 'stop %s' % m.get('word', m['cap']))
                return 'stopped with %s' % st['stop_cap']
            job = StateJob(ctx.core, ad, ctx.req, '%s %s' % (self.instrument_id, m.get('word', m['cap'])), state,
                           stop=stop_call if st.get('stop_cap') else None,
                           max_s=float(st.get('max_s', 86400)), poll_s=float(m.get('poll_s', 5.0)))
        if job is not None:
            return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
                job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'steps': done}}
        return {'text': 'ok: %s %s%s\n' % (self.instrument_id, '; '.join(done), ('; ' + short(r.get('result'), 120)) if r.get('result') else ''),
                'json': {'steps': done, 'result': r.get('result')}}

    # ------------------------------------------------------------------ method
    def method_base(self, ctx, cap, tv, cur_slot=None):
        """The method in effect: the instrument's own method display (a slot), else the last one sent with chemlab, else the
        contract's example method."""
        key = (cap, tv.get('cell_name'))
        src = 'example'
        base = None
        if cur_slot:
            c = ctx.v(cur_slot)
            if isinstance(c, dict) and c:
                base, src = {k: v for k, v in c.items() if k in self.props(cap)}, 'instrument'
        if base is None and key in self.memory:
            base, src = copy.deepcopy(self.memory[key]), 'last set'
        if base is None:
            base = self.example(cap)
        return base, src

    def h_method(self, ctx, p):
        m = self.vmap('method')
        tv = self.cell_vars(ctx, p, 'method')
        cap = m['cap']
        props = self.props(cap)
        keys = m.get('keys') or {}
        extra = m.get('extra') or {}
        base, src = self.method_base(ctx, cap, tv, m.get('current'))
        fixed = subst(dict(m.get('args') or {}), tv)
        given, extra_given = {}, {}
        for k, text in self.pairs(ctx, p):
            if k in extra:
                e = extra[k]
                sch = self.props(e['cap']).get(e['arg']) or {}
                extra_given.setdefault(e['cap'], {})[e['arg']] = self.parse_value(text, sch, e.get('kind'), k)
                continue
            if k in keys:                                                 # "arg": a native argument; "path": a key inside one
                native, kind, nat = keys[k].get('arg') or keys[k]['path'], keys[k].get('kind'), False
            elif k in props:
                native, kind, nat = k, None, True
            else:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (self.instrument_id, k),
                                 'class keys here: %s; native: %s.' % (', '.join(sorted(set(keys) | set(extra))) or 'none',
                                                                      ', '.join(sorted(a for a in props if a not in fixed)))))
            if native in fixed:
                ctx.fail(invalid('FIXED_SETTING', '%s is set by --cell on %s.' % (native, self.instrument_id), 'use --cell instead.'))
            sch = self.schema_at(cap, native) if '.' in native else (props.get(native) or {})
            given[native] = self.parse_value(text, sch, kind, k, native=nat)
        args = copy.deepcopy(base)
        for native, v in given.items():
            if '.' in native:                                             # a key inside a structured argument (a programme segment)
                cur, ks = args, native.split('.')
                for kk in ks[:-1]:
                    cur = cur[int(kk)] if kk.isdigit() else cur.setdefault(kk, {})
                cur[int(ks[-1]) if ks[-1].isdigit() else ks[-1]] = v
            else:
                args[native] = v
        args.update(fixed)
        args = {k: v for k, v in args.items() if k in props}
        if p.get('show') or not (given or extra_given):
            lines = ['%s method (%s%s; nothing sent):' % (self.instrument_id, {'example': 'the contract\'s example method - not set with chemlab yet',
                                                                                'last set': 'as last set with chemlab',
                                                                                'instrument': 'as the instrument shows it'}[src],
                                                         (', cell %s' % tv['cell_name']) if tv else '')]
            for k in props:
                if k in args:
                    lines.append('  %-30s %s%s' % (k, short(args[k], 80), ('  (%s)' % units.declared(props[k])) if units.declared(props[k]) else ''))
            lines.append('class keys: %s' % (', '.join('%s=%s' % (k, v.get('arg') or v.get('path')) for k, v in sorted(keys.items())) or 'none'))
            if extra:
                lines.append('also: %s' % ', '.join('%s= (%s)' % (k, v['cap']) for k, v in sorted(extra.items())))
            return {'text': '\n'.join(lines) + '\n', 'json': {'method': args, 'source': src, 'sent': False}}
        missing = [a for a in self.required(cap) if a not in args]
        if missing:
            ctx.fail(invalid('MISSING_SETTING', 'the method of %s also needs %s.' % (self.instrument_id, ', '.join(missing)),
                             'give them as KEY=VALUE (see `chemlab help %s method`).' % self.instrument_id))
        self.check_args(ctx, cap, args, 'the method')
        self.requirements(ctx, 'method', tv)
        for xcap, xargs in extra_given.items():                          # separate settings (sample mass, probe, penetrometer)
            spec = next(e for e in extra.values() if e['cap'] == xcap)
            full = dict(subst(dict(spec.get('args') or {}), tv))
            if spec.get('merge'):
                b, _ = self.method_base(ctx, xcap, tv)
                full.update(b)
            full.update(xargs)
            self.check_args(ctx, xcap, full, 'the setting')
            ctx.call(xcap, full, 'set %s' % ', '.join('%s=%s' % (k, short(v, 30)) for k, v in xargs.items()))
        for sub in m.get('also') or []:                                  # an instrument-wide settings call that must exist once
            if sub.get('unless') and ctx.v(sub['unless']):
                continue
            b, _ = self.method_base(ctx, sub['cap'], {})
            ctx.call(sub['cap'], b, 'instrument settings (%s, %s)' % (sub['cap'], 'never set before'))
            if not ctx.dry_run:
                self.memory[(sub['cap'], None)] = copy.deepcopy(b)
        if given:                                                         # only separate settings given: the method stays as it is
            shown = ', '.join('%s=%s' % (k, short(v, 30)) for k, v in list(given.items()) + list(fixed.items()))
            ctx.call(cap, args, 'set the method (%s)' % shown)
        if not ctx.dry_run and given:
            self.memory[(cap, tv.get('cell_name'))] = copy.deepcopy(args)
        if not ctx.dry_run:
            for xcap, xargs in extra_given.items():
                self.memory.setdefault((xcap, tv.get('cell_name')), {}).update(xargs)
        else:
            return {'text': '', 'json': {}}
        parts = ['%s=%s' % (k, short(v, 40)) for k, v in given.items()] + \
                ['%s=%s (%s)' % (k, short(v, 40), xc) for xc, xa in extra_given.items() for k, v in xa.items()]
        text = 'ok: method of %s%s set: %s\n%s' % (
            self.instrument_id, (' cell %s' % tv['cell_name']) if tv else '', ', '.join(parts),
            '  (other keys kept from %s)\n' % {'example': 'the contract\'s example method', 'last set': 'the method last set',
                                               'instrument': 'the instrument\'s method'}[src] if given else '')
        return {'text': text, 'json': {'arguments': args, 'given': given, 'extra': extra_given, 'base': src}}

    # ------------------------------------------------------------------ calibrate
    def h_calibrate(self, ctx, p):
        m = self.vmap('calibrate')
        kinds = m.get('kinds') or {}
        kind = p.pos[0] if p.pos else None
        if kind is not None and '=' in kind:                              # the first KEY=VALUE, not a kind
            p.extra.insert(0, kind)
            kind = None
        if kind is None:
            if len(kinds) == 1:
                kind = next(iter(kinds))
            else:
                ctx.fail(invalid('WHICH_CALIBRATION', '%s has several calibrations: %s.' % (self.instrument_id, ', '.join(kinds)),
                                 'name one, e.g. `chemlab run %s calibrate %s KEY=VALUE ...` (see `chemlab help %s calibrate`).' % (
                                     self.instrument_id, next(iter(kinds)), self.instrument_id)))
        if kind not in kinds:
            ctx.fail(invalid('UNKNOWN_CALIBRATION', '%s has no calibration %r.' % (self.instrument_id, kind),
                             'calibrations of %s: %s.' % (self.instrument_id, ', '.join(kinds))))
        k = kinds[kind]
        cap = k['cap']
        if not k.get('per_cell') and p.get('cell') is not None:
            ctx.fail(invalid('NO_CELL', 'calibrate %s on %s is for the whole instrument; leave out --cell.' % (kind, self.instrument_id), 'leave out --cell.'))
        tv = self.cell_vars(ctx, p, 'calibrate') if k.get('per_cell') else {}
        props = self.props(cap)
        aliases = k.get('keys') or {}
        fixed = k.get('args') or {}
        args = subst(dict(fixed), tv)
        for key, text in self.pairs(ctx, p):
            native = aliases.get(key, key)
            if native not in props or native in fixed:
                ctx.fail(invalid('UNKNOWN_SETTING', 'calibrate %s on %s has no %r.' % (kind, self.instrument_id, key),
                                 'its data: %s.' % ', '.join(sorted(set(aliases) | {a for a in props if a not in fixed}))))
            args[native] = self.parse_value(text, props[native], None, key, native=(key == native))
        missing = [a for a in self.required(cap) if a not in args]
        if missing:
            inv = {v: kk for kk, v in aliases.items()}
            ctx.fail(invalid('MISSING_SETTING', 'calibrate %s on %s needs %s.' % (kind, self.instrument_id, ', '.join(
                '%s=%s' % (inv.get(a, a), units.declared(props.get(a) or {}) or (props.get(a) or {}).get('type', 'value')) for a in missing)),
                'see `chemlab help %s calibrate` (lists as a,b,c with units).' % self.instrument_id))
        self.check_args(ctx, cap, args, 'calibrate %s' % kind)
        self.requirements(ctx, 'calibrate', tv)
        r = ctx.call(cap, args, 'calibrate %s (%s)' % (kind, cap))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': 'ok: %s calibration of %s%s entered: %s\n' % (kind, self.instrument_id, (' cell %s' % tv['cell_name']) if tv else '',
                                                                    short(r.get('result'), 200)),
                'json': {'kind': kind, 'arguments': args, 'result': r.get('result')}}

    # ------------------------------------------------------------------ set-temp
    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        lo, hi = self.arg_range_si(m['cap'], m['arg'])
        if (lo is not None and k < lo - 1e-9) or (hi is not None and k > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'set point %s is outside the range of %s (%s to %s).' % (
                show_value(k, 'C'), self.instrument_id, show_value(lo, 'C'), show_value(hi, 'C')),
                'give a set point from %s to %s.' % (show_value(lo, 'C'), show_value(hi, 'C'))))
        ctx.checks.append('set point within %s..%s' % (show_value(lo, 'C'), show_value(hi, 'C')))
        if p.get('ramp') is not None and not m.get('ramp_arg'):
            ctx.fail(invalid('NO_RAMP', '%s takes no heating rate.' % self.instrument_id, 'leave out --ramp.'))
        self.need_power(ctx)
        self.requirements(ctx, 'set-temp')
        args = {m['arg']: snap(round(self.arg_value(m['cap'], m['arg'], k), 10), self.cap_schema(m['cap'], m['arg']))}
        if m.get('ramp_arg'):
            r = p.get('ramp')
            if r is None:
                r = self.slot_si(ctx, self.slot('ramp'))
            if r is None:
                r = units.from_declared(self.example(m['cap']).get(m['ramp_arg']), self.arg_unit(m['cap'], m['ramp_arg']))
            rlo, rhi = self.arg_range_si(m['cap'], m['ramp_arg'])
            if (rlo is not None and r < rlo * (1 - 1e-9)) or (rhi is not None and r > rhi * (1 + 1e-9)):
                ctx.fail(invalid('OUT_OF_RANGE', 'heating rate %s is outside %s..%s.' % (units.fmt(r, 'heating_rate'), units.fmt(rlo, 'heating_rate'),
                                                                                        units.fmt(rhi, 'heating_rate')),
                                 'give --ramp from %s to %s.' % (units.fmt(rlo, 'heating_rate'), units.fmt(rhi, 'heating_rate'))))
            args[m['ramp_arg']] = snap(self.arg_value(m['cap'], m['ramp_arg'], r), self.cap_schema(m['cap'], m['ramp_arg']))
        self.check_args(ctx, m['cap'], args, 'set-temp')
        before = self.slot_si(ctx, self.slot('set'))
        ctx.call(m['cap'], args, 'set the temperature to %s' % show_value(k, 'C'))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        tk = self.slot_si(ctx, self.slot('temp'))
        text = 'ok: %s set point %s (was %s); now %s\n' % (self.instrument_id, show_value(k, 'C'), show_value(before, 'C'), show_value(tk, 'C'))
        js = {'set_point_k': k, 'was_k': before, 'temperature_k': tk}
        if p.get('wait'):
            tol = float(self.param('tolerance_k', 0.5))
            mx = p.get('max') or 3600.0
            t0 = ctx.now()
            ok = lambda v: finite(self.slot_si(ctx, self.slot('temp'))) and abs(self.slot_si(ctx, self.slot('temp')) - k) <= tol + 1e-9
            if not ok(ctx.view):
                ctx.advance(mx, until=ok, step='wait until %s ± %g K' % (show_value(k, 'C'), tol), poll_s=float(self.param('poll_s', 2.0)))
            tk = self.slot_si(ctx, self.slot('temp'))
            if not ok(ctx.view):
                ctx.fail(refused('NOT_REACHED', '%s is at %s after %s; %s ± %g K was not reached.' % (
                    self.instrument_id, show_value(tk, 'C'), units.fmt_s(ctx.now() - t0), show_value(k, 'C'), tol),
                    'wait longer (`chemlab run %s set-temp %s --wait --max 2h`) or check `chemlab run %s status`.' % (
                        self.instrument_id, p.raw.get('TEMP'), self.instrument_id)))
            text += 'reached %s after %s of waiting\n' % (show_value(tk, 'C'), units.fmt_s(ctx.now() - t0))
            js.update({'reached': True, 'temperature_k': tk})
        return {'text': text, 'json': js}

    # ------------------------------------------------------------------ atmosphere
    def h_atmosphere(self, ctx, p):
        m = self.vmap('atmosphere')
        keys = m.get('keys') or {}
        pairs = self.pairs(ctx, p)
        if not pairs:
            ctx.fail(invalid('MISSING_SETTING', 'atmosphere needs KEY=VALUE.', 'keys on %s: %s.' % (self.instrument_id, ', '.join(keys))))
        calls = []
        for k, text in pairs:
            if k not in keys:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no atmosphere setting %r.' % (self.instrument_id, k),
                                 'settings on %s: %s.' % (self.instrument_id, ', '.join(keys) or 'none')))
            spec = keys[k]
            sch = self.props(spec['cap']).get(spec['arg']) or {}
            if spec.get('kind') == 'switch':
                v = self.parse_scalar(text, {'type': 'boolean'}, None, k)
                order = 0 if not v else 9
                calls.append((order, spec, {spec['arg']: v}, '%s %s' % (spec.get('word', 'gas'), 'on' if v else 'off')))
                continue
            si = units.parse(text, spec['kind'], what=k)
            lo, hi = self.arg_range_si(spec['cap'], spec['arg'])
            if (lo is not None and si < lo - 1e-12) or (hi is not None and si > hi * (1 + 1e-9) + 1e-15):
                ctx.fail(invalid('OUT_OF_RANGE', '%s=%s is outside %s..%s on %s.' % (k, text, units.fmt(lo, spec['kind']), units.fmt(hi, spec['kind']),
                                                                                    self.instrument_id),
                                 'give %s from %s to %s.' % (k, units.fmt(lo, spec['kind']), units.fmt(hi, spec['kind']))))
            args = dict(spec.get('args') or {})
            args[spec['arg']] = snap(self.arg_value(spec['cap'], spec['arg'], si), sch)
            self.check_args(ctx, spec['cap'], args, 'atmosphere %s' % k)
            calls.append((5, spec, args, 'set %s to %s' % (k, units.fmt(si, spec['kind']))))
        self.need_power(ctx)
        self.requirements(ctx, 'atmosphere')
        done = []
        for _, spec, args, label in sorted(calls, key=lambda c: c[0]):
            ctx.call(spec['cap'], args, label)
            done.append(label)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': 'ok: %s %s\n' % (self.instrument_id, '; '.join(done)), 'json': {'steps': done}}

    # ------------------------------------------------------------------ start / stop / result
    def h_start(self, ctx, p):
        m = self.vmap('start')
        tv = self.cell_vars(ctx, p, 'start')
        self.need_power(ctx)
        acc = self.access_open(ctx, tv)
        ctx.check(not acc, 'ACCESS_OPEN', 'the sample access of %s is not closed for measuring (%s).' % (self.instrument_id, ', '.join(acc[:4])),
                  'close it: `chemlab run %s load%s`.' % (self.instrument_id, (' --cell %s' % tv['cell_name']) if tv else ''),
                  label='access closed for measuring')
        self.requirements(ctx, 'start', tv)
        args = subst(dict(m.get('args') or {}), tv)
        r = ctx.call(m['cap'], args, 'start the measurement (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        if not r.get('operation_id'):
            return {'text': 'ok: %s started (%s)\n' % (self.instrument_id, r.get('status')), 'json': {'status': r.get('status')}}
        job = PsJob(ctx.core, self, ctx.req, '%s measurement%s' % (self.instrument_id, (' cell %s' % tv['cell_names']) if tv else ''),
                    m['cap'], r['operation_id'], poll_s=float(m.get('poll_s', 1.0)))
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s; then `chemlab run %s result%s`\n' % (
            job.handle, job.description, job.handle, job.handle, job.handle, self.instrument_id, (' --cell %s' % tv['cell_name']) if tv else ''),
            'json': {'job': job.handle, 'kind': job.kind, 'cell': tv.get('cell_name')}}

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        tv = self.cell_vars(ctx, p, 'stop')
        self.requirements(ctx, 'stop', tv)
        ctx.call(m['cap'], subst(dict(m.get('args') or {}), tv), 'stop the measurement (%s)' % m['cap'])
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        return {'text': 'ok: %s stopped%s\n' % (self.instrument_id, (' (cell %s)' % tv['cell_name']) if tv else ''), 'json': {'cell': tv.get('cell_name')}}

    def figures(self, res, tv):
        out = []
        for f in (self.vmap('result') or {}).get('figures') or []:
            v = resolve(res, f['path'], tv)
            path = f['path']
            for k, x in (tv or {}).items():
                path = path.replace('{%s}' % k, str(x))
            out.append((f.get('label', f['path']), v, show_value(v, f.get('show')), path))
        return out

    def h_result(self, ctx, p):
        m = self.vmap('result')
        tv = self.cell_vars(ctx, p, 'result')
        r = ctx.call(m['cap'], subst(dict(m.get('args') or {}), tv), 'read the result (%s)' % m['cap'], query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result')
        res = res if isinstance(res, dict) else {'value': res}
        phase = res.get(m.get('phase_key', 'phase'))
        lines = ['%s result%s: %s' % (self.instrument_id, (' (cell %s)' % tv['cell_name']) if tv else '', phase if phase is not None else '')]
        figs = {}
        for label, v, shown, path in self.figures(res, tv):
            if v is None:
                continue
            lines.append('  %-28s %s' % (label, shown))
            figs[path] = v
        if len(lines) == 1:
            lines.append('  (no figures yet; the full record: --save-to FILE or --json)')
        warn = res.get('warnings') if isinstance(res.get('warnings'), list) else []
        if warn:
            lines.append('  notes: %d model / evidence remarks in the record' % len(warn))
        return {'text': '\n'.join(lines) + '\n', 'json': {'phase': phase, 'figures': figs, 'result': res, 'cell': tv.get('cell_name')}}

    # ------------------------------------------------------------------ analyze
    def h_analyze(self, ctx, p):
        m = self.vmap('analyze')
        models = m.get('models') or {}
        model = p.pos[0] if p.pos else None
        if model is not None and '=' in model:
            p.extra.insert(0, model)
            model = None
        if model is None:
            if len(models) == 1:
                model = next(iter(models))
            else:
                ctx.fail(invalid('WHICH_MODEL', '%s has several evaluations: %s.' % (self.instrument_id, ', '.join(models)), 'name one.'))
        if model not in models:
            ctx.fail(invalid('UNKNOWN_MODEL', '%s has no evaluation %r.' % (self.instrument_id, model),
                             'evaluations of %s: %s.' % (self.instrument_id, ', '.join(models))))
        spec = models[model]
        cap = spec['cap']
        tv = self.cell_vars(ctx, p, 'analyze') if spec.get('per_cell') else {}
        props = self.props(cap)
        aliases = spec.get('keys') or {}
        args = copy.deepcopy(spec.get('defaults') or {})
        given = {}
        for key, text in self.pairs(ctx, p):
            native = aliases.get(key, key)
            if native not in props:
                ctx.fail(invalid('UNKNOWN_SETTING', 'analyze %s on %s has no input %r.' % (model, self.instrument_id, key),
                                 'its inputs: %s.' % ', '.join(sorted(set(aliases) | set(props)))))
            given[native] = self.parse_value(text, props[native], None, key, native=(key == native))
        fill = {a: path for a, path in (spec.get('from_result') or {}).items() if a not in given}
        src = {}
        early = [a for a in self.required(cap) if a not in args and a not in given and a not in fill]
        if early:                                                         # usage errors before anything is read
            inv = {v: kk for kk, v in aliases.items()}
            ctx.fail(invalid('MISSING_SETTING', 'analyze %s on %s needs %s.' % (model, self.instrument_id, ', '.join(
                '%s=%s' % (inv.get(a, a), units.declared(props.get(a) or {}) or (props.get(a) or {}).get('type', 'value')) for a in early)),
                'see `chemlab help %s analyze` and `chemlab help %s %s`.' % (self.instrument_id, self.instrument_id, cap)))
        if fill:
            rm = self.vmap('result')
            r = ctx.call(rm['cap'], subst(dict(rm.get('args') or {}), tv), 'read the result (%s)' % rm['cap'], query=True)
            res = r.get('result') if isinstance(r.get('result'), dict) else {}
            if not ctx.dry_run:
                ph = res.get(rm.get('phase_key', 'phase'))
                for a, path in fill.items():
                    optional = path.startswith('?')                       # may be null in the record (e.g. no individual zero)
                    path = path.lstrip('?')
                    v = resolve(res, path, tv)
                    if isinstance(v, list) and spec.get('drop_none', True):
                        v = [x for x in v if x is not None]
                    if optional and v is None and ph in ('succeeded', 'completed'):
                        args[a] = None
                        src[a] = 'result:' + path
                        continue
                    if v is None or (isinstance(v, list) and not v):
                        ctx.fail(refused('NO_RESULT', '%s has no %s in its last result (phase %s) to evaluate.' % (self.instrument_id, path, ph),
                                         'run a measurement first (`chemlab run %s start`, wait for it), or give %s=... yourself.' % (self.instrument_id, a)))
                    args[a] = v
                    src[a] = 'result:' + path
        args.update(given)
        missing = [a for a in self.required(cap) if a not in args and not (ctx.dry_run and a in fill)]
        if missing:
            inv = {v: kk for kk, v in aliases.items()}
            ctx.fail(invalid('MISSING_SETTING', 'analyze %s on %s needs %s.' % (model, self.instrument_id, ', '.join(
                '%s=%s' % (inv.get(a, a), units.declared(props.get(a) or {}) or (props.get(a) or {}).get('type', 'value')) for a in missing)),
                'see `chemlab help %s analyze` and `chemlab help %s %s`.' % (self.instrument_id, self.instrument_id, cap)))
        if not ctx.dry_run:
            self.check_args(ctx, cap, args, 'analyze %s' % model)
        r = ctx.call(cap, args, 'evaluate %s (%s)' % (model, cap), query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        out = r.get('result')
        lines = ['%s %s:' % (self.instrument_id, model)]
        if isinstance(out, dict):
            for k2, v in out.items():
                if isinstance(v, list) and v and all(finite(x) for x in v):
                    lines.append('  %-28s %d values, %s .. %s' % (k2, len(v), fmt_num(min(v)), fmt_num(max(v))))
                    continue
                if isinstance(v, (list, dict)):
                    continue
                lines.append('  %-28s %s' % (k2, fmt_num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else short(v, 80)))
        else:
            lines.append('  %s' % short(out, 300))
        if src:
            lines.append('  inputs from the last result: %s' % ', '.join(sorted(src)))
        return {'text': '\n'.join(lines) + '\n', 'json': {'model': model, 'result': out, 'inputs_from_result': src}}

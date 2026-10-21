"""Keithley 2450 SourceMeter of the QuadPro2 (CHEM-142): the instrument behind its touch-screen front panel.

The QuadPro2 runtime (signatone_quadpro2.py) models the mapping system that drives the 2450 remotely.  This module adds what an
operator reaches on the 2450 itself: source / measure functions and settings, output on / off with output-off states, ranges,
limits, overvoltage protection and the interlock, readings on whatever is wired to the selected terminals (the SP4 probe on the film
through the QuadPro2 switch matrix, or nothing), reading buffers and their statistics, filter / rel / math / limit tests, the
continuous / manual / trigger-model measurement methods with the trigger-model templates and blocks, configuration lists, sweeps,
setups and macro scripts, the event log, communication and system settings, and local / remote control.  The panel layer
(panel_ui.json + _panel_ui.py) drives these through the smu.* commands below; they are also agent commands (front-panel operations).

Reference: Model 2450 Interactive SourceMeter Instrument Reference Manual 2450-901-01 Rev. D ('p.N' = PDF page of
references/manuals/signatone_quadpro2/communication_manual__keithley_2450_reference_manual_2450-901-01d.pdf).  The 2450 adds range
resolution only; the measurement errors are the QuadPro2 plant's (voltage gain / offset, current gain, the first-order response of the voltage
input with voltage_response_tau_s), default ideal.  The source delay (set or auto, p.120, p.425) is waited before the first reading after the
output turns on or the source magnitude changes; the access mode (p.160-161) asks for the password before the front panel takes local control
in Protected / Lockout; graph draw style and X / Y axis methods (p.59-60, p.148) shape the graph."""
from __future__ import annotations

import copy
import math

V_RANGES = [0.02, 0.2, 2.0, 20.0, 200.0]                                   # p.434, p.405
I_RANGES = [1e-8, 1e-7, 1e-6, 1e-5, 1e-4, 1e-3, 1e-2, 1e-1, 1.0]
R_RANGES = [2.0, 20.0, 200.0, 2e3, 2e4, 2e5, 2e6, 2e7, 2e8]                # p.335 (2 ohm to 200 Mohm)
FUNCTIONS = {'SVMI': ('voltage', 'current'), 'SVMV': ('voltage', 'voltage'), 'SVMR': ('voltage', 'resistance'), 'SVMP': ('voltage', 'power'),
             'SIMI': ('current', 'current'), 'SIMV': ('current', 'voltage'), 'SIMR': ('current', 'resistance'), 'SIMP': ('current', 'power')}
UNITS = {'current': 'A', 'voltage': 'V', 'resistance': 'Ω', 'power': 'W'}
OVP = [None, 2.0, 5.0, 10.0, 20.0, 40.0, 60.0, 80.0, 100.0, 120.0, 140.0, 160.0, 180.0]   # p.432
TEMPLATE_DEFAULTS = dict(count=10, delay=0.0, duration=5.0, buffer='defbuffer1', measure_list='', source_list='', in_line=1, out_line=2, event='display',
                         position=75.0)                                    # p.63-64, p.532-542
TEMPLATES = ['Empty', 'ConfigList', 'LogicTrigger', 'SimpleLoop', 'DurationLoop', 'LoopUntilEvent', 'GradeBinning', 'SortBinning']   # p.63-64
BLOCK_TYPES = ['wait', 'delay', 'measure', 'source_output', 'buffer_clear', 'config_recall', 'config_next', 'config_prev', 'branch_always',
               'branch_counter', 'branch_once', 'branch_on_event', 'branch_limit', 'notify', 'digital_io', 'log_event']   # p.264-266
BLOCK_NAMES = {'wait': 'Wait', 'delay': 'Delay', 'measure': 'Measure', 'source_output': 'Source Output', 'buffer_clear': 'Buffer Clear',
               'config_recall': 'Config Recall', 'config_next': 'Config Next', 'config_prev': 'Config Prev', 'branch_always': 'Branch Always',
               'branch_counter': 'Branch Counter', 'branch_once': 'Branch Once', 'branch_on_event': 'Branch On Event', 'branch_limit': 'Branch Limit',
               'notify': 'Notify', 'digital_io': 'Digital I/O', 'log_event': 'Log Event'}
EVENTS = ['display', 'digio1', 'digio2', 'digio3', 'digio4', 'digio5', 'digio6', 'timer1', 'command', 'none']   # p.540 (front-panel subset)
DEFAULT_BUFFERS = ('defbuffer1', 'defbuffer2')
DEFAULT_CAPACITY = 100000                                                  # p.175
MAX_READINGS = 6875000                                                     # p.172 (standard style)
LINE_HZ = 60.0                                                              # assumption: US mains of the QuadPro2 rack
CLOCK_BASE = '2026-01-01T00:00:00'                                          # assumption: 2450 clock at simulation time 0
FIRMWARE, SERIAL, MAC = '1.5.0b', '04123456', '08:00:11:24:50:01'           # assumption: shown on Info/Manage and LAN tab
QUICKSETUPS = ('voltmeter', 'ammeter', 'ohmmeter', 'power_supply')
REMOTE_INTERFACE = 'USBTMC'                                                 # assumption: the QuadPro2 PC drives the 2450 over USB (host_usb)
OVERFLOW = 9.9e37                                                           # p.405
AUTODELAY_S = {1e-8: (0.05, 0.1, 0.05, 0.1), 1e-7: (0.05, 0.1, 0.05, 0.1), 1e-6: (0.003, 0.02, 0.003, 0.02), 1e-5: (0.002, 0.01, 0.002, 0.01),
               1e-4: (0.001, 0.01, 0.001, 0.01), 1e-3: (0.001, 0.01, 0.001, 0.01), 1e-2: (0.001, 0.005, 0.001, 0.005), 1e-1: (0.001, 0.005, 0.001, 0.005),
               1.0: (0.001, 0.005, 0.002, 0.005)}                          # p.120: current range -> V source, V source high-C, I source, I source high-C
TRACK_LATEST_SPAN_S = 10.0                                                  # assumption: Track Latest shows the last 10 s (fixed scale)


class Rejected(ValueError):
    """A refused 2450 command; the runtime re-raises it as its ActionRejected."""


# ---------------------------------------------------------------- settings table
# key: (scope, type, options or (min, max), default, label, page)
#   scope: 'source' per source function, 'measure' per measure function, 'global' reset on power cycle / System Reset, 'nv' nonvolatile
#   type: bool | enum | num | int | text | range (numeric enum from the function's range list) | list
def _src_defaults(fn):
    return {'source.level': 0.0, 'source.range_auto': True, 'source.range': 0.02 if fn == 'voltage' else 1e-8,
            'source.limit': 105e-6 if fn == 'voltage' else 21.0, 'source.readback': True, 'source.delay_auto': True, 'source.delay': 0.0,
            'source.protect': None, 'source.highc': False, 'source.offmode': 'normal'}


def _meas_defaults(fn):
    low = {'current': 1e-8, 'voltage': 0.02, 'resistance': 2.0, 'power': None}[fn]
    rng = {'current': 1e-4, 'voltage': 20.0, 'resistance': 2e5, 'power': None}[fn]
    out = {'measure.range_auto': True, 'measure.range': rng, 'measure.autorange_low': low, 'measure.sense': '2W', 'measure.nplc': 1.0,
           'measure.autozero': True, 'measure.digits': 5, 'measure.offset_comp': False, 'measure.rel': False, 'measure.rel_level': 0.0,
           'measure.filter': False, 'measure.filter_type': 'repeat', 'measure.filter_count': 10, 'measure.math': False, 'measure.math_format': 'percent',
           'measure.math_m': 1.0, 'measure.math_b': 0.0, 'measure.math_percent': 1.0}
    for n in (1, 2):
        out.update({f'measure.limit{n}': False, f'measure.limit{n}_low': -1.0, f'measure.limit{n}_high': 1.0, f'measure.limit{n}_audible': 'none',
                    f'measure.limit{n}_autoclear': True})
    return out


SETTINGS = {
    'function': ('global', 'enum', list(FUNCTIONS), 'SVMI', 'Function', 'p.30, p.48, p.335, p.337'),
    'count': ('global', 'int', (1, 300000), 1, 'Count', 'p.53, p.418'),
    'terminals': ('global', 'enum', ['front', 'rear'], 'front', 'FRONT/REAR TERMINALS', 'p.31, p.334'),
    'trigger_method': ('global', 'enum', ['continuous', 'manual', 'trigger_model'], 'continuous', 'Measurement method', 'p.42-43, p.241-242'),
    'source.level': ('source', 'num', None, 0.0, 'Source', 'p.45, p.429'),
    'source.range_auto': ('source', 'bool', None, True, 'Source Range Auto', 'p.50, p.337'),
    'source.range': ('source', 'range', None, None, 'Source Range', 'p.50, p.434'),
    'source.limit': ('source', 'num', None, None, 'Limit', 'p.45, p.137, p.430'),
    'source.readback': ('source', 'bool', None, True, 'Source Readback', 'p.50, p.337'),
    'source.delay_auto': ('source', 'bool', None, True, 'Source Delay Auto', 'p.50, p.337'),
    'source.delay': ('source', 'num', (0.0, 4.0), 0.0, 'Source Delay', 'p.50, p.425'),
    'source.protect': ('source', 'enum', OVP, None, 'Overvoltage Protection Limit', 'p.50, p.136, p.432'),
    'source.highc': ('source', 'bool', None, False, 'High Capacitance', 'p.50, p.337'),
    'source.offmode': ('source', 'enum', ['normal', 'high_z', 'zero', 'guard'], 'normal', 'Output Off', 'p.50, p.334'),
    'measure.range_auto': ('measure', 'bool', None, True, 'Measure Range Auto', 'p.44, p.53, p.335'),
    'measure.range': ('measure', 'range', None, None, 'Measure Range', 'p.53, p.405'),
    'measure.autorange_low': ('measure', 'range', None, None, 'Auto Range Low Limit', 'p.53, p.335'),
    'measure.sense': ('measure', 'enum', ['2W', '4W'], '2W', 'Sense', 'p.53, p.336'),
    'measure.nplc': ('measure', 'num', (0.01, 10.0), 1.0, 'NPLCs', 'p.46, p.53, p.400'),
    'measure.autozero': ('measure', 'bool', None, True, 'Auto Zero', 'p.46, p.53, p.335'),
    'measure.digits': ('measure', 'enum', [3, 4, 5, 6], 5, 'Display Digits', 'p.53, p.70, p.380'),
    'measure.offset_comp': ('measure', 'bool', None, False, 'Offset Compensation', 'p.53, p.335'),
    'measure.rel': ('measure', 'bool', None, False, 'Rel', 'p.54, p.162-163, p.336'),
    'measure.rel_level': ('measure', 'num', (-1e12, 1e12), 0.0, 'Rel Value', 'p.54, p.336'),
    'measure.filter': ('measure', 'bool', None, False, 'Filter', 'p.54, p.332'),
    'measure.filter_type': ('measure', 'enum', ['repeat', 'moving'], 'repeat', 'Filter Type', 'p.54, p.332'),
    'measure.filter_count': ('measure', 'int', (1, 100), 10, 'Filter Count', 'p.54, p.332'),
    'measure.math': ('measure', 'bool', None, False, 'Math', 'p.54, p.165-168, p.332'),
    'measure.math_format': ('measure', 'enum', ['mxb', 'percent', 'reciprocal'], 'percent', 'Math Format', 'p.54, p.332'),
    'measure.math_m': ('measure', 'num', (-1e12, 1e12), 1.0, 'm(Scalar)', 'p.54, p.332'),
    'measure.math_b': ('measure', 'num', (-1e12, 1e12), 0.0, 'b(Offset)', 'p.54, p.332'),
    'measure.math_percent': ('measure', 'num', (-1e12, 1e12), 1.0, 'Zero Reference', 'p.55, p.332'),
    **{f'measure.limit{n}{k}': ('measure', t, o, d, f'Limit {n} {lab}'.strip(), 'p.55, p.276-278, p.332-333')
       for n in (1, 2) for k, t, o, d, lab in (('', 'bool', None, False, ''), ('_low', 'num', (-1e12, 1e12), -1.0, 'Low Value'),
                                               ('_high', 'num', (-1e12, 1e12), 1.0, 'High Value'),
                                               ('_audible', 'enum', ['none', 'pass', 'fail'], 'none', 'Audible'),
                                               ('_autoclear', 'bool', None, True, 'Auto Clear'))},
    'quickset.performance': ('global', 'int', (0, 100), 50, 'Performance', 'p.48'),
    'graph.type': ('global', 'enum', ['time', 'scatter'], 'time', 'Graph Type', 'p.59'),
    'graph.draw_style': ('global', 'enum', ['line', 'marker', 'both'], 'line', 'Draw Style', 'p.59'),
    'graph.traces': ('global', 'list', None, [], 'Trace Data', 'p.59'),
    'graph.add_trace': ('global', 'text', (1, 32), '', 'Add Trace', 'p.59, p.147'),
    'graph.remove_trace': ('global', 'text', (1, 32), '', 'Remove Trace', 'p.59, p.147'),
    'graph.x_method': ('global', 'enum', ['smartscale', 'track_latest', 'track_group', 'all', 'off'], 'smartscale', 'X-Axis Method', 'p.60'),
    'graph.y_method': ('global', 'enum', ['smartscale', 'off'], 'smartscale', 'Y-Axis Method', 'p.60'),
    'graph.y_format': ('global', 'enum', ['linear', 'log'], 'linear', 'Y-Axis Scale Format', 'p.60'),
    'histogram.buffer': ('global', 'text', (1, 32), 'defbuffer1', 'Bin Buffer', 'p.61'),
    'histogram.min': ('global', 'num', (-1e12, 1e12), 0.0, 'Minimum Boundary', 'p.62, p.151'),
    'histogram.max': ('global', 'num', (-1e12, 1e12), 1.0, 'Maximum Boundary', 'p.62, p.151'),
    'histogram.bins': ('global', 'int', (1, 100), 10, 'Number of Bins', 'p.62, p.151'),
    'histogram.method': ('global', 'enum', ['smartscale', 'auto_bin', 'fit', 'off'], 'smartscale', 'Method', 'p.62, p.151'),
    'table.buffer': ('global', 'text', (1, 32), 'defbuffer1', 'Buffer', 'p.62'),
    'system.audible_errors': ('nv', 'bool', None, True, 'Audible Errors', 'p.69, p.106'),
    'system.key_click': ('nv', 'bool', None, True, 'Key Click', 'p.69, p.106'),
    'system.backlight_dimmer': ('nv', 'enum', ['never', '1h', '4h', '8h'], 'never', 'Backlight Dimmer', 'p.37, p.69'),
    'system.brightness': ('nv', 'int', (25, 100), 50, 'Backlight Brightness', 'p.37, p.69, p.381'),
    'system.datetime': ('nv', 'text', (5, 19), CLOCK_BASE, 'Time and Date', 'p.69'),
    'system.command_set': ('nv', 'enum', ['SCPI', 'TSP', 'SCPI2400'], 'SCPI', 'Command Set', 'p.69'),
    'system.password': ('nv', 'text', (1, 30), 'admin', 'Password', 'p.69, p.161'),
    'system.reading_format': ('nv', 'enum', ['prefix', 'exponent'], 'prefix', 'Reading Format', 'p.69, p.71-72'),
    'system.access_mode': ('nv', 'enum', ['full', 'exclusive', 'protected', 'lockout'], 'full', 'Access Mode', 'p.69, p.160-161'),
    'comm.gpib_address': ('nv', 'int', (1, 30), 18, 'Address', 'p.68, p.84, p.465'),
    'comm.lan_mode': ('nv', 'enum', ['auto', 'manual'], 'auto', 'TCP/IP Mode', 'p.68'),
    'comm.ip': ('nv', 'text', (7, 15), '192.168.0.2', 'IP Address', 'p.68'),
    'comm.gateway': ('nv', 'text', (7, 15), '192.168.0.1', 'Gateway', 'p.68'),
    'comm.subnet': ('nv', 'text', (7, 15), '255.255.255.0', 'Subnet', 'p.68'),
    'comm.tsplink_node': ('nv', 'int', (1, 64), 1, 'Node', 'p.68'),
    'log.show_warning': ('nv', 'bool', None, True, 'Show Warning', 'p.67, p.159'),
    'log.show_info': ('nv', 'bool', None, True, 'Show Information', 'p.67, p.159'),
    'log.popups': ('nv', 'enum', ['errors_warnings', 'errors', 'none'], 'errors_warnings', 'Popups', 'p.67, p.159'),
    'log.log_warning': ('nv', 'bool', None, True, 'Log Warning', 'p.67, p.159'),
    'log.log_info': ('nv', 'bool', None, True, 'Log Information', 'p.67, p.159'),
    'log.log_command': ('nv', 'bool', None, False, 'Log Command', 'p.67, p.159'),
}
MACRO_SCOPES = ('source', 'measure', 'global')                             # p.197: settings recorded in a macro (not the system UI settings)


def ranges_of(kind, src_fn):
    """Range list of a source function or a measure function (power readings range with the measured quantity: current when sourcing
    voltage, voltage when sourcing current)."""
    if kind in ('voltage',):
        return V_RANGES
    if kind == 'current':
        return I_RANGES
    if kind == 'resistance':
        return R_RANGES
    return I_RANGES if src_fn == 'voltage' else V_RANGES


def auto_range(value, rngs, low=None):
    """Smallest range that holds |value| (105 % over-range, p.405), not below the low limit."""
    pick = next((r for r in rngs if abs(value) <= r * 1.05 + 1e-30), rngs[-1])
    return max(pick, low) if low is not None else pick


_PREFIX = {-12: 'p', -9: 'n', -6: 'µ', -3: 'm', 0: '', 3: 'k', 6: 'M', 9: 'G'}


def _eng(rng):
    e = int(math.floor(math.log10(rng) / 3.0 + 1e-9)) * 3
    return e, _PREFIX[e]


def range_text(rng, unit):
    """'10nA', '2V', '200mV', '200MΩ' (range buttons, p.39, p.45)."""
    if rng is None:
        return 'Auto'
    e, p = _eng(rng)
    return f'{rng / 10 ** e:g}{p}{unit}'


def value_text(v, unit, rng, digits, fmt='prefix', sign=True, space=True):
    """A reading as the 2450 shows it (p.39, p.70-71): digits n.5 give n+1 digit places, scaled to the range's prefix with its integer
    digits zero-padded ('-00.0070 nA' on 10 nA at 5.5 digits, '+1.16244 V' on 2 V); exponent format: mantissa with n+1 digits."""
    if v is None:
        return '--------'
    if abs(v) >= OVERFLOW / 10:
        return 'Overflow'
    places = int(digits) + 1
    s = ('+' if v >= 0 else '-') if sign else ('-' if v < 0 else '')
    if fmt == 'exponent':
        return f'{s}{abs(v):.{places - 1}E}' + (' ' if space else '') + unit
    rng = rng or max(abs(v), 1e-12)
    e, p = _eng(rng)
    intd = len(str(int(round(rng / 10 ** e))))
    dec = max(0, places - intd)
    x = abs(v) / 10 ** e
    return f'{s}{x:0{intd + (1 if dec else 0) + dec}.{dec}f}' + (' ' if space else '') + p + unit


def _blank_stats():
    return dict(n=0, mean=0.0, m2=0.0, lo=None, hi=None)


class K2450:
    """State and commands of the 2450 inside the QuadPro2 rack.  rt is the QuadPro2 runtime (clock, power, plant, probe network)."""

    def __init__(self, rt):
        self.rt = rt
        self.nv = {k: copy.deepcopy(v[3]) for k, v in SETTINGS.items() if v[0] == 'nv'}
        self.scripts = {}                                                  # name -> {kind: setup | macro, body}; nonvolatile (p.65-66)
        self.events = []
        self.event_seq = 0
        self.seen_seq = 0
        self.clock_offset_s = 0.0
        self.lan_applied = {k: self.nv[k] for k in ('comm.lan_mode', 'comm.ip', 'comm.gateway', 'comm.subnet')}
        self.tsplink_nodes = None
        self.beeps = 0
        self.activity_s = 0.0                                              # last front-panel operation (backlight dimmer)
        self.powered = False
        self._volatile()

    # ---- state that a power cycle or System Reset returns to its defaults (p.331-338)
    def _volatile(self):
        self.g = {k: copy.deepcopy(v[3]) for k, v in SETTINGS.items() if v[0] == 'global'}
        self.src = {fn: _src_defaults(fn) for fn in ('voltage', 'current')}
        self.meas = {fn: _meas_defaults(fn) for fn in ('current', 'voltage', 'resistance', 'power')}
        self.output = False
        self.control = 'local'
        self.remote_login = False                                          # the QuadPro2 software has sent 'login' on its interface (p.161-162)
        self.buffers = {}
        for b in DEFAULT_BUFFERS:
            self._new_buffer(b, 'standard', DEFAULT_CAPACITY, 'continuous')
        self.active_buffer = 'defbuffer1'
        self.config_lists = {'source': {}, 'measure': {}}
        self.selected_list = {'source': None, 'measure': None}
        self.tm = dict(template='Empty', params=dict(TEMPLATE_DEFAULTS), blocks=[], state='idle', pc=0, counters={}, once=set(), until=None, wait=None,
                       events=set(), last_reading=None, runs=0)
        self.recording = None
        self.running_script = None
        self.next_reading_s = None
        self.filter_stack = []
        self.last = None                                                   # last reading: dict(value, raw, unit, function, source, ...)
        self.limit_latch = {1: None, 2: None}
        self.interlock_logged = False
        self.settle_until = None                                           # source delay: no reading before this time (p.120, p.425)
        self.source_changed_s = None                                       # last output on / off or source magnitude change
        self.det = None                                                    # voltage input response: last reading time, value and input (QuadPro2 plant tau)
        self.graph_hold = dict(x=None, y=None)                             # graph scale kept when an axis method is Off (p.60)

    # ---- helpers
    @property
    def now(self):
        return self.rt.time_s

    @property
    def src_fn(self):
        return FUNCTIONS[self.g['function']][0]

    @property
    def meas_fn(self):
        return FUNCTIONS[self.g['function']][1]

    def s(self, key):
        return self.src[self.src_fn][key]

    def m(self, key):
        return self.meas[self.meas_fn][key]

    def _new_buffer(self, name, style, capacity, fill):
        self.buffers[name] = dict(name=name, style=style, capacity=int(capacity), fill_mode=fill, data=[], wrapped=0, stats=_blank_stats(),
                                  t0=None)

    def log(self, severity, code, text):
        """Event log entry (p.67, p.157-159): warnings and information only when their logging is on."""
        if severity == 'warning' and not self.nv['log.log_warning'] or severity == 'info' and not self.nv['log.log_info']:
            return
        self.event_seq += 1
        self.events.append(dict(seq=self.event_seq, time_s=self.now, datetime=self.datetime(), severity=severity, code=int(code), text=text))
        del self.events[:-200]
        if severity == 'error' and self.nv['system.audible_errors']:
            self.beeps += 1

    def datetime(self):
        """Instrument clock: the set date and time advancing with the simulation clock (p.69)."""
        t = _iso_seconds(CLOCK_BASE) + self.clock_offset_s + self.now
        return _iso_from_seconds(t)

    # ---- power and reset
    def power(self, on):
        if on and not self.powered:
            self._volatile()
            self.events = []                                               # assumption: the event log starts empty at power-up
            self.seen_seq = self.event_seq
            if 'autoexec' in self.scripts:                                  # p.65: Copy to Power Up runs at power-up
                self._run_script('autoexec')
        if not on:
            self.output = False
            self.recording = None
        self.powered = bool(on)

    def system_reset(self):
        """MENU > System > Info/Manage > System Reset (p.70, p.331-338): settings to defaults, default buffers cleared, user buffers and
        configuration lists deleted; nonvolatile settings and scripts kept."""
        self._volatile()

    # ---- local / remote (p.160, p.607-608)
    def remote_begin(self, method, current_a):
        """The QuadPro2 software takes the 2450 (acquisition / calibration start): remote control, trigger-model method, its settings
        (source current, measure voltage 4-wire, rear terminals, fixed voltage range, compliance), output under software control."""
        self.control = REMOTE_INTERFACE
        self.output = False
        self.recording = None
        self.g.update(function='SIMV', trigger_method='trigger_model', terminals='rear' if self.rt.plant and self.rt.plant['rear_triax_connected'] else 'front')
        s = self.src['current']
        s.update({'source.level': float(current_a), 'source.range_auto': True, 'source.limit': float(min(method['voltage_compliance_v'], 210.0))})
        m = self.meas['voltage']
        m.update({'measure.sense': '4W', 'measure.range_auto': False, 'measure.range': float(method['voltage_range_v'])})

    def record_capture(self, current_a, voltage_v, time_s):
        """A software reading (one phase of a site) lands in the active buffer and on the display like any 2450 reading."""
        if not self.powered:
            return
        self.src['current']['source.level'] = float(current_a)
        rng = self.meas['voltage']['measure.range']
        raw = OVERFLOW if voltage_v is None else float(voltage_v)
        self._store(dict(time_s=time_s, raw=raw, value=raw, unit='V', function='voltage', source=float(current_a), source_unit='A', range=rng,
                         compliance=False, flags=[]))

    # ---- commands (one per front-panel operation; the panel calls the same ones)
    def invoke(self, cap, a):
        if not self.powered:
            raise Rejected('POWER_OFF')
        remote = cap == 'smu.remote_login'                                 # a command of the QuadPro2 software on its remote interface, not a key
        viewing = cap == 'smu.system' and a['action'] == 'local' or cap == 'smu.event_log' and a['action'] == 'viewed' or cap == 'smu.login' or remote
        if cap != 'smu.output' and not viewing and self.control != 'local':
            raise Rejected('REMOTE')                                       # p.608: the front panel is read-only in remote
        if not remote:
            self.activity_s = self.now
        try:
            return getattr(self, '_cmd_' + cap.split('.', 1)[1])(**a)
        except Rejected as exc:                                            # p.461: refused entries are logged (-222 out of range)
            code = str(exc)
            if code != 'PASSWORD_REQUIRED':                                # the password prompt is not an error
                self.log('error', -222 if code == 'OUT_OF_RANGE' else -221, 'Parameter data out of range' if code == 'OUT_OF_RANGE' else
                         'Invalid password' if code == 'PASSWORD_INVALID' else f'Settings conflict: {code}')
            raise

    def _cmd_set(self, setting, value):
        out = self.set(setting, value)
        if self.recording is not None and SETTINGS[setting][0] in MACRO_SCOPES and setting != 'quickset.performance':
            self.recording['body'].append(['set', setting, copy.deepcopy(value)])
        return out

    def set(self, setting, value):
        if setting not in SETTINGS:
            raise Rejected('INVALID_SETTING')
        scope, typ, opts, _, _, _ = SETTINGS[setting]
        value = self._check(setting, typ, opts, value)
        if setting == 'measure.offset_comp' and self.meas_fn != 'resistance':
            raise Rejected('SETTING_UNAVAILABLE')                          # p.53: offset compensation only for resistance
        if setting in ('measure.range', 'measure.range_auto') and self.src_fn == self.meas_fn:
            raise Rejected('SETTING_UNAVAILABLE')                          # p.53: same source and measure quantity: range = source range
        if setting == 'source.limit' and self.s('source.protect') is not None and self.src_fn == 'current' and value > self.s('source.protect'):
            raise Rejected('ABOVE_OVP')                                    # p.137: limits must be below the OVP setting
        if setting == 'source.level' and not self.s('source.range_auto') and abs(value) > self.s('source.range') * 1.05 + 1e-15:
            raise Rejected('OUT_OF_RANGE')
        if setting in ('graph.x_method', 'graph.y_method'):
            axis = setting[6]
            if value == 'off' and self.g[setting] != 'off':                # Off: the graph is not adjusted any more (p.60): keep the present scale
                plot = self._plot()
                self.graph_hold[axis] = [plot['x_lo'], plot['x_hi']] if axis == 'x' else plot['y_auto']
            elif value != 'off':
                self.graph_hold[axis] = None
        old_level = abs(self.s('source.level'))
        if scope == 'source':
            self.src[self.src_fn][setting] = value
            if setting == 'source.level' and abs(value) != old_level:
                self._source_change(self.now)
            if setting == 'source.range':
                self.src[self.src_fn]['source.range_auto'] = False
            if setting == 'source.delay':
                self.src[self.src_fn]['source.delay_auto'] = False         # p.425: a specific delay turns autodelay off
        elif scope == 'measure':
            self.meas[self.meas_fn][setting] = value
            if setting == 'measure.range':
                self.meas[self.meas_fn]['measure.range_auto'] = False      # p.141: a specific range disables auto range
            if setting.startswith('measure.filter'):
                self.filter_stack = []
        elif scope == 'nv':
            if setting == 'system.datetime':
                self.clock_offset_s += _iso_seconds(value) - _iso_seconds(self.datetime())
                value = self.datetime()
            self.nv[setting] = value
        elif setting in ('graph.add_trace', 'graph.remove_trace'):        # Data tab: up to four traces (p.59, p.147); none = the active buffer
            tr = [x for x in self.g['graph.traces'] if x != value]
            if setting == 'graph.add_trace':
                if len(tr) >= 4:
                    raise Rejected('TOO_MANY_TRACES')
                tr.append(value)
            self.g['graph.traces'] = tr
        else:
            old = self.g.get(setting)
            self.g[setting] = value
            if setting == 'function' and value != old:
                self._clear(self.active_buffer)                            # p.169: a front-panel function change clears the active buffer
                self.filter_stack = []
                self.last = None
            if setting == 'quickset.performance':
                self._performance(value)
            if setting == 'trigger_method':
                self._trigger_method(old, value)
        return dict(setting=setting, value=copy.deepcopy(value))

    def _check(self, key, typ, opts, v):
        if typ == 'bool':
            if not isinstance(v, bool):
                raise Rejected('INVALID_VALUE')
        elif typ == 'enum':
            if v not in opts:
                raise Rejected('INVALID_VALUE')
        elif typ in ('num', 'int'):
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or typ == 'int' and float(v) != int(v):
                raise Rejected('INVALID_VALUE')
            lo, hi = opts if opts else self._bounds(key)
            if not lo - 1e-12 <= v <= hi + 1e-12:
                raise Rejected('OUT_OF_RANGE')
            v = int(v) if typ == 'int' else float(v)
        elif typ == 'range':
            rngs = ranges_of(self.src_fn if key.startswith('source') else self.meas_fn, self.src_fn)
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not any(abs(v - r) <= r * 1e-9 for r in rngs):
                raise Rejected('INVALID_VALUE')
            v = next(r for r in rngs if abs(v - r) <= r * 1e-9)
        elif typ == 'text':
            lo, hi = opts
            if not isinstance(v, str) or not lo <= len(v) <= hi:
                raise Rejected('INVALID_VALUE')
            if key == 'system.datetime':                                   # the date or the time alone keeps the other part (date / time pickers)
                now = self.datetime()
                v = v + now[10:] if len(v) == 10 else now[:11] + v + (':00' if len(v) == 5 else '') if len(v) in (5, 8) else v
                if _iso_seconds(v) is None:
                    raise Rejected('INVALID_VALUE')
            if key.startswith('comm.') and not _ipv4(v):
                raise Rejected('INVALID_VALUE')
            if key in ('histogram.buffer', 'table.buffer', 'graph.add_trace') and v not in self.buffers:
                raise Rejected('NO_SUCH_BUFFER')
        elif typ == 'list':
            if not isinstance(v, list) or len(v) > 4 or any(b not in self.buffers for b in v):
                raise Rejected('INVALID_VALUE')                            # p.59: up to four traces
        return v

    def _bounds(self, key):
        fn = self.src_fn
        if key == 'source.level':
            return (-210.0, 210.0) if fn == 'voltage' else (-1.05, 1.05)   # p.429
        if key == 'source.limit':
            return (1e-9, 1.05) if fn == 'voltage' else (0.02, 210.0)       # p.430
        raise Rejected('INVALID_SETTING')

    def _performance(self, p):
        """QuickSet Performance slider (p.48): from speed (0) to resolution (100) it sets NPLC, display digits, autozero and source
        readback (assumption: NPLC log-spaced 0.01-10, digits 3.5-6.5 in quarters, autozero and readback on above the middle; filter off)."""
        m, s = self.meas[self.meas_fn], self.src[self.src_fn]
        m['measure.nplc'] = round(10 ** (-2 + 3 * p / 100.0), 4)
        m['measure.digits'] = 3 + min(3, int(p // 25))
        m['measure.autozero'] = p >= 50
        m['measure.filter'] = False
        s['source.readback'] = p >= 50

    def _trigger_method(self, old, new):
        if old == 'trigger_model' and new != 'trigger_model' and self.tm['state'] in ('running', 'wait'):
            self._tm_stop('idle')
        self.next_reading_s = None

    def _cmd_output(self, on):
        """OUTPUT ON/OFF (p.30, p.32): always active; in remote it turns the output off and switches to local (p.608)."""
        if self.control != 'local':
            self.control = 'local'
            self.remote_login = False
            self.rt._smu_take_local()
            self.output = False
            return dict(on=False)
        if bool(on) != self.output:
            self.output = bool(on)
            self._source_change(self.now)
        self.next_reading_s = None
        if on and self.src_fn == 'voltage' and abs(self.s('source.level')) > 42.0 and not self.rt._smu_interlock():
            self.log('warning', 5074, 'Output voltage limited by interlock')   # p.109
        return dict(on=self.output)

    def _cmd_trigger(self, action):
        """TRIGGER key and the trigger mode indicator menu (p.42-43, p.241-242, p.267)."""
        meth = self.g['trigger_method']
        if action == 'manual':
            if meth == 'continuous':
                raise Rejected('CONTINUOUS_METHOD')                         # p.241: the key asks to change the method instead
            if meth == 'manual':
                self._request(self.now)
                return dict(state='manual')
            if self.tm['state'] in ('running', 'wait'):
                self.tm['events'].add('display')                           # the front-panel TRIGGER key event (p.540)
                self._tm_advance(self.now, self.now)
                return dict(state=self.tm['state'])
            action = 'initiate'
        if action == 'initiate':
            if self.tm['state'] in ('running', 'wait'):
                raise Rejected('TRIGGER_MODEL_RUNNING')
            self.g['trigger_method'] = 'trigger_model'
            if not self.tm['blocks']:
                raise Rejected('EMPTY_TRIGGER_MODEL')                       # assumption: an empty model (template Empty) does not start
            need = self._tm_need()
            buf = self.buffers.get(need[0]) if need else None
            if buf is not None and buf['fill_mode'] == 'once' and need[1] > buf['capacity']:
                self.tm['state'] = 'inactive'                              # p.43: INACT when the count exceeds the buffer capacity
                return dict(state='inactive')
            self.tm.update(state='running', pc=0, counters={}, once=set(), until=None, wait=None, events=set(), runs=self.tm['runs'] + 1)
            self._tm_advance(self.now, self.now)
            return dict(state=self.tm['state'])
        if self.tm['state'] in ('running', 'wait'):                        # abort
            self._tm_stop('idle')
        return dict(state=self.tm['state'])

    def _cmd_quickset(self, setup, sense, level, limit):
        """QuickSet Quick Setups (p.48-49): Voltmeter, Ammeter, Ohmmeter (2- or 4-wire), Power Supply (level, limit, sense); each turns the
        output on with continuous triggering."""
        if setup in ('ohmmeter', 'power_supply') and sense not in ('2W', '4W'):
            raise Rejected('INVALID_VALUE')
        if setup == 'power_supply' and (level is None or limit is None or not -210 <= level <= 210 or not 1e-9 <= limit <= 1.05):
            raise Rejected('OUT_OF_RANGE')
        self.output = False
        if setup == 'voltmeter':
            self._apply_function('SIMV')
            self.src['current'].update({'source.level': 0.0, 'source.range_auto': False, 'source.range': 1e-5, 'source.limit': 200.0})
            self.meas['voltage'].update({'measure.range_auto': True, 'measure.nplc': 1.0})
        elif setup == 'ammeter':
            self._apply_function('SVMI')
            self.src['voltage'].update({'source.level': 0.0, 'source.range_auto': False, 'source.range': 20.0, 'source.limit': 1.0})
            self.meas['current'].update({'measure.range_auto': True})
        elif setup == 'ohmmeter':
            self._apply_function('SIMR')
            self.src['current'].update({'source.level': 1e-7, 'source.range_auto': True})
            self.meas['resistance'].update({'measure.range_auto': True, 'measure.sense': sense})
        else:
            self._apply_function('SVMI')
            self.src['voltage'].update({'source.level': float(level), 'source.range_auto': True, 'source.readback': True, 'source.limit': float(limit),
                                        'source.delay_auto': False, 'source.delay': 0.1})
            self.meas['current'].update({'measure.range_auto': True, 'measure.sense': sense})
        if self.recording is not None:
            self.recording['body'].append(['quickset', setup, sense, level, limit])
        self.g['trigger_method'] = 'continuous'
        self._trigger_method(None, 'continuous')
        return self._cmd_output(True)

    def _apply_function(self, code):
        if code != self.g['function']:
            self._clear(self.active_buffer)
            self.filter_stack = []
            self.last = None
        self.g['function'] = code

    def _cmd_buffer(self, action, name, style, capacity, fill_mode):
        """Reading Buffers (p.56-57, p.169-175): create (named, style, capacity, fill mode; becomes active), clear, delete (not the default
        buffers), make active, resize (clears), fill mode."""
        if action == 'create':
            if not _buffer_name_ok(name) or name in self.buffers:
                raise Rejected('INVALID_NAME')                             # p.172 naming rules
            if style not in ('standard', 'compact', 'full') or fill_mode not in ('continuous', 'once') or not isinstance(capacity, int) or capacity < 1:
                raise Rejected('INVALID_VALUE')
            if sum(b['capacity'] for b in self.buffers.values()) + capacity > MAX_READINGS * (4 if style == 'compact' else 1):
                raise Rejected('BUFFER_MEMORY')                            # p.175: total capacity of all buffers
            self._new_buffer(name, style, capacity, fill_mode)
            self.active_buffer = name
        else:
            if name not in self.buffers:
                raise Rejected('NO_SUCH_BUFFER')
            b = self.buffers[name]
            if action == 'clear':
                self._clear(name)
            elif action == 'delete':
                if name in DEFAULT_BUFFERS:
                    raise Rejected('DEFAULT_BUFFER')                       # p.169: defbuffer1 / defbuffer2 cannot be deleted
                del self.buffers[name]
                if self.active_buffer == name:
                    self.active_buffer = 'defbuffer1'
                for k in ('histogram.buffer', 'table.buffer'):
                    if self.g[k] == name:
                        self.g[k] = 'defbuffer1'
                self.g['graph.traces'] = [x for x in self.g['graph.traces'] if x != name]
            elif action == 'make_active':
                self.active_buffer = name
            elif action == 'resize':
                if not isinstance(capacity, int) or capacity < 1 or sum(x['capacity'] for x in self.buffers.values()) - b['capacity'] + capacity > MAX_READINGS:
                    raise Rejected('BUFFER_MEMORY' if isinstance(capacity, int) and capacity >= 1 else 'INVALID_VALUE')
                b['capacity'] = capacity
                self._clear(name)                                          # p.175: resizing deletes the readings and statistics
            elif action == 'fill_mode':
                if fill_mode not in ('continuous', 'once'):
                    raise Rejected('INVALID_VALUE')
                b['fill_mode'] = fill_mode
            else:
                raise Rejected('INVALID_VALUE')
        if self.recording is not None:
            self.recording['body'].append(['buffer', action, name, style, capacity, fill_mode])
        return dict(active=self.active_buffer, buffers=sorted(self.buffers))

    def _clear(self, name):
        b = self.buffers.get(name)
        if b is not None:
            b.update(data=[], wrapped=0, stats=_blank_stats(), t0=None, full_logged=False)

    def _cmd_config_list(self, action, kind, name, index):
        """Source / Measure Config Lists (p.52, p.56, p.198-218): select, new, delete index, System to Index (store), Index to System
        (recall)."""
        lists = self.config_lists[kind]
        if action == 'create':
            if not _buffer_name_ok(name) or name in lists:
                raise Rejected('INVALID_NAME')
            lists[name] = []
            self.selected_list[kind] = name
        else:
            if name not in lists:
                raise Rejected('NO_SUCH_LIST')
            if action == 'select':
                self.selected_list[kind] = name
            elif action == 'store':
                lists[name].append(self._snapshot(kind))
            elif action in ('delete_index', 'recall'):
                if not isinstance(index, int) or not 1 <= index <= len(lists[name]):
                    raise Rejected('NO_SUCH_INDEX')
                if action == 'delete_index':
                    del lists[name][index - 1]
                else:
                    self._restore(kind, lists[name][index - 1])
            elif action == 'delete':
                del lists[name]
                if self.selected_list[kind] == name:
                    self.selected_list[kind] = None
            else:
                raise Rejected('INVALID_VALUE')
        if self.recording is not None:
            self.recording['body'].append(['config_list', action, kind, name, index])
        return dict(kind=kind, lists={k: len(v) for k, v in lists.items()}, selected=self.selected_list[kind])

    def _snapshot(self, kind):
        """All settings of the active source (or measure) function (p.199: a configuration index stores the complete active state)."""
        if kind == 'source':
            return dict(function=self.src_fn, settings=copy.deepcopy(self.src[self.src_fn]))
        return dict(function=self.meas_fn, settings=copy.deepcopy(self.meas[self.meas_fn]), count=self.g['count'])

    def _restore(self, kind, snap, t=None):
        fn = snap['function']
        if kind == 'source':
            old_level = abs(self.s('source.level'))
            self.src[fn] = copy.deepcopy(snap['settings'])
            if abs(self.src[fn]['source.level']) != old_level:
                self._source_change(self.now if t is None else t)
            self.g['function'] = next(c for c, (s, m) in FUNCTIONS.items() if s == fn and m == self.meas_fn)
        else:
            self.meas[fn] = copy.deepcopy(snap['settings'])
            self.g['count'] = snap['count']
            self.g['function'] = next(c for c, (s, m) in FUNCTIONS.items() if s == self.src_fn and m == fn)

    def _cmd_sweep(self, sweep_type, start, stop, definition, step, points, delay_auto, delay, count_infinite, count, limit, abort_on_limit,
                   ranging):
        """Source > Sweep > Generate (p.51, p.219-225): a source configuration list of the sweep levels and a trigger model that recalls each
        index, sources, waits the delay, measures, and repeats count times; TRIGGER runs it."""
        fn = self.src_fn
        lo, hi = self._bounds('source.level')
        if not (lo <= start <= hi and lo <= stop <= hi) or not 1 <= count <= 268435455:
            raise Rejected('OUT_OF_RANGE')
        if sweep_type.startswith('log') and (start * stop <= 0):
            raise Rejected('INVALID_VALUE')                                # a logarithmic sweep cannot cross or touch zero
        if definition == 'step':
            if step == 0 or (stop - start) / step < 0:
                raise Rejected('INVALID_VALUE')
            points = int(math.floor(abs((stop - start) / step) + 1e-9)) + 1
        if not 2 <= points <= 100000:
            raise Rejected('OUT_OF_RANGE')
        if sweep_type.startswith('linear'):
            levels = [start + (stop - start) * i / (points - 1) for i in range(points)]
        else:
            levels = [start * (stop / start) ** (i / (points - 1)) for i in range(points)]
        if sweep_type.endswith('dual'):
            levels = levels + levels[-2::-1]
        bounds = (1e-9, 1.05) if fn == 'voltage' else (0.02, 210.0)
        if not bounds[0] <= limit <= bounds[1]:
            raise Rejected('OUT_OF_RANGE')
        name = ('Volt' if fn == 'voltage' else 'Curr') + ('Lin' if sweep_type.startswith('linear') else 'Log') + ('Dual' if sweep_type.endswith('dual') else '') + 'SweepList'
        base = copy.deepcopy(self.src[fn])
        fixed = max(abs(x) for x in levels)
        snaps = []
        for x in levels:
            st = dict(base, **{'source.level': float(x), 'source.limit': float(limit)})
            if not delay_auto:
                st.update({'source.delay_auto': False, 'source.delay': float(min(delay, 4.0))})
            if ranging == 'best_fixed':
                st.update({'source.range_auto': False, 'source.range': auto_range(fixed, ranges_of(fn, fn))})
            elif ranging == 'auto':
                st['source.range_auto'] = True
            snaps.append(dict(function=fn, settings=st))
        self.config_lists['source'][name] = snaps
        self.selected_list['source'] = name
        d = 0.0 if delay_auto else float(delay)
        blocks = [dict(type='buffer_clear', buffer=self.active_buffer), dict(type='config_recall', kind='source', list=name, index=1),
                  dict(type='source_output', on=True), dict(type='delay', delay_s=d), dict(type='measure', buffer=self.active_buffer, count=1),
                  dict(type='config_next', kind='source', list=name), dict(type='branch_counter', count=len(levels), target=4)]
        if abort_on_limit:
            blocks.insert(5, dict(type='branch_limit', limit='source', target=len(blocks) + 2))
        blocks += [dict(type='branch_counter', count=int(count) if not count_infinite else 0, target=2), dict(type='source_output', on=False)]
        self.tm.update(template='Sweep', params=dict(sweep_type=sweep_type, start=start, stop=stop, points=len(levels), count='infinite' if count_infinite else count),
                       blocks=blocks, state='idle')
        self.g['trigger_method'] = 'trigger_model'
        if self.recording is not None:
            self.recording['body'].append(['sweep', sweep_type, start, stop, definition, step, points, delay_auto, delay, count_infinite, count,
                                           limit, abort_on_limit, ranging])
        return dict(list=name, points=len(levels), blocks=len(blocks))

    def _cmd_trigger_template(self, template, count, delay, duration, buffer, measure_list, source_list, in_line, out_line, event, position):
        """Trigger > Templates (p.63-64, p.267-268, p.532-542): load a predefined trigger model; the block lists are assumptions built from
        the described behaviour (the manual lists only the parameters)."""
        if buffer not in self.buffers:
            raise Rejected('NO_SUCH_BUFFER')
        if not (1 <= count <= 268435455 and 0 <= delay <= 10000 and 0 < duration <= 100000 and 1 <= in_line <= 6 and 1 <= out_line <= 6 and 0 <= position <= 100):
            raise Rejected('OUT_OF_RANGE')
        b, d = buffer, float(delay)
        if template == 'Empty':
            blocks = []
        elif template == 'SimpleLoop':
            blocks = [dict(type='buffer_clear', buffer=b), dict(type='delay', delay_s=d), dict(type='measure', buffer=b, count=1),
                      dict(type='branch_counter', count=int(count), target=2)]
        elif template == 'DurationLoop':
            blocks = [dict(type='buffer_clear', buffer=b), dict(type='source_output', on=True), dict(type='delay', delay_s=d),
                      dict(type='measure', buffer=b, count=0, duration_s=float(duration)), dict(type='source_output', on=False)]
        elif template == 'ConfigList':
            ml, sl = self.config_lists['measure'].get(measure_list), self.config_lists['source'].get(source_list)
            if not ml or not sl:
                raise Rejected('NO_SUCH_LIST')
            blocks = [dict(type='buffer_clear', buffer=b), dict(type='config_recall', kind='source', list=source_list, index=1),
                      dict(type='config_recall', kind='measure', list=measure_list, index=1), dict(type='source_output', on=True),
                      dict(type='delay', delay_s=d), dict(type='measure', buffer=b, count=1), dict(type='source_output', on=False),
                      dict(type='config_next', kind='source', list=source_list), dict(type='config_next', kind='measure', list=measure_list),
                      dict(type='branch_counter', count=min(len(ml), len(sl)), target=4)]
        elif template == 'LogicTrigger':
            blocks = [dict(type='buffer_clear', buffer=b), dict(type='wait', event=f'digio{in_line}'), dict(type='delay', delay_s=d),
                      dict(type='measure', buffer=b, count=1), dict(type='notify', line=out_line), dict(type='branch_counter', count=int(count), target=2)]
        elif template == 'LoopUntilEvent':
            if event == 'none':
                raise Rejected('INVALID_VALUE')                            # p.539: the event cannot be none
            post = int(round(self.buffers[b]['capacity'] * (100 - position) / 100.0))
            blocks = [dict(type='buffer_clear', buffer=b), dict(type='delay', delay_s=d), dict(type='measure', buffer=b, count=1),
                      dict(type='branch_on_event', event=event, target=5), dict(type='branch_always', target=2),
                      dict(type='measure', buffer=b, count=max(post, 1))]
        elif template in ('GradeBinning', 'SortBinning'):
            blocks = [dict(type='buffer_clear', buffer=b), dict(type='wait', event='digio5'), dict(type='delay', delay_s=d),
                      dict(type='measure', buffer=b, count=1), dict(type='branch_limit', limit='limit1', target=7), dict(type='digital_io', pattern=15),
                      dict(type='branch_counter', count=int(count), target=2)]
        else:
            raise Rejected('INVALID_VALUE')
        self.tm.update(template=template, params=dict(count=count, delay=delay, duration=duration, buffer=buffer, measure_list=measure_list,
                                                      source_list=source_list, in_line=in_line, out_line=out_line, event=event, position=position),
                       blocks=blocks, state='idle')
        if blocks:
            self.g['trigger_method'] = 'trigger_model'                     # p.267: TRIGGER then initiates the loaded model
            self.next_reading_s = None
        if self.recording is not None:
            self.recording['body'].append(['template', template, count, delay, duration, buffer, measure_list, source_list, in_line, out_line,
                                           event, position])
        return dict(template=template, blocks=len(blocks))

    def _cmd_trigger_block(self, action, index, block_type, delay_s, count, target, on):
        """Trigger > Configure (p.64, p.269): edit, insert before the selected block, delete, Clear Model (up to 63 blocks, renumbered)."""
        bl = self.tm['blocks']
        if self.tm['state'] in ('running', 'wait'):
            raise Rejected('TRIGGER_MODEL_RUNNING')
        if action == 'clear':
            bl.clear()
        elif action == 'insert':
            if len(bl) >= 63 or block_type not in BLOCK_TYPES or not 1 <= index <= len(bl) + 1:
                raise Rejected('INVALID_VALUE')
            bl.insert(index - 1, _new_block(block_type, self.active_buffer))
        else:
            if not 1 <= index <= len(bl):
                raise Rejected('NO_SUCH_BLOCK')
            if action == 'delete':
                del bl[index - 1]
            elif action == 'edit':
                b = bl[index - 1]
                for k, v in (('delay_s', delay_s), ('count', count), ('target', target), ('on', on)):
                    if v is not None and k in b:
                        if k == 'target' and not 1 <= v <= len(bl):
                            raise Rejected('NO_SUCH_BLOCK')
                        if k in ('delay_s',) and not 0 <= v <= 10000 or k == 'count' and not 0 <= v <= 268435455:
                            raise Rejected('OUT_OF_RANGE')
                        b[k] = v
            else:
                raise Rejected('INVALID_VALUE')
        self.tm['template'] = 'Custom' if bl else 'Empty'
        if bl:
            self.g['trigger_method'] = 'trigger_model'                     # p.64: TRIGGER initiates the edited model
        if self.recording is not None:
            self.recording['body'].append(['block', action, index, block_type, delay_s, count, target, on])
        return dict(blocks=len(bl))

    def _cmd_script(self, action, name):
        """Scripts Run / Manage / Create Setup / Record (p.65-66, p.152, p.197-198); USB copies need a flash drive (absent)."""
        if action == 'create_setup':
            if not _script_name_ok(name):
                raise Rejected('INVALID_NAME')
            self.scripts[name] = dict(kind='setup', body=self._setup())
        elif action == 'record_start':
            if self.recording is not None:
                raise Rejected('RECORDING')
            self.recording = dict(body=[])
        elif action == 'record_stop':
            if self.recording is None:
                raise Rejected('NOT_RECORDING')
            if not _script_name_ok(name):
                raise Rejected('INVALID_NAME')
            self.scripts[name] = dict(kind='macro', body=self.recording['body'])
            self.recording = None
        elif action == 'record_cancel':
            if self.recording is None:
                raise Rejected('NOT_RECORDING')
            self.recording = None
        elif action in ('run', 'delete', 'copy_to_power_up'):
            if name not in self.scripts:
                raise Rejected('NO_SUCH_SCRIPT')
            if action == 'run':
                self._run_script(name)
            elif action == 'delete':
                del self.scripts[name]
            else:
                self.scripts['autoexec'] = copy.deepcopy(self.scripts[name])
        elif action in ('copy_to_usb', 'copy_from_usb'):
            raise Rejected('NO_USB')
        else:
            raise Rejected('INVALID_VALUE')
        return dict(scripts=sorted(self.scripts), recording=self.recording is not None)

    def _setup(self):
        return dict(g=copy.deepcopy(self.g), src=copy.deepcopy(self.src), meas=copy.deepcopy(self.meas), config_lists=copy.deepcopy(self.config_lists),
                    tm=dict(template=self.tm['template'], params=copy.deepcopy(self.tm['params']), blocks=copy.deepcopy(self.tm['blocks'])))

    def _run_script(self, name):
        """Running a script selects the trigger-model method (p.242); a setup restores settings, lists and trigger model; a macro replays its
        recorded settings at command speed (p.198)."""
        sc = self.scripts[name]
        self.running_script = name
        if sc['kind'] == 'setup':
            b = copy.deepcopy(sc['body'])
            self.g.update(b['g'])
            self.src, self.meas, self.config_lists = b['src'], b['meas'], b['config_lists']
            self.tm.update(b['tm'], state='idle')
        else:
            rec, self.recording = self.recording, None
            for op in copy.deepcopy(sc['body']):
                try:
                    {'set': lambda *a: self.set(*a), 'quickset': self._cmd_quickset, 'buffer': self._cmd_buffer, 'config_list': self._cmd_config_list,
                     'sweep': self._cmd_sweep, 'template': self._cmd_trigger_template, 'block': self._cmd_trigger_block}[op[0]](*op[1:])
                except Rejected as exc:
                    self.log('error', -285, f'Script {name}: {exc}')
            self.recording = rec
        self.g['trigger_method'] = 'trigger_model'
        self.running_script = None

    def _cmd_event_log(self, action):
        """System > Event Log (p.67, p.157-159): viewing marks the events as seen (event indicator back to the empty triangle), Clear Log,
        Reset Popups, Save to USB (no flash drive)."""
        if action == 'viewed':
            self.seen_seq = self.event_seq
        elif action == 'clear':
            self.events = []
            self.seen_seq = self.event_seq
        elif action == 'reset_popups':
            self.nv['log.popups'] = 'errors_warnings'
        elif action == 'save_usb':
            raise Rejected('NO_USB')
        return dict(events=len(self.events))

    def _cmd_system(self, action):
        """Info/Manage System Reset and Password Reset (p.70), LAN Apply Settings (p.68), TSP-Link Initialize (p.68), switch to local
        (p.608), USB firmware upgrade / downgrade (no flash drive)."""
        if action == 'reset':
            self.system_reset()
            if self.recording is not None:
                self.recording['body'].append(['reset'])
        elif action == 'password_reset':
            self.nv['system.password'] = 'admin'
        elif action == 'lan_apply':
            self.lan_applied = {k: self.nv[k] for k in ('comm.lan_mode', 'comm.ip', 'comm.gateway', 'comm.subnet')}
        elif action == 'tsplink_initialize':
            self.tsplink_nodes = 1                                         # no other TSP-Link instrument in the QuadPro2 rack
        elif action == 'local':
            if self.control != 'local':
                if self.nv['system.access_mode'] in ('protected', 'lockout'):
                    raise Rejected('PASSWORD_REQUIRED')                    # p.160-161: a password is needed to change to the front panel
                self.control = 'local'
                self.remote_login = False
                self.rt._smu_take_local()
        elif action == 'rel_acquire':                                      # p.163: a new measurement becomes the relative offset and Rel turns on
            raw = self._conversion(self.now)[0]
            m = self.meas[self.meas_fn]
            m['measure.rel_level'] = raw if raw != OVERFLOW else m['measure.rel_level']
            m['measure.rel'] = m['measure.rel'] or raw != OVERFLOW
            if self.recording is not None:
                self.recording['body'].append(['set', 'measure.rel_level', m['measure.rel_level']])
        elif action in ('upgrade', 'downgrade'):
            raise Rejected('NO_USB')
        else:
            raise Rejected('INVALID_VALUE')
        return dict(control=self.control)

    def _cmd_login(self, password):
        """The password prompt when the front panel takes control from a remote interface in Protected or Lockout access (p.160-161): the
        right password switches to local control; a wrong one is refused and logged.  Full / Exclusive: no password needed."""
        if self.control != 'local':
            if self.nv['system.access_mode'] in ('protected', 'lockout') and password != self.nv['system.password']:
                raise Rejected('PASSWORD_INVALID')
            self.control = 'local'
            self.remote_login = False
            self.rt._smu_take_local()
        return dict(control=self.control)

    def _cmd_remote_login(self, password):
        """'login password' sent by the QuadPro2 software on its remote interface (p.161-162 Switching control interfaces, p.456): with an
        access mode other than Full the software must log in before it may take the instrument from another interface (the front panel);
        Exclusive needs no password, Protected and Lockout the System Settings password (a wrong one is refused and logged).  Full: no login
        needed.  A change to local control (front panel) or a power cycle logs the software out."""
        mode = self.nv['system.access_mode']
        if mode in ('protected', 'lockout') and password != self.nv['system.password']:
            raise Rejected('PASSWORD_INVALID')
        self.remote_login = True
        return dict(logged_in=True, access_mode=mode)

    def require_remote_login(self):
        """The QuadPro2 software's command that takes the 2450 (map or calibration start): refused while another interface has control and the
        access mode asks for a login the software has not given (reads stay allowed)."""
        if self.nv['system.access_mode'] != 'full' and self.control != REMOTE_INTERFACE and not self.remote_login:
            raise Rejected('LOGIN_REQUIRED')

    # ---- source delay and the voltage input response
    def _source_delay(self):
        """Source delay (p.50, p.120, p.425): the set delay, or with Auto the delay of the current range (high capacitance column when on)."""
        s = self.src[self.src_fn]
        if not s['source.delay_auto']:
            return s['source.delay']
        if self.src_fn == 'current':
            rng = s['source.range'] if not s['source.range_auto'] else auto_range(s['source.level'], I_RANGES)
        else:
            m = self.meas['current']
            rng = m['measure.range'] if self.meas_fn == 'current' and not m['measure.range_auto'] else auto_range(s['source.limit'], I_RANGES)
        col = (2 if self.src_fn == 'current' else 0) + (1 if s['source.highc'] else 0)
        return AUTODELAY_S[min(AUTODELAY_S, key=lambda r: abs(math.log10(r) - math.log10(rng)))][col]

    def _source_change(self, t):
        """The output turned on / off or the source magnitude changed at time t: the delay applies to the first output and to magnitude
        changes (p.120); the voltage input starts to follow the new level."""
        self.source_changed_s = t
        self.settle_until = t + self._source_delay() if self.output else None

    def _response(self, t, target):
        """Measured voltage at time t through the QuadPro2 plant's input response (first order, voltage_response_tau_s, the same model as the
        plant's x[1] in signatone_quadpro2.py): it follows a change of its input from the last source change (or the last reading)."""
        tau = (self.rt.plant or {}).get('voltage_response_tau_s')
        d = self.det
        if not tau or d is None:
            v = target
        elif target != d['target']:
            tc = min(t, max(d['t'], self.source_changed_s if self.source_changed_s is not None else d['t']))
            v_c = d['target'] + (d['v'] - d['target']) * math.exp(-max(0.0, tc - d['t']) / tau)
            v = target + (v_c - target) * math.exp(-max(0.0, t - tc) / tau)
        else:
            v = target + (d['v'] - target) * math.exp(-max(0.0, t - d['t']) / tau)
        self.det = dict(t=max(t, d['t']) if d else t, v=v, target=target)
        return v

    # ---- source and measure on the connected load
    def connected(self):
        return self.rt._smu_network() is not None and (self.g['terminals'] == 'rear') == bool(self.rt.plant['rear_triax_connected'])

    def local_active(self):
        """The 2450 drives the probe network under front-panel control (output on, local, the probe's terminals selected)."""
        return self.powered and self.output and self.control == 'local' and self.connected()

    def solve(self, net):
        """Steady source / limit / OVP / interlock solution on the load (p.109, p.136-137, p.4-4): net = None (open) or
        dict(r_force, r_sense); returns current, force voltage, sense voltage, compliance, overvoltage flags."""
        fn, s = self.src_fn, self.src[self.src_fn]
        four = self.m('measure.sense') == '4W'
        vmax = 210.0 if self.rt._smu_interlock() else 42.0
        if s['source.protect'] is not None:
            vmax = min(vmax, s['source.protect'])
        level, lim, comp, ovp = s['source.level'], s['source.limit'], False, False
        if fn == 'voltage':
            vset = max(-vmax, min(vmax, level))
            ovp = abs(level) > vmax
            if net is None:
                return dict(current=0.0, v_force=vset, v_sense=vset, compliance=False, ovp=ovp)
            i = vset / (net['r_sense'] if four else net['r_force'])
            if abs(i) > lim:
                i, comp = math.copysign(lim, i), True
        else:
            vlim = min(lim, vmax)
            if net is None:
                return dict(current=0.0, v_force=math.copysign(vlim, level) if level else 0.0, v_sense=0.0, compliance=level != 0, ovp=False)
            i = level
            if abs(i * net['r_force']) > vlim:
                i, comp = math.copysign(vlim / net['r_force'], level), True
        if abs(i * net['r_force']) > vmax:
            i, comp = math.copysign(vmax / net['r_force'], i), True
        p_max = 20.0                                                        # operating boundary (QuadPro2 runtime: 20 W envelope)
        if i * i * net['r_force'] > p_max:
            i, comp = math.copysign(math.sqrt(p_max / net['r_force']), i), True
        return dict(current=i, v_force=i * net['r_force'], v_sense=i * net['r_sense'], compliance=comp, ovp=ovp)

    def electrical(self, T):
        """Source power on the probe network for the QuadPro2 thermal ODE while the 2450 sources under local control."""
        net = self.rt._smu_network(T)
        e = self.solve(net)
        return dict(current=e['current'], voltage=e['v_sense'] if self.m('measure.sense') == '4W' else e['v_force'], source_voltage=e['v_force'],
                    sample_power=e['current'] ** 2 * net['r_drive'],
                    contact_power=e['current'] ** 2 * net['r_contact'], compliance=e['compliance'], valid=net['valid'])

    def _conversion(self, t):
        """One raw conversion at time t of the present measure function: (value or OVERFLOW, unit, range, source reading, flags)."""
        fn, sf = self.meas_fn, self.src_fn
        plant = self.rt.plant or {}
        vg, vo, ig = plant.get('voltage_gain', 1.0), plant.get('voltage_offset_v', 0.0), plant.get('current_gain', 1.0)
        on = self.output and self.control == 'local'
        net = self.rt._smu_network() if self.connected() else None
        if on:
            e = self.solve(net)
        else:                                                               # output off (normal / zero / guard: 0 at the terminals; high-Z: open)
            e = dict(current=0.0, v_force=0.0, v_sense=0.0, compliance=False, ovp=False)
        four = self.m('measure.sense') == '4W'
        i_meas = ig * e['current']
        v_true = e['v_sense'] if four else e['v_force']
        v_meas = self._response(t, vg * v_true + vo) if net is not None else vg * v_true   # the probe's voltage input (gain, offset, response)
        if fn == 'current':
            raw = i_meas
        elif fn == 'voltage':
            raw = v_meas
        elif fn == 'resistance':
            v_r = v_meas - vo if self.m('measure.offset_comp') and net is not None else v_meas   # offset compensation removes the thermal offset (p.53)
            raw = v_r / i_meas if abs(i_meas) > 1e-15 else OVERFLOW
        else:
            raw = v_meas * i_meas
        rngs = ranges_of(fn, sf)
        if sf == fn:
            rng = self.s('source.range') if not self.s('source.range_auto') else auto_range(self.s('source.level'), rngs)
        elif fn == 'power' or self.m('measure.range_auto'):
            low = self.m('measure.autorange_low')
            rng = auto_range(raw if raw != OVERFLOW else rngs[-1], rngs, low)
        else:
            rng = self.m('measure.range')
        if raw != OVERFLOW and abs(raw) > rng * 1.05:
            raw = OVERFLOW                                                  # p.405: overrange on a fixed range
        if raw != OVERFLOW and fn in ('current', 'voltage'):
            q = rng * (1e-6 if fn == 'current' else 5e-7)                   # resolution as the QuadPro2 runtime (FS*1e-6 A, FS*5e-7 V)
            raw = round(raw / q) * q
        src_read = (v_meas if sf == 'voltage' else i_meas) if on and self.s('source.readback') else self.s('source.level')
        flags = (['LIMIT'] if e['compliance'] else []) + (['OVP'] if e.get('ovp') else [])
        return raw, UNITS[fn], rng, (src_read if on else None), flags

    def _period(self):
        """Time of one reading: NPLC at the line frequency, twice with autozero reference readings, plus 2 ms (assumption)."""
        return self.m('measure.nplc') / LINE_HZ * (2.0 if self.m('measure.autozero') else 1.0) + 0.002

    def _reading(self, t):
        """One processed reading (p.168, p.331): filter, rel, math, limit tests; stored in the active buffer."""
        raw, unit, rng, src, flags = self._conversion(t)
        value = raw
        m = self.meas[self.meas_fn]
        if value != OVERFLOW and m['measure.filter']:
            st = self.filter_stack
            st.append(value)
            n = m['measure.filter_count']
            if m['measure.filter_type'] == 'moving':
                del st[:-n]
                value = sum(st) / len(st)
            else:
                if len(st) < n:
                    return None                                             # repeating filter: a reading every n conversions
                value = sum(st[-n:]) / n
                self.filter_stack = []
        if value != OVERFLOW and m['measure.rel']:
            value = value - m['measure.rel_level']
        if value != OVERFLOW and m['measure.math']:
            f = m['measure.math_format']
            if f == 'mxb':
                value = m['measure.math_m'] * value + m['measure.math_b']
            elif f == 'percent':
                value = (value - m['measure.math_percent']) / m['measure.math_percent'] * 100.0 if m['measure.math_percent'] else OVERFLOW
            else:
                value = 1.0 / value if value else OVERFLOW
            if self.meas_fn != 'resistance':
                unit = {'mxb': 'X', 'percent': '%', 'reciprocal': '1/x'}[f]   # p.165-166
        for n in (1, 2):
            if m[f'measure.limit{n}']:
                ok = value != OVERFLOW and m[f'measure.limit{n}_low'] <= value <= m[f'measure.limit{n}_high']
                if not m[f'measure.limit{n}_autoclear'] and self.limit_latch[n] is False:
                    ok = False                                              # without auto clear a failure stays until cleared
                self.limit_latch[n] = ok
                flags.append(f'L{n}PASS' if ok else f'L{n}FAIL')
                if m[f'measure.limit{n}_audible'] == ('pass' if ok else 'fail'):
                    self.beeps += 1
        return dict(time_s=t, raw=raw, value=value, unit=unit, function=self.meas_fn, source=src, source_unit=UNITS[self.src_fn], range=rng,
                    compliance='LIMIT' in flags, flags=flags)

    def _store(self, r, buffer=None):
        b = self.buffers.get(buffer or self.active_buffer)
        self.last = r
        if b is None:
            return
        if b['fill_mode'] == 'once' and len(b['data']) >= b['capacity']:
            if not b.get('full_logged'):
                b['full_logged'] = True
                self.log('warning', 4915, 'Attempting to store past capacity of reading buffer')   # p.170
            return
        if b['t0'] is None:
            b['t0'] = r['time_s']
        b['data'].append((r['time_s'], r['value'], r['unit'], r['source'], r['range'], tuple(r['flags'])))
        if len(b['data']) > b['capacity']:
            del b['data'][0]
            b['wrapped'] += 1
        v = r['value']
        if v is not None and abs(v) < OVERFLOW / 10:                       # statistics include overwritten readings (p.47)
            st = b['stats']
            st['n'] += 1
            d = v - st['mean']
            st['mean'] += d / st['n']
            st['m2'] += d * (v - st['mean'])
            st['lo'] = v if st['lo'] is None else min(st['lo'], v)
            st['hi'] = v if st['hi'] is None else max(st['hi'], v)

    def _request(self, t, buffer=None, count=None):
        """A measurement request: count readings (p.53, p.418); returns the time they take."""
        n = self.g['count'] if count is None else count
        dt = self._period()
        start = max(t, self.settle_until) if self.settle_until is not None and self.output else t   # the source delay comes first (p.120)
        for k in range(n):
            r = self._reading(start + k * dt)
            if r is not None:
                self._store(r, buffer)
        return start - t + n * dt

    # ---- time
    def advance(self, t0, t1):
        """Readings and trigger model between two host-step boundaries (the state of the probe network is that at t1)."""
        if not self.powered or self.control != 'local':
            self.next_reading_s = None
            return
        meth = self.g['trigger_method']
        if meth == 'continuous':                                           # p.241: readings continuously, stored in the active buffer
            dt = self._period() * self.g['count']
            t = t0 + dt if self.next_reading_s is None else self.next_reading_s
            if self.settle_until is not None and self.output and t < self.settle_until + dt:
                t = self.settle_until + dt                                 # the first reading after a source change waits the source delay
            n = int(math.floor((t1 - t) / dt + 1e-9)) + 1 if t <= t1 + 1e-12 else 0
            b = self.buffers[self.active_buffer]
            for k in range(n):
                if b['fill_mode'] == 'once' and len(b['data']) >= b['capacity']:
                    self._store(self._reading(t + k * dt))                  # p.175: a full fill-once buffer stops the readings (event 4915)
                    break
                if k >= 2000 and self.last is not None and not self.m('measure.filter'):
                    self._repeat(self.last, t + k * dt, n - k, dt)          # long host steps: the steady reading repeats
                    break
                self._request(t + k * dt)
            self.next_reading_s = t + n * dt
        elif meth == 'trigger_model':
            self._tm_advance(t0, t1)

    def _repeat(self, r, t, n, dt):
        """n further identical readings from t (a steady network between two host-step boundaries): the last capacity of them are stored,
        all of them count in the statistics (p.47)."""
        b = self.buffers[self.active_buffer]
        keep = min(n, b['capacity'] if b['fill_mode'] == 'continuous' else max(0, b['capacity'] - len(b['data'])))
        for k in range(n - keep, n):
            self._store(dict(r, time_s=t + k * dt))
        v = r['value']
        if b['fill_mode'] == 'continuous' and n > keep and v is not None and abs(v) < OVERFLOW / 10:
            st, m = b['stats'], n - keep                                    # Chan's update for m equal values
            tot = st['n'] + m
            d = v - st['mean']
            st['m2'] += d * d * st['n'] * m / tot
            st['mean'] += d * m / tot
            st['n'] = tot
            st['lo'] = v if st['lo'] is None else min(st['lo'], v)
            st['hi'] = v if st['hi'] is None else max(st['hi'], v)

    def _tm_need(self):
        for b in self.tm['blocks']:
            if b['type'] == 'measure':
                cnt = b['count'] or 1
                loops = max([x['count'] for x in self.tm['blocks'] if x['type'] == 'branch_counter'] or [1])
                return b['buffer'], cnt * max(loops, 1)
        return None

    def _tm_stop(self, state):
        self.tm.update(state=state, until=None, wait=None)
        if state == 'idle' and any(b['type'] == 'source_output' for b in self.tm['blocks']):
            self.output = False

    def _tm_advance(self, t0, t1):
        """Trigger-model interpreter (p.251-269): blocks run in order; delays and measurements take time; a wait block waits on its event
        (the front-panel TRIGGER key; digital lines, LAN and TSP-Link triggers never arrive in the QuadPro2 rack); branching to past the
        last block ends the run (IDLE)."""
        tm = self.tm
        t = max(t0, tm['until'] or t0)
        if tm['state'] not in ('running', 'wait') or t > t1:
            return
        guard = 0
        while tm['state'] in ('running', 'wait') and t <= t1 + 1e-12:
            guard += 1
            if guard > 200000:
                break
            if tm['pc'] >= len(tm['blocks']):
                self._tm_stop('idle')
                break
            b = tm['blocks'][tm['pc']]
            k, nxt = b['type'], tm['pc'] + 1
            if k == 'wait':
                if b['event'] in tm['events']:
                    tm['events'].discard(b['event'])
                    tm['state'] = 'running'
                else:
                    tm['state'] = 'wait'
                    tm['until'] = None
                    return
            elif k == 'delay':
                if tm['until'] is None:
                    tm['until'] = t + b['delay_s']
                if tm['until'] > t1 + 1e-12:
                    return
                t, tm['until'] = tm['until'], None
            elif k == 'measure':
                if b.get('duration_s'):
                    end = (tm.get('measure_end') or t + b['duration_s'])
                    tm['measure_end'] = end
                    while t < min(end, t1) - 1e-12:
                        t += self._request(t, b['buffer'], 1)
                    if t < end - 1e-12:
                        tm['until'] = t
                        return
                    tm['measure_end'] = None
                else:
                    t += self._request(t, b['buffer'], b['count'])
                    if t > t1 + 1e-12:
                        tm['pc'], tm['until'] = nxt, t
                        return
            elif k == 'source_output':
                if bool(b['on']) != self.output:
                    self.output = bool(b['on'])
                    self._source_change(t)
            elif k == 'buffer_clear':
                self._clear(b['buffer'])
            elif k in ('config_recall', 'config_next', 'config_prev'):
                lst = self.config_lists[b['kind']].get(b['list'])
                if not lst:
                    self.log('error', -221, f'Settings conflict: configuration list {b["list"]} is empty')
                    self._tm_stop('idle')
                    return
                key = ('cfg', b['kind'], b['list'])
                i = b['index'] - 1 if k == 'config_recall' else (tm['counters'].get(key, 0) + (1 if k == 'config_next' else -1)) % len(lst)
                tm['counters'][key] = i
                self._restore(b['kind'], lst[i], t)
            elif k == 'branch_always':
                nxt = b['target'] - 1
            elif k == 'branch_counter':
                key = ('n', tm['pc'])
                c = tm['counters'].get(key, 0) + 1
                if b['count'] == 0 or c < b['count']:
                    tm['counters'][key] = c
                    nxt = b['target'] - 1
                else:
                    tm['counters'][key] = 0
            elif k == 'branch_once':
                if tm['pc'] not in tm['once']:
                    tm['once'].add(tm['pc'])
                    nxt = b['target'] - 1
            elif k == 'branch_on_event':
                if b['event'] in tm['events']:
                    tm['events'].discard(b['event'])
                    nxt = b['target'] - 1
            elif k == 'branch_limit':
                hit = bool(self.last and ('LIMIT' in self.last['flags'] if b['limit'] == 'source' else 'L1FAIL' in self.last['flags']))
                if hit:
                    nxt = b['target'] - 1
            elif k == 'log_event':
                self.log('info', 0, 'Trigger model log event block')
            # notify, digital_io: no external device listens in the QuadPro2 rack
            tm['pc'] = nxt
            if tm['pc'] < 0 or tm['pc'] >= len(tm['blocks']):
                self._tm_stop('idle')
                break

    # ---- what the panel and the agent read
    def slots(self):
        """State slots of the 2450 layer (agent-visible: what the front panel shows or what the operator set)."""
        sf, mf = self.src_fn, self.meas_fn
        s, m = self.src[sf], self.meas[mf]
        on = self.powered and (self.output and self.control == 'local' or self.rt._electrically_active())
        b = self.buffers[self.active_buffer]
        srngs, mrngs = ranges_of(sf, sf), ranges_of(mf, sf)
        opts = lambda rngs, unit, auto: ([dict(value='auto', text='Auto')] if auto else []) + [dict(value=r, text=range_text(r, unit)) for r in rngs]   # noqa: E731
        present = self.last['range'] if self.last and self.last['function'] == mf else (m['measure.range'] or mrngs[0])
        i = min(range(len(mrngs)), key=lambda k: abs(mrngs[k] - present))
        dim = self.nv['system.backlight_dimmer']
        dimmed = dim != 'never' and self.now - self.activity_s >= {'1h': 3600.0, '4h': 14400.0, '8h': 28800.0}[dim]
        return {
            'smu.settings': dict(function=self.g['function'], source_function=sf, measure_function=mf, src=copy.deepcopy(s), meas=copy.deepcopy(m),
                                 globals=copy.deepcopy(self.g), nv=copy.deepcopy(self.nv),
                                 source_range_sel='auto' if s['source.range_auto'] else s['source.range'],
                                 measure_range_sel='auto' if m['measure.range_auto'] or mf == 'power' else m['measure.range'],
                                 range_options=dict(source=opts(srngs, UNITS[sf], True), measure=opts(mrngs, UNITS[mf] if mf != 'power' else ('A' if sf == 'voltage' else 'V'), True),
                                                    low=opts(mrngs, UNITS[mf], False)),
                                 range_up=mrngs[min(i + 1, len(mrngs) - 1)], range_down=mrngs[max(i - 1, 0)],
                                 source_level_bounds=list(self._bounds('source.level')), source_limit_bounds=list(self._bounds('source.limit'))),
            'smu.state': dict(powered=self.powered, output=bool(on), control=self.control, remote_login=self.remote_login, terminals=self.g['terminals'], beeps=self.beeps,
                              recording=self.recording is not None, running_script=self.running_script, datetime=self.datetime(),
                              lan_applied=copy.deepcopy(self.lan_applied), tsplink_nodes=self.tsplink_nodes, line_frequency_hz=LINE_HZ,
                              firmware=FIRMWARE, serial=SERIAL, mac=MAC, brightness=25 if dimmed else self.nv['system.brightness']),
            'smu.reading': None if self.last is None else {k: (list(v) if isinstance(v, tuple) else v) for k, v in self.last.items()},
            'smu.buffers': dict(active=self.active_buffer, list=[self._buffer_info(x) for x in self.buffers.values()],
                                recent=[_row(i, r, x) for i, r, x in self._recent(self.g['table.buffer'], 50)],
                                plot=self._plot(), histogram=self._histogram(), fill=len(b['data']) / max(1, b['capacity'])),
            'smu.trigger': dict(method=self.g['trigger_method'], state=self.tm['state'], template=self.tm['template'], params=copy.deepcopy(self.tm['params']),
                                blocks=[dict(b, index=i + 1, name=BLOCK_NAMES[b['type']]) for i, b in enumerate(self.tm['blocks'])], block=self.tm['pc'] + 1,
                                runs=self.tm['runs']),
            'smu.lists': dict(source={k: len(v) for k, v in self.config_lists['source'].items()}, measure={k: len(v) for k, v in self.config_lists['measure'].items()},
                              names={kind: [dict(name=k) for k in self.config_lists[kind]] for kind in ('source', 'measure')},
                              selected=copy.deepcopy(self.selected_list),
                              details={kind: [dict(index=i + 1, **_flat(sn)) for i, sn in enumerate(self.config_lists[kind].get(self.selected_list[kind]) or [])]
                                       for kind in ('source', 'measure')}),
            'smu.scripts': dict(names=sorted(self.scripts), list=[dict(name=k, kind=self.scripts[k]['kind']) for k in sorted(self.scripts)],
                                kinds={k: v['kind'] for k, v in self.scripts.items()},
                                autoexec='autoexec' in self.scripts, recording=self.recording is not None,
                                recorded=len(self.recording['body']) if self.recording else 0),
            'smu.events': dict(entries=[dict(e) for e in self.events[-50:]][::-1], unseen=self._unseen(), count=len(self.events), popup=self._popup()),
            'smu.display': self.display(),
        }

    def _popup(self):
        """The newest error or warning shown briefly as a popup (p.38, p.67): 3 s (assumption), filtered by the Popups setting."""
        allowed = {'errors_warnings': ('error', 'warning'), 'errors': ('error',), 'none': ()}[self.nv['log.popups']]
        for e in reversed(self.events):
            if self.now - e['time_s'] >= 3.0:
                break
            if e['severity'] in allowed:
                return dict(severity=e['severity'], code=e['code'], text=e['text'])
        return None

    def _plot(self):
        """Graph data (p.57-60, p.146-149): up to four traces (none: the active buffer), y = reading (log10 of |reading| with the log
        scale format), x = time from the buffer's first reading or the source value (Scatter/IV).  X-Axis Method (p.60, p.148): SmartScale
        the latest 200 points, Track Group the latest reading group (count readings; SmartScale when the count is 1), Track Latest the latest
        data on a fixed 10 s scale, All every reading of the buffer (thinned to 600), Off the scale kept when Off was chosen; Y-Axis Method
        SmartScale fits the shown data, Off keeps the scale; Draw Style (p.59) line / marker / both."""
        xm = self.g['graph.x_method']
        names = self.g['graph.traces'] or [self.active_buffer]
        out, xs = [], []
        for n in names:
            b = self.buffers.get(n)
            if b is None:
                continue
            data = b['data']
            if xm in ('all', 'off'):
                data = data if len(data) <= 600 else [data[int(i * (len(data) - 1) / 599)] for i in range(600)]
            elif xm == 'track_group' and self.g['count'] > 1:
                data = data[-self.g['count']:]
            else:
                data = data[-200:]
            pts = []
            for t, v, u, src, rng, fl in data:
                if abs(v) >= OVERFLOW / 10 or (self.g['graph.y_format'] == 'log' and v == 0):
                    continue
                x = (src if src is not None else 0.0) if self.g['graph.type'] == 'scatter' else t - (b['t0'] or t)
                pts.append([x, math.log10(abs(v)) if self.g['graph.y_format'] == 'log' else v])
            xs += [q[0] for q in pts]
            out.append(dict(name=n, points=pts, unit=b['data'][-1][2] if b['data'] else ''))
        lo, hi = (min(xs), max(xs)) if xs else (0.0, 1.0)
        if xm == 'track_latest' and xs:
            lo = hi - TRACK_LATEST_SPAN_S
        if xm == 'off' and self.graph_hold['x']:
            lo, hi = self.graph_hold['x']
        hi = hi if hi > lo else lo + 1.0
        ys = [q[1] for tr in out for q in tr['points'] if lo - 1e-9 <= q[0] <= hi + 1e-9]
        y_lo = min([0.0] + ys)                                              # the automatic scale of the graph widget (fits the shown data)
        y_hi = max(ys) if ys else y_lo + 1.0
        y_hi = y_lo + (abs(y_lo) * 0.1 or 1.0) if y_hi - y_lo < 1e-12 else y_hi + 0.05 * (y_hi - y_lo)
        hold = self.graph_hold['y'] if self.g['graph.y_method'] == 'off' else None
        return dict(traces=out, x_lo=lo, x_hi=hi, span=hi - lo, active=not self.g['graph.traces'], draw=self.g['graph.draw_style'],
                    y_lo=hold[0] if hold else None, y_hi=hold[1] if hold else None, y_auto=[y_lo, y_hi])

    def _histogram(self):
        """Histogram (p.61-62, p.151): readings of the bin buffer in 'bins' bins between the boundaries plus a low and a high outlier bin;
        SmartScale / Auto Bin set the boundaries from the data (assumption: data minimum and maximum)."""
        b = self.buffers.get(self.g['histogram.buffer']) or self.buffers['defbuffer1']
        vals = [v for _, v, *_ in b['data'] if abs(v) < OVERFLOW / 10]
        lo, hi, n = self.g['histogram.min'], self.g['histogram.max'], self.g['histogram.bins']
        if self.g['histogram.method'] in ('smartscale', 'auto_bin') and vals:
            lo, hi = min(vals), max(vals)
        if hi <= lo:
            hi = lo + (abs(lo) * 0.1 or 1.0)
        counts, under, over = [0] * n, 0, 0
        for v in vals:
            if v < lo:
                under += 1
            elif v > hi:
                over += 1
            else:
                counts[min(n - 1, int((v - lo) / (hi - lo) * n))] += 1
        w = (hi - lo) / n
        bars = [[lo - w, 0.0], [lo - w, float(under)], [lo, float(under)]]
        for k, c in enumerate(counts):
            bars += [[lo + k * w, float(c)], [lo + (k + 1) * w, float(c)]]
        bars += [[hi, float(over)], [hi + w, float(over)], [hi + w, 0.0]]
        return dict(counts=counts, low_outliers=under, high_outliers=over, min=lo, max=hi, bins=n, bars=bars, x_lo=lo - w, span=hi - lo + 2 * w,
                    buffer=b['name'], count=len(vals))

    def _unseen(self):
        new = [e['severity'] for e in self.events if e['seq'] > self.seen_seq]
        return 'error' if 'error' in new else 'warning' if 'warning' in new else 'info' if new else 'none'

    def _buffer_info(self, b):
        st = b['stats']
        std = math.sqrt(st['m2'] / st['n']) if st['n'] > 1 else (0.0 if st['n'] else None)
        return dict(name=b['name'], style=b['style'], capacity=b['capacity'], fill_mode=b['fill_mode'], count=len(b['data']),
                    filled_percent=round(100.0 * len(b['data']) / max(1, b['capacity']), 3), readings_total=st['n'],
                    mean=st['mean'] if st['n'] else None, std=std, minimum=st['lo'], maximum=st['hi'],
                    peak_to_peak=None if st['lo'] is None else st['hi'] - st['lo'], unit=b['data'][-1][2] if b['data'] else None)

    def _recent(self, name, n):
        b = self.buffers.get(name) or self.buffers['defbuffer1']
        k0 = b['wrapped'] + max(0, len(b['data']) - n)
        return [(k0 + i + 1, r, b['t0']) for i, r in enumerate(b['data'][-n:])]

    def display(self):
        """Texts of the Home screen and the swipe screens (p.39-47) as the 2450 formats them."""
        sf, mf = self.src_fn, self.meas_fn
        s, m = self.src[sf], self.meas[mf]
        fmt = self.nv['system.reading_format']
        last = self.last
        unit = UNITS[mf]
        rng_now = last['range'] if last else None
        reading = value_text(last['value'], last['unit'], last['range'] if last['unit'] == unit else None, m['measure.digits'], fmt) if last else '--------'
        on = self.powered and (self.output and self.control == 'local' or self.rt._electrically_active())
        srng = s['source.range'] if not s['source.range_auto'] else auto_range(s['source.level'], ranges_of(sf, sf))
        if not on:
            source = 'Output Off'                                          # p.46
        else:
            val = last['source'] if last and last.get('source') is not None and s['source.readback'] else s['source.level']
            source = value_text(val, UNITS[sf], srng, m['measure.digits'], fmt)
        ind = [x for x, c in (('AZERO', m['measure.autozero']), ('FILT', m['measure.filter']), ('MATH', m['measure.math']), ('REL', m['measure.rel'])) if c]
        ind += [f for f in (last['flags'] if last else []) if f.startswith('L')]
        bi = self._buffer_info(self.buffers[self.active_buffer])
        u = bi['unit'] or unit
        rb = (self.buffers[self.active_buffer]['data'] or [(0, 0, 0, 0, None)])[-1][4]
        st = {k: (value_text(bi[k], u, rb, 5, fmt) if bi[k] is not None else '--') for k in ('peak_to_peak', 'mean', 'std', 'minimum', 'maximum')}
        tm = self.tm['state']
        trig = {'continuous': 'CONT', 'manual': 'MAN'}.get(self.g['trigger_method']) or {'running': 'RUN', 'wait': 'WAIT', 'inactive': 'INACT'}.get(tm, 'IDLE')
        if self.control != 'local':                                        # p.242: remote control runs the trigger model of the software
            trig = 'RUN' if self.rt.phase == 'running' else 'IDLE'
        return dict(
            comm='Local' if self.control == 'local' else self.control, buffer=self.active_buffer, buffer_fill=bi['filled_percent'],
            script='Recording' if self.recording is not None else (self.running_script or 'No Script'), trigger=trig, event=self._unseen(),
            measure_title=f'MEASURE {mf.upper()} {"4-WIRE" if m["measure.sense"] == "4W" else "2-WIRE"}', reading=reading,
            measure_range='Auto' if (m['measure.range_auto'] and sf != mf) or mf == 'power' else range_text(rng_now if sf == mf and rng_now else m['measure.range'], unit),
            present_range=range_text(rng_now, unit) if rng_now else '', indicators=' '.join(ind),
            source_title='SOURCE ' + ('V' if sf == 'voltage' else 'I'), source=source,
            source_flag=' '.join(([] if not on else ['MEAS' if s['source.readback'] else 'PROG'])
                                 + [f for f in ('LIMIT', 'OVP') if on and last and f in last['flags']]),
            source_range='Auto' if s['source.range_auto'] else range_text(s['source.range'], UNITS[sf]),
            source_level=value_text(s['source.level'], UNITS[sf], srng, 5, 'prefix', space=False),
            limit=value_text(s['source.limit'], 'A' if sf == 'voltage' else 'V', auto_range(s['source.limit'], ranges_of('current' if sf == 'voltage' else 'voltage', sf)), 5,
                             'prefix', sign=False, space=False),
            nplc=f'{m["measure.nplc"]:.2f}', digits=f'{m["measure.digits"]}.5', stats=st, span=f'{bi["count"]} rdgs',
            limit_text=value_text(s['source.limit'], 'A' if sf == 'voltage' else 'V', None, 4, 'prefix', sign=False),
            user1='', user2='')


# ---------------------------------------------------------------- small helpers
def _row(i, r, t0):
    t, v, u, src, rng, flags = r
    return dict(index=i, time_s=round(t - (t0 or t), 6), value=v, unit=u, source=src, range=rng, flags=list(flags),
                text=value_text(v, u, None, 5) if abs(v) < OVERFLOW / 10 else 'Overflow')


def _flat(sn):
    out = dict(function=sn['function'])
    for k, v in sn['settings'].items():
        out[k.split('.', 1)[1]] = v
    if 'count' in sn:
        out['count'] = sn['count']
    return out


def _new_block(kind, buffer):
    return {'wait': dict(type='wait', event='display'), 'delay': dict(type='delay', delay_s=0.0), 'measure': dict(type='measure', buffer=buffer, count=1),
            'source_output': dict(type='source_output', on=True), 'buffer_clear': dict(type='buffer_clear', buffer=buffer),
            'config_recall': dict(type='config_recall', kind='source', list='', index=1), 'config_next': dict(type='config_next', kind='source', list=''),
            'config_prev': dict(type='config_prev', kind='source', list=''), 'branch_always': dict(type='branch_always', target=1),
            'branch_counter': dict(type='branch_counter', count=1, target=1), 'branch_once': dict(type='branch_once', target=1),
            'branch_on_event': dict(type='branch_on_event', event='display', target=1), 'branch_limit': dict(type='branch_limit', limit='limit1', target=1),
            'notify': dict(type='notify', line=1), 'digital_io': dict(type='digital_io', pattern=0), 'log_event': dict(type='log_event')}[kind]


def _buffer_name_ok(name):
    """p.172: starts with a letter, no period or underscore, up to 32 characters."""
    return isinstance(name, str) and 1 <= len(name) <= 32 and name[0].isalpha() and '.' not in name and '_' not in name and name.isprintable()


def _script_name_ok(name):
    return isinstance(name, str) and 1 <= len(name) <= 32 and name[0].isalpha() and all(c.isalnum() or c == '_' for c in name)


def _ipv4(s):
    parts = s.split('.') if isinstance(s, str) else []
    return len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)


def _days(y, m, d):
    """Days since 1970-01-01 (proleptic Gregorian)."""
    y -= m <= 2
    era = y // 400
    yoe = y - era * 400
    doy = (153 * (m + (-3 if m > 2 else 9)) + 2) // 5 + d - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    return era * 146097 + doe - 719468


def _iso_seconds(s):
    try:
        date, _, time = s.partition('T')
        y, mo, d = (int(x) for x in date.split('-'))
        hms = [int(x) for x in time.split(':')] if time else [0, 0, 0]
        h, mi, se = (hms + [0, 0, 0])[:3]
        if not (1 <= mo <= 12 and 1 <= d <= 31 and 0 <= h < 24 and 0 <= mi < 60 and 0 <= se < 60):
            return None
        return _days(y, mo, d) * 86400 + h * 3600 + mi * 60 + se
    except (ValueError, AttributeError):
        return None


def _iso_from_seconds(t):
    t = int(math.floor(t))
    z, rem = divmod(t, 86400)
    z += 719468
    era = z // 146097
    doe = z - era * 146097
    yoe = (doe - doe // 1460 + doe // 36524 - doe // 146096) // 365
    y = yoe + era * 400
    doy = doe - (365 * yoe + yoe // 4 - yoe // 100)
    mp = (5 * doy + 2) // 153
    d = doy - (153 * mp + 2) // 5 + 1
    m = mp + (3 if mp < 10 else -9)
    y += m <= 2
    return f'{y:04d}-{m:02d}-{d:02d}T{rem // 3600:02d}:{rem % 3600 // 60:02d}:{rem % 60:02d}'

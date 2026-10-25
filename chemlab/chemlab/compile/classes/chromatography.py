"""Chromatography and electrophoresis class ("chromatography"): GC, GC-IMS, HPLC, SFC, GPC/SEC, ion chromatography, AF4,
capillary electrophoresis, TLC densitometry, flash chromatography, centrifugal partition chromatography, gel electrophoresis.

Verbs: method KEY=VALUE ..., load [SAMPLE], run [--pos P] [--wait-ready T], stop, chromatogram [--save-to F], quantify, flow on|off,
set-temp T [--zone Z], status.  Members and their mappings: configs/adapters/chromatography.json.  power / open / close come from
the shared layer (configs/adapters/shared.json).

The method dictionary (canonical keys; each member maps the ones it has, `chemlab help INST method` lists them):

    flow=1mL/min           mobile phase / carrier / column flow                 (flow)
    inj=10uL               injection volume                                     (volume)
    column-temp=40C        column (or column compartment) temperature           (temperature)
    oven=40C:2min..250C@10C/min:5min   GC oven program: start temperature[:hold], then ..final@rate[:hold] per ramp
    inlet=250C             GC inlet / injector temperature                      (temperature)
    det=300C               detector temperature                                 (temperature)
    split=50               split ratio (50 or 50:1)
    inlet-mode=splitless   split | splitless
    run=20min              run time                                             (duration)
    wl=254nm[,280nm]       detection wavelength(s)                              (wavelength)
    gradient=0:10%,10min:90%   time:fraction of the strong solvent / modifier / B channel
    pmax=400bar            pressure limit                                       (pressure)
    rate=5Hz               detector data rate                                   (frequency)
    speed=1600rpm          rotor speed (CPC)                                    (rotation)
    eluent=10mM            eluent concentration (IC)                            (concentration)
    voltage=200V current=40mA constant=voltage   power supply programme (gel electrophoresis)
    lamp=deuterium         lamp / light source

Any other KEY is passed through by the member's own native argument name (still converted into the unit the argument declares),
e.g. bpr_pa=130bar, mode=anion, steps=[...].  Flat method capabilities that need every argument keep the instrument's current
value where it reports one and otherwise take the contract's example method; the reply lists which values were kept and which
came from the default, and nothing is sent when a value is out of the contract range (usage error, exit 2).

Vendor-neutral terms: USP <621> / Ph. Eur. 2.2.46 (retention time, peak area, resolution), ASTM E1947 / E2077 (ANDI chromatography
data: a trace plus a peak table), AnIML / Allotrope result records, OPC UA ADI (OPC 10020) analyser states.  chromatogram returns
one shape for every member: {complete, trace: {x, y, x_unit, y_unit}, peaks: [{rt_s, area, height}]}; where a member gives only
a trace, the class finds the peaks itself (local maxima above 2 % of the signal range, trapezoid areas between valleys).
"""
import copy
import math
import re

from ...contract import validate
from ...generic import CmdError, coerce
from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import CompileError, invalid, refused, unsupported
from ..jobs import OperationJob, StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt

REF = ('Agilent 8890 / 7890B GC user and operating manuals (method set points, oven program, Prep Run / Start); Agilent 1260 / 1290 '
       'Infinity LC, SFC and GPC/SEC manuals; Thermo Dionex Integrion operator manual; G.A.S. FlavourSpec manual; Wyatt Eclipse AF4 '
       'manual; SCIEX PA 800 Plus system guide; CAMAG TLC Scanner 4 manual; Biotage Selekt user manual; Gilson CPC 250 manual; '
       'Bio-Rad Mini-PROTEAN Tetra and PowerPac manuals')
ON_OFF = ['on', 'off']

# canonical method keys: name -> (kind, help)
KEYS = {
    'flow': ('flow', 'mobile phase / carrier / column flow, e.g. 1mL/min'),
    'inj': ('volume', 'injection volume, e.g. 10uL'),
    'column-temp': ('temperature', 'column temperature, e.g. 40C'),
    'oven': ('program', 'GC oven program, e.g. 40C:2min..250C@10C/min:5min (or just 80C)'),
    'inlet': ('temperature', 'inlet / injector temperature, e.g. 250C'),
    'det': ('temperature', 'detector temperature, e.g. 300C'),
    'split': ('number', 'split ratio, e.g. 50 or 50:1'),
    'inlet-mode': ('choice', 'inlet mode: split or splitless'),
    'run': ('duration', 'run time, e.g. 20min'),
    'wl': ('wavelengths', 'detection wavelength(s), e.g. 254nm or 254nm,280nm'),
    'gradient': ('gradient', 'time:fraction list, e.g. 0:10%,10min:90%'),
    'pmax': ('pressure', 'pressure limit, e.g. 400bar'),
    'rate': ('frequency', 'detector data rate, e.g. 5Hz'),
    'speed': ('rotation', 'rotor speed, e.g. 1600rpm'),
    'eluent': ('concentration', 'eluent concentration, e.g. 10mM'),
    'voltage': ('voltage', 'power supply voltage, e.g. 200V'),
    'current': ('current', 'power supply current, e.g. 40mA'),
    'constant': ('choice', 'what the power supply holds constant: voltage or current'),
    'lamp': ('choice', 'lamp / light source, e.g. deuterium'),
}
PEAK_RT_KEYS = ('rt_s', 'retention_time_s', 'migration_time_s', 'apex_time_s', 'time_s', 'distance_m', 'migration_m', 'rf')
PEAK_AREA_KEYS = ('area', 'area_au_s', 'area_C', 'area_s', 'area_au_m', 'corrected_area_au', 'intensity')
PEAK_HEIGHT_KEYS = ('height', 'height_au', 'height_A')


def dig(obj, path):
    """obj['a']['b'] for path 'a.b' ('' -> obj)."""
    if not path:
        return obj
    for part in path.split('.'):
        if isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
    return obj


def coerce_value(text, schema, key):
    """generic.coerce (JSON by the schema's type) with its usage error turned into the class error format."""
    try:
        return coerce(text, schema, key)
    except CompileError:
        raise
    except CmdError as exc:
        raise invalid(exc.code, exc.message, (exc.fix or '') + ' (write JSON inside single quotes in the shell, e.g. %s=\'[...]\')' % key)


def find_peaks(x, y, min_frac=0.02, limit=30):
    """Peaks of a trace: local maxima higher than min_frac of the signal range above the lowest value; each peak's area is the
    trapezoid integral above that baseline between the valleys on either side."""
    pts = [(float(a), float(b)) for a, b in zip(x or [], y or []) if finite(a) and finite(b)]
    if len(pts) < 3:
        return []
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    base, top = min(ys), max(ys)
    span = top - base
    if span <= 0:
        return []
    thr = base + min_frac * span
    out, i, n = [], 1, len(ys)
    while i < n - 1:
        if ys[i] > thr and ys[i] >= ys[i - 1] and ys[i] > ys[i + 1]:
            lo = i
            while lo > 0 and ys[lo - 1] <= ys[lo]:
                lo -= 1
            hi = i
            while hi < n - 1 and ys[hi + 1] <= ys[hi]:
                hi += 1
            area = sum((ys[k] + ys[k + 1] - 2 * base) * (xs[k + 1] - xs[k]) / 2 for k in range(lo, hi))
            half = base + (ys[i] - base) / 2
            left = next((xs[k] for k in range(i, lo - 1, -1) if ys[k] <= half), xs[lo])
            right = next((xs[k] for k in range(i, hi + 1) if ys[k] <= half), xs[hi])
            out.append({'rt_s': xs[i], 'height': ys[i] - base, 'area': area, 'width_half_s': right - left, 'start_s': xs[lo], 'end_s': xs[hi]})
            i = hi
        i += 1
    out.sort(key=lambda p: -p['height'])
    out = out[:limit]
    out.sort(key=lambda p: p['rt_s'])
    return out


class ChromatographyAdapter(ClassAdapter):
    COVERS = {'start': 'run', 'result': 'chromatogram'}     # shared start / result are replaced by run / chromatogram
    CLASS = 'chromatography'
    TITLE = 'chromatography and electrophoresis'
    ABOUT = ('Separation instruments: set the method with one dictionary of keys (flow, oven program, injection, wavelength, '
             'gradient, ...), load the sample, start the run (a job), stop it, read the chromatogram (trace + peak table, one shape '
             'for every instrument), quantify against standards, switch the mobile phase / carrier on or off, set zone temperatures.')
    STANDARDS = ('USP <621> / Ph. Eur. 2.2.46 terms (retention time, peak area); ASTM E1947 / E2077 (ANDI chromatography data); '
                 'AnIML / Allotrope results; OPC UA ADI (OPC 10020) analyser states; no SiLA 2 core feature exists for chromatographs')
    ALWAYS = ('status',)
    UNIT_KINDS = ('flow', 'volume', 'temperature', 'duration', 'wavelength', 'pressure', 'heating_rate', 'fraction', 'frequency',
                  'rotation', 'concentration', 'voltage', 'current')
    PATH_GROUPS = (('run', 'stop', 'chromatogram'),)
    CODES = {
        'BUSY': ('BUSY', '{inst} is busy (a run, test or start-up is in progress).',
                 'wait for it (`chemlab run {inst} status`), or stop the run first (`chemlab run {inst} stop`).'),
        'RUN_ACTIVE': ('BUSY', 'a run is in progress on {inst}.', 'wait for it or stop it (`chemlab run {inst} stop`).'),
        'NOT_READY': ('NOT_READY', '{inst} is not ready to start (temperatures, flows or pressure not at their set points).',
                      'wait until it is ready: `chemlab run {inst} run --wait-ready 15min` waits and then starts.'),
        'LAMP_NOT_READY': ('NOT_READY', 'the lamp of {inst} is off or still warming up.',
                           'select a lamp (`chemlab run {inst} method lamp=deuterium`) and wait for the warm-up '
                           '(`chemlab run {inst} run --wait-ready 10min`).'),
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'NO_SAMPLE': ('NO_SAMPLE', 'no sample is loaded in {inst}.', 'load one first (`chemlab run {inst} load SAMPLE`).'),
        'SAMPLE_PRESENT': ('SAMPLE_PRESENT', 'a sample is already loaded in {inst}.', 'run it, or unload it with `chemlab call {inst} sample.unload`.'),
        'LID_ON': ('LID_ON', 'the lid of {inst} is on.', 'take it off first (`chemlab call {inst} lid.remove`).'),
        'NOT_RUNNING': ('NOT_RUNNING', 'nothing is running on {inst}.', 'nothing to stop; see `chemlab run {inst} status`.'),
        'VIAL_EMPTY': ('VIAL_EMPTY', 'the sample vial of {inst} is empty.', 'ask the lab operator for a new vial.'),
        'PLANT_REQUIRED': ('NOT_SET_UP', '{inst} is not set up in this lab.', 'ask the lab operator.'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        return {
            'method': Verb('method', 'set the separation method: KEY=VALUE ... (class keys with units, or native argument names)',
                           self.h_method, variadic=('KEY=VALUE', 'a method setting'),
                           preconditions=['every value parses into its unit and lies in the contract range', 'not running',
                                          'power on (where the instrument needs it)'],
                           errors=['UNKNOWN_SETTING, BAD_SETTING, OUT_OF_RANGE (usage)', 'BUSY, POWER_OFF (refused)'],
                           example='chemlab run INST method flow=1mL/min inj=10uL column-temp=40C run=20min', reference=REF,
                           details='class keys: ' + '; '.join('%s (%s)' % (k, h) for k, (_, h) in KEYS.items())),
            'load': Verb('load', 'load a sample into the injector / sample position (opens and closes the door or lid itself)',
                         self.h_load, positionals=[Arg('SAMPLE', 'the sample id (as the lab registered it)', required=False)],
                         options={'pos': Opt('sample position (gel: gel position 1-4)', metavar='N'),
                                  'lane': Opt('lane / well (gel)', metavar='N'),
                                  'volume': Opt('volume to load, e.g. 10uL', kind='volume', metavar='V')},
                         preconditions=['not running', 'no sample loaded already (where the instrument holds one)', 'power on where needed'],
                         errors=['MISSING_ARGUMENT, MISSING_OPTION (usage)', 'BUSY, SAMPLE_PRESENT (refused)'],
                         example='chemlab run INST load mix1', reference=REF),
            'run': Verb('run', 'start the separation (returns job-N: chemlab status|wait|cancel job-N)', self.h_run, long_action=True,
                        options={'pos': Opt('sample position for instruments that inject from a tray, e.g. 1:5 (drawer:position)', metavar='POS'),
                                 'wait-ready': Opt('first wait up to TIME until the instrument is ready, then start', kind='duration', metavar='TIME')},
                        preconditions=['power on', 'not running', 'method set, sample loaded, flow on (where the instrument has them)',
                                       'ready (temperatures / flows / lamp), or --wait-ready'],
                        errors=['MISSING_OPTION (usage)', 'POWER_OFF, BUSY, NO_METHOD, NO_SAMPLE, FLOW_OFF, NOT_READY (refused)'],
                        example='chemlab run INST run --wait-ready 10min', reference=REF),
            'stop': Verb('stop', 'stop / abort the running separation', self.h_stop, preconditions=['a run is in progress'],
                         errors=['NOT_RUNNING (refused)'], example='chemlab run INST stop', reference=REF),
            'chromatogram': Verb('chromatogram', 'the chromatogram of the current / last run: trace and peak table', self.h_chromatogram,
                                 readonly=True, options={'save-to': save_to_opt(),
                                                         'signal': Opt('which signal to use as the trace (member specific, e.g. uv_au)', metavar='NAME'),
                                                         'pos': Opt('gel position / TLC track to read', metavar='N')},
                                 example='chemlab run INST chromatogram --save-to run1.json', reference=REF),
            'quantify': Verb('quantify', 'concentration of the peak in a retention window from external standards', self.h_quantify,
                             readonly=True,
                             options={'standards': Opt('standards CONC=AREA,... e.g. 0.1mM=0.012,0.5mM=0.061', metavar='LIST', required=True),
                                      'window': Opt('retention window START..END, e.g. 2.5min..3.5min', metavar='A..B', required=True),
                                      'drift': Opt('GC-IMS drift-time ratio window, e.g. 1.0..1.3 (default: the whole range)', metavar='A..B'),
                                      'save-to': save_to_opt()},
                             preconditions=['a completed run', 'standards with positive concentrations'],
                             errors=['BAD_STANDARDS, BAD_WINDOW (usage)', 'NO_RUN (refused)'],
                             example='chemlab run INST quantify --standards 0.1mM=0.01,0.5mM=0.05 --window 100s..200s', reference=REF),
            'flow': Verb('flow', 'switch the mobile phase / carrier gas / pump on or off', self.h_flow,
                         positionals=[Arg('on|off', 'on or off', choices=ON_OFF)], preconditions=['power on'],
                         errors=['POWER_OFF (refused)'], example='chemlab run INST flow on', reference=REF),
            'set-temp': Verb('set-temp', 'set a zone temperature (oven, inlet, detector, column, capillary, ...)', self.h_set_temp,
                             positionals=[Arg('TEMP', 'set point, e.g. 250C (a bare number is °C)', kind='temperature')],
                             options={'zone': Opt('which zone (default: the member\'s main zone)', metavar='ZONE')},
                             preconditions=['set point within the contract range of the zone', 'power on'],
                             errors=['OUT_OF_RANGE, UNKNOWN_ZONE (usage)', 'POWER_OFF (refused)'],
                             example='chemlab run INST set-temp 250C --zone inlet', reference=REF),
        }

    # ------------------------------------------------------------------ state helpers
    def sv(self, values, spec):
        """A slot value: 'slot', 'slot#key' or 'slot#a.b' (keys inside an object slot)."""
        if spec is None:
            return None
        sid, _, key = spec.partition('#')
        get = values.v if hasattr(values, 'v') else values.get
        v = get(sid)
        return dig(v, key) if key else v

    def running(self, values):
        """True when a run is in progress (from the member's run slot)."""
        s = self.slot('run')
        if not s:
            return False
        v = self.sv(values, s)
        if self.param('running_when_set'):
            return v is not None
        return v in (self.param('running') or [])

    def need_power(self, ctx):
        s = self.slot('power')
        if s:
            ctx.check(bool(self.sv(ctx, s)), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                      'switch it on: `chemlab run %s power on`.' % self.instrument_id, label='power on')

    def need_idle(self, ctx, what):
        ctx.check(not self.running(ctx), 'BUSY', 'a run is in progress on %s, so it cannot %s now.' % (self.instrument_id, what),
                  'wait for it (`chemlab run %s status`) or stop it (`chemlab run %s stop`).' % (self.instrument_id, self.instrument_id),
                  label='not running')

    def cond(self, ctx, c, cap=None, args=None):
        """A step condition {"state", "eq" | "ne" | "lt" | "gt" | "empty"} ("state": a slot id, or slot#key inside an object slot);
        "$max" / "$min" compare with the range of the step's own argument that carries the same token."""
        if not c:
            return True
        v = self.sv(ctx, c['state'])
        if 'eq' in c:
            return v == c['eq']
        if 'ne' in c:
            return v != c['ne']
        if 'empty' in c:
            return (not v) == c['empty']
        for op in ('lt', 'gt'):
            if op in c:
                lim = self.resolve(c[op], cap, args)
                if not (finite(v) and finite(lim)):
                    return op == 'lt'
                return v < lim - 1e-9 if op == 'lt' else v > lim + 1e-9
        return True

    def resolve(self, token, cap, args):
        """'$max' / '$min' -> the range end of the step's argument that carries the same token; '$param:x' -> params[x]."""
        if not isinstance(token, str) or not token.startswith('$'):
            return token
        if token.startswith('$param:'):
            return self.param(token.split(':', 1)[1])
        for a, val in (args or {}).items():
            if val == token:
                lo, hi = self.arg_range(cap, a)
                return hi if token == '$max' else lo
        return None

    def steps(self, ctx, steps, what):
        """Run conditional native steps [{"cap", "args", "if", "step"}] (door / lid / prep / syringe)."""
        for st in steps or []:
            raw = dict(st.get('args') or {})
            if not self.cond(ctx, st.get('if'), st['cap'], raw):
                continue
            args = {a: self.resolve(v, st['cap'], raw) for a, v in raw.items()}
            ctx.call(st['cap'], args, st.get('step') or '%s (%s)' % (what, st['cap']))

    def ready_fix(self):
        spec = (self.vmap('run') or {}).get('ready') or {}
        return (spec.get('fix') or 'wait until it is ready: `chemlab run {inst} run --wait-ready 15min` waits and then starts.').format(
            inst=self.instrument_id)

    def ready(self, ctx):
        """(ready, why) from the member's "ready" spec: {"state", "eq"} | {"state", "empty"} | {"near": [[actual, target], ...]}."""
        spec = (self.vmap('run') or {}).get('ready')
        if not spec:
            return True, ''
        if 'near' in spec:
            tol = float(self.param('ready_tol_k', 0.5))
            bad = []
            for a, t in spec['near']:
                va, vt = self.sv(ctx, a), self.sv(ctx, t)
                if finite(va) and finite(vt) and abs(va - vt) > tol:
                    bad.append('%s %s (target %s)' % (a.split('.')[0], units.fmt(va, 'temperature'), units.fmt(vt, 'temperature')))
            return not bad, ', '.join(bad)
        ok = self.cond(ctx, spec)
        why = ''
        if not ok:
            reasons = self.sv(ctx, spec['reasons']) if spec.get('reasons') else None
            why = '; '.join(map(str, reasons)) if isinstance(reasons, list) and reasons else '%s = %s' % (spec['state'], short(self.sv(ctx, spec['state']), 60))
        return ok, why

    # ------------------------------------------------------------------ summary
    def summary(self, values):
        p = self.slot('power')
        on = self.sv(values, p) if p else True
        if p and not on:
            return ['power off'], {'power': False}
        js = {'power': True}
        bits = []
        rs = self.slot('run')
        if rs:
            st = self.sv(values, rs)
            run = self.running(values)
            js['running'] = run
            js['run_state'] = st if not isinstance(st, dict) else st.get('kind', 'running')
            word = js['run_state'] if isinstance(js['run_state'], str) else None
            bits.append(('running (%s)' % word if word and word not in ('running', 'run') else 'running') if run else
                        ('idle' if word in (None, 'idle') else 'idle (last: %s)' % word))
        spec = (self.vmap('run') or {}).get('ready')
        if spec and 'state' in spec:
            try:
                ok = self.cond(values, spec)
                js['ready'] = ok
                bits.append('ready' if ok else 'not ready')
            except Exception:                                             # noqa: BLE001 - a summary never breaks read
                pass
        for role, word in (('method', 'method set'), ('sample', 'sample loaded'), ('flow', 'flow on')):
            s = self.slot(role)
            if s:
                v = self.sv(values, s)
                ok = bool(v) and v not in ('none', 'off', 'disconnected')
                js[role] = ok
                bits.append(word if ok else word.replace('set', 'not set').replace('loaded', 'not loaded').replace(' on', ' off'))
        temps = []
        for s in self.slot('temps') or []:
            v = self.slot_si(values, s)
            if finite(v):
                parts = s.split('.')
                label = parts[1] if parts[0] in ('thermal', 'temperature') and len(parts) > 1 else parts[0]
                temps.append('%s %s' % (re.sub(r'_(indicated_|temperature_)?k$', '', label), units.fmt(v, 'temperature')))
                js.setdefault('temperatures_k', {})[s] = v
        lines = ['; '.join(bits) or 'on']
        if temps:
            lines.append('temperatures: ' + ', '.join(temps))
        return lines, js

    # ------------------------------------------------------------------ help: the member's method keys
    def verb_help(self, name):
        r = ClassAdapter.verb_help(self, name)
        if name == 'method' and name not in self.unsupported_reasons():
            lines = ['', 'method keys of %s:' % self.instrument_id]
            for call in (self.vmap('method') or {}).get('calls') or []:
                cap = call.get('cap') or call.get('temp_cap')
                for k, spec in (call.get('keys') or {}).items():
                    arg = spec if isinstance(spec, str) else (spec.get('arg') or spec.get('field') or 'program')
                    lines.append('  %-12s -> %s %s%s' % (k, cap, arg, (' (%s)' % ', '.join('%s=%s' % kv for kv in (call.get('args') or {}).items()))
                                                       if call.get('args') else ''))
                if call.get('passthrough', True) and not call.get('object') and call.get('cap'):
                    props = self.cap_props(call['cap'])
                    rest = [a for a in props if a not in self.mapped_args(call)]
                    if rest:
                        lines.append('  native names passed through to %s: %s' % (call['cap'], ', '.join(rest)))
            r['text'] += '\n'.join(lines) + '\n'
        return r

    def cap_props(self, cap):
        return ((self.lab.contract['capabilities'].get(cap) or {}).get('input_schema') or {}).get('properties') or {}

    def cap_schema_all(self, cap):
        return (self.lab.contract['capabilities'].get(cap) or {}).get('input_schema') or {'type': 'object', 'properties': {}}

    @staticmethod
    def mapped_args(call):
        return {(s if isinstance(s, str) else (s.get('arg') or s.get('field'))) for s in (call.get('keys') or {}).values()}

    # ------------------------------------------------------------------ mapping check (the class's own data shapes)
    def check_mapping(self, lab):
        """The base check plus what it cannot see: method keys -> native argument names, "keep" slots, "*_arg" names of load /
        run / quantify, step conditions and ready specs ("state": slot#key)."""
        problems = ClassAdapter.check_mapping(self, lab)
        caps = lab.contract['capabilities']
        visible = {s.lower() for s in lab.view.slots}

        def props(cap):
            return ((caps.get(cap) or {}).get('input_schema') or {}).get('properties') or {}

        def slot_ok(s, where):
            for x in (s if isinstance(s, list) else [s]):
                if isinstance(x, str) and x.split('#', 1)[0].lower() not in visible:
                    problems.append('%s: no visible state slot %s' % (where, x))

        def conds(o, where):
            if isinstance(o, dict):
                for k, v in o.items():
                    if k in ('state', 'reasons') and isinstance(v, str):
                        slot_ok(v, where + '.' + k)
                    elif k == 'near':
                        for pair in v:
                            slot_ok(pair, where + '.near')
                    else:
                        conds(v, where + '.' + k)
            elif isinstance(o, list):
                for x in o:
                    conds(x, where)
        for verb, m in (self.map.get('verbs') or {}).items():
            conds(m, 'verbs.' + verb)
            if verb == 'method':
                for n, call in enumerate(m.get('calls') or []):
                    w = 'verbs.method.calls[%d]' % n
                    if call.get('style') == 'program':
                        for a, c in (('temp_arg', 'temp_cap'), ('time_arg', 'time_cap'), ('index_arg', 'ramp_cap'), ('rate_arg', 'ramp_cap'),
                                     ('final_arg', 'ramp_cap'), ('hold_arg', 'ramp_cap')):
                            if call.get(a) and call.get(c) and call[a] not in props(call[c]):
                                problems.append('%s: %s has no argument %s' % (w, call[c], call[a]))
                        if call.get('prog_cap') and call.get('object') not in props(call['prog_cap']):
                            problems.append('%s: %s has no argument %s' % (w, call['prog_cap'], call.get('object')))
                        continue
                    pr = props(call.get('cap'))
                    if call.get('object'):
                        if call['object'] not in pr:
                            problems.append('%s: %s has no argument %s' % (w, call['cap'], call['object']))
                        continue
                    for k, spec in (call.get('keys') or {}).items():
                        arg = spec if isinstance(spec, str) else spec.get('arg')
                        if arg not in pr:
                            problems.append('%s.keys.%s: %s has no argument %s' % (w, k, call['cap'], arg))
                    for arg, s in (call.get('keep') or {}).items():
                        if arg not in pr:
                            problems.append('%s.keep: %s has no argument %s' % (w, call['cap'], arg))
                        slot_ok(s, w + '.keep.' + arg)
            elif isinstance(m, dict) and m.get('cap'):
                pr = props(m['cap'])
                for k, v in m.items():
                    if k.endswith('_arg') and isinstance(v, str) and v not in pr:
                        problems.append('verbs.%s.%s: %s has no argument %s' % (verb, k, m['cap'], v))
                for a in (m.get('pos') or {}).get('args') or []:
                    if a not in pr:
                        problems.append('verbs.%s.pos: %s has no argument %s' % (verb, m['cap'], a))
            for z, spec in ((m or {}).get('zones') or {}).items() if isinstance(m, dict) else []:
                if spec.get('arg') not in props(spec.get('cap')):
                    problems.append('verbs.%s.zones.%s: %s has no argument %s' % (verb, z, spec.get('cap'), spec.get('arg')))
        t = (self.map.get('targets') or {}).get('zone')
        sm = (self.map.get('verbs') or {}).get('set-temp') or {}
        if t and sm.get('cap'):
            enum = (props(sm['cap']).get(t['arg']) or {}).get('enum')
            if t['arg'] not in props(sm['cap']):
                problems.append('targets.zone: %s has no argument %s' % (sm['cap'], t['arg']))
            elif enum:
                problems += ['targets.zone: %s is not a %s value' % (v, t['arg']) for v in (t.get('values') or {}).values() if v not in enum]
        return problems

    # ------------------------------------------------------------------ refusals
    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code in ('NOT_READY', 'PRECONDITION_FAILED') and cap == (self.vmap('run') or {}).get('cap'):
            ctx.refresh()
            ok, why = self.ready(ctx)
            if not ok:
                return refused('NOT_READY', '%s is not ready to start: %s.' % (self.instrument_id, why), self.ready_fix())
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)

    # ------------------------------------------------------------------ method
    def parse_pairs(self, ctx, items):
        out = {}
        for item in items:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write method KEY=VALUE ... (see `chemlab help %s method`).' % self.instrument_id))
            if k in out:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % k, 'give it once.'))
            out[k] = v
        return out

    def value_for(self, ctx, key, text, spec, call, schema):
        """One KEY=VALUE -> the native value (declared unit for flat arguments, SI for settings objects)."""
        kind = spec.get('kind') or (KEYS[key][0] if key in KEYS else None)
        obj = bool(call.get('object'))
        if kind in units.KINDS:
            if obj or not schema:
                return units.parse(text, kind, what=key)
            if schema.get('type') == 'array':                            # e.g. GPC zone targets: every zone gets the value
                item = schema.get('items') or {}
                one = units.parse_for(text, item, kind, what=key)
                n = len(spec.get('like') or []) or max(1, int(schema.get('minItems', 1)))
                return [one] * n
            v = units.parse_for(text, schema, kind, what=key)
            return int(round(v)) if schema.get('type') == 'integer' and abs(v - round(v)) < 1e-9 else v
        if kind == 'number':
            t = text.strip()
            t = t[:-2] if t.endswith(':1') else t
            try:
                v = float(t)
            except ValueError:
                ctx.fail(invalid('BAD_SETTING', '%s=%s is not a number.' % (key, text), 'write e.g. %s=50.' % key))
            return int(v) if schema.get('type') == 'integer' and v.is_integer() else v
        if kind == 'choice':
            allowed = list((spec.get('values') or {}).keys()) or list(schema.get('enum') or [])
            hit = [a for a in allowed if str(a).lower() == text.strip().lower()]
            if not hit:
                ctx.fail(invalid('BAD_SETTING', '%s=%s is not allowed on %s.' % (key, text, self.instrument_id),
                                 'use one of: %s.' % ', '.join(map(str, allowed))))
            return (spec.get('values') or {}).get(hit[0], hit[0])
        if kind == 'wavelengths':
            parts = [x for x in text.split(',') if x.strip()]
            if schema.get('type') == 'array':
                item = schema.get('items') or {}
                return [units.parse_for(x, item, 'wavelength', what=key) for x in parts]
            if len(parts) != 1:
                ctx.fail(invalid('BAD_SETTING', '%s takes one wavelength.' % self.instrument_id, 'give one, e.g. wl=254nm.'))
            return units.parse(parts[0], 'wavelength', what=key) if obj else units.parse_for(parts[0], schema, 'wavelength', what=key)
        if kind == 'gradient':
            return self.parse_gradient(ctx, key, text, spec, schema)
        return coerce_value(text, schema, key)

    def parse_gradient(self, ctx, key, text, spec, schema):
        item = schema.get('items') or {}
        props = item.get('properties') or {}
        tkey = next((p for p, s in props.items() if units.declared(s) == 's'), 'time_s')
        fkey = spec.get('fraction') or next((p for p in props if p != tkey), 'fraction')
        out = []
        for part in [x for x in text.split(',') if x.strip()]:
            t, colon, f = part.partition(':')
            if not colon:
                ctx.fail(invalid('BAD_GRADIENT', 'gradient point %r is not TIME:FRACTION.' % part, 'write e.g. gradient=0:10%,10min:90%.'))
            if not re.search(r'[%a-zA-Z]\s*$', f.strip()):
                ctx.fail(invalid('BAD_GRADIENT', 'the fraction in %r has no unit.' % part, 'give it as a percentage, e.g. 10%.'))
            out.append({tkey: units.parse_for(t, props.get(tkey) or 's', 'duration', what='gradient time'),
                        fkey: units.parse(f, 'fraction', what='gradient fraction')})
        if not out:
            ctx.fail(invalid('BAD_GRADIENT', 'the gradient is empty.', 'write e.g. gradient=0:10%,10min:90%.'))
        return out

    def parse_program(self, ctx, text):
        """'40C:2min..250C@10C/min:5min' -> (T0_K, hold0_s, [(final_K, rate_K_s, hold_s)])."""
        segs = text.split('..')
        t0, _, h0 = segs[0].partition(':')
        start = units.parse(t0, 'temperature', what='oven start temperature')
        hold0 = units.parse(h0, 'duration', what='oven hold') if h0 else 0.0
        ramps = []
        for seg in segs[1:]:
            m = re.fullmatch(r'\s*([^@:]+)@([^:]+)(?::(.+))?\s*', seg)
            if not m:
                ctx.fail(invalid('BAD_PROGRAM', 'oven ramp %r is not FINAL@RATE[:HOLD].' % seg, 'write e.g. oven=40C:2min..250C@10C/min:5min.'))
            final = units.parse(m.group(1), 'temperature', what='ramp final temperature')
            rate = units.parse(m.group(2), 'heating_rate', what='ramp rate')
            hold = units.parse(m.group(3), 'duration', what='ramp hold') if m.group(3) else 0.0
            if rate <= 0:
                ctx.fail(invalid('BAD_PROGRAM', 'ramp rate %s must be above zero.' % m.group(2), 'give a rate such as 10C/min.'))
            ramps.append((final, rate, hold))
        return start, hold0, ramps

    def program_calls(self, ctx, call, text):
        """The native calls of an oven program, member style "settings" (one settings object) or "calls" (one call per ramp)."""
        start, hold0, ramps = self.parse_program(ctx, text)
        lo, hi = self.arg_range_si(call['temp_cap'], call['temp_arg'])        # the oven zone's range bounds every program temperature
        for t in [start] + [r[0] for r in ramps]:
            if (lo is not None and t < lo - 1e-9) or (hi is not None and t > hi + 1e-9):
                ctx.fail(invalid('OUT_OF_RANGE', 'oven temperature %s is outside the oven range of %s (%s to %s).' % (
                    units.fmt(t, 'temperature'), self.instrument_id, units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature')),
                    'keep the program between %s and %s.' % (units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature'))))
        out = [(call['temp_cap'], dict(call.get('temp_args') or {}, **{call['temp_arg']: self.arg_value(call['temp_cap'], call['temp_arg'], start)}),
                'oven start temperature %s' % units.fmt(start, 'temperature'))]
        if call.get('time_cap'):
            out.append((call['time_cap'], {call['time_arg']: self.arg_value(call['time_cap'], call['time_arg'], hold0)}, 'oven initial hold'))
            lo, hi = self.arg_range(call['ramp_cap'], call['index_arg'])
            if hi is not None and len(ramps) > hi:
                ctx.fail(invalid('BAD_PROGRAM', '%s takes at most %d ramps.' % (self.instrument_id, hi), 'shorten the program.'))
            rc = call['ramp_cap']
            for n, (final, rate, hold) in enumerate(ramps, 1):
                out.append((rc, {call['index_arg']: n, call['rate_arg']: self.arg_value(rc, call['rate_arg'], rate),
                                 call['final_arg']: self.arg_value(rc, call['final_arg'], final),
                                 call['hold_arg']: self.arg_value(rc, call['hold_arg'], hold)}, 'oven ramp %d' % n))
            if hi is None or len(ramps) < hi:
                out.append((rc, {call['index_arg']: len(ramps) + 1, call['rate_arg']: 0.0}, 'end of the oven program'))
        else:
            n_max = int(self.param('oven_ramps', 32))
            if len(ramps) > n_max:
                ctx.fail(invalid('BAD_PROGRAM', '%s takes at most %d ramps.' % (self.instrument_id, n_max), 'shorten the program.'))
            st = {call['hold0']: hold0}
            for n, (final, rate, hold) in enumerate(ramps, 1):
                st[call['rate'].format(n=n)] = rate
                st[call['final'].format(n=n)] = final
                st[call['hold'].format(n=n)] = hold
            if len(ramps) < n_max:
                st[call['rate'].format(n=len(ramps) + 1)] = 0.0
            out.append((call['prog_cap'], dict(call.get('prog_args') or {}, **{call['object']: st}), 'oven program'))
        return out

    def keep_value(self, ctx, cap, arg, slot, schema):
        """The current value of an argument from its slot(s), in the argument's unit, or None (missing / not valid for the call)."""
        if isinstance(slot, list):
            vals = [self.sv(ctx, s) for s in slot]
            if any(v is None for v in vals):
                return None
            v = vals
        else:
            v = self.sv(ctx, slot)
        if v is None:
            return None
        sid = slot[0] if isinstance(slot, list) else slot
        su = units.declared((self.lab.contract['state_slots'].get(sid.split('#')[0]) or {}))
        au = units.declared(schema.get('items') if isinstance(v, list) and schema.get('type') == 'array' else schema)
        if su and au and su != au and units.unit_kind(su) and units.unit_kind(su) == units.unit_kind(au):
            v = [units.to_declared(units.from_declared(x, su), au) for x in v] if isinstance(v, list) else units.to_declared(units.from_declared(v, su), au)
        if schema.get('type') == 'integer' and finite(v) and float(v).is_integer():
            v = int(v)
        return v if not validate(schema, v, arg) else None

    def h_method(self, ctx, p):
        m = self.vmap('method')
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'see `chemlab help %s method`.' % self.instrument_id))
        given = self.parse_pairs(ctx, p.extra)
        calls = m.get('calls') or []
        assign = {i: {} for i in range(len(calls))}
        for k, text in given.items():
            where = next((i for i, c in enumerate(calls) if k in (c.get('keys') or {})), None)
            if where is None:
                where = next((i for i, c in enumerate(calls) if c.get('passthrough', True) and not c.get('object') and c.get('cap')
                              and k in self.cap_props(c['cap'])), None)
            if where is None:
                known = sorted({kk for c in calls for kk in (c.get('keys') or {})})
                native = sorted({a for c in calls if c.get('passthrough', True) and not c.get('object') and c.get('cap')
                                 for a in self.cap_props(c['cap'])})
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (self.instrument_id, k),
                                 'class keys here: %s; native names: %s (see `chemlab help %s method`).' % (
                                     ', '.join(known) or 'none', ', '.join(native) or 'none', self.instrument_id)))
            assign[where][k] = text
        if self.param('method_needs_power', False):
            self.need_power(ctx)
        self.need_idle(ctx, 'change the method')
        self.requirements(ctx, 'method')
        plan, kept, defaults, shown = [], {}, {}, {}
        for i, call in enumerate(calls):
            got = assign[i]
            if not got:
                continue
            if call.get('style') == 'program':
                for k, text in got.items():
                    plan += self.program_calls(ctx, call, text)
                    shown[k] = text
                continue
            cap = call['cap']
            schema = self.cap_schema_all(cap)
            props = schema.get('properties') or {}
            args = copy.deepcopy(call.get('args') or {})
            body = {}
            for k, text in got.items():
                spec = (call.get('keys') or {}).get(k)
                spec = {'arg': spec} if isinstance(spec, str) else dict(spec or {'arg': k, 'kind': 'native'})
                native = spec.get('arg') or spec.get('field')
                arg_schema = props.get(native) or {}
                if spec.get('kind') == 'native':
                    u = units.declared(arg_schema)
                    bare = re.fullmatch(r'\s*[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\s*', text) is not None
                    if u and units.unit_kind(u) and not bare:                   # a value with a unit: converted to the declared unit
                        val = units.parse_for(text, arg_schema, what=k)
                    else:                                                     # a bare number is in the declared unit (as in shared method)
                        val = coerce_value(text, arg_schema, k)
                    if arg_schema.get('type') == 'integer' and finite(val) and float(val).is_integer():
                        val = int(val)
                else:
                    if spec.get('like_example'):
                        spec['like'] = (self.lab.contract['capabilities'][cap].get('example_arguments') or {}).get(native)
                    val = self.value_for(ctx, k, text, spec, call, arg_schema)
                (body if call.get('object') else args)[native] = val
                shown[k] = text
            if call.get('object'):
                args[call['object']] = body
            else:
                example = self.lab.contract['capabilities'][cap].get('example_arguments') or {}
                for r in schema.get('required') or []:
                    if r in args:
                        continue
                    keep = (call.get('keep') or {}).get(r)
                    v = self.keep_value(ctx, cap, r, keep, props.get(r) or {}) if keep else None
                    last = (getattr(self, 'last_method', {}).get(cap) or {})
                    if v is not None:
                        args[r] = v
                        kept[r] = v
                    elif r in last and not validate(props.get(r) or {}, last[r], r):   # what chemlab sent last (no slot reports it)
                        args[r] = copy.deepcopy(last[r])
                        kept[r] = args[r]
                    elif r in example:
                        args[r] = copy.deepcopy(example[r])
                        defaults[r] = args[r]
                    else:
                        ctx.fail(invalid('MISSING_SETTING', '%s needs %s, and %s reports no current value for it.' % (cap, r, self.instrument_id),
                                         'give it: `chemlab run %s method %s=...`.' % (self.instrument_id, r)))
                self.check_gradient_vs_run(ctx, call, args)
            plan.append((cap, args, 'set the method (%s)' % ', '.join(sorted(got))))
        bad = []
        for cap, args, _ in plan:
            bad += self.range_problems(cap, args) or validate(self.cap_schema_all(cap), args, cap)
        if bad:
            ctx.fail(invalid('BAD_SETTING', 'the method does not fit %s: %s.' % (self.instrument_id, '; '.join(bad[:6])),
                             'change the value(s); ranges: `chemlab help %s %s`. Values from the default method can be overridden by giving '
                             'them.' % (self.instrument_id, plan[0][0])))
        for cap, args, step in plan:
            ctx.call(cap, args, step)
            if not ctx.dry_run:
                if not hasattr(self, 'last_method'):
                    self.last_method = {}
                self.last_method[cap] = copy.deepcopy(args)
        text = 'ok: method of %s set: %s\n' % (self.instrument_id, ', '.join('%s=%s' % kv for kv in shown.items()))
        if kept:
            text += '  kept from the current method (what the instrument reports, or what chemlab set last): %s\n' % ', '.join('%s=%s' % (k, self.show_arg(plan, k, v)) for k, v in kept.items())
        if defaults:
            text += '  from the default method (contract example; give them to change): %s\n' % ', '.join(
                '%s=%s' % (k, self.show_arg(plan, k, v)) for k, v in defaults.items())
        return {'text': text, 'json': {'settings': shown, 'calls': [{'capability': c, 'arguments': a} for c, a, _ in plan], 'kept': kept,
                                       'defaults': defaults}}

    def show_arg(self, plan, arg, v):
        """A native value in friendly units ('bpr_pa=130 bar') for the method reply."""
        for cap, args, _ in plan:
            if arg in args:
                u = units.declared(self.cap_props(cap).get(arg) or {})
                if u and units.unit_kind(u) and finite(v):
                    if 'wavelength' in arg:
                        return units.fmt(units.from_declared(v, u), 'wavelength').replace(' ', '')
                    return units.fmt_declared(v, u).replace(' ', '')
        return short(v, 40)

    def range_problems(self, cap, args):
        """Out-of-range numeric arguments in friendly units ('flow 99 mL/min is above the maximum 10 mL/min')."""
        out = []
        props = self.cap_props(cap)
        for a, v in args.items():
            s = props.get(a) or {}
            if not finite(v) or s.get('type') not in ('number', 'integer'):
                continue
            u = units.declared(s)
            kind = units.unit_kind(u) if u else None
            show = (lambda x: units.fmt(units.from_declared(x, u), kind)) if kind else (lambda x: '%g' % x)
            if s.get('minimum') is not None and v < s['minimum'] - 1e-12 * max(1, abs(s['minimum'])):
                out.append('%s %s is below the minimum %s' % (a, show(v), show(s['minimum'])))
            if s.get('maximum') is not None and v > s['maximum'] + 1e-12 * max(1, abs(s['maximum'])):
                out.append('%s %s is above the maximum %s' % (a, show(v), show(s['maximum'])))
            if s.get('exclusiveMinimum') is not None and v <= s['exclusiveMinimum']:
                out.append('%s %s must be above %s' % (a, show(v), show(s['exclusiveMinimum'])))
        return out

    def check_gradient_vs_run(self, ctx, call, args):
        g = (call.get('keys') or {}).get('gradient')
        r = (call.get('keys') or {}).get('run')
        if not g or not r:
            return
        garg = g if isinstance(g, str) else g['arg']
        rarg = r if isinstance(r, str) else r['arg']
        grad, run = args.get(garg), args.get(rarg)
        if isinstance(grad, list) and grad and finite(run):
            tkey = next((k for k in grad[-1] if k.startswith('time')), None)
            if tkey and finite(grad[-1].get(tkey)) and grad[-1][tkey] > run + 1e-9:
                ctx.fail(invalid('BAD_SETTING', 'the gradient ends at %s, after the run time %s.' % (units.fmt_s(grad[-1][tkey]), units.fmt_s(run)),
                                 'give a longer run (run=...) or a shorter gradient.'))

    # ------------------------------------------------------------------ load
    def h_load(self, ctx, p):
        m = self.vmap('load')
        inst = self.instrument_id
        sample = p.pos[0] if p.pos else None
        args = dict(m.get('args') or {})
        if m.get('sample_arg'):
            if not sample:
                ctx.fail(invalid('MISSING_ARGUMENT', 'load needs the sample id on %s.' % inst, 'usage: chemlab run %s load SAMPLE' % inst))
            args[m['sample_arg']] = sample
            if m.get('provided_arg'):
                args[m['provided_arg']] = sample
        for opt, key in (('pos', 'pos_arg'), ('lane', 'lane_arg')):
            if m.get(key):
                v = p.get(opt)
                if v is None and self.param('default_' + opt) is not None:
                    v = self.param('default_' + opt)
                if v is None:
                    ctx.fail(invalid('MISSING_OPTION', 'load on %s needs --%s.' % (inst, opt), 'e.g. `chemlab run %s load %s --%s 1`.' % (inst, sample or 'SAMPLE', opt)))
                try:
                    args[m[key]] = int(v)
                except (TypeError, ValueError):
                    ctx.fail(invalid('BAD_OPTION', '--%s %s is not a whole number.' % (opt, v), 'give e.g. --%s 1.' % opt))
            elif p.get(opt) is not None:
                ctx.fail(invalid('BAD_OPTION', '%s has no %s choice when loading.' % (inst, opt), 'leave out --%s.' % opt))
        if m.get('volume_arg'):
            v = p.get('volume')
            if v is None:
                ctx.fail(invalid('MISSING_OPTION', 'load on %s needs --volume.' % inst, 'e.g. `chemlab run %s load %s --volume 10uL`.' % (inst, sample or 'SAMPLE')))
            lo, hi = self.arg_range_si(m['cap'], m['volume_arg'])
            if (lo is not None and v < lo - 1e-15) or (hi is not None and v > hi + 1e-15):
                ctx.fail(invalid('OUT_OF_RANGE', 'volume %s is outside %s..%s on %s.' % (units.fmt(v, 'volume'), units.fmt(lo, 'volume'), units.fmt(hi, 'volume'), inst),
                                 'give a volume from %s to %s.' % (units.fmt(lo, 'volume'), units.fmt(hi, 'volume'))))
            args[m['volume_arg']] = self.arg_value(m['cap'], m['volume_arg'], v)
        elif p.get('volume') is not None:
            ctx.fail(invalid('BAD_OPTION', '%s takes no --volume when loading.' % inst, 'leave out --volume.'))
        if self.param('load_needs_power', False):
            self.need_power(ctx)
        self.need_idle(ctx, 'load a sample')
        s = self.slot('sample')
        if s and m.get('single', True):
            v = self.sv(ctx, s)
            ctx.check(not v, 'SAMPLE_PRESENT', 'a sample (%s) is already loaded in %s.' % (short(self.sv(ctx, self.slot('sample_id')) or v, 40), inst),
                      'run it first, or take it out with `chemlab call %s sample.unload`.' % inst, label='no sample loaded yet')
        self.requirements(ctx, 'load')
        self.steps(ctx, m.get('before'), 'make the sample position accessible')
        ctx.call(m['cap'], args, 'load %s' % (sample or 'the sample'))
        self.steps(ctx, m.get('after'), 'close up after loading')
        return {'text': 'ok: %s loaded into %s%s\n  steps: %s\n' % (sample or 'the sample', inst, ''.join(
            ' %s %s' % (k, v) for k, v in args.items() if k not in (m.get('sample_arg'), m.get('provided_arg'))), '; '.join(ctx.done)),
                'json': {'sample': sample, 'arguments': args}}

    # ------------------------------------------------------------------ run / stop
    def h_run(self, ctx, p):
        m = self.vmap('run')
        inst = self.instrument_id
        args = dict(m.get('args') or {})
        pos = p.get('pos')
        if m.get('pos'):
            if pos is None:
                ctx.fail(invalid('MISSING_OPTION', 'run on %s needs --pos (which vial).' % inst,
                                 'e.g. `chemlab run %s run --pos 1:1` (drawer:position).' % inst))
            args.update(self.position(ctx, pos, m['pos']))
        elif pos is not None:
            ctx.fail(invalid('BAD_OPTION', '%s takes no --pos for run.' % inst, 'leave out --pos (load the sample with `load`).'))
        self.need_power(ctx)
        self.need_idle(ctx, 'start another run')
        self.requirements(ctx, 'run')
        ok, why = self.ready(ctx)
        strict = (m.get('ready') or {}).get('strict', True)
        if not ok and p.get('wait-ready'):
            def is_ready(view):
                return self.ready(ctx)[0]
            ctx.advance(p.get('wait-ready'), until=is_ready, step='wait until %s is ready' % inst, poll_s=float(self.param('poll_s', 5.0)))
            if not ctx.dry_run:
                ok, why = self.ready(ctx)
                ctx.check(ok, 'NOT_READY', '%s is still not ready after %s: %s.' % (inst, units.fmt_s(p.get('wait-ready')), why),
                          'wait longer (`--wait-ready 30min`) and check `chemlab run %s status`.' % inst, label='ready')
        elif not ok and strict:
            ctx.check(False, 'NOT_READY', '%s is not ready to start: %s.' % (inst, why), self.ready_fix())
        elif ok:
            ctx.checks.append('ready')
        self.steps(ctx, m.get('before'), 'prepare the run')
        r = ctx.call(m['cap'], args, 'start the run (%s)' % m['cap'])
        self.steps(ctx, m.get('after'), 'inject')
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.make_job(ctx, m, r)
        return {'text': 'started %s: %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, job.description, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind, 'arguments': args}}

    def position(self, ctx, text, spec):
        """--pos: 'D:P' for a drawer + position, a plain number for one position, or a named location of the lab config."""
        names = spec.get('args') or []
        parts = text.split(':')
        if all(x.strip().isdigit() for x in parts) and len(parts) == len(names):
            out = {a: int(x) for a, x in zip(names, parts)}
            for a, v in out.items():
                lo, hi = self.arg_range(self.vmap('run')['cap'], a)
                if (lo is not None and v < lo) or (hi is not None and v > hi):
                    ctx.fail(invalid('OUT_OF_RANGE', '%s %d is outside %s..%s on %s.' % (a, v, lo, hi, self.instrument_id),
                                     'give a %s from %s to %s.' % (a, lo, hi)))
            return out
        if re.fullmatch(r'[\d:]+', text):
            ctx.fail(invalid('BAD_POSITION', '--pos %s does not fit %s (%s).' % (text, self.instrument_id, ':'.join(n.upper() for n in names)),
                             'write it as %s, e.g. %s.' % (':'.join(n.upper() for n in names), ':'.join('1' for _ in names))))
        return self.location(ctx, text)

    def make_job(self, ctx, m, r):
        cap = m['cap']
        desc = '%s run (%s)' % (self.instrument_id, cap)
        if r.get('operation_id') and m.get('job') != 'state':
            return OperationJob(ctx.core, self, ctx.req, desc, cap, r['operation_id'], max_s=float(self.param('job_max_s', 0)) or None)
        ad = self
        rs = self.slot('run')
        seen = {'running': False}

        def state(c):
            v = ad.sv(c, rs)
            if ad.running(c):
                seen['running'] = True
                return 'running', '%s = %s' % (rs, short(v, 60)), {'state': v}
            fail = ad.param('failed') or []
            if v in fail:
                return 'failed', '%s = %s' % (rs, short(v, 60)), {'state': v}
            last = ad.slot('last_run')
            if last:
                lv = ad.sv(c, last)
                if isinstance(lv, dict) and lv.get('aborted'):
                    return 'cancelled', 'the run was aborted', {'state': lv}
            return 'succeeded', '%s = %s' % (rs, short(v, 60)), {'state': v}

        def stop(c):
            sm = ad.vmap('stop')
            c.call(sm['cap'], dict(sm.get('args') or {}), 'stop the run')
            return 'stopped with %s' % sm['cap']
        return StateJob(ctx.core, self, ctx.req, desc, state, stop=stop, max_s=float(self.param('job_max_s', 7200.0)), poll_s=float(self.param('poll_s', 5.0)))

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        ctx.check(self.running(ctx), 'NOT_RUNNING', 'nothing is running on %s.' % self.instrument_id,
                  'nothing to stop; see `chemlab run %s status`.' % self.instrument_id, label='a run is in progress')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop the run')
        return {'text': 'ok: the run on %s is stopped\n' % self.instrument_id, 'json': {'stopped': True}}

    # ------------------------------------------------------------------ chromatogram
    def h_chromatogram(self, ctx, p):
        m = self.vmap('chromatogram')
        inst = self.instrument_id
        args = dict(m.get('args') or {})
        if m.get('pos_arg'):
            try:
                args[m['pos_arg']] = int(p.get('pos') or self.param('default_pos', 1))
            except ValueError:
                ctx.fail(invalid('BAD_OPTION', '--pos %s is not a whole number.' % p.get('pos'), 'give e.g. --pos 1.'))
        if m.get('cap'):
            r = ctx.call(m['cap'], args, 'read the chromatogram (%s)' % m['cap'], query=True)
            res = r.get('result') if isinstance(r.get('result'), dict) else {}
        else:
            res = {s: ctx.v(s) for s in m.get('reads') or []}
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        sig = p.get('signal') or m.get('y')
        out = self.normalise(res, m, sig, p)
        lines = ['%s chromatogram: %s%s' % (inst, 'complete' if out['complete'] else 'not complete (run still going or not run)',
                                            (', %d points, %s from %s to %s' % (len(out['trace']['x']), out['trace']['y_name'],
                                                                                 short(out['trace']['x'][0], 12), short(out['trace']['x'][-1], 12)))
                                            if out['trace'] and out['trace']['x'] else ', no trace')]
        if out.get('live'):
            lines.append('  live reading: %s' % short(out['live'], 300))
        if out['peaks']:
            pf = out['peaks'][0].get('position_from') or 'rt_s'
            where = 'Rf' if pf == 'rf' else 'distance, m' if pf.endswith('_m') else 'time, s'
            lines.append('  peaks (%s; position = %s):' % ('from the instrument' if out['peaks_source'] == 'instrument'
                                                             else 'found by chemlab in the trace', where))
            lines.append('    %-4s %-12s %-14s %-14s %s' % ('#', 'position', 'area', 'height', 'label'))
            for n, pk in enumerate(out['peaks'][:20], 1):
                lines.append('    %-4d %-12s %-14s %-14s %s' % (n, '%.4g' % pk['rt_s'] if finite(pk.get('rt_s')) else '-',
                                                                '%.4g' % pk['area'] if finite(pk.get('area')) else '-',
                                                                '%.4g' % pk['height'] if finite(pk.get('height')) else '-',
                                                                pk.get('label') if pk.get('label') is not None else ''))
        else:
            lines.append('  no peaks')
        for k, v in (out.get('extra') or {}).items():
            lines.append('  %s = %s' % (k, short(v, 200)))
        return {'text': '\n'.join(lines) + '\n', 'json': {'chromatogram': out}}

    def normalise(self, res, m, sig, p):
        """Any member's result -> {complete, trace: {x, y, x_unit, y_unit, y_name}, peaks, peaks_source, extra, live}."""
        base = dig(res, m.get('base', ''))
        base = base if isinstance(base, dict) else {}
        x, y, trace_peaks = [], [], None
        if m.get('records'):
            recs = dig(base, m['records']) or []
            recs = [r for r in recs if isinstance(r, dict)]
            if m.get('filter'):
                fk, fv = m['filter']
                recs = [r for r in recs if r.get(fk) not in fv]
            if m.get('last_run_by'):                                     # a history of several runs: keep the latest one
                k = m['last_run_by']
                cut = max([i for i in range(1, len(recs)) if finite(recs[i].get(k)) and finite(recs[i - 1].get(k))
                           and recs[i][k] < recs[i - 1][k]] or [0])
                recs = recs[cut:]
            x = [r.get(m['x']) for r in recs]
            y = [sum(v for v in r.get(sig) if finite(v)) if isinstance(r.get(sig), list) else r.get(sig) for r in recs]
        elif m.get('table'):
            cols = dig(base, m['columns']) or []
            rows = dig(base, m['table']) or []
            if m['x'] in cols and sig in cols:
                ix, iy = cols.index(m['x']), cols.index(sig)
                x = [r[ix] for r in rows]
                y = [r[iy] for r in rows]
        elif m.get('traces'):
            tr = [t for t in (dig(base, m['traces']) or []) if isinstance(t, dict)]
            pick = str(p.get('pos')) if p.get('pos') is not None else None
            t = next((t for t in tr if str(t.get(m.get('label', 'index'))) == pick), None) if pick is not None else (tr[0] if tr else None)
            if t is not None:
                x, y = t.get(m['x']) or [], t.get(sig) or []
                trace_peaks = t.get(m['trace_peaks']) if m.get('trace_peaks') else None
        elif m.get('x'):
            x, y = base.get(m['x']) or [], base.get(sig) or []
        trace = {'x': x, 'y': y, 'x_unit': m.get('x_unit', 's'), 'y_unit': m.get('y_unit', ''), 'y_name': sig} if x else None
        def getp(path):
            if '#' in path:
                sid, _, key = path.partition('#')
                return dig(res.get(sid), key)
            return dig(base, path)
        peaks_raw = getp(m['peaks']) if m.get('peaks') else (trace_peaks if m.get('traces') else None)
        peaks, source = [], 'none'
        if isinstance(peaks_raw, list) and peaks_raw:
            for pk in peaks_raw:
                if not isinstance(pk, dict):
                    continue
                rk = next((k for k in PEAK_RT_KEYS if finite(pk.get(k))), None)
                area = next((pk[k] for k in PEAK_AREA_KEYS if finite(pk.get(k))), None)
                height = next((pk[k] for k in PEAK_HEIGHT_KEYS if finite(pk.get(k))), None)
                peaks.append({'rt_s': pk.get(rk) if rk else None, 'position_from': rk, 'area': area, 'height': height,
                              'label': pk.get('label') or pk.get('substance_id') or pk.get('lane'), 'native': pk})
            source = 'instrument'
        elif trace:
            peaks = find_peaks(x, y)
            source = 'chemlab' if peaks else 'none'
        complete = getp(m['complete']) if m.get('complete') else None
        if m.get('reads'):                                               # read from slots: complete = a last run and none running
            complete = getp(m['complete']) is not None if m.get('complete') else bool(trace or peaks)
            complete = complete and not self.running(res)
        if complete is None:
            complete = bool(trace or peaks)
        extra = {k.replace('#', '.'): getp(k) for k in m.get('extra') or [] if getp(k) is not None}
        live = {k.replace('#', '.'): getp(k) for k in m.get('live') or [] if getp(k) is not None}
        return {'complete': bool(complete), 'trace': trace, 'peaks': peaks, 'peaks_source': source, 'extra': extra, 'live': live}

    # ------------------------------------------------------------------ quantify
    def h_quantify(self, ctx, p):
        m = self.vmap('quantify')
        inst = self.instrument_id
        stds = []
        for part in [x for x in (p.get('standards') or '').split(',') if x.strip()]:
            c, eq, a = part.partition('=')
            if not eq:
                ctx.fail(invalid('BAD_STANDARDS', 'standard %r is not CONC=AREA.' % part, 'write e.g. --standards 0.1mM=0.01,0.5mM=0.05.'))
            conc = units.parse(c, 'concentration', what='standard concentration')
            try:
                area = float(a)
            except ValueError:
                ctx.fail(invalid('BAD_STANDARDS', 'the area in %r is not a number.' % part, 'write the peak area as a plain number.'))
            if not math.isfinite(area):
                ctx.fail(invalid('BAD_STANDARDS', 'the area in %r is not finite.' % part, 'write a plain number.'))
            stds.append((conc, area))
        if len(stds) < int(m.get('min_standards', 1)):
            ctx.fail(invalid('BAD_STANDARDS', '%s needs at least %d standards.' % (inst, int(m.get('min_standards', 1))), 'give more CONC=AREA pairs.'))
        win = self.window(ctx, p.get('window'), 'duration', '--window')
        style = m.get('style')
        if style == 'pairs':
            item = (((self.cap_props(m['cap']).get(m['cal_arg']) or {}).get('items') or {}).get('properties') or {})
            args = {m['cal_arg']: [{m['conc_key']: units.to_declared(c, units.declared(item.get(m['conc_key']) or {}) or 'mol/m^3'), m['area_key']: a}
                                   for c, a in stds],
                    m['window_arg']: [win[0], win[1]]}
            r = ctx.call(m['cap'], args, 'fit the calibration and quantify the window', query=True)
            res = r.get('result') or {}
        elif style == 'slope':
            if any(c <= 0 for c, _ in stds):
                ctx.fail(invalid('BAD_STANDARDS', 'standard concentrations must be above zero for a response factor.', 'drop the blank.'))
            k = sum(c * a for c, a in stds) / sum(c * c for c, _ in stds)
            args = {m['start_arg']: win[0], m['stop_arg']: win[1], m['slope_arg']: k}
            r = ctx.call(m['cap'], args, 'integrate the window with the response factor', query=True)
            res = dict(r.get('result') or {}, response_factor=k)
        else:                                                            # fit_then_analyze (GC-IMS)
            drift = self.window(ctx, p.get('drift'), None, '--drift') if p.get('drift') else None
            roi_schema = self.cap_props(m['cap']).get(m['roi_arg']) or {}
            rp = roi_schema.get('properties') or {}
            if drift is None:
                drift = (rp.get(m['drift_lo'], {}).get('minimum'), rp.get(m['drift_hi'], {}).get('maximum'))
                drift = drift if all(finite(x) for x in drift) else tuple((self.lab.contract['capabilities'][m['cap']].get('example_arguments') or {})
                                                                         .get(m['roi_arg'], {}).get(k) for k in (m['drift_lo'], m['drift_hi']))
            args = {m['roi_arg']: {m['start_key']: win[0], m['stop_key']: win[1], m['drift_lo']: drift[0], m['drift_hi']: drift[1]},
                    m['std_arg']: [{m['conc_key']: c, m['area_key']: a} for c, a in stds], m['label_arg']: 'chemlab quantify'}
            bad = validate(self.cap_schema_all(m['cap']), args, m['cap'])
            if bad:
                ctx.fail(invalid('BAD_WINDOW', 'the calibration does not fit %s: %s.' % (inst, '; '.join(bad[:4])), 'change --window / --drift.'))
            ctx.call(m['cap'], args, 'fit the calibration')
            r = ctx.call(m['analyze_cap'], {}, 'quantify the unknown', query=True)
            res = r.get('result') or {}
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        conc = dig(res, m.get('result_key', 'concentration_mol_m3'))
        if isinstance(conc, list):
            conc = conc[0] if conc else None
        text = '%s: concentration %s%s\n' % (inst, units.fmt(conc, 'concentration') if finite(conc) else 'n/a',
                                             (' (%s)' % res.get('quality')) if res.get('quality') else '')
        return {'text': text, 'json': {'concentration_mol_m3': conc, 'result': res, 'standards': stds, 'window_s': list(win)}}

    def window(self, ctx, text, kind, what):
        a, sep, b = (text or '').partition('..')
        if not sep:
            ctx.fail(invalid('BAD_WINDOW', '%s %s is not START..END.' % (what, text), 'write e.g. %s 2.5min..3.5min.' % what))
        lo = units.parse(a, kind, what=what) if kind else float(a)
        hi = units.parse(b, kind, what=what) if kind else float(b)
        if not hi > lo:
            ctx.fail(invalid('BAD_WINDOW', '%s %s ends before it starts.' % (what, text), 'give START < END.'))
        return lo, hi

    # ------------------------------------------------------------------ flow / set-temp
    def h_flow(self, ctx, p):
        m = self.vmap('flow')
        want = p.pos[0] == 'on'
        self.need_power(ctx)
        self.requirements(ctx, 'flow')
        s = self.slot('flow')
        if s and bool(self.sv(ctx, s)) == want:
            return {'text': 'ok: the flow of %s is already %s\n' % (self.instrument_id, p.pos[0]), 'json': {'flow': want, 'changed': False}}
        args = copy.deepcopy(m.get('args') or {})
        if m.get('object'):
            args[m['object']] = {m['key']: want}
        else:
            args[m['arg']] = want
        ctx.call(m['cap'], args, 'flow %s' % p.pos[0], readback=(lambda v: None if bool(self.sv(ctx, s)) == want else 'flow %s' % self.sv(ctx, s)) if s else None)
        return {'text': 'ok: the flow of %s switched %s\n' % (self.instrument_id, p.pos[0]), 'json': {'flow': want, 'changed': True}}

    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        zones = m.get('zones')
        zone = p.get('zone') or m.get('default_zone')
        if zones:
            if zone not in zones:
                ctx.fail(invalid('UNKNOWN_ZONE', '%s has no zone %r.' % (self.instrument_id, zone), 'zones: %s.' % ', '.join(zones)))
            spec = zones[zone]
            cap, arg, args = spec['cap'], spec['arg'], dict(spec.get('args') or {})
        else:
            cap, arg, args = m['cap'], m['arg'], dict(m.get('args') or {})
            t = (self.map.get('targets') or {}).get('zone') or {}
            vals = t.get('values') or {}
            if zone not in vals:
                ctx.fail(invalid('UNKNOWN_ZONE', '%s has no zone %r.' % (self.instrument_id, zone), 'zones: %s.' % ', '.join(vals)))
            args[t['arg']] = vals[zone]
        lo, hi = self.arg_range_si(cap, arg)
        if (lo is not None and k < lo - 1e-9) or (hi is not None and k > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', '%s %s is outside the range of the %s zone of %s (%s to %s).' % (
                'set point', units.fmt(k, 'temperature'), zone, self.instrument_id, units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature')),
                'give a set point from %s to %s.' % (units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature'))))
        ctx.checks.append('set point within %s..%s' % (units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature')))
        self.need_power(ctx)
        self.requirements(ctx, 'set-temp')
        args[arg] = round(self.arg_value(cap, arg, k), 10)
        ctx.call(cap, args, 'set the %s temperature to %s' % (zone, units.fmt(k, 'temperature')))
        return {'text': 'ok: %s %s set point %s\n' % (self.instrument_id, zone, units.fmt(k, 'temperature')),
                'json': {'zone': zone, 'set_point_k': k, 'set_point_c': k - 273.15}}


_ = (CompileError, unsupported)

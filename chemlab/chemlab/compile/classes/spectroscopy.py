"""Spectroscopy class ("spectroscopy"): molecular spectroscopy and magnetic resonance - UV-Vis, CD, fluorescence, FT-IR, Raman,
stopped-flow, TCSPC, transient absorption, VCD, CPL, benchtop NMR / TD-NMR and EPR.  Operator action: record a spectrum.

Verbs: power, open / close PART, source on|off, purge FLOW|off, method KEY=VALUE ..., load SAMPLE, unload, blank, measure,
stop, spectrum [--save-to F], set-temp T, analyze MODEL [KEY=VALUE ...], status.  Members and mappings:
configs/adapters/spectroscopy.json.

The usual sequence:  power on -> (purge) -> source on --wait -> method ... -> blank --wait -> measure --wait -> spectrum.
A cell placed by the lab operator (most members) is a world fact; `load` / `unload` exist only where the instrument itself
takes the sample (Hach DR6000, JASCO FT/IR-4700, Spinsolve).

Method dictionary (canonical keys; each member maps the keys it has, the rest pass through by native argument name):

    from / to / step     spectral range: wavelength (nm, um) or wavenumber (cm-1) by member; ESR: center / width (field)
    ex / em              excitation / emission wavelength            bw / ex-bw / em-bw   band width(s)
    time                 integration / response / dwell time (s, ms) scans                accumulations / scans / averages
    mode                 measurement mode (absorbance, emission, ...)   resolution        spectral resolution (cm-1)
    speed                scan speed (nm/min)                          power              laser / microwave / RF power (mW, W)
    laser                laser / pump wavelength                      nucleus            NMR nucleus (1H, 13C)
    delay                relaxation / recycle delay                   points             number of points
    center / width       EPR field centre and sweep (mT, G, T)         freq               microwave frequency (GHz)
    wavelength           single wavelength (DR6000 single-wavelength mode, SX20 monitor wavelength)
    program              Hach stored program number (DR6000)

A method sets every argument the instrument needs: what the agent does not give comes from the instrument's documented
example method in its contract (`help INST method` lists the keys and that baseline).

Vendor-neutral references: JCAMP-DX (IUPAC) for spectra, ASTM E131 terms, USP <857> (UV-Vis), <854> (mid-IR), <1120> (Raman),
<761> (NMR), <853> (fluorescence), OPC UA ADI (OPC 10020) analyzer devices, AnIML.
"""
import json
import math
import re

from ...contract import validate
from ...generic import coerce
from .. import shared, units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused
from ..jobs import RUNNING, StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt

REF_ALL = ('JASCO J-1500 / FP-8350 / FT/IR-4700 / NRS-5500 / FVS-6000 / CPL-300 operating manuals (measurement parameters, blank and '
       'sample measurement); Hach DR6000 user manual (zero / read, wavelength scan); Applied Photophysics SX20, Edinburgh FLS1000, '
       'Ultrafast HELIOS, Magritek Spinsolve, Bruker minispec mq20 and Magnettech ESR5000 user manuals (acquisition set-up)')
ON_OFF = ['on', 'off']
NUM = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'

# class kind names used in the fragment ("kind": "field" ...) -> the framework kinds (units.KINDS); every unit is a framework unit
FRAMEWORK_KIND = {'field': 'magnetic_field', 'rate': 'count_rate', 'offset': 'frequency_offset'}
CLASS_KIND = {v: k for k, v in FRAMEWORK_KIND.items()}


def kind_example(kind):
    k = FRAMEWORK_KIND.get(kind, kind)
    return units.example(k) if k in units.KINDS else '1'


def parse_q(text, kind, what=None):
    """Agent text with a unit -> SI float (units.parse; the class's kind names map to the framework kinds).  A time may be
    negative here (pump-probe delays before time zero, -1ps); the contract ranges still apply."""
    k = FRAMEWORK_KIND.get(kind, kind)
    t = (text or '').strip() if isinstance(text, str) else text
    if k == 'duration' and isinstance(t, str) and t.startswith('-'):
        return -units.parse(t[1:], k, what=what or kind)
    return units.parse(t, k, what=what or kind)


def fmt_q(si, kind):
    """SI -> friendly text (local kinds included)."""
    if si is None or not finite(si):
        return 'n/a' if si is None else str(si)
    if kind == 'wavenumber':
        return '%g cm-1' % round(si / 100.0, 4)
    if kind == 'field':
        return '%g mT' % round(si * 1e3, 6)
    if kind == 'scan_speed':
        return '%g nm/min' % round(si * 60e9, 4)
    if kind == 'rate':
        return '%g /s' % round(si, 6)
    if kind == 'offset':
        return '%g Hz' % round(si, 6)
    if kind == 'wavelength':
        return '%g nm' % round(si * 1e9, 4)
    if kind == 'frequency' and abs(si) >= 1e8:
        return '%g GHz' % round(si / 1e9, 6)
    if kind == 'frequency' and abs(si) >= 1e5:
        return '%g MHz' % round(si / 1e6, 6)
    if kind == 'duration' and abs(si) < 1:
        for f, u in ((1e-3, 'ms'), (1e-6, 'µs'), (1e-9, 'ns'), (1e-12, 'ps'), (1e-15, 'fs')):
            if abs(si) >= f:
                return '%g %s' % (round(si / f, 6), u)
        return '0 s' if si == 0 else '%g s' % si
    if kind == 'power' and abs(si) < 1:
        return '%g mW' % round(si * 1e3, 6)
    if kind in units.KINDS:
        return units.fmt(si, kind)
    return '%g' % si


DISPLAY = {'wavelength': ('nm', 1e-9), 'wavenumber': ('cm-1', 100.0), 'field': ('mT', 1e-3), 'offset': ('Hz', 1.0), 'frequency': ('Hz', 1.0)}


def kind_of_declared(u):
    u = units.norm_unit(u)
    if u == 's^-1':
        return 'rate'                                                   # a rate (1/s), not a frequency
    k = units.unit_kind(u)
    k = CLASS_KIND.get(k, k)
    if k == 'length':
        return 'wavelength' if u == 'nm' else 'length'
    return k


def num_schema(schema):
    """The numeric branch of a schema (anyOf number|null) or None."""
    if not isinstance(schema, dict):
        return None
    if schema.get('type') in ('number', 'integer'):
        return schema
    for s in schema.get('anyOf') or []:
        if isinstance(s, dict) and s.get('type') in ('number', 'integer'):
            return s
    return None


def get_path(obj, path):
    """'a/b/0/c' or 'points/*/wavelength_m' (map over a list) into a JSON value; None when missing."""
    if not path:
        return obj
    head, _, rest = path.partition('/')
    if head == '*':
        if not isinstance(obj, list):
            return None
        return [get_path(x, rest) for x in obj]
    if head == '-1' or head.lstrip('-').isdigit():
        if not isinstance(obj, list) or not obj:
            return None
        try:
            return get_path(obj[int(head)], rest)
        except IndexError:
            return None
    if not isinstance(obj, dict):
        return None
    return get_path(obj.get(head), rest)


class SpectroscopyAdapter(ClassAdapter):
    CLASS = 'spectroscopy'
    TITLE = 'record a spectrum'
    ABOUT = ('Spectrometers and magnetic-resonance instruments: switch the source on and let it warm up, set the method with '
             'plain keys and units (nm, cm-1, mT, GHz, s), record the blank / background, measure (a job you can wait for), read '
             'the spectrum (and save it), set the sample temperature, and run the instrument\'s own analysis.')
    STANDARDS = ('JCAMP-DX (IUPAC) spectra; ASTM E131 molecular spectroscopy terms; USP <857> UV-Vis, <854> mid-IR, <1120> Raman, '
                 '<761> NMR, <853> fluorescence; OPC UA ADI (OPC 10020) analyzer devices; AnIML')
    ALWAYS = ('status',)
    UNIT_KINDS = ('wavelength', 'duration', 'temperature', 'power', 'frequency', 'flow')
    PATH_GROUPS = (('measure', 'stop', 'spectrum'),)
    CODES = {
        'OPTICS_NOT_READY': ('OPTICS_NOT_READY', 'the optics of {inst} are not ready: it needs power, the sample compartment closed and '
                             'the light source on and warmed up.',
                             'check `chemlab run {inst} status`; then `chemlab run {inst} source on --wait` and `chemlab run {inst} close`.'),
        'PURGE_NOT_READY': ('PURGE_NOT_READY', '{inst} needs the nitrogen purge flowing and the air flushed out of the optics before the '
                            'lamp may be switched on (ozone and oxygen absorption below 200 nm).',
                            'start the purge and wait for it: `chemlab run {inst} purge 5L/min --wait`, then `source on --wait`.'),
        'INSUFFICIENT_PURGE': ('INSUFFICIENT_PURGE', 'the nitrogen flow of {inst} is too low for the shortest wavelength of the method.',
                               'raise the purge (`chemlab run {inst} purge 15L/min --wait`) or start the scan at a longer wavelength.'),
        'PURGE_REQUIREMENT_UNKNOWN': ('OUT_OF_RANGE', 'no purge flow is documented for wavelengths that short on {inst}.',
                                      'keep the method at 170 nm or longer.'),
        'BLANK_REQUIRED': ('BLANK_REQUIRED', '{inst} has no blank recorded with the current method and cell.',
                           'record the blank first (`chemlab run {inst} blank --wait` with the solvent blank in the holder); a new '
                           'method or temperature needs a new blank.'),
        'MATCHED_BLANK_REQUIRED': ('BLANK_REQUIRED', '{inst} has no blank recorded with the current method, solvent and settings.',
                                   'record the blank first (`chemlab run {inst} blank --wait` with the blank cell in place).'),
        'SAMPLE_METHOD_REQUIRED': ('NOT_READY', '{inst} needs a method and a cell of the right kind in the holder (a blank cell for '
                                   '`blank`, a sample for `measure`).',
                                   'set the method (`chemlab run {inst} method ...`); cells are placed by the lab operator - see '
                                   '`chemlab run {inst} status`.'),
        'WRONG_SAMPLE_KIND': ('WRONG_CELL', 'the cell in {inst} is not the right kind for this step (a blank cell for `blank`, a sample '
                              'for `measure`).', 'ask the lab operator to place the right cell; see `chemlab run {inst} status`.'),
        'SAMPLE_KIND_MISMATCH': ('WRONG_CELL', 'the tube in {inst} is not the right kind for this step (a blank tube for `blank`, a '
                                 'sample for `measure`).', 'ask the lab operator to place the right tube; see `chemlab run {inst} status`.'),
        'REFERENCE_KIND': ('WRONG_CELL', 'the cuvette in {inst} is not the right kind: the IRF needs the scatterer, a decay needs the '
                           'fluorescent sample.', 'ask the lab operator to place the right cuvette.'),
        'SCATTER_WAVELENGTH': ('BAD_METHOD', 'for the IRF the emission wavelength of {inst} must equal the excitation wavelength.',
                               'set `method em=<the excitation wavelength>` before `blank`, and back for the sample.'),
        'GRID_INTERVAL_MISMATCH': ('BAD_STEP', 'the range of the method is not a whole number of steps on {inst}.',
                                   'choose from / to / step so that (to - from) / step is a whole number.'),
        'PIXEL_GRID': ('BAD_STEP', 'the Raman shift range of {inst} must span a whole number of 1 cm-1 pixels (at least 2).',
                       'give whole cm-1 values, e.g. from=200cm-1 to=1800cm-1.'),
        'SPECTRAL_GRID': ('BAD_STEP', 'the probe wavelength grid of {inst} must have 3..512 points with (to - from) a whole number of steps.',
                          'adjust from / to / step.'),
        'BUSY': ('BUSY', '{inst} is measuring now.', 'wait for the job (`chemlab wait job-N`) or stop it (`chemlab run {inst} stop`).'),
        'ACCESSORY_NOT_INSTALLED': ('NO_ACCESSORY', '{inst} has no temperature-control accessory installed in this lab, so it cannot '
                                    'set a sample temperature.', 'measure at room temperature, or ask the lab operator to install it.'),
        'OPTION_NOT_INSTALLED': ('NO_ACCESSORY', 'that option is not installed on {inst}.', 'see `chemlab describe {inst}`.'),
        'RF_NOT_READY': ('NOT_READY', 'the microwave bridge of {inst} is not ready: it needs power, the lid closed and the microwave on.',
                         '`chemlab run {inst} close lid`, `chemlab run {inst} source on`.'),
        'TEMPERATURE_NOT_SETTLED': ('NOT_SETTLED', 'the sample temperature of {inst} is not at its set point yet (within 0.1 K).',
                                    'wait for it: `chemlab run {inst} set-temp T --wait`.'),
        'REFERENCE_TEMPERATURE_MISMATCH': ('TEMPERATURE', 'the sample temperature of {inst} differs from the sample\'s reference '
                                           'temperature by more than 0.25 K.', 'set the temperature it was prepared for (`set-temp`).'),
        'COOLING_WATER_REQUIRED': ('NOT_SET_UP', '{inst} needs its cooling water, which the lab has not connected.', 'ask the lab operator.'),
        'FILTER_WINDOW': ('OUT_OF_RANGE', 'the range is outside the window of the selected optical filter of {inst}.',
                          'stay within the filter window, or select the other filter (`chemlab call {inst} filter.select`).'),
        'CAF2_WINDOW': ('OUT_OF_RANGE', 'CaF2 cell windows absorb below 1100 cm-1.', 'start the range at 1100 cm-1 or higher.'),
        'CENTER_POSITION_REQUIRED': ('NOT_READY', 'the sample stage of {inst} is not at the centre position.',
                                     '`chemlab call {inst} stage.select --position 0`.'),
        'FILTER_NOT_SETTLED': ('NOT_SETTLED', 'the filter wheel of {inst} is still moving.', 'wait a moment (`chemlab wait 2`).'),
        'DETECTOR_SATURATED': ('SATURATED', 'the detector of {inst} saturates with this sample and method.',
                               'use a shorter path / lower concentration, or a longer wavelength.'),
        'TEMPLATE_RANGE': ('OUT_OF_RANGE', 'the method goes outside the wavelengths where {inst} has reference data for this sample.',
                           'narrow the range (see `chemlab help {inst} method`).'),
        'SOURCE_COVERAGE': ('OUT_OF_RANGE', 'the method range is outside the spectral data of the sample on {inst}.',
                            'narrow the range (or choose a wavelength inside the sample\'s band).'),
        'COMPLETE_SAMPLE_REQUIRED': ('NO_RESULT', '{inst} has no complete sample spectrum to analyse.', 'measure the sample first.'),
        'FIT_WAVELENGTH_COVERAGE': ('NO_RESULT', 'the spectrum of {inst} does not cover 190..230 nm with at least 20 points, which the '
                                    'secondary-structure fit needs.', 'measure from 240 nm (or longer) down to 190 nm, then analyse.'),
        'STANDARD_DETECTOR_RANGE': ('OUT_OF_RANGE', 'excitation or emission (with its band width) leaves the 200..750 nm range of the '
                                    'standard detector of {inst}.', 'keep ex / em and the band widths inside 200..750 nm.'),
        'SCATTER_OR_ANTISTOKES_UNSUPPORTED': ('BAD_METHOD', 'the emission range of {inst} overlaps the excitation band (scatter).',
                                              'start the emission range well above the excitation wavelength (by more than the band widths).'),
        'INTEGRATION_OVERLAP': ('BAD_METHOD', 'the sampling interval is shorter than the integration time.', 'make interval >= time.'),
        'FIELD_RANGE': ('OUT_OF_RANGE', 'the field sweep of {inst} leaves -10..650 mT.', 'reduce the width or move the centre.'),
        'INSTALLED_SOURCE_MISMATCH': ('BAD_METHOD', 'the excitation wavelength must be the one of the installed laser of {inst}.',
                                      'use its laser wavelength (see `chemlab run {inst} status`).'),
        'EXTERNAL_LASER_MISMATCH': ('BAD_METHOD', 'the pump settings must match the external laser of {inst}.',
                                    'keep laser= and power= as in the standard method.'),
        'PERIOD_GRID_RESOURCE': ('BAD_METHOD', 'the time window and bins do not fit the repetition period of {inst}.',
                                 'choose a time window <= 1 / repetition rate, with a whole number of bins per period.'),
        'PILEUP_LIMIT': ('TOO_BRIGHT', 'the count rate on {inst} would exceed the 5 % pile-up limit.', 'narrow the slit or lower the rate.'),
        'POWER_OFF_FOR_OPTICS': ('POWER_ON', 'the measuring mode or cell path of {inst} can only be changed with the power off.',
                                 '`chemlab run {inst} power off`, set the method, then `power on`.'),
        'PHYSICAL_RATIO_MISMATCH': ('BAD_METHOD', 'the mixing ratio must be the one the installed syringes give on {inst}.',
                                    'leave the ratio as in the standard method.'),
        'FLUID_NOT_READY': ('NOT_SET_UP', 'the drive gas, valves or cover of {inst} are not ready.', 'ask the lab operator.'),
        'EXTERNAL_FLUSH_REQUIRED': ('NOT_SET_UP', '{inst} must be flushed by the operator before the next shot.', 'ask the lab operator.'),
        'INSUFFICIENT_REAGENT': ('EMPTY', 'the drive syringes of {inst} hold too little for this stop volume.', 'ask the lab operator to refill.'),
        'CELL_PATH_MISMATCH': ('BAD_METHOD', 'that path length is not available in the installed cell of {inst}.', 'see `chemlab help {inst} method`.'),
        'STOP_VOLUME_RANGE': ('OUT_OF_RANGE', 'the stop volume of {inst} is outside the range of the stop syringe.', 'see `chemlab help {inst} method`.'),
        'LAMP_WARMUP': ('WARMING_UP', 'the UV lamp of {inst} is warming up; readings below the UV switch wavelength wait for it.',
                        'wait about a minute (`chemlab wait 60`) and repeat.'),
        'LAMP_OFF': ('SOURCE_OFF', 'the visible lamp of {inst} is off.', '`chemlab run {inst} source on --lamp vis`.'),
        'COVER_OPEN': ('LID_OPEN', 'the cell compartment of {inst} is open.', 'close it: `chemlab run {inst} close`.'),
        'ZERO_FIRST': ('BLANK_REQUIRED', '{inst} needs a zero (blank) reading with this program before it reads samples.',
                       'put the blank in, `chemlab run {inst} blank`, then the sample and `measure`.'),
        'NO_PROGRAM': ('NO_METHOD', 'no program or mode is running on {inst}.', 'start one with `chemlab run {inst} method ...`.'),
        'NOT_READY': ('NOT_READY', '{inst} is still starting up (self-check).', 'wait a little (`chemlab wait 60`).'),
        'SLEEP': ('SLEEP', '{inst} is in sleep mode.', 'switch it off and on (`power off`, `power on`).'),
        'NO_EVALUATION': ('OUT_OF_RANGE', 'the sample of {inst} has no data at these wavelengths.', 'measure inside its spectral range.'),
        'UNSUPPORTED': ('NOT_SUPPORTED', 'this twin of {inst} does not implement that setting.', 'see `chemlab help {inst} method`.'),
        'CALIBRATION_INPUT': ('BAD_INPUT', 'the calibration needs at least 3 different standards with intensities.',
                              'give concentrations_mol_m3=[...] intensities=[...] unknown_intensity=X accept_same_method=true.'),
        'ANALYSIS_INPUT': ('BAD_INPUT', 'the analysis input of {inst} is not usable (empty or mismatched).', 'measure first.'),
        'IRF_GRID_MISMATCH': ('BAD_INPUT', 'the IRF record is not an IRF measured on the same time grid (window and bins) as the decay.',
                              'record the IRF with `blank` (scatterer in place, em = ex) and the same window / bins, save it with '
                              '`spectrum --save-to irf.json`, then `analyze lifetime irf=irf.json tau=...`.'),
        'FIT_MODEL_MISMATCH': ('BAD_INPUT', 'the number of starting lifetimes must match the fit model of the method (mono: 1, bi: 2).',
                               'give tau=4ns (mono) or tau=1ns,5ns (bi).'),
        'FIT_UNIDENTIFIABLE': ('NO_RESULT', 'the lifetimes cannot be determined from these data.', 'collect more counts or change the start values.'),
    }

    # ------------------------------------------------------------------ helpers
    def sv(self, values, role, default=None):
        s = self.slot(role)
        if not s:
            return default
        if isinstance(s, dict):
            s = s.get('slot')
        sid, _, key = s.partition('#')
        v = values.v(sid) if hasattr(values, 'v') else values.get(sid)
        if key and isinstance(v, dict):
            v = v.get(key)
        return v

    def is_open(self, values):
        """True / False when the member has a lid / cover / door slot, else None."""
        if self.slot('opened'):
            return bool(self.sv(values, 'opened'))
        if self.slot('closed'):
            return not bool(self.sv(values, 'closed'))
        return None

    def wait_until(self, ctx, test, max_s, step, poll_s=1.0):
        """Move lab time until test(ctx) is true (checked every poll_s).  Returns True when it became true."""
        if test(ctx):
            return True
        ctx.advance(max_s, until=lambda v: test(ctx), step=step, poll_s=poll_s)
        return bool(test(ctx))

    def default_lid(self):
        parts = (self.vmap('open') or {}).get('parts') or {}
        return next(iter(parts), None)

    # ------------------------------------------------------------------ summary (status / read)
    def summary(self, values):
        def val(role, default=None):
            return self.sv(values, role, default)
        if self.slot('power') and not val('power'):
            return ['power off'], {'power': False}
        parts, js = (['power on'] if self.slot('power') else []), {'power': True}
        if self.slot('source'):
            on = bool(val('source'))
            ready = val('source_ready') if self.slot('source_ready') else None
            word = self.param('source_word', 'source')
            parts.append('%s %s' % (word, ('on' + ('' if ready is None else (', ready' if ready else ', warming up'))) if on else 'off'))
            js.update(source=on, source_ready=ready)
        if self.slot('purge_ready'):
            pr = bool(val('purge_ready'))
            fl = val('purge_flow')
            parts.append('purge %s%s' % (units.fmt(fl, 'flow') if finite(fl) and fl > 0 else 'off', ' (air flushed)' if pr else ' (air not flushed yet)'))
            js.update(purge_ready=pr, purge_flow_m3_s=fl)
        op = self.is_open(values)
        if op is not None:
            parts.append('%s %s' % (self.param('compartment_word') or self.default_lid() or 'compartment', 'open' if op else 'closed'))
            js['compartment_open'] = op
        if self.slot('sample'):
            sl = val('sample')
            sid = val('sample_id') if self.slot('sample_id') else None
            parts.append('sample %s' % (('in (%s)' % sid) if sid else 'in') if sl else 'sample: none')
            js.update(sample_loaded=bool(sl), sample_id=sid)
        lines = ['; '.join(parts)] if parts else []
        more = []
        if self.slot('method'):
            mc = bool(val('method'))
            more.append('method %s' % ('set' if mc else 'not set'))
            js['method_set'] = mc
        if self.slot('blank'):
            b = bool(val('blank'))
            more.append('%s %s' % (self.param('blank_word', 'blank'), 'ready' if b else 'not recorded'))
            js['blank_ready'] = b
        if self.slot('phase'):
            ph = val('phase')
            pts = val('points') if self.slot('points') else None
            more.append('acquisition %s%s' % (ph, (' (%s %s)' % (pts, self.param('points_word', 'points'))) if pts not in (None, '') else ''))
            js.update(phase=ph, points=pts)
        if more:
            lines.append('; '.join(more))
        if self.slot('temp') or self.slot('set'):
            tk = self.slot_si(values, self.slot('temp')) if self.slot('temp') else None
            sk = self.slot_si(values, self.slot('set')) if self.slot('set') else None
            lines.append('sample temperature %s (set point %s)' % (units.fmt(tk, 'temperature') if finite(tk) else 'n/a',
                                                                 units.fmt(sk, 'temperature') if finite(sk) else 'none'))
            js.update(temperature_k=tk, set_point_k=sk)
        for role, label in (self.map.get('extra_status') or {}).items():
            v = values.v(role) if hasattr(values, 'v') else values.get(role)
            lines.append('%s: %s' % (label, short(v, 80)))
        return lines or ['(no state mapped)'], js

    # ------------------------------------------------------------------ refusals
    def explain_refusal(self, ctx, cap, args, response):
        code = (response.get('error') or {}).get('code')
        if code == 'PRECONDITION_FAILED':
            ctx.refresh()
            if self.slot('power') and not self.sv(ctx, 'power'):
                return refused('POWER_OFF', '%s is switched off (it answered %s).' % (self.instrument_id, code),
                               'switch it on: `chemlab run %s power on`.' % self.instrument_id)
        return ClassAdapter.explain_refusal(self, ctx, cap, args, response)

    def need_power(self, ctx):
        if self.slot('power'):
            ctx.check(bool(self.sv(ctx, 'power')), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                      'switch it on: `chemlab run %s power on`.' % self.instrument_id, label='power on')

    def need_idle(self, ctx):
        if self.slot('phase'):
            ph = self.sv(ctx, 'phase')
            ctx.check(ph not in ('running', 'moving', 'driving', 'returning'), 'BUSY', '%s is measuring now (%s).' % (self.instrument_id, ph),
                      'wait for it (`chemlab wait job-N`) or stop it (`chemlab run %s stop`).' % self.instrument_id, label='not measuring')

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        REF = self.param('manual', REF_ALL)                                  # page references of this member's manuals (no text)
        out = {'power': shared.power_verb(self)}
        out.update(shared.open_close_verbs(self))
        wait_opts = {'wait': Opt('wait until it is done (moves lab time)', kind='flag'),
                     'max': Opt('with --wait: the longest wait (default 1 h)', kind='duration', metavar='TIME')}
        out.update({
            'source': Verb('source', 'switch the light source (lamp / laser / microwave bridge) on or off', self.h_source,
                           positionals=[Arg('on|off', 'on or off', choices=ON_OFF)],
                           options={'lamp': Opt('which lamp, where there are two', choices=['uv', 'vis']),
                                    'wait': Opt('then wait until it has warmed up (moves lab time)', kind='flag'),
                                    'max': Opt('with --wait: the longest wait (default 30 min)', kind='duration', metavar='TIME')},
                           preconditions=['power on', 'the member\'s own conditions (e.g. purge flushed, compartment closed)'],
                           errors=['POWER_OFF, PURGE_NOT_READY, LID_OPEN (refused)', 'NOT_READY (refused: still warming up after --max)'],
                           example='chemlab run INST source on --wait', reference=REF),
            'purge': Verb('purge', 'set the nitrogen purge flow (or off)', self.h_purge, positionals=[Arg('FLOW|off', 'flow, e.g. 5L/min, or off')],
                          options={'wait': Opt('then wait until the air is flushed out (moves lab time)', kind='flag'),
                                   'max': Opt('with --wait: the longest wait (default 30 min)', kind='duration', metavar='TIME')},
                          preconditions=['flow within the range of the purge controller'], errors=['OUT_OF_RANGE (usage)'],
                          example='chemlab run INST purge 5L/min --wait', reference=REF),
            'method': Verb('method', 'set the measurement method: KEY=VALUE ... (class keys with units, or native argument names)', self.h_method,
                           variadic=('KEY=VALUE', 'a setting, e.g. from=260nm to=190nm step=0.5nm'),
                           preconditions=['every value within the instrument range', 'not measuring', 'the member\'s own conditions'],
                           errors=['UNKNOWN_SETTING, OUT_OF_RANGE, BAD_CHOICE (usage)', 'BUSY (refused)'],
                           example='chemlab run INST method ' + self.param('method_example', 'from=260nm to=190nm step=1nm scans=2'),
                           reference=REF),
            'load': Verb('load', 'put a sample (cuvette / tube) into the instrument', self.h_load, positionals=[Arg('SAMPLE', 'the sample id')],
                         preconditions=['compartment open where it has one', 'no sample in it'], errors=['LID_CLOSED, OCCUPIED (refused)'],
                         example='chemlab run INST load sample_01', reference=REF),
            'unload': Verb('unload', 'take the sample out of the instrument', self.h_unload, preconditions=['compartment open', 'a sample in it'],
                           errors=['LID_CLOSED, NO_SAMPLE (refused)'], example='chemlab run INST unload'),
            'blank': Verb('blank', 'record the blank / background / zero (or the IRF) with the reference in place', self.h_blank,
                          options=dict(wait_opts), long_action=True,
                          preconditions=['power on', 'compartment closed', 'source ready', 'method set', 'the blank / reference in place'],
                          errors=['POWER_OFF, LID_OPEN, SOURCE_OFF, NO_METHOD, WRONG_CELL, BUSY (refused)'],
                          example='chemlab run INST blank --wait', reference=REF),
            'measure': Verb('measure', 'measure the sample (long runs give job-N; --wait blocks)', self.h_measure, options=dict(wait_opts),
                            long_action=True,
                            preconditions=['power on', 'compartment closed', 'source ready', 'method set', 'a sample in place',
                                           'a blank recorded with the same method where the instrument needs one'],
                            errors=['POWER_OFF, LID_OPEN, SOURCE_OFF, NO_METHOD, NO_SAMPLE, BLANK_REQUIRED, BUSY (refused)'],
                            example='chemlab run INST measure --wait --max 30min', reference=REF),
            'stop': Verb('stop', 'stop the running acquisition (the points measured so far are kept)', self.h_stop,
                         example='chemlab run INST stop'),
            'spectrum': Verb('spectrum', 'the last spectrum / trace: points, range, extremes (full record with --save-to)', self.h_spectrum,
                             readonly=True, options={'save-to': save_to_opt()}, example='chemlab run INST spectrum --save-to spectrum.json'),
            'set-temp': Verb('set-temp', 'set the sample (cell holder) temperature', self.h_set_temp,
                             positionals=[Arg('TEMP', 'set point, e.g. 25C or 298.15K (a bare number is °C)', kind='temperature')],
                             options={'wait': Opt('then wait until the sample is there (within the member\'s tolerance)', kind='flag'),
                                      'max': Opt('with --wait: the longest wait (default 30 min)', kind='duration', metavar='TIME')},
                             preconditions=['the temperature accessory installed', 'set point within its range', 'not measuring'],
                             errors=['OUT_OF_RANGE (usage)', 'NO_ACCESSORY, BUSY, NOT_REACHED (refused)'],
                             example='chemlab run INST set-temp 25C --wait', reference=REF),
            'analyze': Verb('analyze', 'the instrument\'s own analysis of the last spectrum (MODEL: structure, peaks, kinetics, '
                                       'lifetime, integrate, relaxation, g-factor, ...)', self.h_analyze, readonly=True,
                            positionals=[Arg('MODEL', 'which analysis (see `chemlab help INST analyze`)')],
                            variadic=('KEY=VALUE', 'model inputs, e.g. conc=0.2mM path=1mm'), options={'save-to': save_to_opt()},
                            preconditions=['a complete measurement', 'the model\'s inputs'], errors=['UNKNOWN_MODEL, MISSING_SETTING (usage)',
                                                                                                 'NO_RESULT (refused)'],
                            example='chemlab run INST ' + next((mm.get('example') or 'analyze %s' % k for k, mm in
                                                                ((self.map.get('verbs') or {}).get('analyze') or {}).get('models', {}).items()),
                                                               'analyze MODEL'), reference=REF),
        })
        return out

    def verb_help(self, name):
        r = ClassAdapter.verb_help(self, name)
        extra = []
        if name == 'method' and self.vmap('method') and self.lab is not None:
            extra = self.method_help()
        elif name == 'analyze' and self.vmap('analyze'):
            extra = ['models on %s:' % self.instrument_id]
            for mname, mm in (self.vmap('analyze').get('models') or {}).items():
                keys = ', '.join('%s (%s)' % (k, s.get('kind') or s['arg']) for k, s in (mm.get('keys') or {}).items())
                extra.append('  %-12s %s%s' % (mname, mm.get('about', mm['cap']), ('; keys: ' + keys) if keys else ''))
        if extra:
            r['text'] += '\n' + '\n'.join(extra) + '\n'
            r['json']['details'] = extra
        return r

    def method_help(self):
        m = self.vmap('method')
        lines = ['method keys on %s:' % self.instrument_id]
        if m.get('program_cap'):
            for mode, spec in (m.get('modes') or {}).items():
                lines.append('  %s mode: %s' % (mode, ', '.join('%s (%s)' % (k, s.get('kind') or s.get('arg')) for k, s in (spec.get('keys') or {}).items())))
            lines.append('  program=N starts the stored Hach program N')
            return lines
        cap = m['cap']
        props = (self.lab.contract['capabilities'][cap].get('input_schema') or {}).get('properties') or {}
        base = self.method_base(cap)
        for k, s in (m.get('keys') or {}).items():
            lines.append('  %-11s -> %s (%s)' % (k, s['arg'], s.get('kind') or self.describe_schema(props.get(s['arg']))))
        lines.append('baseline (contract example, native units): ' + short(base, 400))
        if m.get('current'):
            lines.append('settings the instrument reports keep their current values: ' + ', '.join(m['current']))
        rest = [p for p in props if p not in {s['arg'] for s in (m.get('keys') or {}).values()}]
        if rest:
            lines.append('other native arguments may be given by name (a bare number is in the declared unit): ' + ', '.join(rest))
        return lines

    def class_section(self, *a, **kw):
        lines, js = ClassAdapter.class_section(self, *a, **kw)
        extra = ('  more units of this class: wavenumber cm-1 (default) / m-1; magnetic field mT (default) / G / T; short times '
                 'ps / ns / us / ms; GHz; concentration mM / uM; Raman count rate /s')
        at = next((i for i, x in enumerate(lines) if x.startswith('  errors say')), len(lines))
        lines.insert(at, extra)
        js['units_extra'] = ['wavenumber', 'field', 'scan_speed', 'rate', 'offset']
        return lines, js

    @staticmethod
    def describe_schema(s):
        if not s:
            return '?'
        if s.get('enum'):
            return 'one of ' + '|'.join(map(str, s['enum']))
        return s.get('type', '?') + ((' ' + s['x-unit']) if s.get('x-unit') not in (None, '1') else '')

    # ------------------------------------------------------------------ setting parser (method / analyze keys)
    def parse_setting(self, text, schema, kind, what):
        """One KEY=VALUE value -> the native value for the argument's schema (units converted to the declared unit, ranges and
        enums checked here so that nothing invalid is sent)."""
        ns = num_schema(schema)
        if ns is not None:
            u = units.declared(ns)
            dkind = kind_of_declared(u) if u else None
            t = text.strip().replace(' ', '')
            m = re.fullmatch(NUM + r'(.*)', t)
            if not m:
                raise invalid('BAD_NUMBER', '%s=%s is not a number%s.' % (what, text, ' with a unit' if (kind or dkind) else ''),
                              'write e.g. %s=%s.' % (what, kind_example(kind or dkind)))
            if kind or (m.group(2) and dkind):
                si = parse_q(t, kind or dkind, what=what)
                if kind == 'temperature_delta' or dkind is None:
                    value = si
                else:
                    value = si if units.unit_kind(u) is None else units.to_declared(si, u)
            elif m.group(2):
                raise invalid('BAD_NUMBER', '%s=%s: this setting takes a plain number.' % (what, text), 'leave out the unit.')
            else:
                value = float(m.group(1))                                  # a bare number for a native argument: its declared unit
            if ns.get('type') == 'integer':
                if abs(value - round(value)) > 1e-9:
                    raise invalid('NOT_WHOLE', '%s=%s must be a whole number.' % (what, text), 'give an integer.')
                value = int(round(value))
            if ns.get('enum'):
                hit = [e for e in ns['enum'] if e == value or abs(e - value) <= 1e-9 * abs(e)]
                if not hit:
                    show = ', '.join(fmt_q(e, kind or dkind) if (kind or dkind) else str(e) for e in ns['enum'])
                    raise invalid('BAD_CHOICE', '%s=%s is not available on %s.' % (what, text, self.instrument_id), 'choose one of: %s.' % show)
                value = hit[0]
            lo, hi = ns.get('minimum'), ns.get('maximum')
            if (lo is not None and value < lo - 1e-9 * abs(lo)) or (hi is not None and value > hi + 1e-9 * abs(hi)):
                k2 = kind or dkind
                show = (lambda x: fmt_q(x, k2)) if k2 else (lambda x: '%g' % x)
                raise invalid('OUT_OF_RANGE', '%s=%s is outside the range of %s (%s to %s).' % (
                    what, text, self.instrument_id, show(lo) if lo is not None else '-', show(hi) if hi is not None else '-'),
                    'give %s from %s to %s.' % (what, show(lo) if lo is not None else '-', show(hi) if hi is not None else '-'))
            return value
        types = {schema.get('type')} if schema.get('type') else {x.get('type') for x in schema.get('anyOf') or []}
        if types == {'boolean'}:
            low = text.strip().lower()
            if low in ('yes', 'true', 'on', '1'):
                return True
            if low in ('no', 'false', 'off', '0'):
                return False
            raise invalid('BAD_CHOICE', '%s=%s is not yes or no.' % (what, text), 'write %s=yes or %s=no.' % (what, what))
        if schema.get('enum'):
            for e in schema['enum']:
                if str(e).lower() == text.strip().lower():
                    return e
            raise invalid('BAD_CHOICE', '%s=%s is not allowed on %s.' % (what, text, self.instrument_id),
                          'choose one of: %s.' % ', '.join(map(str, schema['enum'])))
        if 'array' in types and not text.strip().startswith('['):
            items = (schema.get('items') or {}) if schema.get('type') == 'array' else next((x.get('items') or {} for x in schema.get('anyOf') or [] if x.get('type') == 'array'), {})
            return [self.parse_setting(x, items, kind, what) for x in text.split(',') if x.strip()]
        return coerce(text, schema, what)

    def method_base(self, cap):
        m = self.vmap('method') or {}
        if m.get('base') == 'none':
            return {}
        return json.loads(json.dumps((self.lab.contract['capabilities'][cap].get('example_arguments') or {})))

    def collect(self, ctx, items, keys, props, passthrough=True, what='setting'):
        """KEY=VALUE items -> {native arg: value}, the (key, native) pairs given."""
        out, given = {}, []
        for item in items:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write KEY=VALUE (see `chemlab help %s %s`).' % (self.instrument_id, ctx.verb.name)))
            if k in keys and keys[k].get('local'):                         # a class-side input (e.g. the wavelength to pick)
                try:
                    out['_' + k] = parse_q(v, keys[k]['kind'], what=k)
                except Exception as exc:                                         # noqa: BLE001
                    if hasattr(exc, 'kind'):
                        ctx.fail(exc)
                    raise
                given.append((k, '_' + k))
                continue
            if k in keys:
                spec = keys[k]
                native, kind = spec['arg'], spec.get('kind')
                if spec.get('file'):                                        # a JSON record the agent saved in its work dir
                    out[native] = self.work_json(ctx, v, spec['file'], k)
                    given.append((k, native))
                    continue
            elif passthrough and k in props:
                native, kind = k, None
            else:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no %s %r.' % (self.instrument_id, what, k),
                                 '%ss: %s.' % (what, ', '.join(list(keys) + ([p for p in props if p not in {s['arg'] for s in keys.values()}] if passthrough else [])))))
            if native not in props:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no %s %r here.' % (self.instrument_id, what, k), 'see `chemlab help %s %s`.' % (self.instrument_id, ctx.verb.name)))
            if native in out:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % k, 'give it once.'))
            try:
                out[native] = self.parse_setting(v, props[native], kind, k)
            except Exception as exc:                                             # noqa: BLE001 - usage errors pass through ctx.fail
                if hasattr(exc, 'kind'):
                    ctx.fail(exc)
                raise
            given.append((k, native))
        return out, given

    # ------------------------------------------------------------------ handlers
    def h_source(self, ctx, p):
        m = self.vmap('source')
        want = p.pos[0] == 'on'
        lamp = p.get('lamp')
        if m.get('lamp_arg') and lamp is None:
            ctx.fail(invalid('WHICH_LAMP', '%s has two lamps.' % self.instrument_id, 'give --lamp uv or --lamp vis.'))
        if lamp is not None and not m.get('lamp_arg'):
            ctx.fail(invalid('NO_LAMP_CHOICE', '%s has one source.' % self.instrument_id, 'leave out --lamp.'))
        self.need_power(ctx)
        if want:
            self.requirements(ctx, 'source')
        args = dict(m.get('args') or {})
        if m.get('lamp_arg'):
            args[m['lamp_arg']] = lamp
        args[m['arg']] = want
        cur = None if m.get('lamp_arg') else self.sv(ctx, 'source')
        word = self.param('source_word', 'source')
        if cur is not None and bool(cur) == want and not p.get('wait'):
            return {'text': 'ok: %s %s is already %s\n' % (self.instrument_id, word, p.pos[0]), 'json': {'source': want, 'changed': False}}
        if cur is None or bool(cur) != want:
            ctx.call(m['cap'], args, '%s %s' % (word + (' ' + lamp if lamp else ''), p.pos[0]))
        text = 'ok: %s %s %s\n' % (self.instrument_id, word + (' (%s)' % lamp if lamp else ''), p.pos[0])
        js = {'source': want, 'changed': cur is None or bool(cur) != want}
        if want and p.get('wait') and not ctx.dry_run:
            if not self.slot('source_ready'):
                text += 'note: %s has no warm-up flag to wait for.\n' % self.instrument_id
            else:
                t0 = ctx.now()
                ok = self.wait_until(ctx, lambda c: bool(self.sv(c, 'source_ready')), p.get('max') or 1800.0, 'wait for the %s to warm up' % word)
                if not ok:
                    ctx.fail(refused('NOT_READY', 'the %s of %s is not ready after %s.' % (word, self.instrument_id, units.fmt_s(ctx.now() - t0)),
                                     'wait longer (`chemlab run %s source on --wait --max 1h`) and check `status`.' % self.instrument_id))
                text += 'ready after %s of warm-up (t = %.0f s)\n' % (units.fmt_s(ctx.now() - t0), ctx.now())
                js['waited_s'] = ctx.now() - t0
        elif want and self.slot('source_ready') and not ctx.dry_run and not self.sv(ctx, 'source_ready'):
            text += 'it is warming up: `chemlab run %s source on --wait` waits for it.\n' % self.instrument_id
        return {'text': text, 'json': js}

    def h_purge(self, ctx, p):
        m = self.vmap('purge')
        text = p.raw['FLOW|off'].strip().lower()
        f = 0.0 if text in ('off', '0', 'stop') else parse_q(text, 'flow', what='flow')
        lo, hi = self.arg_range_si(m['cap'], m['arg'])
        if (lo is not None and f < lo - 1e-15) or (hi is not None and f > hi + 1e-15):
            ctx.fail(invalid('OUT_OF_RANGE', 'a purge of %s is outside 0..%s on %s.' % (units.fmt(f, 'flow'), units.fmt(hi, 'flow'), self.instrument_id),
                             'give a flow up to %s (e.g. 5L/min).' % units.fmt(hi, 'flow')))
        self.need_power(ctx)
        ctx.call(m['cap'], {m['arg']: self.arg_value(m['cap'], m['arg'], f)}, 'set the purge to %s' % (units.fmt(f, 'flow') if f else 'off'))
        out = 'ok: %s purge %s\n' % (self.instrument_id, ('%s (%.3g L/min)' % (units.fmt(f, 'flow'), f * 6e4)) if f else 'off')
        js = {'purge_m3_s': f}
        if f and p.get('wait') and not ctx.dry_run and self.slot('purge_ready'):
            t0 = ctx.now()
            ok = self.wait_until(ctx, lambda c: bool(self.sv(c, 'purge_ready')), p.get('max') or 1800.0, 'wait for the purge', poll_s=5.0)
            if not ok:
                ctx.fail(refused('NOT_READY', 'the optics of %s are not flushed after %s.' % (self.instrument_id, units.fmt_s(ctx.now() - t0)),
                                 'wait longer or raise the flow; check the nitrogen supply (`status`).'))
            out += 'air flushed out after %s (t = %.0f s)\n' % (units.fmt_s(ctx.now() - t0), ctx.now())
            js['waited_s'] = ctx.now() - t0
        return {'text': out, 'json': js}

    def h_method(self, ctx, p):
        m = self.vmap('method')
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'see `chemlab help %s method` for the keys.' % self.instrument_id))
        if m.get('program_cap'):
            return self.h_method_program(ctx, p, m)
        cap = m['cap']
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        given_args, given = self.collect(ctx, p.extra, m.get('keys') or {}, props, m.get('passthrough', True))
        self.need_idle(ctx)
        self.requirements(ctx, 'method')
        args = self.method_base(cap)
        current = []
        for a, slot in (m.get('current') or {}).items():                    # settings the instrument reports keep their values
            v = ctx.v(slot)
            if v is not None and a in props:
                args[a] = v
                current.append(a)
        args.update(m.get('args') or {})
        args.update(given_args)
        for a, src in (m.get('derive') or {}).items():                      # arguments that follow others (e.g. a single-wavelength mode)
            if a not in given_args and src in args:
                args[a] = args[src]
        defaulted = [a for a in args if a not in given_args and a not in current]
        kept = [a for a in args if a not in given_args and a in current]
        ctx.call(cap, args, 'set the method (%s)' % ', '.join('%s=%s' % (k, short(given_args[n], 24)) for k, n in given))
        shown = ', '.join('%s=%s' % (k, self.show_native(props.get(n), given_args[n], (m.get('keys') or {}).get(k, {}).get('kind'))) for k, n in given)
        text = 'ok: method of %s set: %s\n' % (self.instrument_id, shown)
        if kept:
            text += '  unchanged: %s\n' % ', '.join('%s=%s' % (a, self.show_native(props.get(a), args[a], None)) for a in kept)
        if defaulted:
            text += '  from the standard method: %s\n' % ', '.join('%s=%s' % (a, self.show_native(props.get(a), args[a], None)) for a in defaulted)
        notes = []
        if self.slot('blank') and m.get('invalidates_blank', True):
            notes.append('a new method needs a new blank before measuring.')
        return {'text': text, 'json': {'arguments': args, 'given': {k: given_args[n] for k, n in given}, 'unchanged': kept,
                                       'from_standard': defaulted}, 'notes': notes}

    def show_native(self, schema, v, kind):
        if isinstance(v, list) and schema and schema.get('type') == 'array' and v and all(isinstance(x, (int, float)) for x in v):
            items = [self.show_native(schema.get('items') or {}, x, kind) for x in v[:6]]
            return '[%s%s]' % (', '.join(items), ', ... (%d)' % len(v) if len(v) > 6 else '')
        ns = num_schema(schema or {})
        if ns is not None and isinstance(v, (int, float)) and not isinstance(v, bool):
            u = units.declared(ns)
            k = kind or (kind_of_declared(u) if u else None)
            if k:
                si = v if units.unit_kind(u) is None else units.from_declared(v, u)
                return fmt_q(si, k)
        return short(v, 30)

    def h_method_program(self, ctx, p, m):
        """Hach DR6000 style: a program / mode is started (program.start) and its options set (program.options)."""
        modes = m.get('modes') or {}
        items = dict(x.partition('=')[::2] for x in p.extra)
        for x in p.extra:
            if '=' not in x:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % x, 'write KEY=VALUE (see `chemlab help %s method`).' % self.instrument_id))
        start_props = (ctx.cap_spec(m['program_cap']).get('input_schema') or {}).get('properties') or {}
        opt_schema = ((ctx.cap_spec(m['options_cap']).get('input_schema') or {}).get('properties') or {}).get('settings') or {}
        opt_props = opt_schema.get('properties') or {}
        if 'program' in items:
            mode, number = 'stored', self.parse_setting(items.pop('program'), start_props.get('number') or {}, None, 'program')
        else:
            number = None
            mode = next((md for md, spec in modes.items() if set(items) & set(spec.get('keys') or {})), None)
            if mode is None:
                ctx.fail(invalid('WHICH_MODE', 'method on %s needs the keys of one mode.' % self.instrument_id,
                                 'e.g. from=400nm to=700nm step=1nm (scan), wavelength=510nm (single), or program=N (stored program).'))
        keys = (modes.get(mode) or {}).get('keys') or {}
        settings, given = self.collect(ctx, ['%s=%s' % kv for kv in items.items()], keys, opt_props, m.get('passthrough', True))
        self.need_power(ctx)
        self.requirements(ctx, 'method')
        start = {'kind': (modes.get(mode) or {}).get('kind', mode)}
        if number is not None:
            start['number'] = number
        ctx.call(m['program_cap'], start, 'start the %s %s' % (mode, 'program %d' % number if number else 'mode'))
        if settings:
            ctx.call(m['options_cap'], {'settings': settings}, 'set the %s options (%s)' % (mode, ', '.join(k for k, _ in given)))
        shown = ', '.join('%s=%s' % (k, self.show_native(opt_props.get(n), settings[n], keys.get(k, {}).get('kind'))) for k, n in given)
        text = 'ok: %s runs the %s%s%s\n' % (self.instrument_id, mode, (' program %d' % number) if number else ' mode', (': ' + shown) if shown else '')
        return {'text': text, 'json': {'mode': mode, 'number': number, 'settings': settings},
                'notes': ['a new program or range needs a new zero: `blank` with the blank cuvette, then `measure`.']}

    def h_load(self, ctx, p):
        m = self.vmap('load')
        self.requirements(ctx, 'load')
        sid = p.pos[0]
        ctx.call(m['cap'], {m['arg']: sid}, 'load %s' % sid, refusal=lambda r: self.load_refusal(ctx, r, sid))
        return {'text': 'ok: %s loaded into %s\n' % (sid, self.instrument_id), 'json': {'sample_id': sid}}

    def load_refusal(self, ctx, r, sid):
        code = (r.get('error') or {}).get('code')
        if code == 'PRECONDITION_FAILED':
            return refused('UNKNOWN_SAMPLE', '%s has no sample %r ready to load (or the holder is not ready for it).' % (self.instrument_id, sid),
                           'check the sample id the lab gave you and `chemlab run %s status`.' % self.instrument_id)
        return None

    def h_unload(self, ctx, p):
        m = self.vmap('unload')
        self.requirements(ctx, 'unload')
        ctx.call(m['cap'], dict(m.get('args') or {}), 'take the sample out')
        return {'text': 'ok: sample taken out of %s\n' % self.instrument_id, 'json': {'unloaded': True}}

    def run_checks(self, ctx, verb):
        self.need_power(ctx)
        self.need_idle(ctx)
        self.requirements(ctx, verb)

    def start_run(self, ctx, p, verb, what):
        m = self.vmap(verb)
        self.run_checks(ctx, verb)
        r = ctx.call(m['cap'], dict(m.get('args') or {}), '%s (%s)' % (what, m['cap']))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        if not m.get('job'):                                                # synchronous: done now
            res = r.get('result') or {}
            text = 'ok: %s done on %s (t = %.1f s)\n' % (what, self.instrument_id, ctx.now())
            brief = {k: v for k, v in res.items() if not isinstance(v, (dict, list))} if isinstance(res, dict) else {}
            if brief:
                text += '  ' + ', '.join('%s=%s' % (k, short(v, 30)) for k, v in list(brief.items())[:8]) + '\n'
            if verb == 'measure':
                text += 'read it: `chemlab run %s spectrum`\n' % self.instrument_id
            return {'text': text, 'json': {'done': True, 'result': brief}}
        job = self.make_job(ctx, what)
        if p.get('wait'):
            w = job.wait_reply(ctx.core, ctx.req, p.get('max') or 3600.0)
            ctx.refresh()
            text = '%s: %s\n%s' % (job.handle, what, w['text'])
            if w['json'].get('status') == 'succeeded' and verb == 'measure':
                text += 'read it: `chemlab run %s spectrum`\n' % self.instrument_id
            if w['json'].get('status') not in RUNNING and w['json'].get('status') != 'succeeded':
                ctx.fail(refused('RUN_FAILED', '%s %s on %s (%s).' % (what, w['json'].get('status'), self.instrument_id, w['json'].get('state_status', '')),
                                 'see `chemlab run %s status` (warning) and fix the cause, then repeat.' % self.instrument_id,
                                 detail={'job': job.handle}))
            js = dict(w['json'])
            js['job'] = job.handle
            return {'text': text, 'json': js}
        return {'text': 'started %s: %s on %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, what, self.instrument_id, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}

    def make_job(self, ctx, what):
        st = self.map.get('job') or {}
        phase, done, failed = self.slot('phase'), st.get('done', ['succeeded']), st.get('failed', ['failed', 'cancelled', 'stopped'])
        ad = self

        def state(c):
            v = ad.sv(c, 'phase')
            pts = ad.sv(c, 'points') if ad.slot('points') else None
            warn = c.v(ad.slot('warning')) if ad.slot('warning') else None
            text = '%s = %s%s%s' % (phase, v, (', %s %s' % (pts, ad.param('points_word', 'points'))) if pts is not None else '',
                                    (', warning %s' % warn) if warn else '')
            js = {'status': v, 'points': pts}
            if warn:
                js['warning'] = warn
            if v in done:
                return 'succeeded', text, js
            if v in failed:
                return 'failed', text, js
            return 'running', text, js

        def stop(c):
            sm = ad.vmap('stop')
            c.call(sm['cap'], dict(sm.get('args') or {}), 'stop the acquisition')
            return 'stopped with %s' % sm['cap']
        return StateJob(ctx.core, self, ctx.req, '%s on %s' % (what, self.instrument_id), state, stop=stop if self.vmap('stop') else None,
                        max_s=float(self.param('max_run_s', 7200)), poll_s=float(self.param('poll_s', 1.0)))

    def h_blank(self, ctx, p):
        return self.start_run(ctx, p, 'blank', self.param('blank_word', 'blank'))

    def h_measure(self, ctx, p):
        return self.start_run(ctx, p, 'measure', 'measurement')

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        if self.slot('phase'):
            ph = self.sv(ctx, 'phase')
            if ph not in ('running', 'moving', 'driving', 'returning', 'stopping'):
                return {'text': 'ok: %s is not measuring (%s); nothing to stop\n' % (self.instrument_id, ph), 'json': {'stopped': False, 'phase': ph}}
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop the acquisition')
        ph = self.sv(ctx, 'phase') if self.slot('phase') else None
        return {'text': 'ok: %s stopped (%s); the points so far are kept\n' % (self.instrument_id, ph), 'json': {'stopped': True, 'phase': ph}}

    def h_spectrum(self, ctx, p):
        m = self.vmap('spectrum')
        if m.get('cap'):
            r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the spectrum (%s)' % m['cap'], query=True)
            rec = r.get('result')
        else:
            rec = {s: ctx.v(s) for s in m.get('reads') or []}
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        alts = m.get('alternatives') or []
        x = get_path(rec, m.get('x'))
        y = get_path(rec, m.get('y'))
        spec = m
        for alt in alts:                                                    # another view (e.g. DR6000 single reading vs a scan)
            if isinstance(x, list) and x:
                break
            x, y, spec = get_path(rec, alt.get('x')), get_path(rec, alt.get('y')), dict(m, **alt)
        if not isinstance(x, list) or not x:
            if isinstance(x, (int, float)) and not isinstance(x, bool):
                x, y = [x], [y] if not isinstance(y, list) else y
            else:
                ctx.fail(refused('NO_RESULT', '%s has no spectrum yet.' % self.instrument_id,
                                 'measure first (`chemlab run %s measure --wait`).' % self.instrument_id))
        pts = [(a, b) for a, b in zip(x, y or []) if finite(a) and finite(b)]
        fy = float(spec.get('y_factor', 1.0))
        xk = spec.get('x_kind')
        lines = ['%s spectrum: %d points, %s from %s to %s' % (self.instrument_id, len(x), spec.get('x_name', 'x'), fmt_q(x[0], xk) if xk else x[0],
                                                               fmt_q(x[-1], xk) if xk else x[-1])]
        js = {'points': len(x), 'x_name': spec.get('x_name', 'x'), 'y_name': spec.get('y_name', 'y'), 'x_unit': spec.get('x_unit'),
              'y_unit': spec.get('y_unit'), 'x': x, 'y': y}
        if pts:
            hi = max(pts, key=lambda q: q[1])
            lo = min(pts, key=lambda q: q[1])
            yl = spec.get('y_label', spec.get('y_name', 'y'))
            lines.append('  maximum %s %.6g at %s; minimum %.6g at %s' % (yl, hi[1] * fy, fmt_q(hi[0], xk) if xk else hi[0], lo[1] * fy,
                                                                        fmt_q(lo[0], xk) if xk else lo[0]))
            disp = DISPLAY.get(xk)
            js['max'] = {'x': hi[0], 'y': hi[1]}
            js['min'] = {'x': lo[0], 'y': lo[1]}
            if disp:                                                        # the position in the usual unit (nm, cm-1, mT, ...)
                for d, q in (('max', hi), ('min', lo)):
                    js[d].update(at=round(q[0] / disp[1], 9), unit=disp[0])
        for k in spec.get('fields') or []:
            v = get_path(rec, k)
            if v is not None:
                lines.append('  %s = %s' % (k.replace('/', '.'), short(v, 80)))
                js[k.replace('/', '_')] = v
        js['record'] = rec
        return {'text': '\n'.join(lines) + '\n', 'json': js}

    def h_set_temp(self, ctx, p):
        m = self.vmap('set-temp')
        k = p.pos[0]
        lo, hi = self.arg_range_si(m['cap'], m['arg'])
        if (lo is not None and k < lo - 1e-9) or (hi is not None and k > hi + 1e-9):
            ctx.fail(invalid('OUT_OF_RANGE', 'a sample temperature of %s is outside the range of %s (%s to %s).' % (
                units.fmt(k, 'temperature'), self.instrument_id, units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature')),
                'give a temperature from %s to %s.' % (units.fmt(lo, 'temperature'), units.fmt(hi, 'temperature'))))
        self.need_idle(ctx)
        self.requirements(ctx, 'set-temp')
        args = dict(m.get('args') or {})
        args[m['arg']] = round(self.arg_value(m['cap'], m['arg'], k), 10)
        ctx.call(m['cap'], args, 'set the sample temperature to %s' % units.fmt(k, 'temperature'))
        tk = self.slot_si(ctx, self.slot('temp')) if self.slot('temp') else None
        text = 'ok: sample temperature set point of %s %s; now %s\n' % (self.instrument_id, units.fmt(k, 'temperature'),
                                                                      units.fmt(tk, 'temperature') if finite(tk) else 'n/a')
        js = {'set_point_k': k, 'temperature_k': tk}
        notes = ['a new temperature needs a new blank before measuring.'] if self.slot('blank') else []
        if p.get('wait') and not ctx.dry_run and self.slot('temp'):
            tol = float(self.param('temp_tolerance_k', 0.1))
            t0 = ctx.now()
            ok = self.wait_until(ctx, lambda c: finite(self.slot_si(c, self.slot('temp'))) and abs(self.slot_si(c, self.slot('temp')) - k) <= tol,
                                 p.get('max') or 1800.0, 'wait until the sample is at %s' % units.fmt(k, 'temperature'), poll_s=1.0)
            tk = self.slot_si(ctx, self.slot('temp'))
            if not ok:
                ctx.fail(refused('NOT_REACHED', 'the sample of %s is at %s after %s, not within %.2g K of %s.' % (
                    self.instrument_id, units.fmt(tk, 'temperature'), units.fmt_s(ctx.now() - t0), tol, units.fmt(k, 'temperature')),
                    'wait longer (`chemlab run %s set-temp ... --wait --max 1h`).' % self.instrument_id))
            text += 'reached %s after %s (t = %.0f s)\n' % (units.fmt(tk, 'temperature'), units.fmt_s(ctx.now() - t0), ctx.now())
            js.update(temperature_k=tk, waited_s=ctx.now() - t0)
        return {'text': text, 'json': js, 'notes': notes}

    def h_analyze(self, ctx, p):
        a = self.vmap('analyze')
        models = a.get('models') or {}
        name = p.pos[0]
        if name not in models:
            ctx.fail(invalid('UNKNOWN_MODEL', '%s has no analysis %r.' % (self.instrument_id, name), 'models: %s.' % ', '.join(models)))
        mm = models[name]
        cap = mm['cap']
        props = (ctx.cap_spec(cap).get('input_schema') or {}).get('properties') or {}
        args, given = self.collect(ctx, p.extra, mm.get('keys') or {}, props, mm.get('passthrough', False), what='input')
        args.update({k: v for k, v in (mm.get('args') or {}).items() if k not in args})
        for k, spec in (mm.get('keys') or {}).items():
            if spec.get('required') and (('_' + k) if spec.get('local') else spec['arg']) not in args:
                ctx.fail(invalid('MISSING_SETTING', 'analyze %s on %s needs %s=...' % (name, self.instrument_id, k),
                                 'e.g. %s' % mm.get('example', 'see `chemlab help %s analyze`' % self.instrument_id)))
        rec = None
        for native, src in (mm.get('fill') or {}).items():                  # inputs taken from the last result or the state
            if native in args:
                continue
            if 'slot' in src:
                v = ctx.v(src['slot'])
            else:
                if rec is None:
                    rq = src.get('cap') or mm.get('result_cap')
                    r = ctx.call(rq, {}, 'read the result (%s)' % rq, query=True)
                    rec = r.get('result') if not ctx.dry_run else {}
                v = get_path(rec, src['path'])
                if src.get('pick') and isinstance(v, list):                 # a column of 2-D data at the agent's choice
                    v = self.pick_column(ctx, rec, src, v, args, mm)
            if ctx.dry_run and v is None:
                v = []
            if v is None or v == [] and not ctx.dry_run:
                ctx.fail(refused('NO_RESULT', '%s has no result to analyse (%s missing).' % (self.instrument_id, native),
                                 'measure first (`chemlab run %s measure --wait`).' % self.instrument_id))
            if src.get('object'):
                v = {k2: get_path(rec, p2) for k2, p2 in src['object'].items()}
            args[native] = v
        for k, spec in (mm.get('keys') or {}).items():
            if spec.get('required') and (('_' + k) if spec.get('local') else spec['arg']) not in args:
                ctx.fail(invalid('MISSING_SETTING', 'analyze %s on %s needs %s=...' % (name, self.instrument_id, k),
                                 'e.g. %s' % mm.get('example', 'see `chemlab help %s analyze`' % self.instrument_id)))
        for k in [k for k in args if k.startswith('_')]:                     # class-side inputs are not sent
            args.pop(k)
        if not ctx.dry_run:                                                 # the data must fit the analysis (e.g. enough points)
            problems = validate(ctx.cap_spec(cap).get('input_schema') or {'type': 'object', 'properties': {}}, args, cap)
            if problems:
                ctx.fail(refused('ANALYSIS_INPUT', 'the data of %s do not fit the %s analysis: %s.' % (self.instrument_id, name, '; '.join(problems[:3])),
                                 mm.get('hint', 'measure with enough points (see `chemlab help %s analyze`).' % self.instrument_id)))
        r = ctx.call(cap, args, 'analyze (%s)' % cap, query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') or {}
        lines = ['%s analysis %s:' % (self.instrument_id, name)]
        for k in mm.get('show') or [k for k, v in res.items() if not isinstance(v, (dict, list, str)) or k in ('fractions',)][:10]:
            if k in res:
                lines.append('  %s = %s' % (k, short(res[k], 160)))
        return {'text': '\n'.join(lines) + '\n', 'json': {'model': name, 'result': res}}

    def pick_column(self, ctx, rec, src, v, args, mm):
        """One column of 2-D data (rows = delays, columns = the axis): the column nearest to the agent's value."""
        axis = get_path(rec, src['pick']['axis'])
        want = args.get('_' + src['pick']['key'])
        if not isinstance(axis, list) or not axis or want is None:
            return v
        idx = min(range(len(axis)), key=lambda i: abs(axis[i] - want) if finite(axis[i]) else float('inf'))
        ctx.notes.append('analysed at %s (the nearest point to %s)' % (fmt_q(axis[idx], 'wavelength'), fmt_q(want, 'wavelength')))
        return [row[idx] if isinstance(row, list) and len(row) > idx else None for row in v]

    def work_json(self, ctx, name, path, key):
        """A record the agent saved in its work dir (e.g. `spectrum --save-to irf.json`), the part at ``path``."""
        p = ctx.core._work_path(name)
        if not p.is_file():
            ctx.fail(invalid('NO_FILE', '%s=%s: there is no such file in your work dir.' % (key, name),
                             'save it first, e.g. `chemlab run %s spectrum --save-to %s` right after the run.' % (self.instrument_id, name)))
        try:
            data = json.loads(p.read_text(encoding='utf-8'))
        except ValueError:
            ctx.fail(invalid('BAD_FILE', '%s is not a JSON record.' % name, 'give the file written by `spectrum --save-to`.'))
        v = get_path(data, path)
        if v is None:
            ctx.fail(invalid('BAD_FILE', '%s has no %s.' % (name, path.replace('/', '.')), 'give the file written by `spectrum --save-to` '
                             'after the right run.'))
        return v

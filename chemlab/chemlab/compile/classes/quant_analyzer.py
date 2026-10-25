"""Calibrated quantitation class ("quant_analyzer"): elemental (CHNS, O/N/H, AOX), TOC, AAS, ICP-OES, ICP-MS, XRF, direct Hg,
RGA, Orbitrap MS, IRMS and segmented-flow analyzers.  The operator action is "measure composition against a calibration":
switch the excitation source on, set the method, measure a blank, measure standards, fit a curve from the standard records, use
it, measure the sample and read the result.

Verbs (members and mappings: configs/adapters/quant_analyzer.json):

    source on|off [--channel CH] [--wait] [--max T]     plasma / flame / filament / ion source / carrier gas / detector lamp
    method KEY=VALUE ... [--channel CH]                 the measurement method (class keys with units, or native names)
    load NAME [--slot S] [--mass M] [--volume V] ...    a sample-table / autosampler / inlet entry (where the agent does it)
    calibrate [CURVE] --from LIST [--reference REF] [--levels L,...] [--channel CH] [--species S] [--elements E,...] [--fit F]
    use-curve CURVE[,CURVE] [--channel CH]              select the calibration used for samples
    blank                                               the blank value used for the correction
    measure [--slot N | --slots a,b] [--time T] [--cups LIST] [--wash T] [--wait] [--max T]   run the sample (job-N)
    stop                                                stop the measurement
    result [--save-to F]                                the result in plain units (mg/L, %, µg, Pa, ‰)
    export json|csv [--save-to F]                       the instrument's own result export
    records [ID] [--save-to F]                          stored measurement records (ids for calibrate) or one record
    status                                              the class view

power, open / close and wait come from the shared layer (configs/adapters/shared.json).

Method key dictionary (each member maps the keys it has; any native argument name of the member's method capability also works,
a bare number then takes the unit the contract declares for it):

    mode (TC|IC|TOC|NPOC, CHNS|CHN, AOX|EOX|POX|TX, flame|furnace, analog|histogram|mid, ms1|ms2)   element, line (AAS)
    lines=a,b (ICP-OES / XRF line ids)   masses=63,65 (ICP-MS / RGA MID masses)   elements=O,N,H (O/N/H)   candidates (RGA)
    integration, stabilization, flush, dwell, combustion, feeding, window (durations: 0.4s, 2min)   repeats   dilution
    injection (volume: 500uL)   rf (power: 1300W)   argon / flow / carrier_flow (flow: 11.7L/min)   temp (temperature: 950C)
    voltage, current (X-ray tube: 50kV, 100uA)   energy_max (20keV)   wavelength (324.754nm)   carrier (He|Ar)
    polarity, resolution, from / to (m/z range)   species (IRMS gas)   inlet, carrier=on|off, amplifier (IRMS)
    method (SFA channel method id)

A member's method capability that needs every argument starts from the method chemlab last sent to it (or, the first time, from
the contract example: the documented default method), and the given keys replace values in it; the reply lists the full method
sent.  A method set with `chemlab call` is not known to the class: give every key that matters after one.

Calibration: ``calibrate`` fits a curve from the records of measured standards.  Members whose fit takes record ids get them as
they are (multi X, DMA-80, Orbitrap, RGA with --species, LECO with --elements; LECO names its curves itself).  Members whose fit
takes observations (TOC-L, contrAA, ICPE, ICPMS, EDX, vario EL) get them built by the class from each standard record read with
records.read (or, for the vario EL, from the autosampler slots of the last sequence): the standard's reference id
(--reference) and its level (--levels: the dilution factor, or the reference fraction on the EDX) come from the agent, the
signal from the record.  The IRMS normalises the delta scale from peaks of reference materials (--from PEAK=SUBSTANCE,...); the
SFA recalculates the curve of the last run (--fit).

Class checks before anything is sent: power on; the source lit / instrument ready where the member's mapping requires it; a
sample in place where the member reports it; ranges and enums of every converted value.  Refusals are explained with the
member's codes (blank missing, not calibrated, curve from another method, source still on for a method change, ...).
"""
import json
import re

from ...generic import coerce
from .. import units
from ..base import ClassAdapter, finite, short
from ..errors import invalid, refused
from ..jobs import RUNNING, OperationJob, StateJob
from ..verbs import Arg, Opt, Verb, save_to_opt, target_opt

ON_OFF = ['on', 'off']
REF = 'the instrument manual (page references per member in the class mapping)'
NUM = r'([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)'

def parse_q(text, kind, what=None):
    """Agent text with a unit -> SI float (units.parse; amount and xray_energy are framework kinds)."""
    return units.parse(text, kind, what=what or kind)


def kind_of_declared(u):
    u = units.norm_unit(u) if u else u
    if u == 'J':
        return 'xray_energy'
    k = units.unit_kind(u) if u else None
    if k == 'length':
        return 'wavelength'
    return k


def num_schema(schema):
    if not isinstance(schema, dict):
        return None
    if schema.get('type') in ('number', 'integer'):
        return schema
    for s in schema.get('anyOf') or []:
        if isinstance(s, dict) and s.get('type') in ('number', 'integer'):
            return s
    return None


def fmt_q(si, kind):
    if si is None or not finite(si):
        return 'n/a'
    if kind == 'amount':
        for f, u in ((1.0, 'mol'), (1e-3, 'mmol'), (1e-6, 'µmol'), (1e-9, 'nmol'), (0, 'pmol')):
            if abs(si) >= f or f == 0:
                return '%g %s' % (round(si / (f or 1e-12), 6), u)
    if kind == 'xray_energy':
        return '%g keV' % round(si / 1.602176634e-16, 4)
    if kind == 'wavelength':
        return '%g nm' % round(si * 1e9, 4)
    if kind in units.KINDS:
        return units.fmt(si, kind)
    return '%g' % si


def conc_text(kg_m3):
    """kg/m^3 -> mg/L (or µg/L, ng/L) text."""
    if not finite(kg_m3):
        return 'n/a'
    mg_l = kg_m3 * 1000.0
    if abs(mg_l) >= 1 or mg_l == 0:
        return '%.4g mg/L' % mg_l
    if abs(mg_l) >= 1e-3:
        return '%.4g µg/L' % (mg_l * 1e3)
    return '%.4g ng/L' % (mg_l * 1e6)


def mass_text(kg):
    if not finite(kg):
        return 'n/a'
    for f, u in ((1e-3, 'g'), (1e-6, 'mg'), (1e-9, 'µg'), (1e-12, 'ng'), (0, 'pg')):
        if abs(kg) >= f or f == 0:
            return '%.4g %s' % (kg / (f or 1e-15), u)
    return '%g kg' % kg


def frac_text(x):
    return 'n/a' if not finite(x) else ('%.4g %%' % (x * 100) if abs(x) >= 1e-4 else '%.4g mg/kg' % (x * 1e6))


def split_list(text):
    return [x.strip() for x in str(text or '').split(',') if x.strip()]


BIG = ('assays', 'active_assay', 'points', 'windows', 'scans', 'chromatogram', 'completed_records', 'energy_edges_j', 'counts',
       'trace', 'peak', 'spectrum', 'line_observations', 'channels', 'profile')


def trim(o, depth=0):
    """A result without its bulky arrays (they stay available through `export json --save-to` / `records ID --save-to`)."""
    if isinstance(o, dict):
        return {k: ('<%d items>' % len(v) if isinstance(v, list) else '<object>') if k in BIG and isinstance(v, (list, dict)) and v
                else trim(v, depth + 1) for k, v in o.items()}
    if isinstance(o, list) and depth < 4:
        return [trim(x, depth + 1) for x in o[:200]]
    return o


class QuantAnalyzerAdapter(ClassAdapter):
    COVERS = {'start': 'measure'}                  # the shared start is replaced by measure
    CLASS = 'quant_analyzer'
    TITLE = 'calibrated quantitation'
    ABOUT = ('Analyzers that measure composition against a calibration (CHNS, O/N/H, AOX, TOC, AAS, ICP-OES, ICP-MS, XRF, direct Hg, '
             'RGA, Orbitrap MS, IRMS, segmented-flow): switch the source on, set the method, measure a blank and standards, fit a '
             'curve from the standard records, use it, measure the sample and read the result in plain units.  The operator puts '
             'samples in; the class never does.')
    STANDARDS = ('ISO 8466-1 calibration functions; ISO/IEC 17025 terms (blank, calibration, LOD); EPA 200.7 (ICP-OES), 200.8 / '
                 'USP <233> (ICP-MS), 7473 (direct Hg), USP <643> (TOC), ASTM D5291 (CHN), ASTM E1019 (O/N/H by fusion); '
                 'OPC UA LADS (FunctionalUnit Start/Stop, result); SiLA 2 (observable commands -> job ids)')
    ALWAYS = ('status',)
    UNIT_KINDS = ('duration', 'volume', 'mass', 'power', 'flow', 'temperature', 'voltage', 'current', 'wavelength')
    PATH_GROUPS = (('measure', 'stop', 'result'),)
    CODES = {
        'MATCHED_BLANK_REQUIRED': ('BLANK_REQUIRED', '{inst} has no blank measured with this method ({cap} needs it).',
                                   'have the operator put a blank in and measure it: `chemlab run {inst} measure --wait`; check with `chemlab run {inst} blank`.'),
        'BLANK_REQUIRED': ('BLANK_REQUIRED', '{inst} has no blank measured with this method ({cap} needs it).',
                           'measure a blank first: `chemlab run {inst} measure --wait` with the blank in place.'),
        'CALIBRATION_REQUIRED': ('NOT_CALIBRATED', '{inst} has no calibration curve selected for this method, so it cannot quantify a sample.',
                                 'fit one from standard records and select it: `chemlab run {inst} calibrate ...`, `chemlab run {inst} use-curve ID`.'),
        'SAMPLE_METHOD_REQUIRED': ('NOT_READY', '{inst} has no method set or no sample in place.',
                                   'set the method (`chemlab run {inst} method ...`); the sample is put in by the lab operator.'),
        'PLANT_METHOD_SAMPLE_REQUIRED': ('NOT_READY', '{inst} has no method set or no sample in place.',
                                         'set the method (`chemlab run {inst} method ...`); the sample is put in by the lab operator.'),
        'NO_SAMPLE': ('NO_SAMPLE', 'there is no sample in {inst}.', 'the sample is put in by the lab operator; ask for it, then measure.'),
        'SAMPLE_REQUIRED': ('NO_SAMPLE', 'there is no sample in {inst}.', 'the sample is put in by the lab operator; ask for it, then measure.'),
        'IGNITION_INTERLOCK': ('SOURCE_INTERLOCK', '{inst} cannot light its source now (power, method, vacuum / gas or optics not ready).',
                               'switch power on, set the method, wait until the instrument is ready (`chemlab run {inst} status`), then `source on --wait`.'),
        'MEASUREMENT_INTERLOCK': ('NOT_READY', '{inst} is not ready to measure (source not lit or not yet stable).',
                                  'switch the source on and wait for it: `chemlab run {inst} source on --wait`, then measure.'),
        'INSTRUMENT_NOT_READY': ('NOT_READY', '{inst} is not ready to measure.', 'see `chemlab run {inst} status`; wait until it is ready, then measure.'),
        'NOT_READY': ('NOT_READY', '{inst} is not ready to measure.', 'see `chemlab run {inst} status`; wait until it is ready, then measure.'),
        'FURNACE_NOT_READY': ('NOT_READY', 'the furnace of {inst} is not at its temperature yet.', 'wait (`chemlab run {inst} status` shows ready), then measure.'),
        'VACUUM_OR_POWER': ('NOT_READY', '{inst} is off or its vacuum is not good enough.', 'switch it on and let it pump down; see `chemlab run {inst} status`.'),
        'GAS_POWER_INTERLOCK': ('NOT_READY', '{inst} is off, a cover is open or the carrier gas is not ready.', 'see `chemlab run {inst} status`.'),
        'CURVE_ID_CONFLICT': ('CURVE_EXISTS', 'a curve with this name already exists on {inst}.', 'choose another name (`chemlab run {inst} records` lists the curves).'),
        'CURVE_ID_EXISTS': ('CURVE_EXISTS', 'a curve with this name already exists on {inst}.', 'choose another name (`chemlab run {inst} records` lists the curves).'),
        'CURVE_EXISTS': ('CURVE_EXISTS', 'a curve with this name already exists on {inst}.', 'choose another name (`chemlab run {inst} records` lists the curves).'),
        'UNKNOWN_CURVE': ('UNKNOWN_CURVE', '{inst} has no such curve.', 'see the curves with `chemlab run {inst} records`.'),
        'UNKNOWN_CALIBRATION': ('UNKNOWN_CURVE', '{inst} has no such curve.', 'see the curves with `chemlab run {inst} records`.'),
        'UNKNOWN_RECORD': ('UNKNOWN_RECORD', '{inst} has no such record.', 'list them with `chemlab run {inst} records`.'),
        'CALIBRATION_SIGNATURE_MISMATCH': ('CURVE_MISMATCH', 'the curve or standard records on {inst} were made with another method than the current one.',
                                           'set the method the standards were measured with, or measure the standards again with this method.'),
        'CALIBRATION_MISMATCH': ('CURVE_MISMATCH', 'the curve or standard records on {inst} were made with another method than the current one.',
                                 'set the method the standards were measured with, or measure the standards again with this method.'),
        'CALIBRATION_SIGNATURE': ('CURVE_MISMATCH', 'the curve or standard records on {inst} were made with another method (or are too old).',
                                  'measure the standards again with the current method and fit a new curve.'),
        'CURVE_METHOD_MISMATCH': ('CURVE_MISMATCH', 'the curve on {inst} was made with another method than the current one.',
                                  'fit a curve with the current method, or set the method of the curve.'),
        'OBSERVATION_SIGNATURE_MISMATCH': ('CURVE_MISMATCH', 'the standard records were measured with another method than the current one.',
                                           'measure the standards again with the current method.'),
        'EXTINGUISH_BEFORE_METHOD_CHANGE': ('SOURCE_ON', '{inst} takes a new method only with the source off.',
                                            'switch it off (`chemlab run {inst} source off`), set the method, switch it on again.'),
        'MODE_CHANGE_INTERLOCK': ('SOURCE_ON', '{inst} takes a new method only with the flame off and the atomizer cold.',
                                  'switch the flame off (`chemlab run {inst} source off`), wait until it is cold, then set the method.'),
        'FLAME_OFF_REQUIRED': ('SOURCE_ON', 'the flame of {inst} must be off for this.', '`chemlab run {inst} source off` first.'),
        'BUSY': ('BUSY', '{inst} is busy with a measurement.', 'wait for it (`chemlab run {inst} status`) or stop it (`chemlab run {inst} stop`).'),
        'RUN_ACTIVE': ('BUSY', '{inst} is acquiring.', 'wait until the acquisition ends or stop it (`chemlab run {inst} stop`).'),
        'CALIBRATION_DISTINCT_LEVELS': ('TOO_FEW_LEVELS', 'the standards give fewer than three distinct levels.',
                                        'use at least three standards at different levels (--levels / --from).'),
        'DEGENERATE_CALIBRATION': ('TOO_FEW_LEVELS', 'the standards give fewer than three distinct levels.', 'use at least three standards at different levels.'),
        'CALIBRATION_RANGE': ('TOO_FEW_LEVELS', 'the standard levels do not span a usable range.', 'use at least three standards at clearly different levels.'),
        'CALIBRATION_FIT_INVALID': ('BAD_FIT', 'the standards do not give a valid straight line (slope <= 0 or r² too low).',
                                    'check that --from, --reference and --levels match the standards measured; repeat a bad standard.'),
        'ACTUAL_STANDARD_REQUIRED': ('NOT_A_STANDARD', 'a record given to calibrate is not a measured standard.',
                                     'give only records of standards (`chemlab run {inst} records ID` shows the kind).'),
        'ACTUAL_STANDARD_RECORD_REQUIRED': ('NOT_A_STANDARD', 'a record given to calibrate is not a measured standard.',
                                            'give only records of standards (`chemlab run {inst} records ID` shows the kind).'),
        'INVALID_STANDARD_OBSERVATION': ('NOT_A_STANDARD', 'a record given to calibrate is not a usable standard measurement.',
                                         'give records of complete standard measurements (`chemlab run {inst} records ID`).'),
        'CALIBRATION_REFERENCE_REQUIRED': ('BAD_REFERENCE', 'the reference given is not a calibration reference for the method elements.',
                                           'give the reference id of the standard actually measured (--reference).'),
        'UNKNOWN_REFERENCE': ('BAD_REFERENCE', '{inst} does not know that reference id.', 'give the reference id of the standard (--reference).'),
        'POWER_OFF': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
        'POWER_REQUIRED': ('POWER_OFF', '{inst} is switched off.', 'switch it on: `chemlab run {inst} power on`.'),
    }

    # ------------------------------------------------------------------ verbs
    def build_verbs(self):
        """The verb table, with the options each member's mapping uses (help shows only what this instrument takes)."""
        tgt = {k: target_opt(k, sorted((t.get('values') or {}).keys()) or None) for k, t in (self.map.get('targets') or {}).items()}
        wait = {'wait': Opt('wait until it is done (the job is waited for; lab time moves)', kind='flag'),
                'max': Opt('with --wait: the longest wait (default 1 h)', kind='duration', metavar='TIME')}
        lm, cm, mm = self.vmap('load') or {}, self.vmap('calibrate') or {}, self.vmap('measure') or {}
        ref = self.param('manual') or REF
        um = self.vmap('use-curve') or {}
        # load: the options of the member's loading style
        lopts = {'table': {'slot': Opt('carousel slot of the boat (0..39)', metavar='N'), 'mass': Opt('weighed sample mass, e.g. 50mg', kind='mass', metavar='MASS'),
                           'stats': Opt('include the entry in the statistics', kind='flag')},
                 'sampler': {'slot': Opt('sampler cup RACK:POS, e.g. A:1', metavar='RACK:POS'), 'volume': Opt('cup volume (default 10 mL)', kind='volume', metavar='VOLUME')},
                 'inject': {'amount': Opt('amount of gas injected, e.g. 4umol', metavar='AMOUNT'), 'inlet': Opt('inlet number (default the selected one)', metavar='N'),
                            'species': Opt('analysed gas (default the magnet setting): CO2, N2, ...', metavar='GAS'),
                            'delay': Opt('injection delay after the command', kind='duration', metavar='TIME'),
                            'width': Opt('peak width', kind='duration', metavar='TIME')}}.get(lm.get('style'), {})
        lex = {'table': 'chemlab run INST load soil_1 --slot 5 --mass 50mg', 'sampler': 'chemlab run INST load river_1 --slot A:1 --volume 10mL',
               'inject': 'chemlab run INST load usgs40 --amount 4umol --delay 100s'}.get(lm.get('style'), 'chemlab run INST load NAME')
        # calibrate: the options of the member's fit
        cstyle = cm.get('style', 'records')
        copts = {}
        if cstyle != 'recalculate':
            copts['from'] = Opt('the %s of the standards, comma separated' % cm.get('from_word', 'records'), metavar='LIST')
        if cstyle == 'observations':
            copts['reference'] = Opt('reference id of the standard (one for all, or one per record)', metavar='REF')
            if cm.get('level'):
                copts['levels'] = Opt('the %s of each standard, comma separated' % cm['level'], metavar='LIST')
        if cm.get('channel_arg') or um.get('channel_arg'):
            if 'channel' not in tgt:
                copts['channel'] = Opt('analysis channel of the curve (default: the channel of the current mode)', metavar='CH')
        if cm.get('species_arg'):
            copts['species'] = Opt('the gas of the standard records', metavar='SPECIES')
        if cm.get('elements_arg'):
            copts['elements'] = Opt('elements to calibrate, comma separated (e.g. O,N,H)', metavar='LIST')
        if cstyle == 'recalculate':
            copts.update({'fit': Opt('curve type: linear, quadratic or auto (default auto)', metavar='FIT'),
                          'no-drift': Opt('no drift correction', kind='flag'), 'no-carryover': Opt('no carry-over correction', kind='flag')})
        if cstyle == 'recalculate':
            cex = 'chemlab run INST calibrate --fit auto'
        elif cstyle == 'records':
            cex = 'chemlab run INST calibrate %s--from %s%s%s' % ('' if not cm.get('curve_arg') else 'cal2 ', cm.get('from_example', 'record_2,record_3,record_4'),
                                                                 ' --species nitrogen' if cm.get('species_arg') else '', ' --elements O,N,H' if cm.get('elements_arg') else '')
        elif cstyle == 'normalize':
            cex = 'chemlab run INST calibrate %s --from %s' % (cm.get('curve_example', '13C'), cm.get('from_example', '2=usgs40,3=usgs41'))
        else:
            cex = 'chemlab run INST calibrate %s --from %s --reference %s%s' % (cm.get('curve_example', 'cal2'), cm.get('from_example', 'record_2,record_3,record_4'),
                                                                               cm.get('reference_example', 'STD'),
                                                                               (' --levels %s' % cm.get('levels_example', '100,10,1')) if cm.get('level') else '')
        uopts = {'channel': copts['channel']} if 'channel' in copts else {}
        # measure: the options of the member's run style
        mopts = dict(wait)
        mopts.update({'slots': {'slot': Opt('carousel slot to measure', metavar='N'), 'slots': Opt('several slots as a sequence, comma separated', metavar='LIST')},
                      'sequence': {'slots': Opt('autosampler slots to run, comma separated', metavar='LIST'), 'slot': Opt('one autosampler slot', metavar='N')},
                      'acquisition': {'time': Opt('acquisition time (default 5 min)', kind='duration', metavar='TIME')},
                      'run': {'cups': Opt('the run: RACK:POS:TYPE,... (TYPE baseline|calibrant|sample|drift|...)', metavar='LIST'),
                              'time': Opt('sample time per cup (default 60 s)', kind='duration', metavar='TIME'),
                              'wash': Opt('wash time between cups (default 30 s)', kind='duration', metavar='TIME')}}.get(mm.get('style'), {}))
        mex = {'slots': 'chemlab run INST measure --slot 5 --wait', 'sequence': 'chemlab run INST measure --slots 1,2,3 --wait',
               'acquisition': 'chemlab run INST measure --time 10min', 'run': 'chemlab run INST measure --cups S:22:baseline,S:1:calibrant,A:1:sample --wait'
               }.get(mm.get('style'), 'chemlab run INST measure --wait')
        return {
            'source': Verb('source', 'switch the excitation source on or off (plasma, flame, filament, ion source, carrier gas, lamp)',
                           self.h_source, positionals=[Arg('on|off', 'on or off', choices=ON_OFF)], options=dict(tgt, **wait),
                           preconditions=['power on', 'the member\'s own conditions (vacuum ready, method set, optics in place, ...)'],
                           errors=['POWER_OFF, SOURCE_INTERLOCK, VACUUM_NOT_READY, POSITIONING (refused)', 'NOT_READY (refused: not ready after --max)'],
                           example='chemlab run INST source on --wait', reference=ref),
            'method': Verb('method', 'set the measurement method: KEY=VALUE ... (class keys take units; see `chemlab help INST method`)',
                           self.h_method, options=dict(tgt), variadic=('KEY=VALUE', 'a method setting'),
                           preconditions=['every value converted to the declared unit and inside its range / enum', 'source off where the member needs it'],
                           errors=['UNKNOWN_SETTING, OUT_OF_RANGE, BAD_CHOICE (usage)', 'SOURCE_ON (refused)'], details=self.method_help(),
                           example='chemlab run INST method %s' % self.param('method_example', 'repeats=3'), reference=ref),
            'load': Verb('load', 'enter a sample in the sample table / sampler / inlet (where the agent does it, not the operator)',
                         self.h_load, positionals=[Arg('NAME', 'sample name, item id or reference id')], options=lopts,
                         errors=['MISSING_OPTION, OUT_OF_RANGE, UNKNOWN_LOCATION (usage)'], example=lex, reference=ref),
            'calibrate': Verb('calibrate', 'fit a calibration curve from the records of measured standards', self.h_calibrate,
                              positionals=[Arg('CURVE', 'name of the new %s' % cm.get('curve_word', 'curve'), required=False)], options=copts,
                              preconditions=['every record given exists (read before the fit)', 'one level per record'],
                              errors=['MISSING_OPTION, LEVELS_MISMATCH (usage)', 'BLANK_REQUIRED, TOO_FEW_LEVELS, BAD_FIT, CURVE_EXISTS, CURVE_MISMATCH, NOT_A_STANDARD (refused)'],
                              example=cex, reference=ref),
            'use-curve': Verb('use-curve', 'select the calibration curve used for samples', self.h_use_curve,
                              positionals=[Arg('CURVE', 'curve name%s' % (' (a comma list, one per gas)' if um.get('list') else ''))], options=uopts,
                              errors=['UNKNOWN_CURVE, CURVE_MISMATCH (refused)'], example='chemlab run INST use-curve cal2', reference=ref),
            'blank': Verb('blank', 'the blank value of the current method (measured with a blank in place)', self.h_blank, readonly=True,
                          options={'save-to': save_to_opt()}, example='chemlab run INST blank'),
            'measure': Verb('measure', 'measure what is in place (returns job-N; --wait waits)', self.h_measure, long_action=True, options=mopts,
                            preconditions=['power on', 'ready / source lit where the member needs it', 'a sample in place where the member reports it'],
                            errors=['POWER_OFF, NOT_READY, SOURCE_OFF, NO_SAMPLE (refused, checked first)', 'BLANK_REQUIRED, NOT_CALIBRATED (refused by the instrument)',
                                    'RUN_FAILED (refused: the run ended failed / cancelled)'],
                            example=mex, reference=ref),
            'stop': Verb('stop', 'stop the measurement in progress', self.h_stop, example='chemlab run INST stop'),
            'result': Verb('result', 'the result of the last measurement, in plain units', self.h_result, readonly=True,
                           options={'save-to': save_to_opt()}, example='chemlab run INST result --save-to result.json'),
            'export': Verb('export', 'the instrument\'s own export of the last result (json or csv)', self.h_export, readonly=True,
                           positionals=[Arg('FORMAT', 'json or csv', choices=['json', 'csv'])], options={'save-to': save_to_opt()},
                           example='chemlab run INST export csv --save-to result.json'),
            'records': Verb('records', 'list stored records and curves, or show one record', self.h_records, readonly=True,
                            positionals=[Arg('ID', 'a record id (omit to list)', required=False)], options={'save-to': save_to_opt()},
                            example='chemlab run INST records'),
        }

    def method_help(self):
        keys = ((self.vmap('method') or {}).get('keys')) or {}
        if not keys:
            return None
        bits = []
        for k, spec in keys.items():
            bits.append('%s%s -> %s%s' % (k, '' if not spec.get('kind') else ' (%s)' % spec['kind'].replace('_', ' '), spec['arg'],
                                          ' [%s]' % spec['cap'] if spec.get('cap') else ''))
        txt = 'method keys on this instrument: ' + '; '.join(bits)
        m = self.vmap('method') or {}
        if m.get('passthrough', True) and m.get('cap'):
            txt += '.\nAny other argument of %s by its own name (a bare number takes its declared unit).' % m['cap']
        if m.get('base', 'example') == 'example':
            txt += '\nSettings not given keep the method chemlab last sent (the first time: the documented default, the contract example).'
        return txt

    # ------------------------------------------------------------------ helpers
    def sv(self, values, role):
        spec = self.slot(role)
        return None if spec is None else self.slot_value(values, spec)

    def need_power(self, ctx):
        if self.slot('power'):
            ctx.check(bool(self.sv(ctx, 'power')), 'POWER_OFF', '%s is switched off.' % self.instrument_id,
                      'switch it on: `chemlab run %s power on`.' % self.instrument_id, label='power on')

    def need_idle(self, ctx):
        ph = self.sv(ctx, 'phase')
        if ph is not None:
            ctx.check(ph not in self.param('running', ['running', 'draining']), 'BUSY', '%s is measuring (%s).' % (self.instrument_id, ph),
                      'wait for it (`chemlab run %s status`) or stop it (`chemlab run %s stop`).' % (self.instrument_id, self.instrument_id),
                      label='not measuring')

    def fail_usage(self, ctx, code, msg, fix):
        ctx.fail(invalid(code, msg, fix))

    def parse_value(self, ctx, text, schema, kind, what):
        """One KEY=VALUE value -> the native value (units converted to the declared unit; range / enum checked here)."""
        try:
            return self._parse_value(text, schema, kind, what)
        except Exception as exc:                                             # noqa: BLE001 - usage errors with the steps done
            if hasattr(exc, 'kind'):
                ctx.fail(exc)
            raise

    def _parse_value(self, text, schema, kind, what):
        ns = num_schema(schema)
        if ns is not None:
            if text.strip().lower() in ('null', 'none') and schema.get('anyOf'):
                return None
            u = units.declared(ns)
            dkind = kind_of_declared(u) if u else None
            t = text.strip().replace(' ', '')
            m = re.fullmatch(NUM + r'(.*)', t)
            if not m:
                raise invalid('BAD_NUMBER', '%s=%s is not a number.' % (what, text), 'write e.g. %s=1%s.' % (what, '' if not (kind or dkind) else ' with a unit'))
            if kind or (m.group(2) and dkind):
                k = kind or dkind
                si = parse_q(t, k, what=what)
                value = si if (u is None or u == 'J' or units.unit_kind(u) is None) else units.to_declared(si, u)
            elif m.group(2):
                raise invalid('BAD_NUMBER', '%s=%s: this setting takes a plain number.' % (what, text), 'leave out the unit.')
            else:
                value = float(m.group(1))
            if ns.get('type') == 'integer':
                if abs(value - round(value)) > 1e-9:
                    raise invalid('NOT_WHOLE', '%s=%s must be a whole number.' % (what, text), 'give an integer.')
                value = int(round(value))
            if ns.get('enum'):
                hit = [e for e in ns['enum'] if e == value or abs(e - value) <= 1e-9 * max(1.0, abs(e))]
                if not hit:
                    raise invalid('BAD_CHOICE', '%s=%s is not available on %s.' % (what, text, self.instrument_id),
                                  'choose one of: %s.' % ', '.join(map(str, ns['enum'])))
                value = hit[0]
            lo, hi = ns.get('minimum'), ns.get('maximum')
            if (lo is not None and value < lo - 1e-9 * max(1.0, abs(lo))) or (hi is not None and value > hi + 1e-9 * max(1.0, abs(hi))):
                k2 = kind or dkind
                show = (lambda x: fmt_q(units.from_declared(x, u) if u and k2 in units.KINDS else x, k2)) if k2 else (lambda x: '%g' % x)
                rng = '%s to %s' % (show(lo) if lo is not None else '-', show(hi) if hi is not None else '-')
                raise invalid('OUT_OF_RANGE', '%s=%s is outside the range of %s (%s).' % (what, text, self.instrument_id, rng),
                              'give %s from %s.' % (what, rng))
            return value
        types = {schema.get('type')} if schema.get('type') else {x.get('type') for x in schema.get('anyOf') or []}
        if types == {'boolean'}:
            low = text.strip().lower()
            if low in ('yes', 'true', 'on', '1'):
                return True
            if low in ('no', 'false', 'off', '0'):
                return False
            raise invalid('BAD_CHOICE', '%s=%s is not on or off.' % (what, text), 'write %s=on or %s=off.' % (what, what))
        if schema.get('enum'):
            for e in schema['enum']:
                if str(e).lower() == text.strip().lower():
                    return e
            raise invalid('BAD_CHOICE', '%s=%s is not allowed on %s.' % (what, text, self.instrument_id),
                          'choose one of: %s.' % ', '.join(map(str, schema['enum'])))
        if 'array' in types and not text.strip().startswith('['):
            items = schema.get('items') if schema.get('type') == 'array' else next((x.get('items') for x in schema.get('anyOf') or [] if x.get('type') == 'array'), {})
            return [self._parse_value(x, items or {}, kind, what) for x in split_list(text)]
        return coerce(text, schema, what)

    def cap_props(self, cap):
        return ((self.lab.contract['capabilities'].get(cap) or {}).get('input_schema') or {}).get('properties') or {}

    def example_args(self, cap):
        return json.loads(json.dumps(self.lab.contract['capabilities'][cap].get('example_arguments') or {}))

    # ------------------------------------------------------------------ summary
    def summary(self, values):
        def g(role):
            return self.sv(values, role)
        lines, js = [], {}
        p = g('power')
        if self.slot('power'):
            js['power'] = bool(p)
            if not p:
                return ['power off'], js
        bits = []
        if self.slot('source'):
            s = g('source')
            js['source_on'] = bool(s)
            bits.append('%s %s' % (self.param('source_word', 'source'), 'on' if s else 'off'))
        if self.slot('ready'):
            r = g('ready')
            js['ready'] = bool(r)
            bits.append('ready' if r else 'not ready')
        ph = g('phase')
        if ph is not None:
            js['phase'] = ph
            bits.append('measurement %s' % ph)
        if bits:
            lines.append(', '.join(bits))
        more = []
        if self.slot('method'):
            js['method'] = g('method')
            more.append('method %s' % short(js['method'], 40))
        if self.slot('blank'):
            js['blank_valid'] = bool(g('blank'))
            more.append('blank %s' % ('valid' if js['blank_valid'] else 'not measured for this method'))
        if self.slot('calibrated'):
            js['calibrated'] = bool(g('calibrated'))
            more.append('calibration %s' % ('selected' if js['calibrated'] else 'none'))
        if self.slot('sample'):
            js['sample_in_place'] = bool(g('sample'))
            more.append('sample %s' % ('in place' if js['sample_in_place'] else 'none'))
        if more:
            lines.append('; '.join(more))
        res = []
        for name, spec in (self.map.get('result_slots') or {}).items():
            v = self.slot_value(values, spec)
            js[name] = v
            if v is not None:
                res.append('%s %s' % (name, self.show_value(v, spec.get('show') if isinstance(spec, dict) else None)))
        if res:
            lines.append('last result: ' + ', '.join(res))
        warn = g('warning')
        if warn:
            js['warning'] = warn
            lines.append('warning: %s' % short(warn, 80))
        return lines or ['(see `chemlab read %s`)' % self.instrument_id], js

    def slot_value(self, values, spec):
        """A slot value: 'slot', 'slot#key.sub' or {'slot', 'key'} (a key path into an object slot)."""
        if isinstance(spec, dict):
            sid, key = spec['slot'], spec.get('key') or ''
            if '#' in sid:
                sid, _, key = sid.partition('#')
        else:
            sid, _, key = spec.partition('#')
        get = values.v if hasattr(values, 'v') else values.get
        v = get(sid)
        for k in [x for x in key.split('.') if x] if key else []:
            v = v.get(k) if isinstance(v, dict) else None
        return v

    @staticmethod
    def show_value(v, how):
        if how == 'conc':
            return conc_text(v)
        if how == 'mass':
            return mass_text(v)
        if how == 'fraction':
            return frac_text(v)
        if how == 'pressure':
            return '%.4g Pa' % v if finite(v) else 'n/a'
        return short(v, 40)

    # ------------------------------------------------------------------ source
    def h_source(self, ctx, p):
        m = self.vmap('source')
        want = p.pos[0] == 'on'
        inst = self.instrument_id
        self.need_power(ctx)
        if want:
            self.requirements(ctx, 'source')
        else:
            self.requirements(ctx, 'source-off')
        tg = self.target_args(ctx, p)
        cur = self.sv(ctx, 'source') if self.slot('source') and not tg else None
        if cur is not None and bool(cur) == want:
            text = 'ok: the %s of %s is already %s\n' % (self.param('source_word', 'source'), inst, p.pos[0])
            if want and p.get('wait') and not ctx.dry_run:
                text += self.wait_source(ctx, p)
            return {'text': text, 'json': {'source': want, 'changed': False}}
        if want:
            self.wait_positioned(ctx, p)
        if m.get('on_cap'):
            cap, args = (m['on_cap'] if want else m['off_cap']), dict(m.get('args') or {})
        else:
            cap, args = m['cap'], dict(m.get('args') or {})
            args[m['arg']] = want
        args.update(tg)
        ctx.call(cap, args, '%s %s' % (self.param('source_word', 'source'), p.pos[0]))
        text = 'ok: %s %s %s\n' % (inst, self.param('source_word', 'source'), p.pos[0])
        js = {'source': want, 'changed': True}
        if want and p.get('wait') and not ctx.dry_run:
            text += self.wait_source(ctx, p)
            js['ready'] = True
        elif want and self.param('source_ready') and not ctx.dry_run:
            text += 'it takes a moment: `chemlab run %s source on --wait` or `chemlab run %s status`.\n' % (inst, inst)
        return {'text': text, 'json': js}

    def wait_positioned(self, ctx, p):
        """Optics / burner that move by themselves after a method change (ICPE view mirror, contrAA burner) must be in place before
        the source can be lit: with --wait the class waits for them (a few seconds), without it refuses with the reason."""
        spec = self.param('positioned')
        if not spec:
            return
        target = (spec.get('targets') or {}).get(self.slot_value(ctx, spec['by']))
        if target is None:
            return

        def there(view):
            v = self.slot_value(ctx, spec['slot'])
            return finite(v) and abs(v - target) <= float(spec.get('tol', 1e-9))
        if there(ctx.view):
            ctx.checks.append('%s in place' % spec['what'])
            return
        if p.get('wait') and not ctx.dry_run:
            ctx.advance(float(spec.get('max_s', 30.0)), until=there, step='wait until the %s is in place' % spec['what'], poll_s=0.5)
        ctx.check(there(ctx.view), 'POSITIONING', 'the %s of %s is still moving into place after the method change.' % (spec['what'], self.instrument_id),
                  'wait a few seconds, or use `chemlab run %s source on --wait` (it waits for it).' % self.instrument_id, label='%s in place' % spec['what'])

    def wait_source(self, ctx, p):
        spec = self.param('source_ready')
        if not spec:
            return ''
        mx = p.get('max') or float(self.param('source_wait_s', 600.0))

        def ok(view):
            v = self.slot_value(ctx, spec)
            return v == spec['value'] if 'value' in spec else bool(v)
        t0 = ctx.now()
        if not ok(ctx.view):
            ctx.advance(mx, until=ok, step='wait until %s is ready' % self.param('source_word', 'source'), poll_s=float(self.param('poll_s', 1.0)))
        if not ok(ctx.view):
            ctx.fail(refused('NOT_READY', 'the %s of %s is not ready after %s (%s = %s).' % (
                self.param('source_word', 'source'), self.instrument_id, units.fmt_s(ctx.now() - t0), spec['slot'], short(self.slot_value(ctx, spec), 40)),
                'wait longer (`--max 30min`) or see `chemlab run %s status`.' % self.instrument_id))
        settle = float(self.param('source_settle_s', 0.0))
        if settle:
            ctx.advance(settle, step='let it stabilise')
        return 'ready after %s of waiting (t = %.1f s)\n' % (units.fmt_s(ctx.now() - t0), ctx.now())

    # ------------------------------------------------------------------ method
    def method_keys(self):
        return (self.vmap('method') or {}).get('keys') or {}

    def h_method(self, ctx, p):
        m = self.vmap('method')
        inst = self.instrument_id
        if not p.extra:
            ctx.fail(invalid('MISSING_SETTING', 'method needs at least one KEY=VALUE.', 'see `chemlab help %s method`.' % inst))
        keys = self.method_keys()
        default_cap = m.get('cap')
        per_cap = {}                                                        # cap -> {native: value}
        given = []
        for item in p.extra:
            k, eq, v = item.partition('=')
            if not eq or not k:
                ctx.fail(invalid('BAD_SETTING', '%r is not KEY=VALUE.' % item, 'write KEY=VALUE (see `chemlab help %s method`).' % inst))
            if k in keys:
                spec = keys[k]
                cap, native, kind = spec.get('cap') or default_cap, spec['arg'], spec.get('kind')
            elif m.get('passthrough', True) and default_cap and k in self.cap_props(default_cap):
                cap, native, kind = default_cap, k, None
            else:
                names = list(keys) + ([x for x in self.cap_props(default_cap) if x not in {s['arg'] for s in keys.values()}] if default_cap and m.get('passthrough', True) else [])
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (inst, k), 'settings: %s.' % ', '.join(names)))
            props = self.cap_props(cap)
            if native not in props:
                ctx.fail(invalid('UNKNOWN_SETTING', '%s has no method setting %r.' % (inst, k), 'see `chemlab help %s method`.' % inst))
            out = per_cap.setdefault(cap, {})
            if native in out:
                ctx.fail(invalid('REPEATED_SETTING', '%s is given twice.' % k, 'give it once.'))
            out[native] = self.parse_value(ctx, v, props[native], kind, k)
            given.append(k)
        if self.param('method_needs_source_off') and self.slot('source') and self.sv(ctx, 'source'):
            ctx.check(False, 'SOURCE_ON', '%s takes a new method only with the %s off.' % (inst, self.param('source_word', 'source')),
                      'switch it off (`chemlab run %s source off`), set the method, then switch it on again.' % inst)
        self.need_idle(ctx)
        self.requirements(ctx, 'method')
        tg = self.target_args(ctx, p)
        sent = {}
        order = [default_cap] + [c for c in per_cap if c != default_cap] if default_cap in per_cap else list(per_cap)
        for cap in order:
            vals = per_cap[cap]
            memory = getattr(self, '_last_method', None)
            if memory is None:
                memory = self._last_method = {}
            from_memory = cap == default_cap and m.get('base', 'example') == 'example' and cap in memory
            if from_memory:
                base = json.loads(json.dumps(memory[cap]))
            else:
                base = self.example_args(cap) if (cap == default_cap and m.get('base', 'example') == 'example') else {}
            base.update(m.get('args') or {} if cap == default_cap else {})
            base.update(vals)
            if cap == default_cap:
                base.update(tg)
            ctx.call(cap, base, 'set the method (%s)' % ', '.join('%s=%s' % (a, short(x, 30)) for a, x in vals.items()))
            sent[cap] = base
            if not ctx.dry_run and cap == default_cap and m.get('base', 'example') == 'example':
                memory[cap] = json.loads(json.dumps(base))
            notes_from = 'the method chemlab last sent' if from_memory else 'the documented default method (the contract example)'
        text = 'ok: method of %s set (%s)\n' % (inst, ', '.join(given))
        for cap, a in sent.items():
            text += '  %s: %s\n' % (cap, short(a, 400))
        if default_cap in sent and m.get('base', 'example') == 'example':
            text += 'settings not given keep %s.\n' % notes_from
        return {'text': text, 'json': {'calls': sent, 'given': given}}

    # ------------------------------------------------------------------ load
    def h_load(self, ctx, p):
        m = self.vmap('load')
        inst = self.instrument_id
        name = p.pos[0]
        style = m.get('style')
        self.need_idle(ctx)
        self.requirements(ctx, 'load')
        cap = m['cap']
        if style == 'table':                                                 # DMA-80 sample table: slot, name, weighed amount
            if p.get('slot') is None:
                ctx.fail(invalid('MISSING_OPTION', 'load on %s needs --slot N (the carousel slot of the boat).' % inst, 'add --slot N.'))
            if p.get('mass') is None:
                ctx.fail(invalid('MISSING_OPTION', 'load on %s needs --mass (the weighed sample mass).' % inst, 'add e.g. --mass 50mg.'))
            props = self.cap_props(cap)
            slot = self.parse_value(ctx, p.get('slot'), props['slot_index'], None, '--slot')
            lo, hi = self.arg_range_si(cap, 'amount_kg')
            mass = p.get('mass')
            if (lo is not None and mass < lo) or (hi is not None and mass > hi):
                ctx.fail(invalid('OUT_OF_RANGE', '--mass %s is outside %s..%s on %s.' % (mass_text(mass), mass_text(lo), mass_text(hi), inst),
                                 'give a mass from %s to %s.' % (mass_text(lo), mass_text(hi))))
            args = {'slot_index': slot, 'name': name, 'amount_kg': self.arg_value(cap, 'amount_kg', mass), 'in_statistics': bool(p.get('stats'))}
            ctx.call(cap, args, 'enter %s in slot %d (%s)' % (name, slot, mass_text(mass)))
            return {'text': 'ok: %s slot %d = %s, %s\n' % (inst, slot, name, mass_text(mass)), 'json': {'slot': slot, 'name': name, 'mass_kg': mass}}
        if style == 'sampler':                                               # SFA sampler: RACK:POS, item, volume
            if not p.get('slot'):
                ctx.fail(invalid('MISSING_OPTION', 'load on %s needs --slot RACK:POS (e.g. A:1, S:22).' % inst, 'add --slot A:1.'))
            rack, pos = self.rack_pos(ctx, p.get('slot'), cap)
            vol = p.get('volume') if p.get('volume') is not None else float(self.param('cup_volume_m3', 1e-5))
            lo, hi = self.arg_range_si(cap, 'volume_m3')
            if (lo is not None and vol < lo) or (hi is not None and vol > hi):
                ctx.fail(invalid('OUT_OF_RANGE', '--volume %s is outside %s..%s.' % (units.fmt(vol, 'volume'), units.fmt(lo, 'volume'), units.fmt(hi, 'volume')),
                                 'give a cup volume from %s to %s.' % (units.fmt(lo, 'volume'), units.fmt(hi, 'volume'))))
            args = {'rack': rack, 'position': pos, 'item_id': name, 'volume_m3': self.arg_value(cap, 'volume_m3', vol)}
            ctx.call(cap, args, 'put %s in cup %s:%d (%s)' % (name, rack, pos, units.fmt(vol, 'volume')))
            return {'text': 'ok: %s cup %s:%d = %s, %s\n' % (inst, rack, pos, name, units.fmt(vol, 'volume')),
                    'json': {'rack': rack, 'position': pos, 'item': name, 'volume_m3': vol}}
        if style == 'inject':                                                # IRMS: inject a sample / reference material
            props = self.cap_props(cap)
            if p.get('amount') is None:
                ctx.fail(invalid('MISSING_OPTION', 'load on %s needs --amount (the amount of gas injected, e.g. 4umol).' % inst, 'add --amount 4umol.'))
            amount = self.parse_value(ctx, p.get('amount'), props['amount_mol'], 'amount', '--amount')
            inlet = p.get('inlet') or self.slot_value(ctx, self.slot('inlet')) if self.slot('inlet') else p.get('inlet')
            if inlet is None:
                ctx.fail(invalid('MISSING_OPTION', 'load on %s needs --inlet N.' % inst, 'add --inlet 1.'))
            inlet = self.parse_value(ctx, str(inlet), props['inlet'], None, '--inlet')
            species = p.get('species') or (self.slot_value(ctx, self.slot('species')) if self.slot('species') else None)
            if not species:
                ctx.fail(invalid('MISSING_OPTION', 'load on %s needs --species (the magnet is not set to a gas yet).' % inst,
                                 'add --species CO2, or set it first: `chemlab run %s method species=CO2`.' % inst))
            species = self.parse_value(ctx, str(species), props['species'], None, '--species')
            args = {'inlet': inlet, 'species': species, 'amount_mol': amount}
            known = self.arg_enum(cap, 'substance_id')
            if name in known:
                args['substance_id'] = name
            else:
                args['sample_id'] = name
            if p.get('delay') is not None:
                args['delay_s'] = self.parse_value(ctx, '%rs' % p.get('delay'), props['delay_s'], 'duration', '--delay')
            if p.get('width') is not None:
                args['width_s'] = self.parse_value(ctx, '%rs' % p.get('width'), props['width_s'], 'duration', '--width')
            r = ctx.call(cap, args, 'inject %s (%s, %s) at inlet %d' % (name, species, fmt_q(amount, 'amount'), inlet))
            res = (r.get('result') or {}) if not ctx.dry_run else {}
            text = 'ok: %s injected %s (%s of %s) at inlet %d' % (inst, name, fmt_q(amount, 'amount'), species, inlet)
            if res.get('arrival_s') is not None:
                text += '; the peak reaches the source at t = %.0f s (an acquisition must be running then)' % res['arrival_s']
            return {'text': text + '\n', 'json': {'injection': res.get('injection'), 'arrival_s': res.get('arrival_s'), 'arguments': args}}
        ctx.fail(invalid('NOT_SUPPORTED', 'load is not mapped on %s.' % inst, 'see `chemlab run %s`.' % inst))

    def rack_pos(self, ctx, text, cap):
        t = str(text).strip().upper().replace(' ', '')
        mm = re.fullmatch(r'([A-Z]+):?(\d+)', t)
        racks = self.arg_enum(cap, 'rack')
        if not mm or (racks and mm.group(1) not in racks):
            ctx.fail(invalid('UNKNOWN_LOCATION', '%r is not a sampler position of %s.' % (text, self.instrument_id),
                             'write RACK:POS with rack %s, e.g. A:1.' % '|'.join(racks or ['A'])))
        pos = int(mm.group(2))
        lo, hi = self.arg_range(cap, 'position')
        if (lo is not None and pos < lo) or (hi is not None and pos > hi):
            ctx.fail(invalid('OUT_OF_RANGE', 'position %d is outside %s..%s on rack %s.' % (pos, lo, hi, mm.group(1)), 'give a position from %s to %s.' % (lo, hi)))
        return mm.group(1), pos

    # ------------------------------------------------------------------ calibrate
    def h_calibrate(self, ctx, p):
        m = self.vmap('calibrate')
        inst = self.instrument_id
        style = m.get('style', 'records')
        curve = p.pos[0] if p.pos else None
        src = split_list(p.get('from'))
        self.need_idle(ctx)
        self.requirements(ctx, 'calibrate')
        cap = m['cap']
        props = self.cap_props(cap)
        args = dict(m.get('args') or {})
        if m.get('curve_arg'):
            if not curve:
                ctx.fail(invalid('MISSING_ARGUMENT', 'calibrate on %s needs the name of the new %s.' % (inst, m.get('curve_word', 'curve')),
                                 'e.g. `chemlab run %s calibrate %s --from ...`.' % (inst, m.get('curve_example', 'cal1'))))
            args[m['curve_arg']] = self.parse_value(ctx, curve, props[m['curve_arg']], None, m.get('curve_word', 'CURVE'))
        elif curve and style != 'recalculate':
            ctx.fail(invalid('CURVE_NAMED_BY_INSTRUMENT', '%s names its curves itself; leave out %r.' % (inst, curve),
                             '`chemlab run %s calibrate --from ...`; the reply gives the name of the new curve.' % inst))
        if style != 'recalculate' and not src:
            ctx.fail(invalid('MISSING_OPTION', 'calibrate needs --from: the %s of the standards.' % m.get('from_word', 'records'),
                             'list them with `chemlab run %s records`; e.g. --from %s.' % (inst, m.get('from_example', 'record_2,record_3,record_4'))))
        if style == 'records':
            args[m['records_arg']] = src
            if m.get('species_arg'):
                if not p.get('species'):
                    ctx.fail(invalid('MISSING_OPTION', 'calibrate on %s needs --species (the gas of the standards).' % inst, 'add e.g. --species nitrogen.'))
                args[m['species_arg']] = p.get('species')
            if m.get('elements_arg'):
                el = split_list(p.get('elements')) or list(self.param('default_elements', []))
                if not el:
                    ctx.fail(invalid('MISSING_OPTION', 'calibrate on %s needs --elements.' % inst, 'add e.g. --elements O,N,H.'))
                args[m['elements_arg']] = el
            r = ctx.call(cap, args, 'fit a curve from %s' % ', '.join(src))
            return self.fit_reply(ctx, r, args)
        if style == 'normalize':                                             # IRMS: delta scale from reference-material peaks
            stds = []
            for item in src:
                pk, eq, sub = item.partition('=')
                if not eq or not pk.strip().isdigit():
                    ctx.fail(invalid('BAD_SETTING', '%r is not PEAK=SUBSTANCE.' % item, 'write --from 2=usgs40,3=usgs41 (peaks from `chemlab run %s result`).' % inst))
                stds.append({'peak': int(pk), 'substance_id': sub.strip()})
            args[m['standards_arg']] = stds
            r = ctx.call(cap, args, 'normalise %s from %d reference peaks' % (curve, len(stds)))
            return self.fit_reply(ctx, r, args)
        if style == 'recalculate':                                           # SFA: refit the calibrants of the last run
            fit = p.get('fit') or self.param('default_fit', 'auto')
            args.update({'fit': self.parse_value(ctx, fit, props['fit'], None, '--fit'), 'carryover_correction': not p.get('no-carryover'),
                         'drift_correction': not p.get('no-drift')})
            r = ctx.call(cap, args, 'recalculate the calibration of the last run (%s fit)' % fit)
            return self.fit_reply(ctx, r, args)
        # observations built from records (or vario EL slots)
        refs = split_list(p.get('reference'))
        if not refs:
            ctx.fail(invalid('MISSING_OPTION', 'calibrate on %s needs --reference: the reference id of the standard(s).' % inst,
                             'e.g. --reference %s (one for all, or one per record).' % m.get('reference_example', 'STD')))
        if len(refs) not in (1, len(src)):
            ctx.fail(invalid('LEVELS_MISMATCH', '--reference gives %d ids for %d standards.' % (len(refs), len(src)), 'give one id, or one per record.'))
        refs = refs * len(src) if len(refs) == 1 else refs
        level_word = m.get('level')
        levels = [None] * len(src)
        if level_word:
            lv = split_list(p.get('levels'))
            if not lv:
                ctx.fail(invalid('MISSING_OPTION', 'calibrate on %s needs --levels: the %s of each standard.' % (inst, level_word),
                                 'e.g. --levels %s.' % m.get('levels_example', '100,10,1')))
            if len(lv) not in (1, len(src)):
                ctx.fail(invalid('LEVELS_MISMATCH', '--levels gives %d values for %d standards.' % (len(lv), len(src)), 'give one level per record.'))
            try:
                levels = [float(x) for x in (lv * len(src) if len(lv) == 1 else lv)]
            except ValueError:
                ctx.fail(invalid('BAD_NUMBER', '--levels %s is not a list of numbers.' % p.get('levels'), 'e.g. --levels 100,10,1.'))
        channel = None
        if m.get('channel_arg'):
            channel = p.get('channel') or self.method_channel(ctx)
            if not channel:
                ctx.fail(invalid('MISSING_OPTION', 'calibrate on %s needs --channel.' % inst, 'add --channel TC (or IC, NPOC).'))
            args[m['channel_arg']] = self.parse_value(ctx, channel, props[m['channel_arg']], None, '--channel')
            channel = args[m['channel_arg']]
        if ctx.dry_run:
            ctx.plan.append({'capability': m.get('read_cap', 'records.read'), 'arguments': {'from': src}, 'step': 'read the %d standard records' % len(src)})
            ctx.plan.append({'capability': cap, 'arguments': dict(args, observations='<built from the records when it runs>'), 'step': 'fit the curve'})
            ctx.unchecked.append('the records exist and are standards measured with the current method')
            return {'text': '', 'json': {}, 'unchecked': ['the records exist and are standards measured with the current method']}
        obs = self.observations(ctx, m, src, refs, levels, channel)
        if not obs:
            ctx.fail(refused('NO_OBSERVATIONS', 'the records %s give no standard signal to fit.' % ', '.join(src),
                             'check them with `chemlab run %s records ID`.' % inst))
        args[m['observations_arg']] = obs
        r = ctx.call(cap, args, 'fit the curve from %d observations' % len(obs))
        return self.fit_reply(ctx, r, args, n_obs=len(obs))

    def method_channel(self, ctx):
        mode = self.sv(ctx, 'method')
        ch = (self.param('channel_of_mode') or {}).get(mode)
        return ch

    def observations(self, ctx, m, src, refs, levels, channel):
        """Fit observations from the standard records, in the shape of the member's fit capability."""
        obs_style = m['observation']
        out = []
        if obs_style == 'vario_areas':                                       # slots of the last sequence (result.read)
            r = ctx.call(m.get('read_cap', 'result.read'), {}, 'read the last sequence', query=True)
            assays = {str(a.get('slot_index')): a for a in (r.get('result') or {}).get('completed_assays') or []}
            el = ctx.args.pos[0] if ctx.args.pos else None
            for s, ref in zip(src, refs):
                a = assays.get(s)
                if a is None:
                    ctx.fail(refused('UNKNOWN_RECORD', 'slot %s was not measured in the last sequence of %s.' % (s, self.instrument_id),
                                     'measure the standards first (`chemlab run %s measure --slots 1,2,3 --wait`); slots measured: %s.' % (
                                         self.instrument_id, ', '.join(sorted(assays)) or 'none')))
                area = (a.get('areas_s') or {}).get(el)
                out.append({'reference_id': ref, 'sample_mass_kg': a.get('mass_kg'), 'peak_area_s': area})
            return out
        read_cap = m.get('read_cap', 'records.read')
        for rid, ref, lv in zip(src, refs, levels):
            r = ctx.call(read_cap, {'record_id': rid}, 'read record %s' % rid, query=True)
            rec = r.get('result') or {}
            if obs_style == 'line_integrals':
                out.append({'reference_id': ref, 'dilution_factor': lv,
                            'line_integrals': [{'line_id': c['line_id'], 'integrated_intensity_s': c['mean_integrated_intensity_s']}
                                               for c in rec.get('concentrations') or [] if finite(c.get('mean_integrated_intensity_s'))]})
            elif obs_style == 'mass_channels':
                for a in rec.get('assays') or []:
                    out.append({'reference_id': ref, 'dilution_factor': lv, 'channels': a.get('channels') or []})
            elif obs_style == 'toc_peaks':
                for a in rec.get('assays') or []:
                    if channel and a.get('channel') != channel:
                        continue
                    out.append({'reference_id': ref, 'reference_channel': a.get('channel'), 'injection_volume_m3': a.get('injection_volume_m3'),
                                'dilution_factor': lv, 'peak_area_s': a.get('peak_area_s')})
            elif obs_style == 'aa_absorbance':
                for a in rec.get('assays') or []:
                    out.append({'reference_id': ref, 'dilution_factor': lv, 'injection_volume_m3': a.get('injection_volume_m3'),
                                'integrated_absorbance_s': a.get('integrated_absorbance_s')})
            elif obs_style == 'xrf_lines':
                for a in rec.get('assays') or []:
                    out.append({'acquisition_signature': rec.get('acquisition_signature'), 'reference_id': ref, 'reference_fraction': lv,
                                'line_observations': [{k: x.get(k) for k in ('line_id', 'net_rate_s_inv', 'roi_low_j', 'roi_high_j')}
                                                      for x in a.get('line_observations') or []]})
        return out

    def fit_reply(self, ctx, r, args, n_obs=None):
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') or {}
        name = res.get('curve_id') or res.get('isotope') or args.get('element') or res.get('run_id')
        r2 = res.get('r_squared') or res.get('minimum_r_squared')
        text = 'ok: %s calibrated: %s' % (self.instrument_id, name)
        if finite(r2):
            text += ', r² = %.6f' % r2
        if n_obs:
            text += ' (%d observations)' % n_obs
        text += '\n'
        if self.supported('use-curve') and res.get('curve_id'):
            text += 'use it for samples: `chemlab run %s use-curve %s`\n' % (self.instrument_id, res['curve_id'])
        return {'text': text, 'json': {'curve': name, 'r_squared': r2, 'fit': trim(res)}}

    # ------------------------------------------------------------------ use-curve
    def h_use_curve(self, ctx, p):
        m = self.vmap('use-curve')
        inst = self.instrument_id
        cap = m['cap']
        props = self.cap_props(cap)
        self.need_idle(ctx)
        args = dict(m.get('args') or {})
        ids = split_list(p.pos[0])
        if m.get('list'):
            args[m['arg']] = ids
        else:
            if len(ids) != 1:
                ctx.fail(invalid('ONE_CURVE', '%s uses one curve at a time.' % inst, 'give one curve name.'))
            args[m['arg']] = ids[0]
        if m.get('channel_arg'):
            ch = p.get('channel')
            if not ch and not ctx.dry_run:                                   # the channel the curve was fitted for
                r = ctx.call(m.get('read_cap', 'calibration.read'), {'curve_id': ids[0]}, 'read curve %s' % ids[0], query=True)
                try:
                    ch = json.loads((r.get('result') or {}).get('curve_json') or '{}').get('channel')
                except ValueError:
                    ch = None
            if not ch:
                ch = self.method_channel(ctx)
            if not ch:
                ctx.fail(invalid('MISSING_OPTION', 'use-curve on %s needs --channel.' % inst, 'add --channel TC (or IC, NPOC).'))
            args[m['channel_arg']] = self.parse_value(ctx, ch, props[m['channel_arg']], None, '--channel')
        ctx.call(cap, args, 'use curve %s' % ', '.join(ids))
        return {'text': 'ok: %s uses %s%s\n' % (inst, ', '.join(ids), (' for %s' % args[m['channel_arg']]) if m.get('channel_arg') else ''),
                'json': {'curves': ids}}

    # ------------------------------------------------------------------ blank
    def h_blank(self, ctx, p):
        m = self.vmap('blank')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the blank', query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') or {}
        valid = res.get('valid')
        if valid is None and 'valid_channels' in res:
            valid = bool(res['valid_channels'])
        blank = res.get('blank_json')
        try:
            bj = json.loads(blank) if isinstance(blank, str) else blank
        except ValueError:
            bj = blank
        text = '%s blank: %s\n' % (self.instrument_id, 'valid for the current method' if valid else 'none measured for the current method')
        if res.get('valid_channels') is not None:
            text += '  valid channels: %s\n' % (', '.join(res['valid_channels']) or 'none')
        if bj not in (None, {}, 'null'):
            text += '  value: %s\n' % short(bj, 300)
        return {'text': text, 'json': {'valid': valid, 'blank': bj, 'raw': trim(res)}}

    # ------------------------------------------------------------------ measure / stop
    def h_measure(self, ctx, p):
        m = self.vmap('measure')
        inst = self.instrument_id
        self.need_power(ctx)
        self.need_idle(ctx)
        self.requirements(ctx, 'measure')
        cap = m['cap']
        args = dict(m.get('args') or {})
        style = m.get('style')
        pre = []
        what = 'measurement'
        if style == 'slots':                                                 # DMA-80: one slot or a sequence
            if p.get('slots'):
                cap = m['slots_cap']
                props = self.cap_props(cap)
                args[m['slots_arg']] = self.parse_value(ctx, p.get('slots'), props[m['slots_arg']], None, '--slots')
                what = 'sequence of slots %s' % p.get('slots')
            else:
                if p.get('slot') is None:
                    ctx.fail(invalid('MISSING_OPTION', 'measure on %s needs --slot N (or --slots a,b,c).' % inst, 'add --slot N.'))
                props = self.cap_props(cap)
                args[m['slot_arg']] = self.parse_value(ctx, p.get('slot'), props[m['slot_arg']], None, '--slot')
                what = 'slot %s' % p.get('slot')
        elif style == 'sequence':                                            # vario EL: configure the sequence, then start it
            sl = p.get('slots') or p.get('slot')
            if not sl:
                ctx.fail(invalid('MISSING_OPTION', 'measure on %s needs --slots a,b,c (the autosampler slots to run).' % inst, 'add --slots 0,1,2.'))
            props = self.cap_props(m['configure_cap'])
            pre.append((m['configure_cap'], {m['slots_arg']: self.parse_value(ctx, sl, props[m['slots_arg']], None, '--slots')}, 'set the sequence to slots %s' % sl))
            what = 'sequence of slots %s' % sl
        elif style == 'acquisition':                                         # IRMS: a timed acquisition
            props = self.cap_props(cap)
            t = p.get('time') if p.get('time') is not None else float(self.param('default_time_s', 300.0))
            args['duration_s'] = self.parse_value(ctx, '%rs' % t, props['duration_s'], 'duration', '--time')
            pulses = self.param('reference_pulses')
            if pulses:
                args['reference_pulses'] = [dict(x) for x in pulses if x['start_s'] + x['duration_s'] <= args['duration_s']]
            what = 'acquisition of %s' % units.fmt_s(args['duration_s'])
        elif style == 'run':                                                 # SFA: a run over sampler cups
            props = self.cap_props(cap)
            if not p.get('cups'):
                ctx.fail(invalid('MISSING_OPTION', 'measure on %s needs --cups RACK:POS:TYPE,... (the run order).' % inst,
                                 'e.g. --cups S:22:baseline,S:1:calibrant,S:2:calibrant,S:3:calibrant,A:1:sample,S:22:baseline.'))
            cups = []
            types = ((props['cups'].get('items') or {}).get('properties') or {}).get('type', {}).get('enum') or []
            for item in split_list(p.get('cups')):
                parts = item.split(':')
                if len(parts) != 3:
                    ctx.fail(invalid('BAD_CUP', '%r is not RACK:POS:TYPE.' % item, 'e.g. A:1:sample.'))
                rack, pos = self.rack_pos(ctx, parts[0] + ':' + parts[1], m.get('rack_cap', 'sampler.load'))
                if types and parts[2] not in types:
                    ctx.fail(invalid('BAD_CHOICE', 'cup type %r is not allowed.' % parts[2], 'types: %s.' % ', '.join(types)))
                cups.append({'rack': rack, 'position': pos, 'type': parts[2]})
            args['cups'] = cups
            args['sample_s'] = self.parse_value(ctx, '%rs' % (p.get('time') if p.get('time') is not None else float(self.param('sample_s', 60.0))),
                                                props['sample_s'], 'duration', '--time')
            args['wash_s'] = self.parse_value(ctx, '%rs' % (p.get('wash') if p.get('wash') is not None else float(self.param('wash_s', 30.0))),
                                              props['wash_s'], 'duration', '--wash')
            what = 'run of %d cups' % len(cups)
        else:
            if p.get('slot') or p.get('slots') or p.get('cups'):
                ctx.fail(invalid('NO_SLOTS', '%s measures the sample in place (no autosampler positions).' % inst, 'leave out --slot / --slots / --cups.'))
        for c, a, step in pre:
            ctx.call(c, a, step)
        r = ctx.call(cap, args, 'start the %s (%s)' % (what, cap))
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        job = self.make_job(ctx, m, r, cap, what)
        if job is None:
            return {'text': 'ok: %s %s done\n' % (inst, what), 'json': {'status': r.get('status')}}
        if p.get('wait'):
            w = job.wait_reply(ctx.core, ctx.req, p.get('max') or float(self.param('max_run_s', 3600.0)))
            ctx.refresh()
            st = w['json'].get('status')
            text = '%s: %s on %s\n%s' % (job.handle, what, inst, w['text'])
            js = dict(w['json'], job=job.handle)
            if st == 'succeeded':
                lines, rows = self.quick_result(ctx)
                text += lines
                js['rows'] = rows
            elif st not in RUNNING:
                warn = self.sv(ctx, 'warning')
                ctx.fail(refused('RUN_FAILED', 'the %s on %s ended %s%s.' % (what, inst, st, (' (%s)' % short(warn, 80)) if warn else ''),
                                 'see `chemlab run %s status`, fix the cause and measure again.' % inst, detail={'job': job.handle}))
            return {'text': text, 'json': js}
        return {'text': 'started %s: %s on %s\nfollow it: chemlab status %s | chemlab wait %s | chemlab cancel %s\n' % (
            job.handle, what, inst, job.handle, job.handle, job.handle), 'json': {'job': job.handle, 'kind': job.kind}}

    def make_job(self, ctx, m, r, cap, what):
        if r.get('operation_id') and m.get('job', 'operation') == 'operation':
            return OperationJob(ctx.core, self, ctx.req, '%s on %s' % (what, self.instrument_id), cap, r['operation_id'],
                                max_s=float(self.param('max_run_s', 3600.0)))
        st = m.get('state')
        if not st:
            return None
        ad = self

        def state(c):
            v = ad.slot_value(c, st)
            if st.get('present'):                                            # running while the object has its key (IRMS acquisition)
                running = isinstance(v, dict) and st['present'] in v
                return ('running' if running else 'succeeded'), 'acquisition %s' % ('running' if running else 'done'), {'state': v}
            if v in (st.get('done') or []):
                return 'succeeded', '%s = %s' % (st['slot'], v), {'state': v}
            if v in (st.get('failed') or []):
                return 'failed', '%s = %s' % (st['slot'], v), {'state': v}
            return 'running', '%s = %s' % (st['slot'], v), {'state': v}
        sm = self.vmap('stop')

        def stop(c):
            c.call(sm['cap'], dict(sm.get('args') or {}), 'stop')
            return 'stopped with %s' % sm['cap']
        return StateJob(ctx.core, self, ctx.req, '%s on %s' % (what, self.instrument_id), state, stop=stop if sm else None,
                        max_s=float(self.param('max_run_s', 3600.0)), poll_s=float(self.param('poll_s', 1.0)))

    def quick_result(self, ctx):
        if not self.supported('result'):
            return '', []
        m = self.vmap('result')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the result', query=True)
        rows, _ = self.rows(r.get('result') or {})
        lines = ''.join('  %s\n' % self.row_text(x) for x in rows[:12])
        if lines:
            lines = 'result:\n' + lines
        return lines, rows

    def h_stop(self, ctx, p):
        m = self.vmap('stop')
        ph = self.sv(ctx, 'phase')
        if self.slot('phase') and ph not in self.param('running', ['running', 'draining']):
            return {'text': 'ok: nothing to stop on %s (measurement %s)\n' % (self.instrument_id, ph), 'json': {'changed': False}}
        ctx.call(m['cap'], dict(m.get('args') or {}), 'stop the measurement')
        return {'text': 'ok: %s stopped (%s)\n' % (self.instrument_id, self.sv(ctx, 'phase') if self.slot('phase') else 'stop sent'),
                'json': {'changed': True}}

    # ------------------------------------------------------------------ result / export / records
    def rows(self, res):
        """The member's result -> [{analyte, value, unit, text, quality}] in plain units, and a short status."""
        style = (self.vmap('result') or {}).get('style')
        rows = []
        if style == 'icp_lines':
            for c in res.get('concentrations') or []:
                rows.append(dict(analyte='%s (%s)' % (c.get('element'), c.get('line_id') or c.get('isotope_id')), value=c.get('concentration_kg_m3'),
                                 unit='kg/m^3', text=conc_text(c.get('concentration_kg_m3')), quality=c.get('quality')))
        elif style == 'toc':
            for k, v in (res.get('concentrations_kg_m3') or {}).items():
                if v is not None:
                    rows.append(dict(analyte=k, value=v, unit='kg/m^3', text=conc_text(v), quality=', '.join(res.get('qc') or []) or 'ok'))
        elif style == 'aa':
            if res.get('element'):
                rows.append(dict(analyte='%s (%s)' % (res.get('element'), res.get('line_id')), value=res.get('concentration_kg_m3'), unit='kg/m^3',
                                 text=conc_text(res.get('concentration_kg_m3')), quality=', '.join(res.get('qc') or []) or 'ok'))
        elif style == 'xrf':
            for c in res.get('mass_fractions') or []:
                rows.append(dict(analyte='%s (%s)' % (c.get('element'), c.get('line_id')), value=c.get('mass_fraction'), unit='1',
                                 text=frac_text(c.get('mass_fraction')), quality=c.get('quality')))
        elif style == 'aox':
            rows.append(dict(analyte='Cl-equivalent (%s)' % res.get('mode'), value=res.get('chlorine_equivalent_mass_kg'), unit='kg',
                             text=mass_text(res.get('chlorine_equivalent_mass_kg')), quality=', '.join(res.get('quality') or [])))
        elif style == 'hg':
            v = res.get('mercury_mass_kg')
            q = ', '.join(res.get('quality') or [])
            rows.append(dict(analyte='Hg mass', value=v, unit='kg', text=mass_text(v), quality=q))
            if res.get('mercury_mass_fraction_kg_kg') is not None:
                rows.append(dict(analyte='Hg mass fraction', value=res['mercury_mass_fraction_kg_kg'], unit='1',
                                 text='%.4g mg/kg' % (res['mercury_mass_fraction_kg_kg'] * 1e6), quality=q))
            if res.get('mercury_concentration_kg_m3') is not None:
                rows.append(dict(analyte='Hg concentration', value=res['mercury_concentration_kg_m3'], unit='kg/m^3',
                                 text=conc_text(res['mercury_concentration_kg_m3']), quality=q))
        elif style == 'rga':
            for s in res.get('species') or []:
                rows.append(dict(analyte=s.get('species_id'), value=s.get('partial_pressure_pa'), unit='Pa',
                                 text=('%.4g Pa' % s['partial_pressure_pa']) if finite(s.get('partial_pressure_pa')) else 'n/a',
                                 quality=', '.join(s.get('quality') or []) or 'ok'))
        elif style == 'onh':
            for e, v in (res.get('element_mass_fractions') or res.get('mass_fractions') or {}).items():
                rows.append(dict(analyte=e, value=v, unit='1', text=frac_text(v), quality=short((res.get('element_quality') or {}).get(e), 80)))
        elif style == 'chns':
            for a in res.get('completed_assays') or []:
                for e, v in (a.get('fractions') or {}).items():
                    rows.append(dict(analyte='slot %s %s' % (a.get('slot_index'), e), value=v, unit='1', text=frac_text(v),
                                     quality=(a.get('fraction_quality') or {}).get(e)))
        elif style == 'orbitrap':
            for i, sc in enumerate(res.get('scans') or []):
                cents = sorted(sc.get('centroids') or [], key=lambda x: -(x.get('intensity_relative') or x.get('intensity') or 0))[:5]
                for c in cents:
                    mz = c.get('mass_to_charge_relative') or c.get('raw_mass_to_charge_relative')
                    rows.append(dict(analyte='scan %d m/z' % i, value=mz, unit='1', text='m/z %.5f' % mz if finite(mz) else 'n/a',
                                     quality='calibrated with %s' % res.get('mass_calibration_id') if res.get('mass_calibration_id') else 'uncalibrated'))
        elif style == 'irms':
            for pk in res.get('peaks') or []:
                if pk.get('type') == 'reference':
                    continue
                d = pk.get('delta_normalized') or pk.get('delta_ref') or {}
                for iso, v in d.items():
                    rows.append(dict(analyte='peak %s %s%s' % (pk.get('peak'), iso, (' (%s)' % pk['label']) if pk.get('label') else ''), value=v, unit='1',
                                     peak=pk.get('peak'), isotope=iso,
                                     text='%.2f ‰' % (v * 1000) if finite(v) else 'n/a',
                                     quality='normalised' if pk.get('delta_normalized') and iso in pk['delta_normalized'] else 'vs working reference'))
        elif style == 'sfa':
            for x in res.get('results') or []:
                if x.get('type') == 'sample':
                    rows.append(dict(analyte='%s:%s %s' % (x.get('rack'), x.get('position'), x.get('channel')), value=x.get('concentration_kg_m3'),
                                     unit='kg/m^3', text=conc_text(x.get('concentration_kg_m3')), quality=', '.join(x.get('flags') or []) or 'ok'))
        status = res.get('phase') or res.get('status') or res.get('state')
        if status is None and style == 'irms':
            status = '%d peaks' % len(res.get('peaks') or [])
        return rows, status

    @staticmethod
    def row_text(x):
        q = x.get('quality')
        return '%-28s %s%s' % (x['analyte'], x['text'], ('   [%s]' % short(q, 70).strip('"')) if q not in (None, '', 'ok') else '')

    def h_result(self, ctx, p):
        m = self.vmap('result')
        r = ctx.call(m['cap'], dict(m.get('args') or {}), 'read the result', query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') or {}
        rows, status = self.rows(res)
        lines = ['%s result (%s):' % (self.instrument_id, status or 'see record')]
        lines += ['  ' + self.row_text(x) for x in rows[:40]] or ['  (no quantified value yet: measure a sample with a selected calibration)']
        if len(rows) > 40:
            lines.append('  ... %d more (use --json or --save-to)' % (len(rows) - 40))
        return {'text': '\n'.join(lines) + '\n', 'json': {'rows': rows, 'status': status, 'result': trim(res)}}

    def h_export(self, ctx, p):
        m = self.vmap('export')
        r = ctx.call(m['cap'], {m['arg']: p.pos[0]}, 'export the result as %s' % p.pos[0], query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') or {}
        content = res.get('content', '')
        head = content if len(content) <= 1500 else content[:1500] + '\n... (%d characters; --save-to FILE keeps all of it)' % len(content)
        return {'text': '%s export (%s):\n%s\n' % (self.instrument_id, p.pos[0], head), 'json': {'format': p.pos[0], 'content': content}}

    def h_records(self, ctx, p):
        m = self.vmap('records')
        inst = self.instrument_id
        if p.pos:
            r = ctx.call(m['read_cap'], {'record_id': p.pos[0]}, 'read record %s' % p.pos[0], query=True)
            if ctx.dry_run:
                return {'text': '', 'json': {}}
            rec = r.get('result') or {}
            rows, status = self.rows(rec)
            kind = rec.get('kind') or ''
            text = '%s record %s: %s%s\n' % (inst, p.pos[0], status or '', (', kind %s' % kind) if kind else '')
            text += ''.join('  %s\n' % self.row_text(x) for x in rows[:20])
            brief = {k: v for k, v in trim(rec).items() if not isinstance(v, (dict, list)) or k in ('qc', 'quality')}
            text += '  ' + short(brief, 600) + '\n'
            return {'text': text, 'json': {'record_id': p.pos[0], 'rows': rows, 'record': trim(rec)}}
        r = ctx.call(m['list_cap'], dict(m.get('list_args') or {}), 'list the records', query=True)
        if ctx.dry_run:
            return {'text': '', 'json': {}}
        res = r.get('result') or {}
        recs = res.get('record_ids') or []
        curves = res.get('curve_ids')
        if curves is None and m.get('curves_cap'):
            r2 = ctx.call(m['curves_cap'], {}, 'list the curves', query=True)
            curves = (r2.get('result') or {}).get('curve_ids')
        text = '%s records (%d): %s\n' % (inst, len(recs), ', '.join(recs) or 'none')
        if curves is not None:
            text += 'curves (%d): %s\n' % (len(curves), ', '.join(curves) or 'none')
        return {'text': text, 'json': {'record_ids': recs, 'curve_ids': curves}}

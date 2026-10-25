"""Chromatography class acceptance tasks (10): gold solutions use `chemlab run` (plus the generic status / wait / cancel of class
jobs); the checkers read the evaluator view and, where the answer is a number the instrument computes, the instrument's own query
called as the evaluator after the run.  Five tasks are recovery tasks: the gold triggers the error, reads `--json` `.error.code` and
recovers as the fix says."""
import json

from .chromatography_world import config as world_config
from .common import run_suite

K0 = 273.15
ML_MIN = 1e-6 / 60
HPLC, GC, GC7, SFC, IC, IMS, AF4, CE, GEL, CPC = ('agilent_hplc1260', 'agilent_gc8890b', 'agilent_7890b_ssl_fid', 'agilent_1260infinity',
                                                 'thermo_dionexintegrion', 'gas_flavourspec25gc', 'wyatt_eclipseaf4', 'sciex_pa800',
                                                 'biorad_miniprotean', 'gilson_cpc250')
CE_STEPS = ('[{"type":"rinse","inlet":"BI:A1","outlet":"BO:A1","pressure_pa":137895.14586336,"duration_s":120.0},'
            '{"type":"inject","injection_mode":"pressure","inlet":"SI:A1","outlet":"BO:B1","pressure_pa":3447.378646584,"duration_s":5.0},'
            '{"type":"separate","inlet":"BI:B1","outlet":"BO:B1","control":"voltage","voltage_v":25000.0,"current_limit_a":0.0003,'
            '"ramp_s":10.0,"duration_s":600.0}]')


def query(c, iid, cap, args):
    """The instrument's own query, called by the evaluator (host side) after the run."""
    r = c.lab.backend.evaluator_invoke(iid, cap, args, 'check-%s-%s' % (iid, cap))
    return r.get('result') or {} if r.get('status') == 'succeeded' else {}


def jobs_of(c, iid):
    return [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == iid]


# ------------------------------------------------------------------ checkers
def c_hplc(c):
    e = c.ev(HPLC)
    c.near('flow 0.5 mL/min', e.get('method.flow_m3_s'), 0.5 * ML_MIN, 1e-12)
    c.near('run time 2 min', e.get('method.run_duration_s'), 120.0, 1e-9)
    c.equal('gradient 5 % -> 95 % B in 1 min', [(round(g['time_s'], 6), round(g['fraction_b'], 6)) for g in e.get('method.gradient') or []],
            [(0.0, 0.05), (60.0, 0.95)])
    c.equal('sample mix1 injected', e.get('sample.id'), 'mix1')
    c.equal('run finished', e.get('run.status'), 'succeeded')
    peaks = (e.get('measurement.chromatogram') or {})
    t, y = peaks.get('time_s') or [], peaks.get('signal') or []
    want = t[max(range(len(y)), key=lambda i: y[i])] if y else None
    c.near('hplc.txt = retention time of the largest peak (s)', c.number_file('hplc.txt'), want if want is not None else -1, 1.0)


def c_gc(c):
    e = c.ev(GC)
    m = e.get('gc.method') or {}
    oven, inlet = m.get('oven') or {}, m.get('inlet') or {}
    c.near('oven start 60 °C', oven.get('initial_K'), K0 + 60, 1e-6)
    c.near('initial hold 1 min', oven.get('initial_time_s'), 60.0, 1e-6)
    c.near('ramp 20 °C/min', oven.get('ramp_1_rate_K_s'), 20 / 60, 1e-9)
    c.near('ramp to 220 °C', oven.get('ramp_1_final_K'), K0 + 220, 1e-6)
    c.near('final hold 2 min', oven.get('ramp_1_final_time_s'), 120.0, 1e-6)
    c.near('program ends after ramp 1', oven.get('ramp_2_rate_K_s'), 0.0, 1e-12)
    c.near('inlet 250 °C', inlet.get('temperature_K'), K0 + 250, 1e-6)
    c.near('split 50:1', inlet.get('split_ratio'), 50.0, 1e-9)
    last = e.get('gc.last_run') or {}
    c.equal('the run of mix1 finished (not aborted)', (last.get('sample_id'), last.get('aborted')), ('mix1', False))
    c.near('run length = program (1 + 8 + 2 min)', last.get('elapsed_s'), 660.0, 1.0)
    c.near('gc_peaks.txt = number of peaks', c.number_file('gc_peaks.txt'), len(last.get('peaks') or []), 0)


def c_sfc(c):
    e = c.ev(SFC)
    c.near('flow 1.5 mL/min', e.get('pump.flow_target_m3_s'), 1.5 * ML_MIN, 1e-12)
    c.near('column 40 °C', e.get('column.target_k'), K0 + 40, 1e-6)
    c.add('one injection ran to the end', e.get('run.count', 0) >= 1 and e.get('run.phase') == 'succeeded', (e.get('run.count'), e.get('run.phase')))
    peaks = query(c, SFC, 'chromatogram.read', {}).get('peaks') or []
    want = max((p['area_au_s'] for p in peaks), default=None)
    c.near('sfc.txt = area of the largest peak', c.number_file('sfc.txt'), want if want is not None else -1, 1e-9 + 1e-6 * abs(want or 0))


def c_ic(c):
    e = c.ev(IC)
    c.near('eluent 20 mM', e.get('eluent.target_mol_m3'), 20.0, 1e-9)
    c.equal('run finished', e.get('acquisition.phase'), 'succeeded')
    want = query(c, IC, 'quantification.integrate', {'start_s': 20.0, 'stop_s': 40.0, 'calibration_s_m_s_per_mol_m3': 0.02}).get('concentration_mol_m3')
    c.near('ic.txt = concentration in mmol/L (window 20-40 s, response 0.02)', c.number_file('ic.txt'), want if want is not None else -1,
           1e-6 + 1e-6 * abs(want or 0))


def c_ims(c):
    e = c.ev(IMS)
    c.near('injector 90 °C', e.get('injector.target_k'), K0 + 90, 1e-6)
    c.equal('run finished', e.get('acquisition.phase'), 'succeeded')
    c.near('run time 200 s', e.get('acquisition.elapsed_s'), 200.0, 1.0)
    try:
        rec = json.loads(c.work_file('gcims.json') or '{}')
    except ValueError:
        rec = {}
    tr = ((rec.get('chromatogram') or {}).get('trace') or {})
    c.add('gcims.json holds the complete 200 s trace', (rec.get('chromatogram') or {}).get('complete') and len(tr.get('x') or []) >= 190,
          len(tr.get('x') or []))


def c_af4(c):
    e = c.ev(AF4)
    c.equal('the separation completed', e.get('run.state'), 'completed')
    c.near('detector flow 1 mL/min', (e.get('method.summary') or {}).get('detector_flow_m3_s'), ML_MIN, 1e-12)
    js = jobs_of(c, AF4)
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in js), [(j.handle, j.state) for j in js])
    try:
        rec = json.loads(c.work_file('af4.json') or '{}')
    except ValueError:
        rec = {}
    tr = ((rec.get('chromatogram') or {}).get('trace') or {})
    c.add('af4.json holds the fractogram', len(tr.get('x') or []) > 1000, len(tr.get('x') or []))


def c_ce(c):
    e = c.ev(CE)
    c.equal('the CE run finished', e.get('cycle.phase'), 'succeeded')
    c.near('detection 214 nm', e.get('detector.wavelength_m'), 2.14e-07, 1e-15)
    peaks = query(c, CE, 'electropherogram.read', {}).get('peaks') or []
    want = max(peaks, key=lambda p: p['height_au'])['migration_time_s'] if peaks else None
    c.near('ce.txt = migration time of the main peak (s)', c.number_file('ce.txt'), want if want is not None else -1, 0.01)


def c_gel(c):
    e = c.ev(GEL)
    rec = query(c, GEL, 'measurement.result', {'record_id': e.get('result.last_record_id') or 'none'})
    st = rec.get('settings') or {}
    c.equal('the run completed', rec.get('status'), 'completed')
    c.equal('constant 200 V for 35 min', (st.get('constant'), st.get('voltage_v'), st.get('time_s')), ('voltage', 200.0, 2100.0))
    lanes = {ln['lane']: sorted({b.get('species') for b in ln.get('bands') or []}) for ln in
             (query(c, GEL, 'gel.inspect', {'gel_id': 'gel_a'}).get('lanes') or [])}
    c.equal('the lysate proteins are in lane 2 only', {k: v for k, v in lanes.items() if v}, {2: ['bsa', 'lysozyme_hen', 'ovalbumin']})
    front = query(c, GEL, 'gel.observe', {'position': 1}).get('dye_front_m')
    c.near('gel.txt = dye front in mm', c.number_file('gel.txt'), front * 1000 if front is not None else -1, 0.05)


def c_gc7(c):
    e = c.ev(GC7)
    ramps = (e.get('oven.method') or {}).get('ramps') or []
    c.near('oven 80 °C', (e.get('oven.method') or {}).get('temp_k'), K0 + 80, 1e-6)
    c.near('initial time 1 min', (e.get('oven.method') or {}).get('initial_time_s'), 60.0, 1e-6)
    c.add('ramp 1: 30 °C/min to 200 °C, hold 1 min', bool(ramps) and abs(ramps[0]['rate'] - 0.5) < 1e-9 and abs(ramps[0]['final'] - (K0 + 200)) < 1e-6
          and abs(ramps[0]['final_time'] - 60.0) < 1e-6, ramps[:2])
    c.near('a whole run (1 + 4 + 1 min) was made', e.get('run.last_s'), 360.0, 2.0)
    c.add('the run is over', e.get('run.state') in ('idle', 'post'), e.get('run.state'))


def c_cpc(c):
    e = c.ev(CPC)
    c.equal('the CPC run is cancelled', e.get('cycle.phase'), 'cancelled')
    js = jobs_of(c, CPC)
    c.add('the class job was cancelled', any(j.state == 'cancelled' for j in js), [(j.handle, j.state) for j in js])


# ------------------------------------------------------------------ gold solutions
GOLD_HPLC = r'''I=agilent_hplc1260
chemlab run $I power on
chemlab run $I method flow=0.5mL/min run=2min gradient=0:5%,1min:95%
chemlab run $I load mix1
chemlab run $I flow on
job=$(chemlab run $I run --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run $I chromatogram --json | jq -r '.chromatogram.peaks | max_by(.height) | .rt_s' > hplc.txt
cat hplc.txt'''

GOLD_GC = r'''I=agilent_gc8890b
chemlab run $I method oven=60C:1min..220C@20C/min:2min inlet=250C split=50
chemlab run $I load mix1
job=$(chemlab run $I run --wait-ready 30min --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run $I chromatogram --json | jq -r '.chromatogram.peaks | length' > gc_peaks.txt
cat gc_peaks.txt'''

GOLD_SFC = r'''I=agilent_1260infinity
chemlab run $I method flow=1.5mL/min column-temp=40C gradient=0:2%,1min:2%,5min:45%,6min:45% run=7min wl=254nm
chemlab run $I flow on
chemlab run $I run --pos 1:1 --json > r.json; rc=$?
jq -r '.error.code + ": " + .error.fix' r.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_READY"' r.json >/dev/null; then
  chemlab run $I run --pos 1:1 --wait-ready 15min --json > r.json
fi
chemlab wait "$(jq -r .job r.json)" --max 30min
chemlab run $I chromatogram --json | jq -r '.chromatogram.peaks | max_by(.area) | .area' > sfc.txt
cat sfc.txt'''

GOLD_IC = r'''I=thermo_dionexintegrion
chemlab run $I method eluent=20mM
chemlab run $I run --json > r.json; rc=$?
jq -r '.error.code + ": " + .error.fix' r.json
if [ $rc -eq 3 ] && jq -e '.error.code == "FLOW_OFF"' r.json >/dev/null; then
  chemlab run $I flow on
  chemlab run $I run --json > r.json
fi
chemlab wait "$(jq -r .job r.json)" --max 10min
chemlab run $I quantify --standards 0.1mM=0.002,0.5mM=0.01 --window 20s..40s --json | jq -r .concentration_mol_m3 > ic.txt
cat ic.txt'''

GOLD_IMS = r'''I=gas_flavourspec25gc
chemlab run $I method run=200s inlet=90C --json > m.json; rc=$?
jq -r '.error.code + ": " + .error.fix' m.json
if [ $rc -eq 3 ] && jq -e '.error.code == "GAS_ON"' m.json >/dev/null; then
  chemlab run $I flow off
  chemlab run $I method run=200s inlet=90C
  chemlab run $I flow on
fi
job=$(chemlab run $I run --wait-ready 15min --json | jq -r .job)
chemlab wait "$job" --max 20min
chemlab run $I chromatogram --save-to gcims.json'''

GOLD_AF4 = r'''I=wyatt_eclipseaf4
chemlab run $I method flow=1mL/min
chemlab run $I load unknown_1 --volume 20uL
job=$(chemlab run $I run --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 1h
chemlab run $I chromatogram --save-to af4.json'''

GOLD_CE = r'''I=sciex_pa800
chemlab run $I method steps='%s' wl=214nm
chemlab run $I run --json > r.json; rc=$?
jq -r '.error.code + ": " + .error.fix' r.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_READY"' r.json >/dev/null; then
  chemlab run $I method lamp=on
  chemlab run $I run --wait-ready 10min --json > r.json
fi
chemlab wait "$(jq -r .job r.json)" --max 30min
chemlab run $I chromatogram --json | jq -r '.chromatogram.peaks | max_by(.height) | .rt_s' > ce.txt
cat ce.txt''' % CE_STEPS

GOLD_GEL = r'''I=biorad_miniprotean
chemlab run $I method voltage=200V run=35min constant=voltage
chemlab run $I load lysate_1 --pos 1 --lane 2 --volume 10uL
job=$(chemlab run $I run --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run $I chromatogram --json | jq -r '.chromatogram.extra.dye_front_m * 1000' > gel.txt
cat gel.txt'''

GOLD_GC7 = r'''I=agilent_7890b_ssl_fid
chemlab run $I run --json > r.json; rc=$?
jq -r '.error.code + ": " + .error.fix' r.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NO_PROGRAM"' r.json >/dev/null; then
  chemlab run $I method oven=80C:1min..200C@30C/min:1min
  chemlab run $I run --wait-ready 30min --json > r.json
fi
chemlab wait "$(jq -r .job r.json)" --max 1h
chemlab run $I status'''

GOLD_CPC = r'''I=gilson_cpc250
job=$(chemlab run $I run --json | jq -r .job)
chemlab status "$job"
chemlab cancel "$job"
chemlab run $I status'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'c01_hplc_run', 'kind': 'multi-step (power, method, load, flow, run job, report)', 'instruments': [HPLC], 'check': c_hplc,
     'instruction': 'On the Agilent 1260 LC (agilent_hplc1260): switch it on, set a method with 0.5 mL/min flow, a 2 min run and a gradient '
                    'from 5 % to 95 % B in 1 min, load sample mix1, start the run with the mobile phase on, wait for it, and write the '
                    'retention time in seconds of the largest peak into hplc.txt.', 'gold': GOLD_HPLC},
    {'id': 'c02_gc_oven_program', 'kind': 'multi-step (oven program, ready wait, job, report)', 'instruments': [GC], 'check': c_gc,
     'instruction': 'On the Agilent 8890 GC (agilent_gc8890b) run sample mix1 with the oven at 60 °C for 1 min, then 20 °C/min to 220 °C '
                    'held for 2 min, inlet at 250 °C and split 50:1. Start when the GC is ready, wait for the end of the run, and write the '
                    'number of peaks into gc_peaks.txt.', 'gold': GOLD_GC},
    {'id': 'c03_sfc_recover_not_ready', 'kind': 'recovery: not ready (SFC pressure / flow settling)', 'instruments': [SFC], 'check': c_sfc,
     'instruction': 'On the Agilent 1260 SFC (agilent_1260infinity) set flow 1.5 mL/min, column 40 °C, modifier 2 % for 1 min then up to '
                    '45 % at 5 min held to 6 min, a 7 min run and detection at 254 nm. Start the pump and inject the vial in drawer 1, '
                    'position 1; if the instrument refuses, follow its fix. Write the area of the largest peak into sfc.txt.', 'gold': GOLD_SFC},
    {'id': 'c04_ic_recover_flow_off', 'kind': 'recovery: pump off + quantify', 'instruments': [IC], 'check': c_ic,
     'instruction': 'On the Dionex Integrion (thermo_dionexintegrion) raise the eluent to 20 mM, run the loaded sample, and quantify the peak '
                    'between 20 s and 40 s against two standards: 0.1 mM gives area 0.002 and 0.5 mM gives area 0.01. Write the '
                    'concentration in mmol/L into ic.txt.', 'gold': GOLD_IC},
    {'id': 'c05_gcims_recover_gas_on', 'kind': 'recovery: method only with the gas off', 'instruments': [IMS], 'check': c_ims,
     'instruction': 'On the FlavourSpec GC-IMS (gas_flavourspec25gc) set the run time to 200 s and the injector to 90 °C, then measure the '
                    'loaded headspace sample and save the chromatogram to gcims.json.', 'gold': GOLD_IMS},
    {'id': 'c06_af4_job', 'kind': 'long action: job id (status, wait) + record file', 'instruments': [AF4], 'check': c_af4,
     'instruction': 'On the Eclipse AF4 (wyatt_eclipseaf4) use the default elution programme with a detector flow of 1 mL/min, inject 20 µL '
                    'of the provided sample unknown_1, start the separation as a job, check its status once, wait for it and save the '
                    'fractogram to af4.json.', 'gold': GOLD_AF4},
    {'id': 'c07_ce_recover_lamp', 'kind': 'recovery: lamp off (not ready)', 'instruments': [CE], 'check': c_ce,
     'instruction': 'On the PA 800 Plus (sciex_pa800) run this CE method: rinse 120 s at 20 psi from BI:A1 to BO:A1, pressure injection '
                    '5 s at 0.5 psi from SI:A1 (outlet BO:B1), separation at 25 kV for 10 min from BI:B1 to BO:B1 (10 s ramp, 300 µA '
                    'limit), UV detection at 214 nm. Write the migration time in seconds of the main peak into ce.txt.', 'gold': GOLD_CE},
    {'id': 'c08_gel_run', 'kind': 'multi-step (load a lane, programme, run, observe)', 'instruments': [GEL], 'check': c_gel,
     'instruction': 'On the Mini-PROTEAN Tetra (biorad_miniprotean) load 10 µL of sample lysate_1 into lane 2 of the gel at position 1, run '
                    'at constant 200 V for 35 min, wait for the end and write how far the dye front moved, in mm, into gel.txt.',
     'gold': GOLD_GEL},
    {'id': 'c09_gc7890_recover_no_program', 'kind': 'recovery: no oven program (the run would never end)', 'instruments': [GC7],
     'check': c_gc7,
     'instruction': 'On the Agilent 7890B (agilent_7890b_ssl_fid) make one manual injection with the oven at 80 °C for 1 min, then '
                    '30 °C/min to 200 °C held for 1 min, and wait until the run is over.', 'gold': GOLD_GC7},
    {'id': 'c10_cpc_cancel_job', 'kind': 'long action: job status + cancel', 'instruments': [CPC], 'check': c_cpc,
     'instruction': 'Start the CPC 250 (gilson_cpc250) separation with its current method as a job, check that it is running, then cancel '
                    'it because the wrong solvent system was loaded.', 'gold': GOLD_CPC},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'chromatography'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('chromatography', TASKS, only=only, noact=noact)

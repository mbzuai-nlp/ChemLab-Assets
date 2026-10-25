"""Bioassay class acceptance tasks (10, 4 recovery): gold solutions use `chemlab run` (and jq) inside the sandbox; the checkers read
the evaluator view and, for computed results, ask the instrument's own evaluation as the evaluator (read-only queries).

The lab is the bioassay world (tasks/bioassay_world.py); some tasks add a little more world (a plate already on the washer's
carrier, an ITC cell already filled with its method set).
"""
from .bioassay_world import config as world_config
from .common import run_suite

K0 = 273.15
BMG, WASH, PCR, SPR, ITC, FACS = ('bmg_clariostarplus', 'biotek_405ts', 'biorad_cfxopus', 'cytiva_biacoret200', 'malvern_microcalpeaq',
                                  'bd_facsariaiii')
_N = [0]


def world_call(lab, iid, cap, args):
    _N[0] += 1
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, _N[0]))
    assert r.get('status') in ('succeeded', 'accepted'), (cap, r)
    return r


def ask(c, iid, cap, args):
    """A read-only query as the evaluator (checkers): the instrument's own result."""
    _N[0] += 1
    r = c.lab.backend.evaluator_invoke(iid, cap, args, 'check-%s-%d' % (cap, _N[0]))
    return r.get('result') if r.get('status') == 'succeeded' else None


# ------------------------------------------------------------------ worlds
def w_bmg_on(lab):
    world_call(lab, BMG, 'power.set', {'on': True})
    lab.backend.advance(30)


def w_plate_on_washer(lab):
    world_call(lab, WASH, 'plate.load', {'plate_id': 'p1'})


ITC_SHORT = [{'volume_m3': 4e-10, 'duration_s': 0.8, 'spacing_s': 120.0}] + [{'volume_m3': 2e-9, 'duration_s': 4.0, 'spacing_s': 120.0}] * 3


def w_itc_cell_and_method(lab):
    world_call(lab, ITC, 'pipette.move', {'location': 'rest'})
    lab.backend.advance(40)
    world_call(lab, ITC, 'cell.load', {'solution_id': 'edta', 'volume_m3': 3e-7, 'rinse': True})
    world_call(lab, ITC, 'method.set', {'temperature_k': K0 + 25, 'reference_power_w': 2.092e-5, 'feedback': 'high', 'stir_rate_hz': 12.5,
                                        'initial_delay_s': 60.0, 'injections': ITC_SHORT, 'cell_concentration_mol_m3': 0.1,
                                        'syringe_concentration_mol_m3': 1.0})


# ------------------------------------------------------------------ checkers
def c_plate_read(c):
    e = c.ev(BMG)
    c.equal('run completed', (e.get('run.status') or {}).get('state'), 'completed')
    p = e.get('protocol.current') or {}
    c.equal('fluorescence end point', p.get('mode'), 'fi_endpoint')
    rows = (ask(c, BMG, 'data.read', {}) or {}).get('rows') or []
    c.add('read at the 525 nm emission', bool(rows) and all(abs((r.get('wavelength_m') or 0) - 5.25e-7) < 1e-12 for r in rows),
          sorted({r.get('wavelength_m') for r in rows}))
    c.equal('the six wells were read', sorted({r['well'] for r in rows}), ['A1', 'A2', 'A3', 'B1', 'B2', 'B3'])
    a = [r['value'] for r in rows if r['well'] in ('A1', 'A2', 'A3')]
    b = [r['value'] for r in rows if r['well'] in ('B1', 'B2', 'B3')]
    want = sum(a) / len(a) - sum(b) / len(b) if a and b else None
    got = c.number_file('signal.txt')
    c.add('signal.txt is the blank-corrected mean of A1:A3 (within 0.1 %)', want is not None and got is not None and abs(got - want) <= 1e-3 * abs(want),
          'got %r, want %r' % (got, want))


def c_bmg_busy(c):
    e = c.ev(BMG)
    c.equal('plate wp in the reader', (e.get('carrier.plate') or {}).get('plate_id'), 'wp')
    c.near('carrier in', e.get('drawer.position_m'), 0.0, 1e-6)
    c.near('incubator set point 30 °C', e.get('incubator.setpoint_k'), K0 + 30, 1e-6)


def c_wash(c):
    e = c.ev(WASH)
    rid = e.get('result.last_record_id')
    rec = ask(c, WASH, 'measurement.result', {'record_id': rid}) if rid else None
    c.add('a wash record completed with W-Corning_FLAT', bool(rec) and rec.get('status') == 'completed' and rec.get('kind') == 'wash', rec and rec.get('status'))
    c.equal('record.txt names it', c.work_file('record.txt'), rid)
    c.equal('the plate is back on the bench', e.get('plate.on_carrier'), None)
    c.add('p1 is on the bench', 'p1' in (e.get('plate.bench_ids') or []), e.get('plate.bench_ids'))


def c_prime(c):
    e = c.ev(WASH)
    rid = e.get('result.last_record_id')
    rec = ask(c, WASH, 'measurement.result', {'record_id': rid}) if rid else None
    s = (rec or {}).get('settings') or {}
    c.add('a prime record completed', bool(rec) and rec.get('kind') == 'prime' and rec.get('status') == 'completed', rec and (rec.get('kind'), rec.get('status')))
    c.equal('buffer B', s.get('buffer'), 'B')
    c.near('100 mL', (s.get('volume_m3') or 0) * 1e6, 100.0, 1e-6)
    c.equal('the plate is off the carrier', e.get('plate.on_carrier'), None)


def c_qpcr(c):
    e = c.ev(PCR)
    c.equal('run completed', e.get('run.state'), 'completed')
    d = e.get('protocol.definition') or {}
    steps = d.get('steps') or []
    reads = [s for s in steps if s.get('plate_read')]
    gotos = [s for s in steps if s.get('kind') == 'goto']
    c.add('40 cycles of 95 °C / 60 °C with a read at 60 °C', len(reads) == 1 and abs(reads[0].get('temperature_k', 0) - (K0 + 60)) < 1e-6
          and len(gotos) == 1 and gotos[0].get('cycles') == 39, steps)
    c.near('20 µL', (d.get('sample_volume_m3') or 0) * 1e9, 20.0, 1e-6)
    res = (ask(c, PCR, 'analysis.cq', {}) or {}).get('results') or []
    want = next((r.get('cq') for r in res if r.get('well') == 'A1'), None)
    c.near('cq.txt is the Cq of A1 (±0.01)', c.number_file('cq.txt'), want if want is not None else -1, 0.01)


def c_spr(c):
    e = c.ev(SPR)
    c.equal('chip chip_a docked', (e.get('chip.id'), e.get('chip.state')), ('chip_a', 'docked'))
    c.near('analysis temperature set to 25 °C', e.get('temperature.analysis_setpoint_k'), K0 + 25, 1e-6)
    runs = (ask(c, SPR, 'runs.list', {}) or {}).get('runs') or []
    c.add('run buf1 completed', any(r.get('run_id') == 'buf1' and r.get('state') == 'completed' for r in runs), runs)
    t = c.work_file('report.json') or ''
    c.add('report.json holds the report points', '"rows"' in t and 'relative_ru' in t, t[:120])


def c_itc(c):
    e = c.ev(ITC)
    c.equal('experiment ca1 completed', (e.get('run.experiment_id'), e.get('run.state')), ('ca1', 'completed'))
    fit = ask(c, ITC, 'data.analyze', {'experiment_id': 'ca1', 'model': 'one_set_of_sites', 'exclude_injections': [1]}) or {}
    n = next((p['value'] for p in fit.get('parameters') or [] if p.get('name') == 'n'), None)
    c.near('n.txt is the fitted n (±0.01)', c.number_file('n.txt'), n if n is not None else -1, 0.01)
    c.near('n is about 1 for Ca2+ / EDTA', n, 1.0, 0.1)


def c_itc_recover(c):
    e = c.ev(ITC)
    c.equal('experiment e5 completed', (e.get('run.experiment_id'), e.get('run.state')), ('e5', 'completed'))
    c.equal('cell holds edta', e.get('cell.solution_id'), 'edta')


def c_facs(c):
    e = c.ev(FACS)
    recs = (ask(c, FACS, 'records.list', {}) or {}).get('records') or []
    r = next((x for x in recs if x.get('record_id') == 'pbmc1'), None)
    c.add('record pbmc1 with 5000 events', bool(r) and r.get('events') == 5000, r)
    c.near('events.txt says 5000', c.number_file('events.txt'), 5000, 0)
    c.equal('tube unloaded', e.get('sample.tube_id'), None)
    c.equal('fluidics ready', e.get('fluidics.state'), 'ready')


def c_facs_recover(c):
    e = c.ev(FACS)
    c.equal('tube t1 up and acquiring', (e.get('sample.tube_id'), e.get('sample.acquiring')), ('t1', True))
    c.near('sample chamber set to 4 °C', e.get('sample.temperature_setpoint_k'), K0 + 4, 1e-6)


# ------------------------------------------------------------------ gold solutions
GOLD_BA01 = r'''chemlab run bmg_clariostarplus load wp
chemlab run bmg_clariostarplus method mode=fi_endpoint wells=A1:A3,B1:B3 ex=485nm em=525nm
chemlab run bmg_clariostarplus start
chemlab run bmg_clariostarplus wait --max 10min
chemlab run bmg_clariostarplus data --json > read.json
jq '[.record.rows[] | select(.well|test("^A[123]$")) | .value] as $a | [.record.rows[] | select(.well|test("^B[123]$")) | .value] as $b
    | ($a|add/length) - ($b|add/length)' read.json > signal.txt
cat signal.txt'''

GOLD_BA02 = r'''chemlab run bmg_clariostarplus power on
chemlab run bmg_clariostarplus load wp --json > l.json; rc=$?
if [ $rc -ne 0 ]; then
  jq -r '.error.code + ": " + .error.fix' l.json
  if jq -e '.error.code == "BUSY"' l.json >/dev/null; then chemlab wait 20; chemlab run bmg_clariostarplus load wp; fi
fi
chemlab run bmg_clariostarplus set-temp 30C
chemlab run bmg_clariostarplus status'''

GOLD_BA03 = r'''chemlab run biotek_405ts load p1
job=$(chemlab run biotek_405ts start W-Corning_FLAT --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run biotek_405ts data --json | jq -r .record.record_id > record.txt
chemlab run biotek_405ts unload
cat record.txt'''

GOLD_BA04 = r'''chemlab run biotek_405ts prime buffer=B volume=100mL --json > p.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "PLATE_IN"' p.json >/dev/null; then
  jq -r .error.fix p.json
  chemlab run biotek_405ts unload
  chemlab run biotek_405ts prime buffer=B volume=100mL
fi
chemlab run biotek_405ts wait --max 10min'''

GOLD_BA05 = r'''chemlab run biorad_cfxopus load plate_1
chemlab run biorad_cfxopus method name=GAPDH volume=20uL lid=105C steps=95C@3min,95C@10s,60C@30s+read,goto2x39
chemlab run biorad_cfxopus start
chemlab run biorad_cfxopus wait --max 3h
chemlab run biorad_cfxopus analyze cq --json | jq -r '.evaluation.results[] | select(.well=="A1") | .cq * 100 | round / 100' > cq.txt
cat cq.txt'''

GOLD_BA06 = r'''chemlab run cytiva_biacoret200 load chip_a --type CM5
chemlab run cytiva_biacoret200 prime
chemlab run cytiva_biacoret200 wait --max 30min
chemlab run cytiva_biacoret200 set-temp 25C
chemlab run cytiva_biacoret200 start buf1 samples=R1B1 contact=60s dissociation=60s flow=30uL/min path=1-2 ref=2-1 rate=1
chemlab run cytiva_biacoret200 wait --max 1h
chemlab run cytiva_biacoret200 data buf1 --save-to report.json
head -c 200 report.json'''

GOLD_BA07 = r'''chemlab run malvern_microcalpeaq method temp=25C ref=5ucal/s feedback=high stir=750rpm delay=60s \
    injections=0.4uL@0.8s,12x2uL@4s spacing=150s cell=0.1mM syringe=1mM
chemlab run malvern_microcalpeaq load edta --into cell
chemlab run malvern_microcalpeaq load ca --into syringe
chemlab run malvern_microcalpeaq start ca1
chemlab run malvern_microcalpeaq wait --max 3h
chemlab run malvern_microcalpeaq analyze one_set_of_sites exclude=1 --json | jq -r '.evaluation.parameters[] | select(.name=="n") | .value' > n.txt
cat n.txt'''

GOLD_BA08 = r'''chemlab run malvern_microcalpeaq start e5 --json > s.json; rc=$?
if [ $rc -eq 3 ]; then
  jq -r '.error.code + ": " + .error.fix' s.json
  if jq -e '.error.code == "SYRINGE_NOT_LOADED"' s.json >/dev/null; then
    chemlab run malvern_microcalpeaq load ca --into syringe
    chemlab run malvern_microcalpeaq start e5
  fi
fi
chemlab run malvern_microcalpeaq wait --max 3h'''

GOLD_BA09 = r'''chemlab run bd_facsariaiii prime
chemlab run bd_facsariaiii load t1
chemlab run bd_facsariaiii start pbmc1 --events 5000
chemlab run bd_facsariaiii wait --max 10min
chemlab run bd_facsariaiii data pbmc1 --json | jq -r .record.total_events > events.txt
chemlab run bd_facsariaiii stop
chemlab run bd_facsariaiii unload
cat events.txt'''

GOLD_BA10 = r'''chemlab run bd_facsariaiii load t1 --json > l.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "STREAM_OFF"' l.json >/dev/null; then
  jq -r .error.fix l.json
  chemlab run bd_facsariaiii prime
  chemlab run bd_facsariaiii load t1
fi
chemlab run bd_facsariaiii set-temp 4C'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'ba01_plate_read', 'kind': 'multi-step + computed report', 'instruments': [BMG], 'world': w_bmg_on, 'check': c_plate_read,
     'instruction': 'The CLARIOstar plate reader (bmg_clariostarplus) is on; plate wp is on the bench with fluorescein in A1:A3 and water '
                    'blanks in B1:B3. Read the fluorescence of these six wells (excitation 485 nm, emission 525 nm) and write the mean of '
                    'A1:A3 minus the mean of B1:B3 (RFU) into signal.txt.', 'gold': GOLD_BA01},
    {'id': 'ba02_recover_reader_starting', 'kind': 'recovery: instrument still starting up', 'instruments': [BMG], 'check': c_bmg_busy,
     'instruction': 'Switch on the CLARIOstar plate reader (bmg_clariostarplus), load plate wp into it and set the incubator to 30 °C.',
     'gold': GOLD_BA02},
    {'id': 'ba03_wash_plate', 'kind': 'long action: job + record', 'instruments': [WASH], 'check': c_wash,
     'instruction': 'Wash ELISA plate p1 on the BioTek 405 TS washer (biotek_405ts) with its stored protocol W-Corning_FLAT, write the '
                    'id of the wash record into record.txt, and put the plate back on the bench.', 'gold': GOLD_BA03},
    {'id': 'ba04_recover_prime_plate_on', 'kind': 'recovery: a plate blocks priming', 'instruments': [WASH], 'world': w_plate_on_washer,
     'check': c_prime, 'instruction': 'Prime buffer line B of the BioTek 405 TS washer (biotek_405ts) with 100 mL and wait until it is done.',
     'gold': GOLD_BA04},
    {'id': 'ba05_qpcr_cq', 'kind': 'method syntax + long run + analysis', 'instruments': [PCR], 'check': c_qpcr,
     'instruction': 'Run plate plate_1 on the CFX Opus (biorad_cfxopus): 20 µL reactions, lid 105 °C, 95 °C for 3 min, then 40 cycles of '
                    '95 °C 10 s and 60 °C 30 s with a plate read at 60 °C. When it has finished, write the Cq of well A1 (two decimals) '
                    'into cq.txt.', 'gold': GOLD_BA05},
    {'id': 'ba06_spr_buffer_cycle', 'kind': 'consumable + prime job + run job + record file', 'instruments': [SPR], 'check': c_spr,
     'instruction': 'On the Biacore T200 (cytiva_biacoret200), dock a new CM5 sensor chip named chip_a, prime the system, set the analysis '
                    'temperature to 25 °C, and run "buf1": one cycle injecting the running buffer from rack position R1B1 for 60 s '
                    '(60 s dissociation) at 30 µL/min over flow cells 1-2 with reference subtraction 2-1 at 1 Hz. Save the run\'s report '
                    'points into report.json.', 'gold': GOLD_BA06},
    {'id': 'ba07_itc_titration', 'kind': 'composite loading + long run + fit', 'instruments': [ITC], 'check': c_itc,
     'instruction': 'On the PEAQ-ITC (malvern_microcalpeaq), titrate 1 mM CaCl2 ("ca", into the syringe) into 0.1 mM EDTA ("edta", in the '
                    'cell) at 25 °C: reference power 5 µcal/s, high feedback, 750 rpm, 60 s initial delay, a 0.4 µL injection then 12 '
                    'injections of 2 µL, 150 s apart. Run it as experiment ca1, fit one set of sites (leave out the first injection) and '
                    'write the stoichiometry n into n.txt.', 'gold': GOLD_BA07},
    {'id': 'ba08_recover_itc_syringe', 'kind': 'recovery: syringe not loaded', 'instruments': [ITC], 'world': w_itc_cell_and_method,
     'check': c_itc_recover, 'instruction': 'The PEAQ-ITC (malvern_microcalpeaq) has EDTA in its cell and the method set. Run experiment e5 '
                                           'with the CaCl2 solution "ca" as titrant, and wait until it has finished.', 'gold': GOLD_BA08},
    {'id': 'ba09_cytometer_record', 'kind': 'start-up + record + report', 'instruments': [FACS], 'check': c_facs,
     'instruction': 'On the FACSAria III (bd_facsariaiii), start up the fluidics, load tube t1, record 5000 events as record pbmc1, write '
                    'the number of recorded events into events.txt, then stop acquiring and unload the tube.', 'gold': GOLD_BA09},
    {'id': 'ba10_recover_stream_off', 'kind': 'recovery: fluidics not started', 'instruments': [FACS], 'check': c_facs_recover,
     'instruction': 'Load tube t1 on the FACSAria III (bd_facsariaiii) so that it acquires, and set the sample chamber to 4 °C.',
     'gold': GOLD_BA10},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'bioassay'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('bioassay', TASKS, only=only, noact=noact)

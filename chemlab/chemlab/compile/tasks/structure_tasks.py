"""Structure class acceptance tasks (8, three of them recovery tasks): gold solutions use `chemlab run`; the checkers read the
evaluator view (truth slots included), records read by the evaluator and the agent's work files.  World:
tasks/structure_world.py plus task-specific operator steps."""
import json
import math

from .common import run_suite
from .structure_world import config as world_config

MF, XT, SX, XPS, SE = ('rigaku_miniflex600_external_cooling', 'rigaku_xtalabsynergy', 'antonpaar_saxspace', 'kratos_axissupra', 'ja_alphase')
DEG = math.pi / 180.0


def with_steps(slug, steps):
    def cfg(c):
        c = world_config(c)
        for e in c['instruments']:
            if e['asset_type_id'].split('.', 1)[1] == slug:
                e['setup'] = list(e.get('setup') or []) + steps
        return c
    return cfg


def op(cap, args):
    return {'capability': cap, 'arguments': args}


def ev_record(c, iid, cap, args):
    r = c.lab.backend.evaluator_invoke(iid, cap, args, 'check-%s-%s' % (iid, cap))
    return r.get('result') if r.get('status') == 'succeeded' else None


def pattern_peak(points):
    """2theta (deg) of the strongest point of a MiniFlex pattern."""
    pts = [p for p in points or [] if isinstance(p, dict) and isinstance(p.get('intensity_cps'), (int, float))]
    if not pts:
        return None
    best = max(pts, key=lambda p: p['intensity_cps'])
    return best['two_theta_rad'] / DEG


# ------------------------------------------------------------------ checkers
def c_pxrd_quartz(c):
    e = c.ev(MF)
    c.equal('quartz holder in the sample slot', e.get('slot.sample.connection_id'), 'quartz_holder')
    c.equal('scan completed', e.get('scan.status'), 'completed')
    c.near('scan from 20 deg', e.get('scan.start_rad'), 20 * DEG, 1e-6)
    c.near('scan to 30 deg', e.get('scan.stop_rad'), 30 * DEG, 1e-6)
    c.near('0.05 deg steps', e.get('scan.step_rad'), 0.05 * DEG, 1e-9)
    t = c.work_file('xrd.json')
    rec = json.loads(t) if t else {}
    pts = ((rec.get('result') or {}).get('samples')) or []
    c.equal('xrd.json holds the 201 measured points', len(pts), 201)
    peak = pattern_peak(pts)
    c.add('the strongest quartz line (101) near 26.6 deg', peak is not None and abs(peak - 26.64) < 0.3, peak)


def c_pxrd_calcite(c):
    e = c.ev(MF)
    c.equal('calcite holder in the sample slot', e.get('slot.sample.connection_id'), 'calcite_holder')
    c.equal('scan completed', e.get('scan.status'), 'completed')
    peak = pattern_peak(e.get('measurement.pattern'))
    c.add('the calcite (104) line near 29.4 deg was measured', peak is not None and abs(peak - 29.4) < 0.3, peak)


def c_scxrd_index(c):
    rid = c.work_file('index.txt')
    rec = (ev_record(c, XT, 'measurement.result', {'record_id': rid, 'max_values': 16}) or {}).get('record') if rid else None
    c.add('index.txt names an indexing record', bool(rid) and rid.startswith('idx'), rid)
    c.add('that indexing run completed', bool(rec) and rec.get('kind') == 'indexing' and rec.get('status') == 'completed', (rec or {}).get('status'))
    c.equal('the X-rays are on', c.ev(XT).get('xray.state'), 'on')


def c_saxs(c):
    e = c.ev(SX)
    c.equal('lysozyme_5 in the capillary', e.get('holder.sample_id'), 'lysozyme_5')
    lst = ev_record(c, SX, 'records.list', {}) or {}                      # the last-record slots are per caller: ask the instrument
    recs = lst.get('records') or []
    rec = ev_record(c, SX, 'acquisition.result', {'record_id': recs[-1]}) if recs else None
    c.add('an exposure of lysozyme_5 was recorded', bool(rec) and rec.get('sample_label') == 'lysozyme_5', recs)
    c.equal('2 frames', (rec or {}).get('frames'), 2)
    c.add('a reduced curve exists', bool(lst.get('reduced')), lst.get('reduced'))


def c_saxs_loaded(c):
    e = c.ev(SX)
    c.equal('lysozyme_5 in a capillary', e.get('holder.sample_id'), 'lysozyme_5')
    c.equal('capillary holder', e.get('holder.type'), 'capillary_1mm')
    c.add('flight path evacuated again', bool(e.get('vacuum.ready')), e.get('vacuum.pressure_indicated_pa'))


def c_xps(c):
    e = c.ev(XPS)
    rid = c.work_file('xps.txt')
    rec = ev_record(c, XPS, 'acquisition.result', {'record_id': rid}) if rid else None
    names = [s.get('name') for s in (rec or {}).get('spectra') or []]
    c.equal('holder H1 on the analysis stage', e.get('stage.holder_id'), 'H1')
    c.add('xps.txt names a completed spectrum record', bool(rec) and rec.get('status') == 'completed', rid)
    c.add('survey, C 1s and Si 2p regions measured', {'survey', 'C1s', 'Si2p'} <= set(names), names)
    q = c.work_file('quant.json')
    rows = (json.loads(q).get('result') or {}).get('results') if q else None
    c.add('quant.json holds a C / Si quantification', bool(rows) and {r.get('element') for r in rows} >= {'C', 'Si'}, rows)


def c_se(c):
    e = c.ev(SE)
    t = e.get('result.last_thickness_m')
    got = c.number_file('film.txt')
    c.add('a fit was done', isinstance(t, float), t)
    c.near('film.txt reports the fitted thickness in nm', got, (t or 0) * 1e9, 0.5)
    c.near('the film is about 122 nm (PMMA 120 nm + native oxide)', got, 121.8, 4.0)


def c_xt_pre(c):
    e = c.ev(XT)
    rid = e.get('result.last_record_id') or ''
    c.add('a pre-experiment was recorded', rid.startswith('pre'), rid)
    c.equal('the X-rays are on', e.get('xray.state'), 'on')


# ------------------------------------------------------------------ gold solutions
GOLD_PXRD = '''chemlab run rigaku_miniflex600_external_cooling load quartz_holder
chemlab run rigaku_miniflex600_external_cooling source on --kv 40kV --ma 15mA
chemlab run rigaku_miniflex600_external_cooling scan from=20deg to=30deg step=0.05deg time=0.5s --wait --max 1h
chemlab run rigaku_miniflex600_external_cooling result --save-to xrd.json'''

GOLD_PXRD_RECOVER = r'''M=rigaku_miniflex600_external_cooling
chemlab run $M load calcite_holder --json > l.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "OCCUPIED"' l.json >/dev/null; then
  jq -r .error.fix l.json
  chemlab run $M unload
  chemlab run $M load calcite_holder
fi
chemlab run $M scan from=28deg to=30deg step=0.02deg time=1s --wait --max 1h --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "SOURCE_NOT_SET"' s.json >/dev/null; then
  jq -r .error.fix s.json
  chemlab run $M source on --kv 40kV --ma 15mA
  chemlab run $M scan from=28deg to=30deg step=0.02deg time=1s --wait --max 1h
fi'''

GOLD_SCXRD = '''X=rigaku_xtalabsynergy
chemlab run $X load aspirin_01
chemlab run $X align --wait
chemlab run $X source on --wait
chemlab run $X scan exposure=1s --wait --max 2h
chemlab run $X analyze what=index --json | jq -r .result.record_id > index.txt; cat index.txt'''

GOLD_SAXS = '''S=antonpaar_saxspace
chemlab run $S load lysozyme_5 --holder capillary_1mm
chemlab run $S vacuum on --wait
chemlab run $S source on --wait
chemlab run $S align --wait
chemlab run $S scan exposure=60s frames=2 --wait
chemlab run $S analyze thickness=1mm'''

GOLD_SAXS_RECOVER = r'''S=antonpaar_saxspace
chemlab run $S load lysozyme_5 --json > l.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_VENTED"' l.json >/dev/null; then
  jq -r .error.fix l.json
  chemlab run $S vacuum off --wait
  chemlab run $S load lysozyme_5
fi
chemlab run $S vacuum on --wait'''

GOLD_XPS = '''K=kratos_axissupra
chemlab run $K load sio2_on_si --wait --max 1h
chemlab run $K source on --wait
chemlab run $K align --wait
chemlab run $K scan region=survey,C1s,Si2p --wait --max 1h
chemlab run $K result --json | jq -r .record_id > xps.txt
chemlab run $K analyze regions=C1s,Si2p --save-to quant.json'''

GOLD_SE = '''E=ja_alphase
chemlab run $E load pmma_on_si
chemlab run $E scan mode=standard --wait
chemlab run $E analyze model=cauchy_on_si
chemlab read $E result.last_thickness_m --json | jq -r '.observations["result.last_thickness_m"].value * 1e9 * 100 | round / 100' > film.txt; cat film.txt'''

GOLD_XT_RECOVER = r'''X=rigaku_xtalabsynergy
chemlab run $X scan exposure=1s --wait --max 2h --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "SOURCE_NOT_READY"' s.json >/dev/null; then
  jq -r .error.fix s.json
  chemlab run $X source on --wait
  chemlab run $X scan exposure=1s --wait --max 2h
fi'''

MF_QUARTZ_IN = [op('access.set_door_angle', {'angle_rad': 1.5}), op('slot.set_sample', {'connection_id': 'quartz_holder'}),
                op('access.set_door_angle', {'angle_rad': 0.0})]
SX_PUMPED = [{'advance_s': 10.0}, op('vacuum.set_pump', {'on': True}), {'advance_s': 300.0}]
XT_MOUNTED = [op('door.open', {'door': 'left', 'angle_rad': 1.5}), op('motion_enable.hold', {'held': True}), op('goniometer.mount_position', {}),
              {'advance_s': 20.0}, op('crystal.mount', {'sample_id': 'aspirin_01'}), {'advance_s': 10.0}, op('crystal.center', {}), {'advance_s': 100.0}]

TASKS = [
    {'id': 'st01_pxrd_quartz', 'kind': 'multi-step + long action + record', 'instruments': [MF], 'check': c_pxrd_quartz,
     'instruction': 'Record a powder pattern of the quartz sample holder (quartz_holder) on the MiniFlex (rigaku_miniflex600_external_cooling) from 20 '
                    'to 30 deg 2theta in 0.05 deg steps, 0.5 s per step, at 40 kV / 15 mA, and save the pattern as xrd.json.', 'gold': GOLD_PXRD},
    {'id': 'st02_pxrd_recover', 'kind': 'recovery: slot occupied, then tube not set', 'instruments': [MF], 'config': with_steps(MF, MF_QUARTZ_IN),
     'check': c_pxrd_calcite,
     'instruction': 'Scan the calcite sample holder (calcite_holder) on the MiniFlex (rigaku_miniflex600_external_cooling) from 28 to 30 deg 2theta in '
                    '0.02 deg steps, 1 s per step, at 40 kV / 15 mA.', 'gold': GOLD_PXRD_RECOVER},
    {'id': 'st03_scxrd_index', 'kind': 'long multi-step (mount, centre, X-rays, pre-experiment) + report', 'instruments': [XT], 'check': c_scxrd_index,
     'instruction': 'Mount the aspirin crystal (aspirin_01) on the XtaLAB Synergy (rigaku_xtalabsynergy), centre it, switch the X-rays on, run a '
                    'pre-experiment with 1 s frames and index it. Write the record id of the indexing result into index.txt.', 'gold': GOLD_SCXRD},
    {'id': 'st04_saxs_reduce', 'kind': 'long multi-step (vacuum, X-rays, alignment, exposure, reduction)', 'instruments': [SX], 'check': c_saxs,
     'instruction': 'Measure the lysozyme solution (lysozyme_5) on the SAXSpace (antonpaar_saxspace) in a 1 mm capillary: evacuate, X-rays on, '
                    'auto-align, expose 2 frames of 60 s, then reduce the data with 1 mm sample thickness.', 'gold': GOLD_SAXS},
    {'id': 'st05_saxs_recover_vacuum', 'kind': 'recovery: chamber under vacuum', 'instruments': [SX], 'config': with_steps(SX, SX_PUMPED),
     'check': c_saxs_loaded,
     'instruction': 'Load lysozyme_5 in a 1 mm capillary into the SAXSpace (antonpaar_saxspace) and leave the flight path evacuated.', 'gold': GOLD_SAXS_RECOVER},
    {'id': 'st06_xps_quantify', 'kind': 'long multi-step (load lock, transfer, source, height, spectra) + record', 'instruments': [XPS], 'check': c_xps,
     'instruction': 'Load the SiO2-on-Si film (sio2_on_si) into the AXIS Supra+ (kratos_axissupra), switch the X-ray source on, set the height, '
                    'acquire a survey plus C 1s and Si 2p regions, write the spectrum record id into xps.txt, and save a quantification of C 1s and '
                    'Si 2p as quant.json.', 'gold': GOLD_XPS},
    {'id': 'st07_se_thickness', 'kind': 'multi-step + report', 'instruments': [SE], 'check': c_se,
     'instruction': 'Measure the PMMA-on-Si wafer (pmma_on_si) on the alpha-SE ellipsometer (ja_alphase) in standard mode, fit the Cauchy-on-Si model '
                    'and write the film thickness in nm into film.txt.', 'gold': GOLD_SE},
    {'id': 'st08_scxrd_recover_source', 'kind': 'recovery: X-rays off', 'instruments': [XT], 'config': with_steps(XT, XT_MOUNTED), 'check': c_xt_pre,
     'instruction': 'The aspirin crystal is mounted and centred on the XtaLAB Synergy (rigaku_xtalabsynergy). Run a pre-experiment with 1 s frames.',
     'gold': GOLD_XT_RECOVER},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'structure'
    _t.setdefault('config', world_config)


def main(only=None, noact=True):
    return run_suite('structure', TASKS, only=only, noact=noact)

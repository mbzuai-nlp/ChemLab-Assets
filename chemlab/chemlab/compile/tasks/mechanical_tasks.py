"""Mechanical class acceptance tasks (9): gold solutions use `chemlab run` (plus `chemlab wait` / `jq` in the sandbox); the
checkers read the evaluator view and the instruments' own records (evaluator calls on the host side) and the agent's report
files.  The world (specimens, charges, grips) is the operator's: tasks/mechanical_world.py."""
import itertools

from . import mechanical_world as W
from .common import run_suite

TRB, RST, HIT, AGS, H5, MF, MCR, FT4 = ('antonpaar_trb', 'antonpaar_rst300', 'antonpaar_hit300', 'shimadzu_ags10knxd', 'zwick_hit5p',
                                       'zwick_mflow', 'antonpaar_mcr503', 'freeman_ft4powder')
_n = itertools.count()


def ev_call(c, iid, cap, args=None):
    """An evaluator-side call (host only): the instrument's own record or evaluation, for the checker."""
    r = c.lab.backend.evaluator_invoke(iid, cap, args or {}, 'check-%s-%s-%d' % (iid, cap, next(_n)))
    return r.get('result') if r.get('status') == 'succeeded' else None


def world(extra=None):
    return lambda cfg: W.config(cfg, extra)


# ------------------------------------------------------------------ checkers
def c_tensile(c):
    res = ev_call(c, AGS, 'acquisition.result') or {}
    rows = res.get('rows') or []
    c.equal('test finished', res.get('phase'), 'succeeded')
    c.add('the specimen broke (run to fracture)', c.ev(AGS).get('sample.broken'), res.get('terminal_reason'))
    moving = [r for r in rows if r.get('kind') == 'sample' and r.get('crosshead_m', 0) > 1e-5]
    if len(moving) >= 2:
        a, b = moving[0], moving[-1]
        c.near('crosshead speed 1 mm/min', (b['crosshead_m'] - a['crosshead_m']) / (b['time_s'] - a['time_s']) * 60e3, 1.0, 0.02)
    else:
        c.add('crosshead moved', False, len(moving))
    c.add('force zeroed before the test (zero offset taken)', abs(c.ev(AGS).get('calibration.force_zero_n') or 0) > 0, c.ev(AGS).get('calibration.force_zero_n'))
    peak = max((r['force_n'] for r in rows if r.get('force_n') is not None), default=None)
    c.near('peak.txt = peak force in N (±0.5 N)', c.number_file('peak.txt'), peak if peak is not None else -1, 0.5)


def c_modulus(c):
    res = ev_call(c, AGS, 'acquisition.result') or {}
    rows = [r for r in res.get('rows') or [] if r.get('force_n') is not None]
    c.equal('test finished', res.get('phase'), 'succeeded')
    ref = ev_call(c, AGS, 'analysis.tensile', {'rows': rows, 'area_prior_m2': 4e-6, 'gauge_prior_m': 0.05, 'frame_compliance_prior_m_n': None,
                                               'elastic_fit_min_strain': 0.0002, 'elastic_fit_max_strain': 0.0015}) if len(rows) >= 3 else None
    e = (ref or {}).get('elastic_modulus_pa')
    c.add('a modulus can be fitted from the run', e is not None, e)
    c.near('modulus.txt = elastic modulus in GPa for 4 mm² / 50 mm (±1 %)', c.number_file('modulus.txt'), (e or 0) / 1e9, abs(e or 1e9) / 1e9 * 0.01)


def c_indent(c):
    e = c.ev(HIT)
    res = ev_call(c, HIT, 'acquisition.result') or {}
    c.equal('two indents completed', res.get('completed_indents'), 2)
    c.near('peak load 5 mN', max((r['force_n'] for r in res.get('rows') or [] if r.get('force_n') is not None), default=0), 0.005, 0.0002)
    c.add('specimen clamped', e.get('clamp.locked'))
    un = [r for r in res.get('rows') or [] if r.get('phase') == 'unloading' and r.get('indent_index') == 0 and r.get('force_n') is not None
          and r.get('depth_m') is not None]
    tmpl = c.lab.backend.instruments[HIT].contract['capabilities']['analysis.oliver_pharr']['example_arguments']['calibration']
    ref = ev_call(c, HIT, 'analysis.oliver_pharr', {'load_n': [r['force_n'] for r in un], 'depth_m': [r['depth_m'] for r in un], 'calibration': tmpl}) if len(un) >= 3 else None
    h = (ref or {}).get('hardness_pa')
    c.near('hardness.txt = Oliver-Pharr hardness of the first indent in GPa (±1 %)', c.number_file('hardness.txt'), (h or 0) / 1e9, abs(h or 1e9) / 1e9 * 0.01)


def c_scratch(c):
    res = ev_call(c, RST, 'acquisition.result') or {}
    c.equal('last pass is a finished scratch', (res.get('pass_kind'), res.get('phase')), ('scratch', 'succeeded'))
    m = res.get('method_json') or ''
    c.add('progressive 1 N -> 30 N over 3 mm from x = 10 mm', all(s in m.replace(' ', '') for s in ('"initial_force_n":1.0', '"final_force_n":30.0', '"length_m":0.003', '"start_x_m":0.01')), m[:200])
    lc = ((res.get('critical') or {}).get('critical_load_n'))
    c.add('a critical load was found', lc is not None, res.get('critical'))
    c.near('lc.txt = critical load in N (±0.05 N)', c.number_file('lc.txt'), lc if lc is not None else -1, 0.05)


def c_pin_on_disk(c):
    e = c.ev(TRB)
    res = ev_call(c, TRB, 'acquisition.result') or {}
    c.equal('test finished', res.get('phase'), 'succeeded')
    c.near('60 s test', res.get('elapsed_s'), 60.0, 0.5)
    c.near('normal load 5 N', e.get('normal.setting_n'), 5.0, 1e-9)
    c.near('track radius 8 mm', e.get('radius.position_m'), 0.008, 1e-9)
    c.add('lid closed', not e.get('lid.open'))
    mus = [r['friction_coefficient'] for r in res.get('rows') or [] if r.get('friction_coefficient') is not None]
    c.near('mu.txt = mean friction coefficient (±0.002)', c.number_file('mu.txt'), sum(mus) / len(mus) if mus else -1, 0.002)


def c_calibrate_pendulum(c):
    e = c.ev(H5)
    c.add('friction calibration valid', e.get('calibration.valid'))
    c.near('torque.txt = friction torque in N m (±1e-6)', c.number_file('torque.txt'), e.get('calibration.friction_torque_nm') or -1, 1e-6)


def c_impact(c):
    e = c.ev(H5)
    res = ev_call(c, H5, 'acquisition.result') or {}
    c.add('the bar was struck', e.get('sample.spent'))
    c.equal('impact swing valid', (res.get('mode'), res.get('validity')), ('impact', 'valid'))
    c.near('energy.txt = absorbed (corrected) energy in J (±0.001)', c.number_file('energy.txt'), res.get('corrected_energy_j') or -1, 0.001)


def c_mfr(c):
    res = ev_call(c, MF, 'acquisition.result') or {}
    c.equal('valid melt flow test', (res.get('phase'), res.get('validity')), ('succeeded', 'valid'))
    c.near('at 250 °C', res.get('test_temperature_k'), 523.15, 0.01)
    flow = res.get('flow') or {}
    c.near('window 40 -> 10 mm', (flow.get('window_positions_m') or [0, 0])[0], 0.04, 0.0005)
    c.near('mfr.txt = MFR in g/10 min (±0.05)', c.number_file('mfr.txt'), flow.get('mfr_g_10min') or -1, 0.05)


def c_viscosity(c):
    e = c.ev(MCR)
    res = ev_call(c, MCR, 'acquisition.result') or {}
    c.equal('rotation measurement finished', res.get('phase'), 'succeeded')
    rows = res.get('rows') or []
    c.add('shear rate 100 1/s', bool(rows) and all(abs((r.get('shear_rate_s_inv') or 0) - 100.0) < 1e-6 for r in rows), rows[:1])
    c.near('gap 1 mm', e.get('geometry.gap_m'), 0.001, 1e-9)
    eta = res.get('mean_apparent_viscosity_pa_s')
    c.near('eta.txt = mean viscosity in mPa s (±0.01)', c.number_file('eta.txt'), (eta or -1) * 1000, 0.01)


def c_powder(c):
    e = c.ev(FT4)
    res = ev_call(c, FT4, 'acquisition.result') or {}
    c.equal('dynamic test finished', (res.get('mode'), res.get('phase')), ('dynamic', 'succeeded'))
    c.add('vessel clamped, door closed', e.get('clamp.locked') and (e.get('door.angle_rad') or 0) < 1e-9)
    downs = [p for p in res.get('points') or [] if p.get('kind') == 'down']
    c.equal('3 measurement cycles', len(downs), 3)
    rows = [r for r in res.get('rows') or [] if r.get('segment') == 'measure_down']
    cyc = sorted({r['cycle'] for r in rows})
    first = [r for r in rows if cyc and r['cycle'] == cyc[0]]
    ref = ev_call(c, FT4, 'analysis.energy', {k: [r[k] for r in first] for k in ('position_m', 'rotation_rad', 'force_n', 'torque_nm')}) if len(first) >= 2 else None
    c.near('energy.txt = flow energy of the first measurement traverse in J (±0.001)', c.number_file('energy.txt'), (ref or {}).get('energy_j') or -1, 0.001)


# ------------------------------------------------------------------ gold solutions
GOLD_TENSILE = r'''chemlab run shimadzu_ags10knxd zero
chemlab run shimadzu_ags10knxd method speed=1mm/min target=3mm time=300s
job=$(chemlab run shimadzu_ags10knxd start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run shimadzu_ags10knxd result --json | jq -r '.summary["peak force"]' > peak.txt
cat peak.txt'''

GOLD_MODULUS = r'''chemlab run shimadzu_ags10knxd method speed=1mm/min target=3mm time=300s
job=$(chemlab run shimadzu_ags10knxd start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run shimadzu_ags10knxd analyze area=4mm2 gauge=50mm --json | jq -r '.summary["elastic modulus"] / 1e9' > modulus.txt
cat modulus.txt'''

GOLD_INDENT = r'''chemlab run antonpaar_hit300 power on
chemlab run antonpaar_hit300 move 20mm --wait
chemlab run antonpaar_hit300 contact on --json > c.json; rc=$?
jq -r '.error.code + ": " + .error.fix' c.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_CLAMPED"' c.json >/dev/null; then
  chemlab run antonpaar_hit300 clamp on
  chemlab run antonpaar_hit300 contact on
fi
chemlab run antonpaar_hit300 method load=5mN positions=[0.02,0.022]
job=$(chemlab run antonpaar_hit300 start --json | jq -r .job)
chemlab wait "$job" --max 30min
chemlab run antonpaar_hit300 analyze --index 0 --json | jq -r '.summary.hardness / 1e9' > hardness.txt
cat hardness.txt'''

GOLD_SCRATCH = r'''I=antonpaar_rst300
chemlab run $I power on && chemlab run $I clamp on
chemlab run $I move 10mm --wait && chemlab run $I contact on
chemlab run $I method mode=prescan start=10mm length=3mm
job=$(chemlab run $I start --json | jq -r .job); chemlab wait "$job" --max 10min
chemlab run $I contact off && chemlab run $I move 10mm --wait && chemlab run $I contact on
chemlab run $I method mode=scratch start=10mm length=3mm load-start=1N load-end=30N
job=$(chemlab run $I start --json | jq -r .job); chemlab wait "$job" --max 10min
chemlab run $I result --json | jq -r '.summary["critical load"]' > lc.txt
cat lc.txt'''

GOLD_PIN = r'''I=antonpaar_trb
chemlab run $I power on
chemlab run $I zero --json > z.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_CLOSED"' z.json >/dev/null; then
  jq -r '.error.fix' z.json
  chemlab run $I open lid
  chemlab run $I zero
fi
chemlab run $I method load=5N speed=100rpm time=60s
chemlab run $I move 8mm
chemlab run $I contact on
chemlab run $I close lid
job=$(chemlab run $I start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run $I result --json | jq -r '.summary["mean friction coefficient"]' > mu.txt
cat mu.txt'''

GOLD_CAL = r'''job=$(chemlab run zwick_hit5p calibrate --json | jq -r .job)
chemlab wait "$job" --max 2min
chemlab run zwick_hit5p status --json | jq -r '.state.friction_torque_n_m' > torque.txt
cat torque.txt'''

GOLD_IMPACT = r'''chemlab run zwick_hit5p status
job=$(chemlab run zwick_hit5p start --json | jq -r .job)
chemlab wait "$job" --max 2min
chemlab run zwick_hit5p result --json | jq -r '.summary["absorbed energy"]' > energy.txt
cat energy.txt'''

GOLD_MFR = r'''I=zwick_mflow
chemlab run $I set-temp 250C --wait --max 1h
chemlab run $I method window=40mm..10mm time=300s
for i in $(seq 1 20); do
  chemlab run $I start --json > s.json && break
  code=$(jq -r .error.code s.json); echo "start: $code"
  [ "$code" = PREHEAT_NOT_COMPLETE ] || [ "$code" = TEMPERATURE_NOT_STABLE ] || exit 1
  chemlab wait 30
done
chemlab wait "$(jq -r .job s.json)" --max 20min
chemlab run $I result --json | jq -r '.summary["MFR (g/10min)"]' > mfr.txt
cat mfr.txt'''

GOLD_VISC = r'''I=antonpaar_mcr503
chemlab run $I method mode=rotation shear-rate=100 time=2s interval=10ms
chemlab run $I set-temp 25C --wait
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "GAP_FILL_MISMATCH"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab run $I move 1mm --wait
  chemlab run $I start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 5min
chemlab run $I result --json | jq -r '.summary["mean viscosity (Pa s)"] * 1000' > eta.txt
cat eta.txt'''

GOLD_POWDER = r'''I=freeman_ft4powder
chemlab run $I power on
chemlab run $I close door
chemlab run $I method cycles=3
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "CLAMP_REQUIRED"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab run $I open door
  chemlab run $I clamp on
  chemlab run $I close door
  chemlab run $I start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 10min
chemlab run $I analyze --index 0 --json | jq -r '.summary.energy' > energy.txt
cat energy.txt'''

TRB_LID_CLOSED = {TRB: [{'capability': 'lid.set', 'arguments': {'opened': False}, 'evaluator': True}]}

TASKS = [
    {'id': 'me01_tensile_peak', 'kind': 'long action (job) + report', 'instruments': [AGS], 'config': world(), 'check': c_tensile,
     'instruction': 'A steel tensile specimen is clamped in the Shimadzu AGS-10kNXD (shimadzu_ags10knxd). Zero the force, then pull it at '
                    'a crosshead speed of 1 mm/min (up to 3 mm travel, 300 s at most) until it breaks. Write the peak force in N into '
                    'peak.txt.', 'gold': GOLD_TENSILE},
    {'id': 'me02_tensile_modulus', 'kind': 'evaluation with specimen priors', 'instruments': [AGS], 'config': world(), 'check': c_modulus,
     'instruction': 'Pull the specimen in the AGS-10kNXD (shimadzu_ags10knxd) at 1 mm/min to fracture (3 mm travel, 300 s at most). The '
                    'specimen has a 4 mm² cross-section and a 50 mm gauge length. Write its elastic modulus in GPa into modulus.txt.',
     'gold': GOLD_MODULUS},
    {'id': 'me03_indent_recover_clamp', 'kind': 'recovery: specimen not clamped', 'instruments': [HIT], 'config': world(), 'check': c_indent,
     'instruction': 'On the Anton Paar HIT 300 nanoindenter (antonpaar_hit300), make two 5 mN indents at x = 20 mm and 22 mm and write '
                    'the Oliver-Pharr hardness of the first indent in GPa into hardness.txt.', 'gold': GOLD_INDENT},
    {'id': 'me04_scratch_lc', 'kind': 'multi-step protocol (prescan + scratch)', 'instruments': [RST], 'config': world(), 'check': c_scratch,
     'instruction': 'On the RST 300 scratch tester (antonpaar_rst300), make a progressive scratch from 1 N to 30 N over 3 mm starting at '
                    'x = 10 mm (with the prescan of that path the instrument needs first) and write the critical load the instrument '
                    'reports in N into lc.txt.', 'gold': GOLD_SCRATCH},
    {'id': 'me05_pin_on_disk_recover_lid', 'kind': 'recovery: lid closed', 'instruments': [TRB], 'config': world(TRB_LID_CLOSED),
     'check': c_pin_on_disk,
     'instruction': 'Run a 60 s pin-on-disk test on the TRB3 tribometer (antonpaar_trb): 5 N normal load, 100 rpm, 8 mm track radius, '
                    'friction sensor zeroed first. Write the mean friction coefficient into mu.txt.', 'gold': GOLD_PIN},
    {'id': 'me06_pendulum_calibration', 'kind': 'long action (job) + report', 'instruments': [H5], 'config': world(), 'check': c_calibrate_pendulum,
     'instruction': 'Run the empty friction-calibration swing of the ZwickRoell HIT5P pendulum (zwick_hit5p) and write the friction torque '
                    'it found (N m) into torque.txt.', 'gold': GOLD_CAL},
    {'id': 'me07_charpy_impact', 'kind': 'single test + report', 'instruments': [H5], 'config': world(W.READY), 'check': c_impact,
     'instruction': 'The HIT5P (zwick_hit5p) is calibrated and a notched bar lies on the supports. Strike it and write the absorbed energy in '
                    'J into energy.txt.', 'gold': GOLD_IMPACT},
    {'id': 'me08_mfr_recover_preheat', 'kind': 'recovery: preheat not complete', 'instruments': [MF], 'config': world(), 'check': c_mfr,
     'instruction': 'The barrel of the Mflow (zwick_mflow) is charged and loaded with 2.16 kg. Measure the melt flow rate at 250 °C with the '
                    'measuring window from 40 mm to 10 mm piston position (test up to 300 s) and write the MFR in g/10 min into mfr.txt.',
     'gold': GOLD_MFR},
    {'id': 'me09_viscosity_recover_gap', 'kind': 'recovery: gap does not match the sample', 'instruments': [MCR], 'config': world(),
     'check': c_viscosity,
     'instruction': 'Water was loaded on the MCR 503 rheometer (antonpaar_mcr503) for a 1 mm gap. Measure it at 25 °C in rotation at a shear '
                    'rate of 100 1/s for 2 s (10 ms sampling) and write the mean viscosity in mPa·s into eta.txt.', 'gold': GOLD_VISC},
    {'id': 'me10_powder_recover_clamp', 'kind': 'recovery: vessel not clamped', 'instruments': [FT4], 'config': world(), 'check': c_powder,
     'instruction': 'Run the FT4 powder rheometer (freeman_ft4powder) dynamic test on the loaded powder with 3 measurement cycles and write '
                    'the flow energy of the first measurement traverse in J into energy.txt.', 'gold': GOLD_POWDER},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'mechanical'


def main(only=None, noact=True):
    return run_suite('mechanical', TASKS, only=only, noact=noact)

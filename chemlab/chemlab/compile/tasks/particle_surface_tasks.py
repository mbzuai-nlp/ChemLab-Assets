"""particle_surface acceptance tasks (10): gold solutions use `chemlab run` (and the generic `chemlab wait`); the checkers read
the evaluator view and, for the reported figures, the instrument's own result record read on the evaluator side.  World set-up
(the operator's part: liquids, gases, the sample in the holder) is tasks/particle_surface_world.py.
"""
import math

from . import particle_surface_world as W
from .common import run_suite

K0 = 273.15


def world(fn, **kw):
    return lambda lab: fn(lab.backend, **kw)


def ev_result(c, iid, cap, args=None):
    """The instrument's result record, read on the evaluator side (no state change)."""
    r = c.lab.backend.evaluator_invoke(iid, cap, dict(args or {}), 'check-%s-%s' % (iid, cap))
    return r.get('result') if isinstance(r.get('result'), dict) else {}


def phase(c, iid, slot='acquisition.phase', want='succeeded'):
    return c.equal('%s %s' % (iid, slot), c.ev(iid).get(slot), want)


# ------------------------------------------------------------------ checkers
def c_dls(c):
    e = c.ev(W.DLS)
    phase(c, W.DLS)
    c.near('method temperature 25 °C', e.get('temperature.target_k'), K0 + 25, 1e-6)
    z = (ev_result(c, W.DLS, 'process.measure').get('dls') or {}).get('z_average_m')
    c.add('a DLS result exists', z is not None, z)
    c.near('zavg.txt = the Z-average in nm (±0.5 nm)', c.number_file('zavg.txt'), (z or 0) * 1e9, 0.5)


def c_turbiscan(c):
    e = c.ev(W.TURB)
    phase(c, W.TURB)
    c.near('set point 30 °C', e.get('thermal.target_k'), K0 + 30, 1e-6)
    res = ev_result(c, W.TURB, 'acquisition.result')
    prof = res.get('profiles') or []
    gaps = [round(b['start_time_s'] - a['start_time_s'], 3) for a, b in zip(prof, prof[1:])]
    c.add('scans every 10 s for 60 s (6 profiles 10 s apart)', len(prof) == 6 and all(abs(g - 10.0) < 0.2 for g in gaps), (len(prof), gaps))
    c.near('tsi.txt = the TSI of the result (±0.001)', c.number_file('tsi.txt'), res.get('tsi') if res.get('tsi') is not None else -1, 1e-3)


def c_autosorb(c):
    e = c.ev(W.ASORB)
    c.equal('station 1 measured', e.get('station0.phase'), 'succeeded')
    bet = e.get('station0.bet_m2_kg')
    c.add('a BET area exists', isinstance(bet, (int, float)), bet)
    c.near('bet.txt = BET in m²/g (±1 %)', c.number_file('bet.txt'), (bet or 0) / 1000.0, abs(bet or 0) / 1000.0 * 0.01 + 1e-9)


def c_ultrapyc(c):
    e = c.ev(W.UPYC)
    phase(c, W.UPYC)
    c.near('sample mass entered: 50 g', e.get('sample.entered_mass_kg'), 0.05, 1e-9)
    rho = ev_result(c, W.UPYC, 'process.measure').get('mean_density_kg_m3')
    c.add('a density result exists', rho is not None, rho)
    c.near('density.txt = mean density in g/cm³ (±0.001)', c.number_file('density.txt'), (rho or 0) / 1000.0, 1e-3)


def c_sigma(c):
    e = c.ev(W.SIGMA)
    phase(c, W.SIGMA)
    c.add('balance tared', e.get('balance.tared'))
    t = ev_result(c, W.SIGMA, 'acquisition.result').get('tension_n_m')
    truth = e.get('truth.surface_tension_n_m')
    c.near('measured tension close to the liquid\'s true tension (±1 mN/m)', t, truth if truth is not None else 0.02245, 1e-3)
    c.near('tension.txt in mN/m (±0.05)', c.number_file('tension.txt'), (t or 0) * 1e3, 0.05)


def c_dvs(c):
    e = c.ev(W.DVS)
    phase(c, W.DVS)
    c.near('method / chamber at 25 °C', e.get('thermal.target_k'), K0 + 25, 1e-6)
    res = ev_result(c, W.DVS, 'acquisition.result')
    pts = res.get('points') or []
    c.add('two RH steps measured (10 %, 40 %)', [round(p.get('target_relative_humidity', -1), 3) for p in pts] == [0.1, 0.4],
          [p.get('target_relative_humidity') for p in pts])
    c.near('mass.txt = mass at the last step in mg (±0.001)', c.number_file('mass.txt'), (pts[-1]['mass_kg'] * 1e6) if pts else -1, 1e-3)


def c_oxtran(c):
    e = c.ev(W.OXTR)
    c.equal('cell A test finished', e.get('cell_a.phase'), 'succeeded')
    res = ev_result(c, W.OXTR, 'test.result', {'cell': 'A'})
    c.equal('3 exams', res.get('exam_count'), 3)
    c.near('otr.txt = OTR in cm³/(m²·day) (±0.05)', c.number_file('otr.txt'), res.get('final_otr_cc_m2_day') if res.get('final_otr_cc_m2_day') is not None else -1, 0.05)


def c_porolux(c):
    phase(c, W.PLUX)
    res = ev_result(c, W.PLUX, 'acquisition.result')
    c.add('a wet scan was measured (the sample was wetted)', len(res.get('wet_points') or []) > 0, len(res.get('wet_points') or []))
    d = (res.get('analysis') or {}).get('mean_flow_diameter_m')
    c.add('a mean flow pore size exists', d is not None, d)
    c.near('mfp.txt = mean flow pore size in µm (±0.01)', c.number_file('mfp.txt'), (d or 0) * 1e6, 0.01)


def c_theta(c):
    e = c.ev(W.THETA)
    phase(c, W.THETA)
    c.add('camera calibrated', e.get('camera.calibrated'))
    rows = ev_result(c, W.THETA, 'acquisition.result').get('rows') or []
    a = rows[-1]['mean_rad'] * 180 / math.pi if rows else None
    c.add('a contact angle was measured', a is not None, a)
    c.near('angle.txt = mean contact angle in degrees (±0.1)', c.number_file('angle.txt'), a if a is not None else -1, 0.1)


def c_mediuml(c):
    e = c.ev(W.MEDL)
    phase(c, W.MEDL)
    c.near('compressed to 140 cm²', e.get('area.indicated_m2'), 0.014, 1e-6)
    rows = ev_result(c, W.MEDL, 'acquisition.result').get('rows') or []
    c.add('a film was spread (surface pressure above 10 mN/m at the end)', bool(rows) and (rows[-1].get('surface_pressure_n_m') or 0) > 0.01,
          rows[-1].get('surface_pressure_n_m') if rows else None)
    c.near('pressure.txt = final surface pressure in mN/m (±0.05)', c.number_file('pressure.txt'),
           rows[-1]['surface_pressure_n_m'] * 1e3 if rows else -1, 0.05)
    iso = c.work_file('iso.json') or ''
    c.add('iso.json holds the isotherm evaluation', '"model": "isotherm"' in iso or '"isotherm"' in iso, iso[:80])


# ------------------------------------------------------------------ gold solutions
GOLD_DLS = r'''chemlab run antonpaar_litesizerdls power on
chemlab run antonpaar_litesizerdls method temp=25C
chemlab run antonpaar_litesizerdls start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "WARMING_UP"' s.json >/dev/null; then
  chemlab wait 360
  chemlab run antonpaar_litesizerdls start --json > s.json
fi
job=$(jq -r .job s.json)
chemlab wait "$job" --max 30min
chemlab run antonpaar_litesizerdls result --json | jq -r '.figures["dls.z_average_m"] * 1e9' > zavg.txt
cat zavg.txt'''

GOLD_TURB = r'''chemlab run microtrac_turbiscanlab power on
chemlab run microtrac_turbiscanlab load
chemlab run microtrac_turbiscanlab set-temp 30C --wait --max 1h
chemlab run microtrac_turbiscanlab method time=60s interval=10s
job=$(chemlab run microtrac_turbiscanlab start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run microtrac_turbiscanlab result --json | jq -r '.figures.tsi' > tsi.txt
cat tsi.txt'''

GOLD_ASORB = r'''I=antonpaar_autosorb6100
chemlab run $I load
chemlab run $I calibrate pressure --cell 1 reference=0,1000mbar measured=0,1000mbar
chemlab run $I calibrate volume --cell 1 warm_temperature_k=308.15 warm_dosed_mol=0.00039030457583950266 warm_pressure_pa=50000 cold_dosed_mol=0.0009761416063934054 cold_pressure_pa=50000
chemlab run $I method --cell 1 mass=100mg
job=$(chemlab run $I start --cell 1 --json | jq -r .job)
chemlab wait "$job" --max 2h
chemlab run $I result --cell 1 --json | jq -r '.figures["stations[0].bet.area_m2_kg"] / 1000' > bet.txt
cat bet.txt'''

GOLD_UPYC = r'''I=antonpaar_ultrapyc5000
chemlab run $I load
chemlab run $I calibrate volume blank_ratio=1 sphere_ratio=0.8 sphere_volume_m3=1e-5
chemlab run $I start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NO_MASS"' s.json >/dev/null; then
  chemlab run $I method mass=50g
  chemlab run $I start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 1h
chemlab run $I result --json | jq -r '.figures.mean_density_kg_m3 / 1000' > density.txt
cat density.txt'''

GOLD_SIGMA = r'''I=biolin_sigma700
chemlab run $I power on
chemlab run $I load
chemlab run $I start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_TARED"' s.json >/dev/null; then
  chemlab run $I prepare
  chemlab run $I start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 10min
chemlab run $I result --json | jq -r '.figures.tension_n_m * 1000' > tension.txt
cat tension.txt'''

GOLD_DVS = r'''I=surface_dvsresolution
chemlab run $I power on
chemlab run $I load
chemlab run $I set-temp 25C
chemlab run $I method temp=25C steps=10%,40% time=10s
chemlab run $I atmosphere rh=10% gas=on
job=$(chemlab run $I start --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run $I result --json | jq -r '.figures["points[-1].mass_kg"] * 1e6' > mass.txt
cat mass.txt'''

GOLD_OXTR = r'''I=ametek_oxtran
chemlab run $I load --cell A
chemlab run $I set-temp 23C
chemlab run $I atmosphere rh=0%
chemlab run $I method --cell A mode=standard runs=3 time=30min conditioning=0h
job=$(chemlab run $I start --cell A --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 40h
chemlab run $I result --cell A --json | jq -r '.figures.final_otr_cc_m2_day' > otr.txt
cat otr.txt'''

GOLD_PLUX = r'''I=porometer_poroluxrevo
chemlab run $I power on
chemlab run $I load
chemlab run $I prepare --json > p.json; rc=$?
jq -r '.error.code + ": " + .error.fix' p.json
if [ $rc -eq 3 ] && jq -e '.error.code == "HOLDER_CLOSED"' p.json >/dev/null; then
  chemlab run $I unload && chemlab run $I prepare && chemlab run $I load
fi
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "GAS_CLOSED"' s.json >/dev/null; then
  chemlab run $I atmosphere gas=on
  chemlab run $I start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 30min
chemlab run $I result --json | jq -r '.figures["analysis.mean_flow_diameter_m"] * 1e6' > mfp.txt
cat mfp.txt'''

GOLD_THETA = r'''I=biolin_thetaflex
chemlab run $I power on
chemlab run $I calibrate camera reference=1mm measured=566.86
chemlab run $I load --volume 0.2uL
job=$(chemlab run $I start --json | jq -r .job)
chemlab wait "$job" --max 5min
chemlab run $I result --json | jq -r '.figures["rows[-1].mean_rad"] * 180 / 3.141592653589793' > angle.txt
cat angle.txt'''

GOLD_MEDL = r'''I=biolin_mediuml
chemlab run $I prepare
chemlab run $I load --volume 50uL
chemlab wait 30
chemlab run $I method mode=compression area=140cm2 time=60s
job=$(chemlab run $I start --json | jq -r .job)
chemlab wait "$job" --max 30min
chemlab run $I result --json | jq -r '.figures["rows[-1].surface_pressure_n_m"] * 1000' > pressure.txt
chemlab run $I analyze isotherm --save-to iso.json
cat pressure.txt'''

TASKS = [
    {'id': 'ps01_dls_warmup', 'kind': 'recovery: laser still warming up', 'instruments': [W.DLS], 'world': world(W.dls), 'check': c_dls,
     'instruction': 'The cuvette with the nanoparticle dispersion is in the Litesizer DLS (antonpaar_litesizerdls), which is switched off. '
                    'Measure the particle size at 25 °C and write the Z-average diameter in nm into zavg.txt.', 'gold': GOLD_DLS},
    {'id': 'ps02_turbiscan_tsi', 'kind': 'multi-step: load, temperature, method, job', 'instruments': [W.TURB], 'world': world(W.turbiscan),
     'check': c_turbiscan,
     'instruction': 'The lab operator put a vial of the emulsion into the raised holder of the TURBISCAN LAB (microtrac_turbiscanlab) and left '
                    'the lid open. Measure its stability at 30 °C for 60 s with a scan every 10 s, and write the TSI into tsi.txt.',
     'gold': GOLD_TURB},
    {'id': 'ps03_autosorb_bet', 'kind': 'long action: calibration + station job', 'instruments': [W.ASORB], 'world': world(W.autosorb, stations=(0,)),
     'check': c_autosorb,
     'instruction': 'A degassed sample (100 mg) is mounted on station 1 of the Autosorb 6100 (antonpaar_autosorb6100) and the dewar is filled. '
                    'Enter the station calibrations - pressure transducer: reference 0 and 1000 mbar read as 0 and 1000 mbar; free space: '
                    'warm_temperature_k=308.15, warm_dosed_mol=0.00039030457583950266, warm_pressure_pa=50000, '
                    'cold_dosed_mol=0.0009761416063934054, cold_pressure_pa=50000 - then measure the nitrogen isotherm and write the BET '
                    'surface area in m²/g into bet.txt.', 'gold': GOLD_ASORB},
    {'id': 'ps04_ultrapyc_mass', 'kind': 'recovery: sample mass not entered', 'instruments': [W.UPYC], 'world': world(W.ultrapyc),
     'check': c_ultrapyc,
     'instruction': 'The cup with 50.0 g of aluminium powder is in the open cell of the Ultrapyc 5000 (antonpaar_ultrapyc5000). The cell '
                    'volume calibration is: blank_ratio=1, sphere_ratio=0.8, sphere_volume_m3=1e-5. Measure the skeletal density and write '
                    'it in g/cm³ into density.txt.', 'gold': GOLD_UPYC},
    {'id': 'ps05_sigma_tare', 'kind': 'recovery: balance not tared', 'instruments': [W.SIGMA], 'world': world(W.sigma), 'check': c_sigma,
     'instruction': 'The vessel with the liquid stands in the Sigma 700 force tensiometer (biolin_sigma700), door open, instrument off. Measure '
                    'the surface tension with the Wilhelmy plate and write it in mN/m into tension.txt.', 'gold': GOLD_SIGMA},
    {'id': 'ps06_dvs_sorption', 'kind': 'multi-step: temperature, humidity, gas, method, job', 'instruments': [W.DVS],
     'world': world(W.dvs, tared=True), 'check': c_dvs,
     'instruction': 'The sample pan is loaded in the DVS Resolution (surface_dvsresolution); the balance was tared with the empty pan. Run a '
                    'water sorption measurement at 25 °C with the RH steps 10 % and 40 % (at most 10 s per step) and write the sample mass '
                    'at the last step in mg into mass.txt.', 'gold': GOLD_DVS},
    {'id': 'ps07_oxtran_otr', 'kind': 'long action: many hours, job status and wait', 'instruments': [W.OXTR], 'world': world(W.oxtran),
     'check': c_oxtran,
     'instruction': 'A PET film is mounted in the drawer of cell A of the OX-TRAN 2/22 (ametek_oxtran). Measure its oxygen transmission rate at '
                    '23 °C and 0 % RH in standard mode with 3 exams of 30 min and no conditioning, and write the OTR in cm³/(m²·day) into '
                    'otr.txt.', 'gold': GOLD_OXTR},
    {'id': 'ps08_porolux_recover', 'kind': 'recovery: holder closed for wetting, gas closed', 'instruments': [W.PLUX], 'world': world(W.porolux),
     'check': c_porolux,
     'instruction': 'The membrane disc, the wetting liquid and the nitrogen are in place at the POROLUX Revo (porometer_poroluxrevo), switched '
                    'off. Close the holder, wet the sample and measure the wet/dry curves with the default method; write the mean flow pore '
                    'size in µm into mfp.txt.', 'gold': GOLD_PLUX},
    {'id': 'ps09_theta_drop', 'kind': 'composite load (stage, dispense) + calibration', 'instruments': [W.THETA], 'world': world(W.theta),
     'check': c_theta,
     'instruction': 'The substrate and the probe-liquid syringe are in the Theta Flex (biolin_thetaflex), switched off. The camera scale is '
                    '1 mm = 566.86 pixels. Place a 0.2 µL drop, measure the static contact angle and write the mean angle in degrees into '
                    'angle.txt.', 'gold': GOLD_THETA},
    {'id': 'ps10_trough_isotherm', 'kind': 'multi-step + evaluation saved to a file', 'instruments': [W.MEDL], 'world': world(W.mediuml),
     'check': c_mediuml,
     'instruction': 'The trough (biolin_mediuml) is filled with PBS and the lipid spreading solution is ready. Tare the balance on the clean '
                    'subphase, spread 50 µL, let the solvent evaporate, compress to 140 cm² in 60 s, write the final surface pressure in '
                    'mN/m into pressure.txt and save the isotherm evaluation into iso.json.', 'gold': GOLD_MEDL},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'particle_surface'
    _t.setdefault('config', W.bare)                  # the class world is applied to the bare members (see W.bare)


def main(only=None, noact=True):
    return run_suite('particle_surface', TASKS, only=only, noact=noact)

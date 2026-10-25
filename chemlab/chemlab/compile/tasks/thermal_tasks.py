"""Thermal class acceptance tasks (10): gold solutions use `chemlab run`; the checkers read the evaluator view (true
temperatures where the twin has them) and the agent's report files."""
from .common import run_suite
from .thermal_world import config as world_config

K0 = 273.15
JUL, IKA, FD, VD, NAB, CELL, KBF, CRYO, KS = ('julabo_corio_cd200f', 'ika_plate_rctdigital', 'binder_fd056', 'binder_vd56', 'nabertherm_l9',
                                             'eppendorf_cellxpertc170i', 'binder_kbf115', 'eppendorf_cryocubef570n', 'ika_ks4000')


def world_call(lab, iid, cap, args):
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, int(lab.backend.now() * 10)))
    assert r.get('status') == 'succeeded', (cap, r)


def w_ika_low_limit(lab):
    world_call(lab, IKA, 'safety.set_limit', {'limit_k': K0 + 70})


def w_cell_35(lab):
    world_call(lab, CELL, 'temperature.set', {'setpoint_k': K0 + 35})
    lab.backend.advance(600)


def w_cryo_80(lab):
    world_call(lab, CRYO, 'settings.configure', {'set_point_k': K0 - 80})


# ------------------------------------------------------------------ checkers
def c_julabo_40(c):
    e = c.ev(JUL)
    c.near('set point 40 °C', e['thermal.target_k'], K0 + 40, 1e-6)
    c.add('circulation and control running', e['circulation.running'])
    c.near('bath at 40 °C (±0.5 K)', e['thermal.temperature_k'], K0 + 40, 0.5)


def c_hotplate(c):
    e = c.ev(IKA)
    c.near('set point 60 °C', e['heating.set_k'], K0 + 60, 1e-6)
    c.add('heating on', e['heating.on'])
    c.add('stirring on', e['stirring.on'])
    c.near('stirring 400 rpm', e['stirring.set_rad_s'], 400 * 2 * 3.141592653589793 / 60, 1e-6)


def c_oven(c):
    e = c.ev(FD)
    c.near('set point 105 °C', e['temperature.set_k'], K0 + 105, 1e-6)
    c.near('oven.txt reports about 105 °C (±1.5 K)', c.number_file('oven.txt'), 105.0, 1.5)
    c.add('the oven is at temperature (display within 1.5 K)', abs((e.get('display.temperature') or 0) - 105.0) <= 1.5, e.get('display.temperature'))


def c_ika_limit(c):
    e = c.ev(IKA)
    c.near('set point 90 °C', e['heating.set_k'], K0 + 90, 1e-6)
    c.add('safety limit raised above 90 °C', e['safety.limit_k'] > K0 + 90, e['safety.limit_k'])
    c.add('heating on', e['heating.on'])


def c_vd56(c):
    e = c.ev(VD)
    c.add('switched on', e['power.enabled'])
    c.near('set point 50 °C', e['thermal.target_k'], K0 + 50, 1e-6)
    c.add('temperature control on', e['thermal.control_enabled'])


def c_furnace_job(c):
    e = c.ev(NAB)
    c.near('set point 550 °C', e['thermal.target_k'], K0 + 550, 1e-6)
    c.near('furnace at 550 °C (±3 K)', e['thermal.indicated_temperature_k'], K0 + 550, 3.0)
    jobs = [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == NAB]
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in jobs), [(j.handle, j.state) for j in jobs])


def c_incubator(c):
    e = c.ev(CELL)
    c.near('set point 37.5 °C', e['setpoint.temperature_k'], K0 + 37.5, 1e-6)


def c_freezer(c):
    e = c.ev(CRYO)
    c.near('set point -75 °C', (e.get('settings.current') or {}).get('set_point_k'), K0 - 75, 1e-6)
    c.add('still on', e['power.on'])


def c_chamber(c):
    e = c.ev(KBF)
    c.near('set point 30 °C', (e.get('setpoint.fixed') or {}).get('temperature_k'), K0 + 30, 1e-6)
    c.near('chamber.txt reports 30 °C (±0.3 K)', c.number_file('chamber.txt'), 30.0, 0.3)
    c.near('the chamber is at 30 °C (±0.3 K)', e['temperature.chamber_k'], K0 + 30, 0.3)


def c_shaker(c):
    e = c.ev(KS)
    c.add('switched on', e['power.enabled'])
    c.near('set point 37 °C', e['thermal.target_k'], K0 + 37, 1e-6)
    c.add('heating on', e['heater.enabled'])
    t = c.work_file('shake.txt') or ''
    c.add('shake.txt reports why shaking is impossible (NO_FIXTURE)', 'NO_FIXTURE' in t, t[:80])


GOLD_T01 = r'''chemlab run julabo_corio_cd200f set-temp 40C --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "REMOTE_OFF"' s.json >/dev/null; then
  chemlab run julabo_corio_cd200f remote on
  chemlab run julabo_corio_cd200f set-temp 40C
fi
chemlab run julabo_corio_cd200f heat on
chemlab run julabo_corio_cd200f wait-temp --max 1h --hold 2min'''

GOLD_T04 = r'''chemlab run ika_plate_rctdigital set-temp 90C --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.message' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "ABOVE_SAFETY_LIMIT"' s.json >/dev/null; then
  chemlab run ika_plate_rctdigital safety-limit 120C
  chemlab run ika_plate_rctdigital set-temp 90C
fi
chemlab run ika_plate_rctdigital heat on'''

GOLD_T05 = r'''chemlab run binder_vd56 set-temp 50C; rc=$?
if [ $rc -eq 3 ]; then chemlab run binder_vd56 power on && chemlab run binder_vd56 set-temp 50C; fi
chemlab run binder_vd56 heat on
chemlab run binder_vd56 status'''

GOLD_T06 = r'''chemlab run nabertherm_l9 set-temp 550C
job=$(chemlab run nabertherm_l9 wait-temp --job --max 3h --json | jq -r .job)
echo "job: $job"
chemlab status "$job"
chemlab wait "$job" --max 3h
chemlab run nabertherm_l9 temp'''

GOLD_T09 = r'''chemlab run binder_kbf115 set-temp 30C
chemlab run binder_kbf115 wait-temp --within 0.3K --max 2h
chemlab run binder_kbf115 temp --json | jq -r '.temperature_c * 100 | round / 100' > chamber.txt
cat chamber.txt'''

GOLD_T10 = r'''chemlab run ika_ks4000 set-temp 37C || { chemlab run ika_ks4000 power on && chemlab run ika_ks4000 set-temp 37C; }
chemlab run ika_ks4000 heat on
chemlab run ika_ks4000 stir 150rpm --json > st.json; rc=$?
if [ $rc -ne 0 ]; then jq -r '"shaking not possible: " + .error.code + " - " + .error.message' st.json > shake.txt; fi
cat shake.txt'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'th01_circulator_remote', 'kind': 'recovery: remote control off', 'instruments': [JUL], 'check': c_julabo_40,
     'instruction': 'Bring the bath of the JULABO CORIO circulator (julabo_corio_cd200f) to 40 °C with circulation running, and make sure it '
                    'has reached 40 °C.', 'gold': GOLD_T01},
    {'id': 'th02_hotplate_stir', 'kind': 'multi-step', 'instruments': [IKA], 'check': c_hotplate,
     'instruction': 'On the IKA RCT digital hot plate (ika_plate_rctdigital), heat to 60 °C and stir at 400 rpm.',
     'gold': 'chemlab run ika_plate_rctdigital set-temp 60C && chemlab run ika_plate_rctdigital heat on && chemlab run ika_plate_rctdigital stir 400rpm'},
    {'id': 'th03_oven_dry', 'kind': 'long action (blocking wait) + report', 'instruments': [FD], 'check': c_oven,
     'instruction': 'Set the BINDER FD 56 drying oven (binder_fd056) to 105 °C, wait until it is there, and write the oven temperature in °C '
                    'into oven.txt.',
     'gold': 'chemlab run binder_fd056 set-temp 105C --wait --max 1h && chemlab run binder_fd056 temp --json | jq -r .temperature_c > oven.txt; cat oven.txt'},
    {'id': 'th04_recover_safety_limit', 'kind': 'recovery: above the safety limit', 'instruments': [IKA], 'world': w_ika_low_limit,
     'check': c_ika_limit, 'instruction': 'Heat the IKA hot plate (ika_plate_rctdigital) to 90 °C for an aqueous bath (raising its safety '
                                         'limit to 120 °C is fine for this bath).', 'gold': GOLD_T04},
    {'id': 'th05_recover_power_off', 'kind': 'recovery: instrument switched off', 'instruments': [VD], 'check': c_vd56,
     'instruction': 'Set the BINDER VD 56 vacuum drying oven (binder_vd56) to 50 °C with its temperature control on.', 'gold': GOLD_T05},
    {'id': 'th06_furnace_job', 'kind': 'long action: job id (status, wait)', 'instruments': [NAB], 'check': c_furnace_job,
     'instruction': 'Heat the Nabertherm muffle furnace (nabertherm_l9) to 550 °C. Use a job to follow the heating (do not block on it at '
                    'first), check its status, then wait for it to finish.', 'gold': GOLD_T06},
    {'id': 'th07_incubator', 'kind': 'single step (decimal set point)', 'instruments': [CELL], 'world': w_cell_35, 'check': c_incubator,
     'instruction': 'Set the Eppendorf CellXpert CO2 incubator (eppendorf_cellxpertc170i) to 37.5 °C.',
     'gold': 'chemlab run eppendorf_cellxpertc170i temp && chemlab run eppendorf_cellxpertc170i set-temp 37.5C'},
    {'id': 'th08_freezer', 'kind': 'single step (negative °C)', 'instruments': [CRYO], 'world': w_cryo_80, 'check': c_freezer,
     'instruction': 'The ultra-low freezer (eppendorf_cryocubef570n) should hold -75 °C. Set it.',
     'gold': 'chemlab run eppendorf_cryocubef570n set-temp -75C && chemlab run eppendorf_cryocubef570n status'},
    {'id': 'th09_chamber_report', 'kind': 'long action + report', 'instruments': [KBF], 'check': c_chamber,
     'instruction': 'Set the BINDER KBF 115 climate chamber (binder_kbf115) to 30 °C. When the chamber is within 0.3 K of it, write the '
                    'chamber temperature in °C into chamber.txt.', 'gold': GOLD_T09},
    {'id': 'th10_recover_shaker', 'kind': 'recovery: power off + an impossible step reported', 'instruments': [KS], 'check': c_shaker,
     'instruction': 'Prepare the IKA KS 4000 incubated shaker (ika_ks4000) at 37 °C with heating on, and start shaking at 150 rpm. If shaking '
                    'is not possible, write the reason (the error code) into shake.txt instead.', 'gold': GOLD_T10},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'thermal'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('thermal', TASKS, only=only, noact=noact)

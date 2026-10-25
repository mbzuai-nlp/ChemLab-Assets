"""Evaporation class acceptance tasks (9): gold solutions use `chemlab run` (and `chemlab call` where the class says so); the
checkers read the evaluator view (truth slots included), trusted product reads and the agent's report files."""
import json
import math

from .common import run_suite
from .evaporation_world import config as world

K0 = 273.15
RPM = 2 * math.pi / 60
RV, PUMP, LYO, SPRAY, CPD, MIVAC, PILO, KDL = ('ika_rv10_digital_v', 'vacuubrand_pc3001_vario_select', 'buchi_lyovapor_l250_pro', 'buchi_minispray',
                                               'tousimis_autosamdri815', 'genevac_mivac2', 'pilodist_pilodist104', 'uic_kdl1basic')


def cfg(variants=None, extra=None):
    """The task's lab config: the evaporation world (+ extra operator steps per instrument)."""
    def f(c):
        c = world(c, variants=variants)
        for e in c['instruments']:
            e['setup'] = list(e.get('setup') or []) + list((extra or {}).get(e['id'], []))
        return c
    return f


def product(c, iid):
    r = c.lab.backend.evaluator_invoke(iid, 'product.read', {}, 'check-product-%s' % iid)
    return r.get('result') or {}


def saved(c, name):
    t = c.work_file(name)
    try:
        return json.loads(t) if t else None
    except ValueError:
        return None


# ------------------------------------------------------------------ checkers
def c_rotavap(c):
    e = c.ev(RV)
    c.near('bath set point 40 °C', e['thermal.target_temperature_k'], K0 + 40, 1e-6)
    c.add('bath heating on', e['thermal.enabled'] is True)
    c.add('flask rotating', e['rotation.running'] is True)
    c.near('speed 120 rpm', e['rotation.target_speed_rad_s'], 120 * RPM, 1e-6)
    c.near('timer 30 min', e['timer.setting_s'], 1800, 1e-6)


def c_rotavap_pump(c):
    e, p = c.ev(RV), c.ev(PUMP)
    c.near('bath set point 40 °C', e['thermal.target_temperature_k'], K0 + 40, 1e-6)
    c.add('bath heating on', e['thermal.enabled'] is True)
    c.near('pump set point 175 mbar', p['vacuum.target_pressure_pa'], 17500.0, 1e-6)


def c_lyo_cycle(c):
    e = c.ev(LYO)
    c.equal('cycle complete', e['cycle.phase'], 'complete')
    r = e.get('cycle.recipe') or {}
    c.add('recipe 10 / 30 / 10 min', [r.get('pre_freeze_s'), r.get('primary_drying_s'), r.get('secondary_drying_s')] == [600.0, 1800.0, 600.0], r)
    c.near('shelf set point -20 °C', e['thermal.target_shelf_temperature_k'], K0 - 20, 1e-6)
    c.near('vacuum set point 0.3 mbar', e['vacuum.target_pressure_pa'], 30.0, 1e-6)
    c.equal('ends on schedule', e['cycle.endpoint_mode'], 'schedule_only')


def c_lyo_recover(c):
    e = c.ev(LYO)
    c.add('chamber closed', e['chamber.open'] is False)
    c.add('cycle running', e['cycle.running'] is True, e['cycle.phase'])
    r = e.get('cycle.recipe') or {}
    c.add('recipe 1 h / 12 h / 3 h', [r.get('pre_freeze_s'), r.get('primary_drying_s'), r.get('secondary_drying_s')] == [3600.0, 43200.0, 10800.0], r)


def c_mivac(c):
    e = c.ev(MIVAC)
    c.near('set point 45 °C', e['thermal.target_k'], K0 + 45, 1e-6)
    c.equal('run finished', e['cycle.phase'], 'succeeded')
    c.add('lid closed', e['lid.open'] is False)
    c.near('mivac.txt = remaining solvent fraction', c.number_file('mivac.txt'), e['output.remaining_fraction'], 0.005)


def c_pilodist(c):
    e = c.ev(PILO)
    c.near('bath set point 100 °C', e['thermal.target_k'], K0 + 100, 1e-6)
    c.near('reflux ratio 3', e['reflux.target_ratio'], 3.0, 1e-9)
    c.equal('run finished', e['cycle.phase'], 'succeeded')
    c.equal('collector at receiver 1', e['fraction.index'], 1)
    rec = product(c, PILO).get('records') or []
    r0 = sum((r.get('fractions_mol') or [0, 0])[0] for r in rec)
    r1 = sum((r.get('fractions_mol') or [0, 0])[1] for r in rec)
    c.add('distillate in receiver 0 (first cut)', r0 > 0, r0)
    c.add('distillate in receiver 1 (main cut)', r1 > 0, r1)


def c_kdl(c):
    e = c.ev(KDL)
    c.near('wall 65 °C', e['thermal.wall_target_k'], K0 + 65, 1e-6)
    c.near('condenser 15 °C', e['thermal.condenser_target_k'], K0 + 15, 1e-6)
    c.near('vacuum 0.1 mbar', e['vacuum.target_pa'], 10.0, 1e-6)
    c.near('wiper 250 rpm', e['wiper.target_rad_s'], 250 * RPM, 1e-6)
    c.equal('run finished', e['cycle.phase'], 'succeeded')
    rec = saved(c, 'kdl.json') or {}
    c.equal('kdl.json holds the finished run record', (rec.get('result') or {}).get('phase'), 'succeeded')


def c_cpd(c):
    e = c.ev(CPD)
    c.near('purge 10 min', e['method.purge_time_s'], 600.0, 1e-6)
    c.equal('cycle finished', e['cycle.phase'], 'succeeded')
    c.add('passed the critical point', e.get('critical.controller_pass') is True, e.get('critical.controller_pass'))


def c_spray(c):
    e = c.ev(SPRAY)
    sp = e.get('tiles.setpoints') or {}
    c.near('inlet set point 150 °C', sp.get('inlet_target_k'), K0 + 150, 1e-6)
    c.near('feed 4 mL/min', sp.get('feed_m3_s'), 4e-6 / 60, 1e-12)
    c.add('sprayed for at least 2 min', (e.get('cycle.elapsed_s') or 0) >= 110, e.get('cycle.elapsed_s'))
    c.add('stopped', e['cycle.phase'] in ('cancelled', 'succeeded'), e['cycle.phase'])
    rec = saved(c, 'spray.json') or {}
    c.add('spray.json holds the run record', isinstance(rec.get('result'), dict) and 'inlet_k' in rec['result'], str(rec)[:120])


# ------------------------------------------------------------------ gold solutions
GOLD_RV = 'chemlab run ika_rv10_digital_v set-temp 40C --heat && chemlab run ika_rv10_digital_v rotate 120rpm && chemlab run ika_rv10_digital_v method time=30min'

GOLD_PUMP = r'''chemlab run ika_rv10_digital_v set-temp 40C --heat
chemlab run ika_rv10_digital_v vacuum 175mbar --json > v.json; rc=$?
jq -r '.error.code + ": " + .error.fix' v.json
if [ $rc -eq 6 ]; then chemlab call vacuubrand_pc3001_vario_select vacuum.set_pressure --pressure_pa 17500; fi
chemlab run ika_rv10_digital_v status'''

GOLD_LYO = r'''chemlab run buchi_lyovapor_l250_pro method freeze=10min primary=30min secondary=10min shelf=-20C pressure=0.3mbar
chemlab run buchi_lyovapor_l250_pro endpoint schedule
job=$(chemlab run buchi_lyovapor_l250_pro start --json | jq -r .job)
chemlab wait "$job" --max 2h
chemlab run buchi_lyovapor_l250_pro result'''

GOLD_LYO_RECOVER = r'''for i in 1 2 3 4; do
  chemlab run buchi_lyovapor_l250_pro start --json > s.json && break
  code=$(jq -r .error.code s.json); echo "$code: $(jq -r .error.fix s.json)"
  case "$code" in
    CHAMBER_OPEN) chemlab run buchi_lyovapor_l250_pro close chamber ;;
    NO_RECIPE) chemlab run buchi_lyovapor_l250_pro method freeze=1h primary=12h secondary=3h ;;
    *) break ;;
  esac
done
chemlab run buchi_lyovapor_l250_pro status'''

GOLD_MIVAC = r'''chemlab run genevac_mivac2 set-temp 45C
chemlab run genevac_mivac2 start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_OPEN"' s.json >/dev/null; then
  jq -r .error.fix s.json
  chemlab run genevac_mivac2 close lid
  chemlab run genevac_mivac2 start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 2h
chemlab run genevac_mivac2 result --json | jq -r .result.remaining_fraction > mivac.txt
cat mivac.txt'''

GOLD_PILO = r'''chemlab run pilodist_pilodist104 power on
chemlab run pilodist_pilodist104 set-temp 100C
chemlab run pilodist_pilodist104 reflux 3
job=$(chemlab run pilodist_pilodist104 start --json | jq -r .job)
chemlab wait "$job" --max 30s
chemlab run pilodist_pilodist104 fraction 1
chemlab wait "$job" --max 1h
chemlab run pilodist_pilodist104 result'''

GOLD_KDL = r'''chemlab run uic_kdl1basic power on
chemlab run uic_kdl1basic method temp=65C condenser=15C pressure=0.1mbar speed=250rpm feed=1.5g/min time=2min timeout=5min
job=$(chemlab run uic_kdl1basic start --json | jq -r .job)
chemlab wait "$job" --max 30min
chemlab run uic_kdl1basic result --save-to kdl.json'''

GOLD_CPD = r'''for i in 1 2 3 4; do
  chemlab run tousimis_autosamdri815 start --json > s.json && break
  code=$(jq -r .error.code s.json); echo "$code: $(jq -r .error.fix s.json)"
  case "$code" in
    POWER_OFF) chemlab run tousimis_autosamdri815 power on ;;
    NO_METHOD) chemlab run tousimis_autosamdri815 method purge=10min bleed=14364 ;;
    *) break ;;
  esac
done
chemlab wait "$(jq -r .job s.json)" --max 3h
chemlab run tousimis_autosamdri815 result'''

GOLD_SPRAY = r'''chemlab run buchi_minispray method inlet=150C feed=4mL/min
chemlab run buchi_minispray start --json > s.json; rc=$?
if [ $rc -eq 3 ]; then
  jq -r '.error.code + ": " + .error.fix' s.json
  chemlab run buchi_minispray preheat on --wait
  chemlab run buchi_minispray start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 2min
chemlab run buchi_minispray stop
chemlab run buchi_minispray result --save-to spray.json'''


TASKS = [
    {'id': 'ev01_rotavap_setup', 'kind': 'multi-step (set points + rotation + timer)', 'instruments': [RV], 'config': cfg(), 'check': c_rotavap,
     'instruction': 'On the IKA RV 10 digital V rotary evaporator (ika_rv10_digital_v): heat the bath to 40 °C with the heating on, rotate the '
                    'flask at 120 rpm, and set the timer to 30 minutes.', 'gold': GOLD_RV},
    {'id': 'ev02_rotavap_vacuum_pump', 'kind': 'recovery: not supported here, use the external vacuum source (two instruments)',
     'instruments': [RV, PUMP], 'config': cfg(), 'check': c_rotavap_pump,
     'instruction': 'Prepare the rotavap (ika_rv10_digital_v) for ethanol: bath at 40 °C with heating on and a vacuum set point of 175 mbar.',
     'gold': GOLD_PUMP},
    {'id': 'ev03_lyo_cycle', 'kind': 'long action: recipe + job to the end', 'instruments': [LYO], 'config': cfg(), 'check': c_lyo_cycle,
     'instruction': 'Freeze-dry the loaded samples on the BUCHI Lyovapor L-250 (buchi_lyovapor_l250_pro): 10 min pre-freeze, 30 min primary '
                    'and 10 min secondary drying, shelf at -20 °C, vacuum 0.3 mbar, ending on schedule. Run the cycle to its end.', 'gold': GOLD_LYO},
    {'id': 'ev04_lyo_recover_start', 'kind': 'recovery: chamber open, then no recipe', 'instruments': [LYO],
     'config': cfg(variants={LYO: 'open'}), 'check': c_lyo_recover,
     'instruction': 'Start a freeze-drying cycle on the Lyovapor (buchi_lyovapor_l250_pro) with 1 h pre-freeze, 12 h primary and 3 h '
                    'secondary drying. Do not wait for it to finish.', 'gold': GOLD_LYO_RECOVER},
    {'id': 'ev05_mivac_lid_report', 'kind': 'recovery: lid open + job + report', 'instruments': [MIVAC],
     'config': cfg(extra={MIVAC: [{'capability': 'lid.set_open', 'arguments': {'open': True}}]}), 'check': c_mivac,
     'instruction': 'Concentrate the samples in the Genevac miVac (genevac_mivac2) at 45 °C until the run ends, then write the remaining '
                    'solvent fraction (a number between 0 and 1) into mivac.txt.', 'gold': GOLD_MIVAC},
    {'id': 'ev06_pilodist_cut', 'kind': 'long action: job with a step in the middle (fraction cut)', 'instruments': [PILO], 'config': cfg(),
     'check': c_pilodist,
     'instruction': 'Distil the charge on the PILODIST 104 (pilodist_pilodist104) with the bath at 100 °C and a reflux ratio of 3:1. Collect '
                    'the first 30 s of distillate in receiver 0, then switch to receiver 1 for the rest of the run, and let the run finish.',
     'gold': GOLD_PILO},
    {'id': 'ev07_kdl_record', 'kind': 'method + job + record file', 'instruments': [KDL], 'config': cfg(), 'check': c_kdl,
     'instruction': 'On the UIC KDL 1 short-path still (uic_kdl1basic) distil with the wall at 65 °C, the condenser at 15 °C, 0.1 mbar, the '
                    'wiper at 250 rpm and a feed of 1.5 g/min for 2 minutes (timeout 5 minutes). Run it and save the full run record to kdl.json.',
     'gold': GOLD_KDL},
    {'id': 'ev08_cpd_recover', 'kind': 'recovery: switched off, then no method; long job', 'instruments': [CPD], 'config': cfg(), 'check': c_cpd,
     'instruction': 'Run the critical point drying of the loaded specimen on the Tousimis Autosamdri-815 (tousimis_autosamdri815) with a '
                    '10 min purge and a bleed rate of 14364 Pa/s, to the end of the cycle.', 'gold': GOLD_CPD},
    {'id': 'ev09_spray_preheat', 'kind': 'recovery: not preheated; timed run, stop, record', 'instruments': [SPRAY], 'config': cfg(), 'check': c_spray,
     'instruction': 'Spray-dry the feed on the BUCHI Mini Spray Dryer S-300 (buchi_minispray) at an inlet temperature of 150 °C and a feed of '
                    '4 mL/min. Let it spray for 2 minutes of lab time, then stop it and save the run record to spray.json.', 'gold': GOLD_SPRAY},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'evaporation'


def main(only=None, noact=True):
    return run_suite('evaporation', TASKS, only=only, noact=noact)

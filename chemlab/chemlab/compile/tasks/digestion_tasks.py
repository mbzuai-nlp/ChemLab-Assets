"""Digestion class acceptance tasks (9): gold solutions use `chemlab run`; the checkers read the evaluator view (run records,
sample ledgers) and the agent's report files."""
import json

from .common import run_suite
from .digestion_world import config as world_config

K0 = 273.15
MW, DP, UV, HX = 'multiwave_7101', 'scp_digiprepms', 'metrohm_909uv', 'buchi_hydrolexh'


def _recs(e):
    return e.get('data.experiments') or []


# ------------------------------------------------------------------ checkers
def c_epa_run(c):
    e = c.ev(MW)
    recs = _recs(e)
    c.add('one method run recorded', len(recs) == 1, recs)
    c.add('the run used EPA 3051A HNO3 and completed', bool(recs) and recs[-1].get('method') == 'EPA 3051A HNO3' and recs[-1].get('result') == 'completed',
          recs[-1] if recs else None)
    c.add('a vessel at position 1', (e.get('rack.occupied_slots') or [False])[0])
    t = c.work_file('digest.json') or ''
    try:
        rec = json.loads(t).get('result') or {}
    except ValueError:
        rec = {}
    c.add('digest.json is the evaluation of vessel 1 (NIST SRM 2711a, programme complete)',
          rec.get('slot_index') == 0 and rec.get('material_id') == 'nist_srm_2711a' and rec.get('status') == 'complete', str(rec)[:200])


def c_custom_method(c):
    e = c.ev(MW)
    m = next((x for x in e.get('methods.library') or [] if x.get('name') == 'MYDIGEST'), None)
    c.add('method MYDIGEST exists', m is not None)
    steps = (m or {}).get('steps') or []
    c.add('two steps: 200 °C for 20 min, then 200 °C for 15 min',
          [(s.get('temperature_C'), s.get('time_s')) for s in steps] == [(200.0, 1200.0), (200.0, 900.0)], steps)
    c.near('power limit 1200 W', (m or {}).get('max_power_W'), 1200.0, 1e-6)
    c.equal('MYDIGEST selected', e.get('methods.selected'), 'MYDIGEST')


def c_load_start(c):
    e = c.ev(MW)
    occ = e.get('rack.occupied_slots') or []
    c.add('vessels at positions 3 and 4', len(occ) > 3 and occ[2] and occ[3], occ[:6])
    c.add('the door is closed', e.get('door.closed'))
    c.add('the selected method runs', e.get('run.status') in ('running', 'cooling', 'finished') and e.get('run.method') == 'EPA 3051A HNO3',
          (e.get('run.status'), e.get('run.method')))


def c_digiprep_run(c):
    e = c.ev(DP)
    c.equal('cycle succeeded', e.get('cycle.phase'), 'succeeded')
    c.add('held at least 20 min at 95 °C', (e.get('cycle.hold_elapsed_s') or 0) >= 1200 - 1e-6, e.get('cycle.hold_elapsed_s'))
    c.near('final programme temperature 95 °C', e.get('thermal.target_k'), K0 + 95, 1e-6)
    c.add('mineral dissolved in the tube', (e.get('truth.dissolved_mol') or 0) > 0, e.get('truth.dissolved_mol'))
    c.near('block.txt reports the block temperature (°C, ±1 K)', c.number_file('block.txt'), (e.get('thermal.indicated_temperature_k') or 0) - K0, 1.0)


def c_digiprep_power(c):
    e = c.ev(DP)
    c.add('switched on', e.get('power.enabled'))
    c.equal('cycle running', e.get('cycle.phase'), 'running')
    c.near('constant 90 °C programme', e.get('thermal.target_k'), K0 + 90, 1e-6)


def c_uv_run(c):
    e = c.ev(UV)
    c.equal('UV digestion succeeded', e.get('cycle.phase'), 'succeeded')
    c.near('90 °C', e.get('thermal.target_k'), K0 + 90, 1e-6)
    c.add('the shield is closed', not e.get('shield.open_target'))
    c.add('the sample was digested (parent converted)', sum(e.get('truth.converted_mol') or [0]) > 0, e.get('truth.converted_mol'))


def c_uv_whole(c):
    e = c.ev(UV)
    c.add('method set', e.get('method.configured'))
    c.near('92 °C (nearest whole degree to 92.4 °C)', e.get('thermal.target_k'), K0 + 92, 1e-6)
    c.near('45 min left on the timer', e.get('cycle.remaining_s'), 2700.0, 1e-6)


def c_hydrolysis(c):
    e = c.ev(HX)
    c.equal('hydrolysis complete', (e.get('cycle.phase'), e.get('cycle.stage')), ('succeeded', 'hydrolysis_complete'))
    led = e.get('truth.fat_ledger') or {}
    bound = led.get('bound_in_vessels_kg')
    c.add('bound fat released in the beakers', bound is not None and bound < 0.8 * 0.8 * (led.get('initial_fat_kg') or 0), bound)
    c.add('stopcocks 1 and 3 open, the others closed', [round(e.get('valve_%d.angle_rad' % i) or 0, 3) > 1 for i in range(1, 7)] == [True, False, True, False, False, False])
    t = c.work_file('hydrolysis.txt') or ''
    c.add('hydrolysis.txt has the record (phase succeeded)', 'succeeded' in t, t[:120])


def c_hydrolex_preheat(c):
    e = c.ev(HX)
    c.equal('hydrolysis running', e.get('cycle.phase'), 'running')
    c.add('preheated 10 min before', (e.get('thermal.preheat_s') or 0) >= 600 - 1e-6, e.get('thermal.preheat_s'))
    lift = e.get('rack.lift_m')
    c.add('rack down', lift is not None and lift <= 1e-9, lift)


# ------------------------------------------------------------------ gold solutions
LOAD = '--material nist_srm_2711a --method epa_3051a_hno3 --mass 0.2g --acid 4mL'

GOLD_D01 = r'''chemlab run multiwave_7101 open door
chemlab run multiwave_7101 load 1 %s
chemlab run multiwave_7101 close door
chemlab run multiwave_7101 method name="EPA 3051A HNO3"
job=$(chemlab run multiwave_7101 start --json | jq -r .job)
chemlab wait "$job" --max 3h
chemlab run multiwave_7101 result --slot 1 --save-to digest.json''' % LOAD

GOLD_D03 = r'''chemlab run multiwave_7101 load 3,4 %s --json > l.json; rc=$?
jq -r '.error.code + ": " + .error.fix' l.json
if [ $rc -eq 3 ] && jq -e '.error.code == "DOOR_CLOSED"' l.json >/dev/null; then
  chemlab run multiwave_7101 open door
  chemlab run multiwave_7101 load 3,4 %s
fi
chemlab run multiwave_7101 start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "DOOR_OPEN"' s.json >/dev/null; then
  chemlab run multiwave_7101 close door
  chemlab run multiwave_7101 start
fi
chemlab run multiwave_7101 status''' % (LOAD, LOAD)

GOLD_D04 = r'''chemlab run scp_digiprepms power on
chemlab run scp_digiprepms method temp=95C ramp=10min hold=20min
job=$(chemlab run scp_digiprepms start --json | jq -r .job)
chemlab wait "$job" --max 4h
chemlab run scp_digiprepms result
chemlab run scp_digiprepms status --json | jq -r '.state.temperature_k - 273.15' > block.txt
cat block.txt'''

GOLD_D05 = r'''chemlab run scp_digiprepms method temp=90C hold=15min
chemlab run scp_digiprepms start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "POWER_OFF"' s.json >/dev/null; then
  chemlab run scp_digiprepms power on
  chemlab run scp_digiprepms start
fi'''

GOLD_D06 = r'''chemlab run metrohm_909uv method temp=90C hold=30min
chemlab run metrohm_909uv start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "SHIELD_OPEN"' s.json >/dev/null; then
  chemlab run metrohm_909uv close shield
  chemlab wait 2
  chemlab run metrohm_909uv start --json > s.json
fi
job=$(jq -r .job s.json)
chemlab wait "$job" --max 2h
chemlab run metrohm_909uv result'''

GOLD_D07 = r'''chemlab run metrohm_909uv method temp=92.4C hold=45min --json > m.json; rc=$?
jq -r '.error.code + ": " + .error.message' m.json
if [ $rc -eq 2 ] && jq -e '.error.code == "BAD_STEP"' m.json >/dev/null; then
  chemlab run metrohm_909uv method temp=92C hold=45min
fi
chemlab run metrohm_909uv status'''

GOLD_D08 = r'''chemlab run buchi_hydrolexh power on
chemlab run buchi_hydrolexh method hold=30min
chemlab run buchi_hydrolexh preheat on --wait
chemlab run buchi_hydrolexh load 1,3
job=$(chemlab run buchi_hydrolexh start --json | jq -r .job)
chemlab wait "$job" --max 2h
chemlab run buchi_hydrolexh result > hydrolysis.txt
cat hydrolysis.txt'''

GOLD_D09 = r'''chemlab run buchi_hydrolexh power on
chemlab run buchi_hydrolexh method hold=20min
chemlab run buchi_hydrolexh load 1,3
chemlab run buchi_hydrolexh start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_PREHEATED"' s.json >/dev/null; then
  chemlab run buchi_hydrolexh preheat on --wait
  chemlab run buchi_hydrolexh start
fi'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'dg01_multiwave_epa_run', 'kind': 'multi-step + long action (job) + record', 'instruments': [MW], 'check': c_epa_run,
     'instruction': 'On the Anton Paar Multiwave 7101 (multiwave_7101), digest 0.2 g of NIST SRM 2711a soil (material nist_srm_2711a) in 4 mL '
                    'of acid at rack position 1 with the EPA 3051A HNO3 method (method id epa_3051a_hno3, library method "EPA 3051A HNO3"). '
                    'Run it to the end and save the evaluation of vessel 1 as digest.json.', 'gold': GOLD_D01},
    {'id': 'dg02_multiwave_new_method', 'kind': 'single step (method editor)', 'instruments': [MW], 'check': c_custom_method,
     'instruction': 'On the Multiwave 7101 (multiwave_7101), create a digestion method named MYDIGEST that ramps to 200 °C in 20 min and then '
                    'holds 200 °C for 15 min, with a microwave power limit of 1200 W, and make it the selected method.',
     'gold': 'chemlab run multiwave_7101 method name=MYDIGEST temp=200C ramp=20min hold=15min power=1200W && chemlab run multiwave_7101 status'},
    {'id': 'dg03_recover_door', 'kind': 'recovery: door closed for loading, open for starting', 'instruments': [MW], 'check': c_load_start,
     'instruction': 'On the Multiwave 7101 (multiwave_7101), put two vessels with 0.2 g of NIST SRM 2711a (nist_srm_2711a) and 4 mL acid each at '
                    'positions 3 and 4 for EPA 3051A (epa_3051a_hno3), then start the selected method.', 'gold': GOLD_D03},
    {'id': 'dg04_digiprep_programme', 'kind': 'long action (job) + report', 'instruments': [DP], 'check': c_digiprep_run,
     'instruction': 'Run a digestion cycle on the SCP DigiPREP MS block (scp_digiprepms): ramp to 95 °C in 10 min and hold 20 min at 95 °C. '
                    'Wait until the cycle has ended and write the block temperature in °C into block.txt.', 'gold': GOLD_D04},
    {'id': 'dg05_recover_power', 'kind': 'recovery: instrument switched off', 'instruments': [DP], 'check': c_digiprep_power,
     'instruction': 'Start a constant 90 °C digestion programme with a 15-min hold on the DigiPREP MS (scp_digiprepms).', 'gold': GOLD_D05},
    {'id': 'dg06_uv_recover_shield', 'kind': 'recovery: protective shield open + job', 'instruments': [UV], 'check': c_uv_run,
     'instruction': 'Digest the samples in the Metrohm 909 UV Digester (metrohm_909uv) at 90 °C for 30 min and wait until it has finished.',
     'gold': GOLD_D06},
    {'id': 'dg07_uv_whole_degree', 'kind': 'recovery: usage error (whole °C)', 'instruments': [UV], 'check': c_uv_whole,
     'instruction': 'Set the Metrohm 909 UV Digester (metrohm_909uv) to digest at 92.4 °C for 45 min (do not start it). If the instrument does '
                    'not take that temperature, use the nearest temperature it accepts.', 'gold': GOLD_D07},
    {'id': 'dg08_hydrolysis', 'kind': 'multi-step + long action (job) + record', 'instruments': [HX], 'check': c_hydrolysis,
     'instruction': 'The BUCHI HydrolEx H-506 (buchi_hydrolexh) has sample beakers at positions 1 and 3. Hydrolyse them for 30 min (preheat the '
                    'unit first), wait until the hydrolysis is complete and write its record into hydrolysis.txt.', 'gold': GOLD_D08},
    {'id': 'dg09_recover_preheat', 'kind': 'recovery: not preheated', 'instruments': [HX], 'check': c_hydrolex_preheat,
     'instruction': 'Start a 20-min hydrolysis of the beakers at positions 1 and 3 of the BUCHI HydrolEx (buchi_hydrolexh).', 'gold': GOLD_D09},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'digestion'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('digestion', TASKS, only=only, noact=noact)

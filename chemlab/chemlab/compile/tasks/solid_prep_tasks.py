"""solid_prep class acceptance tasks (8): gold solutions use `chemlab run` (plus `chemlab wait` / `jq` in the sandbox); the
checkers read the evaluator view, the instruments' own records (evaluator calls on the host side) and the agent's report files.
The world (jar, sieve stack, hopper fill, die fill, crucible) is the operator's: tasks/solid_prep_world.py."""
import itertools

from . import solid_prep_world as W
from .common import run_suite

PM, AS, PT, SP, LN = 'retsch_pm100', 'retsch_as200', 'retsch_pt100', 'specac_atlasmanual', 'malvern_leneo'
RPM = 2 * 3.141592653589793 / 60
_n = itertools.count()


def ev_call(c, iid, cap, args=None):
    r = c.lab.backend.evaluator_invoke(iid, cap, args or {}, 'check-%s-%s-%d' % (iid, cap, next(_n)))
    return r.get('result') if r.get('status') == 'succeeded' else None


def world(extra=None):
    return lambda cfg: W.config(cfg, extra)


def max_speed(c):
    recs = (ev_call(c, PM, 'history.read') or {}).get('records') or []
    return max((r.get('speed_rad_s') or 0.0 for r in recs), default=0.0)


# ------------------------------------------------------------------ checkers
def c_mill(c):
    e = c.ev(PM)
    c.equal('grinding cycle finished', e.get('cycle.phase'), 'succeeded')
    c.near('ground for 1 min', e.get('cycle.active_s'), 60.0, 0.5)
    c.near('at 400 rpm', max_speed(c), 400 * RPM, 0.05)
    c.near('time.txt = grinding time in s (±0.5)', c.number_file('time.txt'), e.get('cycle.active_s') or -1, 0.5)


def c_mill_again(c):
    e = c.ev(PM)
    c.equal('second grinding cycle finished', e.get('cycle.phase'), 'succeeded')
    c.near('ground 30 s in the new cycle', e.get('cycle.active_s'), 30.0, 0.5)
    c.near('set speed 300 rpm', e.get('milling.target_rad_s'), 300 * RPM, 1e-6)


def c_sieve(c):
    e = c.ev(AS)
    res = ev_call(c, AS, 'sieving.measure') or {}
    c.equal('sieving finished', e.get('cycle.phase'), 'succeeded')
    c.near('sieved 2 min', res.get('driven_s'), 120.0, 0.5)
    c.near('amplitude 1.5 mm', e.get('sieving.target_total_lift_m'), 0.0015, 1e-9)
    c.near('driven.txt = driven time in s (±0.5)', c.number_file('driven.txt'), res.get('driven_s') or -1, 0.5)


def c_sieve_pause(c):
    e = c.ev(AS)
    res = ev_call(c, AS, 'sieving.measure') or {}
    c.equal('sieving finished', e.get('cycle.phase'), 'succeeded')
    c.near('sieved 2 min of drive time', res.get('driven_s'), 120.0, 0.5)
    used = [(v, x) for i, v, x in c.verbs_used() if i == AS]
    c.add('paused and resumed (pause on / pause off succeeded)', ('pause', 0) in used and used.count(('pause', 0)) >= 2, used)


def c_divide(c):
    e = c.ev(PT)
    res = ev_call(c, PT, 'dividing.measure') or {}
    c.equal('division finished', e.get('cycle.phase'), 'succeeded')
    c.equal('feeder intensity 60', res.get('intensity'), 60)
    prod = ev_call(c, PT, 'product.read') or {}
    got = sum(sum(row) for row in prod.get('recipient_mass_kg') or [])
    c.add('material reached the bottles', got > 0.01, got)


def c_press(c):
    e = c.ev(SP)
    c.equal('press cycle complete (load held)', e.get('cycle.phase'), 'succeeded')
    c.add('load released', (e.get('load.indicated_n') or 0) < 10, e.get('load.indicated_n'))
    c.add('front guard raised again for the die', (e.get('guard_front.lift_m') or 0) > 0.17, e.get('guard_front.lift_m'))
    res = ev_call(c, SP, 'press.measure') or {}
    c.near('held for the method time (12 s)', res.get('hold_elapsed_s'), 12.0, 0.1)


def c_fusion(c):
    e = c.ev(LN)
    res = ev_call(c, LN, 'fusion.measure') or {}
    c.equal('fusion cycle finished, bead cold', (e.get('cycle.phase'), e.get('cycle.stage')), ('succeeded', 'cold_product'))
    c.near('programme 10 min + 9 min', res.get('elapsed_s'), 1140.0, 1.0)
    t = c.work_file('stage.txt') or ''
    c.add('stage.txt reports the final stage', 'cold_product' in t, t[:60])


def c_delayed(c):
    e = c.ev(PM)
    c.equal('grinding cycle finished', e.get('cycle.phase'), 'succeeded')
    recs = (ev_call(c, PM, 'history.read') or {}).get('records') or []
    t0 = getattr(c.lab.backend, 't_setup', 0.0)                         # the agent starts when the bring-up has finished
    first = next((r['time_s'] for r in recs if (r.get('speed_rad_s') or 0) > 0), None)
    c.add('the drive started about 2 min after the command (delayed start)', first is not None and 115.0 <= first - t0 <= 130.0,
          (first, t0))


# ------------------------------------------------------------------ gold solutions
GOLD_MILL = r'''chemlab run retsch_pm100 method speed=400rpm time=1min
job=$(chemlab run retsch_pm100 start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run retsch_pm100 result --json | jq -r '.summary["grinding time"]' > time.txt
cat time.txt'''

GOLD_MILL_AGAIN = r'''I=retsch_pm100
chemlab run $I method speed=300rpm time=30s --json > m.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "CYCLE_FINISHED"' m.json >/dev/null; then
  jq -r '.error.fix' m.json
  chemlab run $I rearm
  chemlab run $I method speed=300rpm time=30s
fi
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_OPEN"' s.json >/dev/null; then
  chemlab run $I close lid
  chemlab wait 5
  chemlab run $I start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 10min
chemlab run $I status'''

GOLD_SIEVE = r'''chemlab run retsch_as200 method amplitude=1.5mm time=2min interval-on=off
job=$(chemlab run retsch_as200 start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run retsch_as200 result --json | jq -r '.summary.driven' > driven.txt
cat driven.txt'''

GOLD_SIEVE_PAUSE = r'''chemlab run retsch_as200 method amplitude=1.5mm time=2min interval-on=off
job=$(chemlab run retsch_as200 start --json | jq -r .job)
chemlab wait 30
chemlab run retsch_as200 pause on
chemlab wait 20
chemlab run retsch_as200 pause off
chemlab wait "$job" --max 10min'''

GOLD_DIVIDE = r'''I=retsch_pt100
chemlab run $I power on
chemlab run $I method time=1min intensity=60
chemlab wait 2
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "FEEDER_OFF"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab run $I feeder on
  chemlab run $I start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 10min
chemlab run $I result'''

GOLD_PRESS = r'''I=specac_atlasmanual
chemlab run $I method load=68kN hold=12s
chemlab run $I leadscrew 73mm
for i in 1 2 3; do
  chemlab run $I start --json > s.json && break
  code=$(jq -r .error.code s.json); echo "start: $code"
  case "$code" in
    GUARD_OPEN) chemlab run $I close guard ;;
    VALVE_OPEN) chemlab run $I valve close ;;
    *) exit 1 ;;
  esac
done
chemlab run $I pump --to 68kN --hold --max 10min
chemlab run $I result
chemlab run $I valve open
chemlab run $I open guard'''

GOLD_FUSION = r'''I=malvern_leneo
chemlab run $I method segments=10min@1065C,9min@1065C
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "DOOR_OPEN"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab run $I close door
  chemlab wait 5
  chemlab run $I start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 2h
chemlab run $I result --json | jq -r '.summary.stage' > stage.txt
cat stage.txt'''

GOLD_DELAYED = r'''chemlab run retsch_pm100 method speed=300rpm time=30s
job=$(chemlab run retsch_pm100 start --delay 2min --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 10min'''

PM_FINISHED = {PM: [{'capability': 'acquisition.start', 'arguments': {}, 'evaluator': True}, {'advance_s': 60.0}]}

TASKS = [
    {'id': 'sp01_mill', 'kind': 'long action (job) + report', 'instruments': [PM], 'config': world(), 'check': c_mill,
     'instruction': 'Grind the sample in the RETSCH PM 100 planetary mill (retsch_pm100) at 400 rpm for 1 min (keep the interval and '
                    'reversal settings it has). Write the grinding time in seconds it reports into time.txt.', 'gold': GOLD_MILL},
    {'id': 'sp02_mill_recover_finished', 'kind': 'recovery: last cycle not re-armed, lid open', 'instruments': [PM], 'config': world(PM_FINISHED),
     'check': c_mill_again,
     'instruction': 'A colleague has just ground a sample on the PM 100 (retsch_pm100). Grind it once more at 300 rpm for 30 s.',
     'gold': GOLD_MILL_AGAIN},
    {'id': 'sp03_sieve', 'kind': 'long action (job) + report', 'instruments': [AS], 'config': world(), 'check': c_sieve,
     'instruction': 'Sieve the sample on the RETSCH AS 200 (retsch_as200) for 2 min at 1.5 mm amplitude, continuous (no interval). Write '
                    'the drive time in seconds it reports into driven.txt.', 'gold': GOLD_SIEVE},
    {'id': 'sp04_sieve_pause', 'kind': 'pause and resume a job', 'instruments': [AS], 'config': world(), 'check': c_sieve_pause,
     'instruction': 'Sieve on the AS 200 (retsch_as200) for 2 min at 1.5 mm, continuous. After 30 s pause it for 20 s (a colleague checks '
                    'the stack), then let it finish.', 'gold': GOLD_SIEVE_PAUSE},
    {'id': 'sp05_divide_recover_feeder', 'kind': 'recovery: feeder off', 'instruments': [PT], 'config': world(), 'check': c_divide,
     'instruction': 'Divide the sample in the RETSCH PT 100 rotary divider (retsch_pt100) for 1 min with feeder intensity 60.',
     'gold': GOLD_DIVIDE},
    {'id': 'sp06_press_recover_guard_valve', 'kind': 'recovery: guard up, valve open', 'instruments': [SP], 'config': world(), 'check': c_press,
     'instruction': 'Press a KBr pellet on the Specac Atlas 15T manual press (specac_atlasmanual): 68 kN held for 12 s. The leadscrew goes to '
                    '73 mm to close the gap over the die. Afterwards release the load and raise the front guard so the die can be taken out.',
     'gold': GOLD_PRESS},
    {'id': 'sp07_fusion_recover_door', 'kind': 'recovery: door open (long job)', 'instruments': [LN], 'config': world(), 'check': c_fusion,
     'instruction': 'Make a fused bead on the Claisse LeNeo (malvern_leneo) with the programme 10 min to 1065 °C, then 9 min at 1065 °C, '
                    'and wait until the bead is cold. Write the final stage it reports into stage.txt.', 'gold': GOLD_FUSION},
    {'id': 'sp08_mill_delayed_start', 'kind': 'delayed start (job without an operation)', 'instruments': [PM], 'config': world(), 'check': c_delayed,
     'instruction': 'Set the PM 100 (retsch_pm100) to 300 rpm for 30 s and let it start by itself in 2 minutes; wait until it has finished.',
     'gold': GOLD_DELAYED},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'solid_prep'


def main(only=None, noact=True):
    return run_suite('solid_prep', TASKS, only=only, noact=noact)

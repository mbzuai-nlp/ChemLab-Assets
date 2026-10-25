"""Enclosure class acceptance tasks (6): gold solutions use `chemlab run`; the checkers read the evaluator view and the agent's
report files.  The labs carry the class world (tasks/enclosure_world.py: the glovebox scene)."""
from .common import run_suite
from .enclosure_world import config as world_config

GB, BSC, HOOD = 'inert_purelab_he_2gb', 'esco_ac24s8', 'esco_efa5uduvw'
_N = [0]


def world_call(lab, iid, cap, args):
    _N[0] += 1
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, _N[0]))
    assert r.get('status') == 'succeeded', (cap, r)


def w_bsc_in_use(lab):
    """The cabinet was in use: sash at the safe height, blower on and warmed up, light on."""
    world_call(lab, BSC, 'sash.move', {'direction': 'up'})
    lab.backend.advance(10)
    world_call(lab, BSC, 'fan.set', {'state': 'on'})
    lab.backend.advance(200)


# ------------------------------------------------------------------ checkers
def c_bsc_ready(c):
    e = c.ev(BSC)
    c.equal('blower on', e['fan.state'], 'on')
    c.equal('airflow OK', e['airflow.status'], 'ok')
    c.equal('sash at the safe height', e['sash.status'], 'safe')
    c.add('work light on', e['lamp.on'])


def c_bsc_uv(c):
    e = c.ev(BSC)
    c.add('UV lamp on', e['uv.on'])
    c.equal('sash closed', e['sash.status'], 'closed')
    c.equal('blower off', e['fan.state'], 'off')


def c_hood_load(c):
    e = c.ev(HOOD)
    c.near('sash back at the 457 mm working height', e['sash.height_m'], 0.457, 1e-6)
    c.add('sash not held', not e['sash.held'])
    raised = [x for x in c.lab.core.log.entries if x['argv'][1:4] == ['run', HOOD, 'sash'] and x['exit'] == 0 and '--hold' in x['argv']]
    c.add('it was raised above the stop (held) on the way', bool(raised), len(raised))
    c.near('face.txt reports the face velocity (m/s, +-0.02)', c.number_file('face.txt'), e['monitor.reading_m_s'], 0.02)


def c_hood_night(c):
    e = c.ev(HOOD)
    c.near('sash closed', e['sash.height_m'], 0.0, 1e-9)
    c.add('night setback on', e['night_setback.closed'])
    c.add('light off', not e['light.on'])


def c_transfer(c):
    e = c.ev(GB)
    c.add('inner door open', e['antechamber.inner_door_open'])
    c.add('outer door closed', not e['antechamber.outer_door_open'])
    c.equal('antechamber cycles completed', e['antechamber.phase'], 'completed')
    c.add('at least 3 evacuate / refill cycles', (e.get('antechamber.completed_cycles') or 0) >= 3, e.get('antechamber.completed_cycles'))


def c_purify(c):
    e = c.ev(GB)
    c.near('blower speed 70 %', (e.get('blower.state') or {}).get('speed_set_pct'), 70.0, 1e-6)
    c.add('gas purification on', e['purification.enabled'])
    c.near('box pressure set point 5 mbar', e['pressure.target_gauge_pa'], 500.0, 1e-6)


GOLD_E01 = r'''chemlab run esco_ac24s8 fan on --wait --json > f.json; rc=$?
jq -r '.error.code + ": " + .error.fix' f.json
if [ $rc -eq 3 ] && jq -e '.error.code == "SASH_CLOSED"' f.json >/dev/null; then
  chemlab run esco_ac24s8 sash safe
  chemlab run esco_ac24s8 fan on --wait
fi
chemlab run esco_ac24s8 light on
chemlab run esco_ac24s8 read'''

GOLD_E02 = r'''for i in 1 2 3; do
  chemlab run esco_ac24s8 uv on --json > u.json && break
  code=$(jq -r .error.code u.json); echo "uv: $code"
  case "$code" in
    SASH_NOT_CLOSED) chemlab run esco_ac24s8 sash close ;;
    FAN_RUNNING) chemlab run esco_ac24s8 fan off ;;
    *) break ;;
  esac
done
chemlab run esco_ac24s8 status'''

GOLD_E03 = r'''chemlab run esco_efa5uduvw sash open --json > s.json; rc=$?
if [ $rc -eq 2 ] && jq -e '.error.code == "NEEDS_HOLD"' s.json >/dev/null; then
  chemlab run esco_efa5uduvw sash open --hold
fi
chemlab run esco_efa5uduvw sash safe
chemlab wait 10
chemlab run esco_efa5uduvw read --json | jq -r '.readings["monitor.reading_m_s"]' > face.txt
cat face.txt'''

GOLD_E05 = r'''chemlab run inert_purelab_he_2gb open outer
chemlab run inert_purelab_he_2gb close outer
chemlab run inert_purelab_he_2gb open inner --json > o.json; rc=$?
jq -r '.error.code + ": " + .error.fix' o.json
if [ $rc -eq 3 ] && jq -e '.error.code == "ANTECHAMBER_NOT_PURGED"' o.json >/dev/null; then
  chemlab run inert_purelab_he_2gb cycle --cycles 3 --wait --max 30min
  chemlab run inert_purelab_he_2gb open inner
fi
chemlab run inert_purelab_he_2gb read'''

TASKS = [
    {'id': 'en01_bsc_startup', 'kind': 'recovery: sash closed', 'instruments': [BSC], 'check': c_bsc_ready,
     'instruction': 'Prepare the Esco biosafety cabinet (esco_ac24s8) for work: blower running with the airflow OK at the safe working height, '
                    'work light on.', 'gold': GOLD_E01},
    {'id': 'en02_bsc_uv', 'kind': 'recovery: two UV interlocks (sash, blower)', 'instruments': [BSC], 'world': w_bsc_in_use, 'check': c_bsc_uv,
     'instruction': 'Work in the Esco biosafety cabinet (esco_ac24s8) is finished. Decontaminate it with its UV lamp (it must be off-duty: sash '
                    'closed, blower off).', 'gold': GOLD_E02},
    {'id': 'en03_hood_load', 'kind': 'recovery: above the sash stop + report', 'instruments': [HOOD], 'check': c_hood_load,
     'instruction': 'Raise the Esco fume hood sash (esco_efa5uduvw) fully to bring a large apparatus in, then lower it back to the working '
                    'height. After 10 s, write the airflow monitor reading (m/s) into face.txt.', 'gold': GOLD_E03},
    {'id': 'en04_hood_night', 'kind': 'multi-step', 'instruments': [HOOD], 'check': c_hood_night,
     'instruction': 'End of the day at the Esco fume hood (esco_efa5uduvw): close the sash, put its airflow monitor into night setback and switch '
                    'the light off.',
     'gold': 'chemlab run esco_efa5uduvw sash close && chemlab run esco_efa5uduvw mode night && chemlab run esco_efa5uduvw light off'},
    {'id': 'en05_glovebox_transfer', 'kind': 'recovery: antechamber not purged (long action)', 'instruments': [GB], 'check': c_transfer,
     'instruction': 'Bring a sample vial into the Inert PureLab glovebox (inert_purelab_he_2gb) through the antechamber: open the outer door (the '
                    'operator puts the vial in), close it, and open the inner door so the vial can be taken into the box - without letting air '
                    'into the box (at least 3 evacuate / refill cycles).', 'gold': GOLD_E05},
    {'id': 'en06_glovebox_purify', 'kind': 'class + secondary class verbs (composite)', 'instruments': [GB], 'check': c_purify,
     'instruction': 'On the Inert PureLab glovebox (inert_purelab_he_2gb): set the circulation blower to 70 %, set the box working pressure to '
                    '5 mbar and switch the gas purification on.',
     'gold': 'chemlab run inert_purelab_he_2gb fan 70% && chemlab run inert_purelab_he_2gb setpoint 5mbar && chemlab run inert_purelab_he_2gb start'},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'enclosure'
    _t['config'] = world_config


def main(only=None, noact=True):
    return run_suite('enclosure', TASKS, only=only, noact=noact)

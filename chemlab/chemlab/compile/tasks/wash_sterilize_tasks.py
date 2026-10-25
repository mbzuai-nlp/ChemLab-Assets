"""wash_sterilize class acceptance tasks (5): gold solutions use `chemlab run`; the checkers read the evaluator view and the core log
(for what happened during a run).  The labs carry the class world (tasks/wash_sterilize_world.py)."""
from .common import run_suite
from .wash_sterilize_world import config as world_config

K0 = 273.15
WASH, AUTO = 'miele_plw6111', 'tuttnauer_3870elvd'
_N = [0]


def world_call(lab, iid, cap, args, ok=('succeeded',)):
    _N[0] += 1
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, _N[0]))
    assert r.get('status') in ok, (cap, r)
    return r


def w_washer_loaded(lab):
    """The operator has loaded the baskets and left the door open."""
    world_call(lab, WASH, 'door.open', {})


def agent_call(lab, iid, cap, args, ok=('succeeded',)):
    """An action the agent itself did earlier (its own principal: only the starter may stop or interrupt a run)."""
    _N[0] += 1
    r = lab.backend.invoke('agent', iid, cap, args, 'world-agent-%s-%d' % (cap, _N[0]))
    assert r.get('status') in ok, (cap, r)
    return r


def w_washer_running(lab):
    agent_call(lab, WASH, 'programme.select', {'programme_id': 'quick_wash'})
    agent_call(lab, WASH, 'programme.start', {})
    lab.backend.advance(300)


def w_autoclave_running(lab):
    """A tray of instruments is in the autoclave and a 134 C unwrapped-instrument cycle was started 5 min ago."""
    world_call(lab, AUTO, 'lid.set', {'open': True})
    lab.backend.advance(2)
    world_call(lab, AUTO, 'load.add', {'load_id': 'tray_1', 'kind': 'solid', 'mass_kg': 1.2, 'material': 'stainless_316'})
    world_call(lab, AUTO, 'lid.set', {'open': False})
    lab.backend.advance(3)
    world_call(lab, AUTO, 'program.select', {'program': 'unwrapped_instruments'})
    world_call(lab, AUTO, 'cycle.start', {}, ok=('accepted', 'succeeded'))
    lab.backend.advance(300)


def w_autoclave_wrong(lab):
    agent_call(lab, AUTO, 'program.select', {'program': 'liquids_121'})
    agent_call(lab, AUTO, 'cycle.start', {}, ok=('accepted', 'succeeded'))
    lab.backend.advance(120)


def _calls(c, iid, cap):
    return [x for e in c.lab.core.log.entries for x in e['backend_calls']
            if x.get('op') == 'invoke' and x.get('capability') == cap and x.get('status') == 'succeeded' and e.get('argv', [None, None, None])[2:3] == [iid]]


# ------------------------------------------------------------------ checkers
def c_wash(c):
    e = c.ev(WASH)
    c.equal('universal programme', e['programme.selected'], 'universal')
    c.add('drying on', e['programme.drying'])
    c.add('a programme ran to its end (cycle counter)', (e.get('programme.cycle_counter') or 0) >= 1, e.get('programme.cycle_counter'))
    c.add('no programme running now', not e['program.running'])
    c.add('not cancelled', e['programme.status'] not in ('cancelled', 'malfunction'), e['programme.status'])
    c.add('door open for unloading', not e['door.closed'])


def c_restart(c):
    e = c.ev(WASH)
    c.equal('programme running again', e['programme.status'], 'running')
    c.equal('quick-wash programme', e['programme.selected'], 'quick_wash')
    c.add('door closed', e['door.closed'])
    c.add('the first run was cancelled and a new one started (cycle counter 2)', (e.get('programme.cycle_counter') or 0) >= 2, e.get('programme.cycle_counter'))
    c.add('the door was opened in between', bool(_calls(c, WASH, 'door.open')))


def c_media(c):
    e = c.ev(AUTO)
    cur = e.get('program.current') or {}
    c.equal('custom_1 program', cur.get('program'), 'custom_1')
    c.near('121 C', cur.get('temperature_k'), K0 + 121, 1e-6)
    c.near('20 min', cur.get('time_s'), 1200.0, 1e-6)
    c.equal('slow liquid exhaust', cur.get('exhaust'), 'slow_liquids')
    c.near('end temperature 80 C', cur.get('end_temperature_k'), K0 + 80, 1e-6)
    st = e.get('cycle.status') or {}
    c.equal('the cycle ended normally', st.get('result'), 'CYCLE ENDED')
    c.add('the bottle is in the chamber', any((x.get('load_id') if isinstance(x, dict) else x) == 'media_1' for x in (e.get('loads.present') or [])))
    c.add('lid open', (e.get('lid.angle_rad') or 0) > 0)
    jobs = [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == AUTO]
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in jobs), [(j.handle, j.state) for j in jobs])


def c_unload(c):
    e = c.ev(AUTO)
    st = e.get('cycle.status') or {}
    c.equal('the cycle was not aborted (it ended normally)', st.get('result'), 'CYCLE ENDED')
    c.add('lid open', (e.get('lid.angle_rad') or 0) > 0)
    c.add('tray_1 taken out', not any((x.get('load_id') if isinstance(x, dict) else x) == 'tray_1' for x in (e.get('loads.present') or [])))


def c_abort(c):
    e = c.ev(AUTO)
    st = e.get('cycle.status') or {}
    c.add('the cycle was stopped', st.get('stage') in ('exhaust', 'failed') and st.get('result') != 'CYCLE ENDED', st)
    c.add('it was stopped by a stop command', bool(_calls(c, AUTO, 'cycle.stop')))


GOLD_W01 = r'''chemlab run miele_plw6111 program universal
chemlab run miele_plw6111 configure drying=on
chemlab run miele_plw6111 start --wait --max 3h --json > s.json; rc=$?
jq -r '.error.code // "started"' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "DOOR_OPEN"' s.json >/dev/null; then
  chemlab run miele_plw6111 close door
  chemlab run miele_plw6111 start --wait --max 3h
fi
chemlab run miele_plw6111 open door
chemlab run miele_plw6111 status'''

GOLD_W02 = r'''chemlab run miele_plw6111 open door --json > o.json; rc=$?
jq -r '.error.code + ": " + .error.fix' o.json
if [ $rc -eq 3 ] && jq -e '.error.code == "DOOR_LOCKED"' o.json >/dev/null; then
  chemlab run miele_plw6111 stop
  chemlab run miele_plw6111 open door
fi
chemlab run miele_plw6111 close door
chemlab run miele_plw6111 start
chemlab run miele_plw6111 status'''

GOLD_W03 = r'''chemlab run tuttnauer_3870elvd configure temp=121C time=20min exhaust=slow_liquids end_temp=80C --json > c.json; rc=$?
jq -r '.error.code + ": " + .error.fix' c.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_EDITABLE"' c.json >/dev/null; then
  chemlab run tuttnauer_3870elvd program custom_1
  chemlab run tuttnauer_3870elvd configure temp=121C time=20min exhaust=slow_liquids end_temp=80C
fi
chemlab run tuttnauer_3870elvd open lid
chemlab run tuttnauer_3870elvd load media_1 --kind liquid --mass 700g --liquid water --volume 1L --material borosilicate_glass --closure loose
chemlab run tuttnauer_3870elvd close lid
job=$(chemlab run tuttnauer_3870elvd start --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 5h
for i in 1 2 3 4 5 6 7 8 9 10 11 12; do
  chemlab run tuttnauer_3870elvd open lid --json > o.json && break
  jq -r '.error.code + ": " + .error.message' o.json
  chemlab wait 600
done
chemlab run tuttnauer_3870elvd status'''

GOLD_W04 = r'''for i in 1 2 3 4 5 6 7 8; do
  chemlab run tuttnauer_3870elvd open lid --json > o.json && break
  jq -r '.error.code + ": " + .error.fix' o.json
  chemlab wait 900
done
chemlab run tuttnauer_3870elvd unload tray_1
chemlab run tuttnauer_3870elvd status'''

TASKS = [
    {'id': 'ws01_wash_glassware', 'kind': 'recovery: door open + long action', 'instruments': [WASH], 'world': w_washer_loaded, 'check': c_wash,
     'instruction': 'The glassware is in the baskets of the Miele PLW 6111 washer (miele_plw6111). Wash it with the universal programme with '
                    'drying, wait until the programme has ended, then open the door for unloading.', 'gold': GOLD_W01},
    {'id': 'ws02_washer_restart', 'kind': 'recovery: door locked during a programme', 'instruments': [WASH], 'world': w_washer_running, 'check': c_restart,
     'instruction': 'You started a quick-wash programme on the Miele washer (miele_plw6111) and a beaker was forgotten. Get the door open so the '
                    'operator can put it in, close it again and run the quick-wash programme from the start.', 'gold': GOLD_W02},
    {'id': 'ws03_autoclave_media', 'kind': 'recovery: program not editable + job', 'instruments': [AUTO], 'check': c_media,
     'instruction': 'Sterilize a 1 L borosilicate bottle with 700 g of water (cap loose, load id media_1) in the Tuttnauer 3870ELV-D autoclave '
                    '(tuttnauer_3870elvd): 121 C for 20 min, slow liquid exhaust, 80 C end temperature, in a custom program. Follow the cycle '
                    'with a job, and open the lid when it has finished.', 'gold': GOLD_W03},
    {'id': 'ws04_autoclave_unload', 'kind': 'recovery: lid locked (wait for the end)', 'instruments': [AUTO], 'world': w_autoclave_running,
     'check': c_unload, 'instruction': 'A tray of instruments (load tray_1) is being sterilized in the Tuttnauer autoclave (tuttnauer_3870elvd). '
                                       'Take it out as soon as it is safe to open the lid; do not abort the cycle.', 'gold': GOLD_W04},
    {'id': 'ws05_autoclave_abort', 'kind': 'single step (stop a wrong cycle)', 'instruments': [AUTO], 'world': w_autoclave_wrong, 'check': c_abort,
     'instruction': 'You started the wrong program on the Tuttnauer autoclave (tuttnauer_3870elvd) two minutes ago. Stop the cycle.',
     'gold': 'chemlab run tuttnauer_3870elvd status; chemlab run tuttnauer_3870elvd stop; chemlab run tuttnauer_3870elvd status'},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'wash_sterilize'
    _t['config'] = world_config


def main(only=None, noact=True):
    return run_suite('wash_sterilize', TASKS, only=only, noact=noact)

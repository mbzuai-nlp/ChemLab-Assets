"""sample_intro class acceptance tasks (8): gold solutions use `chemlab run`; the checkers read the evaluator view and the lab's own
request log (what reached the instrument).  World: tasks/sample_intro_world.py plus per-task host steps."""
import math

from .common import run_suite
from .sample_intro_world import config as world_config
from .checks_common import last_args, native_calls, text_has, used_job, world_call

K0 = 273.15
HS, ATOMX, EGA, UNITY, LASER = 'agilent_hs8697', 'teledyne_atomxxyz', 'frontier_egapy', 'markes_unityxr', 'teledyne_analyteexcite'


# ------------------------------------------------------------------ worlds
def w_hs_ready_lid_open(lab):
    """A colleague loaded vial v1 at position 1, set the zones and the method, and left the lid open."""
    world_call(lab, HS, 'lid.set_angle', {'angle_rad': math.pi / 2})
    world_call(lab, HS, 'sample.load', {'sample_id': 'v1', 'slot_id': 'tray_01'})
    for z, k in (('oven', K0 + 70), ('loop_valve', K0 + 80), ('transfer_line', K0 + 90)):
        world_call(lab, HS, 'thermal.set_temperature', {'zone': z, 'temperature_K': k})
    world_call(lab, HS, 'method.set_equilibration_time', {'duration_s': 300.0})


def w_atomx_stopped_run(lab):
    """The previous user started vial 1 and stopped the run halfway: the sparge vessel still holds the sample."""
    ex = lab.backend.instruments[ATOMX].contract['capabilities']['method.configure']['example_arguments']
    world_call(lab, ATOMX, 'power.set', {'enabled': True})
    world_call(lab, ATOMX, 'method.configure', ex)
    world_call(lab, ATOMX, 'acquisition.start', {'vial_index': 0})
    lab.backend.advance(120)
    world_call(lab, ATOMX, 'acquisition.stop', {})


def w_unity_lid_open(lab):
    """The tube was loaded and the lid left open; the method (no re-collection) is set and the desorber is on."""
    ex = dict(lab.backend.instruments[UNITY].contract['capabilities']['method.configure']['example_arguments'], recollect=False)
    world_call(lab, UNITY, 'power.set', {'enabled': True})
    world_call(lab, UNITY, 'method.configure', ex)
    world_call(lab, UNITY, 'lid.set', {'lift_m': 0.18})


# ------------------------------------------------------------------ checkers
def c_hs_sequence(c):
    e = c.ev(HS)
    c.equal('two injections', e.get('measurement.count'), 2)
    c.equal('vials v1 then v2 injected', [x.get('sample_id') for x in e.get('injection.log') or []], ['v1', 'v2'])
    c.near('oven set point 70 °C', e.get('thermal.oven.setpoint_k'), K0 + 70, 1e-6)
    c.near('loop set point 80 °C', e.get('thermal.loop_valve.setpoint_k'), K0 + 80, 1e-6)
    c.near('transfer line set point 90 °C', e.get('thermal.transfer_line.setpoint_k'), K0 + 90, 1e-6)
    c.near('equilibration 10 min', e.get('method.equilibration_time_s'), 600.0, 1e-6)
    c.add('sequence finished', e.get('hs.sequence') is None, e.get('hs.sequence'))
    used_job(c, HS)


def c_hs_lid(c):
    e = c.ev(HS)
    c.equal('one injection', e.get('measurement.count'), 1)
    c.add('lid closed', e.get('lid.closed') is True, e.get('lid.closed'))
    c.add('vial v1 injected', [x.get('sample_id') for x in e.get('injection.log') or []] == ['v1'], e.get('injection.log'))


def c_atomx(c):
    e = c.ev(ATOMX)
    m = last_args(c, ATOMX, 'method.configure') or {}
    c.near('purge 40 °C', m.get('purge_k'), K0 + 40, 1e-6)
    c.near('purge 10 min', m.get('purge_s'), 600.0, 1e-6)
    c.near('desorb 250 °C', m.get('desorb_k'), K0 + 250, 1e-6)
    starts = native_calls(c, ATOMX, 'acquisition.start')
    c.add('vial at tray position 1 run (vial_index 0)', bool(starts) and starts[-1][1].get('vial_index') == 0, starts)
    c.equal('run succeeded', e.get('acquisition.phase'), 'succeeded')
    used_job(c, ATOMX)


def c_atomx_recover(c):
    e = c.ev(ATOMX)
    starts = native_calls(c, ATOMX, 'acquisition.start')
    c.add('vial at tray position 3 run (vial_index 2)', bool(starts) and starts[-1][1].get('vial_index') == 2, starts)
    c.add('the sparger was rinsed', bool(native_calls(c, ATOMX, 'system.rinse')))
    c.equal('run succeeded', e.get('acquisition.phase'), 'succeeded')
    text_has(c, 'blocked.txt names the refusal (NOT_READY)', 'blocked.txt', 'NOT_READY')


def c_ega(c):
    e = c.ev(EGA)
    m = last_args(c, EGA, 'method.configure') or {}
    c.equal('single shot', m.get('mode'), 'single')
    c.near('pyrolysis 600 °C', m.get('pyrolysis_k'), K0 + 600, 1e-6)
    c.equal('run succeeded', e.get('acquisition.phase'), 'succeeded')
    c.equal('one GC start', e.get('gc.trigger_count'), 1)
    c.add('the cup was dropped', bool(native_calls(c, EGA, 'cup.drop')))
    c.near('gc_starts.txt reports 1', c.number_file('gc_starts.txt'), 1, 0)


def c_unity_lid(c):
    e = c.ev(UNITY)
    c.add('lid closed', (e.get('lid.lift_m') or 0) == 0, e.get('lid.lift_m'))
    c.equal('run succeeded', e.get('acquisition.phase'), 'succeeded')
    c.equal('one GC start', e.get('gc.trigger_count'), 1)
    c.equal('leak test passed', e.get('leak.result'), 'passed')


def c_laser(c):
    e = c.ev(LASER)
    m = last_args(c, LASER, 'method.configure') or {}
    c.equal('spot mode', m.get('mode'), 'spot')
    c.equal('20 shots', m.get('shots'), 20)
    c.near('80 µm spot', m.get('spot_size_m'), 8e-05, 1e-12)
    c.equal('20 pulses fired', e.get('laser.pulse_count'), 20)
    c.equal('run succeeded', e.get('acquisition.phase'), 'succeeded')
    c.near('pulses.txt reports 20', c.number_file('pulses.txt'), 20, 0)


def c_unity_limit(c):
    e = c.ev(UNITY)
    m = last_args(c, UNITY, 'method.configure') or {}
    c.near('tube desorbed at 350 °C (the sorbent limit)', m.get('tube_k'), K0 + 350, 1e-6)
    c.equal('run succeeded', e.get('acquisition.phase'), 'succeeded')
    text_has(c, 'limit.txt names the refusal (TOO_HOT)', 'limit.txt', 'TOO_HOT')


# ------------------------------------------------------------------ gold solutions
GOLD_HS = r'''chemlab run agilent_hs8697 load v1 --vial 1
chemlab run agilent_hs8697 load v2 --vial 2
chemlab run agilent_hs8697 set-temp 70C --zone oven
chemlab run agilent_hs8697 set-temp 80C --zone loop
chemlab run agilent_hs8697 set-temp 90C --zone transfer
chemlab run agilent_hs8697 method equilibration=10min
job=$(chemlab run agilent_hs8697 start --vials 1,2 --json | jq -r .job)
chemlab wait "$job" --max 3h
chemlab run agilent_hs8697 result'''

GOLD_HS_LID = r'''chemlab run agilent_hs8697 start --vials 1 --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_OPEN"' s.json >/dev/null; then
  chemlab run agilent_hs8697 close lid
  chemlab run agilent_hs8697 start --vials 1 --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 3h'''

GOLD_ATOMX = r'''chemlab run teledyne_atomxxyz power on
chemlab run teledyne_atomxxyz method purge-temp=40C purge-time=10min desorb-temp=250C
job=$(chemlab run teledyne_atomxxyz start --vial 1 --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run teledyne_atomxxyz result'''

GOLD_ATOMX_RECOVER = r'''chemlab run teledyne_atomxxyz start --vial 3 --json > s.json; rc=$?
jq -r '.error.code' s.json > blocked.txt; cat blocked.txt
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_READY"' s.json >/dev/null; then
  chemlab run teledyne_atomxxyz rinse
  chemlab run teledyne_atomxxyz start --vial 3 --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 1h'''

GOLD_EGA = r'''chemlab run frontier_egapy power on
chemlab run frontier_egapy method mode=single pyrolysis-temp=600C pyrolysis-time=0.1min
job=$(chemlab run frontier_egapy start --json | jq -r .job)
chemlab run frontier_egapy inject --wait --max 30min
chemlab wait "$job" --max 1h
chemlab run frontier_egapy status --json | jq -r '.state["GC starts"]' > gc_starts.txt; cat gc_starts.txt'''

GOLD_UNITY_LID = r'''chemlab run markes_unityxr start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "COVER_OPEN"' s.json >/dev/null; then
  chemlab run markes_unityxr close lid
  chemlab run markes_unityxr start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 1h
chemlab run markes_unityxr status'''

GOLD_LASER = r'''chemlab run teledyne_analyteexcite power on
chemlab run teledyne_analyteexcite position 30mm 30mm 10mm
chemlab run teledyne_analyteexcite method mode=spot shape=square spot=80um shots=20 'path_m=[[0.03,0.03,0.01]]'
job=$(chemlab run teledyne_analyteexcite start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run teledyne_analyteexcite status --json | jq -r '.state.pulses' > pulses.txt; cat pulses.txt'''

GOLD_UNITY_LIMIT = r'''chemlab run markes_unityxr power on
chemlab run markes_unityxr method mode=analyze desorb-temp=380C recollect=false --json > m.json; rc=$?
if [ $rc -eq 3 ]; then
  jq -r '.error.code + ": " + .error.message' m.json > limit.txt
  chemlab run markes_unityxr method mode=analyze desorb-temp=350C recollect=false
fi
cat limit.txt
job=$(chemlab run markes_unityxr start --json | jq -r .job)
chemlab wait "$job" --max 1h'''


TASKS = [
    {'id': 'si01_headspace_sequence', 'kind': 'multi-step + long action (job)', 'instruments': [HS], 'check': c_hs_sequence,
     'instruction': 'On the Agilent 8697 headspace sampler (agilent_hs8697), load the lab\'s vials v1 and v2 into tray positions 1 and 2, '
                    'set the oven to 70 °C, the loop to 80 °C and the transfer line to 90 °C, use a 10 min equilibration time, and run '
                    'a headspace sequence of both vials. Wait until both are injected.', 'gold': GOLD_HS},
    {'id': 'si02_recover_hs_lid', 'kind': 'recovery: lid left open', 'instruments': [HS], 'world': w_hs_ready_lid_open, 'check': c_hs_lid,
     'instruction': 'Vial v1 is in tray position 1 of the Agilent 8697 (agilent_hs8697) and its method is set. Run it as a one-vial '
                    'headspace sequence and wait until it has been injected.', 'gold': GOLD_HS_LID},
    {'id': 'si03_purge_and_trap', 'kind': 'multi-step (method keys with units) + job', 'instruments': [ATOMX], 'check': c_atomx,
     'instruction': 'On the Teledyne Tekmar Atomx XYZ purge-and-trap sampler (teledyne_atomxxyz), purge at 40 °C for 10 min and desorb '
                    'the trap at 250 °C (other settings as documented), then run the vial at tray position 1 and wait for the run to end.',
     'gold': GOLD_ATOMX},
    {'id': 'si04_recover_atomx_stopped_run', 'kind': 'recovery: sparger not rinsed after a stopped run', 'instruments': [ATOMX],
     'world': w_atomx_stopped_run, 'check': c_atomx_recover,
     'instruction': 'Run the vial at tray position 3 of the Atomx XYZ (teledyne_atomxxyz) with the current method and wait for the run. '
                    'If the instrument refuses at first, write the error code into blocked.txt and fix the cause.',
     'gold': GOLD_ATOMX_RECOVER},
    {'id': 'si05_pyrolysis_single_shot', 'kind': 'job + manual step (inject)', 'instruments': [EGA], 'check': c_ega,
     'instruction': 'Run a single-shot pyrolysis of the loaded sample cup on the Frontier EGA/PY-3030D (frontier_egapy) at 600 °C for '
                    '6 s, then write the number of GC starts it gave into gc_starts.txt.', 'gold': GOLD_EGA},
    {'id': 'si06_recover_unity_lid', 'kind': 'recovery: lid left open', 'instruments': [UNITY], 'world': w_unity_lid_open,
     'check': c_unity_lid,
     'instruction': 'The UNITY-xr thermal desorber (markes_unityxr) has its tube and method ready. Desorb the tube into the GC and '
                    'wait for the run to end.', 'gold': GOLD_UNITY_LID},
    {'id': 'si07_laser_spot', 'kind': 'multi-step (stage, method, job) + report', 'instruments': [LASER], 'check': c_laser,
     'instruction': 'On the Analyte Excite+ laser ablation system (teledyne_analyteexcite), move the stage to x 30 mm, y 30 mm, z 10 mm, '
                    'set a spot method there (square 80 µm spot, 20 shots), ablate, and write the number of laser pulses fired into '
                    'pulses.txt.', 'gold': GOLD_LASER},
    {'id': 'si08_recover_sorbent_limit', 'kind': 'recovery: setting refused by the instrument', 'instruments': [UNITY],
     'check': c_unity_limit,
     'instruction': 'Desorb the tube in the UNITY-xr (markes_unityxr) at 380 °C in analyze mode without re-collection. If the instrument '
                    'does not allow that temperature, write its error code and message into limit.txt and use the highest temperature '
                    'it allows instead (350 °C); then run and wait for the end.', 'gold': GOLD_UNITY_LIMIT},
]
for _t in TASKS:
    _t['class'] = 'sample_intro'
    _t['config'] = world_config


def main(only=None, noact=True):
    return run_suite('sample_intro', TASKS, only=only, noact=noact)

"""electrochem class acceptance tasks (6): gold solutions use `chemlab run`; the checkers read the evaluator view (true film mass,
currents, positions) and the agent's report files.  World: tasks/electrochem_world.py plus per-task host steps."""
import json

from .common import run_suite
from .electrochem_world import config as world_config
from .checks_common import last_args, native_calls, text_has, used_job, world_call

GAMRY, EQCM, SECM, RRDE = 'gamry_reference620', 'ch_chi440c', 'ch_chi920d', 'pine_wavevortex10'
RPM = 2 * 3.141592653589793 / 60


# ------------------------------------------------------------------ worlds
def w_gamry_cell_only(lab):
    """A colleague switched the potentiostat on, set a potential step and registered only the cell."""
    world_call(lab, GAMRY, 'power.set', {'enabled': True})
    world_call(lab, GAMRY, 'slot.set_connection', {'port_id': 'cell', 'connection_id': 'cell_01'})
    world_call(lab, GAMRY, 'electrochem.configure_waveform', {'program': {'mode': 'potential', 'sample_interval_s': 0.1, 'values': [0.5] * 20}})


def w_rrde_enclosure_open(lab):
    """The rotator was turning at 1000 rpm when someone opened the enclosure window (the motor stops)."""
    world_call(lab, RRDE, 'power.set', {'enabled': True})
    world_call(lab, RRDE, 'speed.set', {'angular_speed_rad_s': 1000 * RPM})
    world_call(lab, RRDE, 'motor.run', {'enabled': True})
    lab.backend.advance(3)
    world_call(lab, RRDE, 'enclosure.set', {'lift_m': 0.285})
    lab.backend.advance(3)


# ------------------------------------------------------------------ checkers
def c_gamry_cv(c):
    e = c.ev(GAMRY)
    prog = e.get('waveform.program') or {}
    vals = prog.get('values') or []
    c.equal('waveform played to the end', e.get('waveform.status'), 'completed')
    c.add('cv from 0.6 V to -0.2 V and back', bool(vals) and abs(vals[0] - 0.6) < 1e-9 and abs(min(vals) + 0.2) < 1e-9 and abs(vals[-1] - 0.6) < 1e-9,
          (vals[:2], min(vals) if vals else None, vals[-1:]))
    c.near('2 mV steps at 50 mV/s (40 ms per point)', prog.get('sample_interval_s'), 0.04, 1e-9)
    c.equal('801 points played', e.get('waveform.sample_count'), 801)
    t = c.work_file('cv.json')
    try:
        n = len(((json.loads(t) or {}).get('data') or {}).get('samples') or []) if t else 0
    except ValueError:
        n = 0
    c.equal('cv.json holds the 801 recorded samples', n, 801)
    used_job(c, GAMRY)


def c_gamry_recover(c):
    e = c.ev(GAMRY)
    for port, ident in (('working_electrode', 'we_01'), ('reference_electrode', 'ref_01'), ('counter_electrode', 'ce_01')):
        c.equal('%s = %s' % (port, ident), e.get('slot.%s.connection_id' % port), ident)
    c.equal('waveform played to the end', e.get('waveform.status'), 'completed')
    text_has(c, 'why.txt names the refusal (NOT_CONNECTED)', 'why.txt', 'NOT_CONNECTED')


def c_eqcm(c):
    e = c.ev(EQCM)
    m = last_args(c, EQCM, 'method.configure') or {}
    c.equal('constant-current technique', m.get('mode'), 'current')
    c.add('10 µA for 2 s', [(s.get('current_a'), s.get('duration_s')) for s in m.get('steps') or []] == [(1e-05, 2.0)], m.get('steps'))
    c.equal('run succeeded', e.get('acquisition.phase'), 'succeeded')
    dm = e.get('mass.inferred_delta_kg')
    c.add('film grew (inferred mass gain)', isinstance(dm, float) and dm > 0, dm)
    c.near('mass.txt reports the gain in µg', c.number_file('mass.txt'), (dm or 0) * 1e9, 1e-4)
    c.add('the baseline was zeroed before the run', bool(native_calls(c, EQCM, 'baseline.zero')))


def c_secm(c):
    e = c.ev(SECM)
    m = last_args(c, SECM, 'method.configure') or {}
    c.equal('constant-height line scan', m.get('mode'), 'constant_height')
    c.equal('5 path points', len(m.get('path') or []), 5)
    c.near('tip at 0.5 V', m.get('tip_potential_v'), 0.5, 1e-12)
    c.equal('run succeeded', e.get('acquisition.phase'), 'succeeded')
    c.near('points.txt reports 5', c.number_file('points.txt'), 5, 0)
    c.near('probe parked at z 1 mm afterwards', e.get('stage.z_m'), 0.001, 1e-8)


def c_rrde(c):
    e = c.ev(RRDE)
    c.near('rotation 1600 rpm', e.get('speed.target_rad_s'), 1600 * RPM, 1e-6)
    c.equal('recording succeeded', e.get('acquisition.phase'), 'succeeded')
    d = e.get('disk.current_a')
    c.near('disk.txt reports the disk current in µA', c.number_file('disk.txt'), (d or 0) * 1e6, 0.01)
    c.add('a cathodic (negative) disk current for oxygen reduction', isinstance(d, float) and d < 0, d)


def c_rrde_recover(c):
    e = c.ev(RRDE)
    c.add('enclosure closed', (e.get('enclosure.lift_m') or 0) == 0, e.get('enclosure.lift_m'))
    c.equal('recording succeeded', e.get('acquisition.phase'), 'succeeded')
    text_has(c, 'why.txt names the refusal (ENCLOSURE_OPEN)', 'why.txt', 'ENCLOSURE_OPEN')


# ------------------------------------------------------------------ gold solutions
GOLD_CV = r'''chemlab run gamry_reference620 power on
chemlab run gamry_reference620 connect working=we_01 reference=ref_01 counter=ce_01 cell=cell_01
chemlab run gamry_reference620 technique cv --from 0.6V --to -0.2V --rate 50mV/s --step 2mV
job=$(chemlab run gamry_reference620 start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run gamry_reference620 data --save-to cv.json'''

GOLD_GAMRY_RECOVER = r'''chemlab run gamry_reference620 start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.message' s.json | tee why.txt
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_CONNECTED"' s.json >/dev/null; then
  chemlab run gamry_reference620 connect working=we_01 reference=ref_01 counter=ce_01
  chemlab run gamry_reference620 start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 10min
chemlab run gamry_reference620 status'''

GOLD_EQCM = r'''chemlab run ch_chi440c power on
chemlab wait 5
chemlab run ch_chi440c zero
chemlab run ch_chi440c technique cp --current 10uA --time 2s
job=$(chemlab run ch_chi440c start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run ch_chi440c status --json | jq -r '.state["mass change"] * 1000000000' > mass.txt
cat mass.txt'''

GOLD_SECM = r'''chemlab run ch_chi920d power on
chemlab run ch_chi920d technique secm-line --path '-40um,0um,5um;-20um,0um,5um;0um,0um,5um;20um,0um,5um;40um,0um,5um' --tip 0.5V --substrate 0V
job=$(chemlab run ch_chi920d start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run ch_chi920d status --json | jq -r '.state.points' > points.txt
chemlab run ch_chi920d move 0um 0um 1mm
cat points.txt'''

GOLD_RRDE = r'''chemlab run pine_wavevortex10 power on
chemlab run pine_wavevortex10 rotate 1600rpm
chemlab wait 3
job=$(chemlab run pine_wavevortex10 start --time 5s --json | jq -r .job)
chemlab wait "$job" --max 5min
chemlab run pine_wavevortex10 status --json | jq -r '.state.disk * 1000000' > disk.txt
cat disk.txt'''

GOLD_RRDE_RECOVER = r'''chemlab run pine_wavevortex10 start --time 3s --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json | tee why.txt
if [ $rc -eq 3 ] && jq -e '.error.code == "ENCLOSURE_OPEN"' s.json >/dev/null; then
  chemlab run pine_wavevortex10 close enclosure
  chemlab wait 3
  chemlab run pine_wavevortex10 start --time 3s --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 5min'''


TASKS = [
    {'id': 'ec01_gamry_cv', 'kind': 'multi-step (connect, technique) + job + record file', 'instruments': [GAMRY], 'check': c_gamry_cv,
     'instruction': 'On the Gamry Reference 620 (gamry_reference620), connect working electrode we_01, reference ref_01, counter ce_01 and '
                    'cell cell_01, run one cyclic voltammogram from 0.6 V to -0.2 V and back at 50 mV/s in 2 mV steps, and save the '
                    'recorded series into cv.json.', 'gold': GOLD_CV},
    {'id': 'ec02_recover_gamry_connections', 'kind': 'recovery: electrodes not registered', 'instruments': [GAMRY],
     'world': w_gamry_cell_only, 'check': c_gamry_recover,
     'instruction': 'A colleague prepared a potential step on the Gamry Reference 620 (gamry_reference620). Start it and wait for it to '
                    'finish. If it refuses, write the error code and message into why.txt and fix it; the electrodes on the cell are '
                    'we_01 (working), ref_01 (reference) and ce_01 (counter).', 'gold': GOLD_GAMRY_RECOVER},
    {'id': 'ec03_eqcm_plating', 'kind': 'multi-step (zero, technique) + job + report (units)', 'instruments': [EQCM], 'check': c_eqcm,
     'instruction': 'On the CHI440C EQCM (ch_chi440c) with the copper bath, switch on, zero the frequency baseline once the crystal '
                    'reading is valid, plate at a constant 10 µA for 2 s, and write the inferred mass gain of the film in µg into '
                    'mass.txt.', 'gold': GOLD_EQCM},
    {'id': 'ec04_secm_line', 'kind': 'technique with a probe path + job + move', 'instruments': [SECM], 'check': c_secm,
     'instruction': 'On the CHI920D SECM (ch_chi920d), scan the tip at constant height 5 µm along y = 0 from x = -40 µm to 40 µm in '
                    '20 µm steps with the tip at 0.5 V and the substrate at 0 V. Write the number of recorded points into points.txt '
                    'and afterwards park the probe at x 0, y 0, z 1 mm.', 'gold': GOLD_SECM},
    {'id': 'ec05_rrde_record', 'kind': 'rotation + timed recording (job) + report', 'instruments': [RRDE], 'check': c_rrde,
     'instruction': 'On the Pine WaveVortex 10 rotator (pine_wavevortex10), rotate the ring-disk electrode at 1600 rpm, record the '
                    'ring and disk currents for 5 s, and write the last disk current in µA into disk.txt.', 'gold': GOLD_RRDE},
    {'id': 'ec06_recover_rrde_enclosure', 'kind': 'recovery: enclosure window open', 'instruments': [RRDE],
     'world': w_rrde_enclosure_open, 'check': c_rrde_recover,
     'instruction': 'The rotator (pine_wavevortex10) is set to 1000 rpm. Record the ring and disk currents for 3 s. If it refuses, '
                    'write the error code and the fix into why.txt and fix it.', 'gold': GOLD_RRDE_RECOVER},
]
for _t in TASKS:
    _t['class'] = 'electrochem'
    _t['config'] = world_config


def main(only=None, noact=True):
    return run_suite('electrochem', TASKS, only=only, noact=noact)

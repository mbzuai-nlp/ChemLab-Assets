"""Agitation class acceptance tasks (10): gold solutions use `chemlab run` (class verbs, plus the shared `power` / `open` / `close`
and the thermal verbs where the instrument is in both classes); checkers read the evaluator view (process truth where the twin has
it).  World: agitation_world (tubes registered, rotor selected, cup / vessel / flask / probe set up by the operator)."""
import math

from .agitation_world import config as world_config
from .common import run_suite

K0 = 273.15
RPM = 2 * math.pi / 60
CF, MIX, KS, T25, VCX, ELMA = ('eppendorf_5910ri', 'thinky_are310s', 'ika_ks4000', 'ika_t25', 'sonics_vcx750', 'elma_elmasonics60h')


def cfg(c):
    return world_config(c)


_N = [0]


def world_call(lab, iid, cap, args):
    _N[0] += 1
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d-%d' % (iid, cap, int(lab.backend.now() * 10), _N[0]))
    assert r.get('status') == 'succeeded', (cap, args, r)


def jobs_of(c, iid):
    return [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == iid]


# ------------------------------------------------------------------ worlds
def w_cf_unbalanced(lab):
    world_call(lab, CF, 'sample.load', {'slot_id': 'slot_1', 'sample_id': 'tube_a'})
    world_call(lab, CF, 'sample.load', {'slot_id': 'slot_2', 'sample_id': 'tube_c'})
    world_call(lab, CF, 'centrifuge.set_method', {'speed_rad_s': 3000 * RPM, 'temperature_k': K0 + 20, 'duration_s': 60.0, 'acceleration_level': 9,
                                                  'braking_level': 9})
    world_call(lab, CF, 'lid.set_angle', {'angle_rad': 0.0})


def w_mixer_wrong_dial(lab):
    world_call(lab, MIX, 'balance.set', {'gross_mass_kg': 0.10})
    world_call(lab, MIX, 'lid.set_open', {'open': False})
    lab.backend.advance(3)


def w_ks_on_hood_open(lab):
    world_call(lab, KS, 'power.set', {'enabled': True})
    world_call(lab, KS, 'hood.set_angle', {'angle_rad': 1.5707963267948966})


# ------------------------------------------------------------------ checkers
def c_spin(c):
    e = c.ev(CF)
    cv = e.get('measurement.curve') or {}
    sp = [x for x in (cv.get('speed_rad_s') or []) if isinstance(x, (int, float))]
    want = math.sqrt(2000 * 9.80665 / 0.2)
    c.near('spun at 2000 x g on the 0.2 m rotor', max(sp) if sp else None, want, 1.0)
    c.near('chamber set to 4 °C', e.get('method.temperature_k'), K0 + 4, 1e-6)
    c.near('run time 2 min', e.get('method.duration_s'), 120.0, 1e-6)
    c.equal('the run finished', e.get('run.status'), 'succeeded')
    c.near('rcf.txt reports the top RCF (2000 ± 5)', c.number_file('rcf.txt'), 2000.0, 5.0)


def c_rebalance(c):
    e = c.ev(CF)
    loaded = {x['slot_id']: x['sample_id'] for x in e.get('sample.loaded_slots') or []}
    c.equal('tube_b replaced tube_c opposite tube_a', loaded, {'slot_1': 'tube_a', 'slot_2': 'tube_b'})
    c.equal('the run finished', e.get('run.status'), 'succeeded')
    c.add('the lid is closed', e.get('lid.closed'), e.get('lid.closed'))


def c_mix(c):
    e = c.ev(MIX)
    c.equal('the mixing program finished', e.get('cycle.phase'), 'succeeded')
    c.near('balance dial at the cup mass', e.get('balance.gross_mass_kg'), 0.14, 1e-9)
    c.add('the paste is mixed (mixing index > 0.9)', (e.get('truth.mixing_index') or 0) > 0.9, e.get('truth.mixing_index'))


def c_mix_recover(c):
    e = c.ev(MIX)
    c.near('balance dial corrected to 140 g', e.get('balance.gross_mass_kg'), 0.14, 1e-9)
    c.equal('the run finished', e.get('cycle.phase'), 'succeeded')


def c_shaker_job(c):
    e = c.ev(KS)
    c.near('shaking set to 150 rpm', e.get('shaking.target_rad_s'), 150 * RPM, 1e-6)
    c.near('timed 30 min', e.get('timer.duration_s'), 1800.0, 1e-6)
    c.equal('the run finished', e.get('cycle.phase'), 'succeeded')
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in jobs_of(c, KS)), [(j.handle, j.state) for j in jobs_of(c, KS)])


def c_hood(c):
    e = c.ev(KS)
    c.add('hood closed', (e.get('hood.angle_rad') or 0) <= 1e-4, e.get('hood.angle_rad'))
    c.near('shaking set to 120 rpm', e.get('shaking.target_rad_s'), 120 * RPM, 1e-6)
    c.equal('continuous mode', e.get('timer.mode'), 'continuous')
    c.equal('shaking now', e.get('cycle.phase'), 'running')


def c_disperser(c):
    e = c.ev(T25)
    c.near('speed 12000 rpm', e.get('motor.target_rad_s'), 12000 * RPM, 1e-6)
    c.equal('the run finished', e.get('cycle.phase'), 'succeeded')
    c.near('run 40 s', e.get('cycle.elapsed_s'), 40.0, 0.5)
    c.add('droplets were broken up (size below the initial 200 µm)', (e.get('truth.size_m') or 1) < 2e-4, e.get('truth.size_m'))


def c_sonicate(c):
    e = c.ev(VCX)
    c.near('amplitude 50 %', e.get('sonication.amplitude_fraction'), 0.5, 1e-9)
    c.near('temperature limit 35 °C', e.get('thermal.limit_k'), K0 + 35, 1e-6)
    c.equal('the run finished', e.get('cycle.phase'), 'succeeded')
    c.near('2 min of sonication', e.get('cycle.on_time_s'), 120.0, 0.5)


def c_bath(c):
    e = c.ev(ELMA)
    c.equal('degas mode', e.get('ultrasound.mode'), 'degas')
    c.equal('the run finished', e.get('cycle.phase'), 'succeeded')
    c.near('5 min run', e.get('timer.elapsed_s'), 300.0, 1.0)


def c_sonic_order(c):
    e = c.ev(VCX)
    c.near('amplitude 60 %', e.get('sonication.amplitude_fraction'), 0.6, 1e-9)
    c.equal('the run finished', e.get('cycle.phase'), 'succeeded')
    c.near('45 s of sonication', e.get('cycle.on_time_s'), 45.0, 0.5)


# ------------------------------------------------------------------ gold solutions
GOLD_SPIN = r'''chemlab run eppendorf_5910ri load tube_a --slot slot_1
chemlab run eppendorf_5910ri load tube_b --slot slot_2
chemlab run eppendorf_5910ri speed --rcf 2000g --time 2min
chemlab run eppendorf_5910ri set-temp 4C
chemlab run eppendorf_5910ri close lid
chemlab run eppendorf_5910ri start --wait --json | jq -r '.max_rcf | round' > rcf.txt; cat rcf.txt'''

GOLD_REBALANCE = r'''chemlab run eppendorf_5910ri start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "IMBALANCE"' s.json >/dev/null; then
  chemlab run eppendorf_5910ri open lid
  chemlab run eppendorf_5910ri unload --slot slot_2
  chemlab run eppendorf_5910ri load tube_b --slot slot_2
  chemlab run eppendorf_5910ri close lid
fi
chemlab run eppendorf_5910ri start --wait'''

GOLD_MIX_RECOVER = r'''chemlab run thinky_are310s speed 1500rpm --time 30s
chemlab run thinky_are310s start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.message' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "IMBALANCE"' s.json >/dev/null; then
  chemlab run thinky_are310s open lid
  chemlab wait 2
  chemlab run thinky_are310s load --mass 140g
  chemlab run thinky_are310s close lid
  chemlab wait 2
fi
chemlab run thinky_are310s start --wait'''

GOLD_SHAKER = r'''chemlab run ika_ks4000 power on
chemlab run ika_ks4000 speed 150rpm --time 30min
job=$(chemlab run ika_ks4000 start --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 1h
chemlab run ika_ks4000 result'''

GOLD_HOOD = r'''chemlab run ika_ks4000 speed 120rpm
chemlab run ika_ks4000 time continuous
chemlab run ika_ks4000 start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "HOOD_OPEN"' s.json >/dev/null; then
  chemlab run ika_ks4000 close hood
  chemlab run ika_ks4000 start
fi
chemlab run ika_ks4000 status'''

GOLD_SONIC_ORDER = r'''chemlab run sonics_vcx750 intensity 60% --json > a.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NO_METHOD"' a.json >/dev/null; then
  chemlab run sonics_vcx750 time 45s
  chemlab run sonics_vcx750 intensity 60%
fi
chemlab run sonics_vcx750 start --wait'''

TASKS = [
    {'id': 'ag01_spin_rcf', 'kind': 'multi-step (load, RCF, temperature, run) + report', 'instruments': [CF], 'check': c_spin,
     'instruction': 'Centrifuge tube_a and tube_b (equal masses) in the Eppendorf 5910 Ri (eppendorf_5910ri; the large rotor is in, lid open) '
                    'at 2000 x g for 2 minutes at 4 °C. Use opposite positions slot_1 and slot_2. Write the top RCF of the run into rcf.txt.',
     'gold': GOLD_SPIN},
    {'id': 'ag02_recover_imbalance', 'kind': 'recovery: rotor out of balance', 'instruments': [CF], 'world': w_cf_unbalanced, 'check': c_rebalance,
     'instruction': 'Run the Eppendorf 5910 Ri (eppendorf_5910ri) with the tubes and settings already in it. If it cannot start because the '
                    'load is out of balance, replace the tube in slot_2 by tube_b (the same mass as tube_a) and run it.', 'gold': GOLD_REBALANCE},
    {'id': 'ag03_mix_defoam', 'kind': 'multi-step (balance, program with two steps, run)', 'instruments': [MIX], 'check': c_mix,
     'instruction': 'Mix the paste in the THINKY ARE-310 (thinky_are310s): the filled cup in its holder weighs 140 g. Mix at 2000 rpm for '
                    '60 s, then defoam at 2200 rpm for 30 s.',
     'gold': 'chemlab run thinky_are310s load --mass 140g && chemlab run thinky_are310s speed 2000rpm --time 60s --defoam 2200rpm --defoam-time 30s && '
             'chemlab run thinky_are310s close lid && chemlab wait 2 && chemlab run thinky_are310s start --wait'},
    {'id': 'ag04_recover_mixer_balance', 'kind': 'recovery: counterbalance set for the wrong mass', 'instruments': [MIX], 'world': w_mixer_wrong_dial,
     'check': c_mix_recover,
     'instruction': 'Mix the cup in the THINKY ARE-310 (thinky_are310s; gross mass 140 g, lid closed) at 1500 rpm for 30 s.', 'gold': GOLD_MIX_RECOVER},
    {'id': 'ag05_shaker_job', 'kind': 'long action: job id (status, wait)', 'instruments': [KS], 'check': c_shaker_job,
     'instruction': 'Shake the flask on the IKA KS 4000 (ika_ks4000) at 150 rpm for 30 minutes. Start it as a job, check its status, then wait '
                    'for it to finish.', 'gold': GOLD_SHAKER},
    {'id': 'ag06_recover_hood_open', 'kind': 'recovery: hood open', 'instruments': [KS], 'world': w_ks_on_hood_open, 'check': c_hood,
     'instruction': 'Start continuous shaking at 120 rpm on the IKA KS 4000 (ika_ks4000) and leave it running.', 'gold': GOLD_HOOD},
    {'id': 'ag07_disperse', 'kind': 'multi-step (power, time, speed, run)', 'instruments': [T25], 'check': c_disperser,
     'instruction': 'Disperse the emulsion with the IKA T 25 ULTRA-TURRAX (ika_t25; tool and vessel are set up) at 12000 rpm for 40 s.',
     'gold': 'chemlab run ika_t25 power on && chemlab run ika_t25 time 40s && chemlab run ika_t25 speed 12000rpm && chemlab run ika_t25 start --wait'},
    {'id': 'ag08_sonicate_limit', 'kind': 'multi-step (method, amplitude, temperature limit, run)', 'instruments': [VCX], 'check': c_sonicate,
     'instruction': 'Sonicate the suspension with the Sonics VCX 750 (sonics_vcx750) at 50 % amplitude for 2 minutes, with a sample temperature '
                    'limit of 35 °C.',
     'gold': 'chemlab run sonics_vcx750 time 2min && chemlab run sonics_vcx750 intensity 50% && chemlab run sonics_vcx750 safety-limit 35C && '
             'chemlab run sonics_vcx750 start --wait'},
    {'id': 'ag09_bath_degas', 'kind': 'multi-step (power, mode, timer, run)', 'instruments': [ELMA], 'check': c_bath,
     'instruction': 'Degas the bath liquid of the Elmasonic S60H (elma_elmasonics60h) for 5 minutes.',
     'gold': 'chemlab run elma_elmasonics60h power on && chemlab run elma_elmasonics60h intensity degas && chemlab run elma_elmasonics60h time 5min && '
             'chemlab run elma_elmasonics60h start --wait'},
    {'id': 'ag10_recover_sonicator_method', 'kind': 'recovery: amplitude before a method', 'instruments': [VCX], 'check': c_sonic_order,
     'instruction': 'Sonicate the suspension with the Sonics VCX 750 (sonics_vcx750) at 60 % amplitude for 45 s.', 'gold': GOLD_SONIC_ORDER},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'agitation'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('agitation', TASKS, only=only, noact=noact)

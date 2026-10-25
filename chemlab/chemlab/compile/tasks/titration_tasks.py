"""titration class acceptance tasks (6): gold solutions use `chemlab run`; the checkers read the evaluator view (true end point,
water, nitrogen) and the agent's report files.  World: tasks/titration_world.py plus per-task host steps."""
from .common import run_suite
from .titration_world import config as world_config
from .checks_common import text_has, used_job, world_call

OMNIS, KF, UDK = 'metrohm_omnis', 'metrohm_899', 'velp_udk159'


# ------------------------------------------------------------------ worlds
def w_omnis_prepared_no_electrode(lab):
    """A colleague prepared the titration (titrant, method, stirring, beaker) but did not register the electrode."""
    for cap, a in (('power.set', {'enabled': True}), ('slot.set_connection', {'port_id': 'titration_cell', 'connection_id': 'beaker_01'}),
                   ('dosing.configure_cylinder', {'volume_m3': 2e-05}), ('titration.configure_titrant', {'concentration_mol_m3': 100.0}),
                   ('titration.configure_method', {'mode': 'dynamic_equivalent', 'dose_increment_m3': 1e-07, 'maximum_volume_m3': 1.5e-05,
                                                   'endpoint_potential_v': None}),
                   ('dosing.set_rate', {'rate_m3_s': 5e-07}), ('titration.configure_stability', {'hold_s': 0.5, 'tolerance_v': 0.0}),
                   ('stirring.set_speed', {'angular_speed_rad_s': 31.41592653589793})):
        world_call(lab, OMNIS, cap, a)


def w_udk_door_open(lab):
    """The door was left open after the tube went in."""
    world_call(lab, UDK, 'door.set_open', {'open': True})
    lab.backend.advance(5)


# ------------------------------------------------------------------ checkers
def c_omnis(c):
    e = c.ev(OMNIS)
    c.equal('titration completed', e.get('titration.status'), 'completed')
    c.near('end point about 10.0 mL', e.get('titration.endpoint_volume_m3'), 1.0e-05, 0.02e-05)
    c.near('content.txt reports about 0.040 mol/L', c.number_file('content.txt'), 0.040, 0.001)
    c.equal('dynamic mode', e.get('method.mode'), 'dynamic_equivalent')
    used_job(c, OMNIS)


def c_omnis_recover(c):
    e = c.ev(OMNIS)
    c.equal('electrode electrode_01 registered', e.get('slot.electrode.connection_id'), 'electrode_01')
    c.equal('titration completed', e.get('titration.status'), 'completed')
    c.add('the refusal was read (NOT_CONNECTED in why.txt)', 'NOT_CONNECTED' in (c.work_file('why.txt') or ''), c.work_file('why.txt'))


def c_kf(c):
    e = c.ev(KF)
    c.equal('determination completed', e.get('coulometry.status'), 'COMPLETED')
    c.near('current 400 mA', e.get('settings.current_a'), 0.4, 1e-9)
    c.near('sample size 10 mg', e.get('settings.sample_mass_kg'), 1e-05, 1e-12)
    w = e.get('measurement.water_mass_kg')
    c.near('water.txt reports the titrated water in mg', c.number_file('water.txt'), (w or 0) * 1e6, 0.002)
    c.near('about 3.6 mg of water (CuSO4·5H2O, 10 mg)', w, 3.607e-06, 0.05e-06)


def c_kf_recover(c):
    e = c.ev(KF)
    c.equal('determination completed', e.get('coulometry.status'), 'COMPLETED')
    f = e.get('measurement.water_fraction')
    c.near('percent.txt reports the water content in %', c.number_file('percent.txt'), (f or 0) * 100, 0.05)
    text_has(c, 'why.txt records the refusals (NO_METHOD)', 'why.txt', 'NO_METHOD')
    c.add('hold and drift compensation set', e.get('settings.endpoint_hold_s') is not None and e.get('settings.drift_compensation_kg_s') is not None,
          (e.get('settings.endpoint_hold_s'), e.get('settings.drift_compensation_kg_s')))


def c_udk(c):
    e = c.ev(UDK)
    c.equal('analysis succeeded', e.get('cycle.phase'), 'succeeded')
    c.near('nitrogen.txt reports the nitrogen in mg', c.number_file('nitrogen.txt'), (e.get('analysis.nitrogen_kg') or 0) * 1e6, 0.05)
    c.near('about 50 mg of nitrogen found', e.get('analysis.nitrogen_kg'), 5.0e-05, 0.1e-05)
    used_job(c, UDK)


def c_udk_door(c):
    e = c.ev(UDK)
    c.add('door closed', not e.get('door.open_target'), e.get('door.open_target'))
    c.equal('analysis succeeded', e.get('cycle.phase'), 'succeeded')
    text_has(c, 'door.txt names the refusal (DOOR_OPEN)', 'door.txt', 'DOOR_OPEN')


# ------------------------------------------------------------------ gold solutions
GOLD_OMNIS = r'''chemlab run metrohm_omnis power on
chemlab run metrohm_omnis connect electrode=electrode_01 cell=beaker_01
chemlab run metrohm_omnis titrant 0.1M --cylinder 20mL
chemlab run metrohm_omnis method mode=dynamic increment=0.1mL max=15mL hold=0.5s tolerance=0V rate=30mL/min
chemlab run metrohm_omnis stir 300rpm
job=$(chemlab run metrohm_omnis start --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run metrohm_omnis result --sample 25mL --json | jq -r '.result.content_mol_m3 / 1000' > content.txt
cat content.txt'''

GOLD_OMNIS_RECOVER = r'''chemlab run metrohm_omnis start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json | tee why.txt
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_CONNECTED"' s.json >/dev/null; then
  chemlab run metrohm_omnis connect electrode=electrode_01
  chemlab run metrohm_omnis start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 1h
chemlab run metrohm_omnis status'''

GOLD_KF = r'''chemlab run metrohm_899 method current=400mA drift-comp=0ug/min hold=1s
chemlab run metrohm_899 sample 10mg
job=$(chemlab run metrohm_899 start --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run metrohm_899 result --json | jq -r '.result.water_mass_kg * 1000000' > water.txt
cat water.txt'''

GOLD_KF_RECOVER = r'''chemlab run metrohm_899 sample 10mg
for i in 1 2 3; do
  chemlab run metrohm_899 start --json > s.json && break
  code=$(jq -r .error.code s.json); echo "$code: $(jq -r .error.fix s.json)" >> why.txt
  [ "$code" = "NO_METHOD" ] || break
  case "$(jq -r .error.message s.json)" in
    *hold*) chemlab run metrohm_899 method hold=1s ;;
    *drift*) chemlab run metrohm_899 method drift-comp=0ug/min ;;
  esac
done
chemlab wait "$(jq -r .job s.json)" --max 1h
chemlab run metrohm_899 result --json | jq -r '.result.water_fraction * 100' > percent.txt
cat why.txt percent.txt'''

GOLD_UDK = r'''chemlab run velp_udk159 titrant 0.2M
job=$(chemlab run velp_udk159 start --json | jq -r .job)
chemlab wait "$job" --max 2h
chemlab run velp_udk159 result --json | jq -r '.result.nitrogen_kg * 1000000' > nitrogen.txt
cat nitrogen.txt'''

GOLD_UDK_DOOR = r'''chemlab run velp_udk159 start --json > s.json; rc=$?
jq -r '.error.code' s.json > door.txt
if [ $rc -eq 3 ] && jq -e '.error.code == "DOOR_OPEN"' s.json >/dev/null; then
  chemlab run velp_udk159 close door
  chemlab wait 10
  chemlab run velp_udk159 start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 2h
chemlab run velp_udk159 result'''


TASKS = [
    {'id': 'ti01_omnis_acid_content', 'kind': 'multi-step (connect, titrant, method, stir) + job + report', 'instruments': [OMNIS],
     'check': c_omnis,
     'instruction': 'The OMNIS titrator (metrohm_omnis) has 25 mL of an acetic acid sample in its beaker. Register electrode electrode_01 '
                    'and beaker beaker_01, use 0.1 M NaOH from a 20 mL cylinder, titrate in dynamic mode (0.1 mL steps, at most 15 mL, '
                    'hold 0.5 s, tolerance 0 V, 30 mL/min) while stirring at 300 rpm, and write the acid concentration in mol/L '
                    'into content.txt.', 'gold': GOLD_OMNIS},
    {'id': 'ti02_recover_omnis_electrode', 'kind': 'recovery: electrode not registered', 'instruments': [OMNIS],
     'world': w_omnis_prepared_no_electrode, 'check': c_omnis_recover,
     'instruction': 'A colleague prepared a titration on the OMNIS (metrohm_omnis). Start it and wait for the end. If it refuses, write '
                    'the error code and the fix into why.txt and correct it; the electrode on the stand is electrode_01.',
     'gold': GOLD_OMNIS_RECOVER},
    {'id': 'ti03_kf_water', 'kind': 'multi-step + job + report (unit conversion)', 'instruments': [KF], 'check': c_kf,
     'instruction': 'On the Metrohm 899 Karl Fischer coulometer (metrohm_899), the lab has added a 10 mg sample. Use a 400 mA generator '
                    'current, no drift compensation (0 µg/min) and a 1 s end-point hold, give the sample size, determine the water and '
                    'write the water found in mg into water.txt.', 'gold': GOLD_KF},
    {'id': 'ti04_recover_kf_method', 'kind': 'recovery: method incomplete (two refusals)', 'instruments': [KF], 'check': c_kf_recover,
     'instruction': 'Determine the water content of the 10 mg sample in the 899 coulometer (metrohm_899) and write it in percent into '
                    'percent.txt. Fix whatever the instrument says is missing (an end-point hold of 1 s and no drift compensation are '
                    'fine); write each refusal into why.txt.', 'gold': GOLD_KF_RECOVER},
    {'id': 'ti05_kjeldahl_nitrogen', 'kind': 'method field + job + report', 'instruments': [UDK], 'check': c_udk,
     'instruction': 'Run the Kjeldahl distillation and titration of the digested sample in the VELP UDK 159 (velp_udk159) with 0.2 M '
                    'titrant and write the nitrogen found in mg into nitrogen.txt.', 'gold': GOLD_UDK},
    {'id': 'ti06_recover_udk_door', 'kind': 'recovery: safety door open', 'instruments': [UDK], 'world': w_udk_door_open,
     'check': c_udk_door,
     'instruction': 'Start the Kjeldahl analysis on the VELP UDK 159 (velp_udk159) and wait for the end. If it refuses, write the error '
                    'code into door.txt and fix it.', 'gold': GOLD_UDK_DOOR},
]
for _t in TASKS:
    _t['class'] = 'titration'
    _t['config'] = world_config


def main(only=None, noact=True):
    return run_suite('titration', TASKS, only=only, noact=noact)

"""Formulation class acceptance tasks (8): gold solutions use `chemlab run`; the checkers read the evaluator view (truth slots
included) and the agent's report files.  Five are recovery tasks (the gold meets the refusal, reads `--json` `.error.code` and follows
the fix)."""
from .common import run_suite
from .formulation_world import config as world_config

K0 = 273.15
P11, LIN, ENC, IGN, GL = 'thermo_process11', 'linari_rtadvanced', 'buchi_encapsulatorb', 'cytiva_nanoassemblrignite', 'glatt_miniglatt'
ML_MIN = 1e-6 / 60


def jobs_of(c, iid):
    return [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == iid]


# ------------------------------------------------------------------ checkers
def c_extrude(c):
    e = c.ev(P11)
    c.add('every barrel zone set to 180 °C', all(abs(t - (K0 + 180)) < 1e-6 for t in e['thermal.targets_k']), e['thermal.targets_k'])
    c.near('die set to 180 °C', e['die.target_k'], K0 + 180, 1e-6)
    c.equal('the extrusion run finished', e['cycle.phase'], 'succeeded')
    c.near('it ran the 20 s recipe', e['cycle.elapsed_s'], 20.0, 0.5)


def c_profile(c):
    e = c.ev(P11)
    want = [160, 170, 180, 180, 180, 180, 185]
    c.add('zone profile 160/170/180/180/180/180/185 °C', all(abs(t - (K0 + w)) < 1e-6 for t, w in zip(e['thermal.targets_k'], want)), e['thermal.targets_k'])
    c.near('die 185 °C', e['die.target_k'], K0 + 185, 1e-6)
    c.add('heating on', e['thermal.enabled'])
    c.add('every zone at its set point (±2 K)', all(abs(e['thermal.zone_%d_k' % i] - (K0 + w)) <= 2.0 for i, w in enumerate(want)),
          [round(e['thermal.zone_%d_k' % i] - K0, 2) for i in range(7)])
    c.near('die.txt reports the die temperature in °C', c.number_file('die.txt'), e['die.temperature_k'] - K0, 0.5)


def c_spin(c):
    e = c.ev(LIN)
    c.equal('the spinning run finished', e['cycle.phase'], 'succeeded')
    c.near('chamber 30 °C', e['thermal.target_k'], K0 + 30, 1e-6)
    c.near('chamber 35 % RH', e['climate.target_rh'], 0.35, 1e-9)
    d = e.get('fiber.mean_diameter_m') or 0
    c.add('fibres were spun', d > 1e-8, d)
    c.near('fiber.txt reports the mean fibre diameter in nm', c.number_file('fiber.txt'), d * 1e9, 1.0)


def c_clean(c):
    e = c.ev(LIN)
    c.equal('the spinning run finished', e['cycle.phase'], 'succeeded')
    c.add('high voltage off', not e['hv.enabled'])
    c.add('door open', e['door.opened'])
    trail = e.get('audit.trail') or []
    c.add('the needles of group 1 were cleaned', any(x.get('event') == 'needle.clean' for x in trail), [x.get('event') for x in trail][-6:])


def c_encap(c):
    e = c.ev(ENC)
    c.equal('the encapsulation run finished', e['cycle.phase'], 'succeeded')
    c.near('vibration 1000 Hz', e['vibration.target_hz'], 1000.0, 1e-9)
    c.near('electrode 1500 V', e['electrostatic.target_v'], 1500.0, 1e-9)
    c.near('nozzle heater 40 °C', e['thermal.target_k'], K0 + 40, 1e-6)
    c.add('feed closed after the run (valve shut, no pressure)', (e.get('valve.target_fraction') or 0) == 0 and (e.get('air.target_pa') or 0) == 0,
          (e.get('valve.target_fraction'), e.get('air.target_pa')))
    c.add('beads were made (polymer in beads)', ((e.get('truth.ledger') or {}).get('bead_polymer_kg') or 0) > 0, (e.get('truth.ledger') or {}).get('bead_polymer_kg'))


def c_lnp(c):
    e = c.ev(IGN)
    c.equal('the formulation run finished', e['cycle.phase'], 'succeeded')
    c.add('the cartridge was used', e['cartridge.used'])
    c.add('both channels heated to 37 °C', e['heating.enabled'] and abs(e['thermal.target_center_k'] - (K0 + 37)) < 1e-6 and
          abs(e['thermal.target_right_k'] - (K0 + 37)) < 1e-6)
    v = e.get('volume.sample_m3') or 0
    c.add('sample collected', v > 1e-7, v)
    c.near('sample.txt reports the sample volume in mL', c.number_file('sample.txt'), v * 1e6, 0.01)
    js = jobs_of(c, IGN)
    c.add('the priming ran as a job that succeeded', len([j for j in js if j.state == 'succeeded']) >= 2, [(j.handle, j.description, j.state) for j in js])


def c_fluidbed(c):
    e = c.ev(GL)
    c.add('switched on', e['power.enabled'])
    c.equal('the fluid-bed run finished', e['cycle.phase'], 'succeeded')
    c.near('inlet air 60 °C', e['thermal.target_k'], K0 + 60, 1e-6)
    c.near('air 240 L/min', e['flow.target_normal_m3_s'], 0.004, 1e-9)
    c.near('spray 3.5 mL/min', e['spray.target_m3_s'], 3.5 * ML_MIN, 1e-14)
    c.add('tower closed', not e['tower.opened'])


def c_climate(c):
    e = c.ev(LIN)
    c.near('chamber 28 °C', e['thermal.target_k'], K0 + 28, 1e-6)
    c.near('chamber 45 % RH', e['climate.target_rh'], 0.45, 1e-9)
    c.add('climate control on', e['climate.enabled'])
    c.near('the chamber is at 28 °C (±1 K)', e['thermal.temperature_k'], K0 + 28, 1.0)
    c.near('rh.txt reports the humidity in %', c.number_file('rh.txt'), e['climate.humidity'] * 100, 1.0)


# ------------------------------------------------------------------ gold solutions
GOLD_EXTRUDE = r'''chemlab run thermo_process11 speed on
chemlab run thermo_process11 start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "COLD_BARREL"' s.json >/dev/null; then
  for z in 1 2 3 4 5 6 7 die; do chemlab run thermo_process11 set-temp 180C --zone $z; done
  chemlab run thermo_process11 heat on
  chemlab run thermo_process11 wait-temp --max 1h
  chemlab run thermo_process11 start
fi
chemlab run thermo_process11 wait --max 10min
chemlab run thermo_process11 result'''

GOLD_PROFILE = r'''i=1; for t in 160 170 180 180 180 180 185; do chemlab run thermo_process11 set-temp ${t}C --zone $i; i=$((i+1)); done
chemlab run thermo_process11 set-temp 185C --zone die
chemlab run thermo_process11 heat on
chemlab run thermo_process11 wait-temp --max 1h
chemlab run thermo_process11 temp --zone die --json | jq -r .temperature_c > die.txt; cat die.txt'''

GOLD_SPIN = r'''chemlab run linari_rtadvanced set-temp 30C
chemlab run linari_rtadvanced humidity 35%
chemlab run linari_rtadvanced heat on
chemlab run linari_rtadvanced wait-temp --max 30min
chemlab run linari_rtadvanced method voltage=15kV,15kV flows=0.2mL/h,0,0,0,0,0,0,0 rpm=600rpm needle-low=50mm,50mm needle-high=450mm,450mm gap=40mm,40mm time=10s
chemlab run linari_rtadvanced start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "HV_OFF"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab run linari_rtadvanced hv on
  chemlab run linari_rtadvanced start
fi
chemlab run linari_rtadvanced wait --max 10min
chemlab run linari_rtadvanced result --json | jq -r '.result.mean_fiber_diameter_m * 1000000000' > fiber.txt; cat fiber.txt'''

GOLD_CLEAN = r'''chemlab run linari_rtadvanced method voltage=15kV,15kV flows=0.2mL/h,0,0,0,0,0,0,0 rpm=600rpm needle-low=50mm,50mm needle-high=450mm,450mm gap=40mm,40mm time=10s
chemlab run linari_rtadvanced hv on
chemlab run linari_rtadvanced start
chemlab run linari_rtadvanced wait --max 10min
chemlab run linari_rtadvanced clean --head 1 --json > c.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "HV_ON"' c.json >/dev/null; then
  chemlab run linari_rtadvanced hv off
  chemlab wait 30s
  chemlab run linari_rtadvanced clean --head 1
fi
chemlab run linari_rtadvanced open door'''

GOLD_ENCAP = r'''chemlab run buchi_encapsulatorb method volume=5mL flow=6mL/min
chemlab run buchi_encapsulatorb vibration 1000Hz
chemlab run buchi_encapsulatorb vibration on
chemlab run buchi_encapsulatorb hv 1500V
chemlab run buchi_encapsulatorb hv on
chemlab run buchi_encapsulatorb set-temp 40C
chemlab run buchi_encapsulatorb heat on
chemlab run buchi_encapsulatorb start
chemlab run buchi_encapsulatorb feed 300mbar
chemlab run buchi_encapsulatorb wait --max 30min
chemlab run buchi_encapsulatorb feed off
chemlab run buchi_encapsulatorb result'''

GOLD_LNP = r'''chemlab run cytiva_nanoassemblrignite set-temp 37C
chemlab run cytiva_nanoassemblrignite heat on --json > h.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "BLOCK_OPEN"' h.json >/dev/null; then
  chemlab run cytiva_nanoassemblrignite close block
  chemlab run cytiva_nanoassemblrignite heat on
fi
chemlab run cytiva_nanoassemblrignite wait-temp --max 30min
chemlab run cytiva_nanoassemblrignite method flow=4mL/min ratio=3 volume=2mL dilution=1 waste-start=0.2mL waste-end=0.05mL
chemlab run cytiva_nanoassemblrignite prime --json > p.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_OPEN"' p.json >/dev/null; then
  chemlab run cytiva_nanoassemblrignite close lid
  chemlab run cytiva_nanoassemblrignite prime
fi
chemlab run cytiva_nanoassemblrignite wait --max 10min
chemlab run cytiva_nanoassemblrignite start
chemlab run cytiva_nanoassemblrignite wait --max 10min
chemlab run cytiva_nanoassemblrignite result --json | jq -r '.result.sample_m3 * 1000000' > sample.txt; cat sample.txt'''

GOLD_FLUIDBED = r'''chemlab run glatt_miniglatt method operation=granulation air=240L/min temp=60C spray=3.5mL/min time=2min spray-time=90s --json > m.json; rc=$?
if [ $rc -ne 0 ]; then jq -r '.error.code + ": " + .error.fix' m.json; fi
chemlab run glatt_miniglatt set-temp 60C --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "POWER_OFF"' s.json >/dev/null; then
  chemlab run glatt_miniglatt power on
  chemlab run glatt_miniglatt method operation=granulation air=240L/min temp=60C spray=3.5mL/min time=2min spray-time=90s
fi
chemlab run glatt_miniglatt close tower
chemlab run glatt_miniglatt air on
chemlab run glatt_miniglatt heat on
chemlab run glatt_miniglatt wait-temp --max 30min
chemlab run glatt_miniglatt start
chemlab run glatt_miniglatt wait --max 10min
chemlab run glatt_miniglatt result'''

GOLD_CLIMATE = r'''chemlab run linari_rtadvanced set-temp 28C
chemlab run linari_rtadvanced humidity 45%
chemlab run linari_rtadvanced humidity on
chemlab run linari_rtadvanced wait-temp --max 30min
chemlab wait 5min
chemlab run linari_rtadvanced status --json | jq -r '.state.humidity * 100' > rh.txt; cat rh.txt'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'fo01_extrude_recover_cold', 'kind': 'recovery: cold barrel', 'instruments': [P11], 'check': c_extrude,
     'instruction': 'Run the stored recipe on the Thermo Process 11 extruder (thermo_process11) with every barrel zone and the die at 180 °C, '
                    'and wait for the run to finish.', 'gold': GOLD_EXTRUDE},
    {'id': 'fo02_barrel_profile_report', 'kind': 'multi-zone (--zone) + report', 'instruments': [P11], 'check': c_profile,
     'instruction': 'Set the barrel profile of the Process 11 extruder (thermo_process11): zones 1-7 at 160, 170, 180, 180, 180, 180 and '
                    '185 °C, the die at 185 °C. Heat up, wait until everything is at temperature, and write the die temperature in °C into '
                    'die.txt.', 'gold': GOLD_PROFILE},
    {'id': 'fo03_electrospin_recover_hv', 'kind': 'recovery: high voltage off', 'instruments': [LIN], 'check': c_spin,
     'instruction': 'On the Linari RT Advanced (linari_rtadvanced): chamber at 30 °C and 35 % RH. Spin for 10 s with 15 kV on both needle '
                    'groups, pump 1 at 0.2 mL/h (the other seven pumps off), the collector at 600 rpm, needle travel 50-450 mm and a 40 mm '
                    'gap for both groups. Write the mean fibre diameter in nm into fiber.txt.', 'gold': GOLD_SPIN},
    {'id': 'fo04_linari_clean_recover', 'kind': 'recovery: high voltage still on after a run', 'instruments': [LIN], 'check': c_clean,
     'instruction': 'On the Linari RT Advanced (linari_rtadvanced), spin for 10 s (15 kV on both groups, pump 1 at 0.2 mL/h, others off, '
                    'collector 600 rpm, needle travel 50-450 mm, gap 40 mm). Afterwards clean the needle tips of group 1 and open the chamber '
                    'door.', 'gold': GOLD_CLEAN},
    {'id': 'fo05_encapsulation', 'kind': 'multi-step run + feed control', 'instruments': [ENC], 'check': c_encap,
     'instruction': 'Make alginate beads on the BUCHI Encapsulator B-390 (buchi_encapsulatorb): process 5 mL (about 6 mL/min) with the nozzle '
                    'vibrating at 1000 Hz, the electrode at 1500 V and the nozzle heater at 40 °C; drive the feed at 300 mbar. When the 5 mL '
                    'are through, close the feed.', 'gold': GOLD_ENCAP},
    {'id': 'fo06_lnp_recover_block', 'kind': 'recovery: loading block / lid open', 'instruments': [IGN], 'check': c_lnp,
     'instruction': 'On the NanoAssemblr Ignite (cytiva_nanoassemblrignite): warm both syringe channels to 37 °C, then prime the cartridge and '
                    'make 2 mL at 4 mL/min total flow, flow ratio 3, dilution 1, waste 0.2 mL at the start and 0.05 mL at the end. Write the '
                    'collected sample volume in mL into sample.txt.', 'gold': GOLD_LNP},
    {'id': 'fo07_fluidbed_recover_power', 'kind': 'recovery: instrument switched off', 'instruments': [GL], 'check': c_fluidbed,
     'instruction': 'Granulate the loaded powder in the Glatt Mini-Glatt (glatt_miniglatt): inlet air 60 °C at 240 L/min, binder spray 3.5 '
                    'mL/min for 90 s within a 2-minute run (top spray). Wait for the run to finish.', 'gold': GOLD_FLUIDBED},
    {'id': 'fo08_spinning_climate_report', 'kind': 'set points + wait + report', 'instruments': [LIN], 'check': c_climate,
     'instruction': 'Condition the Linari RT Advanced (linari_rtadvanced) chamber to 28 °C and 45 % relative humidity, wait until it is there, '
                    'and write the chamber humidity in % into rh.txt.', 'gold': GOLD_CLIMATE},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'formulation'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('formulation', TASKS, only=only, noact=noact)

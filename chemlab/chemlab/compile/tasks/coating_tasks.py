"""Coating class acceptance tasks (10): gold solutions use `chemlab run`; the checkers read the evaluator view (film ledgers,
cycle states, set points) and the agent's report files."""
from .coating_world import config as world_config
from .common import run_suite

K0 = 273.15
RPM = 0.10471975511965977
SPIN, DIP, BLADE, SLOT, SPUT, CVD, ALD, JET = ('ossila_spincoater', 'ossila_dipcoater', 'ossila_doctorblade', 'ossila_slotdie', 'quorum_q150tes',
                                               'laboao_cvdltf12p50', 'arradiance_gemstarxt', 'fujifilm_dmp2850')


def world_call(lab, iid, cap, args):
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, int(lab.backend.now() * 10)))
    assert r.get('status') == 'succeeded', (cap, r)


def w_lid_open(lab):
    world_call(lab, SPIN, 'lid.set_angle', {'angle_rad': 1.5})
    lab.backend.advance(2)


def _led(e):
    return e.get('truth.ledger') or {}


# ------------------------------------------------------------------ checkers
def c_spin(c):
    e = c.ev(SPIN)
    c.equal('spin programme finished', e.get('cycle.phase'), 'succeeded')
    steps = ((e.get('programs.state') or {}).get('steps') or [])
    c.add('programme 500 rpm / 5 s then 3000 rpm / 30 s',
          [(round(s.get('speed_rad_s') / RPM), s.get('duration_s')) for s in steps] == [(500, 5.0), (3000, 30.0)], steps)
    c.add('a film was spun (solution thrown off, solute kept)', _led(e).get('waste_solvent_kg', 0) > 0 and _led(e).get('film_solute_kg', 0) > 0, _led(e).get('waste_solvent_kg'))


def c_spin_lid(c):
    e = c.ev(SPIN)
    c.equal('spin finished', e.get('cycle.phase'), 'succeeded')
    a, t = e.get('lid.angle_rad'), e.get('lid.target_rad')
    c.add('lid closed', a is not None and t is not None and a <= 0.001 and t <= 0.001, (a, t))
    steps = ((e.get('programs.state') or {}).get('steps') or [])
    c.add('2000 rpm for 20 s', [(round(s.get('speed_rad_s') / RPM), s.get('duration_s')) for s in steps] == [(2000, 20.0)], steps)


def c_dip(c):
    e = c.ev(DIP)
    c.equal('dip programme finished', e.get('cycle.phase'), 'succeeded')
    c.near('clamp closed on the 1 mm slide', e.get('clamp.gap_m'), 0.001, 1e-9)
    led = _led(e)
    c.add('the slide carries a film', (led.get('coated_area_m2') or 0) > 0 and (led.get('film_solute_kg', 0) + led.get('solid_solute_kg', 0)) > 0, led.get('coated_area_m2'))


def c_blade(c):
    e = c.ev(BLADE)
    c.equal('blade coating finished', e.get('cycle.phase'), 'succeeded')
    c.near('gap 100 µm left', e.get('gap.left_target_m'), 1e-4, 1e-12)
    c.near('gap 100 µm right', e.get('gap.right_target_m'), 1e-4, 1e-12)
    c.near('travelled 200 mm', e.get('stage.position_m'), 0.2, 1e-6)


def c_blade_step(c):
    e = c.ev(BLADE)
    l, r = e.get('gap.left_target_m'), e.get('gap.right_target_m')
    c.add('gap set to a whole µm next to 87.5 µm (87 or 88 µm) on both sides',
          l == r and any(abs((l or 0) - x) < 1e-12 for x in (87e-6, 88e-6)), (l, r))
    c.add('gap reached', abs((e.get('gap.left_m') or 0) - (l or -1)) < 1e-12)


def c_slot(c):
    e = c.ev(SLOT)
    c.equal('slot-die coating finished', e.get('cycle.phase'), 'succeeded')
    c.near('bed set to 40 °C', e.get('thermal.target_k'), K0 + 40, 1e-6)
    c.add('heater on', e.get('thermal.enabled'))
    c.add('a film on the substrate', _led(e).get('film_solute_kg', 0) > 0, _led(e).get('film_solute_kg'))


def c_sputter(c):
    e = c.ev(SPUT)
    c.equal('coating run finished', e.get('cycle.phase'), 'succeeded')
    lots = _led(e).get('lots') or [{}]
    c.add('metal deposited on the samples', sum(lots[0].get('sample_kg') or [0]) > 0, lots[0].get('sample_kg'))
    c.add('chamber vented and open', e.get('chamber.opened'))
    c.near('thickness.txt reports the monitor thickness in nm (±0.05)', c.number_file('thickness.txt'), (e.get('ftm.thickness_m') or 0) * 1e9, 0.05)


def c_cvd_purge(c):
    e = c.ev(CVD)
    c.equal('purge run finished', (e.get('cycle.phase'), e.get('cycle.stage')), ('succeeded', 'finished'))
    c.add('furnace and flange closed', not e.get('furnace.opened') and not e.get('flange.opened'))


def c_ald(c):
    e = c.ev(ALD)
    c.equal('ALD recipe finished', e.get('cycle.phase'), 'succeeded')
    c.equal('5 cycles done', e.get('cycle.completed'), 5)
    c.add('alumina film grown', (_led(e).get('film_mol') or {}).get('Al2O3', 0) > 0, _led(e).get('film_mol'))
    c.add('door closed', not e.get('door.opened'))


def c_jet(c):
    e = c.ev(JET)
    c.equal('print finished', e.get('cycle.phase'), 'succeeded')
    c.add('drops printed', (e.get('jet.drop_count') or 0) >= 5, e.get('jet.drop_count'))
    c.near('platen at 40 °C set point', e.get('thermal.platen_target_k'), K0 + 40, 1e-6)
    c.add('ink deposited', _led(e).get('deposit_solute_kg', 0) > 0, _led(e).get('deposit_solute_kg'))


# ------------------------------------------------------------------ gold solutions
GOLD_C01 = r'''chemlab run ossila_spincoater method program=500rpm@5s,3000rpm@30s
job=$(chemlab run ossila_spincoater start --json | jq -r .job)
chemlab wait "$job" --max 5min
chemlab run ossila_spincoater result'''

GOLD_C02 = r'''chemlab run ossila_spincoater method speed=2000rpm time=20s
chemlab run ossila_spincoater start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_OPEN"' s.json >/dev/null; then
  chemlab run ossila_spincoater close lid
  chemlab wait 2
  chemlab run ossila_spincoater start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 5min'''

GOLD_C03 = r'''chemlab run ossila_dipcoater method length=40mm dip-speed=10mm/s withdraw=1mm/s cycles=1 dwell=10s
chemlab run ossila_dipcoater start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ]; then
  chemlab run ossila_dipcoater gap 1mm
  chemlab run ossila_dipcoater start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 30min'''

GOLD_C04 = r'''chemlab run ossila_doctorblade gap 100um
chemlab run ossila_doctorblade method distance=200mm speed=10mm/s
job=$(chemlab run ossila_doctorblade start --json | jq -r .job)
chemlab wait "$job" --max 10min
chemlab run ossila_doctorblade result'''

GOLD_C05 = r'''chemlab run ossila_doctorblade gap 87.5um --json > g.json; rc=$?
jq -r '.error.code + ": " + .error.fix' g.json
if [ $rc -eq 2 ] && jq -e '.error.code == "BAD_STEP"' g.json >/dev/null; then
  chemlab run ossila_doctorblade gap 88um
fi'''

GOLD_C06 = r'''chemlab run ossila_slotdie gap 100um
chemlab run ossila_slotdie set-temp 40C
chemlab run ossila_slotdie method distance=120mm speed=10mm/s flow=1.2mL/min
chemlab run ossila_slotdie start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_PRIMED"' s.json >/dev/null; then
  chemlab run ossila_slotdie prime 100uL
  chemlab run ossila_slotdie start --json > s.json
fi
chemlab wait "$(jq -r .job s.json)" --max 30min'''

GOLD_C07 = r'''chemlab run quorum_q150tes method current=50mA time=60s
job=$(chemlab run quorum_q150tes start --json | jq -r .job)
chemlab wait "$job" --max 30min
chemlab run quorum_q150tes result --json | jq -r '.result.thickness_m * 1e9' > thickness.txt
chemlab run quorum_q150tes vacuum vent
chemlab wait 30
chemlab run quorum_q150tes open chamber
cat thickness.txt'''

GOLD_C08 = r'''for i in 1 2 3; do
  chemlab run laboao_cvdltf12p50 start --mode purge --json > s.json; rc=$?
  [ $rc -eq 0 ] && break
  code=$(jq -r .error.code s.json); echo "$code: $(jq -r .error.fix s.json)"
  case "$code" in
    FURNACE_OPEN) chemlab run laboao_cvdltf12p50 close furnace ;;
    FLANGE_OPEN) chemlab run laboao_cvdltf12p50 close flange ;;
    *) break ;;
  esac
done
chemlab wait "$(jq -r .job s.json)" --max 30min
chemlab run laboao_cvdltf12p50 result'''

GOLD_C09 = r'''chemlab run arradiance_gemstarxt power on
chemlab run arradiance_gemstarxt close door
chemlab run arradiance_gemstarxt method cycles=5 temp=250C
job=$(chemlab run arradiance_gemstarxt start --json | jq -r .job)
chemlab wait "$job" --max 2h
chemlab run arradiance_gemstarxt result'''

GOLD_C10 = r'''chemlab run fujifilm_dmp2850 method spacing=20um voltage=24V pattern=010/111/010 at=50mm,50mm pattern-name=cross
chemlab run fujifilm_dmp2850 set-temp 40C
job=$(chemlab run fujifilm_dmp2850 start --json | jq -r .job)
chemlab wait "$job" --max 30min
chemlab run fujifilm_dmp2850 result'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'co01_spin_two_step', 'kind': 'multi-step programme + job', 'instruments': [SPIN], 'check': c_spin,
     'instruction': 'Spin-coat the substrate on the Ossila spin coater (ossila_spincoater): 500 rpm for 5 s, then 3000 rpm for 30 s. Wait until '
                    'the programme has finished.', 'gold': GOLD_C01},
    {'id': 'co02_spin_recover_lid', 'kind': 'recovery: lid open', 'instruments': [SPIN], 'world': w_lid_open, 'check': c_spin_lid,
     'instruction': 'Spin the substrate on the Ossila spin coater (ossila_spincoater) at 2000 rpm for 20 s and wait until it has finished.',
     'gold': GOLD_C02},
    {'id': 'co03_dip_recover_clamp', 'kind': 'recovery: clamp open (runtime refusal explained)', 'instruments': [DIP], 'check': c_dip,
     'instruction': 'Dip-coat the 1 mm thick slide that is in the Ossila dip coater (ossila_dipcoater): 40 mm coating length, immersion at '
                    '10 mm/s, withdrawal at 1 mm/s, 10 s dwell, one cycle. Wait until it has finished.', 'gold': GOLD_C03},
    {'id': 'co04_blade_coat', 'kind': 'multi-step + job', 'instruments': [BLADE], 'check': c_blade,
     'instruction': 'Blade-coat the substrate on the Ossila doctor blade coater (ossila_doctorblade) with a 100 µm blade gap, over 200 mm at '
                    '10 mm/s, and wait until it has finished.', 'gold': GOLD_C04},
    {'id': 'co05_blade_recover_step', 'kind': 'recovery: usage error (whole µm)', 'instruments': [BLADE], 'check': c_blade_step,
     'instruction': 'Set the blade gap of the Ossila doctor blade coater (ossila_doctorblade) to 87.5 µm on both sides; if the coater does not '
                    'take that value, use the nearest value it accepts.', 'gold': GOLD_C05},
    {'id': 'co06_slotdie_recover_prime', 'kind': 'recovery: die not primed', 'instruments': [SLOT], 'check': c_slot,
     'instruction': 'Slot-die coat the substrate on the Ossila slot-die coater (ossila_slotdie) with a 100 µm die gap, the bed at 40 °C, over '
                    '120 mm at 10 mm/s with 1.2 mL/min of ink. Wait until the coating has finished.', 'gold': GOLD_C06},
    {'id': 'co07_sputter_vent_open', 'kind': 'job + record + chamber handling', 'instruments': [SPUT], 'check': c_sputter,
     'instruction': 'Sputter-coat the samples in the Quorum Q150T ES (quorum_q150tes) at 50 mA for 60 s. Write the film thickness of the '
                    'thickness monitor in nm into thickness.txt, then vent the chamber and open it.', 'gold': GOLD_C07},
    {'id': 'co08_cvd_recover_open', 'kind': 'recovery: furnace lid and flange open', 'instruments': [CVD], 'check': c_cvd_purge,
     'instruction': 'Run a purge cycle (start mode purge) on the LABOAO CVD tube furnace (laboao_cvdltf12p50) and wait until it has finished.',
     'gold': GOLD_C08},
    {'id': 'co09_ald_alumina', 'kind': 'multi-step + long action (job)', 'instruments': [ALD], 'check': c_ald,
     'instruction': 'Grow 5 ALD cycles of alumina at 250 °C on the substrate in the Arradiance GEMStar (arradiance_gemstarxt) and wait until '
                    'the recipe has finished.', 'gold': GOLD_C09},
    {'id': 'co10_inkjet_cross', 'kind': 'multi-step (method + pattern) + job', 'instruments': [JET], 'check': c_jet,
     'instruction': 'Print the pattern 010/111/010 (a small cross) on the FUJIFILM Dimatix DMP-2850 (fujifilm_dmp2850) at x = 50 mm, y = 50 mm '
                    'with 20 µm drop spacing at 24 V and the platen at 40 °C. Wait until the print has finished.', 'gold': GOLD_C10},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'coating'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('coating', TASKS, only=only, noact=noact)

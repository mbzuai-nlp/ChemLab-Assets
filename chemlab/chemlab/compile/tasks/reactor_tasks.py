"""Reactor class acceptance tasks (10): gold solutions use `chemlab run`; the checkers read the evaluator view (truth slots
included) and the agent's report files.  Four are recovery tasks (the gold meets the refusal, reads `--json` `.error.code` and
follows the fix)."""
from .common import run_suite
from .reactor_world import config as world_config

K0 = 273.15
MW, ES, PR, PARR, SY, HC, MYA, RC1 = ('antonpaar_monowave400', 'ika_electrasyn2', 'hepatochem_photoredoxbox', 'parr_4591',
                                      'syrris_asiadiscovery', 'thalesnano_hcube', 'radleys_mya4', 'mt_rc1mxhfcal')
ML_MIN = 1e-6 / 60


def op(lab, iid, cap, args, evaluator=False):
    rid = 'world-%s-%s-%d' % (iid, cap, int(lab.backend.now() * 10))
    if evaluator:
        r = lab.backend.evaluator_invoke(iid, cap, args, rid)
    else:
        r = lab.backend._invoke_raw(lab.backend.instruments[iid], 'chemlab_operator', cap, args, rid)
    assert r.get('status') == 'succeeded', (cap, r)


# ------------------------------------------------------------------ worlds (trusted host, before the agent starts)
def w_photoredox_lid_open(lab):
    op(lab, PR, 'lid.set_lift', {'lift_m': 0.12})


def w_hcube_lid_open(lab):
    op(lab, HC, 'water_lid.set_lift', {'lift_m': 0.03})


def w_rc1_low_limit(lab):
    op(lab, RC1, 'safety.configure', {'tsafe_k': 298.15, 'tr_min_k': 268.15, 'tr_max_k': K0 + 80, 'tj_min_k': 263.15, 'tj_max_k': 423.15,
                                      'tdiff_max_k': 50.0, 'rsafe_rpm': None, 'rmin_rpm': 0.0, 'rmax_rpm': 850.0, 'tc_max_k': 313.15}, evaluator=True)


# ------------------------------------------------------------------ checkers
def jobs_of(c, iid):
    return [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == iid]


def c_microwave(c):
    e = c.ev(MW)
    c.near('programme target 80 °C', e['method.target_k'], K0 + 80, 1e-6)
    c.equal('the run finished', e['cycle.phase'], 'succeeded')
    c.add('the lid is locked', (e.get('lock.travel_m') or 0) >= 0.009, e.get('lock.travel_m'))
    c.add('the vial was heated (truth temperature above 45 °C at some point: now %.1f K)' % (e.get('truth.temperature_k') or 0),
          (e.get('truth.absorbed_energy_j') or 0) > 1000, e.get('truth.absorbed_energy_j'))


def c_electrolysis(c):
    e = c.ev(ES)
    c.equal('constant-current mode', e['method.mode'], 'constant_current')
    c.near('current 10 mA', e['method.current_a'], 0.01, 1e-9)
    c.equal('electrolysis finished', e['cycle.phase'], 'succeeded')
    c.near('0.6 C passed (10 mA x 60 s)', e['electrochem.charge_c'], 0.6, 0.02)
    c.near('charge.txt reports the charge in C', c.number_file('charge.txt'), 0.6, 0.02)
    c.near('stirring 400 rpm', e['stir.target_rad_s'], 400 * 2 * 3.141592653589793 / 60, 0.6)


def c_photoredox(c):
    e = c.ev(PR)
    c.near('chamber limit 35 °C', e['temperature.target_k'], K0 + 35, 1e-6)
    c.add('all LEDs at 80 %', all(abs(x - 0.8) < 1e-9 for x in e['optics.intensities']), e['optics.intensities'])
    c.near('irradiation time 10 min', e['method.duration_s'], 600.0, 1e-6)
    c.equal('irradiation finished', e['cycle.phase'], 'succeeded')
    c.add('lid closed', (e.get('lid.lift_m') or 0) <= 1e-9, e.get('lid.lift_m'))
    c.near('stirring 600 rpm', e['stir.target_rad_s'], 600 * 2 * 3.141592653589793 / 60, 1e-6)


def c_parr(c):
    e = c.ev(PARR)
    c.add('the vessel was heated to 120 °C (heater energy > 30 kJ)', (e.get('thermal.heater_energy_j') or 0) > 30000, e.get('thermal.heater_energy_j'))
    c.near('set point 120 °C', e['thermal.target_k'], K0 + 120, 1e-6)
    c.add('heater off', not e['heater.enabled'])
    c.add('stirrer stopped', abs(e['stirrer.speed_rad_s']) < 1e-9)
    c.add('the vessel is open', not e['lid.closed'])
    c.add('opened cool (now %.1f K <= 323.15 K)' % e['thermal.temperature_k'], e['thermal.temperature_k'] <= 323.15 + 1e-6)
    c.add('opened at ambient pressure', abs(e['pressure.pressure_pa'] - 101325) <= 2500, e['pressure.pressure_pa'])


def c_flow(c):
    e = c.ev(SY)
    c.near('chip at 60 °C', e['temperature.target_k'], K0 + 60, 1e-6)
    c.near('back-pressure 5 bar', e['pressure.backpressure_pa'], 5e5, 1e-3)
    c.equal('the flow run finished', e['cycle.phase'], 'succeeded')
    c.add('both pumps at 0.5 mL/min in the method', all(abs(x - 0.5 * ML_MIN) < 1e-15 for x in e['flow.targets_m3_s']), e['flow.targets_m3_s'])
    vol = e.get('collection.total_m3') or 0
    c.add('product collected', vol > 1e-7, vol)
    c.near('collected.txt reports the collected volume in mL', c.number_file('collected.txt'), vol * 1e6, 0.01)


def c_hcube(c):
    e = c.ev(HC)
    c.near('column 60 °C', e['temperature.target_k'], K0 + 60, 1e-6)
    c.near('30 bar (absolute)', e['pressure.target_abs_pa'], 3e6, 1e-3)
    c.near('0.5 mL/min', e['flow.target_m3_s'], 0.5 * ML_MIN, 1e-15)
    c.near('5 min', e['method.duration_s'], 300.0, 1e-6)
    c.equal('the run finished', e['cycle.phase'], 'succeeded')
    c.add('water lid closed', (e.get('water_lid.lift_m') or 0) <= 1e-9, e.get('water_lid.lift_m'))


def c_zones(c):
    e = c.ev(MYA)
    c.near('zone 1 set point 60 °C', e['zone_1.thermal.target_k'], K0 + 60, 1e-6)
    c.near('zone 2 set point 40 °C', e['zone_2.thermal.target_k'], K0 + 40, 1e-6)
    c.add('zone 1 and 2 control on', e['zone_1.thermal.enabled'] and e['zone_2.thermal.enabled'])
    c.near('zone 1 at 60 °C (±1 K)', e['zone_1.thermal.indicated_temperature_k'], K0 + 60, 1.0)
    c.near('zone 2 at 40 °C (±1 K)', e['zone_2.thermal.indicated_temperature_k'], K0 + 40, 1.0)
    c.add('zone 1 stirring 500 rpm', e['zone_1.stirring.enabled'] and abs(e['zone_1.stirring.target_rad_s'] - 500 * 2 * 3.141592653589793 / 60) < 1e-6)
    c.add('zones 3 and 4 untouched', not e['zone_3.thermal.enabled'] and not e['zone_4.thermal.enabled'])


def c_program(c):
    e = c.ev(MYA)
    segs = e.get('zone_3.program.segments') or []
    c.add('zone 3 programme 50 °C for 2 min, then 30 °C', len(segs) == 2 and abs(segs[0]['temperature_K'] - (K0 + 50)) < 1e-6 and
          abs(segs[0]['hold_s'] - 120) < 1e-6 and abs(segs[1]['temperature_K'] - (K0 + 30)) < 1e-6, segs)
    c.equal('zone 3 programme completed', e['zone_3.program.phase'], 'completed')
    js = jobs_of(c, MYA)
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in js), [(j.handle, j.state) for j in js])


def c_rc1_limit(c):
    e = c.ev(RC1)
    c.add('Tr limit raised to at least 95 °C (and at most 100 °C)', K0 + 95 - 1e-6 <= e['safety.tr_max_k'] <= K0 + 100 + 1e-6, e['safety.tr_max_k'])
    c.equal('Tr control mode', e['thermostat.mode'], 'tr')
    c.near('Tr set point 90 °C', e['thermostat.tr_setpoint_k'], K0 + 90, 1e-6)
    c.near('the reaction is at 90 °C (±0.5 K)', e['reactor.indicated_k'], K0 + 90, 0.5)
    c.add('stirring (>= 100 rpm)', (e.get('stirrer.setpoint_rpm') or 0) >= 100, e.get('stirrer.setpoint_rpm'))


def c_rc1_dose(c):
    e = c.ev(RC1)
    c.near('102 g dosed', e['dosing.indicated_mass_kg'], 0.102, 1e-6)
    c.add('the experiment record was ended', not e['experiment.running'])
    c.near('stirrer 300 rpm', e['stirrer.setpoint_rpm'], 300.0, 1e-6)
    t = c.work_file('rc1.json') or ''
    c.add('rc1.json holds the experiment record (named acetylation)', '"acetylation"' in t and '"points"' in t, t[:120])
    js = jobs_of(c, RC1)
    c.add('the dosing ran as a job that succeeded', any(j.state == 'succeeded' for j in js), [(j.handle, j.state) for j in js])


# ------------------------------------------------------------------ gold solutions
GOLD_MW = r'''chemlab run antonpaar_monowave400 method temp=80C ramp=1min hold=2min cool=50C
chemlab run antonpaar_monowave400 start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_NOT_LOCKED" or .error.code == "LID_OPEN"' s.json >/dev/null; then
  chemlab run antonpaar_monowave400 close lid
  chemlab run antonpaar_monowave400 start
fi
chemlab run antonpaar_monowave400 wait --max 30min
chemlab run antonpaar_monowave400 result'''

GOLD_ES = r'''chemlab run ika_electrasyn2 current 10mA
chemlab run ika_electrasyn2 method stop-by=time time=1min
chemlab run ika_electrasyn2 stir 400rpm
chemlab run ika_electrasyn2 start
chemlab run ika_electrasyn2 wait --max 10min
chemlab run ika_electrasyn2 result --json | jq -r .result.charge_c > charge.txt; cat charge.txt'''

GOLD_PR = r'''chemlab run hepatochem_photoredoxbox set-temp 35C
chemlab run hepatochem_photoredoxbox light 80%
chemlab run hepatochem_photoredoxbox stir 600rpm
chemlab run hepatochem_photoredoxbox method time=10min
chemlab run hepatochem_photoredoxbox start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_OPEN"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab run hepatochem_photoredoxbox close lid
  chemlab run hepatochem_photoredoxbox start
fi
chemlab run hepatochem_photoredoxbox wait --max 30min'''

GOLD_PARR = r'''chemlab run parr_4591 set-temp 120C
chemlab run parr_4591 heat on
chemlab run parr_4591 stir 500rpm
chemlab run parr_4591 wait-temp --max 1h --hold 5min
chemlab run parr_4591 stop
chemlab run parr_4591 open lid --json > o.json || jq -r '.error.code + ": " + .error.fix' o.json
chemlab run parr_4591 wait-temp --to 45C --max 3h
chemlab run parr_4591 open lid --json > o.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "PRESSURISED"' o.json >/dev/null; then
  chemlab run parr_4591 open vent
  chemlab wait 30s
  chemlab run parr_4591 open lid
fi
chemlab run parr_4591 status'''

GOLD_SY = r'''chemlab run syrris_asiadiscovery set-temp 60C --wait
chemlab run syrris_asiadiscovery pressure 5bar
chemlab run syrris_asiadiscovery method flows=0.5mL/min,0.5mL/min time=2min inject=0s,0s 'collect=[{"start_s":30,"end_s":100,"slot":0}]'
chemlab run syrris_asiadiscovery start
chemlab run syrris_asiadiscovery wait --max 10min
chemlab run syrris_asiadiscovery result --json | jq -r '.result.collected_volume_m3 * 1000000' > collected.txt; cat collected.txt'''

GOLD_HC = r'''chemlab run thalesnano_hcube method temp=60C pressure=30bar flow=0.5mL/min time=5min
chemlab run thalesnano_hcube start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "WATER_LID_OPEN"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab run thalesnano_hcube close water_lid
  chemlab run thalesnano_hcube start
fi
chemlab run thalesnano_hcube wait --max 20min
chemlab run thalesnano_hcube result'''

GOLD_ZONES = r'''chemlab run radleys_mya4 set-temp 60C --zone 1
chemlab run radleys_mya4 heat on --zone 1
chemlab run radleys_mya4 stir 500rpm --zone 1
chemlab run radleys_mya4 set-temp 40C --zone 2
chemlab run radleys_mya4 heat on --zone 2
chemlab run radleys_mya4 wait-temp --zone 1 --max 1h
chemlab run radleys_mya4 wait-temp --zone 2 --max 1h
chemlab run radleys_mya4 temp'''

GOLD_PROGRAM = r'''chemlab run radleys_mya4 program 50C@2min,30C@0s --zone 3
job=$(chemlab run radleys_mya4 start --zone 3 --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 2h
chemlab run radleys_mya4 result --zone 3'''

GOLD_RC1_LIMIT = r'''chemlab run mt_rc1mxhfcal stir 300rpm
chemlab run mt_rc1mxhfcal set-temp 90C --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.message' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "ABOVE_SAFETY_LIMIT"' s.json >/dev/null; then
  chemlab run mt_rc1mxhfcal safety-limit 95C
  chemlab run mt_rc1mxhfcal set-temp 90C
fi
chemlab run mt_rc1mxhfcal wait-temp --max 2h'''

GOLD_RC1_DOSE = r'''chemlab run mt_rc1mxhfcal stir 300rpm
chemlab run mt_rc1mxhfcal set-temp 40C --wait --max 1h
chemlab run mt_rc1mxhfcal start --name acetylation
job=$(chemlab run mt_rc1mxhfcal dose acetic_anhydride --rate 9g/min --mass 102g --json | jq -r .job)
chemlab wait "$job" --max 1h
chemlab run mt_rc1mxhfcal stop
chemlab run mt_rc1mxhfcal result --save-to rc1.json'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'rx01_microwave_recover_lock', 'kind': 'recovery: lid not locked', 'instruments': [MW], 'check': c_microwave,
     'instruction': 'A sealed vial with ethanol is in the Anton Paar Monowave 400 (antonpaar_monowave400). Run it at 80 °C: reach it in 1 min, '
                    'hold 2 min, cool to 50 °C. Wait until the run has finished.', 'gold': GOLD_MW},
    {'id': 'rx02_electrolysis', 'kind': 'multi-step + report', 'instruments': [ES], 'check': c_electrolysis,
     'instruction': 'On the IKA ElectraSyn 2.0 (ika_electrasyn2), run a constant-current electrolysis at 10 mA for 1 minute while stirring at '
                    '400 rpm. Write the charge passed (in C) into charge.txt.', 'gold': GOLD_ES},
    {'id': 'rx03_photoredox_recover_lid', 'kind': 'recovery: lid left open', 'instruments': [PR], 'world': w_photoredox_lid_open, 'check': c_photoredox,
     'instruction': 'Irradiate the vials in the HepatoChem PhotoRedOx Box (hepatochem_photoredoxbox) for 10 minutes with all LEDs at 80 %, '
                    'stirring at 600 rpm, with the chamber held at no more than 35 °C.', 'gold': GOLD_PR},
    {'id': 'rx04_parr_heat_cool_open', 'kind': 'safety sequence + recovery: hot / pressurised vessel', 'instruments': [PARR], 'check': c_parr,
     'instruction': 'Heat the Parr 4591 vessel (parr_4591) to 120 °C with stirring at 500 rpm and hold it there for 5 minutes. Then end the '
                    'reaction and open the vessel - safely: only cool and at ambient pressure.', 'gold': GOLD_PARR},
    {'id': 'rx05_flow_run_report', 'kind': 'long action (job) + report', 'instruments': [SY], 'check': c_flow,
     'instruction': 'On the Syrris Asia (syrris_asiadiscovery): chip at 60 °C, back-pressure 5 bar. Run both pumps at 0.5 mL/min for 2 minutes, '
                    'injecting both loops at the start and collecting from 30 s to 100 s into collector slot 0. Write the collected volume in '
                    'mL into collected.txt.', 'gold': GOLD_SY},
    {'id': 'rx06_hcube_recover_water_lid', 'kind': 'recovery: water reservoir lid open', 'instruments': [HC], 'world': w_hcube_lid_open,
     'check': c_hcube, 'instruction': 'Run a 5-minute hydrogenation on the ThalesNano H-Cube Pro (thalesnano_hcube) at 60 °C, 30 bar (absolute) '
                                       'and 0.5 mL/min, and wait for it to finish.', 'gold': GOLD_HC},
    {'id': 'rx07_mya4_two_zones', 'kind': 'multi-zone (--zone) + waits', 'instruments': [MYA], 'check': c_zones,
     'instruction': 'On the Radleys Mya 4 (radleys_mya4): bring zone 1 to 60 °C with stirring at 500 rpm, and zone 2 to 40 °C (no stirring). '
                    'Wait until both zones are at temperature. Leave zones 3 and 4 alone.', 'gold': GOLD_ZONES},
    {'id': 'rx08_mya4_program_job', 'kind': 'programme + job id (status, wait)', 'instruments': [MYA], 'check': c_program,
     'instruction': 'Run a temperature programme in zone 3 of the Radleys Mya 4 (radleys_mya4): 50 °C held for 2 minutes, then down to 30 °C. '
                    'Follow it with a job (check its status once, then wait for it).', 'gold': GOLD_PROGRAM},
    {'id': 'rx09_rc1_recover_limit', 'kind': 'recovery: above the safety limit', 'instruments': [RC1], 'world': w_rc1_low_limit, 'check': c_rc1_limit,
     'instruction': 'Bring the reaction mixture in the METTLER TOLEDO RC1mx (mt_rc1mxhfcal) to 90 °C (reaction temperature) with stirring at '
                    '300 rpm and wait until it is there. Raising the Tr safety limit to 95 °C is acceptable for this aqueous mixture; do not go '
                    'higher.', 'gold': GOLD_RC1_LIMIT},
    {'id': 'rx10_rc1_dosing_record', 'kind': 'experiment record + dosing job + save', 'instruments': [RC1], 'check': c_rc1_dose,
     'instruction': 'On the METTLER TOLEDO RC1mx (mt_rc1mxhfcal): stir at 300 rpm, bring the reaction to 40 °C, start an experiment record '
                    'named acetylation, dose 102 g acetic_anhydride at 9 g/min and wait until it is all in, end the experiment and save its '
                    'record to rc1.json.', 'gold': GOLD_RC1_DOSE},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'reactor'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('reactor', TASKS, only=only, noact=noact)

"""gas_fluid class acceptance tasks: gold solutions use `chemlab run` (and `chemlab call` only for capabilities the class does not
cover); the checkers read the evaluator view (process truth where the twin has it) and the agent's report files.  The labs carry
the class world (tasks/gas_fluid_world.py)."""
from .common import run_suite
from .gas_fluid_world import config as world_config

K0 = 273.15
PSI = 6894.757293168
VAC, P702, CEM, VICI, PEAK, ELGA, ECO, LEAK, MFC = ('vacuubrand_pc3001_vario_select', 'bronkhorst_p702cv_5bar_gauge_n2', 'bronkhorst_cemw',
                                                    'vici_euta_2sd6mwe', 'peak_precisionhydrogen', 'elga_purelab_flex3', 'eco_ncld822',
                                                    'inficon_ul1000fab', 'alicat_mc_1slpm_rev6')


_N = [0]


def world_call(lab, iid, cap, args):
    _N[0] += 1
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, _N[0]))
    assert r.get('status') == 'succeeded', (cap, r)


def w_vac_at_50mbar(lab):
    world_call(lab, VAC, 'vacuum.set_pressure', {'pressure_pa': 5000.0})
    world_call(lab, VAC, 'vacuum.start', {})
    lab.backend.advance(120)


def w_eco_drifted(lab):
    """The analyzer was last spanned against a wrong span value (2500 ppm entered for a 2954 ppm standard): its NO/NOx gains
    read about 15 % low.  Done here as the evaluator, with the standard connected and steady, then the inlet back on sample."""
    world_call(lab, ECO, 'range.select', {'index': 3})
    world_call(lab, ECO, 'measurement.start', {})
    world_call(lab, ECO, 'inlet.connect_standard', {'standard_id': 'srm_2631a'})
    lab.backend.advance(60)
    world_call(lab, ECO, 'calibration.configure', {'span_no_mol_mol': 0.0025, 'span_nox_mol_mol': 0.0025, 'window_s': 30.0})
    r = lab.backend.evaluator_invoke(ECO, 'calibration.span', {}, 'world-eco-span')
    assert r.get('status') in ('accepted', 'succeeded'), r
    lab.backend.advance(60)
    world_call(lab, ECO, 'inlet.disconnect', {})
    world_call(lab, ECO, 'measurement.stop', {})
    world_call(lab, ECO, 'range.select', {'index': 2})
    lab.backend.advance(60)


def w_cem_bleed_open(lab):
    world_call(lab, CEM, 'power.set', {'on': True})
    world_call(lab, CEM, 'bleed.set', {'open': True})
    world_call(lab, CEM, 'power.set', {'on': False})


# ------------------------------------------------------------------ checkers
def c_pumpdown(c):
    e = c.ev(VAC)
    c.add('pump running', e['vacuum.running'])
    c.equal('pressure control mode', e['vacuum.mode'], 'pressure_control')
    c.near('set point 30 mbar', e['vacuum.target_pressure_pa'], 3000.0, 1e-6)
    c.near('flask at 30 mbar (+-1.5 mbar, true pressure)', e['vacuum.pressure_pa'], 3000.0, 150.0)


def c_adjust(c):
    e = c.ev(VAC)
    adj = e.get('sensor.adjustment') or {}
    c.near('atmosphere reference 1013 mbar', adj.get('atm_ref_pa'), 101300.0, 1e-6)
    c.near('adjustment factor near 1 (it was adjusted at atmosphere, not under vacuum)', adj.get('k'), 1.0, 0.01)
    c.add('pump stopped', not e['vacuum.running'])
    c.equal('vent valve closed again', (e.get('vent.state') or {}).get('mode'), 'closed')


def c_backpressure(c):
    e = c.ev(P702)
    c.equal('automatic control', e['control.mode'], 'automatic')
    c.near('set point 2.5 bar g', e['pressure.setpoint_pa'], 250000.0, 1e-6)
    gauge = e['process.upstream_absolute_pressure_pa'] - e['process.downstream_absolute_pressure_pa']
    c.near('line at 2.5 bar g (+-0.05 bar, true pressure)', gauge, 250000.0, 5000.0)
    c.near('pressure.txt reports the pressure in bar (+-0.05)', c.number_file('pressure.txt'), 2.5, 0.05)


def c_evaporator(c):
    e = c.ev(CEM)
    c.add('switched on', e['power.on'])
    c.near('carrier gas 1 L/min', e['setpoint.gas_m3_s'], 1e-3 / 60, 1e-12)
    c.near('water 6 g/h', e['setpoint.liquid_kg_s'], 6e-3 / 3600, 1e-12)
    c.near('evaporator 140 C', e['setpoint.temperature_k'], K0 + 140, 1e-6)
    c.add('bleed closed', not e['bleed.open'])


def c_valve(c):
    e = c.ev(VICI)
    c.equal('valve at position 4', e['valve.position'], 4)
    c.add('in position', e['valve.in_position'])
    c.equal('port.txt names the connected port', c.work_file('port.txt'), 'port_4')


def c_hydrogen(c):
    e = c.ev(PEAK)
    c.near('outlet set point 60 psi', e['pressure.set_pa'], 60 * PSI, 1e-3)
    c.equal('generating', e['state.current'], 'running')


def c_water(c):
    e = c.ev(ELGA)
    c.near('750 mL in the bottle (true volume)', e['water.receiver_volume_m3'], 0.00075, 1e-6)
    c.near('rate 1.5 L/min', e['water.flow_rate_m3_s'], 1.5e-3 / 60, 1e-12)
    jobs = [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == ELGA]
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in jobs), [(j.handle, j.state) for j in jobs])
    c.near('quality.txt reports 18.2 MOhm.cm', c.number_file('quality.txt'), 18.2, 0.05)


def c_nox(c):
    e = c.ev(ECO)
    c.near('span value 2954 ppm', e['calibration.span_no_mol_mol'], 0.002954, 1e-9)
    c.near('NO gain near 1 (a correct span)', e['calibration.gain_no'], 1.0, 0.02)
    c.add('zero offset small (zeroed on zero air)', abs(e['calibration.zero_offset_no_mol_mol']) < 1e-6, e['calibration.zero_offset_no_mol_mol'])
    c.equal('inlet back on the sample', e['inlet.source'], 'ambient_air')
    c.equal('no calibration running', e['calibration.phase'], 'idle')


def c_leak(c):
    e = c.ev(LEAK)
    trig = (e.get('settings.current') or {}).get('trigger_1_pa_m3_s', 1e-10)
    truth = max((x.get('helium_leak_rate_pa_m3_s') or 0.0) for x in (e.get('truth.leaks') or {}).values()) if e.get('truth.leaks') else 0.0
    want = 'REJECT' if truth > trig else 'PASS'
    starts = c.native(LEAK, 'measure.start')
    c.add('a measurement was started (request log)', bool(starts), len(starts))
    c.add('the leak rate was read while measuring (request log)', bool(starts) and bool(c.looked(LEAK, after_seq=starts[0][0]['seq'])),
          len(c.looked(LEAK, after_seq=starts[0][0]['seq'])) if starts else 0)
    c.equal('leak.txt gives the right verdict', (c.work_file('leak.txt') or '').upper(), want)
    c.add('stopped (standby or venting)', e['state.current'] in ('standby', 'vent'), e['state.current'])
    c.add('inlet vented (above 500 mbar)', (e.get('pressure.inlet_pa') or 0) > 50000, e.get('pressure.inlet_pa'))


def c_mfc(c):
    e = c.ev(MFC)
    c.equal('nitrogen selected', e['gas.selected_id'], 'gas_nitrogen')
    c.add('tared at zero flow', e['flow.zeroed'])
    c.near('set point 250 sccm', e['flow.setpoint_m3_s'], 250e-6 / 60, 1e-12)
    c.near('true standard flow 250 sccm (+-2.5 sccm)', e['truth.standard_flow_m3_s'], 250e-6 / 60, 2.5e-6 / 60)
    c.near('flow.txt reports the flow in sccm (+-2.5)', c.number_file('flow.txt'), 250.0, 2.5)


GOLD_G10 = r'''chemlab run alicat_mc_1slpm_rev6 setpoint 250sccm --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NO_GAS"' s.json >/dev/null; then
  chemlab run alicat_mc_1slpm_rev6 select nitrogen
fi
chemlab run alicat_mc_1slpm_rev6 zero
chemlab run alicat_mc_1slpm_rev6 setpoint 250sccm --wait --max 2min
chemlab run alicat_mc_1slpm_rev6 read --json | jq -r '.readings["measurement.standard_flow_m3_s"] * 60000000 * 10 | round / 10' > flow.txt
cat flow.txt'''

GOLD_G01 = r'''chemlab run vacuubrand_pc3001_vario_select setpoint 30mbar --wait --max 10min --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_RUNNING"' s.json >/dev/null; then
  chemlab run vacuubrand_pc3001_vario_select start
  chemlab run vacuubrand_pc3001_vario_select setpoint 30mbar --wait --max 10min
fi
chemlab run vacuubrand_pc3001_vario_select status'''

GOLD_G02 = r'''chemlab run vacuubrand_pc3001_vario_select calibrate reference=1013mbar --json > c.json; rc=$?
jq -r '.error.code + ": " + .error.message' c.json
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_AT_REFERENCE"' c.json >/dev/null; then
  chemlab run vacuubrand_pc3001_vario_select vent
  chemlab wait 300
  chemlab run vacuubrand_pc3001_vario_select read
  chemlab run vacuubrand_pc3001_vario_select calibrate reference=1013mbar
fi
chemlab run vacuubrand_pc3001_vario_select vent off
chemlab run vacuubrand_pc3001_vario_select status'''

GOLD_G03 = r'''chemlab run bronkhorst_p702cv_5bar_gauge_n2 setpoint 2.5bar --wait --max 20min
chemlab run bronkhorst_p702cv_5bar_gauge_n2 read --json | jq -r '.readings["pressure.reading_pa"] / 100000 * 1000 | round / 1000' > pressure.txt
cat pressure.txt'''

GOLD_G04 = r'''chemlab run bronkhorst_cemw setpoint 1slpm --json > a.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "POWER_OFF"' a.json >/dev/null; then
  chemlab run bronkhorst_cemw power on
  chemlab run bronkhorst_cemw setpoint 1slpm
fi
chemlab run bronkhorst_cemw setpoint 140C
chemlab run bronkhorst_cemw setpoint 6g/h --json > b.json; rc=$?
jq -r '.error.code // "ok"' b.json
if [ $rc -eq 3 ] && jq -e '.error.code == "BLEED_OPEN"' b.json >/dev/null; then
  chemlab run bronkhorst_cemw vent off
  chemlab run bronkhorst_cemw setpoint 6g/h
fi
chemlab run bronkhorst_cemw status'''

GOLD_G05 = r'''chemlab run vici_euta_2sd6mwe select 4 --wait
chemlab run vici_euta_2sd6mwe read --json | jq -r '.readings["route.connected_port_id"]' > port.txt
cat port.txt'''

GOLD_G07 = r'''job=$(chemlab run elga_purelab_flex3 dispense 750mL --rate 1.5L/min --json | jq -r .job)
echo "job: $job"
chemlab status "$job"
chemlab wait "$job" --max 10min
chemlab run elga_purelab_flex3 read --json | jq -r '.readings["water.resistivity_ohm_m"] / 10000 * 100 | round / 100' > quality.txt
cat quality.txt'''

GOLD_G08 = r'''chemlab run eco_ncld822 start
chemlab run eco_ncld822 select zero_air
chemlab wait 60
j=$(chemlab run eco_ncld822 zero --json | jq -r .job); chemlab wait "$j" --max 5min
chemlab run eco_ncld822 select srm_2631a
chemlab wait 60
chemlab run eco_ncld822 calibrate span=2954ppm --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "OVER_RANGE"' s.json >/dev/null; then
  chemlab call eco_ncld822 range.select --index 3
  chemlab wait 30
  chemlab run eco_ncld822 calibrate span=2954ppm --json > s.json
fi
j=$(jq -r .job s.json); chemlab wait "$j" --max 5min
chemlab run eco_ncld822 select sample
chemlab run eco_ncld822 status'''

GOLD_G09 = r'''chemlab run inficon_ul1000fab start --wait --max 10min
chemlab wait 120
chemlab run inficon_ul1000fab zero
chemlab call inficon_ul1000fab spray.set --target weld --on true
chemlab wait 10
st=$(chemlab run inficon_ul1000fab read --json | jq -r '.readings["leak.status"]')
chemlab call inficon_ul1000fab spray.set --target weld --on false
if [ "$st" = "reject" ]; then echo REJECT > leak.txt; else echo PASS > leak.txt; fi
chemlab run inficon_ul1000fab vent
chemlab wait 10
chemlab run inficon_ul1000fab status
cat leak.txt'''

TASKS = [
    {'id': 'gf01_vacuum_pumpdown', 'kind': 'recovery: pump not running', 'instruments': [VAC], 'check': c_pumpdown,
     'instruction': 'A 2 L flask is connected to the VACUUBRAND PC 3001 pump (vacuubrand_pc3001_vario_select). Bring it down to 30 mbar under '
                    'pressure control and make sure it has reached 30 mbar.', 'gold': GOLD_G01},
    {'id': 'gf02_gauge_adjust', 'kind': 'recovery: gauge not at the reference', 'instruments': [VAC], 'world': w_vac_at_50mbar, 'check': c_adjust,
     'instruction': 'The room barometer reads 1013 mbar. Adjust the vacuum gauge of the VACUUBRAND pump (vacuubrand_pc3001_vario_select) to this '
                    'atmospheric reference. Leave the pump stopped and its vent valve closed afterwards.', 'gold': GOLD_G02},
    {'id': 'gf03_backpressure', 'kind': 'long action (blocking wait) + report', 'instruments': [P702], 'check': c_backpressure,
     'instruction': 'Regulate the nitrogen line upstream of the Bronkhorst EL-PRESS back-pressure controller (bronkhorst_p702cv_5bar_gauge_n2) at '
                    '2.5 bar gauge. When it is there, write the measured pressure in bar into pressure.txt.', 'gold': GOLD_G03},
    {'id': 'gf04_evaporator', 'kind': 'recovery: switched off + bleed open', 'instruments': [CEM], 'world': w_cem_bleed_open, 'check': c_evaporator,
     'instruction': 'Produce humidified nitrogen with the Bronkhorst CEM evaporator (bronkhorst_cemw): 1 L/min nitrogen carrying 6 g/h of water, '
                    'with the mixing chamber at 140 C.', 'gold': GOLD_G04},
    {'id': 'gf05_stream_select', 'kind': 'single step + report', 'instruments': [VICI], 'check': c_valve,
     'instruction': 'Switch the VICI stream-selection valve (vici_euta_2sd6mwe) to stream 4, make sure it is in position, and write the id of the '
                    'connected port into port.txt.', 'gold': GOLD_G05},
    {'id': 'gf06_hydrogen', 'kind': 'multi-step (psi set point, wait for state)', 'instruments': [PEAK], 'check': c_hydrogen,
     'instruction': 'The GC needs hydrogen at 60 psi. Set the PEAK hydrogen generator (peak_precisionhydrogen) outlet pressure to 60 psi and start '
                    'generation; make sure it is generating.',
     'gold': 'chemlab run peak_precisionhydrogen setpoint 60psi && chemlab run peak_precisionhydrogen start --wait && chemlab run peak_precisionhydrogen status'},
    {'id': 'gf07_water_job', 'kind': 'long action: job id (status, wait) + report', 'instruments': [ELGA], 'check': c_water,
     'instruction': 'Dispense 750 mL of ultrapure water at 1.5 L/min from the ELGA PURELAB flex 3 (elga_purelab_flex3) into the bottle. Follow it '
                    'with a job (check its status, then wait for it), and write the water resistivity in MOhm.cm into quality.txt.', 'gold': GOLD_G07},
    {'id': 'gf08_nox_zero_span', 'kind': 'recovery: reading over range', 'instruments': [ECO], 'world': w_eco_drifted, 'check': c_nox,
     'instruction': 'Calibrate the ECO PHYSICS nCLD 822 NOx analyzer (eco_ncld822): zero it on zero air, then span it with the NIST SRM 2631a '
                    'standard (2954 ppm NO in nitrogen, the certificate value), and put the inlet back on the sample.', 'gold': GOLD_G08},
    {'id': 'gf09_leak_test', 'kind': 'long action + decision + report', 'instruments': [LEAK], 'check': c_leak,
     'instruction': 'A test chamber is on the inlet of the INFICON UL1000 Fab leak detector (inficon_ul1000fab). Start measuring, let it settle '
                    'for 2 minutes, subtract the helium background, then spray helium on the weld (the target "weld") for 10 s and decide '
                    'against its trigger: write PASS or REJECT into leak.txt. Stop spraying, then stop and vent the inlet.', 'gold': GOLD_G09},
    {'id': 'gf10_mfc_nitrogen', 'kind': 'recovery: no gas selected', 'instruments': [MFC], 'check': c_mfc,
     'instruction': 'The Alicat MC mass-flow controller (alicat_mc_1slpm_rev6) sits in a nitrogen line. Tare it at zero flow, then deliver '
                    '250 sccm of nitrogen; when the flow is there, write the measured flow in sccm into flow.txt.', 'gold': GOLD_G10},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'gas_fluid'
    _t['config'] = world_config


def main(only=None, noact=True):
    return run_suite('gas_fluid', TASKS, only=only, noact=noact)

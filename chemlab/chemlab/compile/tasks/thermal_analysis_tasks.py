"""thermal_analysis class acceptance tasks (10, 5 of them recovery tasks): gold solutions use `chemlab run`; the checkers read
the evaluator view, the evaluator's own result query and the agent's report files."""
from .common import run_suite
from .thermal_analysis_world import config as world_config

K0 = 273.15
DSC, TGA, DMA, TMA, LFA, TPS, TAM, MMC, C6 = ('ta_dsc2500', 'ta_tga5500', 'ta_dma850', 'ta_tma450', 'netzsch_lfa467', 'hot_tps2500', 'ta_tamiv',
                                              'netzsch_mmc315', 'ika_c6000')


def ev_call(c, iid, cap, args=None):
    """The evaluator's own query of a result (host side; the agent never sees this)."""
    r = c.lab.backend.evaluator_invoke(iid, cap, args or {}, 'check-%s-%s' % (iid, cap))
    return r.get('result') or {}


def world_call(lab, iid, cap, args):
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, int(lab.backend.now() * 10)))
    assert r.get('status') in ('succeeded', 'accepted'), (cap, r)


def w_dsc_hot(lab):
    """A DSC that has just been used: pan 1 in the cell, lid closed, purge on, held at 200 °C."""
    world_call(lab, DSC, 'autosampler.load', {'position': 1})
    lab.backend.advance(60)
    world_call(lab, DSC, 'lid.set', {'opened': False})
    lab.backend.advance(60)
    world_call(lab, DSC, 'gas.set_flow', {'flow_m3_s': 50e-6 / 60})
    world_call(lab, DSC, 'gas.set_open', {'opened': True})
    world_call(lab, DSC, 'thermal.set_temperature', {'temperature_k': K0 + 200, 'rate_k_s': 20 / 60})
    lab.backend.advance(900)


def used(c, inst, verb, exit_code=0):
    return any(i == inst and v == verb and e == exit_code for i, v, e in c.verbs_used())


# ------------------------------------------------------------------ checkers
def c_dsc_run(c):
    e = c.ev(DSC)
    res = ev_call(c, DSC, 'acquisition.result')
    c.equal('the DSC run succeeded', e.get('acquisition.phase'), 'succeeded')
    rows = res.get('rows') or []
    c.add('the run covered 140 -> 180 °C', rows and abs(rows[0]['sample_k'] - (K0 + 140)) < 1.0 and abs(max(r['sample_k'] for r in rows) - (K0 + 180)) < 1.0,
          rows[:1] and (rows[0]['sample_k'], max(r['sample_k'] for r in rows)))
    c.near('purge 50 mL/min', e.get('gas.flow_m3_s'), 50e-6 / 60, 1e-9)
    t = c.work_file('dsc.json') or ''
    c.add('dsc.json holds the result record', '"heatflow_w"' in t and '"rows"' in t, t[:80])
    c.near('dh.txt holds the indium fusion enthalpy in J/g (NIST SRM 2232a: 28.51 J/g, ±1)', c.number_file('dh.txt'), 28.51, 1.0)


def c_tga_residue(c):
    e = c.ev(TGA)
    res = ev_call(c, TGA, 'acquisition.result')
    rows = res.get('rows') or []
    c.equal('the TGA run succeeded', e.get('acquisition.phase'), 'succeeded')
    c.add('heated to 600 °C under nitrogen', rows and max(r['sample_k'] for r in rows) > K0 + 595 and all(r.get('sample_gas_target') == 'N2' for r in rows),
          rows and max(r['sample_k'] for r in rows))
    if rows:
        pct = 100.0 * rows[-1]['mass_kg'] / rows[0]['mass_kg']
        c.near('residue.txt holds the residual mass in % (±0.5)', c.number_file('residue.txt'), pct, 0.5)
    else:
        c.add('residue.txt holds the residual mass in %', False, 'no rows')


def c_dma(c):
    e = c.ev(DMA)
    res = ev_call(c, DMA, 'acquisition.result')
    c.equal('the DMA run succeeded', e.get('acquisition.phase'), 'succeeded')
    blocks = res.get('blocks') or []
    c.add('one 25 °C / 1 Hz block measured', len(blocks) == 1 and abs(blocks[0].get('frequency_hz', 0) - 1.0) < 1e-9, blocks[:1])
    sm = ((blocks[0].get('analysis') or {}).get('storage_modulus_pa') if blocks else None)
    got = c.number_file('modulus.txt')
    c.add("modulus.txt holds E' in MPa (±1 %)", got is not None and sm and abs(got - sm / 1e6) <= 0.01 * abs(sm / 1e6), (got, sm))


def c_tma(c):
    e = c.ev(TMA)
    res = ev_call(c, TMA, 'acquisition.result')
    rows = res.get('rows') or []
    c.equal('the TMA run succeeded', e.get('acquisition.phase'), 'succeeded')
    c.add('heated to 80 °C with a 0.05 N probe force', rows and abs(max(r['temperature_k'] for r in rows) - (K0 + 80)) < 1.0
          and all(abs(r['force_n'] - 0.05) < 1e-3 for r in rows), rows and max(r['temperature_k'] for r in rows))
    if rows:
        c.near('dl.txt holds the expansion in µm (±0.05)', c.number_file('dl.txt'), (rows[-1]['displacement_m'] - rows[0]['displacement_m']) * 1e6, 0.05)
    else:
        c.add('dl.txt holds the expansion in µm', False, 'no rows')
    c.add('the furnace was opened and the probe lifted at the end', e.get('furnace.opened') and not e.get('probe.engaged'), (e.get('furnace.opened'), e.get('probe.engaged')))


def c_lfa(c):
    e = c.ev(LFA)
    res = ev_call(c, LFA, 'acquisition.result')
    c.equal('the flash shot succeeded', e.get('acquisition.phase'), 'succeeded')
    c.add('a pulse was fired and recorded', res.get('pulse_time_s') is not None and len(res.get('rows') or []) > 100, res.get('pulse_time_s'))
    t = c.work_file('flash.json') or ''
    c.add('flash.json holds the record', '"pulse_time_s"' in t, t[:80])


def c_tps(c):
    res = ev_call(c, TPS, 'acquisition.result')
    k = (res.get('fit') or {}).get('conductivity_w_m_k')
    c.equal('the TPS measurement succeeded', res.get('phase'), 'succeeded')
    c.near('k.txt holds the conductivity in W/(m K) (±1 %)', c.number_file('k.txt'), k if k else -1, abs(k) * 0.01 if k else 0)


def c_tam(c):
    e = c.ev(TAM)
    res = ev_call(c, TAM, 'acquisition.result')
    c.equal('the TAM recording succeeded', e.get('acquisition.phase'), 'succeeded')
    c.near('thermostat at 25 °C', e.get('bath.target_k'), K0 + 25, 1e-6)
    c.add('60 s recorded every 0.5 s', abs((res.get('elapsed_s') or 0) - 60.0) < 1e-6 and len(res.get('rows') or []) == 121, (res.get('elapsed_s'), len(res.get('rows') or [])))


def c_mmc(c):
    res = ev_call(c, MMC, 'test.result')
    c.equal('the heat-wait-search test completed', res.get('phase'), 'succeeded')
    mt = res.get('max_temperature_k')
    c.near('arc.txt holds the maximum temperature in °C (±0.5)', c.number_file('arc.txt'), (mt - K0) if mt else -999, 0.5)
    jobs = [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == MMC]
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in jobs), [(j.handle, j.state) for j in jobs])


def c_c6000(c):
    rows = c.ev(C6).get('measurements.list') or []
    done = [r for r in rows if r.get('name') == 'BA_1' and r.get('status') == 'completed']
    c.add('measurement BA_1 completed', bool(done), [(r.get('name'), r.get('status')) for r in rows])
    ho = done[0].get('gross_calorific_value_j_kg') if done else None
    c.near('ho.txt holds the gross calorific value in MJ/kg (±0.01)', c.number_file('ho.txt'), ho / 1e6 if ho else -1, 0.01)
    c.equal('working mode isoperibol 25 °C', c.ev(C6).get('mode.working'), 'isoperibol_25')


def c_dsc_hot(c):
    e = c.ev(DSC)
    c.add('the pan is back on tray position 1', not e.get('sample.present') and (e.get('autosampler.tray_occupied') or [False])[0],
          (e.get('sample.present'), (e.get('autosampler.tray_occupied') or [None])[0]))
    c.add('the empty cell was zeroed', used(c, DSC, 'zero'), c.verbs_used()[-3:])
    c.add('temperature control off', not e.get('thermal.enabled'), e.get('thermal.enabled'))


# ------------------------------------------------------------------ gold solutions
GOLD_DSC = r'''I=ta_dsc2500
chemlab run $I load 1
chemlab run $I close lid
chemlab run $I gas 50mL/min
chemlab run $I method start=140C ramp=180C@10K/min hold=1min
chemlab run $I set-temp 140C --rate 20K/min
chemlab run $I wait-temp --within 0.3K --max 1h
job=$(chemlab run $I start --json | jq -r .job)
chemlab wait "$job" --max 2h
chemlab run $I result --save-to dsc.json
chemlab run $I analyze integrate mass=2mg from=150C to=170C --json | jq -r '.result.specific_enthalpy_j_kg / 1000' > dh.txt
cat dh.txt'''

GOLD_TGA = r'''I=ta_tga5500
chemlab run $I zero
chemlab run $I load 1
chemlab run $I gas N2 25mL/min --balance 10mL/min
chemlab run $I method start=40C ramp=600C@20K/min gas=N2
chemlab run $I start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
if [ $rc -eq 3 ] && jq -e '.error.code == "FURNACE_OPEN"' s.json >/dev/null; then
  chemlab run $I close furnace
fi
chemlab run $I set-temp 40C --rate 20K/min
chemlab run $I wait-temp --max 30min
job=$(chemlab run $I start --json | jq -r .job)
chemlab wait "$job" --max 3h
chemlab run $I result --save-to tga.json > /dev/null
jq -r '.result.rows | (.[-1].mass_kg / .[0].mass_kg * 100)' tga.json > residue.txt
cat residue.txt'''

GOLD_DMA = r'''I=ta_dma850
chemlab run $I load --preload 0.01N --json > l.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NO_AIR"' l.json >/dev/null; then
  jq -r '.error.fix' l.json
  chemlab run $I gas on
  chemlab run $I load --preload 0.01N
fi
chemlab run $I close furnace
chemlab run $I method step=25C freq=1Hz strain=0.01%
chemlab run $I start
chemlab run $I wait --max 1h
chemlab run $I result --save-to dma.json > /dev/null
jq -r '.result.blocks[0].analysis.storage_modulus_pa / 1e6' dma.json > modulus.txt
cat modulus.txt'''

GOLD_TMA = r'''I=ta_tma450
chemlab run $I load
chemlab run $I zero
chemlab run $I close furnace
chemlab run $I gas 100mL/min
chemlab run $I method start=30C ramp=80C@5K/min hold=1min force=0.05N
chemlab run $I set-temp 30C --rate 10K/min
chemlab run $I wait-temp --max 30min
chemlab run $I start
chemlab run $I wait --max 2h
chemlab run $I result --save-to tma.json > /dev/null
jq -r '.result.rows | ((.[-1].displacement_m - .[0].displacement_m) * 1e6)' tma.json > dl.txt
chemlab run $I set-temp off
chemlab run $I unload --json > u.json || true
chemlab run $I wait-temp --to 35C --within 3K --max 2h
chemlab run $I open furnace
chemlab run $I unload
cat dl.txt'''

GOLD_LFA = r'''I=netzsch_lfa467
chemlab run $I load 1
chemlab run $I close furnace
chemlab run $I gas 50mL/min --channel protective
chemlab run $I gas 50mL/min --channel purge1
chemlab run $I method temp=25C
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "DETECTOR_OFF"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab call $I detector.set_enabled --enabled true
  chemlab wait 60
  chemlab run $I start
fi
chemlab run $I wait --max 1h
chemlab run $I result --save-to flash.json'''

GOLD_TPS = r'''I=hot_tps2500
chemlab run $I start --json > s.json || chemlab run $I power on
chemlab run $I method power=0.5W time=1.5s
chemlab run $I start
chemlab run $I wait --max 10min
chemlab run $I result --json | jq -r '.result.fit.conductivity_w_m_k' > k.txt
cat k.txt'''

GOLD_TAM = r'''I=ta_tamiv
chemlab run $I power on
chemlab run $I set-temp 25C
chemlab run $I wait-temp --max 2h
chemlab run $I method time=60s interval=0.5s
chemlab run $I start
chemlab run $I wait --max 10min
chemlab run $I result'''

GOLD_MMC = r'''I=netzsch_mmc315
chemlab run $I power on
chemlab run $I method mode=hws start=50C end=220C
job=$(chemlab run $I start --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 20h
chemlab run $I result --json | jq -r '.result.max_temperature_k - 273.15' > arc.txt
cat arc.txt'''

GOLD_C6 = r'''I=ika_c6000
chemlab run $I method name=BA_1 mass=1g
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "MODE_MISMATCH"' s.json >/dev/null; then
  jq -r '.error.message' s.json
  mode=$(chemlab read $I mode.available --json | jq -r '.observations["mode.available"].value | map(select(startswith("isoperibol"))) | .[0]')
  chemlab run $I method mode=$mode
  chemlab wait 7min
  chemlab run $I start
fi
chemlab run $I load C6012-0001
chemlab run $I wait --max 1h
chemlab run $I result --json | jq -r '.result.gross_calorific_value_j_kg / 1e6' > ho.txt
cat ho.txt'''

GOLD_DSC_HOT = r'''I=ta_dsc2500
chemlab run $I open lid --json > o.json; rc=$?
jq -r '.error.code + ": " + .error.fix' o.json
if [ $rc -eq 3 ] && jq -e '.error.code == "HOT_CELL"' o.json >/dev/null; then
  chemlab run $I set-temp off
  chemlab run $I wait-temp --to 36C --within 2K --max 2h
  chemlab run $I open lid
fi
chemlab run $I unload
chemlab run $I zero --json > z.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_AMBIENT"' z.json >/dev/null; then
  jq -r '.error.fix' z.json
  chemlab run $I wait-temp --to 25C --within 0.001K --max 1h
  chemlab run $I zero
fi
chemlab run $I status'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'ta01_dsc_scan', 'kind': 'multi-step + long action (job) + record + analysis', 'instruments': [DSC], 'check': c_dsc_run,
     'instruction': 'On the DSC 2500 (ta_dsc2500), measure the indium pan on tray position 1: equilibrate at 140 °C, heat to 180 °C at '
                    '10 °C/min with a 1 min hold, under 50 mL/min nitrogen purge. Save the result record to dsc.json, and write the fusion enthalpy '
                    'of the 2.000 mg indium specimen in J/g (peak between 150 and 170 °C, linear baseline) into dh.txt.', 'gold': GOLD_DSC},
    {'id': 'ta02_tga_recover_furnace', 'kind': 'recovery: furnace open at start', 'instruments': [TGA], 'check': c_tga_residue,
     'instruction': 'On the TGA 5500 (ta_tga5500), tare the empty balance, load the pan on tray position 1 and heat it from 40 °C to 600 °C at '
                    '20 °C/min under nitrogen (25 mL/min sample purge, 10 mL/min balance purge). Write the residual mass in percent of the '
                    'initial mass into residue.txt.', 'gold': GOLD_TGA},
    {'id': 'ta03_dma_recover_air', 'kind': 'recovery: preload without the air bearing', 'instruments': [DMA], 'check': c_dma,
     'instruction': 'The film specimen is mounted in the DMA 850 (ta_dma850). Clamp it with a 0.01 N preload, measure one 25 °C block at 1 Hz '
                    'and 0.01 % strain, and write the storage modulus E\' in MPa into modulus.txt.', 'gold': GOLD_DMA},
    {'id': 'ta04_tma_expansion', 'kind': 'multi-step + clean-up (hot furnace)', 'instruments': [TMA], 'check': c_tma,
     'instruction': 'On the TMA 450 (ta_tma450), measure the expansion of the specimen from 30 °C to 80 °C at 5 °C/min (1 min hold) with a '
                    '0.05 N expansion-probe force and a 100 mL/min purge. Write the length change in µm into dl.txt; at the end leave the '
                    'furnace open and the probe lifted.', 'gold': GOLD_TMA},
    {'id': 'ta05_lfa_recover_detector', 'kind': 'recovery: detector off', 'instruments': [LFA], 'check': c_lfa,
     'instruction': 'Fire one laser-flash shot at 25 °C on the specimen in changer position 1 of the LFA 467 (netzsch_lfa467), with the '
                    'protective gas and purge line 1 at 50 mL/min, and save the record to flash.json.', 'gold': GOLD_LFA},
    {'id': 'ta06_tps_conductivity', 'kind': 'recovery: switched off + report', 'instruments': [TPS], 'check': c_tps,
     'instruction': 'Measure the thermal conductivity of the mounted specimen with the Hot Disk TPS 2500 S (hot_tps2500): 0.5 W heating '
                    'power for 1.5 s. Write the conductivity in W/(m K) into k.txt.', 'gold': GOLD_TPS},
    {'id': 'ta07_tam_isothermal', 'kind': 'multi-step (power, thermostat, record)', 'instruments': [TAM], 'check': c_tam,
     'instruction': 'Record the heat flow of the TAM IV (ta_tamiv) at 25 °C for 60 s with a 0.5 s sampling interval.', 'gold': GOLD_TAM},
    {'id': 'ta08_arc_hws_job', 'kind': 'long action: job id (status, wait) + report', 'instruments': [MMC], 'check': c_mmc,
     'instruction': 'Run a heat-wait-search test on the MMC 315 (netzsch_mmc315) from 50 °C to 220 °C. Follow it as a job, and when it has '
                    'finished write the maximum sample temperature in °C into arc.txt.', 'gold': GOLD_MMC},
    {'id': 'ta09_c6000_recover_mode', 'kind': 'recovery: working mode vs cooling water', 'instruments': [C6], 'check': c_c6000,
     'instruction': 'On the IKA C 6000 (ika_c6000), measure the 1 g benzoic acid sample prepared in bomb C6012-0001 as measurement "BA_1" '
                    '(isoperibolic) and write its gross calorific value in MJ/kg into ho.txt.', 'gold': GOLD_C6},
    {'id': 'ta10_dsc_recover_hot_cell', 'kind': 'recovery: hot cell', 'instruments': [DSC], 'world': w_dsc_hot, 'check': c_dsc_hot,
     'instruction': 'The DSC 2500 (ta_dsc2500) has just been used at 200 °C. Put the pan in the cell back on its tray position, then zero '
                    'the empty cell.', 'gold': GOLD_DSC_HOT, 'kind_note': 'two recoveries: HOT_CELL, then NOT_AMBIENT'},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'thermal_analysis'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('thermal_analysis', TASKS, only=only, noact=noact)

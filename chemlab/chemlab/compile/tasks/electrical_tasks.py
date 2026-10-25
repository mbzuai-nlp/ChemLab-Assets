"""electrical class acceptance tasks (8, 5 of them recovery tasks): gold solutions use `chemlab run`; the checkers read the
evaluator view, the evaluator's own result query and the agent's report files."""
from .common import run_suite
from .electrical_world import config as world_config

K0 = 273.15
M91, QP, VSM, AT, LSR = 'lakeshore_measurereadym91', 'signatone_quadpro2', 'lakeshore_8604vsm', 'novocontrol_alphat', 'linseis_lsr3'


def ev_call(c, iid, cap, args=None):
    r = c.lab.backend.evaluator_invoke(iid, cap, args or {}, 'check-%s-%s' % (iid, cap))
    return r.get('result') or {}


def world_call(lab, iid, cap, args):
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, int(lab.backend.now() * 10)))
    assert r.get('status') in ('succeeded', 'accepted'), (cap, r)


def w_qp_probe_down(lab):
    """The last user left the probe down on the wafer centre."""
    world_call(lab, QP, 'probe.set_contact', {'contact': True})
    lab.backend.advance(5)


def w_vsm_after_run(lab):
    """The VSM right after a measurement: on, rod lowered, coils set, vibrating, field held at 0.3 T."""
    world_call(lab, VSM, 'power.set', {'enabled': True})
    world_call(lab, VSM, 'sample.set_lift', {'lift_m': 0.0})
    lab.backend.advance(10)
    world_call(lab, VSM, 'coil.configure', {'nominal_coupling_wb_per_a_m3': 0.1, 'moment_range_a_m2': 0.01})
    world_call(lab, VSM, 'vibration.configure', {'enabled': True, 'amplitude_m': 0.0005, 'frequency_hz': 82.0})
    world_call(lab, VSM, 'field.set_target', {'field_t': 0.3, 'ramp_rate_t_s': 0.01})
    lab.backend.advance(60)


# ------------------------------------------------------------------ checkers
def c_hall(c):
    e = c.ev(M91)
    c.equal('the contact check passed', (e.get('measurement.status') or {}).get('contact_check'), 'succeeded')
    c.equal('the acquisition succeeded', e.get('acquisition.phase'), 'succeeded')
    c.near('excitation 0.5 mA', e.get('excitation.programmed_current_a'), 0.0005, 1e-9)
    t = c.work_file('hall.json') or ''
    c.add('hall.json holds the record', '"voltage_v"' in t and '"rows"' in t, t[:80])


def c_map(c):
    res = ev_call(c, QP, 'acquisition.result')
    c.equal('the map succeeded', res.get('phase'), 'succeeded')
    rows = res.get('rows') or []
    pts = sorted((round(r['x_m'], 6), round(r['y_m'], 6)) for r in rows)
    c.equal('five points: centre and +-20 mm on x and y', pts, sorted([(0.0, 0.0), (0.02, 0.0), (-0.02, 0.0), (0.0, 0.02), (0.0, -0.02)]))
    mean = (res.get('statistics') or {}).get('mean_ohm_sq')
    c.near('rs.txt holds the mean sheet resistance in ohm/sq (±0.1 %)', c.number_file('rs.txt'), mean or -1, abs(mean or 0) * 1e-3)


def c_probe(c):
    e = c.ev(QP)
    c.near('stage x = 10 mm', e.get('stage.x_m'), 0.01, 1e-9)
    c.near('stage y = -5 mm', e.get('stage.y_m'), -0.005, 1e-9)
    c.near('probe down on the wafer', e.get('probe.position_m'), 0.0, 1e-9)


def c_vsm_loop(c):
    res = ev_call(c, VSM, 'acquisition.result')
    rows = res.get('rows') or []
    c.equal('the loop succeeded', res.get('phase'), 'succeeded')
    c.equal('21 points from +0.5 T to -0.5 T and back', [round(r['field_t'], 6) for r in rows][:1] + [len(rows)], [0.5, 21])
    m0 = rows[0].get('moment_a_m2') if rows else None
    c.near('m.txt holds the moment at +0.5 T in emu (±0.1 %)', c.number_file('m.txt'), (m0 or 0) * 1e3, abs((m0 or 0) * 1e3) * 1e-3 + 1e-12)
    loop = ev_call(c, VSM, 'analysis.loop', {'rows': rows}) if len(rows) >= 3 else {}
    hc = [abs(x['field_t']) for x in loop.get('coercive_crossings') or []]
    c.near('hc.txt holds the coercive field in mT (mean of both branches, ±0.5 %)', c.number_file('hc.txt'), sum(hc) / len(hc) * 1e3 if hc else -1,
           (sum(hc) / len(hc) * 1e3 * 5e-3) if hc else 0)


def c_vsm_raise(c):
    e = c.ev(VSM)
    c.near('rod at 50 mm', e.get('sample.lift_m'), 0.05, 1e-9)
    c.near('field at zero', e.get('field.indicated_t'), 0.0, 1e-9)
    c.add('vibration off', not e.get('vibration.enabled'), e.get('vibration.enabled'))


def c_dielectric(c):
    res = ev_call(c, AT, 'acquisition.result')
    rows = res.get('rows') or []
    c.equal('the sweep succeeded', res.get('phase'), 'succeeded')
    c.equal('6 frequencies, 10 Hz .. 1 MHz', [len(rows)] + [round(rows[0]['frequency_hz'], 6) if rows else None, round(rows[-1]['frequency_hz'], 3) if rows else None],
            [6, 10.0, 1000000.0])
    k = [r for r in rows if abs(r['frequency_hz'] - 1000.0) < 1e-6]
    c.near("eps.txt holds eps' at 1 kHz (±0.1 %)", c.number_file('eps.txt'), k[0]['epsilon_real'] if k else -1, abs(k[0]['epsilon_real']) * 1e-3 if k else 0)


def c_seebeck(c):
    e = c.ev(LSR)
    res = ev_call(c, LSR, 'acquisition.result')
    pts = res.get('transport_points') or []
    c.equal('the measurement succeeded', res.get('phase'), 'succeeded')
    c.near('furnace set to 100 °C', e.get('thermal.target_k'), K0 + 100, 1e-6)
    c.near('gradient 5 K', e.get('thermal.target_delta_k'), 5.0, 1e-9)
    s = [p['seebeck_v_k'] for p in pts if p.get('seebeck_v_k') is not None]
    mean = sum(s) / len(s) * 1e6 if s else None
    c.near('s.txt holds the mean Seebeck coefficient in µV/K (±0.5 %)', c.number_file('s.txt'), mean or -1, abs(mean or 0) * 5e-3)


def c_lsr_power(c):
    e = c.ev(LSR)
    c.add('switched on', e.get('power.enabled'))
    c.near('furnace set to 50 °C', e.get('thermal.target_k'), K0 + 50, 1e-6)
    c.near('gradient 2 K', e.get('thermal.target_delta_k'), 2.0, 1e-9)
    c.add('temperature control on', e.get('thermal.enabled'))


# ------------------------------------------------------------------ gold solutions
GOLD_HALL = r'''I=lakeshore_measurereadym91
chemlab run $I method current=0.5mA
chemlab run $I contact check --json > cc.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "WARMING_UP"' cc.json >/dev/null; then
  jq -r '.error.message + " -> " + .error.fix' cc.json
  chemlab wait 30min
  chemlab run $I contact check --json > cc.json
fi
chemlab wait "$(jq -r .job cc.json)" --max 10min
chemlab run $I start
chemlab run $I wait --max 10min
chemlab run $I result --save-to hall.json'''

GOLD_MAP = r'''I=signatone_quadpro2
chemlab run $I method point=0mm,0mm point=20mm,0mm point=-20mm,0mm point=0mm,20mm point=0mm,-20mm edge=3mm
chemlab run $I start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_CALIBRATED"' s.json >/dev/null; then
  jq -r '.error.fix' s.json
  chemlab run $I method calibration=false
  chemlab run $I start
fi
chemlab run $I wait --max 30min
chemlab run $I result --json | jq -r '.result.statistics.mean_ohm_sq' > rs.txt
cat rs.txt'''

GOLD_PROBE = r'''I=signatone_quadpro2
chemlab run $I move 10mm -5mm --json > m.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "PROBE_DOWN"' m.json >/dev/null; then
  chemlab run $I contact off
  chemlab run $I move 10mm -5mm
fi
chemlab run $I contact on
chemlab run $I status'''

GOLD_LOOP = r'''I=lakeshore_8604vsm
chemlab run $I power on
chemlab run $I move --lift 0mm
chemlab run $I method range=10emu vibrate=on loop=0.5T/0.1T
chemlab run $I start
chemlab run $I wait --max 1h
chemlab run $I result --json | jq -r '.result.rows[0].moment_a_m2 * 1000' > m.txt
chemlab run $I analyze loop --json | jq -r '[.result.coercive_crossings[].field_t | fabs] | add / length * 1000' > hc.txt
cat m.txt hc.txt'''

GOLD_RAISE = r'''I=lakeshore_8604vsm
for try in 1 2 3; do
  chemlab run $I move --lift 50mm --json > mv.json && break
  code=$(jq -r .error.code mv.json); echo "refused: $code"
  case "$code" in
    VIBRATING) chemlab run $I method vibrate=off ;;
    FIELD_ON) chemlab run $I set-field 0 --wait ;;
    *) break ;;
  esac
done
chemlab run $I status'''

GOLD_DIEL = r'''I=novocontrol_alphat
chemlab run $I power on
chemlab run $I set-temp 20.05C
chemlab run $I wait-temp --within 0.1K --max 1h
chemlab run $I method sweep=10Hz..1MHz/6 voltage=0.3V
chemlab run $I start
chemlab run $I wait --max 10min
chemlab run $I result --save-to eps_sweep.json > /dev/null
jq -r '.result.rows[] | select(.frequency_hz > 999.999 and .frequency_hz < 1000.001) | .epsilon_real' eps_sweep.json > eps.txt
cat eps.txt'''

GOLD_SEEBECK = r'''I=linseis_lsr3
chemlab run $I power on
chemlab run $I set-temp 100C --rate 10K/min --gradient 5K
chemlab run $I wait-temp --max 2h
chemlab run $I method current=10mA time=3s
chemlab run $I start
chemlab run $I wait --max 10min
chemlab run $I result --json | jq -r '[.result.transport_points[].seebeck_v_k] | add / length * 1e6' > s.txt
cat s.txt'''

GOLD_LSR_POWER = r'''I=linseis_lsr3
chemlab run $I set-temp 50C --rate 10K/min --gradient 2K --json > t.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "POWER_OFF"' t.json >/dev/null; then
  chemlab run $I power on
  chemlab run $I set-temp 50C --rate 10K/min --gradient 2K
fi
chemlab run $I status'''


def cfg(c):
    return world_config(c)


TASKS = [
    {'id': 'el01_hall_recover_warmup', 'kind': 'recovery: warm-up + contact check job', 'instruments': [M91], 'check': c_hall,
     'instruction': 'On the Lake Shore M91 FastHall (lakeshore_measurereadym91), set a 0.5 mA excitation, run the automatic contact check, '
                    'then run the measurement and save the record to hall.json.', 'gold': GOLD_HALL},
    {'id': 'el02_fpp_recover_calibration', 'kind': 'recovery: method requires a calibration', 'instruments': [QP], 'check': c_map,
     'instruction': 'Map the sheet resistance of the wafer on the Signatone QuadPro2 (signatone_quadpro2) at 5 points: the centre and 20 mm '
                    'from it on +x, -x, +y and -y, with 3 mm edge exclusion. There is no calibration standard in the lab, so measure '
                    'uncalibrated. Write the mean sheet resistance in ohm/sq into rs.txt.', 'gold': GOLD_MAP},
    {'id': 'el03_fpp_recover_probe_down', 'kind': 'recovery: probe left down', 'instruments': [QP], 'world': w_qp_probe_down, 'check': c_probe,
     'instruction': 'On the QuadPro2 (signatone_quadpro2), put the probe down on the wafer at x = 10 mm, y = -5 mm.', 'gold': GOLD_PROBE},
    {'id': 'el04_vsm_loop', 'kind': 'multi-step + record + analysis', 'instruments': [VSM], 'check': c_vsm_loop,
     'instruction': 'Measure a hysteresis loop of the sample in the 8604 VSM (lakeshore_8604vsm) from +0.5 T to -0.5 T and back in 0.1 T steps '
                    '(10 emu coil range, vibration on). Write the moment at the first +0.5 T point in emu into m.txt, and the coercive field (the '
                    'mean of both branches, from the loop analysis) in mT into hc.txt.', 'gold': GOLD_LOOP},
    {'id': 'el05_vsm_recover_raise', 'kind': 'recovery: rod moves only at rest and zero field', 'instruments': [VSM], 'world': w_vsm_after_run,
     'check': c_vsm_raise, 'instruction': 'The VSM (lakeshore_8604vsm) has just finished a measurement. Raise the sample rod to 50 mm for a '
                                          'sample exchange.', 'gold': GOLD_RAISE},
    {'id': 'el06_dielectric_sweep', 'kind': 'multi-step + report', 'instruments': [AT], 'check': c_dielectric,
     'instruction': 'On the Novocontrol Alpha-T (novocontrol_alphat), measure the water cell at 20.05 °C from 10 Hz to 1 MHz (6 log-spaced '
                    'frequencies, 0.3 V). Write the real permittivity at 1 kHz into eps.txt.', 'gold': GOLD_DIEL},
    {'id': 'el07_seebeck', 'kind': 'multi-step (temperature, gradient, run) + report', 'instruments': [LSR], 'check': c_seebeck,
     'instruction': 'On the Linseis LSR-3 (linseis_lsr3), measure the specimen at 100 °C with a 5 K gradient (10 mA, 3 s). Write the mean '
                    'Seebeck coefficient in µV/K into s.txt.', 'gold': GOLD_SEEBECK},
    {'id': 'el08_lsr_recover_power', 'kind': 'recovery: instrument switched off', 'instruments': [LSR], 'check': c_lsr_power,
     'instruction': 'Set the LSR-3 (linseis_lsr3) furnace to 50 °C at 10 °C/min with a 2 K gradient across the specimen.', 'gold': GOLD_LSR_POWER},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'electrical'
    _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('electrical', TASKS, only=only, noact=noact)

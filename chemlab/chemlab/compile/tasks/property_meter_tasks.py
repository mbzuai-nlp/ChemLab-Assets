"""Property-meter class acceptance tasks (10): gold solutions use `chemlab run` (and `chemlab call` where the class has no verb, e.g.
the AQUALAB lever); checkers read the evaluator view and the agent's report files.  World: property_meter_world (samples put in
by the operator; staged entries for the P-4200 and the K-7400S)."""
import json
import re

from ...glab import single
from .common import run_suite
from .property_meter_world import config as world_config

K0 = 273.15
DMA, ABM, JAS, M914, AQ, HACH, KN, HX, MP = ('antonpaar_dma5002', 'antonpaar_abbematadvanced', 'jasco_p4200_sample', 'metrohm_914',
                                             'aqualab_4te', 'hach_tl2300epa', 'knauer_k7400s_ready', 'mt_hx204', 'mt_mp70')


def cfg(c):
    return world_config(c, extra=False)


def staged(name):
    return lambda: single([name], world_config())


def world_call(lab, iid, cap, args):
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, int(lab.backend.now() * 10)))
    assert r.get('status') == 'succeeded', (cap, r)


def w_abbemat_lid_open(lab):
    world_call(lab, ABM, 'lid.set_angle', {'angle_rad': 1.5707963267948966})


def w_aqualab_lever_up(lab):
    world_call(lab, AQ, 'latch.set_angle', {'angle_rad': 0.0})


def jobs_of(c, iid):
    return [j for j in getattr(c.lab.core, 'class_jobs', {}).values() if j.instrument == iid]


# ------------------------------------------------------------------ checkers
def c_density(c):
    e = c.ev(DMA)
    c.near('cell at 20 °C', e['thermal.target_k'], K0 + 20, 1e-6)
    c.add('a density measurement is stored', len(e.get('data.measured') or []) >= 1, len(e.get('data.measured') or []))
    c.near('density.txt reports the ethanol density in g/cm3 (0.7893 ± 0.0002)', c.number_file('density.txt'), 0.7893, 0.0002)


def c_abbemat_cal(c):
    e = c.ev(ABM)
    corr = e.get('adjustment.correction') or {}
    c.add('a user adjustment is applied (not factory)', corr.get('factory') is False, corr)
    c.near('the adjustment offset makes ethanol read 1.3614', corr.get('offset'), 0.0003, 2e-5)
    c.near('nd.txt reports nD 1.3614', c.number_file('nd.txt'), 1.3614, 5e-5)


def c_abbemat_lid(c):
    e = c.ev(ABM)
    c.add('lid closed', (e.get('lid.angle_rad') or 0) <= 0.01, e.get('lid.angle_rad'))
    c.add('a measurement is stored', len(e.get('data.measured') or []) >= 1)
    c.near('nd.txt reports nD 1.3611', c.number_file('nd.txt'), 1.3611, 5e-5)


def verify_verdict(entry, key='within_vendor_tolerance'):
    """What the instrument answered to a verification request, from the reply chemlab printed to the agent (text or --json):
    True (``key`` true / passed), False, or None (not readable)."""
    out = entry.get('stdout') or ''
    try:
        js = json.loads(out)
    except ValueError:
        js = None
    if isinstance(js, dict):
        if isinstance(js.get('passed'), bool):
            return js['passed']
        stack = [js]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if isinstance(o.get(key), bool):
                    return o[key]
                stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
        return None
    m = re.search(r'verdict: (passed|NOT passed)', out)
    if m:
        return m.group(1) == 'passed'
    m = re.search(re.escape(key) + r'\W{1,4}(true|false)', out, re.I)
    return (m.group(1).lower() == 'true') if m else None


def c_aqualab(c):
    e = c.ev(AQ)
    c.add('lever at READ', (e.get('latch.angle_rad') or 0) >= 1.4, e.get('latch.angle_rad'))
    now, up = c.lab.backend.now(), e.get('warmup.elapsed_s') or 0
    runs = [(ent, (up - (now - ent['sim_time_s']))) for ent, _ in c.native(AQ, 'calibration.verify')]   # warm-up time at each call
    warm = [ent for ent, w in runs if w >= 900]
    c.add('calibration.verify ran after the 15 min warm-up', bool(warm), [round(w) for _, w in runs])
    verdict = verify_verdict(warm[-1]) if warm else None
    c.add('the instrument reported that last verification as passed', verdict is True, verdict)
    t = (c.work_file('verify.txt') or '').lower()
    c.add('verify.txt says the verification passed', t.startswith('passed'), t[:60])


def c_hach(c):
    e = c.ev(HACH)
    c.equal('RapidCal calibration stored', e.get('calibration.status'), 'rapid_0_40')
    cal = c.native(HACH, 'calibration.rapid')
    after = [ent for ent, _ in c.native(HACH, 'calibration.verify') if cal and ent['seq'] > cal[-1][0]['seq']]
    c.add('calibration.verify ran after the RapidCal calibration (request log)', bool(after), len(after))
    verdict = verify_verdict(after[-1], 'within_five_percent') if after else None
    c.add('the instrument reported that verification as passed', verdict is True, verdict)
    t = (c.work_file('verify.txt') or '').lower()
    c.add('verify.txt says passed', t.startswith('passed'), t[:60])


def c_jasco(c):
    e = c.ev(JAS)
    c.near('rotation.txt reports the rotation in degrees (6.64 ± 0.01)', c.number_file('rotation.txt'), 6.637, 0.01)
    c.add('a job was used and it succeeded', any(j.state == 'succeeded' for j in jobs_of(c, JAS)), [(j.handle, j.state) for j in jobs_of(c, JAS)])
    c.equal('the last scan was a sample scan', e.get('scan.phase'), 'succeeded')


def c_914(c):
    e = c.ev(M914)
    c.add('pH channel calibrated', e.get('probe.ph.calibrated'), e.get('probe.ph.calibrated'))
    c.near('endpoint hold 5 s', e.get('endpoint.hold_s'), 5.0, 1e-9)
    c.near('ph.txt reports pH 6.864', c.number_file('ph.txt'), 6.864, 0.002)


def c_knauer(c):
    c.add('the agent started the measurement (request log)', c.native(KN, 'acquisition.start'), len(c.native(KN, 'acquisition.start')))
    c.near('osm.txt reports the osmolality in mOsmol/kg (187 ± 3)', c.number_file('osm.txt'), 186.6, 3.0)
    e = c.ev(KN)
    c.equal('the measurement run finished', e.get('acquisition.phase'), 'succeeded')


def c_moisture(c):
    e = c.ev(HX)
    c.near('drying temperature 105 °C', e.get('thermal.target_k'), K0 + 105, 1e-6)
    c.near('drying time 5 min', e.get('method.duration_s'), 300.0, 1e-6)
    c.equal('the drying run finished', e.get('cycle.phase'), 'succeeded')
    c.near('mc.txt reports the moisture content in % (4.2 ± 0.3)', c.number_file('mc.txt'), 4.18, 0.3)


def c_melting(c):
    e = c.ev(MP)
    c.add('the measurement run finished', e.get('cycle.phase') == 'succeeded', e.get('cycle.phase'))
    c.near('clear point of capillary 1 (123.1 ± 0.3 °C)', c.number_file('mp.txt'), 123.1, 0.3)
    c.add('both capillaries melted (onset found)', all(e.get('channel_%d.onset_k' % i) for i in (0, 1)),
          [e.get('channel_%d.onset_k' % i) for i in (0, 1)])


# ------------------------------------------------------------------ gold solutions
GOLD_ABM_LID = r'''chemlab run antonpaar_abbematadvanced measure --wait --json > m.json; rc=$?
jq -r '.error.code + ": " + .error.fix' m.json
if [ $rc -eq 3 ] && jq -e '.error.code == "LID_OPEN"' m.json >/dev/null; then
  chemlab run antonpaar_abbematadvanced close lid
  chemlab run antonpaar_abbematadvanced measure --wait
fi
chemlab run antonpaar_abbematadvanced result --json | jq -r .value > nd.txt; cat nd.txt'''

GOLD_AQ = r'''chemlab run aqualab_4te measure --wait --json > m.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "NOT_LATCHED"' m.json >/dev/null; then
  chemlab call aqualab_4te latch.set_angle --angle_rad 1.57
  chemlab run aqualab_4te measure --wait
fi
chemlab run aqualab_4te check --reference 0.884 --json > v.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "WARMING_UP"' v.json >/dev/null; then
  jq -r .error.message v.json
  chemlab wait 900
  chemlab run aqualab_4te measure --wait
  chemlab run aqualab_4te check --reference 0.884 --json > v.json
fi
jq -r 'if .passed then "passed" else "failed" end' v.json > verify.txt; cat verify.txt'''

GOLD_HACH = r'''chemlab run hach_tl2300epa measure --wait
chemlab run hach_tl2300epa calibrate --json > cal.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "WARMING_UP"' cal.json >/dev/null; then
  jq -r .error.message cal.json
  chemlab wait 1800
  chemlab run hach_tl2300epa measure --wait
  chemlab run hach_tl2300epa calibrate
fi
chemlab run hach_tl2300epa measure --wait
chemlab run hach_tl2300epa check --reference 20NTU --json | jq -r 'if .passed then "passed" else "failed" end' > verify.txt; cat verify.txt'''

GOLD_JAS = r'''job=$(chemlab run jasco_p4200_sample measure --json | jq -r .job)
chemlab status "$job"
chemlab wait "$job" --max 30min
chemlab run jasco_p4200_sample result --json | jq -r '.value * 180 / 3.141592653589793' > rotation.txt; cat rotation.txt'''

GOLD_914 = r'''chemlab run metrohm_914 method hold=5s tolerance=0.1%
chemlab run metrohm_914 measure --wait
chemlab run metrohm_914 calibrate --standard 6.864
chemlab run metrohm_914 measure --wait --json | jq -r .value > ph.txt; cat ph.txt'''

TASKS = [
    {'id': 'pm01_density', 'kind': 'multi-step (temperature, measure, report)', 'instruments': [DMA], 'check': c_density,
     'instruction': 'The Anton Paar DMA 5002 density meter (antonpaar_dma5002) has an ethanol sample in its U-tube. Measure its density at '
                    '20 °C and write the density in g/cm3 into density.txt.',
     'gold': 'chemlab run antonpaar_dma5002 set-temp 20C --wait && chemlab run antonpaar_dma5002 measure --wait && '
             'chemlab run antonpaar_dma5002 result --json | jq -r ".value / 1000" > density.txt; cat density.txt'},
    {'id': 'pm02_refractometer_calibrate', 'kind': 'composite (adjustment run + decide) + report', 'instruments': [ABM], 'check': c_abbemat_cal,
     'instruction': 'The prism of the Abbemat refractometer (antonpaar_abbematadvanced) holds an ethanol standard certified at nD 1.3614 (20 °C). '
                    'Adjust the refractometer on it, then measure it again and write the refractive index into nd.txt.',
     'gold': 'chemlab run antonpaar_abbematadvanced calibrate --standard 1.3614 ethanol && chemlab run antonpaar_abbematadvanced measure --wait && '
             'chemlab run antonpaar_abbematadvanced result --json | jq -r .value > nd.txt; cat nd.txt'},
    {'id': 'pm03_recover_lid_open', 'kind': 'recovery: lid open', 'instruments': [ABM], 'world': w_abbemat_lid_open, 'check': c_abbemat_lid,
     'instruction': 'Measure the refractive index of the sample on the Abbemat refractometer (antonpaar_abbematadvanced) and write it into nd.txt.',
     'gold': GOLD_ABM_LID},
    {'id': 'pm04_recover_aw_verify', 'kind': 'recovery: lever not at READ + warm-up not finished', 'instruments': [AQ], 'world': w_aqualab_lever_up,
     'check': c_aqualab,
     'instruction': 'Verify the AQUALAB 4TE water-activity meter (aqualab_4te) with the verification standard in its chamber (certified aw 0.884 at '
                    '25 °C). Write "passed" or "failed" into verify.txt.', 'gold': GOLD_AQ},
    {'id': 'pm05_recover_turbidity_rapidcal', 'kind': 'recovery: warm-up (60 min) + calibrate + verify', 'instruments': [HACH], 'check': c_hach,
     'instruction': 'The Hach TL2300 turbidimeter (hach_tl2300epa) has the 20 NTU StablCal standard in it. Do a RapidCal calibration on it, then '
                    'verify against 20 NTU and write "passed" or "failed" into verify.txt.', 'gold': GOLD_HACH},
    {'id': 'pm06_polarimeter_job', 'kind': 'long action: job id (status, wait) + unit conversion', 'instruments': [JAS], 'lab_config': staged(JAS),
     'check': c_jasco,
     'instruction': 'The P-4200 polarimeter (jasco_p4200_sample) is zeroed and calibrated, with the sucrose sample cell in place. Start the '
                    'measurement as a job, check its status, wait for it, and write the optical rotation in degrees into rotation.txt.',
     'gold': GOLD_JAS},
    {'id': 'pm07_ph_calibrate', 'kind': 'multi-step (endpoint method, stable reading, calibrate)', 'instruments': [M914], 'check': c_914,
     'instruction': 'The pH probe of the Metrohm 914 meter (metrohm_914) stands in the NIST phosphate buffer, certified pH 6.864 at 25 °C. Set '
                    'the endpoint criterion to a 5 s hold within 0.1 %, calibrate the pH channel on this buffer, then measure it again and write '
                    'the pH into ph.txt.', 'gold': GOLD_914},
    {'id': 'pm08_osmolality', 'kind': 'long action (freezing run) + unit conversion', 'instruments': [KN], 'lab_config': staged(KN), 'check': c_knauer,
     'instruction': 'The K-7400S osmometer (knauer_k7400s_ready) is calibrated and has a sample tube on its measuring head. Measure the sample '
                    'and write its osmolality in mOsmol/kg into osm.txt.',
     'gold': 'chemlab run knauer_k7400s_ready measure --wait && chemlab run knauer_k7400s_ready result --json | jq -r ".value * 1000" > osm.txt; cat osm.txt'},
    {'id': 'pm09_moisture', 'kind': 'multi-step (temperature, method, drying run, report)', 'instruments': [HX], 'check': c_moisture,
     'instruction': 'Determine the moisture content of the sample on the HX204 moisture analyzer (mt_hx204): dry it at 105 °C for 5 minutes '
                    '(standard program, wet basis) and write the moisture content in % into mc.txt.',
     'gold': 'chemlab run mt_hx204 set-temp 105C && chemlab run mt_hx204 method mode=standard time=5min basis=wet && '
             'chemlab run mt_hx204 measure --wait && chemlab run mt_hx204 result --json | jq -r ".value * 100" > mc.txt; cat mc.txt'},
    {'id': 'pm10_melting_point', 'kind': 'multi-step (method store+select, long run, channels)', 'instruments': [MP], 'check': c_melting,
     'instruction': 'Capillaries 1 and 2 of the MP70 melting point system (mt_mp70) hold benzoic acid. Measure its melting range from 110 °C '
                    'to 130 °C at 1 K/min with the transmission criterion, and write the clear point of capillary 1 in °C into mp.txt.',
     'gold': 'chemlab run mt_mp70 method start=110C end=130C rate=1K/min criterion=transmission && chemlab run mt_mp70 measure --wait --max 3h && '
             'chemlab run mt_mp70 result --channel 1 --json | jq -r ".channel_1 - 273.15" > mp.txt; cat mp.txt'},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'property_meter'
    if 'lab_config' not in _t:
        _t['config'] = cfg


def main(only=None, noact=True):
    return run_suite('property_meter', TASKS, only=only, noact=noact)

"""Labware automation class acceptance tasks (8, 4 recovery): gold solutions use `chemlab run` (and jq) in the sandbox; the checkers
read the evaluator view (seal state, cap cycle, rack map, arm events).  The lab is the labware world (tasks/labware_automation_world.py).
"""
from . import labware_automation_world as W
from .common import run_suite

K0 = 273.15
A4S, IXC, DP5, ARM = 'azenta_automated_roll_heat_sealer_a4s', 'azenta_intellixcap_s1_46_0001', 'ziath_dp5_mirage_2023', 'brooks_preciseflex_400'
_N = [0]


def world_call(lab, iid, cap, args):
    _N[0] += 1
    r = lab.backend.evaluator_invoke(iid, cap, args, 'world-%s-%s-%d' % (iid, cap, _N[0]))
    assert r.get('status') in ('succeeded', 'accepted'), (cap, r)
    return r


# ------------------------------------------------------------------ worlds
def w_rack_lifted(lab):
    world_call(lab, DP5, 'power.set', {'enabled': True})
    world_call(lab, DP5, 'scene.set_rack_lift', {'lift_m': 0.05})


def w_arm_holding(lab):
    world_call(lab, ARM, 'power.set', {'enabled': True})
    world_call(lab, ARM, 'motion.set_joints', {'joints': W.ARM['containers']['plate_1']['pose']})
    lab.backend.advance(5)
    world_call(lab, ARM, 'sample.attach', {'container_id': 'plate_1', 'constraint_id': 'grip_plate_1'})
    world_call(lab, ARM, 'motion.set_joints', {'joints': W.ARM['home']})
    lab.backend.advance(5)


# ------------------------------------------------------------------ checkers
def c_sealed_protocol(c):
    e = c.ev(A4S)
    c.add('the plate is sealed', e.get('plate.simulated_sealed') is True, e.get('plate.simulated_sealed'))
    c.equal('protocol 4ti-0520 selected', e.get('protocol.current'), '4ti-0520')
    c.near('sealed at 175 °C (the protocol\'s temperature)', e.get('thermal.setpoint_k'), K0 + 175, 1e-6)
    c.equal('one seal used', e.get('film.remaining_seals'), 99)


def c_sealed_manual(c):
    e = c.ev(A4S)
    c.add('the plate is sealed', e.get('plate.simulated_sealed') is True, e.get('plate.simulated_sealed'))
    c.near('seal temperature 160 °C', e.get('thermal.setpoint_k'), K0 + 160, 1e-6)
    c.near('seal time 2.5 s', e.get('configuration.press_time_s'), 2.5, 1e-9)


def c_decap(c):
    e = c.ev(IXC)
    c.equal('decap cycle succeeded with the thread opened', (e.get('cycle.status'), e.get('cycle.host_confirmed')), ('succeeded', True))
    c.equal('the next cycle is a recap (the tube is open)', e.get('cycle.mode'), 'recap')
    c.equal('release-cap operation', e.get('cycle.operation_mode'), 'release_cap')
    c.equal('cycle.txt says succeeded', c.work_file('cycle.txt'), 'succeeded')


def c_stuck(c):
    e = c.ev(IXC)
    c.equal('the cycle failed (the cap did not turn)', e.get('cycle.status'), 'failed')
    t = c.work_file('decap.txt') or ''
    c.add('decap.txt reports FAILED with the cycle status', t.startswith('FAILED') and 'failed' in t[6:], t[:80])


def c_rack_audit(c):
    e = c.ev(DP5)
    data = e.get('results.data') or {}
    c.equal('a scan with identity matching completed', (e.get('acquisition.status'), data.get('identity_matching')), ('succeeded', True))
    t = c.work_file('problems.txt') or ''
    got = sorted(x.strip() for x in t.split() if x.strip())
    c.equal('problems.txt lists the mismatched and unreadable wells', got, sorted(data.get('mismatch_wells', []) + data.get('missing_wells', [])))
    c.equal('they are C5 and D7', got, ['C5', 'D7'])


def c_rack_csv(c):
    e = c.ev(DP5)
    c.equal('scan completed', e.get('acquisition.status'), 'succeeded')
    c.near('rack seated', e.get('scene.rack_lift_m'), 0.0, 1e-12)
    t = c.work_file('rack.csv') or ''
    lines = [x for x in t.splitlines() if x.strip()]
    c.add('rack.csv: a header and 96 wells', len(lines) == 97 and lines[0].startswith('well_id'), '%d lines' % len(lines))


def _at(e, pose):
    j = e.get('scene.joints') or {}
    return all(abs((j.get(k) or 0) - v) < 1e-6 for k, v in pose.items())


def c_transfer(c):
    e = c.ev(ARM)
    ev = e.get('sample.events') or []
    last = ev[-1] if ev else {}
    c.equal('plate_1 released at sealer_nest', (last.get('event'), last.get('container_id'), last.get('port_id')), ('release', 'plate_1', 'sealer_nest'))
    c.equal('nothing gripped', e.get('sample.attached_container_id'), None)
    c.add('the arm is back home', _at(e, W.ARM['home']), e.get('scene.joints'))


def c_holding(c):
    e = c.ev(ARM)
    ev = e.get('sample.events') or []
    last = ev[-1] if ev else {}
    c.equal('plate_1 released at sealer_nest', (last.get('event'), last.get('container_id'), last.get('port_id')), ('release', 'plate_1', 'sealer_nest'))
    c.add('gripped only once (no drop and re-pick)', sum(1 for x in ev if x.get('event') == 'attach') == 1, [x.get('event') for x in ev])


# ------------------------------------------------------------------ gold solutions
GOLD_LW01 = r'''chemlab run azenta_automated_roll_heat_sealer_a4s configure protocol=4ti-0520
chemlab run azenta_automated_roll_heat_sealer_a4s status
for i in $(seq 1 20); do
  chemlab run azenta_automated_roll_heat_sealer_a4s start --json > s.json && break
  jq -e '.error.code == "NOT_AT_TEMPERATURE"' s.json >/dev/null || break
  chemlab wait 30
done
chemlab run azenta_automated_roll_heat_sealer_a4s wait
chemlab run azenta_automated_roll_heat_sealer_a4s result'''

GOLD_LW02 = r'''chemlab run azenta_automated_roll_heat_sealer_a4s set-temp 160C
chemlab run azenta_automated_roll_heat_sealer_a4s configure time=2.5s
chemlab run azenta_automated_roll_heat_sealer_a4s start --json > s.json; rc=$?
jq -r '.error.code + ": " + .error.fix' s.json
while [ $rc -eq 3 ] && jq -e '.error.code == "NOT_AT_TEMPERATURE"' s.json >/dev/null; do
  chemlab wait 60
  chemlab run azenta_automated_roll_heat_sealer_a4s start --json > s.json; rc=$?
done
chemlab run azenta_automated_roll_heat_sealer_a4s wait'''

GOLD_LW03 = r'''chemlab run azenta_intellixcap_s1_46_0001 configure tube=tube_15ml mode=decap operation=release_cap
chemlab run azenta_intellixcap_s1_46_0001 start
chemlab run azenta_intellixcap_s1_46_0001 wait
chemlab run azenta_intellixcap_s1_46_0001 result --json | jq -r '.record["cycle.status"]' > cycle.txt
cat cycle.txt'''

GOLD_LW04 = r'''chemlab run azenta_intellixcap_s1_46_0001 configure tube=tube_15ml mode=decap
chemlab run azenta_intellixcap_s1_46_0001 start
chemlab run azenta_intellixcap_s1_46_0001 wait --json > w.json
st=$(chemlab run azenta_intellixcap_s1_46_0001 result --json | jq -r '.record["cycle.status"]')
if [ "$st" != "succeeded" ]; then echo "FAILED cycle status: $st" > decap.txt; fi
cat decap.txt'''

GOLD_LW05 = r'''chemlab run ziath_dp5_mirage_2023 power on
chemlab run ziath_dp5_mirage_2023 configure matching=on
chemlab run ziath_dp5_mirage_2023 start
chemlab run ziath_dp5_mirage_2023 wait
chemlab run ziath_dp5_mirage_2023 result --format json --json | jq -r '.record.content.mismatch_wells[], .record.content.missing_wells[]' > problems.txt
cat problems.txt'''

GOLD_LW06 = r'''chemlab run ziath_dp5_mirage_2023 start --json > s.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "RACK_RAISED"' s.json >/dev/null; then
  jq -r .error.fix s.json
  chemlab run ziath_dp5_mirage_2023 close rack
  chemlab run ziath_dp5_mirage_2023 start
fi
chemlab run ziath_dp5_mirage_2023 wait
chemlab run ziath_dp5_mirage_2023 result --format csv --json | jq -r .record.content > rack.csv
head -3 rack.csv'''

GOLD_LW07 = r'''chemlab run brooks_preciseflex_400 power on
chemlab help brooks_preciseflex_400 pick
chemlab run brooks_preciseflex_400 pick plate_1
chemlab run brooks_preciseflex_400 place sealer_nest
chemlab run brooks_preciseflex_400 move home'''

GOLD_LW08 = r'''chemlab run brooks_preciseflex_400 pick plate_1 --json > p.json; rc=$?
if [ $rc -eq 3 ] && jq -e '.error.code == "HOLDING"' p.json >/dev/null; then jq -r .error.message p.json; fi
chemlab run brooks_preciseflex_400 place sealer_nest'''


def cfg(c):
    return W.config(c)


def cfg_mismatch(c):
    return W.mismatch_config(c)


def cfg_stuck(c):
    return W.stuck_cap_config(c)


TASKS = [
    {'id': 'lw01_seal_with_protocol', 'kind': 'settings + wait for temperature + job', 'instruments': [A4S], 'config': cfg, 'check': c_sealed_protocol,
     'instruction': 'Seal the PCR plate on the a4S heat sealer (azenta_automated_roll_heat_sealer_a4s) with its stored protocol 4ti-0520.',
     'gold': GOLD_LW01},
    {'id': 'lw02_recover_not_hot', 'kind': 'recovery: block not at temperature', 'instruments': [A4S], 'config': cfg, 'check': c_sealed_manual,
     'instruction': 'Seal the PCR plate on the a4S heat sealer (azenta_automated_roll_heat_sealer_a4s) at 160 °C for 2.5 s.', 'gold': GOLD_LW02},
    {'id': 'lw03_decap_tube', 'kind': 'settings + cycle job + record', 'instruments': [IXC], 'config': cfg, 'check': c_decap,
     'instruction': 'On the IntelliXcap decapper (azenta_intellixcap_s1_46_0001), take the cap off tube t1 (tube type tube_15ml) and release '
                    'the cap; write the status of the cycle into cycle.txt.', 'gold': GOLD_LW03},
    {'id': 'lw04_recover_stuck_cap', 'kind': 'recovery: a failed cycle is detected and reported', 'instruments': [IXC], 'config': cfg_stuck,
     'check': c_stuck, 'instruction': 'Decap tube t1 (tube type tube_15ml) on the IntelliXcap (azenta_intellixcap_s1_46_0001). If the cap '
                                       'does not come off, write FAILED and the cycle status into decap.txt.', 'gold': GOLD_LW04},
    {'id': 'lw05_rack_audit', 'kind': 'scan job + record analysis', 'instruments': [DP5], 'config': cfg_mismatch, 'check': c_rack_audit,
     'instruction': 'Check the rack on the DP5 Mirage reader (ziath_dp5_mirage_2023) against its task manifest: scan it with identity '
                    'matching on and write every well whose tube does not match or could not be read into problems.txt, one per line.',
     'gold': GOLD_LW05},
    {'id': 'lw06_recover_rack_raised', 'kind': 'recovery: rack lifted off the reader', 'instruments': [DP5], 'config': cfg, 'world': w_rack_lifted,
     'check': c_rack_csv, 'instruction': 'Scan the rack on the DP5 Mirage reader (ziath_dp5_mirage_2023) and save its rack map as CSV in rack.csv.',
     'gold': GOLD_LW06},
    {'id': 'lw07_arm_transfer', 'kind': 'named places: pick, place, move', 'instruments': [ARM], 'config': cfg, 'check': c_transfer,
     'instruction': 'With the PreciseFlex 400 arm (brooks_preciseflex_400), move plate_1 onto the sealer nest (sealer_nest), then send the '
                    'arm home.', 'gold': GOLD_LW07},
    {'id': 'lw08_recover_already_holding', 'kind': 'recovery: the arm already holds the plate', 'instruments': [ARM], 'config': cfg,
     'world': w_arm_holding, 'check': c_holding,
     'instruction': 'Pick up plate_1 with the PreciseFlex 400 arm (brooks_preciseflex_400) and put it on the sealer nest (sealer_nest).',
     'gold': GOLD_LW08},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'labware_automation'


def main(only=None, noact=True):
    return run_suite('labware_automation', TASKS, only=only, noact=noact)

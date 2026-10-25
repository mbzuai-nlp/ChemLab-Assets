"""Dosing class acceptance tasks (Quantos Q2 + XPE206DR in the dosing world; gold with `chemlab run`, NoAct must fail)."""
import json

from .common import run_suite
from .dosing_world import config as world_config

Q = 'mt_quantos_q2_xpe206dr'


def cfg_plus(*steps):
    """The dosing world, then extra lab set-up steps on the Quantos (what the previous user left behind)."""
    def make(c):
        c = world_config(c)
        for e in c['instruments']:
            if e['id'] == Q:
                e['setup'] = list(e['setup']) + list(steps)
        return c
    return make


LEFT_RUNNING = [{'capability': 'head.select', 'arguments': {'head_id': 'head_1'}},
                {'capability': 'container.select_position', 'arguments': {'position_id': 'balance_pan'}},
                {'capability': 'balance.tare', 'arguments': {}},
                {'capability': 'dosing.set_target', 'arguments': {'target_mass_kg': 3e-05}},
                {'capability': 'dosing.set_tolerance_percent', 'arguments': {'percent': 1.0}},
                {'capability': 'dosing.start', 'arguments': {}}]


def dosed_ok(target_kg, rel=0.011):
    def check(c):
        e = c.ev(Q)
        c.equal('the last dosing succeeded', e.get('dosing.status'), 'succeeded')
        c.near('the vial got the target (truth, ±1 %)', e.get('truth.actual_dosed_kg'), target_kg, rel * target_kg)
        c.near('the target was %g mg' % (target_kg * 1e6), e.get('dosing.target_mass_kg'), target_kg, 1e-12)
    return check


def c_dose_report(c):
    dosed_ok(2e-05)(c)
    c.near('dosed.txt = the dosed quantity in mg (±0.02 mg)', c.number_file('dosed.txt'), (c.ev(Q).get('truth.actual_dosed_kg') or 0) * 1e6, 0.02)


def c_tolerance_sample(c):
    e = c.ev(Q)
    dosed_ok(5e-06)(c)
    c.near('absolute tolerance 0.1 mg', e.get('dosing.tolerance_kg'), 1e-07, 1e-13)
    c.equal('sample id S-17 on the record', e.get('dosing.sample_id'), 'S-17')
    t = c.work_file('dose.json') or ''
    try:
        rec = json.loads(t)
    except ValueError:
        rec = {}
    c.add('dose.json holds the dosing result', isinstance(rec.get('dosed_kg'), (int, float)) and rec.get('within_tolerance') is True, t[:120])


def c_aborted(c):
    e = c.ev(Q)
    c.equal('the dosing was aborted', e.get('dosing.status'), 'cancelled')
    act = e.get('truth.actual_dosed_kg') or 0
    c.add('it ran about a minute before the stop (15-60 mg dosed at 0.5 mg/s)', 1.5e-05 <= act <= 6e-05, act)
    t = c.work_file('abort.json') or ''
    c.add('abort.json saved with the partial result', '"status": "cancelled"' in t or '"cancelled"' in t, t[:120])


def c_busy_recovered(c):
    e = c.ev(Q)
    dosed_ok(1e-05)(c)
    c.near('the other user\'s 30 mg dosing ran to its end first (vial holds 30 + 10 mg)', e.get('truth.container_mass_kg'), 4e-05, 4e-07)


def c_substance(c):
    c.add('the Quantos was queried (request log)', c.looked(Q), len(c.looked(Q)))
    c.equal('substance.txt = the substance in the head RFID', (c.work_file('substance.txt') or '').strip(), 'Glucose')


GOLD_DOSE = r'''chemlab run mt_quantos_q2_xpe206dr dose 20mg --tolerance 1% --wait --json > d.json
jq -r '.result.dosed_mg' d.json > dosed.txt
cat dosed.txt'''

GOLD_OFF = r'''chemlab run mt_quantos_q2_xpe206dr dose 10mg --wait --json > d.json; rc=$?
jq -r '.error.code // .status' d.json
if [ $rc -eq 3 ] && [ "$(jq -r .error.code d.json)" = "POWER_OFF" ]; then
  chemlab run mt_quantos_q2_xpe206dr power on
  chemlab run mt_quantos_q2_xpe206dr dose 10mg --wait
fi'''

GOLD_TOL = r'''chemlab run mt_quantos_q2_xpe206dr dose 5mg --tolerance 0.1mg --sample-id S-17 --wait
chemlab run mt_quantos_q2_xpe206dr result --save-to dose.json'''

GOLD_ABORT = r'''job=$(chemlab run mt_quantos_q2_xpe206dr dose 500mg --json | jq -r .job)
chemlab wait 60
chemlab run mt_quantos_q2_xpe206dr stop
chemlab run mt_quantos_q2_xpe206dr result --save-to abort.json'''

GOLD_BUSY = r'''chemlab run mt_quantos_q2_xpe206dr dose 10mg --wait --json > d.json; rc=$?
jq -r '.error.code // .status' d.json
if [ $rc -eq 3 ] && [ "$(jq -r .error.code d.json)" = "BUSY" ]; then
  for i in 1 2 3 4 5; do
    [ "$(chemlab run mt_quantos_q2_xpe206dr status --json | jq -r .state.status)" = "running" ] || break
    chemlab wait 30
  done
  chemlab run mt_quantos_q2_xpe206dr dose 10mg --wait
fi'''

GOLD_HEAD = r'''chemlab run mt_quantos_q2_xpe206dr head --json | jq -r .data.substance > substance.txt
cat substance.txt'''

TASKS = [
    {'id': 'd01_dose_20mg', 'kind': 'one composite verb + job + report', 'instruments': [Q], 'config': cfg_plus(), 'check': c_dose_report,
     'instruction': 'Dose 20 mg of the powder in the Quantos dosing system (mt_quantos_q2_xpe206dr) into the vial on its balance, with a '
                    'tolerance of 1 %. Wait until the dosing is done and write the dosed quantity in mg (a plain number) into dosed.txt.',
     'gold': GOLD_DOSE},
    {'id': 'd02_recover_power_off', 'kind': 'recovery: instrument switched off', 'instruments': [Q],
     'config': cfg_plus({'capability': 'power.set', 'arguments': {'enabled': False}}), 'check': dosed_ok(1e-05),
     'instruction': 'Dose 10 mg of powder into the vial of the Quantos dosing system (mt_quantos_q2_xpe206dr) and wait until it is done.',
     'gold': GOLD_OFF},
    {'id': 'd03_tolerance_sample_id', 'kind': 'options (absolute tolerance, sample id) + saved record', 'instruments': [Q],
     'config': cfg_plus(), 'check': c_tolerance_sample,
     'instruction': 'On the Quantos dosing system (mt_quantos_q2_xpe206dr), dose 5 mg into the vial with an absolute tolerance of 0.1 mg '
                    'and sample id S-17, wait for it, and save the dosing result into dose.json in your work directory.',
     'gold': GOLD_TOL},
    {'id': 'd04_abort_after_a_minute', 'kind': 'long action (job) + stop + partial result', 'instruments': [Q], 'config': cfg_plus(),
     'check': c_aborted,
     'instruction': 'Start dosing 500 mg into the vial of the Quantos dosing system (mt_quantos_q2_xpe206dr), let it run for about one '
                    'minute, then abort it and save the (partial) dosing result into abort.json.',
     'gold': GOLD_ABORT},
    {'id': 'd05_recover_left_running', 'kind': 'recovery: another user\'s dosing is running (BUSY, wait)', 'instruments': [Q],
     'config': cfg_plus(*LEFT_RUNNING), 'check': c_busy_recovered,
     'instruction': 'Another user is dosing on the Quantos dosing system (mt_quantos_q2_xpe206dr). When their dosing has finished, dose '
                    '10 mg into the same vial (on top of their powder) and wait until it is done.',
     'gold': GOLD_BUSY},
    {'id': 'd06_head_substance', 'kind': 'object query (dosing head RFID data)', 'instruments': [Q],
     'config': cfg_plus({'capability': 'head.write_data', 'arguments': {'data': {'substance': 'Glucose', 'lot_id': 'L-1'}}}),
     'check': c_substance,
     'instruction': 'Which substance does the dosing head in the Quantos dosing system (mt_quantos_q2_xpe206dr) hold? Write its name '
                    '(as stored in the head) into substance.txt.',
     'gold': GOLD_HEAD},
]
BY_ID = {t['id']: t for t in TASKS}
for _t in TASKS:
    _t['class'] = 'dosing'


def main(only=None, noact=True):
    return run_suite('dosing', TASKS, only=only, noact=noact)

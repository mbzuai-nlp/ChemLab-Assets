"""Liquid class acceptance: the pilot's 10 Flex tasks (chemlab/flex_tasks.py) in the generic lab, through the compile layer.

Same decks, liquids, faults, protocols, instructions and checkers as the pilot; the Flex is the lab instrument "flex" with a deck
registry and the Flex scene in its lab-config entry.  The gold solutions are the pilot's with two changes the generic lab needs:
`chemlab status flex` is `chemlab run flex status`, and waiting uses `chemlab wait` (lab time moves only with wait) instead of
sleeping on a wall-clock simulation.
"""
import copy

from ... import flex_tasks as P
from ...backend import _load_runtime_module
from ...labbackend import load_config
from .common import run_suite

FLEX_SLUG = 'opentrons_flex_single_5_1000ul'
SCENE = 'chemlab.compile.scenes.flex:FlexScene'


def flex_entry(contents, power=True, protocols=(), faults=None, iid='flex'):
    """A lab-config entry for a Flex with a deck (the trusted host writes it; home and command time from the runtime)."""
    m = _load_runtime_module()
    base = next(e for e in load_config()['instruments'] if e['id'] == FLEX_SLUG)
    deck = P.deck(contents, power=power)
    deck.pop('power', None)
    deck['home_m'] = dict(m.FLEX_HOME_POSITION)
    deck['command_s'] = m.FLEX_COMMAND_S
    e = {k: copy.deepcopy(v) for k, v in base.items() if k not in ('setup', 'setup_source', 'panel')}
    e.update({'id': iid, 'scene': SCENE, 'deck': deck, 'protocols': copy.deepcopy(list(protocols)), 'faults': dict(faults or {}),
              'setup': [{'capability': 'power.set', 'arguments': {'enabled': True}}] if power else []})
    return e


def task_config(ptask):
    def cfg():
        c = load_config()
        c['instruments'] = [flex_entry(ptask['contents'], ptask.get('power', True), ptask.get('protocols', ()), ptask.get('faults'))]
        return c
    return cfg


def colleague_world(ptask):
    def world(lab):
        if ptask.get('colleague_run'):
            lab.backend.instruments['flex'].scene.start_run_as('colleague', ptask['colleague_run'])
    return world


def checker(ptask):
    def check(c):
        scene = c.lab.backend.instruments['flex'].scene
        pc = P.Check(ptask, scene.evaluator_state(), scene)
        ptask['check'](pc)
        c.items.extend(pc.items)
    return check


GOLD = {t['id']: t['gold'] for t in P.TASKS}
GOLD['t01_power_home_lights'] = ('chemlab run flex power on && chemlab run flex home && chemlab run flex lights on && '
                                 'chemlab run flex status')
GOLD['t09_busy_wait'] = r'''chemlab run flex status
for i in $(seq 1 40); do
  out=$(chemlab run flex transfer --volume 100 --from reservoir:A1 --to plate:H1 --json); rc=$?
  [ $rc -eq 0 ] && { echo "$out" | jq -c .transfers; break; }
  echo "$out" | jq -e '.error.code == "BUSY"' >/dev/null || { echo "$out"; exit 1; }
  echo "busy: $(echo "$out" | jq -r .error.message)"
  chemlab wait 30
done'''
GOLD['t10_protocol_then_transfer'] = r'''job=$(chemlab run flex run-protocol plate_prep --param volume_ul=80 --json | jq -r .job)
echo "started $job"
for i in $(seq 1 60); do
  st=$(chemlab status "$job" --json | jq -r .status)
  [ "$st" = succeeded ] && break
  case "$st" in running|paused|setup) ;; *) echo "job ended as $st"; exit 1;; esac
  chemlab status "$job"
  chemlab wait 30
done
chemlab status "$job"
chemlab run flex transfer --volume 20 --from tuberack:A1 --to plate:C1'''

TASKS = []
for _p in P.TASKS:
    TASKS.append({'id': _p['id'], 'kind': _p['kind'], 'class': 'liquid', 'instruments': ['flex'], 'lab_config': task_config(_p),
                  'world': colleague_world(_p), 'instruction': _p['instruction'], 'gold': GOLD[_p['id']], 'check': checker(_p)})

# ------------------------------------------------------------------ NE-1000 syringe pump (its own adapter, classes/syringe_pump.py)
NE = 'newera_ne1000'


def _ne_cfg(*extra):
    from .syringe_pump_world import config as syringe_world

    def make(c):
        c = syringe_world(c)
        for e in c['instruments']:
            if e['id'] == NE:
                e['setup'] = list(e['setup']) + list(extra)
        return c
    return make


def _disp(c):
    return c.ev(NE).get('volume.dispensed') or {}


def c_ne_infuse(c):
    e = c.ev(NE)
    c.near('syringe diameter set to the B-D 10 mL (14.43 mm)', e.get('syringe.diameter_m'), 0.01443, 1e-6)
    c.near('0.5 mL infused', _disp(c).get('infused_m3'), 5e-07, 5e-09)
    c.equal('pump stopped at the end', e.get('run.state'), 'stopped')
    c.near('infused.txt = 0.5 (mL)', c.number_file('infused.txt'), 0.5, 0.005)


def c_ne_withdraw(c):
    e = c.ev(NE)
    c.near('syringe diameter set (14.43 mm)', e.get('syringe.diameter_m'), 0.01443, 1e-6)
    c.near('0.2 mL withdrawn', _disp(c).get('withdrawn_m3'), 2e-07, 2e-09)
    ph = ((e.get('program.state') or {}).get('phases') or [None])[0] or {}
    c.near('at 2 mL/min', ph.get('rate_m3_s'), 2e-6 / 60, 1e-12)


def c_ne_program(c):
    e = c.ev(NE)
    phases = (e.get('program.state') or {}).get('phases') or []
    fn = [(p or {}).get('function') for p in phases[:4]]
    c.equal('program: rate, pause, rate, stop', fn, ['RAT', 'PAS', 'RAT', 'STP'])
    c.near('0.1 mL infused', _disp(c).get('infused_m3'), 1e-07, 2e-09)
    c.near('0.1 mL withdrawn', _disp(c).get('withdrawn_m3'), 1e-07, 2e-09)
    c.equal('program ran to its end', e.get('run.state'), 'stopped')


def c_ne_left_running(c):
    e = c.ev(NE)
    c.equal('pump stopped at the end', e.get('run.state'), 'stopped')
    c.near('volume target 0.3 mL', (((e.get('program.state') or {}).get('phases') or [{}])[0] or {}).get('volume_m3'), 3e-07, 1e-12)
    c.near('0.3 mL infused after the colleague\'s 0.2 mL (counters cleared first)', _disp(c).get('infused_m3'), 3e-07, 3e-09)
    c.near('the colleague\'s 0.2 mL was not cut short (0.5 mL delivered in all)', ((e.get('truth.process') or {}).get('delivered_m3')),
           5e-07, 2e-08)


NE_LEFT = [{'capability': 'syringe.set_diameter', 'arguments': {'diameter_m': 0.01443}},
           {'capability': 'rate.set', 'arguments': {'rate_m3_s': 1e-06 / 60, 'units': 'MM'}},
           {'capability': 'volume.set', 'arguments': {'volume_m3': 2e-07}},
           {'capability': 'pump.run', 'arguments': {}}]

GOLD_NE_INFUSE = r'''chemlab run newera_ne1000 syringe loaded
chemlab run newera_ne1000 rate 1mL/min
chemlab run newera_ne1000 volume 0.5mL
chemlab run newera_ne1000 direction infuse
chemlab run newera_ne1000 run --wait --json | jq -r '.dispensed.infused_m3 * 1e6' > infused.txt
cat infused.txt'''

GOLD_NE_WITHDRAW = r'''chemlab run newera_ne1000 rate 2mL/min --json > r.json; rc=$?
jq -r '.error.code // "ok"' r.json
if [ $rc -eq 3 ] && [ "$(jq -r .error.code r.json)" = "DIAMETER_NOT_SET" ]; then
  chemlab run newera_ne1000 syringe loaded
  chemlab run newera_ne1000 rate 2mL/min
fi
chemlab run newera_ne1000 volume 0.2mL
chemlab run newera_ne1000 direction withdraw
chemlab run newera_ne1000 run --wait'''

GOLD_NE_PROGRAM = r'''chemlab run newera_ne1000 syringe loaded
chemlab run newera_ne1000 phase 1 rate rate=2mL/min volume=0.1mL direction=infuse
chemlab run newera_ne1000 phase 2 pause param=5
chemlab run newera_ne1000 phase 3 rate rate=2mL/min volume=0.1mL direction=withdraw
chemlab run newera_ne1000 phase 4 stop
chemlab run newera_ne1000 run --phase 1 --wait'''

GOLD_NE_LEFT = r'''chemlab run newera_ne1000 volume 0.3mL --json > v.json; rc=$?
jq -r '.error.code // "ok"' v.json
if [ $rc -eq 3 ] && [ "$(jq -r .error.code v.json)" = "BUSY" ]; then
  for i in 1 2 3 4 5 6; do
    [ "$(chemlab run newera_ne1000 status --json | jq -r .state.run_state)" = "running" ] || break
    chemlab wait 10
  done
  chemlab run newera_ne1000 volume 0.3mL
fi
chemlab run newera_ne1000 volume clear
chemlab run newera_ne1000 direction infuse
chemlab run newera_ne1000 run --wait'''

TASKS += [
    {'id': 'l11_ne1000_infuse', 'kind': 'syringe pump: set-up + job + report', 'class': 'liquid', 'instruments': [NE], 'config': _ne_cfg(),
     'check': c_ne_infuse, 'gold': GOLD_NE_INFUSE,
     'instruction': 'The NE-1000 syringe pump (newera_ne1000) holds a B-D 10 mL syringe. Infuse 0.5 mL at 1 mL/min, wait until it is done, '
                    'and write the infused volume in mL (a plain number) into infused.txt.'},
    {'id': 'l12_ne1000_recover_diameter', 'kind': 'recovery: syringe diameter not set', 'class': 'liquid', 'instruments': [NE],
     'config': _ne_cfg(), 'check': c_ne_withdraw, 'gold': GOLD_NE_WITHDRAW,
     'instruction': 'With the NE-1000 syringe pump (newera_ne1000), withdraw 0.2 mL from the reservoir at 2 mL/min and wait until it is done.'},
    {'id': 'l13_ne1000_program', 'kind': 'multi-phase program + job', 'class': 'liquid', 'instruments': [NE], 'config': _ne_cfg(),
     'check': c_ne_program, 'gold': GOLD_NE_PROGRAM,
     'instruction': 'Program the NE-1000 syringe pump (newera_ne1000, B-D 10 mL syringe): phase 1 infuse 0.1 mL at 2 mL/min, phase 2 pause '
                    '5 s, phase 3 withdraw 0.1 mL at 2 mL/min, phase 4 stop. Run the program from phase 1 and wait until it ends.'},
    {'id': 'l14_ne1000_recover_left_running', 'kind': 'recovery: another user\'s run (BUSY, wait)', 'class': 'liquid', 'instruments': [NE],
     'config': _ne_cfg(*NE_LEFT), 'check': c_ne_left_running, 'gold': GOLD_NE_LEFT,
     'instruction': 'A colleague is dispensing with the NE-1000 syringe pump (newera_ne1000). When their run has finished, clear the '
                    'dispensed volume counters and infuse exactly 0.3 mL at the rate already set; wait until it is done.'},
]
BY_ID = {t['id']: t for t in TASKS}


def main(only=None, noact=True):
    return run_suite('liquid', TASKS, only=only, noact=noact)

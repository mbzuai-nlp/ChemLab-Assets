"""Opentrons Flex task suite: 10 tasks with known answers, checkers on the evaluator view, and gold solutions in chemlab.

Kept as a regression and acceptance suite: every gold solution runs through the chemlab client inside the sandbox and every
checker must pass; an empty solution (NoAct) must fail every checker.

Checkers read only the evaluator view (truth.* slots, run history, tip events) and the task's own liquid map.  Liquid
composition is replayed from the transfer ledger with ideal mixing (the twin has no mixing model).
"""
import json
import time
from pathlib import Path

UL = 1e-9
TOL_UL = 1e-3

STANDARD_LABWARE = [
    {'name': 'tiprack1000', 'type': 'opentrons_flex_96_tiprack_1000ul', 'slot': 'B2'},
    {'name': 'tiprack200', 'type': 'opentrons_flex_96_tiprack_200ul', 'slot': 'C3'},
    {'name': 'reservoir', 'type': 'nest_12_reservoir_15ml', 'slot': 'B3'},
    {'name': 'plate', 'type': 'corning_96_wellplate_360ul_flat', 'slot': 'C2'},
    {'name': 'tuberack', 'type': 'opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap', 'slot': 'D1'},
]


def deck(contents, power=True):
    """contents: {'reservoir:A1': ('buffer', 10000), ...} (uL)."""
    labware = []
    for lw in STANDARD_LABWARE:
        lw = dict(lw)
        vols = {loc.split(':')[1]: v for loc, (_, v) in contents.items() if loc.split(':')[0] == lw['name']}
        if vols:
            lw['volumes_ul'] = vols
        labware.append(lw)
    return {'labware': labware, 'trash': 'A1', 'power': power}


def cid(loc):
    return loc.replace(':', '_')


# ------------------------------------------------------------------ checker helpers
class Check:
    def __init__(self, task, ev, backend):
        self.task, self.ev, self.backend = task, ev, backend
        self.items = []
        self.vol = {c['container_id']: c['volume_m3'] for c in ev['truth.containers']}
        self.comp = replay(task['contents'], ev['truth.transfers'])

    def add(self, name, ok, detail=''):
        self.items.append({'check': name, 'ok': bool(ok), 'detail': detail})
        return ok

    def volume(self, loc, want_ul):
        got = self.vol[cid(loc)] / UL
        return self.add('%s holds %g uL' % (loc, want_ul), abs(got - want_ul) <= TOL_UL, 'got %.4f uL' % got)

    def fraction(self, loc, liquid, want, tol=1e-6):
        c = self.comp.get(cid(loc), {})
        total = sum(c.values())
        got = c.get(liquid, 0.0) / total if total else 0.0
        return self.add('%s is %.4g %s' % (loc, want, liquid), abs(got - want) <= tol, 'got %.6f' % got)

    def unchanged_except(self, locs):
        allowed = {cid(l) for l in locs}
        initial = {cid(l): v * UL for l, (_, v) in self.task['contents'].items()}
        bad = [k for k, v in self.vol.items() if k not in allowed and abs(v - initial.get(k, 0.0)) > TOL_UL * UL]
        return self.add('no other well changed', not bad, ', '.join('%s=%.3f uL' % (k, self.vol[k] / UL) for k in bad))

    def no_tip_attached(self):
        return self.add('no tip left on the pipette', self.ev['tip.attached_id'] is None, str(self.ev['tip.attached_id']))

    def agent_tips(self):
        """Tips attached by the agent's own commands (excludes tips used by protocol runs)."""
        run_tips = set(self.task.get('run_tips', []))
        return [e['tip_id'] for e in self.ev['tip.events'] if e['event'] == 'attach' and e['tip_id'] not in run_tips]

    def tips_trashed(self, exclude=()):
        last = {}
        for e in self.ev['tip.events']:
            last[e['tip_id']] = e
        bad = [t for t, e in last.items() if t not in exclude and (e['event'] != 'release' or e['slot_id'] != 'A1')]
        return self.add('used tips are in the trash', not bad, ', '.join(bad))

    def tips_count(self, n, prefix=None):
        tips = self.agent_tips()
        ok = len(tips) == n and (prefix is None or all(t.startswith(prefix) for t in tips))
        return self.add('%d tip(s)%s used' % (n, ' from %s' % prefix if prefix else ''), ok, ', '.join(tips))

    def result(self):
        return {'task': self.task['id'], 'passed': all(i['ok'] for i in self.items), 'checks': self.items}


def replay(contents, transfers):
    """Composition of every container and tip after the transfer ledger, with ideal mixing."""
    comp = {cid(l): {liquid: v * UL} for l, (liquid, v) in contents.items()}
    for t in transfers:
        donor = comp.setdefault(t['donor_id'], {})
        recv = comp.setdefault(t['receiver_id'], {})
        total = sum(donor.values())
        if total <= 0:
            continue
        for liquid in list(donor):
            share = donor[liquid] / total * t['volume_m3']
            donor[liquid] -= share
            recv[liquid] = recv.get(liquid, 0.0) + share
    return comp


def run_record(ev, protocol_id):
    return [h for h in ev['run.history'] if h['protocol_id'] == protocol_id]


# ------------------------------------------------------------------ protocols stored on the robot
def _proto_fill(pid, name, tip, source, wells, volume, delay_s, params=False):
    v = {'param': 'volume_ul', 'scale': UL} if params else volume * UL
    cmds = [{'cmd': 'pick_up_tip', 'tip_id': tip}]
    for w in wells:
        cmds += [{'cmd': 'aspirate', 'container_id': source, 'volume_m3': v}, {'cmd': 'dispense', 'container_id': w, 'volume_m3': v}]
    cmds += [{'cmd': 'drop_tip', 'slot_id': 'A1'}, {'cmd': 'delay', 's': float(delay_s), 'text': 'incubate'}]
    p = {'protocol_id': pid, 'name': name, 'fixtures': {'A1': 'trash_bin'}, 'commands': cmds,
         'description': 'fill %d wells, then incubate %d s' % (len(wells), delay_s)}
    if params:
        p['parameters'] = [{'name': 'volume_ul', 'type': 'int', 'default': 50, 'min': 10, 'max': 200, 'display_name': 'Volume per well (uL)'}]
    return p


# ------------------------------------------------------------------ the tasks
def _t01(c):
    ev = c.ev
    c.add('power on', ev['power.enabled'])
    home = c.backend.registry()['home_m']
    c.add('gantry at home', all(abs(ev['motion.' + k] - home[k]) < 1e-9 for k in home), str({k: ev['motion.' + k] for k in home}))
    c.add('deck lights on', ev['robot.lights_on'])
    c.add('no liquid moved', not ev['truth.transfers'])


def _t02(c):
    c.volume('plate:A1', 150)
    c.volume('reservoir:A1', 9850)
    c.fraction('plate:A1', 'buffer', 1.0)
    c.unchanged_except(['plate:A1', 'reservoir:A1'])
    c.tips_count(1)
    c.tips_trashed()
    c.no_tip_attached()


def _t03(c):
    c.volume('plate:B1', 7.5)
    c.volume('tuberack:A1', 992.5)
    c.fraction('plate:B1', 'dye', 1.0)
    c.unchanged_except(['plate:B1', 'tuberack:A1'])
    c.tips_count(1)
    c.tips_trashed()
    c.no_tip_attached()


def _t04(c):
    for w in ('A1', 'A2', 'A3', 'A4', 'A5', 'A6'):
        c.volume('plate:' + w, 50)
    c.volume('reservoir:A1', 10000 - 300)
    c.unchanged_except(['plate:A%d' % i for i in range(1, 7)] + ['reservoir:A1'])
    c.tips_count(1)
    c.tips_trashed()
    c.no_tip_attached()


def _t05(c):
    for w, v in (('A1', 100), ('A2', 100), ('A3', 100), ('A4', 200)):
        c.volume('plate:' + w, v)
    for w, f in (('A2', 0.5), ('A3', 0.25), ('A4', 0.125)):
        c.fraction('plate:' + w, 'stock', f)
    c.unchanged_except(['plate:A1', 'plate:A2', 'plate:A3', 'plate:A4'])
    c.tips_count(3)
    sources = {}
    for t in c.ev['truth.transfers']:
        if t['kind'] == 'aspirate':
            sources.setdefault(t['receiver_id'], set()).add(t['donor_id'])
    c.add('each tip drew from one well only (no carry-over)', all(len(s) == 1 for s in sources.values()), str({k: sorted(v) for k, v in sources.items()}))
    c.tips_trashed()
    c.no_tip_attached()


def _t06(c):
    c.volume('tuberack:A2', 350)
    c.volume('reservoir:A1', 10000 - 350)
    c.unchanged_except(['tuberack:A2', 'reservoir:A1'])
    c.tips_count(1, prefix='tiprack200_')
    c.tips_trashed()
    c.no_tip_attached()


def _t07(c):
    c.volume('tuberack:A3', 600)
    c.fraction('tuberack:A3', 'buffer', 1.0)
    used = (400 - c.vol['reservoir_A3'] / UL) + (10000 - c.vol['reservoir_A4'] / UL)
    c.add('600 uL in total came from reservoir A3/A4', abs(used - 600) <= TOL_UL, 'used %.3f uL' % used)
    c.unchanged_except(['tuberack:A3', 'reservoir:A3', 'reservoir:A4'])
    c.add('at least one tip used', len(c.agent_tips()) >= 1, ', '.join(c.agent_tips()))
    c.tips_trashed()
    c.no_tip_attached()


def _t08(c):
    for w in ('B1', 'B2', 'B3', 'B4'):
        c.volume('plate:' + w, 100)
    c.volume('reservoir:A1', 10000 - 400)
    c.unchanged_except(['plate:B%d' % i for i in range(1, 5)] + ['reservoir:A1'])
    fallen = [e['tip_id'] for e in c.backend.fault_events]
    c.add('the fault happened (a tip came off)', bool(fallen), str(c.backend.fault_events))
    c.tips_trashed(exclude=fallen)
    c.no_tip_attached()


def _t09(c):
    runs = run_record(c.ev, 'colleague_fill')
    c.add("colleague's run completed (not aborted)", bool(runs) and runs[-1]['status'] == 'succeeded', str(runs))
    for w in ('E1', 'E2', 'E3', 'E4', 'E5', 'E6'):
        c.volume('plate:' + w, 30)
    c.volume('plate:H1', 100)
    c.fraction('plate:H1', 'buffer', 1.0)
    ended = runs[-1]['ended_s'] if runs else None
    mine = [t for t in c.ev['truth.transfers'] if t['receiver_id'] == 'plate_H1']
    c.add('your transfer came after their run', bool(mine) and ended is not None and all(t['time_s'] >= ended for t in mine),
          'run ended %s, transfer at %s' % (ended, [t['time_s'] for t in mine]))
    c.unchanged_except(['plate:E%d' % i for i in range(1, 7)] + ['plate:H1', 'reservoir:A1', 'reservoir:A2'])
    c.tips_count(1)
    c.tips_trashed()
    c.no_tip_attached()


def _t10(c):
    runs = run_record(c.ev, 'plate_prep')
    c.add('plate_prep run completed', bool(runs) and runs[-1]['status'] == 'succeeded', str(runs))
    c.volume('plate:C1', 100)
    for w in ('C2', 'C3', 'C4', 'C5', 'C6'):
        c.volume('plate:' + w, 80)
    c.fraction('plate:C1', 'dye', 0.2)
    ended = runs[-1]['ended_s'] if runs else None
    dye = [t for t in c.ev['truth.transfers'] if t['receiver_id'] == 'plate_C1' and t['kind'] == 'dispense' and t['time_s'] > (ended or 1e300) - 1e-9]
    c.add('dye added after the run had completed', bool(dye), 'run ended %s' % ended)
    c.unchanged_except(['plate:C%d' % i for i in range(1, 7)] + ['reservoir:A2', 'tuberack:A1'])
    c.tips_count(1)
    c.tips_trashed()
    c.no_tip_attached()


GOLD_T08 = r'''out=$(chemlab run flex transfer --volume 100 --from reservoir:A1 --to plate:B1-B4 --json); rc=$?
echo "$out" | jq -c '{ok, code: .error.code, changed: .error.instrument_changed}'
if [ $rc -eq 3 ] && echo "$out" | jq -e '.error.code == "NO_TIP"' >/dev/null; then
  done_wells=$(echo "$out" | jq -r '.error.done[] | select(startswith("dispense")) | sub(".* into plate:"; "")')
  rest=$(for w in B1 B2 B3 B4; do echo "$done_wells" | grep -qx "$w" || echo "plate:$w"; done | paste -sd, -)
  echo "redo: $rest"
  chemlab run flex transfer --volume 100 --from reservoir:A1 --to "$rest"
fi
chemlab run flex status'''

GOLD_T09 = r'''chemlab status flex
while true; do
  out=$(chemlab run flex transfer --volume 100 --from reservoir:A1 --to plate:H1 --json); rc=$?
  [ $rc -eq 0 ] && { echo "$out" | jq -c .transfers; break; }
  echo "$out" | jq -e '.error.code == "BUSY"' >/dev/null || { echo "$out"; exit 1; }
  echo "busy: $(echo "$out" | jq -r .error.message)"
  sleep 2
done'''

GOLD_T10 = r'''job=$(chemlab run flex run-protocol plate_prep --param volume_ul=80 --json | jq -r .job)
echo "started $job"
while :; do
  st=$(chemlab status "$job" --json | jq -r .status)
  [ "$st" = completed ] && break
  case "$st" in running|paused|setup) ;; *) echo "job ended as $st"; exit 1;; esac
  chemlab status "$job"
  sleep 3
done
chemlab status "$job"
chemlab run flex transfer --volume 20 --from tuberack:A1 --to plate:C1'''

GOLD_T06 = r'''chemlab run flex pick-up-tip --rack tiprack200
chemlab run flex aspirate --volume 350 --from reservoir:A1 --json | jq -c '{ok, code: .error.code, fix: .error.fix}'
test "${PIPESTATUS[0]}" -eq 3 || exit 1
for i in 1 2; do
  chemlab run flex aspirate --volume 175 --from reservoir:A1
  chemlab run flex dispense --volume 175 --to tuberack:A2
done
chemlab run flex drop-tip'''

GOLD_T07 = r'''chemlab run flex transfer --volume 600 --from reservoir:A3 --to tuberack:A3; rc=$?
if [ $rc -eq 3 ]; then
  # the opened bottle is short; the tip is still on: finish from the new bottle with the same tip
  chemlab run flex status
  chemlab run flex transfer --volume 600 --from reservoir:A4 --to tuberack:A3 --new-tip never
  chemlab run flex drop-tip
fi'''

TASKS = [
    {'id': 't01_power_home_lights', 'kind': 'single step', 'power': False, 'contents': {'reservoir:A1': ('buffer', 10000)},
     'instruction': 'Switch the Opentrons Flex on, home the gantry and turn the deck lights on.', 'check': _t01,
     'gold': 'chemlab run flex power on && chemlab run flex home && chemlab run flex lights on && chemlab status flex'},
    {'id': 't02_single_transfer', 'kind': 'single step', 'contents': {'reservoir:A1': ('buffer', 10000)},
     'instruction': 'Transfer 150 uL of buffer from reservoir well A1 to plate well A1 with a fresh tip, then discard the tip in the trash.',
     'check': _t02, 'gold': 'chemlab run flex transfer --volume 150uL --from reservoir:A1 --to plate:A1'},
    {'id': 't03_small_volume', 'kind': 'single step (unit trap)', 'contents': {'tuberack:A1': ('dye', 1000)},
     'instruction': 'Add 7.5 uL of dye from tube A1 of the tube rack to plate well B1. Use a fresh tip and discard it afterwards.',
     'check': _t03, 'gold': 'chemlab run flex transfer --volume 7.5uL --from tuberack:A1 --to plate:B1 --rack tiprack200 --dry-run && '
                            'chemlab run flex transfer --volume 7.5uL --from tuberack:A1 --to plate:B1 --rack tiprack200'},
    {'id': 't04_distribute_row', 'kind': 'multi-step transfer', 'contents': {'reservoir:A1': ('buffer', 10000)},
     'instruction': 'Fill plate wells A1 to A6 with 50 uL of buffer each from reservoir A1, using a single tip for the whole job; discard it at the end.',
     'check': _t04, 'gold': 'chemlab run flex transfer --volume 50 --from reservoir:A1 --to plate:A1-A6 --new-tip once'},
    {'id': 't05_serial_dilution', 'kind': 'dilution', 'contents': {'plate:A1': ('stock', 200), 'plate:A2': ('diluent', 100),
                                                                     'plate:A3': ('diluent', 100), 'plate:A4': ('diluent', 100)},
     'instruction': 'Make a 1:2 serial dilution along plate row A: move 100 uL from A1 to A2, then from A2 to A3, then from A3 to A4. '
                    'Use a new tip for every transfer and discard tips in the trash (mixing after each addition is assumed).',
     'check': _t05, 'gold': 'for p in "A1 A2" "A2 A3" "A3 A4"; do set -- $p; '
                            'chemlab run flex transfer --volume 100 --from plate:$1 --to plate:$2 --new-tip always || exit 1; done'},
    {'id': 't06_tip_capacity_recovery', 'kind': 'recovery: volume over the tip size', 'contents': {'reservoir:A1': ('buffer', 10000)},
     'instruction': 'Using only the 200 uL tips (rack tiprack200), move 350 uL of buffer from reservoir A1 into tube A2 of the tube rack. '
                    'Use one tip and discard it.', 'check': _t06, 'gold': GOLD_T06},
    {'id': 't07_empty_source_recovery', 'kind': 'recovery: empty source', 'contents': {'reservoir:A3': ('buffer', 400), 'reservoir:A4': ('buffer', 10000)},
     'instruction': 'Prepare 600 uL of buffer in tube A3 of the tube rack. Take it from reservoir A3 (an opened bottle); if A3 runs short, '
                    'use reservoir A4 (a new bottle of the same buffer) for the rest. Discard tips in the trash.', 'check': _t07, 'gold': GOLD_T07},
    {'id': 't08_tip_lost_recovery', 'kind': 'recovery: no tip (tip falls off)', 'contents': {'reservoir:A1': ('buffer', 10000)},
     'faults': {'tip_off_after_dispenses': 2},
     'instruction': 'Dispense 100 uL of buffer from reservoir A1 into plate wells B1, B2, B3 and B4, one tip for the job; discard tips in the trash.',
     'check': _t08, 'gold': GOLD_T08},
    {'id': 't09_busy_wait', 'kind': 'recovery: instrument busy (another user)', 'contents': {'reservoir:A1': ('buffer', 10000), 'reservoir:A2': ('diluent', 10000)},
     'protocols': [_proto_fill('colleague_fill', 'Colleague plate fill', 'tiprack200_H12', 'reservoir_A2', ['plate_E%d' % i for i in range(1, 7)], 30, 120)],
     'colleague_run': 'colleague_fill', 'run_tips': ['tiprack200_H12'],
     'instruction': "A colleague's protocol run is using the Flex right now. As soon as the robot is free, transfer 100 uL of buffer from reservoir A1 "
                    "to plate well H1 with a fresh tip and discard it. Do not interrupt the colleague's run.", 'check': _t09, 'gold': GOLD_T09},
    {'id': 't10_protocol_then_transfer', 'kind': 'long action (job polling)', 'contents': {'reservoir:A2': ('diluent', 10000), 'tuberack:A1': ('dye', 1000)},
     'protocols': [_proto_fill('plate_prep', 'Plate prep', 'tiprack1000_H12', 'reservoir_A2', ['plate_C%d' % i for i in range(1, 7)], None, 300, params=True)],
     'run_tips': ['tiprack1000_H12'],
     'instruction': 'Run the stored protocol plate_prep with volume_ul = 80 (it ends with a 5-minute incubation). When the run has completed, '
                    'add 20 uL of dye from tube A1 of the tube rack to plate well C1 with a fresh tip and discard it.', 'check': _t10, 'gold': GOLD_T10},
]
BY_ID = {t['id']: t for t in TASKS}


def make_lab(task, run_dir, speed=20.0):
    from .lab import Lab
    lab = Lab(deck(task['contents'], power=task.get('power', True)), run_dir, faults=task.get('faults'), protocols=task.get('protocols', ()),
              speed=speed, callers=('agent',))
    if task.get('colleague_run'):
        lab.backend.start_run_as('colleague', task['colleague_run'])
    return lab


def check(task, lab):
    c = Check(task, lab.backend.evaluator_state(), lab.backend)
    task['check'](c)
    return c.result()


def run_task(task, out_dir, solution=None, speed=20.0, timeout=300):
    """Run one solution (default: the gold) through the sandboxed chemlab client; return the check result."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale in ('core_log.jsonl', 'transcript.json', 'check.json'):
        if (out_dir / stale).exists():
            (out_dir / stale).unlink()
    lab = make_lab(task, out_dir, speed=speed).start()
    t0 = time.monotonic()
    try:
        script = task['gold'] if solution is None else solution
        r = lab.sh(script, timeout=timeout) if script else None
        if task.get('colleague_run'):                                       # let a colleague's run finish before judging
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and any(
                    (lab.backend.evaluator_state().get('run.state') or {}).get('status') == s for s in ('running', 'paused')):
                time.sleep(0.2)
    finally:
        lab.stop()
    result = check(task, lab)
    result.update({'wall_s': round(time.monotonic() - t0, 2), 'sim_time_s': round(lab.backend.now(), 1),
                   'exit_code': None if r is None else r.exit_code, 'timed_out': bool(r and r.timed_out),
                   'requests': len(lab.core.log.entries),
                   'rejected': sum(1 for e in lab.core.log.entries if e['exit'] != 0),
                   'backend_calls': sum(len(e['backend_calls']) for e in lab.core.log.entries)})
    (out_dir / 'transcript.json').write_text(json.dumps({'task': task['id'], 'instruction': task['instruction'], 'solution': script,
                                                         'shell': lab.transcript}, ensure_ascii=False, indent=1))
    (out_dir / 'check.json').write_text(json.dumps(result, ensure_ascii=False, indent=1))
    return result

"""Layer 1 - backend: the lab protocol 1.0.0 (describe / read / invoke / write / poll) plus simulated time.

The core service (layer 2) talks only to the five protocol calls and the clock.  Everything else in this module is the
trusted host side of the simulation and is never reachable from the agent: the deck registry (labware, wells, tips and
the joint targets the runtime needs), the scene that confirms tip pick-up and drop (the runtime asks a trusted host to
confirm each tip event at the current joint position; on a real robot or in Isaac the physics does this), fault
injection for recovery tasks, other users' runs, and the evaluator view used by the task checkers.

This backend wraps the existing Chemistry-Sim runtime (scripts/basic40_runtime_automation.py, OpentronsFlexRuntime) and
does not change it.
"""
import copy
import json
import sys
import threading
import time
from pathlib import Path

CHEMSIM_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = CHEMSIM_ROOT / 'scripts'
FLEX_ASSET_DIR = CHEMSIM_ROOT / 'assets/basic40_batch02/t5_automation/opentrons_flex'
FLEX_CONTRACT = FLEX_ASSET_DIR / 'asset.contract.json'
PROTOCOL_VERSION = '1.0.0'

# Opentrons labware used by the pilot (load names from the Opentrons labware library; geometry rounded).
LABWARE_TYPES = {
    'opentrons_flex_96_tiprack_1000ul': dict(kind='tiprack', display='Opentrons Flex 96 Tip Rack 1000 uL', rows=8, cols=12,
                                             pitch_m=0.009, z_m=0.080, tip_capacity_m3=1e-6),
    'opentrons_flex_96_tiprack_200ul': dict(kind='tiprack', display='Opentrons Flex 96 Tip Rack 200 uL', rows=8, cols=12,
                                            pitch_m=0.009, z_m=0.080, tip_capacity_m3=2e-7),
    'opentrons_flex_96_tiprack_50ul': dict(kind='tiprack', display='Opentrons Flex 96 Tip Rack 50 uL', rows=8, cols=12,
                                           pitch_m=0.009, z_m=0.080, tip_capacity_m3=5e-8),
    'nest_12_reservoir_15ml': dict(kind='reservoir', display='NEST 12 Well Reservoir 15 mL', rows=1, cols=12, pitch_m=0.009,
                                   z_m=0.030, well_capacity_m3=1.5e-5),
    'corning_96_wellplate_360ul_flat': dict(kind='plate', display='Corning 96 Well Plate 360 uL Flat', rows=8, cols=12,
                                            pitch_m=0.009, z_m=0.040, well_capacity_m3=3.6e-7),
    'opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap': dict(kind='tuberack', display='Opentrons 24 Tube Rack, Eppendorf 1.5 mL',
                                                                   rows=4, cols=6, pitch_m=0.0193, z_m=0.035, well_capacity_m3=1.5e-6),
}
ROW_NAMES = 'ABCDEFGH'
# The runtime's travel box is a simulation convention (contract limitation).  Slot centres from the contract geometry are
# scaled by 0.75 into that box so that every well of a labware on any working slot is a reachable joint target.
DECK_SCALE = 0.75
TRASH_Z_M = 0.120
LIMITS = {'x_m': (-0.24, 0.24), 'y_m': (-0.21, 0.21), 'z_m': (0.0, 0.22)}
EVIDENCE = 'chemlab fast-sim deck registry (task setup by the trusted host)'


def _load_runtime_module():
    if str(SCRIPTS_DIR) not in sys.path:
        sys.path.insert(0, str(SCRIPTS_DIR))
    import basic40_runtime_automation as module
    return module


def _same(a, b):
    return all(abs(a[k] - b[k]) <= 1e-9 for k in ('x_m', 'y_m', 'z_m'))


def well_names(rows, cols):
    """Opentrons well order: column by column (A1, B1, ... H1, A2, ...)."""
    return ['%s%d' % (ROW_NAMES[r], c + 1) for c in range(cols) for r in range(rows)]


class BackendError(Exception):
    """Host-side setup error (bad deck, unknown labware)."""


class FastSimFlexBackend:
    """Opentrons Flex on the fast simulation (Chemistry-Sim runtime), one instance."""
    instrument = 'flex'
    asset_type_id = 'instrument.opentrons_flex_single_5_1000ul'
    contract_path = FLEX_CONTRACT

    def __init__(self, deck, *, instance_id='flex1', faults=None, protocols=()):
        self._m = _load_runtime_module()
        self.rt = self._m.OpentronsFlexRuntime()
        self.iid = instance_id
        self.rt.create_instance(instance_id)
        self._lock = threading.RLock()
        self._sessions = {}
        self._visibility = {}
        self._scene_n = 0
        self._dispenses = {}
        self.faults = dict(faults or {})
        self.fault_events = []
        self.clock_errors = []
        self._clock_thread = None
        self._clock_stop = threading.Event()
        self.speed = None
        self.register_principal('scene', 'debug')
        self.register_principal('evaluator', 'evaluator')
        self._geometry = json.loads(FLEX_CONTRACT.read_text())['extensions']['chemistry_lab.geometry_contract']['slots']
        self._build_deck(deck)
        for p in protocols:
            self.rt.host_store_protocol(self.iid, copy.deepcopy(p))

    # ------------------------------------------------------------------ principals
    def register_principal(self, name, visibility='agent'):
        with self._lock:
            if name not in self._sessions:
                self.rt.register_principal(name, visibility=visibility, instance_ids=[self.iid])
                self._sessions[name] = self.rt.session(name)
                self._visibility[name] = visibility
            return name

    def _session(self, principal):
        if principal not in self._sessions:
            raise KeyError('unknown principal %r' % principal)
        return self._sessions[principal]

    # ------------------------------------------------------------------ lab protocol 1.0.0
    def describe(self, principal):
        with self._lock:
            return self._session(principal).describe(instance_id=self.iid)

    def read(self, principal, state_ids=None):
        with self._lock:
            return self._session(principal).read(instance_id=self.iid, state_ids=state_ids)

    def write(self, principal, state_id, value, request_id):
        with self._lock:
            return self._session(principal).write(instance_id=self.iid, state_id=state_id, value=value, request_id=request_id)

    def poll(self, principal, operation_id):
        """The Flex runtime is synchronous (no operation ids); long work is a protocol run read through run.state."""
        self._session(principal)
        return {'protocol_version': PROTOCOL_VERSION, 'instance_id': self.iid, 'request_id': None, 'operation_id': operation_id,
                'status': 'unsupported', 'state_revision': None, 'clock': {'domain': 'simulation', 'time_s': self.now()},
                'result': {}, 'observations': {}, 'error': {'code': 'UNSUPPORTED', 'message': 'no asynchronous operations',
                                                           'retryable': False, 'side_effects': 'none'}}

    def invoke(self, principal, capability_id, arguments, request_id):
        with self._lock:
            session = self._session(principal)
            if self.faults.get('raise_on') == capability_id:
                return self._failed(request_id, 'BACKEND_EXCEPTION', 'injected backend fault')
            try:
                self._scene_before(capability_id, arguments)
                response = session.invoke(instance_id=self.iid, capability_id=capability_id, arguments=arguments, request_id=request_id)
            except Exception as exc:                                    # a runtime bug must not kill the service
                return self._failed(request_id, 'BACKEND_EXCEPTION', '%s: %s' % (type(exc).__name__, exc))
            if response.get('status') == 'succeeded':
                self._scene_after(principal, capability_id, arguments)
            return response

    def _failed(self, request_id, code, message):
        return {'protocol_version': PROTOCOL_VERSION, 'instance_id': self.iid, 'request_id': request_id, 'operation_id': None,
                'status': 'failed', 'state_revision': None, 'clock': {'domain': 'simulation', 'time_s': self.now()}, 'result': {},
                'observations': {}, 'error': {'code': code, 'message': message, 'retryable': False, 'side_effects': 'unknown'}}

    # ------------------------------------------------------------------ simulated time
    def now(self):
        return self.rt.time_s

    def advance(self, dt_s):
        self.advance_to(self.now() + float(dt_s))

    CLOCK_STEP_S = 0.5

    def advance_to(self, t_s):
        """Move simulated time forward continuously.  The runtime runs at most about two protocol commands per clock call
        (it never catches up a long jump), so time is advanced in steps of CLOCK_STEP_S."""
        with self._lock:
            self._service_run_tip_events()
            while self.rt.time_s < t_s:
                self.rt.host_set_clock(float(min(t_s, self.rt.time_s + self.CLOCK_STEP_S)))
                self._service_run_tip_events()

    def start_clock(self, speed):
        """Simulated time follows wall time x speed (a background thread), until stop_clock()."""
        self.stop_clock()
        self.speed = float(speed)
        self._clock_stop.clear()
        t0_sim, t0_wall = self.now(), time.monotonic()

        def loop():
            while not self._clock_stop.wait(0.05):
                try:
                    self.advance_to(t0_sim + self.speed * (time.monotonic() - t0_wall))
                except Exception as exc:                                # keep ticking; status reports the fault
                    if len(self.clock_errors) < 20:
                        self.clock_errors.append('%s: %s' % (type(exc).__name__, exc))
        self._clock_thread = threading.Thread(target=loop, name='flex-clock', daemon=True)
        self._clock_thread.start()

    def stop_clock(self):
        if self._clock_thread is not None:
            self._clock_stop.set()
            self._clock_thread.join(timeout=2)
            self._clock_thread = None

    # ------------------------------------------------------------------ host side: deck registry
    def _target(self, slot, row, col, lw):
        sx, sy, _ = self._geometry[slot]['position_m']
        pos = {'x_m': round(DECK_SCALE * sx + (col - (lw['cols'] - 1) / 2.0) * lw['pitch_m'], 6),
               'y_m': round(DECK_SCALE * sy - (row - (lw['rows'] - 1) / 2.0) * lw['pitch_m'], 6),
               'z_m': lw['z_m']}
        for k, (lo, hi) in LIMITS.items():
            if not lo <= pos[k] <= hi:
                raise BackendError('well %s%d of a labware on %s is outside the travel box' % (ROW_NAMES[row], col + 1, slot))
        return pos

    def _build_deck(self, deck):
        self._labware, self._by_id = [], {}
        used_slots = {}
        for spec in deck.get('labware', []):
            name, slot, ltype = spec['name'], spec['slot'], spec['type']
            if ltype not in LABWARE_TYPES:
                raise BackendError('unknown labware type %r' % ltype)
            if slot in used_slots or self._geometry.get(slot, {}).get('kind') != 'working':
                raise BackendError('slot %r is taken or not a working slot' % slot)
            used_slots[slot] = name
            lw = LABWARE_TYPES[ltype]
            entry = {'name': name, 'type': ltype, 'display': lw['display'], 'kind': lw['kind'], 'slot': slot,
                     'rows': lw['rows'], 'cols': lw['cols'], 'wells': {}}
            names = well_names(lw['rows'], lw['cols'])
            if lw['kind'] == 'tiprack':
                entry['tip_capacity_m3'] = lw['tip_capacity_m3']
                present = spec.get('tips', 'all')
                present = set(names) if present == 'all' else set(present)
            else:
                entry['well_capacity_m3'] = lw['well_capacity_m3']
                volumes = spec.get('volumes_ul', {})
            for n in names:
                row, col = ROW_NAMES.index(n[0]), int(n[1:]) - 1
                pos = self._target(slot, row, col, lw)
                w = {'well': n, 'position_m': pos}
                if lw['kind'] == 'tiprack':
                    w['tip_id'] = '%s_%s' % (name, n) if n in present else None
                    if w['tip_id']:
                        self.rt.host_register_tip(self.iid, w['tip_id'], slot_id=slot, capacity_m3=lw['tip_capacity_m3'],
                                                  position_m=pos, evidence=EVIDENCE)
                        self._by_id[w['tip_id']] = (name, n)
                else:
                    w['container_id'] = '%s_%s' % (name, n)
                    w['capacity_m3'] = lw['well_capacity_m3']
                    self.rt.host_register_container(self.iid, w['container_id'], slot_id=slot, capacity_m3=lw['well_capacity_m3'],
                                                    volume_m3=round(float(volumes.get(n, 0.0)) * 1e-9, 15), position_m=pos,
                                                    evidence=EVIDENCE)
                    self._by_id[w['container_id']] = (name, n)
                entry['wells'][n] = w
            self._labware.append(entry)
        trash_slot = deck.get('trash', 'A1')
        sx, sy, _ = self._geometry[trash_slot]['position_m']
        self._trash = {'slot': trash_slot, 'position_m': {'x_m': round(DECK_SCALE * sx, 6), 'y_m': round(DECK_SCALE * sy, 6), 'z_m': TRASH_Z_M}}
        self.rt.host_register_waste(self.iid, slot_id=trash_slot, position_m=self._trash['position_m'], evidence=EVIDENCE)
        if deck.get('power'):
            self.invoke('scene', 'power.set', {'enabled': True}, 'scene-power-0')

    def registry(self):
        """Static deck layout for the core service: names, slots, wells, joint targets, capacities.  No liquid volumes."""
        return {'labware': copy.deepcopy(self._labware), 'trash': copy.deepcopy(self._trash),
                'home_m': dict(self._m.FLEX_HOME_POSITION), 'limits_m': copy.deepcopy(LIMITS),
                'pipette': {'model': self._m.FLEX_PIPETTE_MODEL, 'min_m3': self._m.OPENTRONS_FLEX_MIN_M3,
                            'max_m3': self._m.OPENTRONS_FLEX_MAX_M3},
                'command_s': self._m.FLEX_COMMAND_S}

    def location_of(self, object_id):
        return self._by_id.get(object_id)

    # ------------------------------------------------------------------ host side: the scene
    def _host_item(self):
        return self.rt._instances[self.iid]                           # trusted host access (same as the runtime's host_* API)

    def _position(self):
        s = self._host_item()['state']
        return {k: s['motion.' + k] for k in ('x_m', 'y_m', 'z_m')}

    def _release_spot_ok(self, slot, pos):
        if slot == self._trash['slot'] and _same(pos, self._trash['position_m']):
            return True
        tips = self._host_item()['tips']
        for lw in self._labware:
            if lw['kind'] == 'tiprack' and lw['slot'] == slot:
                for w in lw['wells'].values():
                    if _same(pos, w['position_m']) and not any(t['slot_id'] is not None and _same(t['position_m'], pos) for t in tips.values()):
                        return True
        return False

    def _scene_before(self, cid, args):
        """The physical world: a tip is picked up when the pipette is at a tip, and released over the trash or an empty rack well."""
        if not isinstance(args, dict):
            return
        item = self._host_item()
        pos = self._position()
        if cid == 'tip.attach' and isinstance(args.get('tip_id'), str):
            tip = item['tips'].get(args['tip_id'])
            if tip is not None and tip['slot_id'] is not None and item['state']['tip.attached_id'] is None and _same(pos, tip['position_m']):
                self.rt.host_confirm_tip_event(self.iid, event='attach', tip_id=args['tip_id'], slot_id=tip['slot_id'], position_m=pos,
                                               evidence='scene: pipette at the tip')
        elif cid == 'tip.release' and isinstance(args.get('target_slot_id'), str):
            tip_id = item['state']['tip.attached_id']
            if tip_id is not None and self._release_spot_ok(args['target_slot_id'], pos):
                self.rt.host_confirm_tip_event(self.iid, event='release', tip_id=tip_id, slot_id=args['target_slot_id'], position_m=pos,
                                               evidence='scene: pipette over the drop spot')

    def _service_run_tip_events(self):
        """A protocol run waits for the host to confirm tip events: confirm them when physically plausible."""
        for _ in range(1000):
            pending = self.rt.host_pending_tip_event(self.iid)
            if pending is None:
                return
            item, pos = self._host_item(), pending['position_m']
            if pending['event'] == 'attach':
                tip = item['tips'].get(pending['tip_id'])
                ok = tip is not None and tip['slot_id'] is not None and _same(pos, tip['position_m'])
            else:
                ok = self._release_spot_ok(pending['slot_id'], pos)
            if not ok:
                return
            self.rt.host_confirm_tip_event(self.iid, event=pending['event'], tip_id=pending['tip_id'], slot_id=pending['slot_id'],
                                           position_m=pos, evidence='scene: run tip event')

    def _scene_after(self, principal, cid, args):
        n = self.faults.get('tip_off_after_dispenses')
        if cid == 'sample.dispense' and n and principal not in ('scene', 'evaluator'):
            self._dispenses[principal] = self._dispenses.get(principal, 0) + 1
            if self._dispenses[principal] == n:
                self._drop_tip_here('fault: the tip came off after dispense %d' % n)

    def _drop_tip_here(self, why):
        item = self._host_item()
        tip_id = item['state']['tip.attached_id']
        if tip_id is None:
            return
        pos = self._position()
        slot = next((lw['slot'] for lw in self._labware for w in lw['wells'].values() if _same(w['position_m'], pos)), None)
        slot = slot or self._trash['slot']
        self.rt.host_confirm_tip_event(self.iid, event='release', tip_id=tip_id, slot_id=slot, position_m=pos, evidence=why)
        self._scene_n += 1
        r = self._session('scene').invoke(instance_id=self.iid, capability_id='tip.release', arguments={'target_slot_id': slot},
                                          request_id='scene-%d' % self._scene_n)
        self.fault_events.append({'time_s': self.now(), 'what': why, 'tip_id': tip_id, 'slot': slot, 'status': r['status']})

    # ------------------------------------------------------------------ host side: other users, operators, evaluator
    def start_run_as(self, principal, protocol_id, params=None):
        """Another user starts a stored protocol (run setup, parameters, play) under their own identity."""
        self.register_principal(principal)
        steps = [('run.setup', {'protocol_id': protocol_id})]
        steps += [('run.set_parameter', {'name': k, 'value': v}) for k, v in (params or {}).items()]
        steps.append(('run.confirm_parameters', {}))
        steps.append(('run.play', {}))
        for n, (cid, a) in enumerate(steps):
            r = self.invoke(principal, cid, a, '%s-start-%s-%d' % (principal, protocol_id, n))
            if r['status'] != 'succeeded':
                raise BackendError('%s failed for %s: %s' % (cid, principal, r['error']))

    def set_container_volume(self, container_id, volume_ul):
        self.rt.host_set_container_volume(self.iid, container_id, volume_m3=round(volume_ul * 1e-9, 15), evidence=EVIDENCE)

    def evaluator_state(self):
        """Everything the instrument knows, including process truth (truth.* slots).  For checkers only."""
        r = self.read('evaluator')
        out = {sid: obs['value'] for sid, obs in r['observations'].items()}
        out['time_s'] = self.now()
        return out

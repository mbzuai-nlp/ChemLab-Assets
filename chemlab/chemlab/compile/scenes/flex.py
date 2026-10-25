"""The Opentrons Flex deck in the generic lab: the trusted host side (what the physical world and the operator do).

Attached to the Flex's lab config entry ("scene": "chemlab.compile.scenes.flex:FlexScene"); reads the entry's

    "deck": {"labware": [...], "trash": "A1", "home_m": {...}, "command_s": 1.0}      (chemlab/compile/deck.py)
    "protocols": [...]            stored on the robot (host_store_protocol)
    "faults": {"tip_off_after_dispenses": N, "raise_on": CAPABILITY}

and does what the pilot backend (chemlab/backend.py) did for the pilot: registers tips, containers (with their liquid volumes,
which only the host knows) and the waste; confirms tip pick-up / drop when the pipette is physically at the tip / over the
trash or an empty rack well (the runtime asks a trusted host for that); services the tip events of protocol runs after every
clock step; injects faults; starts other users' runs.  Nothing here is reachable from the sandbox.
"""
import copy

from ...labbackend import OPERATOR
from ..deck import build_registry

EVIDENCE = 'chemlab generic-lab deck registry (task set-up by the trusted host)'


def _same(a, b):
    return all(abs(a[k] - b[k]) <= 1e-9 for k in ('x_m', 'y_m', 'z_m'))


class FlexScene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host                                      # RuntimeHost
        self.rt = inst.host.host                                   # OpentronsFlexRuntime (trusted host API)
        self.iid = inst.host.instance_id
        deck = inst.entry.get('deck') or {}
        self.reg = build_registry(deck, inst.contract)
        self.faults = dict(inst.entry.get('faults') or {})
        self.fault_events, self._dispenses, self._n = [], {}, 0
        vols = {lw['name']: lw.get('volumes_ul') or {} for lw in deck.get('labware', [])}
        for lw in self.reg['labware']:
            for w in lw['wells'].values():
                if lw['kind'] == 'tiprack':
                    if w['tip_id']:
                        self.rt.host_register_tip(self.iid, w['tip_id'], slot_id=lw['slot'], capacity_m3=lw['tip_capacity_m3'],
                                                  position_m=w['position_m'], evidence=EVIDENCE)
                else:
                    self.rt.host_register_container(self.iid, w['container_id'], slot_id=lw['slot'], capacity_m3=w['capacity_m3'],
                                                    volume_m3=round(float(vols[lw['name']].get(w['well'], 0.0)) * 1e-9, 15),
                                                    position_m=w['position_m'], evidence=EVIDENCE)
        t = self.reg['trash']
        self.rt.host_register_waste(self.iid, slot_id=t['slot'], position_m=t['position_m'], evidence=EVIDENCE)
        for p in inst.entry.get('protocols') or []:
            self.rt.host_store_protocol(self.iid, copy.deepcopy(p))
        self._orig_invoke, self._orig_advance = self.host.invoke, self.host.advance
        self.host.invoke = self._invoke                            # every call (agents, operator, evaluator) passes the scene
        self.host.advance = self._advance                          # every clock step

    # ------------------------------------------------------------------ hooks
    def _invoke(self, principal, cap, args, request_id):
        self.host.flush()
        if self.faults.get('raise_on') == cap and principal.startswith('chemlab_agent_'):
            raise RuntimeError('injected backend fault')
        self._before(cap, args)
        r = self._orig_invoke(principal, cap, args, request_id)
        if isinstance(r, dict) and r.get('status') == 'succeeded':
            self._after(principal, cap, args)
        return r

    def _advance(self, dt):
        self._orig_advance(dt)
        self._service_run_tip_events()

    # ------------------------------------------------------------------ the physical world
    def _item(self):
        return self.rt._instances[self.iid]

    def _position(self):
        s = self._item()['state']
        return {k: s['motion.' + k] for k in ('x_m', 'y_m', 'z_m')}

    def _release_spot_ok(self, slot, pos):
        t = self.reg['trash']
        if slot == t['slot'] and _same(pos, t['position_m']):
            return True
        tips = self._item()['tips']
        for lw in self.reg['labware']:
            if lw['kind'] == 'tiprack' and lw['slot'] == slot:
                for w in lw['wells'].values():
                    if _same(pos, w['position_m']) and not any(x['slot_id'] is not None and _same(x['position_m'], pos) for x in tips.values()):
                        return True
        return False

    def _before(self, cap, args):
        if not isinstance(args, dict):
            return
        item, pos = self._item(), self._position()
        if cap == 'tip.attach' and isinstance(args.get('tip_id'), str):
            tip = item['tips'].get(args['tip_id'])
            if tip is not None and tip['slot_id'] is not None and item['state']['tip.attached_id'] is None and _same(pos, tip['position_m']):
                self.rt.host_confirm_tip_event(self.iid, event='attach', tip_id=args['tip_id'], slot_id=tip['slot_id'], position_m=pos,
                                               evidence='scene: pipette at the tip')
        elif cap == 'tip.release' and isinstance(args.get('target_slot_id'), str):
            tip_id = item['state']['tip.attached_id']
            if tip_id is not None and self._release_spot_ok(args['target_slot_id'], pos):
                self.rt.host_confirm_tip_event(self.iid, event='release', tip_id=tip_id, slot_id=args['target_slot_id'], position_m=pos,
                                               evidence='scene: pipette over the drop spot')

    def _service_run_tip_events(self):
        for _ in range(1000):
            pending = self.rt.host_pending_tip_event(self.iid)
            if pending is None:
                return
            item, pos = self._item(), pending['position_m']
            if pending['event'] == 'attach':
                tip = item['tips'].get(pending['tip_id'])
                ok = tip is not None and tip['slot_id'] is not None and _same(pos, tip['position_m'])
            else:
                ok = self._release_spot_ok(pending['slot_id'], pos)
            if not ok:
                return
            self.rt.host_confirm_tip_event(self.iid, event=pending['event'], tip_id=pending['tip_id'], slot_id=pending['slot_id'],
                                           position_m=pos, evidence='scene: run tip event')

    def _after(self, principal, cap, args):
        n = self.faults.get('tip_off_after_dispenses')
        if cap == 'sample.dispense' and n and principal.startswith('chemlab_agent_'):
            self._dispenses[principal] = self._dispenses.get(principal, 0) + 1
            if self._dispenses[principal] == n:
                self._drop_tip_here('fault: the tip came off after dispense %d' % n)

    def _drop_tip_here(self, why):
        item = self._item()
        tip_id = item['state']['tip.attached_id']
        if tip_id is None:
            return
        pos = self._position()
        slot = next((lw['slot'] for lw in self.reg['labware'] for w in lw['wells'].values() if _same(w['position_m'], pos)), None)
        slot = slot or self.reg['trash']['slot']
        self.rt.host_confirm_tip_event(self.iid, event='release', tip_id=tip_id, slot_id=slot, position_m=pos, evidence=why)
        self._n += 1
        r = self.host.sessions[OPERATOR].invoke(instance_id=self.iid, capability_id='tip.release', arguments={'target_slot_id': slot},
                                                request_id='scene-%d' % self._n)
        self.fault_events.append({'time_s': self.backend.now(), 'what': why, 'tip_id': tip_id, 'slot': slot, 'status': r['status']})

    # ------------------------------------------------------------------ host side: other users, evaluator
    def start_run_as(self, caller, protocol_id, params=None):
        """Another user starts a stored protocol (setup, parameters, play) under their own identity."""
        self.backend.add_caller(caller)
        steps = [('run.setup', {'protocol_id': protocol_id})]
        steps += [('run.set_parameter', {'name': k, 'value': v}) for k, v in (params or {}).items()]
        steps += [('run.confirm_parameters', {}), ('run.play', {})]
        for n, (cap, a) in enumerate(steps):
            r = self.backend.invoke(caller, self.inst.id, cap, a, '%s-start-%s-%d' % (caller, protocol_id, n))
            if r.get('status') != 'succeeded':
                raise RuntimeError('%s failed for %s: %s' % (cap, caller, r.get('error')))

    def registry(self):
        return self.reg

    def evaluator_state(self):
        r = self.backend.evaluator_read(self.inst.id)
        out = {k: (o.get('value') if isinstance(o, dict) else o) for k, o in (r.get('observations') or {}).items()}
        out['time_s'] = self.backend.now()
        return out

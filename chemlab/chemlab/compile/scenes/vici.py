"""The VICI EUTA stream-selection valve in the generic lab: the trusted host side.

The runtime takes a position command and then waits for the trusted host to confirm that the rotor has arrived (host_arrive);
nothing else ever confirms it in a lab without a 3-D scene.  This scene plays the valve drive: after every clock step it confirms
a pending target once ``move_s`` of lab time has passed since the command (an engineering value, "move_s" in the config entry,
default 0.5 s; the EUTA manual gives no switching time), so the valve reports "in position" on the next clock step.

Attached in a lab config entry: "scene": "chemlab.compile.scenes.vici:ViciScene".  Nothing here is reachable from the sandbox.
"""


class ViciScene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host                                      # RuntimeHost
        self.rt = inst.host.host                                   # ViciEutaValveRuntime (trusted host API)
        self.move_s = float(inst.entry.get('move_s', 0.5))
        self.pending = None                                        # (target, requested at lab-local seconds)
        self._n = 0
        self._orig_invoke, self._orig_advance = self.host.invoke, self.host.advance
        self.host.invoke = self._invoke
        self.host.advance = self._advance
        self.arrivals = []

    def _state(self):
        return self.rt.debug_snapshot()['state']

    def _invoke(self, principal, cap, args, request_id):
        r = self._orig_invoke(principal, cap, args, request_id)
        if cap == 'valve.select_position' and isinstance(r, dict) and r.get('status') == 'succeeded':
            self.pending = (args.get('position'), self.host.local_s)
        return r

    def _advance(self, dt):
        self._orig_advance(dt)
        if self.pending is None:
            return
        target, t0 = self.pending
        st = self._state()
        if st.get('valve.target_position') != target or st.get('valve.in_position'):
            self.pending = None
            return
        if self.host.local_s - t0 + 1e-9 >= self.move_s:
            self._n += 1
            r = self.rt.host_arrive(position=target, world_time_s=self.host.local_s, request_id='scene-vici-arrive-%d' % self._n)
            self.arrivals.append({'time_s': self.backend.now(), 'position': target, 'status': (r or {}).get('status')})
            self.pending = None

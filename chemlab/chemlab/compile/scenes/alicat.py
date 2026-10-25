"""The Alicat MC mass-flow controller in the generic lab: the trusted host side (the gas line around it).

The runtime asks the trusted host whether gas is flowing through the line ("no flow" and "equilibrium" assertions); it never
decides that itself, and its tare (flow.zero) and gas selection need the host's "no flow".  This scene plays the gas line: the
cylinder pressure, the downstream pressure and the gas temperature come from the entry's "gas_line" (defaults: 3 bar abs up,
1 atm down, 25 C), and after every successful set-point change the line has "no flow" exactly when the set point is 0 (the
controller's own valve then closes the line).  Attached in a lab config entry: "scene": "chemlab.compile.scenes.alicat:AlicatScene";
the ports and the response time are lab set-up steps (tasks/gas_fluid_world.py).  Nothing here is reachable from the sandbox.
"""


class AlicatScene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host
        self.rt = inst.host.host                                   # AlicatMCRuntime (trusted host API)
        self.iid = inst.host.instance_id
        line = dict(inst.entry.get('gas_line') or {})
        self.up = float(line.get('upstream_pa', 300000.0))
        self.down = float(line.get('downstream_pa', 101325.0))
        self.temp = float(line.get('temperature_k', 298.15))
        self._orig_invoke = self.host.invoke
        self.host.invoke = self._invoke

    def boundary(self, no_flow):
        self.rt.host_set_boundary(self.iid, upstream_pressure_Pa=self.up, downstream_pressure_Pa=self.down, temperature_K=self.temp,
                                  equilibrium=True, no_flow=bool(no_flow))

    def _invoke(self, principal, cap, args, request_id):
        r = self._orig_invoke(principal, cap, args, request_id)
        if cap == 'flow.set_setpoint' and isinstance(r, dict) and r.get('status') == 'succeeded':
            self.host.flush()
            self.boundary(float(args.get('flow_rate_m3_s') or 0.0) == 0.0)
        return r

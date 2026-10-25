"""The Metrohm 914 meter's sample beaker in the generic lab: the trusted host side.

The 914 runtime measures whatever solution the host says the probes are standing in: every clock step must carry that solution
(``host_step(time, inputs={instance: sample})``); a step without it leaves the last reading stale.  The default lab has no
beaker, so the meter shows NO_SAMPLE.  This scene is attached to the 914's lab config entry
(``"scene": "chemlab.compile.scenes.metrohm914:Metrohm914Scene"``) and reads

    "solution": {"kind": "ph_buffer", "substance_id": "nist_srm_186g_phosphate_equimolal_buffer", "temperature_k": 298.15,
                 "standard_channel": "ph"}

(the template solution schema of ``scripts/basic40_templates/metrohm_914.py``: NIST pH buffers and KCl conductivity standards,
or a solution of components).  ``standard_channel`` tells the meter which channel the solution is a calibration standard for
(the operator's declaration when calibrating).  The scene feeds the solution at every clock step; nothing here is reachable from
the sandbox, and it never calls the runtime as an agent.
"""
import copy

from ...recipes import HOST_STEP_ABS


class Metrohm914Scene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host                                      # RuntimeHost
        self.rt = inst.host.host                                   # Metrohm914DeviceRuntime (trusted host API)
        self.iid = inst.host.instance_id
        self.solution = copy.deepcopy(inst.entry.get('solution'))
        if self.solution is not None and self.host.recipe.get('advance') == HOST_STEP_ABS:
            self._orig_advance = self.host.advance
            self.host.advance = self._advance

    def set_solution(self, solution):
        """Trusted host / task world: change the beaker (None = take the probes out)."""
        self.solution = copy.deepcopy(solution)

    def _advance(self, dt):
        if self.solution is None:
            return self._orig_advance(dt)
        t_new = self.host.local_s + dt
        self.host.clock_counter += 1
        self.rt.host_step_template(t_new, solutions={self.iid: copy.deepcopy(self.solution)})
        self.host.local_s = t_new

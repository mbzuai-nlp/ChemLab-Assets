"""The Agilent 8697 headspace sampler's event clock in the generic lab: the trusted host side.

The 8697 runtime keeps its own terminal events (sequence steps, diagnostic tests, tray calibration, start-up) and processes them
only in ``host_update()``, up to the time of its clock callable.  Its Matterix recipe has no advance mode (``advance: None``):
the lab sets the clock it reads, but nothing calls ``host_update()``, so a started sequence never moves.  This
scene, attached to the 8697's lab config entry (``"scene": "chemlab.compile.scenes.hs8697:HS8697Scene"``), calls
``host_update()`` after every clock step - what the instrument's own firmware does.  Nothing here is reachable from the sandbox,
and it never calls the runtime as an agent.
"""


class HS8697Scene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host                                      # RuntimeHost
        self.rt = inst.host.host                                   # AgilentHS8697 (trusted host API)
        # exactly one host_update() per clock step: the recipe mode "clock_host_update" (hosts.RuntimeHost.advance) or the
        # lab's clock hook (labbackend._build, recipe without an advance mode) already runs it; this scene adds it only otherwise
        already = inst.recipe.get('advance') == 'clock_host_update' or getattr(inst, 'clock_hook', None) == 'host_update'
        self.wrapped = False
        if callable(getattr(self.rt, 'host_update', None)) and not already:
            self._orig_advance = self.host.advance
            self.host.advance = self._advance
            self.wrapped = True

    def _advance(self, dt):
        self._orig_advance(dt)
        self.rt.host_update()

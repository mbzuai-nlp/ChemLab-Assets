"""The Inert PureLab HE glovebox in the generic lab: the trusted host side (the world around the box).

The glovebox twin is boundary-driven by design: it never invents the antechamber pressure or the box sensor readings; a trusted
host must supply them.  This scene plays that host, after every clock step:

* the antechamber pressure: a lumped model of the shared vacuum source and the inert-gas refill (evacuating: exponential decay
  towards the source's 200 Pa ultimate pressure with ``evac_tau_s``; backfilling: exponential rise to ambient with
  ``refill_tau_s``; an open outer door: ambient), given to the runtime as a fresh external boundary sample (source running, gas
  ready) at the current time, which is what its antechamber cycle consumes;
* the box sensors: the indicated box pressure follows the runtime's own box-pressure truth (its PLC keeps that in the working
  window), and the O2 / H2O readings are the values the task world declares (``o2_ppm``, ``h2o_ppm`` in the entry's "glovebox").

Engineering values (documented, not from the manual): evac_tau_s 15 s, refill_tau_s 4 s.  Attached in a lab config entry:
"scene": "chemlab.compile.scenes.glovebox:GloveboxScene".  Nothing here is reachable from the sandbox.
"""
import math

AMBIENT_PA = 101325.0
EVIDENCE = 'chemlab generic-lab glovebox scene (task world): lumped antechamber model and declared box sensors'


class GloveboxScene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host
        self.rt = inst.host.host                                   # InertPureLabHost (trusted host API)
        self.iid = inst.host.instance_id
        cfg = dict(inst.entry.get('glovebox') or {})
        self.evac_tau = float(cfg.get('evac_tau_s', 15.0))
        self.refill_tau = float(cfg.get('refill_tau_s', 4.0))
        self.ultimate = float(cfg.get('ultimate_pa', 200.0))
        self.o2 = float(cfg.get('o2_ppm', 0.8)) * 1e-6
        self.h2o = float(cfg.get('h2o_ppm', 0.5)) * 1e-6
        self.ante_p = AMBIENT_PA
        self.rt.connect_vacuum_source(self.iid, cfg.get('vacuum_source', 'lab_vacuum_line'))
        self.rt.configure_cycle_boundaries(self.iid, evacuation_pressure_pa=float(cfg.get('evacuation_pa', 300.0)),
                                           backfill_pressure_pa=float(cfg.get('backfill_pa', 95000.0)), evidence=EVIDENCE)
        self.rt.set_process_truth(self.iid, {'pressure.box_gauge_pa': float(cfg.get('box_gauge_pa', 800.0)),
                                             'atmosphere.oxygen_truth_mole_fraction': self.o2,
                                             'atmosphere.water_truth_mole_fraction': self.h2o}, evidence=EVIDENCE)
        self._orig_advance = self.host.advance
        self.host.advance = self._advance
        self._publish()

    def _state(self):
        return self.rt._instances[self.iid].state

    def _publish(self):
        s = self._state()
        self.rt.set_external_boundary(self.iid, pressure_pa=max(self.ante_p, self.ultimate), source_running=True, gas_supply_ready=True,
                                      evidence=EVIDENCE)
        obs = {'atmosphere.oxygen_mole_fraction': self.o2, 'atmosphere.water_mole_fraction': self.h2o,
               'antechamber.indicated_pressure_pa': round(max(self.ante_p, self.ultimate), 1)}
        if s.get('pressure.box_gauge_pa') is not None:
            obs['pressure.indicated_gauge_pa'] = round(float(s['pressure.box_gauge_pa']), 1)
        self.rt.set_observations(self.iid, obs, evidence=EVIDENCE)

    def _advance(self, dt):
        self._orig_advance(dt)
        s = self._state()
        phase = s.get('antechamber.phase')
        if s.get('antechamber.outer_door_open'):
            self.ante_p = AMBIENT_PA
        elif s.get('antechamber.cycle_active') and phase == 'evacuating':
            self.ante_p = self.ultimate + (self.ante_p - self.ultimate) * math.exp(-dt / self.evac_tau)
        elif s.get('antechamber.cycle_active') and phase == 'backfilling':
            self.ante_p = AMBIENT_PA - (AMBIENT_PA - self.ante_p) * math.exp(-dt / self.refill_tau)
        self._publish()

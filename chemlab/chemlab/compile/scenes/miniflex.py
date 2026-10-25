"""The Rigaku MiniFlex 600 in the generic lab: the trusted host side (what the physical world and the operator provide).

The MiniFlex twin (basic40 runtime) has no sample physics of its own: the diffracted intensity comes from a host-loaded
response grid, and the external chiller is a host connection.  Attached to the lab config entry
("scene": "chemlab.compile.scenes.miniflex:MiniFlexScene"), this scene reads the entry's

    "chiller": "chiller_01"                                  the recirculating chiller connection (connected and ready)
    "samples": {"quartz_holder": [{"phase_id": "quartz", "weight_fraction": 1.0}], ...}
                                                             the powder in each sample holder (public RRUFF / COD templates)

and, after every successful call (any principal), loads the template response of the holder in the sample slot whenever the
holder, the tube voltage / current or the slit label changed (host_load_template_sample: the pattern a real powder would give
with that tube setting).  A holder the lab did not declare gets no response, so a scan of it is refused like an empty holder.
Nothing here is reachable from the sandbox.
"""
import copy


class MiniFlexScene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host
        self.rt = inst.host.host
        self.iid = inst.host.instance_id
        self.samples = copy.deepcopy(inst.entry.get('samples') or {})
        self.loaded = None
        self.events = []
        chiller = inst.entry.get('chiller')
        if chiller:
            self.rt.host_set_cooling(self.iid, connection_id=chiller, ready=True)
        self._orig_invoke = self.host.invoke
        self.host.invoke = self._invoke

    def _state(self):
        i = self.rt._instances[self.iid]
        return i['sample'], i['voltage'], i['current'], i['slit'], i['running']

    def _invoke(self, principal, cap, args, request_id):
        r = self._orig_invoke(principal, cap, args, request_id)
        if isinstance(r, dict) and r.get('status') == 'succeeded':
            self._sync()
        return r

    def _sync(self):
        sample, kv, ma, slit, running = self._state()
        key = (sample, kv, ma, slit)
        if running or key == self.loaded or sample is None or None in (kv, ma, slit):
            return
        phases = self.samples.get(sample)
        if not phases:
            return
        try:
            meta = self.rt.host_load_template_sample(self.iid, copy.deepcopy(phases))
            self.loaded = key
            self.events.append({'time_s': self.backend.now(), 'sample': sample, 'coverage_rad': meta.get('coverage_rad')})
        except ValueError as exc:                                           # a declared holder whose phases the library rejects
            self.events.append({'time_s': self.backend.now(), 'sample': sample, 'error': str(exc)})

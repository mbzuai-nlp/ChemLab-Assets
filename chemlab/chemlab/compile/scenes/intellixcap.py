"""The Azenta IntelliXcap S1 decapper in the generic lab: the trusted host side.

A decap / recap cycle of the runtime stops three times for the trusted host: when the jaws have clamped the cap (an "attach" event:
the host creates the cap-to-gripper constraint), when the rotation has ended (the host reports whether the thread opened or
closed) and before the jaws open (a "release" event: the host removes the constraint).  Nothing confirms these in a lab without a
3-D scene, so this class plays the tube, cap and thread: after every clock step it confirms what the cycle waits for.

The lab config entry may set "thread_ok": false to make the host report a failed thread (a cross-threaded or stuck cap), which the
runtime then reports as a failed cycle (for recovery tasks).

Attached in a lab config entry: "scene": "chemlab.compile.scenes.intellixcap:IntelliXcapScene".  Nothing here is reachable from the
sandbox.
"""


class IntelliXcapScene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host
        self.rt = inst.host.host                                   # IntelliXcapS1Runtime (trusted host API)
        self.iid = inst.host.instance_id
        self.thread_ok = bool(inst.entry.get('thread_ok', True))
        self.confirmations = []
        self._n = 0
        self._orig_advance = self.host.advance
        self.host.advance = self._advance

    def _active(self):
        snap = self.rt.debug_snapshot()
        inst = snap['instances'][self.iid]
        op_id = inst.get('active_operation_id')
        return inst, (snap['operations'].get(op_id) if op_id else None)

    def _pending(self, inst, op, kind):
        for e in inst.get('events') or []:
            if e['kind'] == kind and not e['acknowledged'] and e['operation_id'] == op['operation_id'] and e['event_id'] == op.get('pending_event_id'):
                return e
        return None

    def _advance(self, dt):
        self._orig_advance(dt)
        inst, op = self._active()
        if op is None:
            return
        phase = op.get('phase')
        if phase == 'awaiting_attach':
            e = self._pending(inst, op, 'attach')
            if e is not None:
                self._n += 1
                cid = 'cap_grip_%s_%d' % (self.iid, self._n)
                self.rt.host_confirm_attachment(self.iid, operation_id=op['operation_id'], event_id=e['event_id'], constraint_id=cid,
                                                evidence='lab scene: the jaws hold cap %s' % e.get('cap_id'))
                self.confirmations.append({'time_s': self.backend.now(), 'kind': 'attach', 'constraint_id': cid})
        elif phase == 'awaiting_thread_result':
            self.rt.host_confirm_thread_result(self.iid, operation_id=op['operation_id'], succeeded=self.thread_ok,
                                               evidence='lab scene: thread %s' % ('turned' if self.thread_ok else 'did not turn'))
            self.confirmations.append({'time_s': self.backend.now(), 'kind': 'thread', 'succeeded': self.thread_ok})
        elif phase == 'awaiting_release':
            e = self._pending(inst, op, 'release')
            if e is not None:
                cid = inst['values'].get('attachment.constraint_id')
                self.rt.host_confirm_release(self.iid, operation_id=op['operation_id'], event_id=e['event_id'], constraint_id=cid,
                                             evidence='lab scene: the cap constraint is removed')
                self.confirmations.append({'time_s': self.backend.now(), 'kind': 'release', 'constraint_id': cid})

    def evaluator_state(self):
        return {'confirmations': list(self.confirmations)}

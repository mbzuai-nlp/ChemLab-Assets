"""The Brooks PreciseFlex 400 arm in the generic lab: the trusted host side (scene authority).

The runtime moves nothing by itself: a joint target (motion.set_joints) waits until the trusted scene commits the reached pose
(host_commit_pose), and a grip or release (sample.attach / sample.release) is accepted only with a pose observed at the current
host time and with containers, receiver ports and grip constraints that the host registered.  In a lab without a 3-D scene this
class plays that scene:

* at build: registers itself as the scene authority, commits the home pose, and registers the containers, receiver ports and grip
  constraints of the lab config entry's "arm" world (positions from the arm's own forward kinematics of each named pose, so a
  container is where the arm reaches at that pose);
* after every clock step: commits a pending joint target once its move time has passed (an engineering joint-speed model, "arm":
  {"joint_speed_rad_s", "z_speed_m_s"}; the PF400 data sheet gives no joint speeds for this use), and otherwise re-observes the
  current pose so that it is fresh at the current host time (the arm standing still is still observed);
* a grip or release is confirmed by the scene (the constraint is created / removed) and recorded in ``events``.

The lab config entry:

    "scene": "chemlab.compile.scenes.preciseflex:PreciseFlexScene",
    "arm": {"home": {joints}, "containers": {"plate_1": {"pose": {joints}, "port": "bench_nest", "payload_kg": 0.1}},
            "ports": {"sealer_nest": {"pose": {joints}}}, "tolerance_m": 0.005}

Nothing here is reachable from the sandbox.
"""
import copy
import math
import sys

JOINTS = ('z_m', 'shoulder_rad', 'elbow_rad', 'wrist_rad')


class PreciseFlexScene:
    def __init__(self, backend, inst):
        self.backend, self.inst = backend, inst
        self.host = inst.host                                      # RuntimeHost
        self.rt = inst.host.host                                   # PreciseFlex400Runtime (trusted host API)
        self.iid = inst.host.instance_id
        self.arm = copy.deepcopy(inst.entry.get('arm') or {})
        self.fk = getattr(sys.modules[type(self.rt).__module__], 'preciseflex400_forward_kinematics')
        self.joint_speed = float(self.arm.get('joint_speed_rad_s', 1.0))
        self.z_speed = float(self.arm.get('z_speed_m_s', 0.2))
        self.pending = None                                        # (target joints, move seconds, requested at local s)
        self.events = []
        self._orig_invoke, self._orig_advance = self.host.invoke, self.host.advance
        self.host.invoke = self._invoke
        self.host.advance = self._advance
        self.rt.host_register_scene_adapter(self.iid, self._scene)
        home = {k: float(v) for k, v in (self.arm.get('home') or {k: 0.0 for k in JOINTS}).items()}
        self.rt.host_commit_pose(self.iid, joints=home, world_time_s=self.rt.time_s)
        self.current = dict(home)
        tol = float(self.arm.get('tolerance_m', 0.005))
        for cid, c in (self.arm.get('containers') or {}).items():
            pos = self.fk(c['pose'])['tcp_position_m']
            self.rt.host_register_container(self.iid, cid, source_port_id=c.get('port', cid + '_nest'), position_m=pos, tolerance_m=tol,
                                            gross_payload_kg=c.get('payload_kg'), evidence='lab world: %s placed at its nest' % cid)
            self.rt.host_register_constraint(self.iid, c.get('constraint', 'grip_' + cid), container_id=cid, adapter=self._grip,
                                             evidence='lab world: gripper fingers fit %s' % cid)
        for pid, port in (self.arm.get('ports') or {}).items():
            pos = self.fk(port['pose'])['tcp_position_m']
            self.rt.host_register_receiver_port(self.iid, pid, position_m=pos, tolerance_m=tol, evidence='lab world: receiving nest %s' % pid)

    # the scene authority: the fast-sim scene reaches every commanded pose exactly
    def _scene(self, instance_id, expected):
        return copy.deepcopy(expected)

    def _grip(self, instance_id, event):
        self.events.append({'time_s': self.backend.now(), 'event': copy.deepcopy(event)})
        return True

    def _move_s(self, a, b):
        dz = abs(b['z_m'] - a['z_m']) / self.z_speed
        dr = max(abs(b[k] - a[k]) for k in JOINTS if k != 'z_m') / self.joint_speed
        return max(0.5, dz, dr)

    def _invoke(self, principal, cap, args, request_id):
        r = self._orig_invoke(principal, cap, args, request_id)
        if cap == 'motion.set_joints' and isinstance(r, dict) and r.get('status') == 'succeeded':
            target = {k: float(v) for k, v in args['joints'].items()}
            self.pending = (target, self._move_s(self.current, target), self.host.local_s)
        return r

    def _state(self):
        return self.rt.debug_snapshot()['instances'][self.iid]['state']

    def _advance(self, dt):
        self._orig_advance(dt)
        st = self._state()
        if st.get('motion.status') == 'fault':
            return
        if self.pending is not None:
            target, move_s, t0 = self.pending
            if st.get('motion.target_joints') != target:
                self.pending = None
            elif self.host.local_s - t0 + 1e-9 >= move_s:
                self.rt.host_commit_pose(self.iid, joints=target, world_time_s=self.rt.time_s)
                self.current = dict(target)
                self.pending = None
            return
        if st.get('motion.status') == 'ready':                       # standing still: observe the pose again at the current time
            joints = st.get('motion.target_joints') or self.current
            self.rt.host_commit_pose(self.iid, joints={k: float(v) for k, v in joints.items()}, world_time_s=self.rt.time_s)

    def evaluator_state(self):
        return {'events': copy.deepcopy(self.events), 'current': dict(self.current), 'pending': copy.deepcopy(self.pending)}


_ = math

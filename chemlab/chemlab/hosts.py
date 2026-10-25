"""One Chemistry-Sim runtime host per instrument, built from its Matterix recipe (see recipes.py).

This follows Matterix ``BridgedHost`` (runtime_bridge.py) and the release's ``examples/preview_menu.py``: constructor
arguments, instance creation, host setup calls, sessions and the clock-advance mode all come from the recipe.  The runtimes
are imported from Chemistry-Sim/scripts unchanged.

Principals: every caller of the lab gets its own *agent* principal in each runtime (where the runtime has principals), and
the lab has one *evaluator* principal.  The agent sessions are the only ones the chemlab service ever uses for callers;
the evaluator session is for the trusted host side (set-up, checkers) and is never reachable through the socket.
"""
import importlib
import inspect
import json
import sys
import threading
from pathlib import Path

from . import recipes as R

CHEMSIM_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = CHEMSIM_ROOT / 'scripts'
PROTOCOL_VERSION = '1.0.0'
_IMPORT_LOCK = threading.Lock()


def import_runtime(module):
    """Import Chemistry-Sim/scripts/<module>.py (the project root is also put on sys.path, as runtime_bridge does)."""
    with _IMPORT_LOCK:
        for p in (str(CHEMSIM_ROOT), str(SCRIPTS_DIR)):
            if p not in sys.path:
                sys.path.insert(0, p)
        return importlib.import_module(module)


_CONTRACTS = None


def contract_index():
    """{asset_type_id: contract path} over every asset.contract.json in Chemistry-Sim/assets."""
    global _CONTRACTS
    if _CONTRACTS is None:
        idx = {}
        for f in sorted((CHEMSIM_ROOT / 'assets').rglob('asset.contract.json')):
            try:
                atid = json.loads(f.read_text(encoding='utf-8'))['asset_type_id']
            except Exception:                                               # noqa: BLE001  (a broken file is reported by the caller)
                continue
            idx.setdefault(atid, f)
        _CONTRACTS = idx
    return _CONTRACTS


class HostBuildError(Exception):
    pass


class RuntimeHost:
    """One runtime host + one instance, its sessions and its clock.

    ``clock`` is the lab clock (callable returning simulated seconds); runtimes with no clock call of their own (advance
    mode None) read the host's local time through it.  ``t0`` is the lab time at which this host was built: absolute-time
    runtimes are given local time (lab time - t0), as a Matterix host built at reset would be.
    """

    def __init__(self, asset_type_id, recipe, instance_id, *, t0=0.0, capture_dir=None):
        self.asset_type_id, self.recipe, self.instance_id = asset_type_id, dict(recipe), instance_id
        self.t0 = float(t0)
        self.local_s = 0.0
        self.scene_updates = {}
        self.clock_counter = 0
        self.clock_errors = []
        self.pending = []                                           # queued clock steps [[dt, count], ...]
        self.sessions = {}                                          # principal -> session object
        self.session_info = {}                                      # principal -> how the session was obtained
        module = import_runtime(self.recipe['module'])
        cls = getattr(module, self.recipe['cls'])

        def scene_adapter(instance, updates):
            # The fast-sim lab is its own scene: there is no physics to lag behind, so every scene target is applied at once and
            # confirmed (runtimes created with a scene adapter - Ziath DP5, Azenta a4S, Quantos Q2 - commit only on True).
            # Matterix BridgedHost returns None here; with a 3D scene Matterix confirms through its own adapter.
            self.scene_updates.setdefault(str(instance), {}).update(dict(updates))
            return True

        kwargs = dict(self.recipe.get('ctor') or {})
        try:
            params = inspect.signature(cls.__init__).parameters
        except (TypeError, ValueError):
            params = {}
        if 'scene_adapter' in params and self.recipe.get('ctor_scene_adapter', True):
            kwargs['scene_adapter'] = scene_adapter
        if self.recipe.get('advance') in R.CLOCK_MODES and 'clock' in params and 'clock' not in kwargs:
            kwargs['clock'] = lambda: self.local_s
        if self.recipe.get('instance_kw'):
            kwargs[self.recipe['instance_kw']] = instance_id
            self.host = cls(**kwargs)
        else:
            self.host = cls(instance_id, **kwargs) if self.recipe.get('instance_in_ctor') else cls(**kwargs)
        create = self.recipe.get('create')
        if create == 'type':
            self.host.create_instance(instance_id, asset_type_id)
        elif create == 'plain':
            self.host.create_instance(instance_id)
        elif create == 'scene':
            self.host.create_instance(instance_id, scene_adapter)
        for method, kw in self.recipe.get('setup', []) or []:
            # "$instance_id" in a setup argument is this host's instance id (as in Matterix runtime_bridge.py)
            kw = {k: (instance_id if v == '$instance_id' else v) for k, v in dict(kw).items()}
            getattr(self.host, method)(**kw)
        if capture_dir is not None and hasattr(self.host, 'panel_capture_dir'):
            Path(capture_dir).mkdir(parents=True, exist_ok=True)
            self.host.panel_capture_dir = str(capture_dir)

    # ------------------------------------------------------------------ sessions
    def open_session(self, principal, visibility):
        """Register ``principal`` with ``visibility`` ('agent' or 'evaluator') and return its session.

        Order of attempts (the runtimes differ): host-is-session runtimes have no principals; recipe flags
        agent_session_default / evaluator_register_positional; register_principal(name, visibility=...), then positional;
        an agent that cannot be registered falls back to the runtime's default public agent session ``session()``.
        """
        if principal in self.sessions:
            return self.sessions[principal]
        h, r = self.host, self.recipe
        how = None
        if r.get('host_is_session'):
            sess, how = h, 'host_is_session'
        elif visibility == 'agent' and r.get('agent_session_default'):
            sess, how = h.session(), 'default_session'
        elif visibility == 'evaluator' and r.get('evaluator_register_positional'):
            h.register_principal(principal)
            sess, how = h.session(principal), 'register_positional'
        elif not hasattr(h, 'register_principal'):
            sess, how = (h.session() if hasattr(h, 'session') else h), 'no_principals'
        else:
            try:
                h.register_principal(principal, visibility=visibility)
                how = 'register_kw'
            except TypeError:
                try:
                    h.register_principal(principal, visibility)
                    how = 'register_positional_vis'
                except TypeError:
                    how = None
            if how is None:
                if visibility == 'agent':
                    sess, how = h.session(), 'default_session'
                else:
                    h.register_principal(principal)
                    sess, how = h.session(principal), 'register_name_only'
            else:
                sess = h.session(principal)
        self.sessions[principal] = sess
        self.session_info[principal] = how
        return sess

    def shared_session(self, principal):
        """True when this principal's session is not its own identity in the runtime (shared default / host object)."""
        return self.session_info.get(principal) in ('host_is_session', 'default_session', 'no_principals')

    # ------------------------------------------------------------------ lab protocol 1.0.0 passthrough
    def describe(self, principal):
        self.flush()
        return self.sessions[principal].describe(instance_id=self.instance_id)

    def read(self, principal, state_ids=None):
        self.flush()
        return self.sessions[principal].read(instance_id=self.instance_id, state_ids=state_ids)

    def invoke(self, principal, capability_id, arguments, request_id):
        self.flush()
        return self.sessions[principal].invoke(instance_id=self.instance_id, capability_id=capability_id, arguments=arguments,
                                               request_id=request_id)

    def write(self, principal, state_id, value, request_id):
        self.flush()
        return self.sessions[principal].write(instance_id=self.instance_id, state_id=state_id, value=value, request_id=request_id)

    def _op_call(self, name, principal, operation_id, request_id=None):
        sess = self.sessions[principal]
        fn = getattr(sess, name, None)
        if fn is None:
            return None
        self.flush()
        params = inspect.signature(fn).parameters
        kw = {}
        if 'instance_id' in params:
            kw['instance_id'] = self.instance_id
        kw['operation_id'] = operation_id
        if name == 'cancel' and 'request_id' in params:
            kw['request_id'] = request_id
        return fn(**kw)

    def poll(self, principal, operation_id):
        """The session's poll(operation_id) or None when the runtime has none."""
        return self._op_call('poll', principal, operation_id)

    def cancel(self, principal, operation_id, request_id):
        return self._op_call('cancel', principal, operation_id, request_id)

    def has_op(self, principal, name):
        return callable(getattr(self.sessions.get(principal), name, None))

    # ------------------------------------------------------------------ clock
    def advance(self, dt):
        """Advance this runtime by dt seconds with its recipe's advance mode (as BridgedHost.advance)."""
        mode, h = self.recipe.get('advance'), self.host
        t_new = self.local_s + dt
        self.clock_counter += 1
        if mode == R.STEP_DT:
            h.step(dt)
        elif mode == R.HOST_STEP_DT:
            h.host_step(dt)
        elif mode == R.HOST_STEP_DT_WORLD:
            h.host_step(dt, world_time_s=t_new)
        elif mode == R.STEP_DT_WORLD:
            h.step(dt, world_time_s=t_new)
        elif mode == R.HOST_STEP_ABS:
            h.host_step(t_new)
        elif mode == R.HOST_ADVANCE_ABS:
            h.host_advance(t_new)
        elif mode == R.HOST_SET_CLOCK_ABS:
            h.host_set_clock(t_new)
        elif mode == R.VICI_SET_TIME:
            h.host_set_time(world_time_s=t_new, request_id='chemlab-clock-%d' % self.clock_counter)
        elif mode == R.CLOCK_HOST_UPDATE:
            self.local_s = t_new                                      # the runtime reads the new time through its clock callable,
            h.host_update()                                           # then processes its timed events once
        self.local_s = t_new                                          # mode None: the runtime reads local_s via its clock callable

    def defer(self, dt, count=1):
        """Queue clock steps (the lab clock is applied lazily: the same steps run, in order, before the next access)."""
        if count <= 0 or dt <= 0:
            return
        if self.pending and self.pending[-1][0] == dt:
            self.pending[-1][1] += count
        else:
            self.pending.append([dt, count])

    def flush(self):
        """Run the queued clock steps.  A step that raises is recorded and its time still passes (as in Matterix)."""
        while self.pending:
            dt, n = self.pending[0]
            for _ in range(n):
                try:
                    self.advance(dt)
                except Exception as exc:                                # noqa: BLE001
                    self.local_s += dt
                    self.clock_errors.append('t=%.3f s %s: %s' % (self.t0 + self.local_s, type(exc).__name__, exc))
                    del self.clock_errors[:-20]
                self.pending[0][1] -= 1
            self.pending.pop(0)

    def runtime_time(self):
        """The time the runtime itself reports (host.time_s / t / read clock), or None."""
        for attr in ('time_s', 't', '_time_s', 'world_time_s', 'now_s'):
            v = getattr(self.host, attr, None)
            if callable(v):
                continue
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return None

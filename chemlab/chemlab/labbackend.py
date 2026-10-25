"""Layer 1 for the generic agent layer: a simulated lab of 1 to 203 instruments with one clock.

Each instrument is a Chemistry-Sim runtime host built from its Matterix recipe (recipes.py, hosts.py).  The backend speaks
only the lab protocol 1.0.0 to the runtimes (describe / read / invoke / write / poll / cancel) plus the clock:

* principals: every caller gets its own agent principal ``chemlab_agent_<caller>`` in each runtime; the lab's
  ``chemlab_operator`` (agent level) performs the operator gestures of the host set-up (power on, home screen); the lab's
  ``chemlab_evaluator`` does the world set-up and is used by checkers.  The evaluator is never reachable from callers.
* one clock: ``advance(dt)`` moves every runtime forward in lockstep steps of ``step_s`` with its recipe's advance mode.
* host set-up (from the lab config): steps in the release bring-up format (capability + arguments or "contract_example",
  ``evaluator`` flag, ``advance_s``) plus trusted ``host`` method calls.  The set-ups are aligned so that every instrument
  finishes its set-up at the same lab time T0, which is where the lab clock starts.

What the agent may see is decided here once per instrument (``AgentView``): contract visibility 'agent' (and, for
capabilities, availability 'implemented' and present in the runtime's own agent describe).  Everything else is hidden.
"""
import copy
import importlib
import json
import math
import re
import shutil
import threading
from pathlib import Path

from . import recipes as R
from .hosts import CHEMSIM_ROOT, RuntimeHost, contract_index

CHEMLAB_ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = CHEMLAB_ROOT / 'configs'
DEFAULT_CONFIG = CONFIG_DIR / 'all203.json'
OPERATOR, EVALUATOR = 'chemlab_operator', 'chemlab_evaluator'
CODE_RE = re.compile(r'^[A-Z][A-Z0-9_]{1,63}$')
ID_RE = re.compile(r'^[a-z0-9][a-z0-9_]{0,63}$')


class ConfigError(Exception):
    pass


def agent_principal(caller):
    return 'chemlab_agent_%s' % caller


def load_config(path_or_dict=None):
    if isinstance(path_or_dict, dict):
        cfg = copy.deepcopy(path_or_dict)
    else:
        p = Path(path_or_dict or DEFAULT_CONFIG)
        cfg = json.loads(p.read_text(encoding='utf-8'))
        cfg.setdefault('source', str(p))
    if not isinstance(cfg.get('instruments'), list) or not cfg['instruments']:
        raise ConfigError('the lab config needs a non-empty "instruments" list')
    seen = set()
    for e in cfg['instruments']:
        if 'asset_type_id' not in e:
            raise ConfigError('every instrument needs an asset_type_id')
        e.setdefault('id', e['asset_type_id'].split('.', 1)[1])
        if not ID_RE.match(e['id']) or e['id'] in seen:
            raise ConfigError('bad or repeated instrument id %r (lower-case letters, digits, _)' % e['id'])
        seen.add(e['id'])
    return cfg


def _exc_response(exc, request_id=None, side_effects='unknown'):
    """A runtime raised instead of answering: an envelope with the exception kept (code when it carries one)."""
    text = str(exc)
    code = text if CODE_RE.match(text or '') else 'RUNTIME_EXCEPTION'
    status = 'rejected' if code != 'RUNTIME_EXCEPTION' else 'failed'
    return {'protocol_version': '1.0.0', 'request_id': request_id, 'operation_id': None, 'status': status, 'result': {},
            'observations': {}, 'raised': type(exc).__name__,
            'error': {'code': code, 'message': text if code == 'RUNTIME_EXCEPTION' else None, 'retryable': False,
                      'side_effects': side_effects}}


def _norm_caps(caps):
    if isinstance(caps, dict):
        return caps
    if isinstance(caps, list):
        return {c.get('capability_id') or c.get('id'): c for c in caps if isinstance(c, dict)}
    return {}


class Redactor:
    """Removes a set of ids from agent output: dict keys that are such ids are dropped, mentions in text become [hidden]."""

    def __init__(self, hidden):
        self.hidden = sorted(h for h in hidden if h)
        self.hidden_set = set(self.hidden)
        self._hidden_re = None
        if self.hidden:
            self._hidden_re = re.compile(r'(?<![A-Za-z0-9_.])(%s)(?![A-Za-z0-9_])' % '|'.join(re.escape(h) for h in
                                                                                          sorted(self.hidden, key=len, reverse=True)))

    def redact(self, obj, found=None, allow=()):
        """``allow``: ids the caller typed itself (echoing them back reveals nothing, and keeps hidden == nonexistent)."""
        if isinstance(obj, dict):
            out = {}
            for k, v in obj.items():
                if isinstance(k, str) and k in self.hidden_set and k not in allow:
                    if found is not None:
                        found.add(k)
                    continue
                out[k] = self.redact(v, found, allow)
            return out
        if isinstance(obj, list):
            return [self.redact(v, found, allow) for v in obj]
        if isinstance(obj, str) and self._hidden_re is not None:
            def sub(m):
                if m.group(1) in allow:
                    return m.group(1)
                if found is not None:
                    found.add(m.group(1))
                return '[hidden]'
            return self._hidden_re.sub(sub, obj)
        return obj


class AgentView(Redactor):
    """What one instrument shows to agents, decided from its contract and the runtime's own agent describe."""

    def __init__(self, contract, agent_describe, evaluator_describe):
        caps = contract['capabilities']
        slots = contract['state_slots']
        rcaps = _norm_caps((agent_describe or {}).get('capabilities'))
        ecaps = _norm_caps((evaluator_describe or {}).get('capabilities'))
        eslots = (evaluator_describe or {}).get('state_slots') or {}
        self.callable = {cid for cid, c in caps.items() if c.get('visibility') == 'agent'}
        self.capabilities = {cid: c for cid, c in caps.items()
                             if c.get('visibility') == 'agent' and c.get('availability') == 'implemented' and (not rcaps or cid in rcaps)}
        self.unsupported = {cid: c.get('availability') for cid, c in caps.items()
                            if c.get('visibility') == 'agent' and c.get('availability') != 'implemented'}
        # the agent slots are the contract's; reads are filtered to them.  Runtime and contract slot ids are identical for all 203.
        self.slots = {sid: s for sid, s in slots.items() if s.get('visibility') == 'agent'}   # contract id -> spec
        visible = set(self.callable) | set(self.slots)
        hidden = {cid for cid, c in caps.items() if c.get('visibility') != 'agent'}
        hidden |= {sid for sid, s in slots.items() if s.get('visibility') != 'agent'}
        hidden |= set(ecaps) | set(eslots)
        self.visible = visible
        Redactor.__init__(self, hidden - visible)


class LabInstrument:
    def __init__(self, entry, recipe, contract_path, panel):
        self.entry = entry
        self.id = entry['id']
        self.asset_type_id = entry['asset_type_id']
        self.name = entry.get('name') or self.id
        self.brand, self.model, self.domain = entry.get('brand'), entry.get('model'), entry.get('domain')
        self.recipe = dict(recipe)
        self.recipe.update(entry.get('recipe') or {})
        self.contract_path = Path(contract_path)
        self.contract = json.loads(self.contract_path.read_text(encoding='utf-8'))
        self.panel = panel                       # {'spec': dict, 'main_display': str} or None
        self.host = None
        self.scene = None                        # optional host-side scene from the config entry ("scene": "module:Class")
        self.view = None
        self.setup_report = []


class LabBackend:
    """All instruments of one lab, their principals and the lab clock.  Thread-safe (one lock)."""

    def __init__(self, config=None, *, run_dir, callers=('agent',), step_s=None, setup=True):
        self.config = load_config(config)
        self.run_dir = Path(run_dir).resolve()
        self.capture_root = self.run_dir / 'captures'
        self.step_s = float(step_s or self.config.get('step_s') or 1.0)
        self.callers = list(callers)
        self.time_s = 0.0
        self._lock = threading.RLock()
        recipes = R.all_recipes(self.config.get('recipes_path'))
        index = contract_index()
        self.instruments = {}
        for e in self.config['instruments']:
            atid = e['asset_type_id']
            if atid not in recipes and not e.get('recipe'):
                raise ConfigError('no Matterix recipe for %s' % atid)
            if atid not in index:
                raise ConfigError('no contract for %s' % atid)
            self.instruments[e['id']] = LabInstrument(e, recipes.get(atid, {}), index[atid], None)
            self.instruments[e['id']].panel = _panel_info(e, self.instruments[e['id']].contract)
        for inst in self.instruments.values():
            self._build(inst, t0=0.0)
        hidden_any, visible_any = set(), set()
        for inst in self.instruments.values():
            hidden_any |= inst.view.hidden_set
            visible_any |= inst.view.visible
        # ids hidden on some instrument and visible on none: never shown in any output (contract prose of one instrument can
        # mention another instrument's evaluator capability, e.g. "sim.step")
        self.global_view = Redactor(hidden_any - visible_any)
        if setup:
            self._run_setups()

    # ------------------------------------------------------------------ building
    def _build(self, inst, t0):
        inst.host = RuntimeHost(inst.asset_type_id, inst.recipe, inst.entry.get('instance_id') or inst.id, t0=t0,
                                capture_dir=self.capture_root / inst.id)
        rt = getattr(inst.host, 'host', None)
        if inst.recipe.get('advance') is None and callable(getattr(rt, 'host_update', None)):
            # no advance mode, but the runtime keeps timed events that only host_update() processes (Agilent 8697 sequences,
            # diagnostics, tray calibration; DR6000 also syncs on access): run it after every clock step, as its firmware would.
            # A recipe with advance "clock_host_update" calls host_update() itself (hosts.RuntimeHost.advance): no hook then.
            orig, host = inst.host.advance, inst.host

            def advance(dt, _orig=orig, _rt=rt, _host=host):
                _orig(dt)                                                   # the clock moved; an update error must not move it again
                try:
                    _rt.host_update()
                except Exception as exc:                                    # noqa: BLE001 - recorded like a failed clock step
                    _host.clock_errors.append('t=%.3f s host_update %s: %s' % (_host.t0 + _host.local_s, type(exc).__name__, exc))
                    del _host.clock_errors[:-20]
            inst.host.advance = advance
            inst.clock_hook = 'host_update'
        else:
            inst.clock_hook = None
        inst.scene = None
        if inst.entry.get('scene'):                                         # trusted host-side world (e.g. the Flex deck, compile layer)
            mod, _, cls = inst.entry['scene'].partition(':')
            inst.scene = getattr(importlib.import_module(mod), cls)(self, inst)
        for c in self.callers:
            inst.host.open_session(agent_principal(c), 'agent')
        inst.host.open_session(OPERATOR, 'agent')
        inst.host.open_session(EVALUATOR, 'evaluator')
        a = self._safe(lambda: inst.host.describe(agent_principal(self.callers[0])) if self.callers else None)
        ev = self._safe(lambda: inst.host.describe(EVALUATOR))
        inst.view = AgentView(inst.contract, a if isinstance(a, dict) else None, ev if isinstance(ev, dict) else None)

    @staticmethod
    def _safe(fn):
        try:
            return fn()
        except Exception as exc:                                            # noqa: BLE001
            return {'raised': type(exc).__name__, 'message': str(exc)}

    def add_caller(self, caller):
        with self._lock:
            if caller not in self.callers:
                self.callers.append(caller)
                for inst in self.instruments.values():
                    inst.host.open_session(agent_principal(caller), 'agent')

    @staticmethod
    def _step_sig(inst, st):
        """A set-up step's identity for de-duplication ("contract_example" resolved, so a world that spells the example out
        matches the default step that names it)."""
        d = {k: v for k, v in st.items() if k not in ('default', 'why', 'source')}
        if d.get('arguments') == 'contract_example':
            d['arguments'] = (inst.contract['capabilities'].get(d.get('capability')) or {}).get('example_arguments') or {}
        if isinstance(d.get('kwargs'), dict):
            iid = inst.entry.get('instance_id') or inst.id
            d['kwargs'] = {k: (iid if v == '$instance' else v) for k, v in d['kwargs'].items()}
        return json.dumps(d, sort_keys=True, default=str)

    def _setup_steps(self, inst):
        """The instrument's set-up steps.  Steps tagged ``"default": GROUP`` are the default lab's (make_config's default world:
        utilities, accessories, consumables, reference standards).  A task world can drop them all (entry
        ``"default_world": false``) or by group (a step ``{"replace_default": [GROUP, ...]}``); a world step identical to a kept
        default step is not run a second time (worlds written before the default lab re-did the same set-up)."""
        raw = [dict(st) for st in inst.entry.get('setup') or []]
        off = inst.entry.get('default_world') is False
        replace = set()
        for st in raw:
            replace |= set(st.get('replace_default') or [])
        kept = {}
        steps = []
        for st in raw:
            if 'replace_default' in st:
                continue
            if 'default' in st:
                if off or st['default'] in replace:
                    continue
                sig = self._step_sig(inst, st)
                kept[sig] = kept.get(sig, 0) + 1
                steps.append(st)
                continue
            sig = self._step_sig(inst, st)
            if kept.get(sig):
                kept[sig] -= 1                                          # the world re-does a default step: once is enough
                continue
            steps.append(st)
        return steps

    def _run_setups(self):
        """Run every instrument's set-up on the one lab clock, all starting at t = 0 (world set-up calls usually must come
        before any time passes).  ``advance_s`` makes that instrument wait while the clock runs for everyone; the lab is
        ready at T0 = the end of the longest set-up."""
        plans = {iid: self._setup_steps(inst) for iid, inst in self.instruments.items()}
        pos = {iid: 0 for iid in plans}
        resume = {iid: 0.0 for iid in plans}
        while True:
            for iid, inst in self.instruments.items():
                steps = plans[iid]
                while pos[iid] < len(steps) and resume[iid] <= self.time_s + 1e-9:
                    st = steps[pos[iid]]
                    pos[iid] += 1
                    if 'advance_s' in st:
                        resume[iid] = self.time_s + float(st['advance_s'])
                    else:
                        self._setup_step(inst, st, pos[iid])
            waiting = [resume[i] for i in plans if resume[i] > self.time_s + 1e-9]
            if not waiting:
                break
            self.advance(min(waiting) - self.time_s)
        self.t_setup = self.time_s

    def _setup_step(self, inst, st, n):
        inst.host.flush()
        if 'host' in st:
            iid = inst.entry.get('instance_id') or inst.id
            kwargs = {k: (iid if v == '$instance' else v) for k, v in (st.get('kwargs') or {}).items()}
            try:
                getattr(inst.host.host, st['host'])(**kwargs)
                inst.setup_report.append({'host': st['host'], 'status': 'ok'})
            except Exception as exc:                                        # noqa: BLE001
                inst.setup_report.append({'host': st['host'], 'status': 'raised', 'error': '%s: %s' % (type(exc).__name__, exc)})
            return
        cap = st['capability']
        args = st.get('arguments')
        if args == 'contract_example':
            args = (inst.contract['capabilities'].get(cap) or {}).get('example_arguments') or {}
        principal = EVALUATOR if st.get('evaluator') else OPERATOR
        r = self._invoke_raw(inst, principal, cap, copy.deepcopy(args or {}), 'chemlab-setup-%s-%d' % (inst.id, n))
        inst.setup_report.append({'capability': cap, 'principal': principal, 'time_s': self.time_s, 'status': r.get('status'),
                                  'code': (r.get('error') or {}).get('code')})

    def _advance_one(self, inst, seconds):
        """Advance one host only (fresh copies built by reset_instrument)."""
        n_full = int(math.floor(seconds / self.step_s + 1e-9))
        rest = seconds - n_full * self.step_s
        inst.host.defer(self.step_s, n_full)
        if rest > 1e-9:
            inst.host.defer(rest)
        inst.host.flush()

    def reset_instrument(self, iid, setup=False):
        """Trusted host / tests: rebuild one instrument from its recipe (a fresh copy) at the current lab time."""
        with self._lock:
            inst = self.instruments[iid]
            self._build(inst, t0=self.time_s)
            inst.setup_report = []
            if setup:
                for n, st in enumerate(self._setup_steps(inst)):
                    if 'advance_s' in st:
                        self._advance_one(inst, float(st['advance_s']))
                    else:
                        self._setup_step(inst, st, n + 1)
            return inst

    # ------------------------------------------------------------------ the clock
    def now(self):
        return self.time_s

    def advance(self, seconds, until=None):
        """Advance the lab clock by ``seconds`` in lockstep steps of step_s (the last one shorter).  Every instrument gets the
        same steps with its recipe's advance mode; they are applied lazily (before the next access to that instrument), or
        at once with "clock": "eager" in the config.  ``until()`` (optional) is checked after every step and stops early when
        true.  Returns the seconds advanced."""
        with self._lock:
            total = float(seconds)
            if total <= 0:
                return 0.0
            n_full = int(math.floor(total / self.step_s + 1e-9))
            rest = total - n_full * self.step_s
            if rest < 1e-9:
                rest = 0.0
            steps = [self.step_s] * n_full + ([rest] if rest > 0 else [])
            start = self.time_s
            if until is None:
                for inst in self.instruments.values():
                    inst.host.defer(self.step_s, n_full)
                    if rest > 0:
                        inst.host.defer(rest)
                done = total
                self.time_s = start + total
            else:
                done = 0.0
                for d in steps:
                    for inst in self.instruments.values():
                        inst.host.defer(d)
                    done += d
                    self.time_s = start + done
                    if until():
                        break
            if self.config.get('clock') == 'eager':
                for inst in self.instruments.values():
                    inst.host.flush()
            return done

    def flush(self, iid=None):
        """Apply queued clock steps (one instrument, or all)."""
        with self._lock:
            for inst in ([self.instruments[iid]] if iid else self.instruments.values()):
                inst.host.flush()

    def clock_errors(self):
        return {iid: list(inst.host.clock_errors) for iid, inst in self.instruments.items() if inst.host.clock_errors}

    def runtime_clock(self, iid):
        """The time the runtime reports (read clock of the evaluator session), for checks."""
        inst = self.instruments[iid]
        r = self._safe(lambda: inst.host.read(EVALUATOR))
        t = (r.get('clock') or {}).get('time_s') if isinstance(r, dict) else None
        if t is None:
            t = inst.host.runtime_time()
        return None if t is None else inst.host.t0 + float(t)

    # ------------------------------------------------------------------ protocol for callers (agent principals only)
    def _principal(self, caller):
        if caller not in self.callers:
            raise KeyError('unknown caller %r' % caller)
        return agent_principal(caller)

    def _invoke_raw(self, inst, principal, cap, args, request_id):
        try:
            r = inst.host.invoke(principal, cap, args, request_id)
        except Exception as exc:                                            # noqa: BLE001
            return _exc_response(exc, request_id)
        return r if isinstance(r, dict) else {'status': 'failed', 'raised': 'BadResponse', 'result': {},
                                              'error': {'code': 'RUNTIME_EXCEPTION', 'message': 'non-dict response', 'side_effects': 'unknown'}}

    def describe(self, caller, iid):
        with self._lock:
            inst = self.instruments[iid]
            return self._safe(lambda: inst.host.describe(self._principal(caller)))

    def read(self, caller, iid, state_ids=None):
        with self._lock:
            inst = self.instruments[iid]
            try:
                return inst.host.read(self._principal(caller), state_ids)
            except Exception as exc:                                        # noqa: BLE001
                return _exc_response(exc, None, 'none')

    def invoke(self, caller, iid, cap, args, request_id):
        with self._lock:
            return self._invoke_raw(self.instruments[iid], self._principal(caller), cap, args, request_id)

    def write(self, caller, iid, state_id, value, request_id):
        with self._lock:
            inst = self.instruments[iid]
            try:
                r = inst.host.write(self._principal(caller), state_id, value, request_id)
            except Exception as exc:                                        # noqa: BLE001
                return _exc_response(exc, request_id)
            return r

    def poll(self, caller, iid, operation_id):
        with self._lock:
            inst = self.instruments[iid]
            p = self._principal(caller)
            if not inst.host.has_op(p, 'poll'):
                return None
            try:
                return inst.host.poll(p, operation_id)
            except Exception as exc:                                        # noqa: BLE001
                return _exc_response(exc, None, 'none')

    def cancel(self, caller, iid, operation_id, request_id):
        with self._lock:
            inst = self.instruments[iid]
            p = self._principal(caller)
            if not inst.host.has_op(p, 'cancel'):
                return None
            try:
                return inst.host.cancel(p, operation_id, request_id)
            except Exception as exc:                                        # noqa: BLE001
                return _exc_response(exc, request_id)

    def capture_file(self, response):
        """Path of the PNG a successful panel.capture response refers to (host side), or None."""
        img = ((response or {}).get('result') or {}).get('image') or {}
        p = img.get('path')
        return Path(p) if isinstance(p, str) and Path(p).is_file() else None

    # ------------------------------------------------------------------ trusted host side (never reachable from callers)
    def evaluator_read(self, iid, state_ids=None):
        with self._lock:
            return self.instruments[iid].host.read(EVALUATOR, state_ids)

    def evaluator_invoke(self, iid, cap, args, request_id):
        with self._lock:
            return self._invoke_raw(self.instruments[iid], EVALUATOR, cap, args, request_id)

    def host_call(self, iid, method, **kwargs):
        if method.startswith('_') or method in ('session', 'register_principal', 'describe', 'read', 'invoke', 'write'):
            raise PermissionError(method)
        with self._lock:
            self.instruments[iid].host.flush()
            return getattr(self.instruments[iid].host.host, method)(**kwargs)


def _panel_info(entry, contract):
    """{'spec': panel_ui.json content, 'main_display': id} for an instrument with a front-panel model, else None.

    The spec is the contract's ``panel_ui`` artifact (extension chemistry_lab.panel_ui); the main display comes from the lab
    config (the release's panel_instruments.json), else the first display of the spec.
    """
    ext = (contract.get('extensions') or {}).get('chemistry_lab.panel_ui')
    if not ext:
        return None
    art = (contract.get('artifacts') or {}).get(ext.get('artifact_id') or 'panel_ui') or {}
    path = CHEMSIM_ROOT / art['path'] if art.get('path') else None
    spec = json.loads(path.read_text(encoding='utf-8')) if path is not None and path.exists() else {}
    shots = ((((contract.get('capabilities') or {}).get('panel.capture') or {}).get('input_schema') or {}).get('properties') or {}) \
        .get('display_id', {}).get('enum') or list(spec.get('displays') or {})
    main = (entry.get('panel') or {}).get('main_display')
    if main not in shots:                       # e.g. "all" (LED-only panels in the release preview): the first display instead
        main = shots[0] if shots else None
    return {'spec': spec, 'main_display': main}


def copy_into(src, work_dir, rel):
    """Copy a host file into the agent's work dir at the relative path rel (must stay inside).  Returns the target path."""
    work = Path(work_dir).resolve()
    dst = (work / rel).resolve()
    if work not in dst.parents:
        raise ValueError('outside the work directory')
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)
    return dst


def finite(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)

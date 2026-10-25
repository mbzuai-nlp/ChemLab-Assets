"""One class-verb request: the caller's agent view of the instrument, native calls with contract validation, read-back and
refusal explanation, the dry-run plan, waiting on the lab clock, and the list of steps done (for partial-progress reports).

Everything goes through ``core.backend`` with the caller's own agent principal, exactly like `chemlab call` and `chemlab read`;
every native call and clock advance is recorded in the request's ``backend_calls`` (the log, and the lowering tests).
"""
import copy

from ..contract import validate
from .errors import CompileError, invalid, refused, unexpected

OK = ('succeeded', 'accepted', 'running')


class Ctx:
    def __init__(self, adapter, core, req, verb=None, parsed=None, dry_run=False):
        self.ad, self.core, self.req = adapter, core, req
        self.inst = req.instrument
        self.caller = req.caller
        self.verb = verb
        self.args = parsed
        self.dry_run = bool(dry_run)
        self.plan, self.done, self.notes, self.checks, self.unchecked = [], [], [], [], []
        self.changed = False
        self.view, self.quality = None, {}
        self.waited_s = 0.0
        self.shadow = None
        self._n = 0
        self.lab = core.backend.instruments[self.inst]
        self.refresh()

    # ------------------------------------------------------------------ state
    def refresh(self):
        """Read every agent-visible slot (agent principal).  In a dry run the shadow view is kept after the first read."""
        if self.dry_run and self.view is not None:
            return self.view
        r = self.core.backend.read(self.caller, self.inst, None)
        self.core._record(self.req.calls, self.inst, 'read', state_ids=None, status=r.get('status'),
                          code=(r.get('error') or {}).get('code'))
        if r.get('status') != 'succeeded':
            raise unexpected('READ_FAILED', 'reading %s failed (%s).' % (self.inst, (r.get('error') or {}).get('code')),
                             'retry; if it persists the instrument needs attention.', changed='yes' if self.changed else 'no',
                             done=self.done)
        obs = r.get('observations') or {}
        self.view = {k: (o.get('value') if isinstance(o, dict) else o) for k, o in obs.items()}
        self.quality = {k: (o.get('quality') if isinstance(o, dict) else None) for k, o in obs.items()}
        self.view['_time_s'] = self.core.backend.now()
        return self.view

    def v(self, slot, default=None):
        """Value of a slot by contract id or runtime id (case aliases are resolved)."""
        if slot is None:
            return default
        if slot in self.view:
            return self.view[slot]
        low = {k.lower(): k for k in self.view}
        k = low.get(slot.lower())
        return self.view[k] if k is not None else default

    def now(self):
        return self.core.backend.now()

    # ------------------------------------------------------------------ native calls
    def cap_spec(self, cap):
        return self.lab.contract['capabilities'].get(cap) or {}

    def call(self, cap, args, step, *, refusal=None, readback=None, shadow=None, query=False):
        """Send one capability call as the caller.  ``step``: plain text for plans and reports.  ``refusal(response)`` -> a
        CompileError for a refused call (or None for the class's default explanation).  ``readback(view)`` -> problem text or None.
        ``shadow(view)`` updates the dry-run view.  ``query``: a read-only capability (does not count as a change)."""
        v = self.lab.view
        if cap not in v.capabilities:
            raise unexpected('ADAPTER_BUILT_INVALID_CALL', 'the %s adapter tried %s, which %s does not offer.' % (self.ad.CLASS, cap, self.inst),
                             'report this; nothing was sent.', changed='yes' if self.changed else 'no', done=self.done)
        problems = validate((self.cap_spec(cap).get('input_schema') or {'type': 'object', 'properties': {}}), args, cap)
        if problems:
            raise unexpected('ADAPTER_BUILT_INVALID_CALL', 'the %s adapter built an invalid %s call: %s.' % (self.ad.CLASS, cap, '; '.join(problems)),
                             'report this; nothing was sent.', changed='yes' if self.changed else 'no', done=self.done)
        if self.dry_run and query:
            return {'status': 'succeeded', 'result': None, 'dry_run': True}
        if self.dry_run:
            self.plan.append({'capability': cap, 'arguments': copy.deepcopy(args), 'step': step})
            if shadow:
                shadow(self.view)
            self.ad.shadow(self, cap, args)
            return {'status': 'succeeded', 'result': {}}
        self._n += 1
        rid = '%s.%d' % (self.req.request_id, self._n)
        r = self.core.backend.invoke(self.caller, self.inst, cap, copy.deepcopy(args), rid)
        err = r.get('error') or {}
        self.core._record(self.req.calls, self.inst, 'invoke', capability=cap, arguments=copy.deepcopy(args), request_id=rid,
                          status=r.get('status'), code=err.get('code'), operation_id=r.get('operation_id'))
        if r.get('status') not in OK:
            exc = refusal(r) if refusal else None
            if exc is None:
                exc = self.ad.explain_refusal(self, cap, args, r)
            self.fail(exc, cap, r)
        if not query:
            self.changed = True
            self.done.append(step)
        self.refresh()
        problem = readback(self.view) if readback else None
        if problem:
            raise unexpected('READBACK_MISMATCH', 'after %s, %s reports %s.' % (step, self.inst, problem),
                             'check `chemlab run %s status`; the instrument is not in the state the command should have produced.' % self.inst,
                             changed='unknown', done=self.done)
        return r

    def fail(self, exc, cap=None, response=None):
        """Raise ``exc`` with the steps done so far and what the instrument said."""
        if not isinstance(exc, CompileError):
            raise exc
        if response is not None and cap is not None:
            err = response.get('error') or {}
            contract = self.ad.contract_error_text(self, cap, err.get('code'))
            exc.backend = {'capability': cap, 'status': response.get('status'), 'code': err.get('code'),
                           'message': err.get('message'), 'contract': contract}
            side = err.get('side_effects')
            if side and side not in ('none',):
                exc.side_effects = side
            if side == 'unknown':
                exc.changed = 'unknown'
        exc.done = list(self.done) + [d for d in exc.done if d not in self.done]
        if exc.changed == 'no' and self.changed:
            exc.changed = 'yes'
        raise exc

    # ------------------------------------------------------------------ time
    def advance(self, seconds, until=None, step='wait', poll_s=1.0):
        """Let lab time pass (all instruments), optionally until ``until(view)`` is true (checked every ``poll_s`` of lab time).
        Returns the seconds waited."""
        if seconds <= 0:
            return 0.0
        if self.dry_run:
            self.plan.append({'capability': None, 'arguments': {'seconds': seconds}, 'step': '%s (up to %s of lab time)' % (step, _fmt_s(seconds))})
            return 0.0
        t0 = self.now()
        cond = None
        if until is not None:
            last = [t0]

            def cond():
                if self.now() - last[0] + 1e-9 < poll_s:
                    return False
                last[0] = self.now()
                return bool(until(self.refresh()))
        self.core.backend.advance(seconds, until=cond)
        waited = self.now() - t0
        self.core._record(self.req.calls, None, 'advance', seconds=waited)
        self.waited_s += waited
        self.refresh()
        return waited

    def check(self, ok, code, message, fix, kind='refused', label=None, **kw):
        """A class-level precondition: raise a refused (or usage) error when not ok; else note it for the dry-run report."""
        if not ok:
            exc = (invalid if kind == 'usage' else refused)(code, message, fix, **kw)
            self.fail(exc)
        self.checks.append(label or code.lower().replace('_', ' '))


def _fmt_s(s):
    from .units import fmt_s
    return fmt_s(s)

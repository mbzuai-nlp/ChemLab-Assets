"""Job ids for long class-level actions: `chemlab status job-N`, `chemlab wait job-N [--max S]`, `chemlab cancel job-N`.

A job belongs to the caller that started it (others get "no job of yours").  Kinds:

Three kinds of long action exist in the 203 contracts, one job class each:

* ``OperationJob`` - an asynchronous capability (execution mode not synchronous) answered with an operation_id; progress and
  the end come from the session's poll(operation_id), cancel from its cancel(operation_id).  Most ``*.start`` capabilities.
* ``StateJob`` - a run-type state machine whose progress is only in state slots (Flex protocol runs, furnace programmes, GC
  ``run.start``, ``cycle.start``): done when a slot reaches a done value; cancel sends the class's stop call.
* ``ConditionJob`` - a timed state change with no run object (heat to a set point, wait for a stable reading): done when a
  condition on the state holds; cancelling only stops the waiting unless the class gives an ``on_cancel`` call.
* class-specific jobs (the liquid class's protocol runs) subclass ``Job``.

The registry lives on the generic core (``core.class_jobs``); the generic `status` / `wait` / `cancel` commands route job-N
handles here (adapters.route_job).  Waiting moves the lab clock, like `chemlab wait OP`.
"""
from .ctx import Ctx
from .errors import invalid, refused
from .units import fmt_s

_RUNNING = ('running', 'waiting', 'setup', 'paused', 'awaiting_recovery', 'accepted', 'queued', 'pending', 'started', 'starting',
            'in_progress', 'stopping', 'cancelling', 'heating', 'cooling', 'ramp', 'hold', 'settling',
            # run phases met in the runtimes (explicit, so operation results naming them count as running too)
            'injecting', 'run', 'post', 'condition', 'blank', 'equilibrating', 'initial_delay', 'spacing', 'ready_wait', 'incubating',
            'measuring', 'acquiring', 'sampling', 'conditioning', 'testing', 'purging', 'rinsing', 'washing', 'drying', 'loading',
            'ramping', 'holding', 'preheating', 'priming', 'flushing', 'degassing', 'evacuating', 'venting', 'scanning', 'busy')
# states that end a job; any other text state counts as running (unknown run phases of a member keep the job alive)
TERMINAL = frozenset((
    'completed', 'complete', 'succeeded', 'success', 'successful', 'done', 'finished', 'ended', 'end', 'ok', 'failed', 'failure', 'error',
    'errored', 'fault', 'faulted', 'cancelled', 'canceled', 'aborted', 'stopped', 'halted', 'terminated', 'killed', 'interrupted',
    'rejected', 'unsupported', 'timeout', 'timed_out', 'expired', 'lost', 'unknown', 'none', 'null', 'idle', 'ready', 'standby', 'off',
    'no_sequence', 'not_started', 'locked', 'measured', 'acquired', 'analysed', 'analyzed', 'reached', 'stable', 'unstable', 'empty',
    'gave_up'))


def is_running(st):
    """True while a job with this status is still going: a known running state, or any text state that is not terminal."""
    if not isinstance(st, str) or not st.strip():
        return False
    s = st.strip().lower()
    return s in _RUNNING or s not in TERMINAL


class _Running(tuple):
    """The known running states; ``st in RUNNING`` also holds for unknown non-terminal states (see is_running)."""

    def __contains__(self, st):
        return is_running(st)


RUNNING = _Running(_RUNNING)


def op_status(r):
    """An operation's state from a poll answer: the protocol status (accepted / running / succeeded / failed / cancelled); a
    'succeeded' answer whose result names a known running phase (operation_status / phase / state / status) counts as that phase."""
    st = r.get('status')
    res = r.get('result') if isinstance(r.get('result'), dict) else {}
    if st == 'succeeded':
        for k in ('operation_status', 'phase', 'state', 'status'):
            if isinstance(res.get(k), str) and res[k].strip().lower() in _RUNNING:
                return res[k]
    return st


def registry(core):
    reg = getattr(core, 'class_jobs', None)
    if reg is None:
        reg = core.class_jobs = {}
        core.class_job_seq = 0
    return reg


def new_handle(core):
    registry(core)
    core.class_job_seq += 1
    return 'job-%d' % core.class_job_seq


class Job:
    kind = 'job'

    def __init__(self, core, adapter, req, description):
        self.handle = new_handle(core)
        self.adapter, self.caller, self.instrument = adapter, req.caller, req.instrument
        self.description = description
        self.started_s = core.backend.now()
        self.state = 'running'
        registry(core)[self.handle] = self

    # subclasses: poll(ctx) -> (status, progress text, json dict); cancel(ctx) -> text
    def poll(self, ctx):
        return self.state, '', {}

    def cancel(self, ctx):
        self.state = 'cancelled'
        return 'stopped waiting'

    def ctx(self, core, req):
        req.instrument = self.instrument
        return Ctx(self.adapter, core, req)

    def max_wait_s(self):
        return 3600.0

    # ------------------------------------------------------------------ commands
    def status_reply(self, core, req):
        ctx = self.ctx(core, req)
        st, text, js = self.poll(ctx)
        out = {('%s_%s' % (self.kind, k) if k in ('status', 'job') else k): v for k, v in js.items()}   # e.g. run_status: completed
        out.update({'job': self.handle, 'instrument': self.instrument, 'kind': self.kind, 'description': self.description, 'status': st,
                    'done': st not in RUNNING, 'started_s': self.started_s, 'time_s': core.backend.now()})
        line = '%s %s: %s (%s, started at t = %.1f s, now t = %.1f s)' % (self.handle, self.description, st, self.instrument,
                                                                         self.started_s, core.backend.now())
        return {'text': line + ('\n  ' + text if text else '') + '\n', 'json': out}

    def wait_reply(self, core, req, max_s=None):
        ctx = self.ctx(core, req)
        mx = min(float(max_s if max_s is not None else self.max_wait_s()), getattr(core, 'max_wait_s', 86400.0))
        st, _, _ = self.poll(ctx)
        waited = 0.0
        if st in RUNNING:
            state = {'st': st}

            def until(view):
                state['st'] = self.poll(ctx)[0]
                return state['st'] not in RUNNING
            waited = ctx.advance(mx, until=until, step='wait for %s' % self.handle, poll_s=self.poll_s())
        st, text, js = self.poll(ctx)
        done = st not in RUNNING
        out = {('%s_%s' % (self.kind, k) if k in ('status', 'job') else k): v for k, v in js.items()}
        out.update({'job': self.handle, 'status': st, 'done': done, 'waited_s': waited, 'time_s': core.backend.now()})
        line = '%s %s after %s of waiting (t = %.1f s)%s' % (self.handle, st, fmt_s(waited), core.backend.now(),
                                                            '' if done else '; still running (gave up after --max %s)' % fmt_s(mx))
        return {'text': line + ('\n  ' + text if text else '') + '\n', 'json': out}

    def cancel_reply(self, core, req):
        ctx = self.ctx(core, req)
        st, _, _ = self.poll(ctx)
        if st not in RUNNING:
            raise refused('JOB_FINISHED', '%s is already %s.' % (self.handle, st), 'nothing to cancel; see `chemlab status %s`.' % self.handle)
        what = self.cancel(ctx)
        return {'text': 'ok: %s cancelled (%s)\n' % (self.handle, what), 'json': {'job': self.handle, 'status': 'cancelled', 'what': what}}

    def poll_s(self):
        return 1.0


class ConditionJob(Job):
    """Done when ``test(ctx)`` returns (True, text); ``progress(ctx)`` -> text.  ``on_cancel(ctx)`` optional."""
    kind = 'condition'

    def __init__(self, core, adapter, req, description, test, *, max_s=7200.0, on_cancel=None, poll_s=5.0):
        super().__init__(core, adapter, req, description)
        self.test, self.on_cancel, self._max, self._poll = test, on_cancel, max_s, poll_s
        self.result = None

    def poll(self, ctx):
        if self.state in ('cancelled', 'succeeded'):
            return self.state, self.result or '', {}
        ok, text = self.test(ctx)
        if ok:
            self.state = 'succeeded'
            self.result = text
        return self.state, text, {}

    def cancel(self, ctx):
        self.state = 'cancelled'
        if self.on_cancel:
            return self.on_cancel(ctx)
        return 'stopped waiting; the instrument keeps its settings'

    def max_wait_s(self):
        return self._max

    def poll_s(self):
        return self._poll


class OperationJob(Job):
    """A runtime operation (operation_id) of an asynchronous capability."""
    kind = 'operation'

    def __init__(self, core, adapter, req, description, cap, operation_id, *, max_s=None, poll_s=None):
        super().__init__(core, adapter, req, description)
        self.cap, self.operation_id = cap, operation_id
        # lab seconds between polls while waiting: the argument, else the member's "params": {"job_poll_s": N}, else 1 s
        # (runtimes that answer a poll with a whole curve want 10-60 s, or a long wait is slow in wall time)
        p = poll_s if poll_s is not None else (adapter.param('job_poll_s') if hasattr(adapter, 'param') else None)
        self._poll = float(p) if p else 1.0
        to = ((core.backend.instruments[self.instrument].contract['capabilities'].get(cap) or {}).get('execution') or {}).get('timeout_s')
        self._max = float(max_s or (to if isinstance(to, (int, float)) and to > 0 else 3600.0))
        self.last = None

    def poll(self, ctx):
        r = ctx.core.backend.poll(ctx.caller, self.instrument, self.operation_id)
        if r is None:
            return self.state, 'this instrument has no operation status query; read its state instead', {}
        st = op_status(r)
        ctx.core._record(ctx.req.calls, self.instrument, 'poll', operation_id=self.operation_id, status=r.get('status'), op_status=st)
        if self.state != 'cancelled':
            self.state = st
        self.last = r.get('result')
        res = r.get('result') if isinstance(r.get('result'), dict) else {}
        err = (r.get('error') or {}).get('code')
        text = ', '.join('%s=%s' % (k, v) for k, v in list(res.items())[:6] if not isinstance(v, (dict, list)))
        return self.state, text + ((' error %s' % err) if err else ''), {'operation_id': self.operation_id, 'result': r.get('result'), 'error': err}

    def cancel(self, ctx):
        r = ctx.core.backend.cancel(ctx.caller, self.instrument, self.operation_id, '%s.cancel' % ctx.req.request_id)
        ctx.core._record(ctx.req.calls, self.instrument, 'cancel', operation_id=self.operation_id, status=(r or {}).get('status'))
        if r is None:
            raise refused('NO_CANCEL', '%s cannot cancel its operations.' % self.instrument, 'use the class stop verb if it has one.')
        self.state = 'cancelled'
        return 'cancel of operation %s sent (%s)' % (self.operation_id, r.get('status'))

    def max_wait_s(self):
        return self._max

    def poll_s(self):
        return self._poll


class StateJob(Job):
    """A run-type action whose progress is in state slots: ``state(ctx)`` -> (status text, progress text, json), with status in
    RUNNING while it runs; ``stop(ctx)`` cancels (optional)."""
    kind = 'state'

    def __init__(self, core, adapter, req, description, state, *, stop=None, max_s=7200.0, poll_s=1.0):
        super().__init__(core, adapter, req, description)
        self.state_fn, self.stop_fn, self._max, self._poll = state, stop, max_s, poll_s

    def poll(self, ctx):
        if self.state == 'cancelled':
            return self.state, '', {}
        st, text, js = self.state_fn(ctx)
        self.state = st
        return st, text, js

    def cancel(self, ctx):
        if self.stop_fn is None:
            raise refused('NO_CANCEL', '%s cannot be stopped from chemlab.' % self.description, 'wait for it to end.')
        what = self.stop_fn(ctx)
        self.state = 'cancelled'
        return what

    def max_wait_s(self):
        return self._max

    def poll_s(self):
        return self._poll


def latest(core, caller, instrument):
    """The caller's most recent job on an instrument (or None)."""
    for j in reversed(list(registry(core).values())):
        if j.caller == caller and j.instrument == instrument:
            return j
    return None


def route(core, req, cmd, args):
    """The generic status / wait / cancel for a job-N handle.  Returns a reply dict, or None when args[0] is not a class job."""
    reg = registry(core)
    if not args or args[0] not in reg:
        if args and args[0].startswith('job-'):
            raise invalid('UNKNOWN_JOB', 'no job %r of yours.' % args[0], 'see `chemlab status` for your jobs and operations.')
        return None
    job = reg[args[0]]
    if job.caller != req.caller:
        raise invalid('UNKNOWN_JOB', 'no job %r of yours.' % args[0], 'see `chemlab status` for your jobs and operations.')
    if cmd == 'status':
        if len(args) != 1:
            raise invalid('BAD_ARGUMENTS', 'status takes one job.', 'usage: chemlab status JOB')
        return job.status_reply(core, req), job.instrument
    if cmd == 'cancel':
        if len(args) != 1:
            raise invalid('BAD_ARGUMENTS', 'cancel takes one job.', 'usage: chemlab cancel JOB')
        return job.cancel_reply(core, req), job.instrument
    mx = None
    if len(args) == 3 and args[1] == '--max':
        from .units import parse
        mx = parse(args[2], 'duration', what='--max')
    elif len(args) != 1:
        raise invalid('BAD_ARGUMENTS', 'usage: chemlab wait JOB [--max S]')
    return job.wait_reply(core, req, mx), job.instrument


def listing(core, caller):
    """Lines and json rows of the caller's class jobs (for `chemlab status` without arguments)."""
    rows = [j for j in registry(core).values() if j.caller == caller]
    return (['  %-7s %-28s %-36s last status: %s' % (j.handle, j.instrument, j.description[:36], j.state) for j in rows],
            [{'job': j.handle, 'instrument': j.instrument, 'description': j.description, 'status': j.state} for j in rows])

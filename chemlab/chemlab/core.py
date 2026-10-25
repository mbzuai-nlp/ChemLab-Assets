"""Layer 2 - the core service, the gate.  Every agent request passes here; the shell (CLI) only carries text.

Generic part (this module): command-line parsing, request ids and replay, the per-instrument lease lock, the append-only
log, dry-run, read-back after every backend call, contract validation of every backend call, error formatting and exit
codes, the agent/evaluator split (the core only ever uses the caller's agent principal).  Instrument part: a verb table
(flex_verbs.py) registered as an Instrument adapter.
"""
import copy
import datetime
import json
import re
import threading
import time

from .contract import Contract
from .errors import (EXIT_OK, ChemlabError, DefinedExecutionError, UndefinedExecutionError, ValidationError)

REQUEST_ID_RE = re.compile(r'^[A-Za-z0-9._-]{1,64}$')
GLOBAL_FLAGS = {'--json': 'json', '--dry-run': 'dry_run', '--yes': 'yes', '--force': 'force', '--help': 'help', '-h': 'help'}


class Reply:
    def __init__(self, exit_code=0, out='', err=''):
        self.exit_code, self.out, self.err = exit_code, out, err


class Opt:
    """A verb option: kind 'value' (--name VALUE), 'multi' (repeatable value) or 'flag'."""

    def __init__(self, help, kind='value', metavar='VALUE', choices=None, required=False):
        self.help, self.kind, self.metavar, self.choices, self.required = help, kind, metavar, choices, required


class Verb:
    def __init__(self, name, group, summary, handler, *, positionals=(), options=None, maps_to=(), readonly=False,
                 destructive=False, preconditions=(), errors=(), example=None, reference=None, details=None):
        self.name, self.group, self.summary, self.handler = name, group, summary, handler
        self.positionals = list(positionals)          # [(name, help, required)]
        self.options = dict(options or {})
        self.maps_to, self.readonly, self.destructive = list(maps_to), readonly, destructive
        self.preconditions, self.errors = list(preconditions), list(errors)
        self.example, self.reference, self.details = example, reference, details

    def usage(self, instrument):
        parts = ['chemlab run %s %s' % (instrument, self.name)]
        for name, _, required in self.positionals:
            parts.append(name if required else '[%s]' % name)
        for name, o in self.options.items():
            s = '--%s' % name if o.kind == 'flag' else '--%s %s' % (name, o.metavar)
            parts.append(s if o.required else '[%s]' % s)
        if self.destructive:
            parts.append('[--yes]')
        return ' '.join(parts)

    def help_text(self, instrument):
        lines = ['usage: ' + self.usage(instrument), '', self.summary]
        if self.details:
            lines += ['', self.details]
        if self.positionals:
            lines += ['', 'arguments:'] + ['  %-14s %s' % (n, h) for n, h, _ in self.positionals]
        if self.options:
            lines += ['', 'options:']
            for n, o in self.options.items():
                flag = '--%s' % n if o.kind == 'flag' else '--%s %s' % (n, o.metavar)
                extra = (' (one of: %s)' % ', '.join(map(str, o.choices))) if o.choices else ''
                lines.append('  %-24s %s%s' % (flag, o.help, extra))
        lines += ['  %-24s %s' % ('--dry-run', 'check everything and show the plan; send nothing to the instrument'),
                  '  %-24s %s' % ('--json', 'machine-readable output'),
                  '  %-24s %s' % ('--request-id ID', 'your own request id; repeating it with the same command replays the reply')]
        if self.destructive:
            lines.append('  %-24s %s' % ('--yes', 'really do it (without --yes this verb only shows what it would do)'))
        if self.preconditions:
            lines += ['', 'checked before anything is sent:'] + ['  - ' + p for p in self.preconditions]
        if self.errors:
            lines += ['', 'errors:'] + ['  - ' + e for e in self.errors]
        if self.maps_to:
            lines += ['', 'instrument capabilities used: ' + ', '.join(self.maps_to)]
        if self.reference:
            lines += ['manual: ' + self.reference]
        if self.example:
            lines += ['', 'example:', '  ' + self.example]
        return '\n'.join(lines) + '\n'


def parse_verb_args(verb, tokens):
    """-> (positionals list, options dict).  Raises ValidationError."""
    pos, opts = [], {}
    i = 0
    while i < len(tokens):
        t = tokens[i]
        if t.startswith('--') and len(t) > 2:
            name, eq, val = t[2:].partition('=')
            o = verb.options.get(name)
            if o is None:
                known = ', '.join('--' + n for n in verb.options) or 'none'
                raise ValidationError('UNKNOWN_OPTION', '%s does not take --%s.' % (verb.name, name),
                                      'options of %s: %s (see `chemlab help flex %s`).' % (verb.name, known, verb.name))
            if o.kind == 'flag':
                if eq:
                    raise ValidationError('BAD_OPTION', '--%s takes no value.' % name, 'write just --%s.' % name)
                opts[name] = True
            else:
                if not eq:
                    i += 1
                    if i >= len(tokens):
                        raise ValidationError('MISSING_VALUE', '--%s needs a value.' % name, 'write --%s %s.' % (name, o.metavar))
                    val = tokens[i]
                if o.choices and val not in [str(c) for c in o.choices]:
                    raise ValidationError('BAD_CHOICE', '--%s %s is not allowed.' % (name, val),
                                          'use one of: %s.' % ', '.join(map(str, o.choices)))
                if o.kind == 'multi':
                    opts.setdefault(name, []).append(val)
                elif name in opts:
                    raise ValidationError('REPEATED_OPTION', '--%s is given twice.' % name, 'give it once.')
                else:
                    opts[name] = val
        else:
            pos.append(t)
        i += 1
    required_pos = [n for n, _, r in verb.positionals if r]
    if len(pos) < len(required_pos):
        missing = required_pos[len(pos)]
        raise ValidationError('MISSING_ARGUMENT', '%s needs %s.' % (verb.name, missing), 'usage: %s' % verb.usage('flex'))
    if len(pos) > len(verb.positionals):
        raise ValidationError('EXTRA_ARGUMENT', 'unexpected argument %r.' % pos[len(verb.positionals)], 'usage: %s' % verb.usage('flex'))
    for n, o in verb.options.items():
        if o.required and n not in opts:
            raise ValidationError('MISSING_OPTION', '%s needs --%s.' % (verb.name, n), 'usage: %s' % verb.usage('flex'))
    return pos, opts


class InstrumentLock:
    """One lease per instrument: the caller that changes it holds it until idle for lease_s (or while its job runs)."""

    def __init__(self, lease_s):
        self.lease_s = lease_s
        self.holder, self.last = None, 0.0

    def acquire(self, caller, has_active_job):
        now = time.monotonic()
        if self.holder not in (None, caller) and (now - self.last < self.lease_s or has_active_job(self.holder)):
            raise DefinedExecutionError('LOCKED', 'the instrument is in use by %r (last command %.0f s ago).' % (self.holder, now - self.last),
                                        'wait until it is released (after %.0f s without commands and with no running job), then retry.'
                                        % self.lease_s, detail={'holder': self.holder})
        self.holder, self.last = caller, now

    def view(self, caller):
        if self.holder is None or (time.monotonic() - self.last >= self.lease_s):
            return 'free'
        return 'held by you' if self.holder == caller else 'held by %r' % self.holder


class AppendLog:
    """Append-only JSON-lines log of every request: command, backend calls, outcome and the full reply text."""

    def __init__(self, path):
        self.path = path
        self._lock = threading.Lock()
        self.entries = []

    def append(self, entry):
        with self._lock:
            self.entries.append(entry)
            if self.path:
                with open(self.path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + '\n')


class Ctx:
    """One request: the caller's agent view of the instrument, backend calls with contract validation and read-back,
    the dry-run shadow, and the list of completed steps for error reports."""

    def __init__(self, core, instrument, caller, request_id, flags):
        self.core, self.inst, self.caller, self.request_id = core, instrument, caller, request_id
        self.dry_run = bool(flags.get('dry_run'))
        self.flags = flags
        self.calls, self.plan, self.done, self.notes = [], [], [], []
        self.changed = False
        self._n = 0
        self.view = None
        self.shadow = instrument.new_shadow() if self.dry_run else None
        core.register_caller(caller)
        self.refresh()

    def refresh(self):
        if self.dry_run and self.view is not None:
            return self.view
        r = self.core.backend.read(self.caller)
        if r.get('status') != 'succeeded':
            raise UndefinedExecutionError('READ_FAILED', 'reading the instrument state failed (%s).' % (r.get('error') or {}).get('code'),
                                          'retry; if it persists the service needs attention.', changed='unknown')
        self.view = {sid: o['value'] for sid, o in r['observations'].items()}
        self.view['_time_s'] = r['clock']['time_s']
        return self.view

    def call(self, cid, args, step, *, refusal=None, readback=None):
        """Send one capability call.  step: human text for reports.  refusal(response) -> ChemlabError for a refused call.
        readback(view) -> problem text or None."""
        problems = self.core.contract.check(cid, args)
        if problems:
            raise UndefinedExecutionError('GATE_BUILT_INVALID_CALL', 'the gate built an invalid %s call: %s.' % (cid, '; '.join(problems)),
                                          'report this; nothing was sent.', changed='yes' if self.changed else 'no', done=self.done)
        if self.dry_run:
            self.plan.append({'capability': cid, 'arguments': args, 'step': step})
            self.inst.apply_shadow(self, cid, args)
            return {'status': 'succeeded', 'result': {}}
        self._n += 1
        rid = '%s.%d' % (self.request_id, self._n)
        response = self.core.backend.invoke(self.caller, cid, args, rid)
        status = response.get('status')
        err = response.get('error') or {}
        self.calls.append({'capability': cid, 'arguments': args, 'request_id': rid, 'status': status, 'code': err.get('code')})
        if status != 'succeeded':
            backend = {'capability': cid, 'status': status, 'code': err.get('code'), 'message': err.get('message')}
            changed = 'unknown' if err.get('side_effects') == 'unknown' else ('yes' if self.changed else 'no')
            exc = refusal(response) if refusal else None
            if exc is None:
                exc = self.inst.default_refusal(self, cid, response)
            exc.backend, exc.done = backend, list(self.done)
            exc.changed = changed if exc.changed == 'no' else exc.changed
            raise exc
        self.changed = True
        self.done.append(step)
        self.refresh()
        problem = readback(self.view) if readback else None
        if problem:
            raise UndefinedExecutionError('READBACK_MISMATCH', 'after %s the instrument reports %s.' % (step, problem),
                                          'check `chemlab status flex`; the instrument state is not what the command should have produced.',
                                          changed='unknown', done=self.done)
        return response


class CoreService:
    def __init__(self, backend, instrument_factory, *, log_path=None, lease_s=120.0):
        self.backend = backend
        self.contract = Contract(backend.contract_path)
        self.inst = instrument_factory(self)
        self.inst.check_against_contract()
        self.lock = InstrumentLock(lease_s)
        self.log = AppendLog(log_path)
        self._mutex = threading.RLock()
        self._replies = {}
        self._seq = 0

    # ---------------------------------------------------------------- entry point
    RESERVED_CALLERS = ('scene', 'evaluator')

    def register_caller(self, caller):
        if caller in self.RESERVED_CALLERS:
            raise ValueError('reserved principal %r cannot be a caller' % caller)
        self.backend.register_principal(caller, 'agent')

    def handle(self, argv, caller):
        self.register_caller(caller)
        with self._mutex:
            self._seq += 1
            seq = self._seq
            tokens = list(argv[1:])
            flags, rest, rid_given = {'json': '--json' in tokens}, [], None
            try:
                i = 0
                while i < len(tokens):
                    t = tokens[i]
                    if t in GLOBAL_FLAGS:
                        flags[GLOBAL_FLAGS[t]] = True
                    elif t == '--request-id' or t.startswith('--request-id='):
                        if '=' in t:
                            rid_given = t.split('=', 1)[1]
                        else:
                            i += 1
                            rid_given = tokens[i] if i < len(tokens) else ''
                        if not REQUEST_ID_RE.match(rid_given or ''):
                            raise ValidationError('BAD_REQUEST_ID', 'request id %r is not allowed.' % rid_given,
                                                  'use 1-64 letters, digits, ".", "_" or "-".')
                    else:
                        rest.append(t)
                    i += 1
            except ValidationError as exc:
                return self._finish(seq, caller, argv, 'r-%04d' % seq, flags, None, exc)
            request_id = rid_given or 'r-%04d' % seq
            key = (caller, request_id)
            fingerprint = json.dumps(argv)
            if rid_given and key in self._replies:
                prev_fp, prev = self._replies[key]
                if prev_fp == fingerprint:
                    note = '(replayed reply of request %s; nothing was sent again)\n' % request_id
                    reply = Reply(prev.exit_code, prev.out, prev.err + ('' if flags.get('json') else note))
                    self.log.append({'seq': seq, 'wall_time': _now_iso(), 'sim_time_s': self.backend.now(), 'caller': caller,
                                     'request_id': request_id, 'argv': list(argv), 'replayed': True, 'exit': reply.exit_code,
                                     'stdout': reply.out, 'stderr': reply.err, 'backend_calls': []})
                    return reply
                exc = ValidationError('REQUEST_ID_REUSED', 'request id %s was already used for a different command.' % request_id,
                                      'use a new request id (or none: the service makes one).')
                return self._finish(seq, caller, argv, request_id, flags, None, exc)
            ctx = None
            try:
                result, ctx = self._dispatch(rest, caller, request_id, flags)
                reply = self._finish(seq, caller, argv, request_id, flags, ctx, None, result)
            except ChemlabError as exc:
                reply = self._finish(seq, caller, argv, request_id, flags, getattr(exc, 'ctx', ctx), exc)
            except Exception as exc:                                        # never crash the service
                wrapped = UndefinedExecutionError('INTERNAL', 'the service hit an unexpected problem (%s: %s).' % (type(exc).__name__, exc),
                                                  'check `chemlab status flex` before going on; report this.', changed='unknown')
                reply = self._finish(seq, caller, argv, request_id, flags, ctx, wrapped)
            if rid_given:
                self._replies[key] = (fingerprint, reply)
            return reply

    # ---------------------------------------------------------------- dispatch
    def _dispatch(self, rest, caller, request_id, flags):
        inst = self.inst
        if not rest or rest[0] == 'help' and len(rest) == 1:
            return {'text': overview_text(inst)}, None
        cmd, args = rest[0], rest[1:]
        if cmd in ('help',):
            return {'text': inst.help(args)}, None
        if flags.get('help'):
            if cmd == 'run' and len(args) >= 2:
                return {'text': inst.help(args[1:2])}, None
            return {'text': overview_text(inst)}, None
        if cmd == 'describe':
            if args and args[0] not in ('--all',) and args[0] != inst.name:
                raise ValidationError('UNKNOWN_INSTRUMENT', 'no instrument %r here.' % args[0], 'instruments: %s.' % inst.name)
            if not args:
                return {'text': 'instruments:\n  %-6s %s\n\nnext: chemlab describe %s\n' % (inst.name, inst.title, inst.name),
                        'json': {'instruments': [{'name': inst.name, 'title': inst.title}]}}, None
            return inst.describe(show_all='--all' in args), None
        if cmd == 'log':
            return self._log_view(caller, args), None
        if cmd == 'status':
            target = args[0] if args else inst.name
            ctx = Ctx(self, inst, caller, request_id, flags)
            if target == inst.name:
                return inst.status(ctx), ctx
            return inst.job_status(ctx, target), ctx
        if cmd == 'cancel':
            if len(args) != 1:
                raise ValidationError('MISSING_ARGUMENT', 'cancel needs a job id.', 'usage: chemlab cancel JOB (see `chemlab status flex`).')
            return self._run_verb(caller, request_id, flags, 'cancel', args)
        if cmd == 'run':
            if not args:
                raise ValidationError('MISSING_ARGUMENT', 'run needs an instrument and a verb.', 'usage: chemlab run flex VERB ...')
            if args[0] != inst.name:
                raise ValidationError('UNKNOWN_INSTRUMENT', 'no instrument %r here.' % args[0], 'instruments: %s.' % inst.name)
            if len(args) < 2:
                raise ValidationError('MISSING_ARGUMENT', 'which verb?', 'see `chemlab describe flex`.')
            return self._run_verb(caller, request_id, flags, args[1], args[2:])
        raise ValidationError('UNKNOWN_COMMAND', 'unknown command %r.' % cmd,
                              'commands: describe, help, run, status, cancel, log (see `chemlab --help`).')

    def _run_verb(self, caller, request_id, flags, name, tokens):
        inst = self.inst
        verb = inst.verbs.get(name)
        if verb is None:
            raise inst.unknown_verb(name)
        if flags.get('help'):
            return {'text': verb.help_text(inst.name)}, None
        pos, opts = parse_verb_args(verb, tokens)
        if verb.destructive and not flags.get('yes'):
            flags = dict(flags, dry_run=True, destructive_preview=True)
        if not verb.readonly and not flags.get('dry_run'):
            self.lock.acquire(caller, inst.has_active_job)
        ctx = Ctx(self, inst, caller, request_id, flags)
        try:
            result = verb.handler(ctx, pos, opts)
        except ChemlabError as exc:
            exc.ctx = ctx
            if not exc.done:
                exc.done = list(ctx.done)
            if exc.changed == 'no' and ctx.changed:
                exc.changed = 'yes'
            raise
        except Exception as exc:                                            # a gate bug: report what was already done
            wrapped = UndefinedExecutionError('INTERNAL', 'the service hit an unexpected problem (%s: %s).' % (type(exc).__name__, exc),
                                              'check `chemlab status flex` before going on; report this.',
                                              changed='unknown' if ctx.changed else 'no', done=ctx.done)
            wrapped.ctx = ctx
            raise wrapped
        return result, ctx

    def _log_view(self, caller, args):
        last = 20
        if args[:1] == ['--last'] and len(args) == 2 and args[1].isdigit():
            last = int(args[1])
        elif args:
            raise ValidationError('BAD_OPTION', 'log takes only --last N.', 'usage: chemlab log [--last N]')
        mine = [e for e in self.log.entries if e['caller'] == caller][-last:]
        lines = []
        for e in mine:
            outcome = 'ok' if e['exit'] == 0 else '%s (exit %d)' % (e.get('outcome'), e['exit'])
            lines.append('%-8s t=%7.1fs  %-60s -> %s%s' % (e['request_id'], e['sim_time_s'], ' '.join(e['argv'])[:60], outcome,
                                                          ' [dry-run]' if e.get('dry_run') else ''))
        public = [{k: e.get(k) for k in ('request_id', 'wall_time', 'sim_time_s', 'argv', 'exit', 'outcome', 'dry_run', 'instrument_changed')}
                  for e in mine]
        return {'text': ('\n'.join(lines) + '\n') if lines else 'no requests yet\n', 'json': {'entries': public}}

    # ---------------------------------------------------------------- output
    def _finish(self, seq, caller, argv, request_id, flags, ctx, exc, result=None):
        as_json = flags.get('json')
        dry = bool(ctx and ctx.dry_run)
        if exc is None:
            exit_code = EXIT_OK
            if as_json:
                payload = {'ok': True, 'request_id': request_id, 'dry_run': dry}
                payload.update(result.get('json') or {})
                if ctx and dry:
                    payload['plan'] = [p['step'] for p in ctx.plan]
                    payload['backend_calls'] = [{'capability': p['capability'], 'arguments': p['arguments']} for p in ctx.plan]
                if ctx and ctx.notes:
                    payload['notes'] = ctx.notes
                out, err = json.dumps(payload, ensure_ascii=False, indent=1) + '\n', ''
            else:
                text = result.get('text', '')
                if ctx and dry:
                    text = dry_run_text(ctx, result, ctx.flags.get('destructive_preview'))
                elif ctx and ctx.notes:
                    text += ''.join('note: %s\n' % n for n in ctx.notes)
                out, err = text, ''
            outcome, changed = 'ok', ('no' if dry or not ctx else ('yes' if ctx.changed else 'no'))
        else:
            exit_code = exc.exit_code
            if as_json:
                out, err = json.dumps({'ok': False, 'request_id': request_id, 'dry_run': dry, 'error': exc.to_json()},
                                      ensure_ascii=False, indent=1) + '\n', ''
            else:
                out, err = '', exc.to_text(request_id)
            outcome, changed = '%s/%s' % (exc.kind, exc.code), exc.changed
        self.log.append({'seq': seq, 'wall_time': _now_iso(), 'sim_time_s': self.backend.now(), 'caller': caller, 'request_id': request_id,
                         'argv': list(argv), 'dry_run': dry, 'exit': exit_code, 'outcome': outcome, 'instrument_changed': changed,
                         'backend_calls': copy.deepcopy(ctx.calls) if ctx else [], 'plan': copy.deepcopy(ctx.plan) if ctx and dry else [],
                         'stdout': out, 'stderr': err})
        return Reply(exit_code, out, err)


def dry_run_text(ctx, result, destructive_preview=False):
    head = 'dry-run (nothing was sent to the instrument)' if not destructive_preview else \
        'not done: this verb changes or deletes things that cannot be restored; add --yes to do it. It would:'
    lines = [head + (':' if not destructive_preview else '')]
    for n, p in enumerate(ctx.plan, 1):
        lines.append('  %d. %s' % (n, p['step']))
    if not ctx.plan:
        lines.append('  (no instrument calls)')
    checks = result.get('checks') or []
    if checks:
        lines.append('checks passed: ' + '; '.join(checks))
    unchecked = result.get('unchecked') or []
    if unchecked:
        lines.append('checked only when it runs: ' + '; '.join(unchecked))
    return '\n'.join(lines) + '\n'


def overview_text(inst):
    return ('chemlab - command line for lab instruments (this shell: %s)\n\n'
            'usage:\n'
            '  chemlab describe [%s] [--all]      what the instrument can do (verbs, units, locations)\n'
            '  chemlab help %s [VERB|admin]       help for one verb, or the maintenance/admin verbs\n'
            '  chemlab run %s VERB [ARGS]         run a verb (add --help to any verb)\n'
            '  chemlab status [%s|JOB]            instrument state, or a job\'s progress\n'
            '  chemlab cancel JOB                  stop a job\n'
            '  chemlab log [--last N]              your past requests\n\n'
            'global options: --json  --dry-run  --request-id ID  --help\n'
            'exit codes: 0 ok, 2 invalid request (nothing sent), 3 instrument refused (documented reason), '
            '4 unexpected error, 5 service unreachable\n'
            % (inst.name, inst.name, inst.name, inst.name, inst.name))


def _now_iso():
    return datetime.datetime.now().isoformat(timespec='milliseconds')

"""Generic commands for every instrument of a lab, generated from the contracts (layer 2 of the generic agent layer).

The same commands work for all 203 instruments; each instrument's own contract capabilities are exposed directly:

    chemlab list | describe INST | help INST CAP | read INST [SLOT ...] | call INST CAP [--ARG VALUE ...] [--args JSON]
    chemlab write INST SLOT VALUE | status [OP] | cancel OP | wait SECONDS | wait OP [--max S] | time
    chemlab panel INST press KEY [--hold S] | turn KNOB N | switch SW POS | touch DISPLAY X Y [--hold S] [--to X2 Y2]
    chemlab screen INST [DISPLAY] [-o FILE.png] | log [--last N] [--full] | run INST VERB ... (class-level verbs, step 2)

What this layer does NOT do (on purpose): no verb tables per instrument class, no named locations, no unit conversion
(values are what the contract defines, SI), no precondition checks or inference - the runtime checks every call itself and
its answer is passed on with the contract's description of the error code.  What it does: hide everything that is not
agent-visible (contract visibility, availability), turn command-line text into JSON values by the capability's schema,
request ids and replay, operation handles for asynchronous capabilities, the lab clock (``wait`` is the only way time
moves), screen captures into the work dir, an append-only log, and stable exit codes.

Exit codes: 0 ok, 2 bad usage (nothing was sent), 3 refused or failed by the instrument (its error code), 4 unexpected
(runtime crash or service fault), 5 service unreachable (printed by the client), 6 unsupported by this instrument twin.
"""
import copy
import datetime
import json
import math
import os
import re
import tempfile
import threading
from pathlib import Path

from .adapters import load_adapters, route_job
from .contract_text import (KIND_EN, arg_signature, example_call, summary_en, type_long, type_short, unit_of)
from .core import REQUEST_ID_RE, AppendLog, Reply
from .errors import EXIT_DEFINED, EXIT_OK, EXIT_UNDEFINED, EXIT_VALIDATION
from .hosts import CHEMSIM_ROOT

EXIT_USAGE, EXIT_REFUSED, EXIT_UNEXPECTED, EXIT_UNSUPPORTED = EXIT_VALIDATION, EXIT_DEFINED, EXIT_UNDEFINED, 6
EXIT_FOR = {'usage': EXIT_USAGE, 'refused': EXIT_REFUSED, 'unexpected': EXIT_UNEXPECTED, 'unsupported': EXIT_UNSUPPORTED}
SILA = {'usage': 'ValidationError', 'refused': 'DefinedExecutionError', 'unexpected': 'UndefinedExecutionError',
        'unsupported': 'DefinedExecutionError'}
RUNNING = {'accepted', 'running', 'queued', 'pending', 'started', 'starting', 'in_progress', 'paused', 'stopping', 'cancelling',
           'waiting', 'injecting'}   # injecting: an HPLC/SFC run's first phase (call status succeeded, result phase injecting)
OK_STATUSES = {'succeeded', 'accepted', 'running'}
AUTO_RID = re.compile(r'^r-\d+$')
TEXT_LIMIT = 400
GESTURES = {
    'press': ('panel.press', ['input_id'], 'KEY'),
    'turn': ('panel.turn', ['input_id', 'detents'], 'KNOB DETENTS'),
    'switch': ('panel.switch', ['input_id', 'position'], 'SWITCH POSITION'),
    'touch': ('panel.touch', ['display_id', 'x', 'y'], 'DISPLAY X Y'),
}
# Protocol-level error codes any call can return (lab protocol 1.0.0); describe lists them once instead of per capability.
PROTOCOL_ERRORS = ('INVALID_ARGUMENT', 'REQUEST_CONFLICT', 'UNKNOWN_INSTANCE', 'UNKNOWN_CAPABILITY', 'ACCESS_DENIED', 'UNSUPPORTED')
# host paths are replaced by [host path] in every output: the folder holding this project, the home directory and the folder
# that holds the home directories, the temp dir and the usual system roots ('/' itself is never a prefix here)
_HOST_PATHS = sorted({p for p in (str(CHEMSIM_ROOT.parent), str(Path.home()), str(Path.home().parent), tempfile.gettempdir(), '/tmp',
                                  '/var/tmp', '/home', '/root') if p.strip('/')}, key=len, reverse=True)
_HOST_PATH_RE = re.compile('(?<![\\w.~-])(%s)(?=[/\\s"\']|$)[^\\s"\'<>|;,)]*' % '|'.join(re.escape(p) for p in _HOST_PATHS))


class CmdError(Exception):
    def __init__(self, kind, code, message, *, fix=None, contract=None, side_effects='none', runtime=None, see=None):
        super().__init__(message)
        self.kind, self.code, self.message, self.fix = kind, code, message, fix
        self.contract, self.side_effects, self.runtime, self.see = contract, side_effects, runtime, see

    @property
    def exit_code(self):
        return EXIT_FOR[self.kind]

    def to_json(self):
        out = {'class': self.kind, 'sila_class': SILA[self.kind], 'code': self.code, 'message': self.message,
               'side_effects': self.side_effects}
        if self.fix:
            out['fix'] = self.fix
        if self.contract is not None:
            out['contract'] = self.contract
        if self.runtime is not None:
            out['runtime'] = self.runtime
        if self.see:
            out['see'] = self.see
        return out

    def to_text(self, request_id):
        lines = ['error[%s/%s]: %s' % (self.kind, self.code, self.message)]
        if self.contract is not None:
            lines.append('contract: %s' % (self.contract or '(this code is not declared in the contract for this capability)'))
        rt = self.runtime or {}
        if rt.get('message') and rt.get('message') != self.contract:
            lines.append('runtime message: %s' % rt['message'])
        if self.fix:
            lines.append('fix: %s' % self.fix)
        lines.append('side effects: %s' % self.side_effects)
        if self.see:
            lines.append('see: %s' % self.see)
        lines.append('request: %s' % request_id)
        return '\n'.join(lines) + '\n'


def usage(code, message, fix=None):
    return CmdError('usage', code, message, fix=fix, side_effects='none (nothing was sent)')


class Req:
    def __init__(self, command, instrument, args, caller, request_id, flags):
        self.command, self.instrument, self.args = command, instrument, list(args)
        self.caller, self.request_id, self.flags = caller, request_id, flags


def _now_iso():
    return datetime.datetime.now().isoformat(timespec='milliseconds')


def _types(schema):
    if not isinstance(schema, dict):
        return set()
    t = schema.get('type')
    out = set(t if isinstance(t, list) else [t] if t else [])
    for key in ('anyOf', 'oneOf'):
        for s in schema.get(key) or []:
            out |= _types(s)
    if 'enum' in schema and not out:
        out |= {type(v).__name__ for v in schema['enum']}
    return out


def _json_type_ok(v, types):
    if v is None:
        return 'null' in types
    if isinstance(v, bool):
        return 'boolean' in types
    if isinstance(v, int):
        return bool(types & {'integer', 'number'})
    if isinstance(v, float):
        return 'number' in types or ('integer' in types and v.is_integer())
    if isinstance(v, str):
        return 'string' in types
    if isinstance(v, list):
        return 'array' in types
    if isinstance(v, dict):
        return 'object' in types
    return False


def coerce(text, schema, name):
    """Command-line text -> JSON value, by the schema's type (no unit conversion, no range check: the runtime checks)."""
    types = _types(schema)
    if not types:
        try:
            return json.loads(text)
        except ValueError:
            return text
    if types == {'string'}:
        return text
    src = text
    if 'string' not in types and types & {'number', 'integer'} and re.fullmatch(r'\+\.?\d.*', text or ''):
        src = text[1:]                                              # a signed number: "+20" detents, "+0.5" offset
    try:
        v = json.loads(src)
    except ValueError:
        v = _NOPE
    if v is not _NOPE and _json_type_ok(v, types):
        return v
    if 'string' in types:
        return text
    want = '/'.join(sorted(t for t in types if t))
    raise usage('BAD_VALUE', '--%s %s is not %s %s value.' % (name, text, 'an' if want[:1] in 'aeiou' else 'a', want),
                'write it as JSON: numbers like 373.15, -2, +20 or 5e-9, true/false, "text", [..] or {..}.')


_NOPE = object()


def _short(v, limit=TEXT_LIMIT):
    s = json.dumps(json_safe(v), ensure_ascii=False, default=str)
    return s if len(s) <= limit else s[:limit] + ' ... (%d chars; use --json)' % len(s)


def _class_text(r):
    """A reply of the compile layer (class verbs, class jobs, class views): its human text and notes go through
    compile.units.tidy_text (float noise, unit-suffixed labels); its JSON is left exact."""
    if isinstance(r, dict) and (r.get('text') or r.get('notes')):
        from .compile.units import tidy_text
        r = dict(r, text=tidy_text(r.get('text', '')))
        if r.get('notes'):
            r['notes'] = [tidy_text(n) for n in r['notes']]
    return r


def _clip(s, limit):
    return s if len(s) <= limit else s[:limit] + ' ... (%d chars; use --json)' % len(s)


def json_safe(obj):
    """JSON has no NaN / Infinity: such numbers are written as the strings "NaN", "Infinity", "-Infinity"."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return 'NaN' if obj != obj else ('Infinity' if obj > 0 else '-Infinity')
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    return obj


def scrub_paths(obj):
    """Host file paths never reach the agent: replace them by [host path]."""
    if isinstance(obj, dict):
        return {k: scrub_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_paths(v) for v in obj]
    if isinstance(obj, str):
        return _HOST_PATH_RE.sub('[host path]', obj)
    return obj


class GenericCore:
    """Entry point ``handle(argv, caller) -> Reply`` (the same interface as the pilot's CoreService, so the pilot's socket
    server serves it unchanged)."""

    RESERVED_CALLERS = ('operator', 'evaluator', 'scene')

    def __init__(self, backend, *, work_dir, log_path=None, adapters=None, max_wait_s=86400.0):
        self.backend = backend
        self.work_dir = Path(work_dir).resolve()
        self.log = AppendLog(log_path)
        self.adapters = load_adapters(backend.config) if adapters is None else dict(adapters)
        self.max_wait_s = float(max_wait_s)
        self._mutex = threading.RLock()
        self._replies = {}
        self._seq = 0
        self.ops = {}                          # op handle -> {...}
        self._op_seq = 0
        self.leaks = []                        # host-side record of hidden ids removed from agent output
        self._clock_seen = {}

    # ================================================================ entry point
    def handle(self, argv, caller):
        if caller in self.RESERVED_CALLERS:
            raise ValueError('reserved caller name %r' % caller)
        self.backend.add_caller(caller)
        with self._mutex:
            self._seq += 1
            seq = self._seq
            tokens = list(argv[1:])
            flags, rest, rid_given = {'json': False}, [], None
            err = None
            i = 0
            while i < len(tokens):
                t = tokens[i]
                if t == '--json':
                    flags['json'] = True
                elif t in ('--help', '-h'):
                    flags['help'] = True
                elif t == '--request-id' or t.startswith('--request-id='):
                    if '=' in t:
                        rid_given = t.split('=', 1)[1]
                    else:
                        i += 1
                        rid_given = tokens[i] if i < len(tokens) else ''
                    if not REQUEST_ID_RE.match(rid_given or '') or AUTO_RID.match(rid_given):
                        err = usage('BAD_REQUEST_ID', 'request id %r is not allowed.' % rid_given,
                                    'use 1-64 letters, digits, ".", "_" or "-" (ids like r-12 are made by the service).')
                else:
                    rest.append(t)
                i += 1
            request_id = rid_given or 'r-%04d' % seq
            if err is not None:
                return self._finish(seq, caller, argv, request_id, flags, None, err, None, [])
            fingerprint = json.dumps(argv)
            key = (caller, request_id)
            if rid_given and key in self._replies:
                prev_fp, prev = self._replies[key]
                if prev_fp == fingerprint:
                    note = '' if flags['json'] else '(replayed the reply of request %s; nothing was sent again)\n' % request_id
                    reply = Reply(prev.exit_code, prev.out, prev.err + note)
                    self.log.append({'seq': seq, 'wall_time': _now_iso(), 'sim_time_s': self.backend.now(), 'caller': caller,
                                     'request_id': request_id, 'argv': list(argv), 'replayed': True, 'exit': reply.exit_code,
                                     'outcome': 'replay', 'stdout': reply.out, 'stderr': reply.err, 'backend_calls': []})
                    return reply
                exc = usage('REQUEST_ID_REUSED', 'request id %s was already used for a different command.' % request_id,
                            'use a new request id (or none: the service makes one).')
                return self._finish(seq, caller, argv, request_id, flags, None, exc, None, [])
            calls = []
            inst = None
            if len(rest) > 1 and rest[0] in self.INSTRUMENT_SCOPED and rest[1] in self.backend.instruments:
                inst = rest[1]                                               # errors of this request are filtered with its view
            try:
                result, inst = self._dispatch(rest, caller, request_id, flags, calls)
                reply = self._finish(seq, caller, argv, request_id, flags, result, None, inst, calls)
            except CmdError as exc:
                reply = self._finish(seq, caller, argv, request_id, flags, None, exc, getattr(exc, 'inst', inst), calls)
            except Exception as exc:                                        # never crash the service
                wrapped = CmdError('unexpected', 'INTERNAL', 'the service hit an unexpected problem (%s).' % type(exc).__name__,
                                   side_effects='unknown', fix='check the instrument with `chemlab read` before going on.')
                wrapped.trace = repr(exc)
                reply = self._finish(seq, caller, argv, request_id, flags, None, wrapped, inst, calls)
            if rid_given:
                self._replies[key] = (fingerprint, reply)
            return reply

    # ================================================================ output
    def _finish(self, seq, caller, argv, request_id, flags, result, exc, inst, calls):
        view = self.backend.instruments[inst].view if inst in getattr(self.backend, 'instruments', {}) else None
        found = set()
        if exc is None:
            exit_code = EXIT_OK
            payload = {'ok': True, 'request_id': request_id}
            payload.update(result.get('json') or {})
            text = result.get('text', '')
            notes = result.get('notes') or []
        else:
            exit_code = exc.exit_code
            payload = {'ok': False, 'request_id': request_id, 'error': exc.to_json()}
            if getattr(exc, 'extra', None):
                payload.update(exc.extra)
            text = exc.to_text(request_id)
            notes = []
        typed = set(argv)
        for v in (view, getattr(self.backend, 'global_view', None)):
            if v is not None:
                allow = typed & v.hidden_set
                payload = v.redact(payload, found, allow)
                text = v.redact(text, found, allow)
                notes = v.redact(notes, found, allow)
        payload, text, notes = scrub_paths(json_safe(payload)), scrub_paths(text), scrub_paths(notes)
        if notes:
            payload['notes'] = notes
        if found:
            self.leaks.append({'seq': seq, 'instrument': inst, 'argv': list(argv), 'removed': sorted(found)})
        if flags.get('json'):
            out, err = json.dumps(payload, ensure_ascii=False, indent=1, default=str) + '\n', ''
        elif exc is None:
            out, err = text + ''.join('note: %s\n' % n for n in notes), ''
        else:
            out, err = '', text
        outcome = 'ok' if exc is None else '%s/%s' % (exc.kind, exc.code)
        entry = {'seq': seq, 'wall_time': _now_iso(), 'sim_time_s': self.backend.now(), 'caller': caller, 'request_id': request_id,
                 'argv': list(argv), 'instrument': inst, 'exit': exit_code, 'outcome': outcome, 'backend_calls': copy.deepcopy(calls),
                 'stdout': out, 'stderr': err}
        if exc is not None and getattr(exc, 'trace', None):
            entry['internal'] = exc.trace
        self.log.append(entry)
        return Reply(exit_code, out, err)

    # ================================================================ dispatch
    COMMANDS = ('list', 'describe', 'help', 'read', 'call', 'write', 'status', 'cancel', 'wait', 'time', 'panel', 'screen',
                'log', 'run')
    INSTRUMENT_SCOPED = ('describe', 'help', 'read', 'call', 'write', 'panel', 'screen', 'run')

    def _dispatch(self, rest, caller, request_id, flags, calls):
        if not rest:
            return {'text': overview_text(), 'json': {'commands': list(self.COMMANDS)}}, None
        cmd, args = rest[0], rest[1:]
        if cmd not in self.COMMANDS:
            raise usage('UNKNOWN_COMMAND', 'unknown command %r.' % cmd, 'commands: %s (see `chemlab help`).' % ', '.join(self.COMMANDS))
        inst = None
        if cmd in self.INSTRUMENT_SCOPED and args:
            inst = args[0] if args[0] in self.backend.instruments else None
        if flags.get('help'):
            if cmd == 'call' and len(args) >= 2 and inst:
                return self._help(inst, args[1]), inst
            if cmd == 'run' and inst and inst in self.adapters:          # class verb help (compile layer)
                req = Req(cmd, inst, args, caller, request_id, flags)
                req.calls = calls
                return _class_text(self.adapters[inst].dispatch_run(self, req, args[1:])), inst
            if inst:
                return self._describe(inst, caller, full=False), inst
            return {'text': overview_text()}, None
        req = Req(cmd, inst, args, caller, request_id, flags)
        req.calls = calls
        if cmd in ('status', 'wait', 'cancel') and args and args[0].startswith('job-'):   # class-level jobs (compile layer)
            r = route_job(self, req, cmd, args)
            if r is not None:
                return _class_text(r[0]), r[1]
        if inst and inst in self.adapters:
            r = self.adapters[inst].intercept(self, req)
            if r is not None:
                return _class_text(r), inst
        if cmd == 'list':
            return self._list(args), None
        if cmd == 'time':
            return {'text': 't = %.3f s (simulated)\n' % self.backend.now(), 'json': {'time_s': self.backend.now()}}, None
        if cmd == 'log':
            return self._log_view(caller, args), None
        if cmd == 'wait':
            return self._wait(req)
        if cmd == 'status':
            return self._status(req)
        if cmd == 'cancel':
            return self._cancel(req)
        if cmd == 'help' and not args:
            return {'text': overview_text()}, None
        if not args:
            raise usage('MISSING_INSTRUMENT', '%s needs an instrument.' % cmd, 'see `chemlab list`.')
        if inst is None:
            raise usage('UNKNOWN_INSTRUMENT', 'no instrument %r in this lab.' % args[0], 'see `chemlab list`.')
        if cmd == 'describe':
            if len(args) > 1:
                raise usage('EXTRA_ARGUMENT', 'describe takes one instrument.', 'usage: chemlab describe INST [--json]')
            return self._describe(inst, caller, full=flags['json']), inst
        if cmd == 'help':
            if len(args) == 1:
                return self._describe(inst, caller, full=False), inst
            if args[1] == 'panel':
                return self._panel_help(inst), inst
            return self._help(inst, args[1]), inst
        if cmd == 'read':
            return self._read(req), inst
        if cmd == 'call':
            return self._call(req), inst
        if cmd == 'write':
            return self._write(req), inst
        if cmd == 'panel':
            return self._panel(req), inst
        if cmd == 'screen':
            return self._screen(req), inst
        if cmd == 'run':
            ad = self.adapters.get(inst)
            if ad is None:
                raise usage('NO_CLASS_VERBS', '%s has no class-level verbs yet.' % inst,
                            'use its own capabilities: `chemlab describe %s`, then `chemlab call %s CAP ...`.' % (inst, inst))
            return _class_text(ad.dispatch_run(self, req, args[1:])), inst
        raise usage('UNKNOWN_COMMAND', 'unknown command %r.' % cmd)

    # ================================================================ helpers
    def _inst(self, iid):
        return self.backend.instruments[iid]

    def _record(self, calls, iid, op, **kw):
        e = {'instrument': iid, 'op': op}
        e.update(kw)
        calls.append(e)
        return e

    def _clock_notes(self, iid):
        host = self._inst(iid).host
        seen = self._clock_seen.get(iid, 0)
        errs = host.clock_errors
        if len(errs) > seen or (errs and seen > len(errs)):
            self._clock_seen[iid] = len(errs)
            kinds = sorted({e.split(' ', 2)[2].split(':', 1)[0] for e in errs[-5:] if len(e.split(' ', 2)) > 2})
            return ['the simulation of %s raised %s during a clock step; its state may be off.' % (iid, ', '.join(kinds) or 'an error')]
        return []

    def _cap(self, iid, cap_id, for_call=True):
        view = self._inst(iid).view
        if for_call and cap_id in view.callable:
            return view.capabilities.get(cap_id) or self._inst(iid).contract['capabilities'][cap_id]
        if not for_call and cap_id in view.capabilities:
            return view.capabilities[cap_id]
        if cap_id in view.unsupported:
            raise CmdError('unsupported', 'UNSUPPORTED', '%s is not supported by this %s twin (%s).' % (cap_id, iid, view.unsupported[cap_id]),
                           side_effects='none (nothing was sent)', see='chemlab describe %s' % iid)
        raise usage('UNKNOWN_CAPABILITY', '%s has no capability %r you can call.' % (iid, cap_id),
                    'see `chemlab describe %s` for its capabilities.' % iid)

    def _error_from_response(self, iid, cap_id, response, what='call'):
        """A non-success protocol response -> CmdError with the runtime code, the contract's text for it and side effects."""
        status = response.get('status')
        err = response.get('error') or {}
        code = err.get('code') or 'NO_CODE'
        contract_text = self._contract_error(iid, cap_id, code)
        side = err.get('side_effects') or (self._contract_side(iid, cap_id, code)) or 'not reported'
        runtime = {'status': status, 'code': err.get('code'), 'message': err.get('message'), 'retryable': err.get('retryable')}
        if response.get('raised'):
            runtime['raised'] = response['raised']
        if status == 'unsupported':
            kind = 'unsupported'
        elif code == 'RUNTIME_EXCEPTION' or status not in ('rejected', 'failed', 'unsupported'):
            kind = 'unexpected'
        else:
            kind = 'refused'
        target = '%s %s' % (cap_id, what) if what != 'call' else cap_id
        if kind == 'unexpected' and code == 'RUNTIME_EXCEPTION':
            msg = 'the %s runtime raised %s on %s.' % (iid, response.get('raised') or 'an exception', target)
            runtime['message'] = None
        elif kind == 'unexpected':
            msg = '%s answered %s with status %r.' % (iid, target, status)
        else:
            msg = '%s %s %s (status %s).' % (iid, 'refused' if status == 'rejected' else 'reports' if kind == 'unsupported' else 'failed', target, status)
        e = CmdError(kind, code, msg, contract=contract_text, side_effects=side, runtime=runtime,
                     see='chemlab help %s %s' % (iid, cap_id) if cap_id and cap_id in self._inst(iid).view.capabilities else None)
        e.inst = iid
        return e

    def _contract_error(self, iid, cap_id, code):
        caps = self._inst(iid).contract['capabilities']
        for e in (caps.get(cap_id) or {}).get('errors') or []:
            if e.get('code') == code:
                return e.get('meaning_zh') or e.get('meaning') or ''
        return ''

    def _contract_side(self, iid, cap_id, code):
        for e in (self._inst(iid).contract['capabilities'].get(cap_id) or {}).get('errors') or []:
            if e.get('code') == code:
                return e.get('side_effects')
        return None

    # ================================================================ list / describe / help
    def inventory(self):
        """The lab's physical inventory (lab config "inventory", see chemlab/inventory.py)."""
        from . import inventory as INV
        return INV.resolve(self.backend.config)

    def inventory_items(self, key=None):
        """Containers of the lab's inventory; ``key``: an instrument id (what is for it or inside it) or a storage place id."""
        inv = self.inventory()
        recs = [dict(r) for r in (inv.get('containers') or []) + (inv.get('instrument_contents') or [])]
        if key is None:
            return recs
        if key in self.backend.instruments:
            return [r for r in recs if key in (r.get('for') or []) or r['location'].get('instrument') == key]
        return [r for r in recs if r['location'].get('place') == key]

    @staticmethod
    def inventory_line(r):
        from . import inventory as INV
        return '%-34s %s - %s' % (r['id'], INV.contents_text(r), INV.where_text(r))

    def _list_inventory(self, args):
        inv = self.inventory()
        places = {p['id']: p for p in inv.get('places') or []}
        key = args[0] if args else None
        if key is not None and key not in self.backend.instruments and key not in places:
            raise usage('UNKNOWN_ID', 'no instrument or storage place %r in this lab.' % key,
                        'see `chemlab list` (instruments) and `chemlab list --inventory` (places).')
        recs = self.inventory_items(key)
        lines = ['inventory of the lab%s: %d containers (what, how much, where); moving one is a hands / robot job, '
                 'not a chemlab command' % (' for %s' % key if key else '', len(recs)), '']
        by = {}
        for r in recs:
            by.setdefault(r['location'].get('place'), []).append(r)
        for pid in [p for p in places if p in by] + [p for p in by if p not in places]:
            p = places.get(pid) or {}
            lines.append('%s%s' % (pid, ' - %s (%d positions)' % (p.get('type'), p.get('slots') or len(p.get('positions') or []))
                                   if p.get('positions') else ''))
            lines += ['  ' + self.inventory_line(r) for r in by[pid]]
            lines.append('')
        if not recs:
            lines.append('  (nothing listed)')
        return {'text': '\n'.join(lines).rstrip() + '\n', 'json': {'containers': recs, 'key': key,
                                                                   'places': list(places.values()) if key is None else
                                                                   [places[k] for k in by if k in places]}}

    def _list(self, args):
        dom = None
        if args[:1] in (['--inventory'], ['--shelf']) and len(args) <= 2:
            return self._list_inventory(args[1:])
        if args[:1] == ['--domain'] and len(args) == 2:
            dom = args[1]
        elif args:
            raise usage('BAD_OPTION', 'list takes --domain D or --inventory [ID].', 'usage: chemlab list [--domain D | --inventory [ID]] [--json]')
        rows = []
        for iid, inst in self.backend.instruments.items():
            if dom and inst.domain != dom:
                continue
            rows.append({'id': iid, 'name': inst.name, 'brand': inst.brand, 'model': inst.model, 'domain': inst.domain,
                         'front_panel': inst.panel is not None, 'class_verbs': bool(self.adapters.get(iid) and self.adapters[iid].verbs())})
        w = max([len(r['id']) for r in rows] + [10])
        lines = ['%d instruments, t = %.1f s (simulated)' % (len(rows), self.backend.now()), '',
                 '%-*s  %-16s %s' % (w, 'ID', 'DOMAIN', 'NAME')]
        for r in rows:
            nm = r['name'] + (' - %s %s' % (r['brand'], r['model']) if r['brand'] else '')
            lines.append('%-*s  %-16s %s%s' % (w, r['id'], r['domain'] or '-', nm, '  [panel]' if r['front_panel'] else ''))
        lines += ['', 'next: chemlab describe ID; the lab\'s bottles, standards, consumables and gas cylinders: chemlab list --inventory [ID]']
        return {'text': '\n'.join(lines) + '\n', 'json': {'time_s': self.backend.now(), 'instruments': rows}}

    def _visible_ids(self, iid, ids):
        v = self._inst(iid).view
        return [x for x in ids or [] if x in v.slots]

    def _cap_entry(self, iid, cid, c, full):
        v = self._inst(iid).view
        props = (c.get('input_schema') or {}).get('properties') or {}
        req = set((c.get('input_schema') or {}).get('required') or [])
        e = {'id': cid, 'kind': c.get('kind'), 'summary': summary_en(c),
             'arguments': [{'name': k, 'type': type_short(s), 'unit': unit_of(s), 'required': k in req,
                            **({k2: s[k2] for k2 in ('minimum', 'maximum', 'exclusiveMinimum', 'enum') if k2 in s})}
                           for k, s in props.items()]}
        if full:
            ex = c.get('execution') or {}
            e.update({'description_zh': c.get('function_zh'), 'input_schema': c.get('input_schema'), 'output_schema': c.get('output_schema'),
                      'example_arguments': c.get('example_arguments'),
                      'errors': [{'code': x.get('code'), 'meaning_zh': x.get('meaning_zh'), 'side_effects': x.get('side_effects')}
                                 for x in c.get('errors') or []],
                      'execution': {k: ex.get(k) for k in ('mode', 'time_domain', 'timeout_s', 'cancellation', 'retry', 'completion_criterion',
                                                           'partial_failure_zh')},
                      'preconditions': [{'state_ids': self._visible_ids(iid, p.get('state_ids')), 'description_zh': p.get('description_zh')}
                                        for p in c.get('preconditions') or []],
                      'effects': [{'state_id': x['state_id'], 'timing': x.get('timing')}
                                  for x in c.get('state_effects') or [] if x.get('state_id') in v.slots],
                      'observes': self._visible_ids(iid, c.get('observes')),
                      'component_ids': c.get('component_ids'), 'limitations': c.get('limitations')})
        return e

    def _slot_entry(self, iid, sid, s, full):
        wv = s.get('write_via') or None
        e = {'id': sid, 'type': type_short(s.get('value_schema') or {}), 'unit': None if s.get('unit') in (None, '', '1') else s.get('unit'),
             'access': s.get('access'), 'writable_via': (wv or {}).get('capability_id') if isinstance(wv, dict) else None}
        if full:
            e.update({'meaning_zh': s.get('meaning_zh'), 'value_schema': s.get('value_schema'), 'origin': s.get('origin'),
                      'reference_conditions': s.get('reference_conditions'), 'sampling': s.get('sampling')})
        return e

    def _panel_view(self, iid):
        inst = self._inst(iid)
        if inst.panel is None:
            return None
        caps = inst.view.capabilities
        spec = inst.panel.get('spec') or {}
        sin, sdisp = spec.get('inputs') or {}, spec.get('displays') or {}

        def enum(cap, prop):
            return list((((caps.get(cap) or {}).get('input_schema') or {}).get('properties') or {}).get(prop, {}).get('enum') or [])
        keys = enum('panel.press', 'input_id')
        knobs = enum('panel.turn', 'input_id')
        switches = enum('panel.switch', 'input_id')
        touch = enum('panel.touch', 'display_id')
        displays = enum('panel.capture', 'display_id')
        out = {'ui_class': spec.get('ui_class'), 'main_display': inst.panel.get('main_display'),
               'gestures': sorted(g for g, (cap, _, _) in GESTURES.items() if cap in caps),
               'keys': [{'id': k, 'kind': (sin.get(k) or {}).get('kind'), 'name': (sin.get(k) or {}).get('name')} for k in keys],
               'knobs': [{'id': k, 'kind': (sin.get(k) or {}).get('kind'), 'name': (sin.get(k) or {}).get('name')} for k in knobs],
               'switches': [{'id': k, 'positions': (sin.get(k) or {}).get('positions'), 'name': (sin.get(k) or {}).get('name')} for k in switches],
               'touch_displays': touch,
               'displays': [{'id': d, 'kind': (sdisp.get(d) or {}).get('kind'), 'size': (sdisp.get(d) or {}).get('size')} for d in displays],
               'capture': 'panel.capture' in caps}
        return out

    def _describe(self, iid, caller, full):
        inst = self._inst(iid)
        v = inst.view
        caps = [self._cap_entry(iid, cid, c, full) for cid, c in sorted(v.capabilities.items())]
        slots = [self._slot_entry(iid, sid, s, full) for sid, s in sorted(v.slots.items())]
        panel = self._panel_view(iid)
        ad = self.adapters.get(iid)
        head = {'id': iid, 'name': inst.name, 'brand': inst.brand, 'model': inst.model, 'domain': inst.domain,
                'asset_type_id': inst.asset_type_id, 'asset_version': inst.contract.get('asset_version')}
        js = {'instrument': head, 'time_s': self.backend.now(), 'capabilities': caps, 'state_slots': slots, 'panel': panel,
              'class_verbs': ad.verbs() if ad else {},
              'conventions': {'values': 'as the contract defines them (SI units); no unit conversion',
                              'checks': 'the instrument checks every call itself'}}
        if full:
            js['instrument']['description_zh'] = inst.contract.get('description_zh')
            js['instrument']['name_zh'] = inst.contract.get('name_zh')
        lines = ['%s - %s%s' % (iid, inst.name, ' (%s %s)' % (inst.brand, inst.model) if inst.brand else ''),
                 'domain %s; t = %.1f s; values in SI units as the contract defines them; the instrument checks every call.'
                 % (inst.domain or '-', self.backend.now()), '',
                 'capabilities (%d)   chemlab call %s CAP [--ARG VALUE ...]   chemlab help %s CAP' % (len(caps), iid, iid)]
        w = max([len(c['id']) for c in caps] + [8])
        common = set(PROTOCOL_ERRORS)
        for cid, c in sorted(v.capabilities.items()):
            flag = ''
            ex = c.get('execution') or {}
            if ex.get('mode') and ex['mode'] != 'synchronous':
                flag = '  [async]'
            lines.append('  %-*s  %s%s' % (w, cid, arg_signature(c.get('input_schema')), flag))
            lines.append('  %-*s    %s' % (w, '', self._short_help(iid, c, common)))
        lines.insert(4, '  (every call can also return %s; those are not repeated below)' % ', '.join(PROTOCOL_ERRORS))
        lines += ['', 'state (%d)   chemlab read %s [SLOT ...]' % (len(slots), iid)]
        w = max([len(s['id']) for s in slots] + [8])
        for s in slots:
            acc = '' if s['access'] == 'read_only' else ('  writable (chemlab write, or %s)' % s['writable_via'] if s['writable_via'] else '  ' + str(s['access']))
            unit = ' ' + s['unit'] if s['unit'] and s['unit'] not in s['type'] else ''
            lines.append('  %-*s  %s%s%s' % (w, s['id'], s['type'], unit, acc))
        if panel:
            lines += ['', 'front panel (%s)   chemlab help %s panel' % (panel['ui_class'] or 'panel', iid)]
            if panel['keys']:
                lines.append('  chemlab panel %s press KEY [--hold S]      KEY: %s' % (iid, ', '.join(k['id'] for k in panel['keys'])))
            if panel['knobs']:
                lines.append('  chemlab panel %s turn KNOB DETENTS        KNOB: %s' % (iid, ', '.join(k['id'] for k in panel['knobs'])))
            if panel['switches']:
                lines.append('  chemlab panel %s switch SWITCH POSITION   %s' % (
                    iid, '; '.join('%s: %s' % (k['id'], '|'.join(k['positions'] or []) or 'POSITION') for k in panel['switches'])))
            if panel['touch_displays']:
                lines.append('  chemlab panel %s touch DISPLAY X Y [--hold S] [--to X2 Y2]   DISPLAY: %s' % (iid, ', '.join(panel['touch_displays'])))
            if panel['capture']:
                lines.append('  chemlab screen %s [DISPLAY] -o FILE.png   DISPLAY: %s (main: %s)' % (
                    iid, ', '.join('%s%s' % (d['id'], ' %dx%d' % tuple(d['size'][:2]) if d.get('size') else '') for d in panel['displays']),
                    panel['main_display']))
        inv = self.inventory_items(iid)          # before the class section (a class adapter replaces that part)
        if inv:
            lines += ['', 'inventory for %s (%d containers; moving one is a hands / robot job)   chemlab list --inventory %s' % (
                iid, len(inv), iid)] + ['  ' + self.inventory_line(r) for r in inv[:12]]
            if len(inv) > 12:
                lines.append('  ... %d more: chemlab list --inventory %s' % (len(inv) - 12, iid))
            js['inventory'] = inv
        if ad and ad.verbs():
            lines += ['', 'class-level verbs   chemlab run %s VERB ...' % iid] + ['  %-16s %s' % kv for kv in ad.verbs().items()]
        lines += ['', 'full form: chemlab describe %s --json' % iid]
        return {'text': '\n'.join(lines) + '\n', 'json': js}

    def _short_help(self, iid, c, common):
        """One line of English from the contract structure: kind, what it sets at once, the instrument's own error codes."""
        v = self._inst(iid).view
        bits = [KIND_EN.get(c.get('kind'), c.get('kind') or 'capability')]
        sets = []
        for x in c.get('state_effects') or []:
            sid = x.get('state_id')
            if x.get('timing') == 'immediate' and sid in v.slots and sid not in sets:
                sets.append(sid)
        if sets:
            bits.append('sets ' + ', '.join(sets[:3]) + (' +%d' % (len(sets) - 3) if len(sets) > 3 else ''))
        errs = [e.get('code') for e in c.get('errors') or [] if e.get('code') and e.get('code') not in common]
        if errs:
            bits.append('errors: ' + ' '.join(errs[:8]) + (' +%d' % (len(errs) - 8) if len(errs) > 8 else ''))
        return '; '.join(bits)

    def _help(self, iid, cap_id):
        inst = self._inst(iid)
        c = self._cap(iid, cap_id, for_call=False)
        e = self._cap_entry(iid, cap_id, c, True)
        lines = ['%s %s - %s' % (iid, cap_id, e['summary']), '',
                 'usage: chemlab call %s %s %s' % (iid, cap_id, arg_signature(c.get('input_schema')))]
        props = (c.get('input_schema') or {}).get('properties') or {}
        req = set((c.get('input_schema') or {}).get('required') or [])
        if props:
            lines += ['', 'arguments (values as the contract defines them, SI units; give JSON for lists/objects):']
            for k, s in props.items():
                lines.append('  --%s%s' % (k, '' if k in req else ' (optional)'))
                lines += type_long(s, '      ')
            if (c.get('input_schema') or {}).get('additionalProperties') is False:
                lines.append('  (no other arguments)')
        lines += ['', 'example (the contract\'s example arguments):'] + ['  ' + x for x in example_call(iid, cap_id, c.get('example_arguments'))]
        pre = [p for p in e['preconditions'] if p['state_ids'] or p['description_zh']]
        if pre:
            lines += ['', 'checked by the instrument (contract preconditions):']
            for p in pre:
                if p['state_ids']:
                    lines.append('  state: %s' % ', '.join(p['state_ids']))
                if p['description_zh']:
                    lines.append('  (zh) %s' % p['description_zh'])
        now = sorted({x['state_id'] for x in e['effects'] if x['timing'] == 'immediate'})
        later = sorted({x['state_id'] for x in e['effects'] if x['timing'] != 'immediate'} - set(now))
        if now:
            lines += ['', 'changes at once: ' + ', '.join(now)]
        if later:
            lines += ['changes as time passes: ' + _clip(', '.join(later), 300)]
        if e['errors']:
            lines += ['', 'errors it may return (code, contract description, side effects):']
            w = max(len(x['code'] or '') for x in e['errors'])
            for x in e['errors']:
                lines.append('  %-*s  %s  [%s]' % (w, x['code'], x['meaning_zh'] or '', x['side_effects'] or '-'))
        out = (c.get('output_schema') or {}).get('properties') or {}
        if out:
            lines += ['', 'result: ' + _clip(', '.join('%s %s' % (k, type_short(s)) for k, s in out.items()), 600)]
        ex = e['execution']
        lines += ['', 'execution: %s%s; cancellation %s; retry %s' % (ex.get('mode') or '-', ', timeout %s s' % ex['timeout_s'] if ex.get('timeout_s') else '',
                                                                   ex.get('cancellation') or '-', ex.get('retry') or '-')]
        if ex.get('mode') and ex['mode'] != 'synchronous':
            lines.append('  returns an operation: follow it with `chemlab status OP`, `chemlab wait OP`, `chemlab cancel OP`.')
        if c.get('function_zh'):
            lines += ['', 'contract description (zh): %s' % c['function_zh']]
        return {'text': '\n'.join(lines) + '\n', 'json': {'instrument': iid, 'capability': e}}

    def _panel_help(self, iid):
        p = self._panel_view(iid)
        if p is None:
            raise usage('NO_PANEL', '%s has no front-panel model.' % iid, 'use its capabilities: `chemlab describe %s`.' % iid)
        lines = ['%s front panel (%s); gestures go to the instrument\'s panel.* capabilities and act as you.' % (iid, p['ui_class']), '']
        if 'press' in p['gestures']:
            lines.append('chemlab panel %s press KEY [--hold SECONDS] [--with KEY2 ...]    -> panel.press' % iid)
            lines += ['    %-22s %s' % (k['id'], k['name'] or k['kind'] or '') for k in p['keys']]
        if 'turn' in p['gestures']:
            lines.append('chemlab panel %s turn KNOB DETENTS   (+ clockwise)              -> panel.turn' % iid)
            lines += ['    %-22s %s' % (k['id'], k['name'] or k['kind'] or '') for k in p['knobs']]
        if 'switch' in p['gestures']:
            lines.append('chemlab panel %s switch SWITCH POSITION [--with KEY ...]        -> panel.switch' % iid)
            lines += ['    %-22s %s  (%s)' % (k['id'], '|'.join(k['positions'] or []), k['name'] or '') for k in p['switches']]
        if 'touch' in p['gestures']:
            lines.append('chemlab panel %s touch DISPLAY X Y [--hold S] [--to X2 Y2]      -> panel.touch (pixels of that display)' % iid)
        if p['capture']:
            lines.append('chemlab screen %s [DISPLAY] [-o FILE.png]   -> panel.capture, PNG in your work dir' % iid)
            lines += ['    %-22s %s %s%s' % (d['id'], d['kind'] or '', 'x'.join(map(str, d['size'][:2])) if d.get('size') else '',
                                             '  (main)' if d['id'] == p['main_display'] else '') for d in p['displays']]
        return {'text': '\n'.join(lines) + '\n', 'json': {'instrument': iid, 'panel': p}}

    # ================================================================ read / write
    def _read(self, req):
        iid = req.instrument
        inst = self._inst(iid)
        v = inst.view
        want = req.args[1:]
        if any(w.startswith('--') for w in want):
            raise usage('BAD_OPTION', 'read takes slot ids only.', 'usage: chemlab read %s [SLOT ...] [--json]' % iid)
        ids = None
        if want:
            ids = []
            for w in want:
                if w not in v.slots:
                    raise usage('UNKNOWN_SLOT', '%s has no state slot %r you can read.' % (iid, w), 'see `chemlab describe %s`.' % iid)
                ids.append(w)
        r = self.backend.read(req.caller, iid, ids)
        status = r.get('status')
        self._record(req.calls, iid, 'read', state_ids=ids, status=status, code=(r.get('error') or {}).get('code'))
        if status != 'succeeded':
            raise self._error_from_response(iid, None, r, what='read')
        obs = r.get('observations') or {}
        out = {}
        for sid, o in obs.items():
            if sid in v.slots:                                               # agent reads show contract agent slots only
                out[sid] = o
        t = self.backend.now()
        lines = ['%s at t = %.1f s%s' % (iid, t, ' (state revision %s)' % r['state_revision'] if r.get('state_revision') is not None else '')]
        w = max([len(k) for k in out] + [8])
        for k in sorted(out):
            o = out[k] if isinstance(out[k], dict) else {'value': out[k]}
            unit = o.get('unit')
            q = o.get('quality')
            extra = '  (%s)' % o['unavailable_reason'] if o.get('unavailable_reason') else ''
            lines.append('  %-*s = %s%s%s%s' % (w, k, _short(o.get('value'), 200), ' ' + unit if unit not in (None, '', '1') else '',
                                              '  [%s]' % q if q and q != 'valid' else '', extra))
        notes = self._clock_notes(iid)
        return {'text': '\n'.join(lines) + '\n', 'notes': notes,
                'json': {'instrument': iid, 'time_s': t, 'state_revision': r.get('state_revision'), 'observations': out}}

    def _write(self, req):
        iid = req.instrument
        v = self._inst(iid).view
        a = req.args[1:]
        if len(a) != 2:
            raise usage('BAD_ARGUMENTS', 'write needs a slot and a value.', 'usage: chemlab write %s SLOT VALUE' % iid)
        sid = a[0]
        cid = sid if sid in v.slots else None
        if cid is None:
            raise usage('UNKNOWN_SLOT', '%s has no state slot %r you can write.' % (iid, sid), 'see `chemlab describe %s`.' % iid)
        spec = v.slots[cid]
        if spec.get('access') != 'read_write':
            raise usage('READ_ONLY', '%s.%s is read-only.' % (iid, sid), 'see `chemlab describe %s` for writable slots.' % iid)
        value = coerce(a[1], spec.get('value_schema') or {}, sid)
        r = self.backend.write(req.caller, iid, cid, value, req.request_id)
        self._record(req.calls, iid, 'write', state_id=cid, value=value, request_id=req.request_id, status=r.get('status'),
                     code=(r.get('error') or {}).get('code'))
        wv = spec.get('write_via') or {}
        if r.get('status') not in OK_STATUSES:
            raise self._error_from_response(iid, wv.get('capability_id'), r, what='write')
        return {'text': 'ok: %s %s = %s (t = %.1f s)\n' % (iid, cid, _short(value, 100), self.backend.now()),
                'json': {'instrument': iid, 'state_id': cid, 'value': value, 'status': r.get('status'), 'result': r.get('result')},
                'notes': self._clock_notes(iid)}

    # ================================================================ call
    def _parse_call_args(self, iid, cap_id, c, tokens):
        props = (c.get('input_schema') or {}).get('properties') or {}
        args, given = {}, set()
        save = None
        i = 0
        while i < len(tokens):
            t = tokens[i]
            if not t.startswith('--') or len(t) < 3:
                raise usage('UNEXPECTED_ARGUMENT', 'unexpected %r.' % t, 'arguments are --NAME VALUE (see `chemlab help %s %s`).' % (iid, cap_id))
            name, eq, val = t[2:].partition('=')
            if not eq:
                i += 1
                if i >= len(tokens):
                    raise usage('MISSING_VALUE', '--%s needs a value.' % name, 'see `chemlab help %s %s`.' % (iid, cap_id))
                val = tokens[i]
            if name == 'args':
                try:
                    obj = json.loads(val)
                except ValueError as exc:
                    raise usage('BAD_JSON', '--args is not valid JSON (%s).' % exc, 'give one JSON object, e.g. --args \'{"x": 1}\'.')
                if not isinstance(obj, dict):
                    raise usage('BAD_JSON', '--args must be a JSON object.', 'give one JSON object, e.g. --args \'{"x": 1}\'.')
                for k, v in obj.items():
                    if k in given:
                        raise usage('REPEATED_ARGUMENT', '%s is given twice.' % k, 'give it once.')
                    args[k] = v
                    given.add(k)
            elif name == 'save-to':
                save = val
            else:
                if name in given:
                    raise usage('REPEATED_ARGUMENT', '--%s is given twice.' % name, 'give it once.')
                args[name] = coerce(val, props.get(name) or {}, name)
                given.add(name)
            i += 1
        return args, save

    def _invoke(self, req, iid, cap_id, args):
        r = self.backend.invoke(req.caller, iid, cap_id, args, req.request_id)
        err = r.get('error') or {}
        self._record(req.calls, iid, 'invoke', capability=cap_id, arguments=copy.deepcopy(args), request_id=req.request_id,
                     status=r.get('status'), code=err.get('code'), operation_id=r.get('operation_id'))
        return r

    def _call(self, req):
        iid = req.instrument
        if len(req.args) < 2:
            raise usage('MISSING_CAPABILITY', 'call needs a capability.', 'usage: chemlab call %s CAP [--ARG VALUE ...]; see `chemlab describe %s`.' % (iid, iid))
        cap_id = req.args[1]
        c = self._cap(iid, cap_id)
        args, save = self._parse_call_args(iid, cap_id, c, req.args[2:])
        r = self._invoke(req, iid, cap_id, args)
        return self._call_result(req, iid, cap_id, r, save)

    def _call_result(self, req, iid, cap_id, r, save=None):
        status = r.get('status')
        if status not in OK_STATUSES:
            e = self._error_from_response(iid, cap_id, r)
            e.extra = {'instrument': iid, 'capability': cap_id, 'status': status}
            raise e
        result = copy.deepcopy(r.get('result'))
        files = self._export_files(iid, result)
        js = {'instrument': iid, 'capability': cap_id, 'status': status, 'time_s': self.backend.now(), 'result': result}
        if files:
            js['files'] = files
        lines = []
        op = r.get('operation_id')
        if op and status in ('accepted', 'running'):
            h = self._new_op(req, iid, cap_id, op, status)
            js['operation'] = h
            js['operation_id'] = op
            lines.append('started: %s %s -> operation %s (%s, t = %.1f s)' % (iid, cap_id, h, status, self.backend.now()))
            lines.append('follow it: chemlab status %s | chemlab wait %s | chemlab cancel %s' % (h, h, h))
        else:
            lines.append('ok: %s %s (%s, t = %.1f s)' % (iid, cap_id, status, self.backend.now()))
        lines += self._result_lines(iid, cap_id, result)
        for f in files:
            lines.append('file: %s' % f)
        if save:
            p = self._work_path(save)
            p.write_text(json.dumps(scrub_paths(self._inst(iid).view.redact(js)), ensure_ascii=False, indent=1, default=str) + '\n')
            lines.append('saved the full reply to %s (in your work dir)' % p.relative_to(self.work_dir))
        return {'text': '\n'.join(lines) + '\n', 'json': js, 'notes': self._clock_notes(iid)}

    def _result_lines(self, iid, cap_id, result):
        if not result:
            return []
        out = ((self._inst(iid).contract['capabilities'].get(cap_id) or {}).get('output_schema') or {}).get('properties') or {}
        if not isinstance(result, dict):
            return ['  result = %s' % _short(result)]
        lines = []
        w = max(len(k) for k in result)
        for k, v in list(result.items())[:40]:
            u = unit_of(out.get(k) or {})
            lines.append('  %-*s = %s%s' % (w, k, _short(v, 300), ' ' + u if u and not isinstance(v, (dict, list)) else ''))
        if len(result) > 40:
            lines.append('  ... %d more fields (use --json)' % (len(result) - 40))
        return lines

    def _work_path(self, rel):
        p = Path(rel)
        if p.is_absolute():
            try:
                p.resolve().relative_to(self.work_dir)
            except ValueError:
                raise usage('OUTSIDE_WORK_DIR', '%s is outside your work dir.' % rel, 'give a path relative to your work dir.')
            dst = p.resolve()
        else:
            dst = (self.work_dir / p).resolve()
            try:
                dst.relative_to(self.work_dir)
            except ValueError:
                raise usage('OUTSIDE_WORK_DIR', '%s is outside your work dir.' % rel, 'give a path relative to your work dir.')
        dst.parent.mkdir(parents=True, exist_ok=True)
        return dst

    def _export_files(self, iid, result, dest=None):
        """Files a runtime wrote (results with a 'path') are copied into the work dir; the result then names that copy."""
        found = []

        def walk(o):
            if isinstance(o, dict):
                p = o.get('path')
                if isinstance(p, str) and os.path.isabs(p) and os.path.isfile(p):
                    name = dest or 'files/%s_%s' % (iid, os.path.basename(p))
                    dst = self._work_path(name)
                    with open(p, 'rb') as fsrc, open(dst, 'wb') as fdst:
                        fdst.write(fsrc.read())
                    o['path'] = str(dst.relative_to(self.work_dir))
                    found.append(o['path'])
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)
        walk(result)
        return found

    # ================================================================ operations
    def _new_op(self, req, iid, cap_id, op_id, status):
        self._op_seq += 1
        h = 'op-%d' % self._op_seq
        self.ops[h] = {'handle': h, 'caller': req.caller, 'instrument': iid, 'capability': cap_id, 'operation_id': op_id,
                       'started_s': self.backend.now(), 'status': status, 'request_id': req.request_id}
        return h

    def _op(self, req, handle):
        o = self.ops.get(handle)
        if o is None or o['caller'] != req.caller:
            raise usage('UNKNOWN_OPERATION', 'no operation %r of yours.' % handle, 'see `chemlab status` for your operations.')
        return o

    def _poll(self, req, o):
        iid = o['instrument']
        r = self.backend.poll(req.caller, iid, o['operation_id'])
        if r is None:
            raise CmdError('unsupported', 'NO_POLL', '%s has no operation status query.' % iid, side_effects='none',
                           fix='read its state instead: `chemlab read %s`.' % iid)
        st = self._op_status(r)
        self._record(req.calls, iid, 'poll', operation_id=o['operation_id'], status=r.get('status'), op_status=st,
                     code=(r.get('error') or {}).get('code'))
        if r.get('raised') or ((r.get('status') in ('rejected', 'failed', 'unsupported')) and r.get('error')):
            e = self._error_from_response(iid, o['capability'], r, what='status query')
            raise e
        o['status'] = st
        return r, st

    @staticmethod
    def _op_status(r):
        """The operation's state from a poll answer: the protocol's top-level status (accepted / running / succeeded / failed /
        cancelled); an answer whose call status is 'succeeded' but whose result says the work is still running counts as
        running."""
        st = r.get('status')
        res = r.get('result') if isinstance(r.get('result'), dict) else {}
        if st == 'succeeded':
            for k in ('operation_status', 'phase', 'state', 'status'):
                if isinstance(res.get(k), str) and res[k] in RUNNING:
                    return res[k]
        return st

    def _status(self, req):
        if not req.args:
            mine = [o for o in self.ops.values() if o['caller'] == req.caller]
            lines = ['your operations (t = %.1f s):' % self.backend.now()] if mine else ['no operations yet']
            for o in mine:
                lines.append('  %-6s %-28s %-28s last status: %s' % (o['handle'], o['instrument'], o['capability'], o['status']))
            js = {'operations': [dict(o, caller=None) for o in mine]}
            if getattr(self, 'class_jobs', None):                         # class-level jobs (compile layer)
                from .compile.jobs import listing
                jl, jr = listing(self, req.caller)
                if jr:
                    lines += ['your jobs:'] + jl
                    js['jobs'] = jr
            return {'text': '\n'.join(lines) + '\n', 'json': js}, None
        if len(req.args) != 1:
            raise usage('BAD_ARGUMENTS', 'status takes one operation.', 'usage: chemlab status [OP]')
        o = self._op(req, req.args[0])
        r, st = self._poll(req, o)
        res = r.get('result')
        js = {'operation': o['handle'], 'instrument': o['instrument'], 'capability': o['capability'], 'operation_id': o['operation_id'],
              'status': st, 'done': st not in RUNNING, 'time_s': self.backend.now(), 'result': res}
        lines = ['%s %s %s: %s (t = %.1f s, started at %.1f s)' % (o['handle'], o['instrument'], o['capability'], st, self.backend.now(), o['started_s'])]
        if isinstance(res, dict):
            lines += self._result_lines(o['instrument'], o['capability'], res)
        return {'text': '\n'.join(lines) + '\n', 'json': js, 'notes': self._clock_notes(o['instrument'])}, o['instrument']

    def _cancel(self, req):
        if len(req.args) != 1:
            raise usage('BAD_ARGUMENTS', 'cancel takes one operation.', 'usage: chemlab cancel OP')
        o = self._op(req, req.args[0])
        iid = o['instrument']
        r = self.backend.cancel(req.caller, iid, o['operation_id'], req.request_id)
        if r is None:
            raise CmdError('unsupported', 'NO_CANCEL', '%s cannot cancel operations.' % iid, side_effects='none (nothing was sent)',
                           see='chemlab help %s %s' % (iid, o['capability']))
        self._record(req.calls, iid, 'cancel', operation_id=o['operation_id'], request_id=req.request_id, status=r.get('status'),
                     code=(r.get('error') or {}).get('code'))
        if r.get('status') not in OK_STATUSES and r.get('status') not in ('cancelled', 'canceled'):
            raise self._error_from_response(iid, o['capability'], r, what='cancel')
        o['status'] = 'cancel requested'
        return {'text': 'ok: cancel of %s sent (%s %s, t = %.1f s); check with chemlab status %s\n' % (o['handle'], iid, o['capability'],
                                                                                                    self.backend.now(), o['handle']),
                'json': {'operation': o['handle'], 'status': r.get('status'), 'result': r.get('result')}}, iid

    # ================================================================ time
    def _wait(self, req):
        a = req.args
        if not a:
            raise usage('MISSING_ARGUMENT', 'wait needs seconds or an operation.', 'usage: chemlab wait SECONDS | chemlab wait OP [--max S]')
        if a[0] in self.ops or a[0].startswith('op-'):
            o = self._op(req, a[0])
            mx = None
            if len(a) == 3 and a[1] == '--max':
                mx = _seconds(a[2])
            elif len(a) != 1:
                raise usage('BAD_ARGUMENTS', 'usage: chemlab wait OP [--max SECONDS]')
            if mx is None:
                to = ((self._inst(o['instrument']).contract['capabilities'].get(o['capability']) or {}).get('execution') or {}).get('timeout_s')
                mx = float(to) if isinstance(to, (int, float)) and to > 0 else 3600.0
            mx = min(mx, self.max_wait_s)
            t0 = self.backend.now()
            r, st = self._poll(req, o)
            state = {'st': st}
            if st in RUNNING:
                def until():
                    rr = self.backend.poll(req.caller, o['instrument'], o['operation_id']) or {}
                    state['st'] = self._op_status(rr)
                    return state['st'] not in RUNNING
                self.backend.advance(mx, until=until)
                r, st = self._poll(req, o)
            waited = self.backend.now() - t0
            done = st not in RUNNING
            js = {'operation': o['handle'], 'status': st, 'done': done, 'waited_s': waited, 'time_s': self.backend.now(), 'result': r.get('result')}
            text = '%s %s after %.1f s of waiting (t = %.1f s)%s\n' % (o['handle'], st, waited, self.backend.now(),
                                                                     '' if done else '; still running (gave up after --max %.0f s)' % mx)
            res = r.get('result')
            if isinstance(res, dict):
                text += '\n'.join(self._result_lines(o['instrument'], o['capability'], res)) + '\n'
            return {'text': text, 'json': js, 'notes': self._clock_notes(o['instrument'])}, o['instrument']
        if len(a) != 1:
            raise usage('BAD_ARGUMENTS', 'wait takes one number of seconds.', 'usage: chemlab wait SECONDS')
        s = _seconds(a[0])
        if s > self.max_wait_s:
            raise usage('TOO_LONG', 'wait at most %.0f s per command.' % self.max_wait_s, 'wait in several steps.')
        t0 = self.backend.now()
        self.backend.advance(s)
        self._record(req.calls, None, 'advance', seconds=s)
        return {'text': 't = %.1f s -> %.1f s (waited %g s)\n' % (t0, self.backend.now(), s),
                'json': {'from_s': t0, 'time_s': self.backend.now(), 'waited_s': s}}, None

    # ================================================================ front panel
    def _panel(self, req):
        iid = req.instrument
        a = req.args[1:]
        if not a or a[0] not in GESTURES:
            raise usage('BAD_GESTURE', 'panel needs a gesture: %s.' % ', '.join(GESTURES), 'see `chemlab help %s panel`.' % iid)
        g = a[0]
        cap_id, names, meta = GESTURES[g]
        inst = self._inst(iid)
        if inst.panel is None or cap_id not in inst.view.capabilities:
            raise usage('NO_SUCH_GESTURE', '%s has no %s gesture.' % (iid, g), 'see `chemlab help %s panel`.' % iid)
        c = inst.view.capabilities[cap_id]
        props = (c.get('input_schema') or {}).get('properties') or {}
        pos, opts = [], {}
        i = 1
        while i < len(a):
            t = a[i]
            if t in ('--hold', '--with', '--to'):
                n = 2 if t == '--to' else 1
                vals = a[i + 1:i + 1 + n]
                if len(vals) != n:
                    raise usage('MISSING_VALUE', '%s needs %d value(s).' % (t, n), 'see `chemlab help %s panel`.' % iid)
                opts.setdefault(t, []).append(vals)
                i += 1 + n
            elif t.startswith('--'):
                raise usage('BAD_OPTION', 'unknown option %s.' % t, 'see `chemlab help %s panel`.' % iid)
            else:
                pos.append(t)
                i += 1
        if len(pos) != len(names):
            raise usage('BAD_ARGUMENTS', 'usage: chemlab panel %s %s %s' % (iid, g, meta), 'see `chemlab help %s panel`.' % iid)
        args = {n: coerce(v, props.get(n) or {}, n) for n, v in zip(names, pos)}
        if '--hold' in opts:
            if 'duration_s' not in props:
                raise usage('BAD_OPTION', '%s does not take --hold.' % g)
            args['duration_s'] = coerce(opts['--hold'][-1][0], props['duration_s'], 'hold')
        if '--with' in opts:
            if 'held' not in props:
                raise usage('BAD_OPTION', '%s does not take --with on %s.' % (g, iid))
            args['held'] = [v[0] for v in opts['--with']]
        if '--to' in opts:
            if 'to_x' not in props:
                raise usage('BAD_OPTION', '%s does not take --to.' % g)
            args['to_x'] = coerce(opts['--to'][-1][0], props['to_x'], 'to_x')
            args['to_y'] = coerce(opts['--to'][-1][1], props['to_y'], 'to_y')
        r = self._invoke(req, iid, cap_id, args)
        if r.get('status') not in OK_STATUSES:
            e = self._error_from_response(iid, cap_id, r)
            e.extra = {'instrument': iid, 'capability': cap_id, 'status': r.get('status')}
            raise e
        res = r.get('result') or {}
        acc = res.get('accepted') if isinstance(res, dict) else None
        word = {True: 'accepted', False: 'ignored by the instrument', None: 'done'}[acc if acc in (True, False) else None]
        text = '%s: %s %s -> %s (t = %.1f s); see it with chemlab screen %s\n' % (iid, g, ' '.join(pos), word, self.backend.now(), iid)
        return {'text': text, 'json': {'instrument': iid, 'capability': cap_id, 'arguments': args, 'status': r.get('status'),
                                       'accepted': acc, 'result': res, 'time_s': self.backend.now()}, 'notes': self._clock_notes(iid)}

    def _screen(self, req):
        iid = req.instrument
        inst = self._inst(iid)
        if 'panel.capture' not in inst.view.capabilities:
            raise usage('NO_SCREEN', '%s has no screen capture.' % iid, 'read its state: `chemlab read %s`.' % iid)
        a = req.args[1:]
        out, disp = None, None
        i = 0
        while i < len(a):
            if a[i] in ('-o', '--out'):
                if i + 1 >= len(a):
                    raise usage('MISSING_VALUE', '-o needs a file name.')
                out = a[i + 1]
                i += 2
            elif a[i].startswith('-'):
                raise usage('BAD_OPTION', 'unknown option %s.' % a[i], 'usage: chemlab screen %s [DISPLAY] [-o FILE.png]' % iid)
            else:
                if disp is not None:
                    raise usage('EXTRA_ARGUMENT', 'one display at a time.', 'usage: chemlab screen %s [DISPLAY] [-o FILE.png]' % iid)
                disp = a[i]
                i += 1
        disp = disp or (inst.panel or {}).get('main_display')
        out = out or 'screen_%s_%s.png' % (iid, disp)
        dst = self._work_path(out)
        r = self._invoke(req, iid, 'panel.capture', {'display_id': disp})
        if r.get('status') != 'succeeded':
            e = self._error_from_response(iid, 'panel.capture', r)
            raise e
        src = self.backend.capture_file(r)
        if src is None:
            raise CmdError('unexpected', 'NO_IMAGE', 'panel.capture of %s returned no image file.' % iid, side_effects='none')
        with open(src, 'rb') as fsrc, open(dst, 'wb') as fdst:
            fdst.write(fsrc.read())
        img = dict((r.get('result') or {}).get('image') or {})
        img['path'] = str(dst.relative_to(self.work_dir))
        js = {'instrument': iid, 'display_id': disp, 'file': img['path'], 'image': img, 'time_s': self.backend.now()}
        text = 'wrote %s (%s x %s PNG of %s %s, t = %.1f s)\n' % (img['path'], img.get('width'), img.get('height'), iid, disp, self.backend.now())
        return {'text': text, 'json': js, 'notes': self._clock_notes(iid)}

    # ================================================================ log
    def _log_view(self, caller, args):
        last, full = 20, False
        i = 0
        while i < len(args):
            if args[i] == '--last' and i + 1 < len(args) and args[i + 1].isdigit():
                last = int(args[i + 1])
                i += 2
            elif args[i] == '--full':
                full = True
                i += 1
            else:
                raise usage('BAD_OPTION', 'log takes --last N and --full.', 'usage: chemlab log [--last N] [--full] [--json]')
        mine = [e for e in self.log.entries if e['caller'] == caller][-last:]
        lines = []
        for e in mine:
            outcome = 'ok' if e['exit'] == 0 else '%s (exit %d)' % (e.get('outcome'), e['exit'])
            lines.append('%-10s t=%8.1fs  %-64s -> %s' % (e['request_id'], e['sim_time_s'], ' '.join(e['argv'])[:64], outcome))
            if full:
                lines += ['    ' + x for x in (e.get('stdout') or '').splitlines()[:30]]
                lines += ['    ' + x for x in (e.get('stderr') or '').splitlines()[:30]]
        public = [{k: e.get(k) for k in ('request_id', 'wall_time', 'sim_time_s', 'argv', 'exit', 'outcome', 'stdout', 'stderr')} for e in mine]
        return {'text': ('\n'.join(lines) + '\n') if lines else 'no requests yet\n', 'json': {'entries': public}}


def _seconds(text):
    """Seconds: a bare number, or a number with a time unit (30s, 5min, 1.5h, 2d)."""
    try:
        s = float(text)
    except ValueError:
        from .compile.errors import CompileError
        from .compile.units import parse
        try:
            s = parse(text, 'duration', what='time')
        except CompileError:
            raise usage('BAD_VALUE', '%r is not a time.' % text, 'e.g. chemlab wait 60 (seconds), 30s, 5min or 1.5h')
    if not math.isfinite(s) or s <= 0:
        raise usage('BAD_VALUE', 'the time must be > 0.', 'e.g. chemlab wait 60 (seconds), 30s, 5min or 1.5h')
    return s


def overview_text():
    return ('chemlab - command line for the simulated lab (every instrument, its own contract capabilities)\n\n'
            'usage:\n'
            '  chemlab list [--domain D]                      instruments in the lab: id, name, domain\n'
            '  chemlab describe INST                          its capabilities (arguments with types, units, ranges) and state slots\n'
            '  chemlab help INST CAP                          one capability in full, with an example call\n'
            '  chemlab read INST [SLOT ...]                   observations with units and quality\n'
            '  chemlab call INST CAP [--ARG VALUE ...] [--args JSON] [--save-to FILE]\n'
            '                                                 invoke one capability (values as the contract defines them, SI)\n'
            '  chemlab write INST SLOT VALUE                  set a writable state slot\n'
            '  chemlab status [OP] | chemlab cancel OP        asynchronous operations (op-N from call)\n'
            '  chemlab wait TIME | chemlab wait OP [--max TIME]   let simulated time pass (the only way time moves); 60, 30s, 5min\n'
            '  chemlab time                                   the simulated clock\n'
            '  chemlab panel INST press KEY [--hold S] | turn KNOB N | switch SW POS | touch DISPLAY X Y [--hold S] [--to X2 Y2]\n'
            '  chemlab screen INST [DISPLAY] [-o FILE.png]    capture a display into your work dir\n'
            '  chemlab run INST [VERB ...] [--dry-run]        class-level verbs in plain units (80C, 50uL, 300rpm), where the\n'
            '                                                 instrument has them; long ones give job-N (status / wait / cancel job-N)\n'
            '  chemlab log [--last N] [--full]                your past requests and replies\n\n'
            'every command takes --json; --request-id ID makes a repeat of the same command return the first reply.\n'
            'exit codes: 0 ok, 2 bad usage (nothing sent), 3 refused or failed by the instrument, 4 unexpected error,\n'
            '            5 service unreachable, 6 not supported by this instrument twin\n')

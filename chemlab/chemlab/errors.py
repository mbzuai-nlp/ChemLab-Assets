"""Error classes of the core service, after SiLA 2: validation / defined execution / undefined execution.

Every error says three things: what is wrong, how to fix it, and whether the instrument changed.  Exit codes are stable:

  0  ok                  2  validation error (bad command, argument, unit, range, unknown location)
  3  defined execution error (a documented precondition or device refusal: no tip, not enough liquid, busy, ...)
  4  undefined execution error (backend crash, read-back mismatch, anything not foreseen)
  5  the service cannot be reached (printed by the client)
"""

EXIT_OK, EXIT_VALIDATION, EXIT_DEFINED, EXIT_UNDEFINED, EXIT_UNREACHABLE = 0, 2, 3, 4, 5
SILA_CLASS = {'validation': 'ValidationError', 'defined': 'DefinedExecutionError', 'undefined': 'UndefinedExecutionError'}
EXIT_FOR = {'validation': EXIT_VALIDATION, 'defined': EXIT_DEFINED, 'undefined': EXIT_UNDEFINED}


class ChemlabError(Exception):
    kind = 'undefined'

    def __init__(self, code, message, fix, *, changed='no', done=None, backend=None, detail=None):
        super().__init__(message)
        self.code, self.message, self.fix = code, message, fix
        self.changed = changed            # 'no' | 'yes' | 'unknown'
        self.done = list(done or [])      # steps that completed before the error (composite verbs)
        self.backend = backend            # {'capability': ..., 'status': ..., 'code': ...} of the refused call
        self.detail = dict(detail or {})

    @property
    def exit_code(self):
        return EXIT_FOR[self.kind]

    def to_json(self, request_id=None):
        out = {'class': self.kind, 'sila_class': SILA_CLASS[self.kind], 'code': self.code, 'message': self.message,
               'fix': self.fix, 'instrument_changed': self.changed, 'done': self.done}
        if self.backend:
            out['backend'] = self.backend
        if self.detail:
            out['detail'] = self.detail
        return out

    def to_text(self, request_id=None):
        lines = ['error[%s/%s]: %s' % (self.kind, self.code, self.message), 'fix: %s' % self.fix]
        changed = 'instrument changed: %s' % self.changed
        steps = [d for d in self.done if not d.startswith('move to ')]       # moves are in --json and the log
        if steps:
            changed += ' - done before the error: ' + '; '.join(steps)
        elif self.done:
            changed += ' - the gantry moved (%s)' % self.done[-1]
        lines.append(changed)
        if request_id:
            lines.append('request: %s' % request_id)
        return '\n'.join(lines) + '\n'


class ValidationError(ChemlabError):
    kind = 'validation'


class DefinedExecutionError(ChemlabError):
    kind = 'defined'


class UndefinedExecutionError(ChemlabError):
    kind = 'undefined'

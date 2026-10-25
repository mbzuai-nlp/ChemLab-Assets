"""The compile layer's error format: what is wrong, how to fix it, whether the instrument changed, and the steps done before the
error (composite verbs).  A CompileError is a generic CmdError, so the generic service prints it, filters it and logs it like
any other answer; exit codes stay the generic ones:

    2 usage        the command itself is wrong (unit, range, unknown verb or location); nothing was sent
    3 refused      the instrument refused (or a documented precondition is not met), with the reason in plain words
    4 unexpected   anything not foreseen (a runtime exception, a read-back that does not match)
    6 unsupported  this instrument has no such function (class verb not supported on this member)

Text form:

    error[refused/UNSTABLE]: the reading of ohaus_px224e is not stable (a draft-shield door is open).
    fix: close the doors (`chemlab run ohaus_px224e door close`), then retry; or add --wait 30s.
    instrument changed: no
    instrument said: weighing.tare -> rejected UNSTABLE (contract: 读数不稳定)
    request: r-0007
"""
from ..generic import CmdError

KIND_ALIASES = {'validation': 'usage', 'defined': 'refused', 'undefined': 'unexpected'}   # pilot error classes -> generic kinds


class CompileError(CmdError):
    def __init__(self, kind, code, message, fix=None, *, changed='no', done=None, backend=None, detail=None, see=None):
        kind = KIND_ALIASES.get(kind, kind)
        side = 'none (nothing was sent)' if kind == 'usage' else ('none' if changed == 'no' else 'see "instrument changed"')
        super().__init__(kind, code, message, fix=fix, side_effects=side, see=see)
        self.changed = changed            # 'no' | 'yes' | 'unknown' | free text
        self.done = list(done or [])      # steps completed before the error
        self.backend = backend            # {'capability', 'status', 'code', 'message', 'contract'} of the refused call
        self.detail = dict(detail or {})

    def to_json(self):
        out = super().to_json()
        out['instrument_changed'] = self.changed
        out['done'] = list(self.done)
        if self.backend:
            out['backend'] = self.backend
        if self.detail:
            out['detail'] = self.detail
        return out

    def to_text(self, request_id):
        lines = ['error[%s/%s]: %s' % (self.kind, self.code, self.message)]
        if self.fix:
            lines.append('fix: %s' % self.fix)
        changed = 'instrument changed: %s' % self.changed
        steps = [d for d in self.done if not d.startswith('move to ')]
        if steps:
            changed += ' - done before the error: ' + '; '.join(steps)
        elif self.done:
            changed += ' - it moved (%s)' % self.done[-1]
        lines.append(changed)
        b = self.backend or {}
        if b.get('capability'):
            said = '%s -> %s %s' % (b['capability'], b.get('status'), b.get('code') or '')
            if b.get('contract'):
                said += ' (contract: %s)' % b['contract']
            lines.append('instrument said: %s' % said.strip())
        if self.see:
            lines.append('see: %s' % self.see)
        lines.append('request: %s' % request_id)
        return '\n'.join(lines) + '\n'


def invalid(code, message, fix=None, **kw):
    return CompileError('usage', code, message, fix, **kw)


def refused(code, message, fix=None, **kw):
    return CompileError('refused', code, message, fix, **kw)


def unexpected(code, message, fix=None, **kw):
    kw.setdefault('changed', 'unknown')
    return CompileError('unexpected', code, message, fix, **kw)


def unsupported(code, message, fix=None, **kw):
    return CompileError('unsupported', code, message, fix, **kw)


def from_pilot(exc):
    """A pilot ChemlabError (validation / defined / undefined) -> CompileError with the same fields."""
    e = CompileError(exc.kind, exc.code, exc.message, exc.fix, changed=exc.changed, done=exc.done, backend=exc.backend,
                     detail=exc.detail)
    return e

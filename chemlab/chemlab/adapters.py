"""Hook for the class-level core (step 2, the "compile layer") between the generic commands and the backend.

Step 1 exposes each instrument's own contract capabilities through generic commands.  Step 2 adds verb tables per instrument
class (pipetting, temperature control, balance, ...), in chemlab/compile/.  They plug in here without changing the generic
commands:

* ``InstrumentAdapter.intercept(core, req)`` sees every instrument-scoped request first (describe, help, read, call, write,
  panel, screen, status, cancel, wait OP) and may answer it itself; returning None (the default) leaves it to the generic
  path.  ``req`` has ``command``, ``instrument``, ``args``, ``caller``, ``request_id`` and ``flags``.
* ``InstrumentAdapter.verbs()`` lists class-level verbs; they are run with ``chemlab run INST VERB ...`` (without an adapter
  it answers that the instrument has no class-level verbs yet) and are listed by ``describe``.
* ``InstrumentAdapter.dispatch_run(core, req, args)`` handles ``chemlab run INST ...`` (args = [VERB, ...]); the default
  checks the verb against verbs() and calls run().  Class adapters override it (help, unsupported verbs with reasons).
* Adapters come from the lab config: an entry may name one (``"adapter": "package.module:ClassName"``), and the per-class
  config fragments ``configs/adapters/<class>.json`` are merged at lab load (chemlab/compile/fragments.py; the config key
  ``"class_adapters": false`` switches them off).  An adapter reaches the instrument only through ``core.backend`` with the
  caller's agent principal, exactly like the generic commands, so the visibility rules and the runtime's own checks stay in
  force.
* Class-level jobs (job-N) answer the generic ``status`` / ``wait`` / ``cancel`` through ``route_job``.
"""
import importlib


class InstrumentAdapter:
    """Base class: does nothing (every request goes to the generic path)."""

    def __init__(self, instrument_id, config_entry):
        self.instrument_id = instrument_id
        self.config_entry = config_entry

    def verbs(self):
        """{verb: one-line summary} shown by describe; empty for the base class."""
        return {}

    def intercept(self, core, req):
        """Return a reply dict ({'text': ..., 'json': {...}}) to answer the request here, or None for the generic path."""
        return None

    def run(self, core, req, verb, args):
        """Handle ``chemlab run INST VERB ARGS``."""
        raise NotImplementedError

    def dispatch_run(self, core, req, args):
        """``chemlab run INST`` + args: the verb must be one of verbs(), then run()."""
        from .generic import usage
        inst = self.instrument_id
        if not args or args[0] not in self.verbs():
            raise usage('UNKNOWN_VERB', '%s has no class-level verb %s.' % (inst, repr(args[0]) if args else ''),
                        'use its own capabilities: `chemlab describe %s`, then `chemlab call %s CAP ...`.' % (inst, inst))
        return self.run(core, req, args[0], args[1:])


def load_adapters(config):
    """{instrument id: adapter}: entries that name one, then the per-class config fragments for the others."""
    out = {}
    for e in config.get('instruments', []):
        spec = e.get('adapter')
        if not spec:
            continue
        mod, _, cls = spec.partition(':')
        out[e['id']] = getattr(importlib.import_module(mod), cls)(e['id'], e)
    if config.get('class_adapters', True) is not False:
        from .compile.fragments import adapters_for
        for iid, ad in adapters_for(config).items():
            out.setdefault(iid, ad)
    return out


def route_job(core, req, cmd, args):
    """Generic status / wait / cancel of a class-level job (job-N); None when args do not name one."""
    if not args or not str(args[0]).startswith('job-'):
        return None
    from .compile.jobs import route
    return route(core, req, cmd, args)

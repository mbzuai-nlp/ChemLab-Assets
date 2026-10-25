"""Verb tables of the compile layer: a verb is a name, positionals and options (with quantity kinds for unit parsing), a
one-line summary, the class-level preconditions it checks, the errors it may give, and its handler.

    Verb('set-temp', 'set the temperature set point', h_set_temp,
         positionals=[Arg('TEMP', 'set point, e.g. 80C', kind='temperature')],
         options={'wait': Opt('then wait until the temperature is there', kind='flag')},
         preconditions=['power on', 'set point within the instrument range'], errors=['OUT_OF_RANGE (usage)', 'POWER_OFF (refused)'],
         reference='JULABO CORIO manual: setting the set point; IKA RCT manual: temperature setting')

Values with a ``kind`` are parsed into SI by units.parse before the handler runs; ``choices`` are checked.  Every verb also takes
--dry-run (check and plan, send nothing); --json and --request-id are handled by the generic service.
"""
from . import units
from .errors import invalid


class Arg:
    def __init__(self, name, help, required=True, kind=None, choices=None):
        self.name, self.help, self.required, self.kind, self.choices = name, help, required, kind, choices


class Opt:
    """kind: 'flag', 'value' (string), 'multi' (repeatable string) or a quantity kind ('temperature', 'duration', ...)."""

    def __init__(self, help, kind='value', metavar=None, choices=None, required=False, default=None):
        self.help, self.kind, self.choices, self.required, self.default = help, kind, choices, required, default
        self.metavar = metavar or ('VALUE' if kind in ('value', 'multi') else kind.upper().split('_')[0] if kind != 'flag' else None)

    @property
    def quantity(self):
        return self.kind not in ('flag', 'value', 'multi')


def save_to_opt():
    """The common --save-to FILE option: the full reply (JSON) goes to FILE in the work dir (records, spectra, results)."""
    return Opt('also save the full record (JSON) to FILE in your work dir', metavar='FILE')


TARGET_OPTS = {'zone': 'which zone (multi-zone devices, e.g. a heating block or reactor pad)',
               'channel': 'which channel (pumps, gas lines, measuring channels)',
               'cell': 'which measuring cell or chamber',
               'head': 'which measuring head or dosing head'}


def target_opt(kind, choices=None):
    """--zone / --channel / --cell / --head: one convention for devices with several identical units; the member mapping's
    "targets": {"zone": {"arg": NATIVE_ARG, "values": {"1": native, ...}}} lowers it (see COMPILE_GUIDE.md)."""
    return Opt(TARGET_OPTS[kind], metavar=kind.upper(), choices=choices)


class Verb:
    def __init__(self, name, summary, handler=None, *, positionals=(), options=None, group='work', readonly=False, destructive=False,
                 preconditions=(), errors=(), example=None, reference=None, details=None, long_action=False, variadic=None):
        self.name, self.summary, self.handler = name, summary, handler
        self.positionals = list(positionals)
        self.options = dict(options or {})
        self.group, self.readonly, self.destructive = group, readonly, destructive
        self.preconditions, self.errors = list(preconditions), list(errors)
        self.example, self.reference, self.details, self.long_action = example, reference, details, long_action
        self.variadic = variadic                      # (NAME, help) for any number of trailing arguments, e.g. KEY=VALUE pairs

    def kinds(self):
        return sorted({a.kind for a in self.positionals if a.kind} | {o.kind for o in self.options.values() if o.quantity})

    def usage(self, inst):
        parts = ['chemlab run %s %s' % (inst, self.name)]
        for a in self.positionals:
            parts.append(a.name if a.required else '[%s]' % a.name)
        if self.variadic:
            parts.append('%s ...' % self.variadic[0])
        for n, o in self.options.items():
            s = '--%s' % n if o.kind == 'flag' else '--%s %s' % (n, o.metavar)
            parts.append(s if o.required else '[%s]' % s)
        if self.destructive:
            parts.append('[--yes]')
        return ' '.join(parts)

    def help_text(self, inst, maps_to=None, notes=None):
        lines = ['usage: ' + self.usage(inst), '', self.summary]
        if self.details:
            lines += ['', self.details]
        if self.positionals:
            lines += ['', 'arguments:'] + ['  %-14s %s%s' % (a.name, a.help, (' (one of: %s)' % ', '.join(map(str, a.choices))) if a.choices else '')
                                           for a in self.positionals]
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
        kinds = self.kinds()
        if kinds:
            lines += ['', 'units: ' + units.describe_kinds(kinds)]
        if self.preconditions:
            lines += ['', 'checked before anything is sent:'] + ['  - ' + p for p in self.preconditions]
        if self.errors:
            lines += ['', 'errors:'] + ['  - ' + e for e in self.errors]
        if maps_to:
            lines += ['', 'on %s this uses: %s' % (inst, ', '.join(maps_to))]
        for n in notes or []:
            lines.append('note: %s' % n)
        if self.reference:
            lines += ['manual: ' + self.reference]
        if self.example:
            lines += ['', 'example:', '  ' + self.example.replace('INST', inst)]
        return '\n'.join(lines) + '\n'


class Parsed:
    """Parsed verb arguments: .pos (list), .opts (dict), .raw (dict of the typed text), .dry_run, .yes."""

    def __init__(self):
        self.pos, self.opts, self.raw, self.extra = [], {}, {}, []
        self.dry_run = self.yes = False

    def get(self, name, default=None):
        return self.opts.get(name, default)


def parse_args(verb, tokens, inst):
    p = Parsed()
    i = 0
    raw_pos = []
    while i < len(tokens):
        t = tokens[i]
        if t == '--dry-run':
            p.dry_run = True
        elif t == '--yes':
            p.yes = True
        elif t.startswith('--') and len(t) > 2:
            name, eq, val = t[2:].partition('=')
            o = verb.options.get(name)
            if o is None:
                known = ', '.join('--' + n for n in verb.options) or 'none'
                raise invalid('UNKNOWN_OPTION', '%s does not take --%s.' % (verb.name, name),
                              'options of %s: %s (see `chemlab help %s %s`).' % (verb.name, known, inst, verb.name))
            if o.kind == 'flag':
                if eq:
                    raise invalid('BAD_OPTION', '--%s takes no value.' % name, 'write just --%s.' % name)
                p.opts[name] = True
            else:
                if not eq:
                    i += 1
                    if i >= len(tokens):
                        raise invalid('MISSING_VALUE', '--%s needs a value.' % name, 'write --%s %s.' % (name, o.metavar))
                    val = tokens[i]
                if o.choices and val not in [str(c) for c in o.choices]:
                    raise invalid('BAD_CHOICE', '--%s %s is not allowed.' % (name, val), 'use one of: %s.' % ', '.join(map(str, o.choices)))
                if o.kind == 'multi':
                    p.opts.setdefault(name, []).append(val)
                elif name in p.opts:
                    raise invalid('REPEATED_OPTION', '--%s is given twice.' % name, 'give it once.')
                else:
                    p.raw[name] = val
                    p.opts[name] = units.parse(val, o.kind, what='--' + name) if o.quantity else val
        else:
            raw_pos.append(t)
        i += 1
    req = [a for a in verb.positionals if a.required]
    if len(raw_pos) < len(req):
        raise invalid('MISSING_ARGUMENT', '%s needs %s.' % (verb.name, req[len(raw_pos)].name), 'usage: %s' % verb.usage(inst))
    if len(raw_pos) > len(verb.positionals):
        if not verb.variadic:
            raise invalid('EXTRA_ARGUMENT', 'unexpected argument %r.' % raw_pos[len(verb.positionals)], 'usage: %s' % verb.usage(inst))
        p.extra = raw_pos[len(verb.positionals):]
        raw_pos = raw_pos[:len(verb.positionals)]
    for a, text in zip(verb.positionals, raw_pos):
        if a.choices and text.lower() not in [str(c).lower() for c in a.choices]:
            raise invalid('BAD_CHOICE', '%s takes %s, not %r.' % (verb.name, ' or '.join(map(str, a.choices)), text),
                          'usage: %s' % verb.usage(inst))
        p.raw[a.name] = text
        p.pos.append(units.parse(text, a.kind, what=a.name) if a.kind else (text.lower() if a.choices else text))
    for n, o in verb.options.items():
        if o.required and n not in p.opts:
            raise invalid('MISSING_OPTION', '%s needs --%s.' % (verb.name, n), 'usage: %s' % verb.usage(inst))
        if n not in p.opts and o.default is not None:
            p.opts[n] = units.parse(o.default, o.kind) if o.quantity else o.default
    return p

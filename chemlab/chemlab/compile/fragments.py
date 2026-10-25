"""Per-class config fragments: configs/adapters/<class>.json, merged at lab load (one file per class, so agents adding classes
in parallel never edit the same file; all203.json is not touched).

A fragment:

    {"class": "balance",
     "adapter": "chemlab.compile.classes.balance:BalanceAdapter",
     "about": "...", "membership": {...decisions, excluded instruments with reasons...},
     "members": {"<asset slug>": {"role": "primary"|"secondary", "verbs": {...}, "slots": {...}, "params": {...},
                                   "codes": {...}, "unsupported": {...}, "alternatives": {...}, "notes": {...}}}}

Members are matched by asset type (asset_type_id without "instrument."), so a lab may name its instruments freely (two balances
"bal1" / "bal2" of one type both get the adapter).  A config entry that names its own "adapter" keeps it.  An instrument in
several fragments gets a CompositeAdapter, in this order: class fragments before a generated shared / thin fragment
("layer": "shared"), primary role before secondary, then by class name.  A verb belongs to the first part that supports it, so
a class fragment overrides the shared verbs it defines and the shared layer still answers the verbs the class does not have.  The lab config can switch fragments off ("class_adapters": false), point to another directory
("class_adapters": "path/to/dir"), or keep only some classes ("class_adapters": ["balance"]).
"""
import importlib
import json
import os
import time
from pathlib import Path

from ..adapters import InstrumentAdapter

FRAGMENT_DIR = Path(__file__).resolve().parents[2] / 'configs' / 'adapters'


def write_json_atomic(path, obj):
    """Write a fragment (or any JSON file a lab may be loading right now) atomically: a temp file in the same directory, then
    rename, so a reader never sees a half-written file."""
    path = Path(path)
    tmp = path.with_name('.%s.%d.tmp' % (path.name, os.getpid()))
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1) + '\n', encoding='utf-8')
    os.replace(tmp, path)


def _read_json(f):
    """A fragment file; one retry after a short pause on a decode error (a generator still writing it non-atomically)."""
    try:
        return json.loads(f.read_text(encoding='utf-8'))
    except ValueError:
        time.sleep(0.5)
        return json.loads(f.read_text(encoding='utf-8'))


def load_fragments(directory=None, only=None):
    d = Path(directory) if directory else FRAGMENT_DIR
    out = []
    if not d.is_dir():
        return out
    for f in sorted(d.glob('*.json')):
        frag = _read_json(f)
        frag.setdefault('class', f.stem)
        frag['_path'] = str(f)
        if only is not None and frag['class'] not in only:
            continue
        out.append(frag)
    return out


def _cls(spec):
    mod, _, name = spec.partition(':')
    return getattr(importlib.import_module(mod), name)


LOAD_ERRORS = []        # (class, instrument, error) of adapters that could not be built; the lab loads without them


def adapters_for(config, fragments=None, strict=False):
    """{instrument id: adapter} from the fragments for the entries of a lab config (entries naming an adapter are skipped).
    An adapter that cannot be built (a class module with a bug) is left out and recorded in LOAD_ERRORS, so one broken class
    never breaks a lab; ``strict=True`` (the class tests) raises instead."""
    opt = config.get('class_adapters', True)
    if opt is False:
        return {}
    if fragments is None:
        fragments = load_fragments(opt if isinstance(opt, str) else None, opt if isinstance(opt, list) else None)
    out = {}
    for e in config.get('instruments', []):
        if e.get('adapter'):
            continue
        slug = (e.get('asset_type_id') or '').split('.', 1)[-1]
        found = []
        for frag in fragments:
            m = (frag.get('members') or {}).get(slug)
            if m is None:
                continue
            entry = dict(e)
            entry['class_map'] = m
            entry['class_fragment'] = frag['class']
            try:
                ad = _cls(m.get('adapter') or frag['adapter'])(e['id'], entry)
            except Exception as exc:                                        # noqa: BLE001
                if strict:
                    raise
                LOAD_ERRORS.append((frag['class'], e['id'], '%s: %s' % (type(exc).__name__, exc)))
                continue
            ad.layer = frag.get('layer', 'class')
            # precedence: class fragments before the generated shared/thin one ("layer": "shared"); primary before secondary
            found.append((1 if frag.get('layer') == 'shared' else 0, 0 if m.get('role', 'primary') == 'primary' else 1, frag['class'], ad))
        if len(found) == 1:
            out[e['id']] = found[0][-1]
        elif found:
            out[e['id']] = CompositeAdapter(e['id'], e, [x[-1] for x in sorted(found, key=lambda x: x[:3])])
    return out


class CompositeAdapter(InstrumentAdapter):
    """One instrument in several classes (e.g. a hot-plate stirrer: temperature + stirring)."""

    def __init__(self, instrument_id, config_entry, parts):
        super().__init__(instrument_id, config_entry)
        self.parts = list(parts)
        self.CLASS = '+'.join(p.CLASS for p in self.parts)
        for p in self.parts:
            p.composite = self                          # its describe / overview leave out the verbs another part answers

    def owner(self, verb):
        """The part that answers a verb.  Order: a class part that supports it (primary class first); then a class part that
        marks it unsupported or hides it (its reason beats the shared layer, unless the member lists the verb in
        "shared_fallthrough"); then the shared layer.  A class's "unsupported" never hides another class's supported verb."""
        classes = [p for p in self.parts if getattr(p, 'layer', 'class') != 'shared']
        shared = [p for p in self.parts if getattr(p, 'layer', 'class') == 'shared']
        for p in classes:
            if verb in p.verbs():
                return p
        for p in classes:
            if hasattr(p, 'blocks') and p.blocks(verb):
                return p
        for p in shared:
            if verb in p.verbs():
                return p
        for p in self.parts:
            if verb in p.table:
                return p
        return self.parts[0]

    def verbs(self):
        out = {}
        for p in self.parts:
            for k, v in p.verbs().items():
                if self.owner(k) is p:
                    out.setdefault(k, v)
        return out

    def _claimed(self, p):
        """Verbs this part answers on this instrument (so later parts do not list them again)."""
        names = set(getattr(p, 'table', {})) | set(p.verbs()) | set(getattr(p, 'hidden_verbs', lambda: {})())
        return {v for v in names if self.owner(v) is p}

    def dispatch_run(self, core, req, args):
        if not args or args[0] in ('--help', '-h'):
            texts, js, taken = [], [], set()
            for p in self.parts:
                try:
                    p.bind(core)
                except Exception:                                           # noqa: BLE001 - a broken part is left out of the overview
                    continue
                r = p.overview(core, req, taken)
                taken |= self._claimed(p)
                texts.append(r['text'])
                js.append(r['json'].get('class'))
            return {'text': ''.join(texts), 'json': {'instrument': self.instrument_id, 'classes': js}}
        return self.owner(args[0]).dispatch_run(core, req, args)

    def intercept(self, core, req):
        if req.command == 'help' and len(req.args) >= 2:
            return self.owner(req.args[1]).intercept(core, req)
        if req.command == 'describe' and len(req.args) == 1:
            r = self.parts[0].intercept(core, req)
            if r is None:
                return None
            taken = self._claimed(self.parts[0])
            for p in self.parts[1:]:
                try:
                    p.bind(core)
                except Exception:                                           # noqa: BLE001 - a broken part never breaks describe
                    continue
                lines, js = p.class_section(taken)
                taken |= self._claimed(p)
                a = r['text'].find('\nfull form:')
                r['text'] = r['text'][:a] + '\n' + '\n'.join(lines) + '\n' + r['text'][a:] if a >= 0 else r['text'] + '\n'.join(lines) + '\n'
                r['json'].setdefault('classes', [r['json'].get('class')]).append(js)
            return r
        return self.parts[0].intercept(core, req)

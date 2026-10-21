"""Front-panel layer for Chemistry-Sim instrument assets: the real screens, keys, knobs and menus from the manual, driving the asset's own
runtime capabilities (no second copy of instrument logic).

A declarative spec (panel_ui.json next to asset.contract.json, written from the manual; every screen, item and rule carries evidence) lists the
inputs, displays, screens, menus, editors and rules.  This engine holds the UI state and interprets the spec; it has no instrument code.  The
host runtime implements three methods (the panel-host protocol):
    host.panel_read()                      -> {state_id: value}   agent-visible slots, plus optional 'host.*' values (e.g. host.powered)
    host.panel_invoke(capability_id, args) -> dict                 the path a direct capability call takes; raises PanelRejected(code)
    host.panel_time()                      -> float                host simulation clock (s)
Every value the panel shows or sets goes through them.

Gestures (each returns True when the instrument reacted as its manual describes, False when it ignored or refused the gesture: power off,
locked, refused by the runtime - a refusal shows as the instrument's own indication from the spec 'errors' table, never as Python text):
    press(input_id, duration_s=0.1, held=())      keys; long press by the input's hold threshold; held = other keys held at the same time
    push(input_id, duration_s=0.1)                push-knobs (an encoder's push key) and keys
    turn(input_id, detents)                       encoders, knobs, screwdriver trimmers (signed detents)
    switch(input_id, position, held=())           switches (held: keys held while switching, e.g. power-on combinations)
    touch(display_id, x, y, duration_s=0.1, to=None)   touch screens (pixel coordinates of that display; to=(x, y) drags)
Navigation styles, selected per screen: static (bindings only), encoder_menu, arrow_menu, softkey_menu, parameter_cycle, touch_gui.

Output: frame() is the structured screen (per display: fields / icons / lines / widgets, their blink flags and what is shown at this instant,
plus the UI state for grading); render(display_id) is an RGB uint8 numpy array at the display's resolution: segment displays drawn as
segments (seven-segment letters included), character and graphic LCDs as 5x7 dot text, TFT / touch screens as flat widgets, LEDs as discs.
Blinking follows the host clock, so a frame is a pure function of the UI state and the clock (deterministic).

Spec reference: the docstrings of PanelUI and check_spec below; worked example: assets/expansion_batch05/t4_measure/ika_plate_rctdigital/panel_ui.json."""
import bisect
import copy
import hashlib
import json
import math
from pathlib import Path

SPEC_VERSION = '0.1'
STYLES = ('static', 'encoder_menu', 'arrow_menu', 'softkey_menu', 'parameter_cycle', 'touch_gui')
PRESS_KINDS = ('key', 'arrow_key', 'softkey')
TURN_KINDS = ('encoder', 'knob', 'trimmer')
INPUT_KINDS = PRESS_KINDS + TURN_KINDS + ('switch',)
DISPLAY_KINDS = ('segment_lcd', 'seven_segment', 'char_lcd', 'graphic_lcd', 'tft', 'touch_tft', 'led')
PAGE_KINDS = ('graphic_lcd', 'tft', 'touch_tft')
ITEM_TYPES = ('submenu', 'enum', 'number', 'bool', 'action', 'info', 'text')
MENU_STYLES = ('encoder_menu', 'arrow_menu', 'softkey_menu')


class PanelRejected(Exception):
    """Raised by host.panel_invoke when the instrument refuses a command; carries the runtime error code."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


class PanelError(ValueError):
    """A gesture the panel cannot take (unknown input or display, wrong gesture for the input): the host reports INVALID_ARGUMENT."""


def _aslist(x):
    return list(x) if isinstance(x, (list, tuple)) else [x]


def _key(v):
    """Map key of a value: JSON spelling for booleans / null, integers without '.0'."""
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if v is None:
        return 'null'
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# ---------------------------------------------------------------- glyphs
# 5x7 dot font (ASCII 0x20-0x7E, column-major, bit 0 = top row; the classic HD44780-style table) and seven-segment letters.
_FONT_HEX = ('0000000000 00005f0000 0007000700 147f147f14 242a7f2a12 2313086462 3649552250 0005030000 001c224100 0041221c00 '
             '082a1c2a08 08083e0808 0050300000 0808080808 0060600000 2010080402 3e5149453e 00427f4000 4261514946 2141454b31 '
             '1814127f10 2745454539 3c4a494930 0171090503 3649494936 064949291e 0036360000 0056360000 0008142241 1414141414 '
             '4122140800 0201510906 324979413e 7e1111117e 7f49494936 3e41414122 7f4141221c 7f49494941 7f09090101 3e41415132 '
             '7f0808087f 00417f4100 2040413f01 7f08142241 7f40404040 7f0204027f 7f0408107f 3e4141413e 7f09090906 3e4151215e '
             '7f09192946 4649494931 01017f0101 3f4040403f 1f2040201f 7f2018207f 6314081463 0304780403 6151494543 00007f4141 '
             '0204081020 41417f0000 0402010204 4040404040 0001020400 2054545478 7f48444438 3844444420 384444487f 3854545418 '
             '087e090102 081454543c 7f08040478 00447d4000 2040443d00 007f102844 00417f4000 7c04180478 7c08040478 3844444438 '
             '7c14141408 081414187c 7c08040408 4854545420 043f444020 3c4040207c 1c2040201c 3c4030403c 4428102844 0c5050503c '
             '4464544c44 0008364100 00007f0000 0041360800 08082a1c08')
FONT5X7 = {chr(0x20 + i): bytes.fromhex(h) for i, h in enumerate(_FONT_HEX.split())}
FONT5X7['°'] = bytes.fromhex('0006090906')                          # degree sign
FONT5X7['µ'] = bytes.fromhex('7c20201c20')                          # micro sign
FONT5X7['Ω'] = bytes.fromhex('4e7101714e')                          # ohm (Greek capital omega; the ohm sign U+2126 normalizes to it)
FONT5X7['Æ'] = bytes.fromhex('7e097f4941')                          # AE ligature (text editors of Metrohm instruments)
FONT5X7['κ'] = bytes.fromhex('007c102844')                          # kappa (conductivity) and rho (resistivity)
FONT5X7['ρ'] = bytes.fromhex('7c1212120c')
FONT5X7['→'] = bytes.fromhex('08082a1c08')                          # arrows of HD44780-type character displays (menu cursors)
FONT5X7['←'] = bytes.fromhex('081c2a0808')
FONT5X7['ψ'] = bytes.fromhex('07087f0807')                          # trident (the USB symbol of character displays)
FONT5X7['◆'] = bytes.fromhex('081c3e1c08')                          # rhombus (the screen field of the KNAUER K-7400S menu)
FONT5X7['🔒'] = bytes.fromhex('3c3f2d3f3c')                         # padlock (a locked screen)
FONT5X7['□'] = bytes.fromhex('3e2222223e')                          # square (ohms per square, sheet resistance of the M91 FastHall)
FONT5X7['²'] = bytes.fromhex('00090d0a00')                          # superscripts and the middle dot of units (cm²/(V·s), m⁻³)
FONT5X7['³'] = bytes.fromhex('0011151f00')
FONT5X7['⁻'] = bytes.fromhex('0002020200')
FONT5X7['·'] = bytes.fromhex('0000080000')
SEG7 = {'0': 'abcdef', '1': 'bc', '2': 'abdeg', '3': 'abcdg', '4': 'bcfg', '5': 'acdfg', '6': 'acdefg', '7': 'abc', '8': 'abcdefg', '9': 'abcdfg',
        'A': 'abcefg', 'a': 'abcdeg', 'B': 'abcdefg', 'b': 'cdefg', 'C': 'adef', 'c': 'deg', 'D': 'bcdeg', 'd': 'bcdeg', 'E': 'adefg', 'e': 'abdefg',
        'F': 'aefg', 'f': 'aefg', 'G': 'acdef', 'g': 'abcdfg', 'H': 'bcefg', 'h': 'cefg', 'I': 'bc', 'i': 'c', 'J': 'bcde', 'j': 'bcd', 'K': 'bcefg',
        'L': 'def', 'l': 'ef', 'N': 'abcef', 'n': 'ceg', 'O': 'abcdef', 'o': 'cdeg', 'P': 'abefg', 'p': 'abefg', 'Q': 'abcfg', 'q': 'abcfg',
        'R': 'eg', 'r': 'eg', 'S': 'acdfg', 's': 'acdfg', 'T': 'defg', 't': 'defg', 'U': 'bcdef', 'u': 'cde', 'V': 'bcdef', 'v': 'cde',
        'X': 'bcefg', 'x': 'bcefg', 'Y': 'bcdfg', 'y': 'bcdfg', 'Z': 'abdeg', 'z': 'abdeg', '-': 'g', '_': 'd', '=': 'dg', ' ': '',
        '°': 'abfg', "'": 'f', '"': 'bf', '?': 'abeg', '[': 'adef', ']': 'abcd'}


def seven_segment_cells(chars, n_cells, colons=(), align='right'):
    """Lay text out on a row of seven-segment cells: chars is [(char, blink)]; '.' lights the decimal point of the cell before it, ':' the colon
    at that cell boundary (only at the field's printed colons).  Returns ([dict(segments, dp, blink, dp_blink)] * n_cells, {boundary: blink})."""
    cells, col = [], {}
    for ch, bl in chars:
        if ch == '.':
            if not cells:
                cells.append(dict(segments='', dp=False, blink=bl, dp_blink=bl))
            cells[-1]['dp'], cells[-1]['dp_blink'] = True, bl
        elif ch == ':':
            col[len(cells)] = bl
        else:
            cells.append(dict(segments=SEG7.get(ch, SEG7.get(ch.upper(), SEG7.get(ch.lower(), ''))), dp=False, blink=bl, dp_blink=bl))
    shift = n_cells - len(cells) if align == 'right' else 0
    if shift < 0:                                                         # too long: the leftmost characters fall off the field
        cells, shift = cells[-shift:], shift
    blank = dict(segments='', dp=False, blink=False, dp_blink=False)
    out = ([dict(blank) for _ in range(shift)] + cells + [dict(blank) for _ in range(n_cells)])[:n_cells]
    return out, {b + shift: v for b, v in col.items() if (b + shift) in set(colons)}


# ---------------------------------------------------------------- spec checks
def _walk_actions(actions, where, out):
    for a in actions or []:
        out.append((where, a))
        if a.get('do') == 'if':
            _walk_actions(a.get('then'), where + '.then', out)
            _walk_actions(a.get('else'), where + '.else', out)
        for code, acts in (a.get('on_reject') or {}).items():
            _walk_actions(acts, f'{where}.on_reject.{code}', out)
        _walk_actions(a.get('indication'), where + '.indication', out)


def _items_of(items, where, out):
    for i, it in enumerate(items or []):
        out.append((f'{where}[{i}]', it))
        _items_of(it.get('items'), f'{where}[{i}].items', out)
        _items_of(it.get('fields'), f'{where}[{i}].fields', out)


def all_actions(spec):
    """Every action list in the spec, as [(location, action)]."""
    out = []
    for i, g in enumerate(spec.get('gates', [])):
        _walk_actions(g.get('do'), f'gates[{i}]', out)
    for i, b in enumerate(spec.get('bindings', [])):
        _walk_actions(b.get('do'), f'bindings[{i}]', out)
    for sid, s in spec.get('screens', {}).items():
        for i, b in enumerate(s.get('bindings', [])):
            _walk_actions(b.get('do'), f'screens.{sid}.bindings[{i}]', out)
        for k in ('on_enter', 'on_timeout'):
            _walk_actions(s.get(k), f'screens.{sid}.{k}', out)
        nav = s.get('nav') or {}
        for i, sk in enumerate(nav.get('softkeys', [])):
            _walk_actions(sk.get('do'), f'screens.{sid}.nav.softkeys[{i}]', out)
        for i, fb in enumerate(nav.get('function_bar', [])):                 # text dialogs: function bar on the bottom line
            _walk_actions(fb.get('do'), f'screens.{sid}.nav.function_bar[{i}]', out)
        for pid, page in (nav.get('pages') or {}).items():
            for j, w in enumerate(_page_defs(page)):
                _walk_actions(w.get('do'), f'screens.{sid}.nav.pages.{pid}.widgets[{j}]', out)
                _walk_actions(w.get('do_long'), f'screens.{sid}.nav.pages.{pid}.widgets[{j}].do_long', out)
                _walk_actions((w.get('row') or {}).get('do'), f'screens.{sid}.nav.pages.{pid}.widgets[{j}].row', out)
                _walk_actions((w.get('guard') or {}).get('do'), f'screens.{sid}.nav.pages.{pid}.widgets[{j}].guard', out)
                for k, row in enumerate(w.get('rows', [])):
                    _walk_actions(row.get('do'), f'screens.{sid}.nav.pages.{pid}.widgets[{j}].rows[{k}]', out)
            for sw, acts in (page.get('swipes') or {}).items():
                _walk_actions(acts, f'screens.{sid}.nav.pages.{pid}.swipes.{sw}', out)
    for eid, e in spec.get('editors', {}).items():
        for k in ('then', 'cancel'):
            _walk_actions(e.get(k), f'editors.{eid}.{k}', out)
    for mid, m in spec.get('menus', {}).items():
        items = []
        _items_of(m.get('items'), f'menus.{mid}.items', items)
        for where, it in items:
            _walk_actions(it.get('on_finish'), where + '.on_finish', out)
            _walk_actions(it.get('enter'), where + '.enter', out)
            for key, acts in (it.get('keys') or {}).items():                  # per-line keys (e.g. a GC's On/Yes, Off/No on the active line)
                _walk_actions(acts, f'{where}.keys.{key}', out)
    for code, e in spec.get('errors', {}).get('runtime', {}).items():
        _walk_actions(e.get('indication'), f'errors.runtime.{code}', out)
    for fid, f in spec.get('forms', {}).items():                            # touch forms and dialogs
        for k in ('on_open', 'on_save', 'on_cancel', 'invalid'):
            _walk_actions(f.get(k), f'forms.{fid}.{k}', out)
        for i, fld in enumerate(f.get('fields', [])):
            for k in ('do', 'on_change'):
                _walk_actions(fld.get(k), f'forms.{fid}.fields[{i}].{k}', out)
            _walk_actions((fld.get('guard') or {}).get('do'), f'forms.{fid}.fields[{i}].guard', out)
    for did, d in spec.get('dialogs', {}).items():
        for i, b in enumerate(d.get('buttons', [])):
            _walk_actions(b.get('do'), f'dialogs.{did}.buttons[{i}]', out)
    return out


def _writes(w):
    if not w:
        return []
    if 'calls' in w:
        return [x for c in w['calls'] for x in _writes(c)]
    return [w]


def all_writes(spec):
    """Every capability write the spec can make (adjusters, menu items and fields, editors, parameter cycles, widgets)."""
    out = [w for v in spec.get('values', {}).values() for w in _writes(v.get('write'))]
    for m in spec.get('menus', {}).values():
        items = []
        _items_of(m.get('items'), '', items)
        out += [w for _, it in items for w in _writes(it.get('write'))]
    for e in spec.get('editors', {}).values():
        out += [w for f in e.get('fields', []) for w in _writes(f.get('write'))] + _writes(e.get('write'))
    for s in spec.get('screens', {}).values():
        nav = s.get('nav') or {}
        out += [w for f in nav.get('params', []) for w in _writes(f.get('write'))]
        for page in (nav.get('pages') or {}).values():
            out += [w for wd in _page_defs(page) for w in _writes(wd.get('write'))]
    for f in spec.get('forms', {}).values():
        out += _writes(f.get('write')) + [w for fld in f.get('fields', []) for w in _writes(fld.get('write'))]
    return out


def spec_capabilities(spec):
    """Runtime capabilities the panel can call (bindings, gates, writes): the effects of a panel gesture are a subset of theirs."""
    caps = {a['capability'] for _, a in all_actions(spec) if a.get('do') == 'invoke'}
    return sorted(caps | {w['capability'] for w in all_writes(spec)})


def check_spec(spec, capabilities=None):
    """Static checks of a spec: version, kinds, styles, and every reference (inputs, displays, screens, menus, editors, values, capabilities).
    Returns a list of error strings (empty: valid).  capabilities: the runtime capability ids a write / invoke may name."""
    err = []
    if spec.get('spec_version') != SPEC_VERSION:
        err.append(f'spec_version must be {SPEC_VERSION}')
    ins, dis, scr = spec.get('inputs', {}), spec.get('displays', {}), spec.get('screens', {})
    for k, v in ins.items():
        if v.get('kind') not in INPUT_KINDS:
            err.append(f'inputs.{k}.kind {v.get("kind")!r}')
        if v.get('push_input') and ins.get(v['push_input'], {}).get('kind') not in PRESS_KINDS:
            err.append(f'inputs.{k}.push_input must name a key')
        if v.get('kind') == 'switch' and not v.get('positions'):
            err.append(f'inputs.{k}.positions missing')
    for k, v in dis.items():
        if v.get('kind') not in DISPLAY_KINDS:
            err.append(f'displays.{k}.kind {v.get("kind")!r}')
        if v.get('kind') != 'led' and not (v.get('size') or v.get('kind') == 'char_lcd'):
            err.append(f'displays.{k}.size missing')
    for k, s in scr.items():
        if s.get('style', 'static') not in STYLES:
            err.append(f'screens.{k}.style {s.get("style")!r}')
        if s.get('extends') and s['extends'] not in scr:
            err.append(f'screens.{k}.extends {s["extends"]!r}')
        for d in (s.get('layout') or {}):
            if d not in dis:
                err.append(f'screens.{k}.layout display {d!r}')
        nav = s.get('nav') or {}
        if s.get('style') in MENU_STYLES and nav.get('menu') not in spec.get('menus', {}):
            err.append(f'screens.{k}.nav.menu {nav.get("menu")!r}')
        for key in ('turn', 'push', 'exit', 'back', 'up', 'down', 'left', 'right', 'enter', 'select', 'inc', 'dec', 'knob', 'confirm', 'home'):
            for x in _aslist(nav.get(key) or []):
                if x not in ins:
                    err.append(f'screens.{k}.nav.{key} input {x!r}')
        for x in [s.get('editor')] if s.get('editor') else []:
            if x not in spec.get('editors', {}):
                err.append(f'screens.{k}.editor {x!r}')
        for b in s.get('bindings', []):
            for x in _aslist(b.get('on', {}).get('input') or []):
                if x not in ins:
                    err.append(f'screens.{k}.bindings input {x!r}')
    for b in spec.get('bindings', []) + [g for gate in spec.get('gates', []) for g in gate.get('except', [])]:
        for x in _aslist((b.get('on', b)).get('input') or []):
            if x not in ins:
                err.append(f'bindings/gates input {x!r}')
    p = spec.get('power') or {}
    for x in [p.get('off_screen')] + [o.get('screen') for o in p.get('on_screen', [])] + [spec.get('start_screen')] + \
            [r.get('to') for sc in scr.values() for r in sc.get('leave', [])]:
        if x is not None and x not in scr:
            err.append(f'power/start screen {x!r}')
    for where, a in all_actions(spec):
        d = a.get('do')
        if d == 'goto' and a.get('screen') not in scr:
            err.append(f'{where}: goto {a.get("screen")!r}')
        if d in ('adjust', 'confirm') and a.get('value') not in spec.get('values', {}):
            err.append(f'{where}: value {a.get("value")!r}')
        if d == 'edit' and a.get('editor') not in spec.get('editors', {}):
            err.append(f'{where}: editor {a.get("editor")!r}')
        if d == 'invoke' and capabilities is not None and a.get('capability') not in capabilities:
            err.append(f'{where}: capability {a.get("capability")!r}')
        if d in ('form_open', 'form_save', 'form_cancel', 'form_set', 'form_edit') and (a.get('form') is not None or d == 'form_open') \
                and a.get('form') not in spec.get('forms', {}):
            err.append(f'{where}: form {a.get("form")!r}')
        if d == 'form_edit' and a.get('form') in spec.get('forms', {}) and a.get('field') not in [f['id'] for f in spec['forms'][a['form']]['fields']]:
            err.append(f'{where}: field {a.get("field")!r}')
        if d == 'dialog' and a.get('dialog') not in spec.get('dialogs', {}):
            err.append(f'{where}: dialog {a.get("dialog")!r}')
    err += _check_touch(spec)
    if capabilities is not None:
        for w in all_writes(spec):
            if w.get('capability') not in capabilities:
                err.append(f'write capability {w.get("capability")!r}')
    for mid, m in spec.get('menus', {}).items():
        items = []
        _items_of(m.get('items'), f'menus.{mid}.items', items)
        for where, it in items:
            if it.get('type') not in ITEM_TYPES:
                err.append(f'{where}.type {it.get("type")!r}')
            if not it.get('evidence') and not it.get('assumption') and '.fields' not in where:
                err.append(f'{where}: evidence missing')
            for key in (it.get('keys') or {}):
                if key not in ins:
                    err.append(f'{where}.keys input {key!r}')
    return err


def load_spec(path, capabilities=None):
    spec = json.loads(Path(path).read_text())
    errors = check_spec(spec, capabilities)
    if errors:
        raise PanelError('panel spec: ' + '; '.join(errors[:12]))
    return spec


def check_arguments(schema, value, path='$'):
    """Errors of panel.* arguments against the schemas capability_definitions writes (object / string / number / integer / boolean / array
    with enum, bounds, items and sizes): for hosts whose own validator does not cover these keywords.  Empty list: valid."""
    t = schema.get('type')
    ok = {'object': lambda v: isinstance(v, dict), 'string': lambda v: isinstance(v, str), 'boolean': lambda v: isinstance(v, bool),
          'array': lambda v: isinstance(v, list), 'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),
          'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
    if t in ok and not ok[t](value):
        return [f'{path}: expected {t}']
    if 'enum' in schema and value not in schema['enum']:
        return [f'{path}: not one of {schema["enum"]}']
    if t in ('number', 'integer') and (value < schema.get('minimum', -math.inf) or value > schema.get('maximum', math.inf)):
        return [f'{path}: out of range']
    err = []
    if t == 'object':
        props = schema.get('properties', {})
        err += [f'{path}.{k}: missing' for k in schema.get('required', []) if k not in value]
        if schema.get('additionalProperties') is False:
            err += [f'{path}.{k}: unknown' for k in value if k not in props]
        for k, v in value.items():
            if k in props:
                err += check_arguments(props[k], v, f'{path}.{k}')
    if t == 'array':
        if not schema.get('minItems', 0) <= len(value) <= schema.get('maxItems', math.inf):
            err.append(f'{path}: size')
        for i, v in enumerate(value):
            err += check_arguments(schema.get('items', {}), v, f'{path}[{i}]')
    return err


# ---------------------------------------------------------------- contract capabilities
def _number(unit, lo=None, hi=None, integer=False):
    s = {'type': 'integer' if integer else 'number', 'x-unit': unit}
    if lo is not None:
        s['minimum'] = lo
    if hi is not None:
        s['maximum'] = hi
    return s


def _object(props, optional=()):
    return dict(type='object', properties=props, required=[k for k in props if k not in optional], additionalProperties=False)


def _uses_held(spec):
    pats = [b.get('on', {}) for s in spec.get('screens', {}).values() for b in s.get('bindings', [])] + [b.get('on', {}) for b in spec.get('bindings', [])]
    return any(p.get('held') for p in pats)


def capability_definitions(spec):
    """Contract-ready panel capabilities for the inputs and displays this spec declares (panel.turn / panel.switch / panel.touch only when the
    device has such inputs): {capability_id: dict(input_schema, output_schema, example_arguments, component_ids, function_zh, kind, visibility)}."""
    ins, dis = spec['inputs'], spec['displays']
    comp = lambda d, k: d[k].get('component_id', k)                         # noqa: E731
    keys = [k for k, v in ins.items() if v['kind'] in PRESS_KINDS]
    knobs = [k for k, v in ins.items() if v['kind'] == 'encoder' and v.get('push_input')]
    turns = [k for k, v in ins.items() if v['kind'] in TURN_KINDS]
    switches = [k for k, v in ins.items() if v['kind'] == 'switch']
    touch = [k for k, v in dis.items() if v['kind'] == 'touch_tft']
    held = _uses_held(spec)
    accepted = _object(dict(accepted={'type': 'boolean'}))
    out = {}
    if keys:
        props = dict(input_id={'type': 'string', 'enum': keys + knobs},
                     duration_s=_number('s', 0.02, 60.0))
        if held:
            props['held'] = dict(type='array', items={'type': 'string', 'enum': keys}, minItems=0, maxItems=4)
        out['panel.press'] = dict(input_schema=_object(props, optional=('duration_s', 'held')), output_schema=accepted,
                                  example_arguments=dict(input_id=keys[0]), component_ids=sorted({comp(ins, k) for k in keys}), kind='device_command',
                                  visibility='agent',
                                  function_zh='按前面板按键（按键或旋钮按压；duration_s≥长按阈值为长按' + ('；held为同时按住的键' if held else '') +
                                              '）：按手册的界面规则执行，结果只返回是否被仪器接受，界面变化须看屏幕')
    if turns:
        out['panel.turn'] = dict(input_schema=_object(dict(input_id={'type': 'string', 'enum': turns}, detents=_number('1', -1000, 1000, integer=True))),
                                 output_schema=accepted, example_arguments=dict(input_id=turns[0], detents=1), component_ids=sorted({comp(ins, k) for k in turns}),
                                 kind='device_command', visibility='agent',
                                 function_zh='转动前面板旋钮或调节螺钉（detents为带符号格数，正为顺时针）：按手册的界面规则执行，只返回是否被接受')
    if switches:
        pos = sorted({p for k in switches for p in ins[k]['positions']})
        props = dict(input_id={'type': 'string', 'enum': switches}, position={'type': 'string', 'enum': pos})
        if held:
            props['held'] = dict(type='array', items={'type': 'string', 'enum': keys}, minItems=0, maxItems=4)
        out['panel.switch'] = dict(input_schema=_object(props, optional=('held',)), output_schema=accepted,
                                   example_arguments=dict(input_id=switches[0], position=ins[switches[0]]['positions'][-1]),
                                   component_ids=sorted({comp(ins, k) for k in switches}), kind='device_command', visibility='agent',
                                   function_zh='拨动前面板开关到指定位置：按手册的界面规则执行，只返回是否被接受')
    if touch:
        w, h = max(dis[k]['size'][0] for k in touch), max(dis[k]['size'][1] for k in touch)
        if any(dis[k].get('rotation') is not None for k in touch):            # a display turned by 90 / 270 deg: x and y take either length
            w = h = max(w, h)
        props = dict(display_id={'type': 'string', 'enum': touch}, x=_number('1', 0, w - 1, integer=True), y=_number('1', 0, h - 1, integer=True),
                     duration_s=_number('s', 0.02, 60.0), to_x=_number('1', 0, w - 1, integer=True), to_y=_number('1', 0, h - 1, integer=True))
        out['panel.touch'] = dict(input_schema=_object(props, optional=('duration_s', 'to_x', 'to_y')), output_schema=accepted,
                                  example_arguments=dict(display_id=touch[0], x=w // 2, y=h // 2), component_ids=sorted({comp(dis, k) for k in touch}),
                                  kind='device_command', visibility='agent', function_zh='点按或拖动触摸屏（显示屏像素坐标；to_x/to_y为拖动终点）：只返回是否被接受')
    shown = [k for k in dis]
    image = _object(dict(path={'type': 'string'}, format={'type': 'string', 'enum': ['png']}, width=_number('1', 1, 10000, integer=True),
                         height=_number('1', 1, 10000, integer=True), sha256={'type': 'string', 'pattern': '^[a-f0-9]{64}$'}))
    out['panel.capture'] = dict(input_schema=_object(dict(display_id={'type': 'string', 'enum': shown})),
                                output_schema=_object(dict(display_id={'type': 'string'}, time_s=_number('s'), image=image)),
                                example_arguments=dict(display_id=shown[0]), component_ids=sorted({comp(dis, k) for k in shown}), kind='query', visibility='agent',
                                function_zh='屏幕特写相机：把显示屏当前画面渲染为PNG文件（返回文件路径、尺寸与SHA-256；闪烁按仿真时钟的当前相位）')
    out['panel.frame'] = dict(input_schema=_object({}), output_schema=_object(dict(frame={'type': 'object'})), example_arguments={},
                              component_ids=sorted({comp(dis, k) for k in shown}), kind='query', visibility='evaluator',
                              function_zh='评测者：结构化画面与界面状态（当前屏幕、菜单位置、编辑缓冲、待确认值、闪烁、各显示区文字与图标）')
    return out


# ---------------------------------------------------------------- engine
class PanelUI:
    """UI state machine of one instrument instance, driven by a panel spec and a panel host (see the module docstring).

    Spec sections (all optional except inputs / displays / screens):
      timing     {long_press_s, blink_period_s}
      inputs     {id: {kind, label, long_press_s, push_input (encoders), positions (switches), component_id, evidence}}
      displays   {id: {kind, size, component_id, colors, fields / icons (segment), rows / cols (char_lcd), evidence}}
                                                       (a segment field with font '5x7' is a dot-matrix text line: cells, scale, align)
      conversions{id: {when, scale, offset}}         display unit conversions (e.g. degC -> degF when the unit menu says F)
      vars       {name: {initial, persist}}            UI variables (e.g. a transient display deadline)
      values     {id: adjuster}                        values set outside menus by a knob or keys: read, min, max, step, extra,
                                                       min_from / max_from (dynamic limits), write, confirm_when + confirm_timeout_s
      menus      {id: {label, wrap, items: [item]}}    item: id, label, type (submenu | enum | number | bool | action | info | text),
                                                       options / min / max / step / extra, read, write, fields (multi-field items),
                                                       visible_if, display (per display layout), unsupported, on_finish, evidence,
                                                       keys {input: actions} (keys of the active line, e.g. On/Yes, Off/No), actual /
                                                       value_text (columns of a display with param_list: see _param_lines)
      editors    {id: {fields, write, then, cancel}}   stand-alone multi-field entries outside menus (e.g. a timer hh / mm / ss)
      screens    {id: {style, extends, nav, layout, bindings, editor, on_enter, timeout_s, on_timeout, leave_if, leave_to, evidence}}
      power      {powered, epoch, off_screen, on_screen: [{when, screen}]}
      gates      [{when, except: [pattern], do}]       checked before every gesture (power off, lock ...): the gesture is refused
      bindings   [{on: pattern, when, do}]             global bindings, after the screen's own bindings and its style
      errors     {runtime: {code: {indication, text_zh}}, device: [...]}
    Conditions: {all|any|not}, {state|var|field|value|current|event: ..., path, eq|ne|in|gt|ge|lt|le|gt_now}, {editing}, {pending}, {screen},
    {blinking}, {alternate: {period_s, count, index}}.
    Sources: {const|state|var|field|field_text|value|event|now|sum|cases|current|message|hook} with map, scale, offset, mod, convert,
    round (to an integer: floor | ceil | nearest; or to n decimals), now_plus (applied in that order).
    Actions: invoke, goto (menu, path), adjust, confirm, nav (move | enter | back | exit | change | confirm | cancel | next_field | prev_field |
    next_param | type | backspace | clear | digit_left | digit_right), edit, set_var, blink, beep, message, if, ignore, reject, unsupported, page,
    page_back, hook.
    Entry modes of number / text fields: arrows or knob (step / option; 'stride' grid steps per press), typed on a keypad (nav.keypad; range-checked on confirm, errors.entry
    indication when refused), digit-wise (entry 'digits', digit_confirm 'advance' for SET-per-digit; 'signed': a leading +/- that is the first
    cursor position), character-wise (text with a charset).
    Softkey lists (e.g. six keys around a graphic LCD): display softkey_rows / menu_title / menu_values / rotate_if, item label bindings, 'value',
    'mark', edit_style 'list', edit_layout (the item's entry screen); sources {'item': key}, {'current': 'type' | 'digit'}; nav 'set', digit wrap;
    'scale' / 'offset' may be sources or (scale) a list of factors, e.g. a unit factor that follows the unit setting; source {'menu_path'} and a
    goto 'path' given as a source return to a remembered list line; a softkey with label_only names a key in the label row without taking it
    (e.g. OK on a character grid, handled by the arrow-menu rules through nav.enter).  Display option menu_titles 'own': a list shows the
    title of its submenu item ('title', else its label) instead of the breadcrumb, and a value editor the item's 'title' (e.g. 'Set positions:').
    Touch widgets: label, button (do / do_long), toggle, choice, number, text (keypads), slider, list (rows, drag to scroll, or arrow buttons with
    {'do': 'scroll', 'widget', 'rows'}); page swipes.
    Touch toolkit (touch_gui; worked example: assets/basic40_batch02/t4_measure/mt_xpr204e/panel_ui.json):
      page layout conventions   pages with title / header / sidebar / content ({'row': [...]}, 'h' px or 'flex') / actions get their rects from
                                touch_layout (header_h, action_h, margin, gap, row_h, sidebar_w); hidden widgets leave no gap
      forms                     forms {id: {fields, write, on_save, on_cancel, invalid}}: field types enum / bool / number / text / password / date /
                                time / multi / info / link / action, options or options_src (runtime lists), visible_if / enabled_if / required /
                                readonly / unsupported; a 'form' widget lists the rows; form_open (set) / form_save / form_cancel / form_set /
                                form_edit (one field's editor opened from a button); screens' timeout_s and the page action may be sources; a
                                form that is not open shows the live values and writes each field at once
      editors (overlays)        keyboards (spec 'keyboards'; built-in 'alpha' QWERTY with Shift / 12@ / Del / Space / Cancel / OK and 'numeric';
                                keyboard_select picks a layout from a setting), choice lists, date / time pickers (+ / -), check lists; the shown
                                text is selected on opening (the first key replaces it); a multi field with 'ordered' keeps the selection order
                                (position numbers, e.g. function keys), 'std' adds [STD] (factory selection), 'max_items' caps the selection
      dialogs                   dialogs {id: {title, lines, form + fields, buttons [{label, do, style, keep}], buttons_layout 'column'}} opened by
                                'dialog'; overlays stack (a keyboard over a dialog); Back closes the top overlay, nav.home returns to nav.start
      widgets                   lamp, gauge (fraction + marks), xy (bubble target), dynamic list (items / row cells / selected / enabled), form,
                                plot (trend chart: series of [x, y] on a left / right axis, x window, fixed or automatic y range), image (a
                                bitmap of grey / rgb rows drawn by the device; its 'do' gets the touch in image fractions u, v, u2, v2); any widget or
                                form field may have a 'guard' {when, do}: while it holds a tap runs do instead (e.g. a remote-control lock warning);
                                label / button options style / scale / align / fill / color / wrap / selected; pass_through (a passive
                                message drawn over other widgets that does not take the touch, e.g. an event popup); a label, lamp, gauge or
                                xy with a 'do' is a touch zone (e.g. the weight value or the SmartTrac bar of a balance)
      sources and conditions    {'row': path}, {'row_index'}, {'form_values': form}, {'object': {...}}, {'list': [...]}, {'len': source}; 'key'
                                (+ key_path / key_field) lookups in entity lists, list paths (keys with dots), 'at', 'slice'; conditions {'page'},
                                {'form_open'}, {'overlay'}, 'same' / 'in' / 'gt'... against {'src': source}; screens 'leave' [{when, to}]
      displays                  'brightness' source (percent), 'dark_if' condition with a 'colors_dark' palette (dark mode setting; frame
                                'dark'), LED 'color' from the layout, i18n tables (static texts and option labels),
                                5x7 glyph fallback to the base letter; a text part with {'strike': condition} is drawn crossed out on graphic
                                displays (frame: strike_spans), e.g. a changed and unsaved method name"""

    def __init__(self, spec, host, hooks=None):
        self.spec = spec
        self.host = host
        self.hooks = dict(hooks or {})
        self._cache = None
        self._accepted = True
        self.ui = self._fresh(None)

    def __deepcopy__(self, memo):
        """Copy the UI state only: the spec is shared and the host stays the same object unless the host itself is being copied."""
        new = object.__new__(type(self))
        memo[id(self)] = new
        new.spec, new.hooks = self.spec, self.hooks
        new.host = memo.get(id(self.host), self.host)
        new.ui = copy.deepcopy(self.ui, memo)
        new._cache, new._accepted = None, True
        return new

    def _fresh(self, screen, keep_vars=None):
        vars_ = {k: (keep_vars[k] if keep_vars and v.get('persist') and k in keep_vars else copy.deepcopy(v.get('initial')))
                 for k, v in self.spec.get('vars', {}).items()}
        return dict(screen=screen, menu=None, powered=None, epoch=None, path=[], editor=None, pending={}, blinks={}, vars=vars_, entered_t=None,
                    last_input_t=None, page=None, history=[], overlay=None, scroll={}, focus=None, message=None, beeps=0, last=None, unsupported=None,
                    overlays=[], forms={}, form_order=[])

    # ---- host access
    def _now(self):
        return float(self.host.panel_time())

    def _state(self):
        if self._cache is None:
            self._cache = dict(self.host.panel_read())
        return self._cache

    def _screen(self, name=None):
        return self.spec['screens'][name or self.ui['screen']]

    def _input(self, input_id):
        try:
            return self.spec['inputs'][input_id]
        except KeyError:
            raise PanelError(f'unknown input {input_id!r}') from None

    def _period(self):
        return float(self.spec.get('timing', {}).get('blink_period_s', 1.0))

    # ---- gestures
    def press(self, input_id, duration_s=0.1, held=()):
        inp = self._input(input_id)
        if inp['kind'] == 'encoder' and inp.get('push_input'):
            input_id, inp = inp['push_input'], self._input(inp['push_input'])
        if inp['kind'] not in PRESS_KINDS:
            raise PanelError(f'{input_id!r} is not pressed')
        if not (isinstance(duration_s, (int, float)) and not isinstance(duration_s, bool) and duration_s > 0):
            raise PanelError('duration_s')
        held = self._held(held, input_id)
        thr = inp.get('long_press_s', self.spec.get('timing', {}).get('long_press_s'))
        long_ = thr is not None and duration_s >= thr - 1e-9
        return self._gesture(dict(event='press', input=input_id, long=long_, held=held, duration_s=float(duration_s)))

    def push(self, input_id, duration_s=0.1):
        return self.press(input_id, duration_s)

    def turn(self, input_id, detents):
        inp = self._input(input_id)
        if inp['kind'] not in TURN_KINDS:
            raise PanelError(f'{input_id!r} is not turned')
        if isinstance(detents, bool) or not isinstance(detents, int):
            raise PanelError('detents must be an integer')
        if detents == 0:
            return False
        return self._gesture(dict(event='turn', input=input_id, detents=detents))

    def switch(self, input_id, position, held=()):
        inp = self._input(input_id)
        if inp['kind'] != 'switch' or position not in inp['positions']:
            raise PanelError(f'{input_id!r} has no position {position!r}')
        return self._gesture(dict(event='switch', input=input_id, position=position, held=self._held(held, input_id)))

    def touch(self, display_id, x, y, duration_s=0.1, to=None):
        d = self.spec['displays'].get(display_id)
        if d is None or d['kind'] != 'touch_tft':
            raise PanelError(f'{display_id!r} is not a touch screen')
        w, h = d['size']
        r = self._rotation(display_id)
        sw, sh = (h, w) if r in (90, 270) else (w, h)                         # screen pixels of a display turned by its rotation setting
        pts = [(x, y)] + ([tuple(to)] if to is not None else [])
        if any(not (isinstance(a, int) and isinstance(b, int) and 0 <= a < sw and 0 <= b < sh) for a, b in pts):
            raise PanelError('touch outside the display')
        if r:                                                                 # screen point -> point of the (unturned) layout
            back = {90: lambda a, b: (b, h - 1 - a), 180: lambda a, b: (w - 1 - a, h - 1 - b), 270: lambda a, b: (w - 1 - b, a)}[r]
            x, y = back(x, y)
            to = list(back(*to)) if to is not None else None
        thr = self.spec.get('timing', {}).get('long_press_s')
        return self._gesture(dict(event='touch', display=display_id, x=x, y=y, to=list(to) if to is not None else None, duration_s=float(duration_s),
                                  long=thr is not None and duration_s >= thr - 1e-9))

    def _held(self, held, input_id):
        held = sorted(set(held or ()))
        for k in held:
            if k == input_id or self._input(k)['kind'] not in PRESS_KINDS:
                raise PanelError(f'held input {k!r}')
        return held

    def _gesture(self, ev):
        self._cache = None
        self._sync()
        self._accepted = True
        self.ui['last'] = dict(ev, accepted=None, rejection=None)
        if self.ui['message'] is not None and self.ui['message'].get('until_input'):   # a message shown until the next key (e.g. [Info] help)
            self.ui['message'] = None
        handled = self._dispatch(ev)
        if not handled and ev.get('long'):                                 # a long press without a long-press binding acts as a short press
            handled = self._dispatch(dict(ev, long=False))
        ok = bool(handled) and self._accepted
        self.ui['last_input_t'] = self._now()
        last = self.ui['last']
        last['accepted'] = ok
        self._cache = None
        self._sync()
        self.ui['last'] = last
        return ok

    def _dispatch(self, ev):
        for gate in self.spec.get('gates', []):
            if self._cond(gate.get('when'), dict(ev=ev)) and not any(self._match(p, ev) for p in gate.get('except', [])):
                self._run(gate.get('do', []), dict(ev=ev))
                self._accepted = False
                return True
        scr = self._screen()
        if (scr.get('style') in MENU_STYLES and self.ui['path'] and self.ui['editor'] is None and ev['event'] == 'press'
                and not ev.get('long')):                                      # keys of the active menu line (item 'keys': {input: actions})
            item, ref = self._current_item()
            acts = (item.get('keys') or {}).get(ev['input'])
            if acts is not None and self._cond(item.get('visible_if'), {}):
                self._run(acts, dict(ev=ev, item=item, item_ref=ref))
                return True
        for b in self._bindings(self.ui['screen']):
            if self._match(b['on'], ev) and self._cond(b.get('when'), dict(ev=ev)):
                self._run(b.get('do', []), dict(ev=ev))
                return True
        handled = getattr(self, '_style_' + scr.get('style', 'static'))(scr, ev)
        if handled is not None:
            return handled
        for b in self.spec.get('bindings', []):
            if self._match(b['on'], ev) and self._cond(b.get('when'), dict(ev=ev)):
                self._run(b.get('do', []), dict(ev=ev))
                return True
        return False

    def _bindings(self, name):
        out, seen = [], set()
        while name and name not in seen:
            seen.add(name)
            s = self._screen(name)
            out += s.get('bindings', [])
            name = s.get('extends')
        return out

    def _match(self, on, ev):
        """Does a gesture match a binding pattern: event, input, long ('any' for both), held keys ('any': with or without), switch position, turn direction ('+' / '-'),
        press-duration window [lo, hi) s, touch display / region / swipe direction."""
        if on.get('any'):
            return True
        if 'event' in on and ev['event'] not in _aslist(on['event']):
            return False
        if 'input' in on and ev.get('input') not in _aslist(on['input']):
            return False
        if ev['event'] in ('press', 'touch'):
            want = on.get('long', False)
            if want != 'any' and bool(want) != bool(ev.get('long')):
                return False
        if 'duration_s' in on:
            lo, hi = on['duration_s']
            if not lo - 1e-9 <= ev.get('duration_s', 0.0) < hi:
                return False
        held = on.get('held', [])
        if ev['event'] in ('press', 'switch') and held != 'any' and sorted(held) != ev.get('held', []):   # 'any': with or without held keys
            return False
        if 'position' in on and on['position'] != ev.get('position'):
            return False
        if on.get('dir') == '+' and ev.get('detents', 0) <= 0 or on.get('dir') == '-' and ev.get('detents', 0) >= 0:
            return False
        if 'display' in on and on['display'] != ev.get('display'):
            return False
        if 'region' in on:
            x, y, w, h = on['region']
            if not (x <= ev.get('x', -1) < x + w and y <= ev.get('y', -1) < y + h):
                return False
        if 'swipe' in on and _swipe(ev) != on['swipe']:
            return False
        return True

    # ---- clock-driven state: power transitions, deadlines, time-outs, forced exits
    def _sync(self):
        now = self._now()
        p = self.spec.get('power')
        if p:
            powered = bool(self._cond(p.get('powered'), {}))
            epoch = self._src(p['epoch'], {}) if p.get('epoch') else None
            if self.ui['powered'] is None or powered != self.ui['powered'] or (powered and epoch != self.ui['epoch']):
                last, beeps = self.ui['last'], self.ui['beeps']
                self.ui = self._fresh(None, self.ui['vars'])
                self.ui.update(powered=powered, epoch=epoch, last=last, beeps=beeps)
                self._goto(self._pick(p.get('on_screen', []), {}) if powered else p['off_screen'])
        elif self.ui['screen'] is None:
            self._goto(self.spec.get('start_screen') or next(iter(self.spec['screens'])))
        ui = self.ui
        ui['pending'] = {k: v for k, v in ui['pending'].items() if now < v['until'] - 1e-9}     # unconfirmed values skip back
        ui['blinks'] = {k: v for k, v in ui['blinks'].items() if now < v['start'] + v['times'] * v['period'] - 1e-9}
        if ui['message'] is not None and now >= ui['message']['until'] - 1e-9:
            ui['message'] = None
        name, scr = ui['screen'], self._screen()
        t_ref = max(x for x in (ui['last_input_t'], ui['entered_t'], -math.inf) if x is not None)
        timeout = self._src(scr['timeout_s'], {}) if isinstance(scr.get('timeout_s'), dict) else scr.get('timeout_s')   # a source: a setting (screen saver)
        if timeout is not None and now - t_ref >= timeout - 1e-9:          # menu / entry time-out (only where the manual has one)
            self._run(scr.get('on_timeout', []), {})
            if self.ui['screen'] == name:
                self._goto(scr.get('timeout_screen') or (scr.get('nav') or {}).get('exit_screen') or self.spec.get('start_screen')
                           or next(iter(self.spec['screens'])))
        scr = self._screen()
        if scr.get('leave_if') and self._cond(scr['leave_if'], {}):
            self._goto(scr['leave_to'])
        else:
            for rule in scr.get('leave', []):                                # several forced exits, the first that holds wins
                if self._cond(rule['when'], {}):
                    self._goto(rule['to'])
                    break
        for _ in range(4 if self.spec.get('leave_chain') else 0):            # spec leave_chain: a screen reached by a forced exit may be left at
            name = self.ui['screen']                                         # once by its own 'leave' rules (e.g. start image -> main -> message)
            rule = next((r for r in self._screen().get('leave', []) if self._cond(r['when'], {})), None)
            if rule is None or rule['to'] == name:
                break
            self._goto(rule['to'])

    def _pick(self, choices, ctx):
        for c in choices:
            if self._cond(c.get('when'), ctx):
                return c['screen']
        raise PanelError('no screen matches')

    def _goto(self, name, ctx=None, menu=None, path=None):
        """Show a screen; a menu screen may open another menu (menu) and start at an item path (jump keys)."""
        if name not in self.spec['screens']:
            raise PanelError(f'unknown screen {name!r}')
        ui = self.ui
        ui.update(screen=name, menu=menu, editor=None, path=[], overlay=None, overlays=[], focus=None, entered_t=self._now())
        s = self._screen(name)
        style, nav = s.get('style', 'static'), s.get('nav') or {}
        if nav.get('function_bar') is not None:                               # text dialogs: the function bar starts at its first function
            ui['fbar'] = 0
        if style in MENU_STYLES:
            ui['path'] = list(path) if path else [nav.get('start', 0)]
            if not path and nav.get('function_bar') is not None or nav.get('settle'):
                self._menu_settle()
        if style == 'parameter_cycle':
            self._open_editor(('cycle', name), nav['params'])
        if style == 'touch_gui':
            ui['page'], ui['history'] = nav['start'], []
        if s.get('editor'):
            self._open_editor(('editor', s['editor']), self.spec['editors'][s['editor']]['fields'])
        self._run(s.get('on_enter', []), ctx or {})
        return True

    # ---- conditions and sources
    def _cond(self, c, ctx):
        if c is None:
            return True
        if isinstance(c, bool):
            return c
        if 'all' in c:
            return all(self._cond(x, ctx) for x in c['all'])
        if 'any' in c:
            return any(self._cond(x, ctx) for x in c['any'])
        if 'not' in c:
            return not self._cond(c['not'], ctx)
        if 'editing' in c:
            ed = self.ui['editor']
            if not self._edits(ed, ctx):
                return False
            return c['editing'] is True or self._current_field(ed)['id'] == c['editing']
        if 'pending' in c:
            return c['pending'] in self.ui['pending']
        if 'screen' in c:
            return self.ui['screen'] in _aslist(c['screen'])
        if 'page' in c:                                                       # touch pages, open forms and overlays
            return self.ui['page'] in _aslist(c['page'])
        if 'form_open' in c:
            return c['form_open'] in self.ui['forms']
        if 'overlay' in c:
            ov = self.ui['overlay']
            return ov is not None and (c['overlay'] is True or c['overlay'] in (ov.get('kind'), ov.get('dialog')))
        if 'focus' in c:                                                      # the widget the rotary focus is on (e.g. EDIT acts on it)
            return self.ui['focus'] in _aslist(c['focus'])
        if 'blinking' in c:
            return c['blinking'] in self.ui['blinks']
        if 'alternate' in c:                                                  # displays that alternate two contents (e.g. value / error code)
            a = c['alternate']
            return int(math.floor(self._now() / a.get('period_s', 2.0) + 1e-9)) % a.get('count', 2) == a.get('index', 0)
        v = self._src(c, ctx)
        if 'same' in c:                                                       # compare two sources (e.g. a list row with the selection)
            return v == self._src(c['same'], ctx)
        if 'eq' in c:
            return v == c['eq']
        if 'ne' in c:
            return v != c['ne']
        if 'in' in c:
            pool = self._src(c['in']['src'], ctx) if isinstance(c['in'], dict) and 'src' in c['in'] else c['in']   # {'src': source} list
            return v in (pool or [])
        for op, f in (('gt', lambda a, b: a > b), ('ge', lambda a, b: a >= b), ('lt', lambda a, b: a < b), ('le', lambda a, b: a <= b)):
            if op in c:
                b = self._src(c[op]['src'], ctx) if isinstance(c[op], dict) and 'src' in c[op] else c[op]   # {'src': source}: compare two sources
                try:
                    return v is not None and b is not None and f(v, b)
                except TypeError:
                    return False
        if c.get('gt_now'):
            return v is not None and v > self._now() + 1e-9
        return bool(v)

    def _src(self, s, ctx):
        if not isinstance(s, dict):
            return s
        if 'cases' in s:
            for case in s['cases']:
                if self._cond(case.get('when'), ctx):
                    return self._src({k: v for k, v in case.items() if k != 'when'}, ctx)
            return None
        if 'const' in s:
            v = s['const']
        elif 'state' in s:
            v = self._state().get(s['state'])
            if 'key' in s:                                                    # entity collections: the element whose id is the key
                v = _keyed(_dig(v, s['key_path']) if s.get('key_path') else v, self._src(s['key'], ctx), s.get('key_field', 'id'))
            if s.get('path'):                                                 # dotted string or list of keys (keys with dots); list indexes
                v = _dig(v, s['path']) if isinstance(v, (dict, list)) else None
        elif 'var' in s:
            v = self.ui['vars'].get(s['var'])
        elif 'field' in s:
            v = self._field_value(s['field'], ctx)
        elif 'field_text' in s:
            v = self._field_display(s['field_text'], ctx)
        elif 'message' in s:
            v = self.ui['message']['text'] if self.ui['message'] else None
        elif 'value' in s:
            v = ctx.get('value') if s['value'] is True else self._value_shown(s['value'])
        elif 'current' in s:
            v = self._current_info(s['current'])
        elif 'event' in s:
            ev = ctx.get('ev') or {}
            if s['event'] == 'repeats':                                      # auto-repeat steps of a held key
                d = ev.get('duration_s', 0.0)
                v = 1 + max(0, int(math.floor((d - s.get('delay_s', 0.5)) * s.get('rate_hz', 10.0) + 1e-9)))
            else:
                v = ev.get(s['event'])
        elif 'now' in s:
            v = self._now()
        elif 'sum' in s:
            parts = [self._src(x, ctx) for x in s['sum']]
            v = None if any(x is None for x in parts) else sum(parts)
        elif 'row' in s:                                                     # dynamic list rows and dialogs opened from a row
            v = _dig(ctx.get('row'), s['row'])
        elif 'row_index' in s:
            v = ctx.get('row_index')
        elif 'form_values' in s:                                              # all value fields of a form (written as one object)
            fid = s['form_values'] if isinstance(s['form_values'], str) else self._form_id({})
            buf = self._form_buffer(fid, ctx)
            v = {f['id']: copy.deepcopy(buf.get(f['id'])) for f in self._form(fid)['fields']
                 if f.get('type') not in ('link', 'action', 'info') and not f.get('no_write')
                 and (not s.get('visible_only') or self._cond(f.get('visible_if'), dict(ctx, buffer=buf)))}
        elif 'object' in s:                                                   # composed arguments: {'object': {k: source}}, {'list': [source]}
            v = {k: self._src(x, ctx) for k, x in s['object'].items()}
        elif 'list' in s:
            v = [self._src(x, ctx) for x in s['list']]
        elif 'len' in s:
            x = self._src(s['len'], ctx)
            v = len(x) if isinstance(x, (list, tuple, dict, str)) else 0
        elif 'hook' in s:
            v = self.hooks[s['hook']](self, ctx, s)
        elif 'menu' in s:                                                     # text dialogs: title, cursor position, item data, function bar
            v = self._menu_info(s['menu'], ctx)
        elif 'item' in s:                                                    # a key of the menu item under the cursor (e.g. its id, a gas id)
            it, _ = self._current_item() if self.ui['path'] and self._screen().get('style') in MENU_STYLES else (None, None)
            v = None if it is None else (self._item_label(it) if s['item'] == 'label' else it.get(s['item']))
        elif 'menu_path' in s:                                                # the menu cursor path (to come back to the same line later)
            v = list(self.ui['path']) if self.ui['path'] and self._screen().get('style') in MENU_STYLES else None
        else:
            raise PanelError(f'unknown source {sorted(s)}')
        if v is None:
            return None
        if 'map' in s:
            v = s['map'].get(_key(v), s.get('default', v))
        if 'scale' in s:                                                      # a factor, a source (e.g. of the display unit) or a list of them
            for k in (s['scale'] if isinstance(s['scale'], list) else [s['scale']]):
                k = self._src(k, ctx) if isinstance(k, dict) else k
                if k is None:
                    return None
                v = v * k
        if 'offset' in s:
            k = self._src(s['offset'], ctx) if isinstance(s['offset'], dict) else s['offset']
            if k is None:
                return None
            v = v + k
        if 'mod' in s:
            v = math.fmod(v, s['mod'])
        if 'convert' in s:
            cv = self.spec['conversions'][s['convert']]
            if self._cond(cv.get('when'), ctx):
                v = v * cv.get('scale', 1.0) + cv.get('offset', 0.0)
        if 'round' in s:
            if isinstance(s['round'], int) and not isinstance(s['round'], bool):          # round to n decimals (e.g. K = C + 273.15 to 0.01)
                v = round(v, s['round'])
            else:
                v = {'ceil': lambda x: int(math.ceil(x - 1e-9)), 'floor': lambda x: int(math.floor(x + 1e-9)),
                     'nearest': lambda x: int(math.floor(x + 0.5 + 1e-9))}[s['round']](v)
        if 'now_plus' in s:
            v = self._now() + s['now_plus']
        if 'state' not in s and 'key' in s:                                   # keyed lookup on any other source, then 'at' (a path)
            v = _keyed(v, self._src(s['key'], ctx), s.get('key_field', 'id'))
        if 'at' in s:
            v = _dig(v, s['at'])
        if 'slice' in s and isinstance(v, (str, list)):                       # part of a string / list, e.g. the date of an ISO time
            v = v[s['slice'][0]:s['slice'][1]]
        return v

    def _num(self, s, ctx):
        v = self._src(s, ctx) if isinstance(s, dict) else s
        return int(v)

    # ---- adjusters (values set by a knob outside menus; mode-D style confirmation with a time-out)
    def _bounds(self, d, ctx=None):
        lo, hi = d['min'], d['max']
        if d.get('min_from'):
            v = self._src(d['min_from'], ctx or {})
            lo = lo if v is None else max(lo, v)
        if d.get('max_from'):
            v = self._src(d['max_from'], ctx or {})
            hi = hi if v is None else min(hi, v)
        return lo, hi

    def _grid(self, d, ctx=None, limited=True):
        """Allowed values of a number, sorted (min..max by step plus 'extra', inside the dynamic limits when limited); None when the range is
        too fine to list (it is then stepped arithmetically)."""
        step = d.get('step', 1)
        n = int(math.floor((d['max'] - d['min']) / step + 1e-9))
        if n > 20000:
            return None
        vals = sorted({round(d['min'] + i * step, 9) for i in range(n + 1)} | set(d.get('extra', [])))
        if not limited or not (d.get('min_from') or d.get('max_from')):
            return vals
        lo, hi = self._bounds(d, ctx)                                         # dynamic limits only ('extra' values may lie outside min..max)
        return [v for v in vals if (not d.get('min_from') or v >= lo - 1e-9) and (not d.get('max_from') or v <= hi + 1e-9)] or vals[:1]

    def _snap_value(self, d, v, ctx=None, limited=False):
        g = self._grid(d, ctx, limited)
        if g is not None:
            return self._snap(g, v)
        step = d.get('step', 1)
        lo, hi = self._bounds(d, ctx) if limited else (d['min'], d['max'])
        return round(min(max(d['min'] + math.floor((v - d['min']) / step + 0.5) * step, lo), hi), 9)

    @staticmethod
    def _snap(grid, v):
        i = bisect.bisect_left(grid, v)
        cand = [grid[j] for j in (i - 1, i) if 0 <= j < len(grid)]
        return min(cand, key=lambda g: (abs(g - v), g))

    def _step_number(self, d, v, n, ctx=None, wrap=False):
        g = self._grid(d, ctx)
        if g is None:
            lo, hi = self._bounds(d, ctx)
            step = d.get('step', 1)
            x = self._snap_value(d, v) + n * step
            if wrap:
                x = lo + (x - lo) % (hi - lo + step)
            return round(min(max(x, lo), hi), 9)
        i = bisect.bisect_left(g, self._snap(self._grid(d, ctx, limited=False), v) - 1e-9)
        j = (i + n) % len(g) if wrap else min(max(i + n, 0), len(g) - 1)
        return g[j]

    def _value_current(self, vid):
        d = self.spec['values'][vid]
        v = self._src(d['read'], {})
        return None if v is None else self._snap_value(d, v)

    def _value_shown(self, vid):
        p = self.ui['pending'].get(vid)
        return p['value'] if p else self._value_current(vid)

    def _adjust(self, vid, n):
        d = self.spec['values'][vid]
        base = self._value_shown(vid)
        if base is None:
            return False
        new = self._step_number(d, base, n, wrap=d.get('wrap', False))
        if d.get('confirm_when') is not None and self._cond(d['confirm_when'], {}):
            self.ui['pending'][vid] = dict(value=new, until=self._now() + d.get('confirm_timeout_s', math.inf))
            return True
        if new == base:
            return True                                                      # at a stop: nothing changes
        return self._write(d['write'], dict(value=new))

    def _confirm_value(self, vid):
        p = self.ui['pending'].pop(vid, None)
        if p is None:
            return False
        return self._write(self.spec['values'][vid]['write'], dict(value=p['value']))

    # ---- capability calls
    def _args(self, args, ctx):
        out = {}
        for k, s in (args or {}).items():
            if isinstance(s, dict) and 'when' in s and 'cases' not in s:
                if not self._cond(s['when'], ctx):
                    continue
                s = {a: b for a, b in s.items() if a != 'when'}
            v = self._src(s, ctx)
            if isinstance(v, float) and v.is_integer() and isinstance(s, dict) and s.get('integer'):
                v = int(v)
            out[k] = v
        return out

    def _invoke(self, cap, args, on_reject=None, ctx=None):
        try:
            self.host.panel_invoke(cap, args)
            return True
        except PanelRejected as exc:
            code = exc.code
            if self.ui.get('last') is not None:
                self.ui['last']['rejection'] = code
            acts = (on_reject or {}).get(code)
            if acts is None:
                acts = self.spec.get('errors', {}).get('runtime', {}).get(code, {}).get('indication', [])
            self._accepted = False
            self._run(acts, ctx or {})
            return False
        finally:
            self._cache = None

    def _write(self, w, ctx):
        if not w:
            return True
        if w.get('when') is not None and not self._cond(w['when'], ctx):
            return True
        if 'calls' in w:
            return all(self._write(c, ctx) for c in w['calls'])
        return self._invoke(w['capability'], self._args(w.get('args'), ctx), w.get('on_reject'), ctx)

    # ---- actions
    def _run(self, actions, ctx):
        for a in actions or []:
            if not self._act(a, ctx):
                return False
        return True

    def _act(self, a, ctx):
        k = a['do']
        if k == 'invoke':
            return self._invoke(a['capability'], self._args(a.get('args'), ctx), a.get('on_reject'), ctx)
        if k == 'goto':                                                       # path: item indices, or a source (e.g. a menu path kept in a var)
            path = self._src(a['path'], ctx) if isinstance(a.get('path'), dict) else a.get('path')
            return self._goto(a['screen'], ctx, a.get('menu'), path)
        if k == 'adjust':
            return self._adjust(a['value'], self._num(a.get('detents', {'event': 'detents'}), ctx))
        if k == 'confirm':
            return self._confirm_value(a['value'])
        if k == 'nav':
            return self._nav(a['op'], ctx, a)
        if k == 'edit':
            self._open_editor(('editor', a['editor']), self.spec['editors'][a['editor']]['fields'])
            return True
        if k == 'set_var':
            self.ui['vars'][a['var']] = self._src(a['value'], ctx)
            return True
        if k == 'blink':
            self.ui['blinks'][a['target']] = dict(start=self._now(), times=int(a.get('times', 5)), period=float(a.get('period_s', self._period())))
            return True
        if k == 'beep':
            self.ui['beeps'] += 1
            return True
        if k == 'message':                                                    # transient text (e.g. 'Out of range'), shown for duration_s
            self.ui['message'] = dict(text=str(self._src(a['text'], ctx)) if isinstance(a['text'], dict) else a['text'],
                                      until=self._now() + float(a.get('duration_s', 2.0)))
            if a.get('until_input'):                                          # also cleared by the next gesture (help texts, prompts)
                self.ui['message']['until_input'] = True
            return True
        if k == 'if':
            return self._run(a.get('then') if self._cond(a['when'], ctx) else a.get('else'), ctx)
        if k == 'ignore':
            self._accepted = False
            return True
        if k == 'reject':
            self._run(a.get('indication'), ctx)
            self._accepted = False
            return False
        if k == 'unsupported':
            self.ui['unsupported'] = a.get('note', True)
            self._accepted = False
            return False
        if k == 'page':
            page = self._src(a['page'], ctx) if isinstance(a['page'], dict) else a['page']       # a source: e.g. the page a screen saver left
            if page is None:
                return True
            if a.get('clear_history'):
                self.ui['history'] = []
            elif self.ui['page'] != page:
                self.ui['history'].append(self.ui['page'])
            self.ui.update(page=page, overlay=None, overlays=[])
            return True
        if k == 'page_back':
            if self.ui['history']:
                self.ui.update(page=self.ui['history'].pop(), overlay=None, overlays=[])
            return True
        if k == 'hook':
            return bool(self.hooks[a['name']](self, ctx, a))
        if k in TOUCH_ACTIONS:
            return self._touch_act(a, ctx)
        raise PanelError(f'unknown action {k!r}')

    # ---- editors (menu items, stand-alone entries, parameter cycles share one model: a buffer edited field by field)
    def _item_fields(self, item):
        t = item['type']
        if t in ('bool', 'enum', 'number', 'text'):
            return [item]
        if t in ('submenu', 'action') and item.get('fields'):
            return item['fields']
        return []

    def _field_initial(self, f, ctx):
        if 'initial' in f:
            return copy.deepcopy(f['initial'])
        v = self._src(f['read'], ctx) if f.get('read') else None
        if f['type'] == 'number':
            if v is None and 'none_text' in f:                                 # a number that may be switched off ('off', 'max.')
                return None
            if v is not None and f.get('entry') == 'grid':                      # typed on a character grid: any value, checked on Accept
                return v
            return self._snap_value(f, v if v is not None else f['min'], ctx, limited=True)
        if f['type'] in ('enum', 'bool'):
            opts = [o['value'] for o in (self._opts(f, ctx) if 'options_src' in f else self._options(f))]
            return v if v in opts or not opts else opts[0]
        return '' if v is None and f['type'] == 'text' else v

    @staticmethod
    def _options(f):
        if f['type'] == 'bool':
            return f.get('options') or [dict(value=False, text='OFF'), dict(value=True, text='ON')]
        return f.get('options', [])

    def _open_editor(self, ref, fields):
        ctx = dict(item_ref=ref[1] if ref[0] == 'item' else None)
        buf = {}
        for f in fields:
            buf[f['id']] = self._field_initial(f, dict(ctx, buffer=buf))
        self.ui['editor'] = dict(ref=list(ref), index=0, buffer=buf, typed=None, digit=0)
        self._skip_hidden(+1)
        self._enter_field()

    def _enter_field(self):
        """Reset the per-field entry state (typed text, digit cursor) when the editor moves to another field."""
        ed = self.ui['editor']
        fields = self._editor_fields(ed)
        ed['typed'] = None
        ed['digit'] = fields[ed['index']].get('digit_start', 0) if 0 <= ed['index'] < len(fields) else 0
        if 0 <= ed['index'] < len(fields) and fields[ed['index']].get('entry') == 'grid':
            self._grid_init(ed, fields[ed['index']])                         # character-grid entry starts from the shown value

    def _editor_fields(self, ed=None):
        ed = ed or self.ui['editor']
        kind, ref = ed['ref'][0], ed['ref'][1]
        if kind == 'item':
            return self._item_fields(self._item_at(ref))
        if kind == 'editor':
            return self.spec['editors'][ref]['fields']
        return self._screen(ref)['nav']['params']

    def _current_field(self, ed=None):
        ed = ed or self.ui['editor']
        return self._editor_fields(ed)[ed['index']]

    def _visible(self, f, buf):
        return self._cond(f.get('visible_if'), dict(buffer=buf))

    def _skip_hidden(self, direction):
        ed = self.ui['editor']
        fields = self._editor_fields(ed)
        while 0 <= ed['index'] < len(fields) and not self._visible(fields[ed['index']], ed['buffer']):
            ed['index'] += direction

    def _edit_change(self, n, wrap=None):
        """Turn / arrow on the field being edited: next option or step, the digit under the cursor (entry 'digits', no carry), or the
        character under the cursor (text fields with a charset)."""
        ed = self.ui['editor']
        f = self._current_field(ed)
        ctx = dict(buffer=ed['buffer'])
        v = ed['buffer'][f['id']]
        w = f.get('wrap', False) if wrap is None else wrap
        ed['typed'] = None
        if f['type'] == 'number' and f.get('entry') == 'digits' and f.get('signed') and ed['digit'] == 0:
            nv = round(-v, 9)                                                 # 'signed': the cursor on the sign flips it (e.g. a +/-10 C offset)
            if f.get('min') is not None and f.get('max') is not None and not (f['min'] - 1e-9 <= nv <= f['max'] + 1e-9):
                return False
            ed['buffer'][f['id']] = nv
        elif f['type'] == 'number' and f.get('entry') == 'digits':
            k = ed['digit'] - (1 if f.get('signed') else 0)
            place, radix = f['places'][k] if f.get('places') else (10.0 ** (f['digits'] - f.get('decimals', 0) - 1 - k), 10)
            d = int(math.floor(abs(v) / place + 1e-9)) % radix                # 'places': [place, radix] per digit (e.g. HH:MM:SS.s in seconds)
            nv = abs(v) + (((d + n) % radix) - d) * place
            nv = round(math.copysign(nv, v) if v < 0 else nv, 9)
            if f.get('min') is not None and f.get('max') is not None and not (f['min'] - 1e-9 <= nv <= f['max'] + 1e-9):
                return False                                                  # a digit that would leave the range does not move
            ed['buffer'][f['id']] = nv
        elif f['type'] == 'number':
            ed['buffer'][f['id']] = self._step_number(f, v, n * f.get('stride', 1), ctx, wrap=w)   # stride: grid steps per press / detent
        elif f['type'] in ('enum', 'bool'):
            opts = [o['value'] for o in (self._opts(f, ctx) if 'options_src' in f else self._options(f))]
            if not opts:
                return False
            i = opts.index(v) if v in opts else 0
            ed['buffer'][f['id']] = opts[(i + n) % len(opts)] if w else opts[min(max(i + n, 0), len(opts) - 1)]
        elif f['type'] == 'text' and f.get('charset'):
            cs, text, pos = f['charset'], list(v or ''), ed['digit']
            while len(text) <= pos:
                text.append(cs[0])
            text[pos] = cs[(cs.index(text[pos]) + n) % len(cs) if text[pos] in cs else 0]
            ed['buffer'][f['id']] = ''.join(text)
        else:
            return False
        return True

    def _edit_type(self, ch):
        """A keypad character typed into the field being edited (numbers are parsed and range-checked on confirm)."""
        ed = self.ui['editor']
        f = self._current_field(ed)
        t = ed['typed'] or ''
        if len(t) >= f.get('max_chars', 12):
            return False
        ed['typed'] = t + ch
        return True

    def _digit_move(self, n, wrap=False):
        ed = self.ui['editor']
        f = self._current_field(ed)
        if f.get('entry') == 'digits':
            last = f['digits'] - (0 if f.get('signed') else 1)               # 'signed': the sign is the first cursor position
        elif f['type'] == 'text' and f.get('charset'):
            last = f.get('max_len', 16) - 1
        else:
            return False
        ed['digit'] = (ed['digit'] + n) % (last + 1) if wrap else min(max(ed['digit'] + n, 0), last)   # wrap: one key cycles the cursor
        return True

    def _edit_confirm(self):
        """Accept the field being edited: a typed entry is parsed and range-checked (refused: the field's / spec's 'invalid' indication, the
        entry is cleared); a digit-wise field with digit_confirm 'advance' first walks its digits; then the field's own write (if any) and the
        next visible field; after the last one the owner's write."""
        ed = self.ui['editor']
        f = self._current_field(ed)
        if ed['typed'] is not None:
            text, ed['typed'] = ed['typed'], None
            if f['type'] == 'number':
                try:
                    v = float(text)
                except ValueError:
                    v = None
                if v is None or not (f['min'] - 1e-9 <= v <= f['max'] + 1e-9):
                    self._accepted = False
                    self._run(f.get('on_invalid', self.spec.get('errors', {}).get('entry', {}).get('indication', [])), {})
                    return False
                ed['buffer'][f['id']] = self._snap_value(f, v) if f.get('snap_typed', True) else v
            else:
                ed['buffer'][f['id']] = text
        elif f.get('digit_confirm') == 'advance' and ed['digit'] < f['digits'] - 1:
            ed['digit'] += 1
            return True
        ctx = dict(buffer=ed['buffer'], value=ed['buffer'][f['id']])
        if f.get('write') and not self._write(f['write'], ctx):
            self._close_editor(False)
            return False
        if ed['ref'][0] == 'cycle':                                          # parameter cycle: the style decides where confirm goes next
            return True
        ed['index'] += 1
        self._skip_hidden(+1)
        if ed['index'] < len(self._editor_fields(ed)):
            self._enter_field()
            return True
        return self._finish_editor()

    def _owner(self, ed):
        kind, ref = ed['ref'][0], ed['ref'][1]
        if kind == 'item':
            return self._item_at(ref)
        if kind == 'editor':
            return self.spec['editors'][ref]
        return self._screen(ref)['nav']

    def _finish_editor(self):
        ed = self.ui['editor']
        owner = self._owner(ed)
        ctx = dict(buffer=ed['buffer'], item_ref=ed['ref'][1] if ed['ref'][0] == 'item' else None)
        single = ed['ref'][0] == 'item' and self._item_fields(owner) == [owner]   # a one-value item: its write ran as the field's write
        ok = True if single else self._write(owner.get('write'), ctx)
        self.ui['editor'] = None
        if ok:
            ok = self._run(owner.get('on_finish', []), ctx) and ok
        if ed['ref'][0] == 'editor':
            self._run(owner.get('then', []), ctx)
        return ok

    def _close_editor(self, run_cancel=True):
        ed = self.ui['editor']
        self.ui['editor'] = None
        if ed and run_cancel and ed['ref'][0] == 'editor':
            self._run(self.spec['editors'][ed['ref'][1]].get('cancel', []), {})

    @staticmethod
    def _edits(ed, ctx):
        """Is the open editor the one of the context's menu item (any editor when the context is not a menu item)?"""
        if ed is None:
            return False
        ref = ctx.get('item_ref')
        return ref is None or (ed['ref'][0] == 'item' and list(ed['ref'][1]) == list(ref))

    def _field_value(self, fid, ctx):
        ed = self.ui['editor']
        if ctx.get('buffer') is not None and fid in ctx['buffer']:
            return ctx['buffer'][fid]
        if self._edits(ed, ctx) and fid in ed['buffer']:
            return ed['buffer'][fid]
        for form_id in reversed(self.ui.get('form_order', [])):              # open touch forms, the latest first
            if fid in self.ui['forms'][form_id]['buffer']:
                return self.ui['forms'][form_id]['buffer'][fid]
        f = self._field_def(fid, ctx)
        return None if f is None else self._field_initial(f, ctx)

    def _field_def(self, fid, ctx):
        ed = self.ui['editor']
        pools = []
        if ctx.get('item'):
            pools.append(self._item_fields(ctx['item']))
        if ed is not None:
            pools.append(self._editor_fields(ed))
        for fields in pools:
            for f in fields:
                if f['id'] == fid:
                    return f
        return None

    def _field_display(self, fid, ctx):
        """Text of a field as the display shows it: the typed characters while typing, else the formatted value."""
        ed = self.ui['editor']
        if self._edits(ed, ctx) and ed['typed'] is not None and self._current_field(ed)['id'] == fid:
            return ed['typed']
        f = self._field_def(fid, ctx)
        return None if f is None else self._field_text(f, self._field_value(fid, ctx))

    def _field_text(self, f, v):
        if v is None and not (f['type'] == 'enum' and any(o.get('value', 0) is None for o in f.get('options', []))) \
                and not (f['type'] == 'enum' and (f.get('options_src') or {}).get('none')):
            return f.get('none_text', '')                                     # e.g. 'off' of a number that can be switched off
        if f['type'] in ('enum', 'bool'):
            for o in self._opts(f, filtered=False):
                if o['value'] == v:
                    return self._tr(o.get('text', _key(v)))
            return _key(v)
        if f['type'] == 'number':
            fmt = f.get('fmt') or ({'decimals': f['decimals']} if 'decimals' in f else {})
            if f.get('entry') == 'digits' and f.get('places') and f.get('mask'):
                return _mask_digits(v, f)
            if f.get('entry') == 'digits' and not f.get('fmt'):
                dec = f.get('decimals', 0)
                if f.get('signed'):                                           # '+05': the sign stays shown (and edited) also for zero
                    return ('-' if v < 0 else '+') + f'{abs(v):0{f["digits"] + (1 if dec else 0)}.{dec}f}'
                return f'{v:0{f["digits"] + (1 if dec else 0)}.{dec}f}'
            return _format(v, fmt)
        return str(v)

    def _current_info(self, what):
        ed = self.ui['editor']
        if ed is None:
            return None
        f = self._current_field(ed)
        if what == 'id':
            return f['id']
        if what == 'label':
            return f.get('label', f['id'])
        if what == 'value':
            return ed['buffer'][f['id']]
        if what in ('type', 'entry'):                                         # what kind of field is being edited (softkey labels follow it)
            return f.get(what)
        if what == 'digit':                                                   # digit / character cursor of a digit-wise or character entry
            return ed['digit']
        return ed['typed'] if ed['typed'] is not None else self._field_text(f, ed['buffer'][f['id']])

    # ---- menus
    def _menu_id(self):
        return self.ui.get('menu') or self._screen()['nav']['menu']

    def _menu(self):
        return self.spec['menus'][self._menu_id()]

    def _item_at(self, ref):
        """Item at a reference path [menu_id, i, j, ...] (submenu children by index)."""
        items, it = self.spec['menus'][ref[0]]['items'], None
        for i in ref[1:]:
            it = items[i]
            items = it.get('items', [])
        return it

    def _list_at(self, path):
        items = self._menu()['items']
        for i in path[:-1]:
            items = items[i].get('items', [])
        return items

    def _visible_indices(self, items):
        return [i for i, it in enumerate(items) if self._cond(it.get('visible_if'), {})]

    def _current_item(self):
        if not self.ui['path']:
            return None, None
        items = self._list_at(self.ui['path'])
        return items[self.ui['path'][-1]], [self._menu_id()] + list(self.ui['path'])

    def _nav(self, op, ctx, a=None):
        ui = self.ui
        ed = ui['editor']
        n = self._num(a.get('n', 1), ctx) if a and 'n' in a else 1
        if op == 'change':
            return self._edit_change(n) if ed else False
        if op == 'confirm':
            return self._edit_confirm() if ed else False
        if op == 'cancel':
            if ed:
                self._close_editor()
            return True
        if op in ('type', 'backspace', 'clear'):
            if ed is None:                                                   # typing on a selected line opens it (e.g. a GC parameter list)
                item, _ = self._current_item() if self.ui['path'] else (None, None)
                if op != 'type' or item is None or not self._item_fields(item) or not self._nav('enter', ctx):
                    return False
                ed = ui['editor']
            if op == 'type':
                return self._edit_type(str(a['char']))
            ed['typed'] = (ed['typed'] or '')[:-1] if op == 'backspace' else None
            return True
        if op in ('digit_left', 'digit_right'):
            return bool(ed) and self._digit_move(-1 if op == 'digit_left' else 1, bool(a and a.get('wrap')))
        if op == 'set':                                                      # a key that puts a value into the field (e.g. CLEAR -> 0)
            if not ed:
                return False
            f = self._current_field(ed)
            v = self._src(a['value'], dict(ctx, buffer=ed['buffer']))
            ed['buffer'][f['id']] = self._snap_value(f, v) if f['type'] == 'number' and v is not None and f.get('entry') != 'digits' else v
            self._enter_field()
            return True
        if op in ('next_field', 'prev_field'):
            if not ed:
                return False
            ed['index'] = min(max(ed['index'] + (1 if op == 'next_field' else -1), 0), len(self._editor_fields(ed)) - 1)
            self._skip_hidden(1 if op == 'next_field' else -1)
            ed['index'] = min(max(ed['index'], 0), len(self._editor_fields(ed)) - 1)
            self._enter_field()
            return True
        if op == 'next_param':                                               # parameter cycle: the select key steps to the next parameter
            nav = self._screen()['nav']
            if ed and nav.get('confirm_on_select') and not self._edit_confirm():
                return False
            ed = ui['editor']
            if ed is None:
                return True
            ed['index'] += 1
            self._skip_hidden(+1)
            if ed['index'] >= len(self._editor_fields(ed)):
                ui['editor'] = None
                return self._goto(nav.get('exit_screen') or self.spec.get('start_screen'), ctx)
            f = self._current_field(ed)
            ed['buffer'][f['id']] = self._field_initial(f, dict(buffer=ed['buffer']))
            self._enter_field()
            return True
        if op == 'exit':
            if ed:
                self._close_editor()
            return self._goto(self._screen()['nav'].get('exit_screen') or self.spec.get('start_screen'), ctx)
        if not ui['path']:
            return False
        items = self._list_at(ui['path'])
        if op == 'move':
            if ed:
                return False
            vis = [i for i in self._visible_indices(items) if self._item_enabled(items[i])]   # shown but not selectable: skipped
            if not vis:
                return False
            pos = vis.index(ui['path'][-1]) if ui['path'][-1] in vis else 0
            wrap = self._menu().get('wrap', False)
            pos = (pos + n) % len(vis) if wrap else min(max(pos + n, 0), len(vis) - 1)
            ui['path'][-1] = vis[pos]
            return True
        if op == 'enter':
            if ed:
                return self._edit_confirm()
            item, ref = self._current_item()
            if not self._item_enabled(item):
                return False
            if item.get('enter'):
                return self._run(item['enter'], dict(ctx, item=item, item_ref=ref))
            if item['type'] == 'submenu' and item.get('items'):
                ui['path'].append(self._visible_indices(item['items'])[0])
                return True
            fields = self._item_fields(item)
            if fields:
                self._open_editor(('item', ref), fields)
                return True
            if item['type'] == 'action':
                ok = self._write(item.get('write'), dict(item_ref=ref))
                return self._run(item.get('on_finish', []), dict(item_ref=ref)) and ok
            return True                                                      # info: nothing to edit
        if op == 'back':
            if ed:
                self._close_editor()
                return True
            if len(ui['path']) > 1:
                ui['path'].pop()
                return True
            return self._nav('exit', ctx)
        raise PanelError(f'unknown navigation {op!r}')

    # ---- styles
    def _style_static(self, scr, ev):
        if (scr.get('nav') or {}).get('function_bar') is not None and ev['event'] == 'press' and not ev.get('long'):
            return self._fbar_event(scr['nav'], ev)                          # text dialogs without a list: only the function bar
        return None

    def _typing(self, nav, ev):
        if ev['event'] != 'press' or ev['long']:
            return None
        i = ev['input']
        if i in (nav.get('keypad') or {}):
            return self._nav('type', dict(ev=ev), dict(char=nav['keypad'][i]))
        if i in _aslist(nav.get('backspace') or []):
            return self._nav('backspace', dict(ev=ev))
        if i in _aslist(nav.get('clear') or []) and self.ui['editor'] is not None and self.ui['editor']['typed'] is not None:
            return self._nav('clear', dict(ev=ev))
        return None

    def _style_encoder_menu(self, scr, ev):
        nav, ed = scr['nav'], self.ui['editor']
        typed = self._typing(nav, ev)
        if typed is not None:
            return typed
        if ev['event'] == 'turn' and ev['input'] == nav.get('turn'):
            return self._nav('change' if ed else 'move', {}, dict(n=ev['detents']))
        if ev['event'] == 'press' and not ev['long']:
            if ev['input'] == nav.get('push'):
                return self._nav('confirm' if ed else 'enter', dict(ev=ev))
            if ev['input'] in _aslist(nav.get('back') or []):
                return self._nav('back', dict(ev=ev))
            if ev['input'] in _aslist(nav.get('exit') or []):
                return self._nav('exit', dict(ev=ev))
        return None

    def _style_arrow_menu(self, scr, ev):
        """up / down move the cursor or change the edited value (held keys repeat when nav.repeat is set); left / right move the digit cursor of
        digit-wise and character fields, else the field (editing) or go back / enter; enter, back, exit; an optional knob; keypad typing."""
        nav, ed = scr['nav'], self.ui['editor']
        typed = self._typing(nav, ev)
        if typed is not None:
            return typed
        if ev['event'] == 'turn' and ev['input'] == nav.get('knob'):
            return self._nav('change' if ed else 'move', {}, dict(n=ev['detents']))
        if ev['event'] != 'press':
            return None
        i = ev['input']
        ged = self.ui['editor']
        if ev['long'] and ged is not None and self._current_field(ged).get('entry') == 'grid':
            return self._grid_event(nav, ev)                                 # a character grid: holding OK enters the variant (capitals)
        if ev['long'] and i not in (nav.get('up'), nav.get('down')):
            return None                                                      # falls back to a short press
        dlg = self._dialog_event(scr, ev)                                    # text dialogs: grid entry, selection lists, function bar
        if dlg is not None:
            return dlg
        steps = self._src({'event': 'repeats', **nav['repeat']}, dict(ev=ev)) if nav.get('repeat') else 1   # held keys auto-repeat
        if i == nav.get('up'):
            return self._nav('change' if ed else 'move', {}, dict(n=steps if ed else -steps))
        if i == nav.get('down'):
            return self._nav('change' if ed else 'move', {}, dict(n=-steps if ed else steps))
        if i in (nav.get('left'), nav.get('right')):
            if ed:
                f = self._current_field(ed)
                if f.get('lr_change'):                                       # a field changed with left / right (e.g. a unit)
                    return self._nav('change', {}, dict(n=1 if i == nav.get('right') else -1))
                if f.get('entry') == 'digits' or (f['type'] == 'text' and f.get('charset')):
                    return self._nav('digit_right' if i == nav.get('right') else 'digit_left', {})
                return self._nav('next_field' if i == nav.get('right') else 'prev_field', {})
            if nav.get('left_right') == 'none':
                return None
            return self._nav('back' if i == nav.get('left') else 'enter', dict(ev=ev))
        if i in _aslist(nav.get('enter') or []):
            return self._nav('confirm' if ed else 'enter', dict(ev=ev))
        if i in _aslist(nav.get('back') or []):
            return self._nav('back', dict(ev=ev))
        if i in _aslist(nav.get('exit') or []):
            return self._nav('exit', dict(ev=ev))
        return None

    def _style_softkey_menu(self, scr, ev):
        nav = scr['nav']
        for sk in nav.get('softkeys', []):
            if sk.get('label_only'):                                          # only names the key (e.g. OK of a character grid: the arrow-menu rules act)
                continue
            if ev['event'] == 'press' and not ev['long'] and ev['input'] == sk['input'] and self._cond(sk.get('when'), dict(ev=ev)):
                if sk.get('op'):
                    op = sk['op']
                    if op in ('up', 'down'):
                        ed = self.ui['editor']
                        n = (1 if op == 'up' else -1) * (1 if ed else -1)
                        return self._nav('change' if ed else 'move', {}, dict(n=n))
                    if op == 'enter' and self.ui['editor']:
                        op = 'confirm'
                    return self._nav(op, dict(ev=ev))
                self._run(sk.get('do', []), dict(ev=ev))
                return True
        return self._style_arrow_menu(scr, ev) if any(nav.get(k) for k in ('up', 'down', 'enter', 'knob')) else None

    def _style_parameter_cycle(self, scr, ev):
        nav, ed = scr['nav'], self.ui['editor']
        if ed is None:
            return None
        typed = self._typing(nav, ev)
        if typed is not None:
            return typed
        if ev['event'] == 'turn' and ev['input'] == nav.get('knob'):
            return self._nav('change', {}, dict(n=ev['detents']))
        if ev['event'] != 'press':
            return None
        i = ev['input']
        if ev['long'] and i not in (nav.get('inc'), nav.get('dec')):
            return None
        steps = self._src({'event': 'repeats', **nav['repeat']}, dict(ev=ev)) if nav.get('repeat') else 1
        if i in (nav.get('inc'), nav.get('dec')):
            return self._nav('change', {}, dict(n=steps if i == nav.get('inc') else -steps))
        if i == nav.get('select'):
            return self._nav('next_param', dict(ev=ev))
        if i == nav.get('confirm'):
            ok = self._edit_confirm()
            after = nav.get('after_confirm', 'next')
            if after == 'next' and self.ui['editor'] is not None:
                return self._nav('next_param', dict(ev=ev)) and ok
            if after == 'exit':
                self.ui['editor'] = None
                self._goto(nav.get('exit_screen') or self.spec.get('start_screen'))
            return ok
        if i in _aslist(nav.get('exit') or []):
            return self._nav('exit', dict(ev=ev))
        return None

    def _style_touch_gui(self, scr, ev):
        """Touch: the overlay (keypad / choice list) first, then the page's swipes, then the top-most widget under the finger.  A knob
        (nav.knob / nav.push) moves a focus over the enabled widgets and activates it, as on touch screens with a rotary control."""
        nav = scr['nav']
        if ev['event'] == 'press' and not ev.get('long') and ev['input'] in _aslist(nav.get('home') or []):
            return self._touch_home(nav)
        if ev['event'] == 'press' and not ev.get('long') and ev['input'] in _aslist(nav.get('back') or []):
            return self._touch_back()
        if nav.get('knob_edits_overlay') and self.ui['overlay'] is not None and (
                (ev['event'] == 'turn' and ev['input'] == nav.get('knob')) or (ev['event'] == 'press' and ev['input'] in _aslist(nav.get('push') or []))):
            return self._knob_overlay(self.ui['overlay'], ev)                 # rotary control on an open editor (e.g. BUCHI navigation control)
        if ev['event'] == 'turn' and ev['input'] == nav.get('knob') and self.ui['overlay'] is None:
            ws = [w for w in self._page_widgets(nav['pages'][self.ui['page']]) if self._cond(w.get('enabled'), {}) and w['type'] != 'label'
                  and not self._cond(w.get('knob_skip', False), {})]            # knob_skip: widgets the rotary focus passes over (e.g. a function bar)
            if not ws:
                return False
            ids = [w['id'] for w in ws]
            k = ids.index(self.ui['focus']) if self.ui['focus'] in ids else -1 if ev['detents'] > 0 else 0
            self.ui['focus'] = ids[min(max(k + ev['detents'], 0), len(ids) - 1)]
            return True
        if ev['event'] == 'press' and ev['input'] in _aslist(nav.get('push') or []) and self.ui['focus'] and self.ui['overlay'] is None:
            w = next((x for x in self._page_widgets(nav['pages'][self.ui['page']]) if x['id'] == self.ui['focus']), None)   # push: one key or several (knob push, ENTER)
            if w is None:
                return False
            x, y, ww, hh = w['rect']
            return self._touch_widget(scr, w, dict(ev, x=x + ww // 2, y=y + hh // 2, to=None))
        if ev['event'] != 'touch':
            return None
        if ev['display'] != nav['display']:
            return False
        ov = self.ui['overlay']
        if ov is not None:
            return self._touch_overlay(scr, ov, ev)
        page = nav['pages'][self.ui['page']]
        sw = _swipe(ev)
        if sw and any(w.get('drag_target') and w['rect'][0] <= ev['x'] < w['rect'][0] + w['rect'][2] and w['rect'][1] <= ev['y'] < w['rect'][1] + w['rect'][3]
                      for w in self._page_widgets(page)):
            sw = None                                                         # drag_target: a drag that starts on it (e.g. a set-point marker) is not a swipe
        if sw and page.get('swipes', {}).get(sw) is not None:
            return self._run(page['swipes'][sw], dict(ev=ev))
        for w in reversed(self._page_widgets(page)):
            x, y, ww, hh = w['rect']
            if not (x <= ev['x'] < x + ww and y <= ev['y'] < y + hh) or w.get('pass_through'):
                continue                                                      # pass_through: a passive message (toast) over the widgets
            if not self._cond(w.get('enabled'), dict(ev=ev)):
                return False
            return self._touch_widget(scr, w, ev)
        return False

    def _page_widgets(self, page):
        return self._layout_page(page)

    def _knob_overlay(self, ov, ev):
        """nav.knob_edits_overlay: turning the rotary control steps the value of an open number keyboard by the field's step (within min / max)
        or moves the highlighted option of an open choice list; pressing it confirms (the keyboard's OK, the highlighted option).  Other
        overlays ignore the knob."""
        kind = ov.get('kind')
        if kind not in ('keyboard', 'select'):
            return False
        f = self._target_def(ov['target'])
        if kind == 'keyboard':
            if f.get('type') != 'number':
                return False
            if ev['event'] == 'press':
                return self._keyboard_ok(ov, ev)
            try:
                v = float(ov['buffer'])
            except ValueError:
                v = f.get('min', 0.0)
            v = round(min(max(v + ev['detents'] * f.get('step', 1), f.get('min', -math.inf)), f.get('max', math.inf)), 9)
            ov.update(buffer=self._field_text(dict(f, unit=None), v), selected=False, error=None)
            return True
        values = [o['value'] for o in self._opts(f)]
        if not values:
            return False
        if ev['event'] == 'press':
            if ov.get('value') not in values:
                return False
            self._pop_overlay()
            return self._commit(ov['target'], ov['value'], ev)
        i = values.index(ov['value']) if ov.get('value') in values else (-1 if ev['detents'] > 0 else len(values))
        ov['value'] = values[min(max(i + ev['detents'], 0), len(values) - 1)]
        return True

    def _touch_widget(self, scr, w, ev):
        t = w['type']
        ctx = dict(ev=ev)
        if w.get('guard') and self._cond(w['guard'].get('when'), ctx):      # e.g. a remote lock: the device's warning instead of the widget's action
            return self._run(w['guard'].get('do', []), ctx)
        if t == 'plot':                                                      # a chart is passive unless it has a 'do' (e.g. a thumbnail opening its page)
            return self._run(w['do'], ctx) if w.get('do') else False
        if t == 'image':                                                     # a bitmap: 'do' gets the touch (and drag end) in image fractions u, v
            if not w.get('do'):
                return False
            box = _image_box(w['rect'], self._image_entry(w, ctx))
            uv = lambda x, y: (min(max((x - box[0]) / box[2], 0.0), 1.0), min(max((y - box[1]) / box[3], 0.0), 1.0))   # noqa: E731
            u, v = uv(ev['x'], ev['y'])
            u2, v2 = uv(*ev['to']) if ev.get('to') is not None else (u, v)
            return self._run(w['do'], dict(ctx, ev=dict(ev, u=u, v=v, u2=u2, v2=v2)))
        if t in ('label', 'lamp', 'gauge', 'xy') and w.get('do') is not None:   # touch zones (a weight value, a SmartTrac bar): their 'do' runs
            return self._run(w['do'], ctx)
        if t in ('form', 'lamp', 'gauge', 'xy') or (t == 'list' and 'items' in w) or (t in ('number', 'text') and w.get('keyboard')):
            return self._touch_widget2(scr, w, ev)
        if t == 'button':
            return self._run(w['do_long'] if ev.get('long') and w.get('do_long') is not None else w.get('do', []), ctx)
        if t == 'slider':                                                    # tap or drag: the value under the finger (end of a drag)
            x0, _, ww, _ = w['rect']
            x = (ev['to'] or [ev['x']])[0]
            v = w['min'] + (w['max'] - w['min']) * min(max((x - x0) / max(1, ww - 1), 0.0), 1.0)
            return self._write(w['write'], dict(ctx, value=self._snap_value(dict(w, extra=[]), v)))
        if t == 'toggle':
            cur = bool(self._src(w['read'], ctx))
            return self._write(w['write'], dict(ctx, value=not cur))
        if t == 'list':
            if ev.get('to') is not None:                                     # drag: scroll the list
                rows = w.get('rows', [])
                visible = max(1, w['rect'][3] // w.get('row_h', 40))
                off = self.ui['scroll'].get(w['id'], 0) - int(round((ev['to'][1] - ev['y']) / w.get('row_h', 40)))
                self.ui['scroll'][w['id']] = min(max(off, 0), max(0, len(rows) - visible))
                return True
            k = self.ui['scroll'].get(w['id'], 0) + (ev['y'] - w['rect'][1]) // w.get('row_h', 40)
            rows = [r for r in w.get('rows', []) if self._cond(r.get('visible_if'), ctx)]
            return 0 <= k < len(rows) and self._run(rows[k].get('do', []), ctx)
        if t in ('number', 'text'):
            cur = self._src(w['read'], ctx) if w.get('read') else None
            self.ui['overlay'] = dict(kind='keypad', widget=w['id'], buffer='' if cur is None or w.get('clear_on_open', True) else _key(cur), error=None)
            return True
        if t == 'choice':
            self.ui['overlay'] = dict(kind='choice', widget=w['id'], buffer=None, error=None)
            return True
        return False

    def _widget(self, scr, wid):
        for page in scr['nav']['pages'].values():
            for w in _page_defs(page):
                if w.get('id') == wid:
                    return w
        raise PanelError(f'unknown widget {wid!r}')

    def _keypad_keys(self, scr, ov):
        w = self._widget(scr, ov['widget'])
        if ov['kind'] == 'choice':
            return [[o.get('text', _key(o['value']))] for o in w['options']] + [['Cancel']]
        return w.get('keys') or scr['nav'].get('keypad', {}).get('keys') or [['7', '8', '9', '<'], ['4', '5', '6', 'C'], ['1', '2', '3', 'OK'],
                                                                             ['-', '0', '.', 'ESC']]

    def _keypad_rect(self, scr):
        d = self.spec['displays'][scr['nav']['display']]
        return scr['nav'].get('keypad', {}).get('rect') or [d['size'][0] // 4, d['size'][1] // 6, d['size'][0] // 2, d['size'][1] * 2 // 3]

    def _keypad_hit(self, scr, ov, x, y):
        kx, ky, kw, kh = self._keypad_rect(scr)
        keys = self._keypad_keys(scr, ov)
        top = ky + 30
        rows = len(keys)
        if not (kx <= x < kx + kw and top <= y < ky + kh):
            return None
        r = min((y - top) * rows // max(1, ky + kh - top), rows - 1)
        row = keys[r]
        return row[min((x - kx) * len(row) // kw, len(row) - 1)]

    def _touch_overlay(self, scr, ov, ev):
        if ov['kind'] not in ('keypad', 'choice'):
            return self._touch_overlay2(scr, ov, ev)
        key = self._keypad_hit(scr, ov, ev['x'], ev['y'])
        if key is None:
            return False
        w = self._widget(scr, ov['widget'])
        if ov['kind'] == 'choice':
            self.ui['overlay'] = None
            for o in w['options']:
                if key == o.get('text', _key(o['value'])):
                    return self._write(w['write'], dict(ev=ev, value=o['value']))
            return True
        if key in ('ESC', 'Cancel'):
            self.ui['overlay'] = None
        elif key == '<':
            ov['buffer'], ov['error'] = ov['buffer'][:-1], None
        elif key == 'C':
            ov['buffer'], ov['error'] = '', None
        elif key == 'OK':
            if w['type'] == 'text':
                self.ui['overlay'] = None
                return self._write(w['write'], dict(ev=ev, value=ov['buffer']))
            try:
                v = float(ov['buffer'])
            except ValueError:
                ov['error'] = w.get('error_text', 'Invalid')
                return True
            if not (w.get('min', -math.inf) - 1e-12 <= v <= w.get('max', math.inf) + 1e-12):
                ov['error'] = w.get('range_text', 'Out of range')
                return True
            self.ui['overlay'] = None
            return self._write(w['write'], dict(ev=ev, value=v))
        elif len(ov['buffer']) < w.get('max_chars', 12):
            ov['buffer'], ov['error'] = ov['buffer'] + key, None
        return True

    # ---- frame
    def frame(self):
        """Structured screen at this instant: per display what is drawn (text, icons, lines, widgets; blink flags and what is shown now) and the
        UI state (screen, menu path, editor buffer, pending values, effects) for grading."""
        self._cache = None
        self._sync()
        now = self._now()
        ed = self.ui['editor']
        item, ref = (self._current_item() if self._screen().get('style') in MENU_STYLES else (None, None))
        out = dict(spec_version=SPEC_VERSION, time_s=now, screen=self.ui['screen'], powered=self.ui['powered'],
                   ui=dict(menu=None, editor=None, pending={k: dict(value=v['value'], until_s=v['until']) for k, v in self.ui['pending'].items()},
                           effects={k: dict(v) for k, v in self.ui['blinks'].items()}, vars=copy.deepcopy(self.ui['vars']), page=self.ui['page'],
                           overlay=copy.deepcopy(self.ui['overlay']), focus=self.ui['focus'], message=copy.deepcopy(self.ui['message']),
                           beeps=self.ui['beeps'], last_gesture=copy.deepcopy(self.ui['last']), unsupported=self.ui['unsupported'],
                           overlay_stack=copy.deepcopy(self.ui['overlays']), forms={k: copy.deepcopy(v['buffer']) for k, v in self.ui['forms'].items()}),
                   displays={})
        if item is not None:
            out['ui']['menu'] = dict(menu=ref[0], path=list(self.ui['path']), item=item['id'], label=self._item_label(item),
                                     index=self.ui['path'][-1], count=len(self._list_at(self.ui['path'])))
        if ed is not None:
            f = self._current_field(ed)
            out['ui']['editor'] = dict(ref=list(ed['ref']), field=f['id'], index=ed['index'], buffer=copy.deepcopy(ed['buffer']), typed=ed['typed'],
                                       digit=ed['digit'])
            if 'grid' in ed:                                                  # character-grid entry: the selected cell and sub-grid
                out['ui']['editor'].update(grid=list(ed['grid']), grid_name=ed.get('grid_name'))
        for did in self.spec['displays']:
            out['displays'][did] = self._resolve(did, now)
            if self.spec['displays'][did].get('brightness') is not None:   # screen brightness setting (percent)
                out['displays'][did]['brightness'] = self._src(self.spec['displays'][did]['brightness'], {})
            if self.spec['displays'][did].get('rotate_if') is not None:    # display rotation setting (upside-down mounting): drawn turned 180 deg
                out['displays'][did]['rotated'] = bool(self._cond(self.spec['displays'][did]['rotate_if'], {}))
            if self.spec['displays'][did].get('rotation') is not None:     # screen orientation setting 0 / 90 / 180 / 270 deg clockwise (touches mapped)
                out['displays'][did]['rotation'] = self._rotation(did)
            if self.spec['displays'][did].get('invert_if') is not None:    # inverted LCD setting: drawn with text and background colours swapped
                out['displays'][did]['inverted'] = bool(self._cond(self.spec['displays'][did]['invert_if'], {}))
            if self.spec['displays'][did].get('dark_if') is not None:      # dark mode setting: drawn with the display's 'colors_dark' palette
                out['displays'][did]['dark'] = bool(self._cond(self.spec['displays'][did]['dark_if'], {}))
        return json.loads(json.dumps(out, default=str))

    def _layout(self, did):
        """Merged layout of a display on the current screen: base layout, the extends chain, then the menu item's own display."""
        chain, name = [], self.ui['screen']
        while name and name not in chain:
            chain.append(name)
            name = self._screen(name).get('extends')
        lay = copy.deepcopy(self.spec.get('layout_base', {}).get(did, {}))
        for name in reversed(chain):
            _merge(lay, self._screen(name).get('layout', {}).get(did, {}))
        item, ref = (self._current_item() if self._screen().get('style') in MENU_STYLES else (None, None))
        if item is not None:
            _merge(lay, item.get('display', {}).get(did, {}))
            if item.get('edit_layout') and self._edits(self.ui['editor'], dict(item_ref=ref)) and self.ui['editor']['ref'][0] == 'item':
                _merge(lay, item['edit_layout'].get(did, {}))                  # the item's own entry screen while its editor is open
        return lay, item, ref

    def _resolve(self, did, now):
        d = self.spec['displays'][did]
        lay, item, ref = self._layout(did)
        ctx = dict(item=item, item_ref=ref)
        P = self._period()
        clock_on = (now % P) < P / 2.0 - 1e-9 or P <= 0
        kind = d['kind']
        if kind == 'led':
            on = bool(self._cond(lay.get('on', False), ctx))
            fx = self.ui['blinks'].get(did)
            shown = on != (fx is not None and ((now - fx['start']) % fx['period']) < fx['period'] / 2.0)
            blink = bool(self._cond(lay.get('blink', False), ctx)) and on
            p = lay.get('period_s', P)
            res = dict(kind='led', on=on, blink=blink or fx is not None, shown=shown and ((now % p) < p / 2.0 - 1e-9 or not blink))
            if 'color' in lay:                                                # multi-colour LEDs (status lights)
                res['color'] = self._src(lay['color'], ctx) if isinstance(lay['color'], dict) else lay['color']
            return res
        if kind in ('segment_lcd', 'seven_segment'):
            fields = {}
            for fid in d.get('fields', {}):
                chars = self._text_chars(lay.get('fields', {}).get(fid), ctx)
                text = ''.join(c for c, _ in chars)
                shown = text if clock_on else ''.join(c for c, _ in _blanked(chars))
                fields[fid] = dict(text=text, blink=any(b for _, b in chars), shown=shown)
                if 0 < sum(b for _, b in chars) < len(chars):
                    fields[fid]['blink_mask'] = ''.join('1' if b else '0' for _, b in chars)
            icons = {}
            for iid in d.get('icons', {}):
                spec_ = lay.get('icons', {}).get(iid, False)
                on, blink, attrs = self._icon_state(spec_, ctx)
                fx = self.ui['blinks'].get(iid)
                flash = fx is not None and ((now - fx['start']) % fx['period']) < fx['period'] / 2.0
                p = spec_.get('period_s', P) if isinstance(spec_, dict) else P
                shown = (on and ((now % p) < p / 2.0 - 1e-9 or not blink)) != flash
                icons[iid] = dict(on=on, blink=blink or fx is not None, shown=shown, **attrs)
            return dict(kind=kind, fields=fields, icons=icons)
        if kind == 'char_lcd' or (kind in PAGE_KINDS and not self._screen().get('style') == 'touch_gui'):
            lines = self._lines(d, lay, ctx, clock_on, did)
            out = dict(kind=kind, lines=[x['text'] for x in lines], shown=[x['shown'] for x in lines],
                       inverse=[x['inverse'] for x in lines], blink=[x['blink'] for x in lines])
            if any(x.get('spans') for x in lines):
                out['inverse_spans'] = [x.get('spans', []) for x in lines]
            if any(x.get('strike') for x in lines):
                out['strike_spans'] = [x.get('strike', []) for x in lines]
            fbs = self._fbar_items() if self.ui['editor'] is None and (self._screen().get('nav') or {}).get('display', did) == did else []
            if fbs:
                out['function_bar'] = dict(labels=[self._fbar_label(x) for x in fbs], selected=min(self.ui.get('fbar', 0), len(fbs) - 1))
            sk = self._softkey_labels(did)
            if sk is not None:
                out['softkeys'] = sk
            if kind in PAGE_KINDS:
                out['widgets'] = self._static_widgets(lay, ctx)
            return out
        return self._resolve_page(did, d, lay, ctx, clock_on)

    def _icon_state(self, s, ctx):
        if isinstance(s, (bool, type(None))):
            return bool(s), False, {}
        if 'on' not in s and 'blink' not in s and 'attrs' not in s:
            return bool(self._cond(s, ctx)), False, {}
        on = bool(self._cond(s.get('on', True), ctx))
        blink = on and bool(self._cond(s.get('blink', False), ctx))
        attrs = {k: self._src(v, ctx) for k, v in (s.get('attrs') or {}).items()}
        return on, blink, attrs

    def _text_chars(self, b, ctx):
        """A text binding resolved to [(char, blink)]: {text}, {src, fmt, map}, {cases}, {parts}; 'show' hides it, 'blink' flashes it
        ('blink_only': 'int' / 'frac' flashes only the digits before / after the point, any other string only those characters)."""
        if b is None:
            return []
        if isinstance(b, str):
            return [(c, False) for c in self._tr(b)]
        if 'cases' in b:
            for case in b['cases']:
                if self._cond(case.get('when'), ctx):
                    return self._text_chars({k: v for k, v in case.items() if k != 'when'}, ctx)
            return []
        if not self._cond(b.get('show'), ctx):
            return []
        if 'parts' in b:
            chars = [c for p in b['parts'] for c in self._text_chars(p, ctx)]
        elif 'text' in b:
            chars = [(c, False) for c in self._tr(b['text'])]
        else:
            v = self._src(b['src'], ctx)
            fmt = {k: (self._src(x, ctx) if isinstance(x, dict) else x) for k, x in (b.get('fmt') or {}).items()}   # patterns may follow settings
            text = '' if v is None else (self._tr(b['map'].get(_key(v), _key(v))) if 'map' in b else _format(v, fmt))
            if b.get('translate'):
                text = self._tr(text)
            if 'clip' in b:                                                  # at most n characters (a field of a fixed width)
                text = text[:b['clip']]
            if 'pad' in b:
                text = text.rjust(b['pad']) if b.get('align', 'right') == 'right' else text.ljust(b['pad'])
            chars = [(c, False) for c in text]
        if b.get('blink') is not None and self._cond(b['blink'], ctx):
            only = b.get('blink_only')                                           # 'int' / 'frac': the part before / after the point; else characters
            point = next((i for i, (c, _) in enumerate(chars) if c == '.'), len(chars))
            pick = {'int': lambda i, c: i < point, 'frac': lambda i, c: i > point}.get(only, lambda i, c: only is None or c in only)
            chars = [(c, True if pick(i, c) else bl) for i, (c, bl) in enumerate(chars)]
        return chars

    def _lines(self, d, lay, ctx, clock_on, did=None):
        """Text lines of a character / graphic display: the layout's lines, or (menu screens without explicit lines) the menu drawn by the
        engine: title, the item list around the cursor ('>' or an inverse bar, display option menu_cursor), the edited value flashing.  The
        softkey labels of a softkey screen take the last line; a transient message replaces the line above them."""
        rows, cols = self._rows_cols(d)
        sk = self._softkey_labels(did) if did else None
        krows = self._softkey_rows(d, cols) if sk is not None and d.get('softkey_rows') else None   # labels in rows above / below the text
        body = rows - (len(krows[0]) + len(krows[1]) if krows else (1 if sk is not None else 0))
        nav = self._screen().get('nav') or {}
        fb = self._fbar_line(cols) if (self.ui['editor'] is None and nav.get('function_bar') is not None and nav.get('display', did) == did) else None
        foot = nav.get('footer') if (self.ui['editor'] is None or d.get('edit_inline')) and lay.get('lines') is None else None   # a fixed bottom line
        #                                                                     of the menu display (e.g. 'Line 1 of 4'; not on displays with their own lines;
        #                                                                     with edit_inline the page and its footer stay while a value is edited)
        body -= (1 if fb is not None else 0) + (1 if foot is not None else 0)   # text dialogs: the function bar takes the bottom line
        ged = self.ui['editor']
        if ged is not None and self._current_field(ged).get('entry') == 'grid':
            specs = self._grid_lines(body, cols, self._current_field(ged))   # the character-grid entry screen
        elif lay.get('lines') is not None:
            specs = list(lay['lines'])
        elif self._screen().get('style') in MENU_STYLES and self.ui['path'] and d.get('param_list'):
            specs = self._param_lines(body, cols, d['param_list'], d)
        elif self._screen().get('style') in MENU_STYLES and self.ui['path']:
            specs = self._menu_lines(body, cols, d.get('menu_cursor', 'marker'), dict(d, menu_title=False) if nav.get('title') is False else d)
        elif self._screen().get('style') == 'parameter_cycle' and self.ui['editor']:
            specs = [{'src': {'current': 'label'}}, {'src': {'current': 'text'}, 'blink': True}]
        else:
            specs = []
        if nav.get('title') not in (None, False) and (ged is None or d.get('edit_inline')) and specs and self._screen().get('style') in MENU_STYLES \
                and lay.get('lines') is None and d.get('menu_title', True):
            specs[0] = nav['title']                                          # text dialogs: title line binding (e.g. with the status field)
        specs = (specs + [None] * rows)[:body]
        if self.ui['message'] is not None and lay.get('message_line', True) and body > 0:
            specs[body - 1] = {'text': self.ui['message']['text'], 'inverse': True}
        if foot is not None:
            specs.append(foot)
        if fb is not None:
            specs.append(fb)
        if krows:
            specs = [{'text': t} for t in krows[0]] + specs + [{'text': t} for t in krows[1]]
        elif sk is not None:
            w = max(1, cols // max(1, len(sk)))
            specs.append({'text': ''.join(lbl[:w - 1].center(w) for lbl in sk)[:cols], 'inverse': True})
        out = []
        for b in specs:
            chars = self._text_chars(b, ctx)[:cols]
            text = ''.join(c for c, _ in chars)
            shown = ''.join(c if (not bl or clock_on) else ' ' for c, bl in chars)
            inv = b.get('inverse') if isinstance(b, dict) else False
            line = dict(text=text, shown=shown, blink=any(bl for _, bl in chars),
                        inverse=bool(self._cond(inv, ctx)) if isinstance(inv, dict) else bool(inv))   # a line may be inverted by a condition
            spans = self._inverse_spans(b, ctx, len(text)) if isinstance(b, dict) else []
            if spans:
                line['spans'] = spans                                         # inverted parts of the line (selection in a function bar)
            strikes = self._inverse_spans(b, ctx, len(text), key='strike') if isinstance(b, dict) else []
            if strikes:
                line['strike'] = strikes                                      # crossed-out parts (e.g. a changed, unsaved method name)
            out.append(line)
        return out

    def _softkey_labels(self, did):
        """Labels of the softkeys on this display (softkey_menu screens; a softkey whose 'when' fails shows an empty label)."""
        scr = self._screen()
        nav = scr.get('nav') or {}
        if scr.get('style') != 'softkey_menu' or not nav.get('softkeys') or nav.get('display', did) != did:
            return None
        return [''.join(c for c, _ in self._text_chars(sk.get('label', ''), {})) if self._cond(sk.get('when'), {}) else '' for sk in nav['softkeys']]

    def _softkey_rows(self, d, cols):
        """Softkey labels in rows above and below the text (display option softkey_rows {'top': [input ids], 'bottom': [input ids]}, e.g. three
        keys over and three under a graphic LCD): per key the label of its first softkey entry whose 'when' holds, in equal columns (left,
        centred, right); a label with a newline takes two lines.  Returns (top lines, bottom lines)."""
        nav = self._screen().get('nav') or {}

        def label(inp):
            for sk in nav.get('softkeys', []):
                if sk['input'] == inp and self._cond(sk.get('when'), {}):
                    return ''.join(c for c, _ in self._text_chars(sk.get('label', ''), {}))
            return ''
        out = []
        for pos in ('top', 'bottom'):
            ids = d['softkey_rows'].get(pos, [])
            labels = [label(i).split('\n') for i in ids]
            lines = []
            for r in range(max([len(x) for x in labels] + [1]) if ids else 0):
                buf = [' '] * cols
                for j, parts in enumerate(labels):
                    a, b = j * cols // len(ids), (j + 1) * cols // len(ids)
                    text = (parts[r] if r < len(parts) else '')[:b - a]
                    buf[a:b] = text.ljust(b - a) if j == 0 else text.rjust(b - a) if j == len(ids) - 1 else text.center(b - a)
                lines.append(''.join(buf).rstrip())
            out.append(lines)
        return out[0], out[1]

    def _item_label(self, it, ctx=None):
        """Label of a menu item: static text (in the current language of the spec i18n tables), or a text binding (e.g. a user-defined name
        kept by the runtime)."""
        lab = it.get('label', it.get('id', ''))
        return ''.join(c for c, _ in self._text_chars(lab, dict(ctx or {}, item=it))) if isinstance(lab, dict) else self._tr(lab)

    def _rows_cols(self, d):
        if d['kind'] == 'char_lcd':
            return d['rows'], d['cols']
        s = d.get('text_scale', 2)
        return d.get('rows') or max(1, (d['size'][1] - 4) // (9 * s)), d.get('cols') or max(1, (d['size'][0] - 4) // (6 * s))

    def _menu_lines(self, rows, cols, cursor='marker', d=None):
        """Display options (d): menu_title False (lists without a title line), menu_values 'inline' ('Label: value' instead of a right-aligned
        value), edit_inline (a single value is edited on its own line of the page instead of the editor view), menu_marker (the cursor character,
        default '>', e.g. an arrow).  Item options: a text binding as label, 'value' (a setting shown on a submenu / action line), 'mark' (right-aligned, e.g. '>'),
        edit_style 'list' (an enum edited as its list of options with the cursor on the chosen one)."""
        d = d or {}
        ed = self.ui['editor']
        item, ref = self._current_item()
        inline = bool(ed is not None and d.get('edit_inline') and item is not None and len(self._item_fields(item)) == 1
                      and self._current_field(ed)['id'] == item['id'])   # display option edit_inline: the value is edited on its own line
        titled = d.get('menu_title', True)
        title = ' > '.join(self._item_label(x) for x in self._path_items()[:-1]) or self._tr(self._menu().get('label', ''))
        if d.get('menu_titles') == 'own':                                     # each list shows its own title (e.g. 'Valve Menu' under 'Valve Setup')
            parents = self._path_items()[:-1]
            title = self._tr(parents[-1].get('title') or self._item_label(parents[-1])) if parents else self._tr(self._menu().get('label', ''))
        if ed is not None and 'list' in (item.get('edit_style'), self._current_field(ed).get('edit_style')) \
                and self._current_field(ed)['type'] in ('enum', 'bool'):
            f = self._current_field(ed)
            opts = self._opts(f, dict(buffer=ed['buffer'])) if 'options_src' in f else self._options(f)   # options_src: a runtime list
            cur = next((i for i, o in enumerate(opts) if o['value'] == ed['buffer'][f['id']]), 0)
            n = max(1, rows - (1 if titled else 0))
            top = min(max(0, cur - n + 1), max(0, len(opts) - n))
            lines = [{'text': self._item_label(f if f.get('edit_style') == 'list' else item)[:cols]}] if titled else []
            for i in range(top, min(len(opts), top + n)):
                mark = (d.get('menu_marker', '>') if i == cur else ' ') if cursor == 'marker' else ''
                lines.append({'text': (mark + self._tr(opts[i].get('text', _key(opts[i]['value']))))[:cols], 'inverse': i == cur and cursor == 'inverse'})
            return lines
        if ed is not None and not inline:
            lines = [{'text': (self._tr(item['title']) if d.get('menu_titles') == 'own' and item.get('title') else self._item_label(item))[:cols]}]
            cur_id = self._current_field(ed)['id']
            for f in self._item_fields(item):
                if not self._visible(f, ed['buffer']):
                    continue
                cur = f['id'] == cur_id
                val = (ed['typed'] + '_' if cur and ed['typed'] is not None else self._field_text(f, ed['buffer'][f['id']]))
                label = f.get('label', '') if f is not item else ''
                parts = [{'text': (label + ' ' if label else '')}]
                if cur and ed['typed'] is None and (f.get('entry') == 'digits' or (f['type'] == 'text' and f.get('charset'))):
                    at = _digit_at(val, f, ed['digit']) if f.get('entry') == 'digits' else ed['digit']
                    parts += [{'text': c, 'blink': j == at} for j, c in enumerate(val)]   # only the digit under the cursor flashes
                else:
                    parts.append({'text': val, 'blink': cur and ed['typed'] is None})
                if f.get('unit'):
                    parts.append({'text': ' ' + f['unit']})
                lines.append({'parts': parts})
            return lines
        items = self._list_at(self.ui['path'])
        vis = self._visible_indices(items)
        pos = vis.index(self.ui['path'][-1]) if self.ui['path'][-1] in vis else 0
        n = max(1, rows - (1 if titled else 0))
        top = min(max(0, pos - n + 1), max(0, len(vis) - n))
        lines = [{'text': title[:cols]}] if titled else []
        for i in vis[top:top + n]:
            it = items[i]
            here = i == self.ui['path'][-1]
            mark = (d.get('menu_marker', '>') if here else ' ') if cursor == 'marker' else ''
            val = ''
            if it.get('value') is not None:                                   # the line's own value text (any item type)
                val = ''.join(c for c, _ in self._text_chars(it['value'], dict(item=it)))
            elif it['type'] in ('bool', 'enum', 'number', 'text'):
                v0 = self._field_initial(it, {})
                val = self._field_text(it, v0) + (' ' + it['unit'] if it.get('unit') and v0 is not None else '')   # 'off' without a unit
            label = self._item_label(it)
            tail = it.get('mark', '')
            blink = ()
            if here and inline:                                               # the edited value (or only its digit under the cursor) flashes
                f = self._current_field(ed)
                etext = ed['typed'] + '_' if ed['typed'] is not None else self._field_text(f, ed['buffer'][f['id']])
                if ed['typed'] is None and (f.get('entry') == 'digits' or (f['type'] == 'text' and f.get('charset'))):
                    blink = (_digit_at(etext, f, ed['digit']) if f.get('entry') == 'digits' else ed['digit'],)
                else:
                    blink = tuple(range(len(etext)))
                val = etext + (' ' + f['unit'] if f.get('unit') else '')
            if d.get('menu_values') == 'inline':
                text = mark + label + (' ' + val if val else '')
            else:
                room = cols - len(mark) - len(val) - len(tail)
                text = mark + (label[:max(0, room - 1)].ljust(max(0, room)) + val if val else label[:cols - len(mark)])
            off = len(text) - len(val)
            text = text[:cols - len(tail)].ljust(cols - len(tail)) + tail if tail else text[:cols]
            if blink:
                lines.append({'parts': [{'text': c, 'blink': j - off in blink} for j, c in enumerate(text)], 'inverse': here and cursor == 'inverse'})
                continue
            lines.append({'text': text, 'inverse': here and cursor == 'inverse'})
        return lines

    def _param_lines(self, rows, cols, pl, d=None):
        """Parameter-list display (display option param_list, e.g. a GC keypad display with Actual / Setpoint columns): a title line (the
        menu's or submenu's 'title' text binding, else its label; title_align 'center' / 'left'), then one line per visible item around the
        cursor: the label at the left, the item's 'actual' text binding right-aligned to column actual_end, the setpoint ('value_text' binding,
        else the item's value) right-aligned to value_end, and the cursor mark after the active line's setpoint.  While a value is typed (or
        opened with Enter) that line shows the entry and a flashing edit_mark instead of the cursor; an enum / bool opened for editing is shown
        as its list of options with the cursor after the chosen one.  pl: {actual_end, value_end, cursor, edit_mark, title_align, radix}
        (radix: a source; ',' shows numbers as 1,00).  Items with several fields fall back to the standard menu drawing while edited."""
        ed = self.ui['editor']
        item, ref = self._current_item()
        a_end, v_end = int(pl.get('actual_end', cols - 12)), int(pl.get('value_end', cols - 3))
        cur_c, ed_c = pl.get('cursor', '<'), pl.get('edit_mark', '*')
        comma = pl.get('radix') is not None and self._src(pl['radix'], {}) == ','
        if ed is not None and len(self._item_fields(item)) > 1:
            return self._menu_lines(rows, cols, 'marker', d)
        node = (self._path_items()[:-1] or [None])[-1]
        owner = node if node is not None else self._menu()

        def chars(b, it=None):
            out = self._text_chars(b, dict(item=it, item_ref=None)) if b is not None else []
            return [(',' if comma and c == '.' else c, bl) for c, bl in out]

        def title_line(text):
            text = text[:cols]
            pad = (cols - len(text)) // 2 if pl.get('title_align', 'center') == 'center' else 0
            return {'text': ' ' * pad + text}

        def compose(label, actual, value, mark, mark_blink=False):
            cells = [(' ', False)] * cols
            starts = []
            for seg, end in ((actual, a_end), (value, v_end)):
                if seg:
                    s = end - len(seg) + 1
                    starts.append(s)
                    for i, cb in enumerate(seg):
                        if 0 <= s + i < cols:
                            cells[s + i] = cb
            room = (min(starts) - 1) if starts else (v_end + 1 if mark else cols)
            for i, cb in enumerate(label[:max(0, room)]):
                cells[i] = cb
            if mark and v_end + 1 < cols:
                cells[v_end + 1] = (mark, mark_blink)
            return {'parts': [{'text': c, 'blink': True} if b else {'text': c} for c, b in cells]}

        tb = owner.get('title')
        title = ''.join(c for c, _ in chars(tb)) if tb is not None else (self._item_label(owner) if node is not None else owner.get('label', ''))
        n = max(1, rows - 1)
        if ed is not None and self._current_field(ed)['type'] in ('enum', 'bool'):         # the option list ([Mode/Type])
            f = self._current_field(ed)
            opts = self._opts(f)
            cur = next((i for i, o in enumerate(opts) if o['value'] == ed['buffer'][f['id']]), 0)
            top = min(max(0, cur - n + 1), max(0, len(opts) - n))
            lines = [title_line(self._item_label(item))]
            for i in range(top, min(len(opts), top + n)):
                text = self._tr(opts[i].get('text', _key(opts[i]['value'])))[:cols - 2]
                lines.append({'text': text + (' ' + cur_c if i == cur else '')})
            return lines
        items = self._list_at(self.ui['path'])
        vis = self._visible_indices(items)
        pos = vis.index(self.ui['path'][-1]) if self.ui['path'][-1] in vis else 0
        top = min(max(0, pos - n + 1), max(0, len(vis) - n))
        lines = [title_line(title)]
        for i in vis[top:top + n]:
            it = items[i]
            here = i == self.ui['path'][-1]
            label = [(c, False) for c in self._item_label(it)]
            actual = chars(it.get('actual'), it)
            if here and ed is not None:
                f = self._current_field(ed)
                text = ed['typed'] if ed['typed'] is not None else self._field_text(f, ed['buffer'][f['id']])
                lines.append(compose(label, actual, [(',' if comma and c == '.' else c, False) for c in text], ed_c, True))
                continue
            if it.get('value_text') is not None:
                value = chars(it['value_text'], it)
            elif it['type'] in ('bool', 'enum', 'number', 'text'):
                value = [(',' if comma and c == '.' else c, False) for c in self._field_text(it, self._field_initial(it, {}))]
            elif it.get('value') is not None:
                value = chars(it['value'], it)
            else:
                value = []
            lines.append(compose(label, actual, value, cur_c if here else ''))
        return lines

    def _path_items(self):
        items, out = self._menu()['items'], []
        for i in self.ui['path']:
            out.append(items[i])
            items = items[i].get('items', [])
        return out

    def _static_widgets(self, lay, ctx):
        out = []
        for w in lay.get('widgets', []):
            if self._cond(w.get('visible_if'), ctx):
                ent = dict(id=w['id'], type=w.get('type', 'label'), rect=list(w['rect']), text=''.join(c for c, _ in self._text_chars(w.get('text'), ctx)))
                for k in ('scale', 'align', 'valign', 'style', 'fill', 'color', 'wrap'):   # drawing options (e.g. a large main readout)
                    if k in w:
                        ent[k] = self._src(w[k], ctx) if isinstance(w[k], dict) else w[k]
                if ent['type'] == 'plot':                                     # a curve on a graphic LCD (e.g. a titration curve)
                    ent.update(self._plot_entry(w, ctx))
                if ent['type'] == 'gauge':                                    # a bar graph on a graphic LCD (e.g. a leak-rate bar with trigger marks)
                    v = self._src(w.get('value'), ctx)
                    ent['fraction'] = None if v is None else min(max(float(v), 0.0), 1.0)
                    ent['marks'] = [min(max(float(m), 0.0), 1.0) for m in (self._src(x, ctx) for x in w.get('marks', [])) if m is not None]
                out.append(ent)
        return out

    def _resolve_page(self, did, d, lay, ctx, clock_on):
        scr = self._screen()
        nav = scr.get('nav') or {}
        page = nav.get('pages', {}).get(self.ui['page'], {})
        widgets = []
        for w in self._page_widgets(page) if nav.get('display') == did else []:
            t = w['type']
            text = ''.join(c for c, _ in self._text_chars(w.get('text', w.get('label')), dict(ctx, ev={})))
            ent = dict(id=w['id'], type=t, rect=list(w['rect']), text=text, enabled=bool(self._cond(w.get('enabled'), ctx)),
                       focus=self.ui['focus'] == w['id'])
            if t == 'slider':
                ent['value'] = self._src(w['read'], ctx)
                ent['fraction'] = None if ent['value'] is None else min(max((ent['value'] - w['min']) / (w['max'] - w['min']), 0.0), 1.0)
            if t in ('toggle', 'number', 'text', 'choice') and w.get('read'):
                v = self._src(w['read'], ctx)
                ent['value'] = v
                if t == 'choice':
                    ent['value_text'] = next((o.get('text', _key(o['value'])) for o in w['options'] if o['value'] == v), _key(v))
                elif t != 'toggle':
                    ent['value_text'] = '' if v is None else _format(v, w.get('fmt') or {})
            if t == 'list' and 'items' not in w:
                rows = [r for r in w.get('rows', []) if self._cond(r.get('visible_if'), ctx)]
                off = self.ui['scroll'].get(w['id'], 0)
                n = max(1, w['rect'][3] // w.get('row_h', 40))
                ent['rows'] = [''.join(c for c, _ in self._text_chars(r.get('text', r.get('label')), ctx)) for r in rows[off:off + n]]
                ent['scroll'] = off
            self._resolve_touch2(w, ent, ctx)
            widgets.append(ent)
        widgets += self._static_widgets(lay, ctx)
        lines = self._lines(d, lay, ctx, clock_on) if lay.get('lines') else []
        own = nav.get('display') == did                                       # the page (title, header bar) belongs to the touch display only
        out = dict(kind=d['kind'], page=self.ui['page'], title=''.join(c for c, _ in self._text_chars(page.get('title'), ctx)) if own else '', widgets=widgets,
                   lines=[x['text'] for x in lines])
        if any(x['inverse'] for x in lines):                                  # inverse lines on the other displays of a touch screen (e.g. lit OLED key legends)
            out['inverse'] = [x['inverse'] for x in lines]
        if own and any(k in page for k in TEMPLATE_PARTS) and (page.get('title') is not None or page.get('header')):
            out['header_h'] = self._touch_layout()['header_h']
        ov = self.ui['overlay']
        if ov is not None and nav.get('display') == did and ov['kind'] not in ('keypad', 'choice'):
            out['overlays'] = [self._ov_layout(o) for o in self.ui['overlays'] + [ov]]
            out['overlay'] = out['overlays'][-1]
        elif ov is not None and nav.get('display') == did:
            w = self._widget(scr, ov['widget'])
            out['overlay'] = dict(kind=ov['kind'], widget=ov['widget'], title=''.join(c for c, _ in self._text_chars(w.get('label', ''), ctx)),
                                  buffer=ov['buffer'], error=ov['error'], rect=self._keypad_rect(scr), keys=self._keypad_keys(scr, ov))
        return out

    # ---- rendering
    def render(self, display_id):
        """RGB uint8 array (height, width, 3) of the display at this instant."""
        import numpy as np
        return np.asarray(self.render_image(display_id), dtype=np.uint8)

    def render_image(self, display_id):
        if display_id not in self.spec['displays']:
            raise PanelError(f'unknown display {display_id!r}')
        res = self.frame()['displays'][display_id]
        d = self.spec['displays'][display_id]
        if res.get('dark') and d.get('colors_dark'):                          # dark_if: the dark palette replaces the colour roles it lists
            d = dict(d, colors=dict(d.get('colors', {}), **d['colors_dark']))
        if res.get('inverted') and 'background' in d.get('colors', {}) and 'text' in d.get('colors', {}):   # invert_if: swap the two colours
            fg, bg = d['colors']['text'], d['colors']['background']                       # (and every role drawn in one of them, e.g. a mono accent)
            d = dict(d, colors={k: bg if v == fg else fg if v == bg else v for k, v in d['colors'].items()})
        img = draw_display(d, res)
        if res.get('rotation'):                                               # display 'rotation': the frame turned clockwise (90 / 270: landscape)
            img = img.rotate(-res['rotation'], expand=True)
        return _dim(img.rotate(180) if res.get('rotated') else img, res.get('brightness'))

    def _rotation(self, display_id):
        """Clockwise turn of a display by its 'rotation' source (0 / 90 / 180 / 270; anything else 0)."""
        src = self.spec['displays'][display_id].get('rotation')
        if src is None:
            return 0
        v = self._src(src, {}) if isinstance(src, dict) else src
        try:
            v = int(round(float(v))) % 360
        except (TypeError, ValueError):
            return 0
        return v if v in (90, 180, 270) else 0

    def png_bytes(self, display_id):
        """PNG encoding of the display at this instant: (bytes, width, height)."""
        import io
        img = self.render_image(display_id)
        buf = io.BytesIO()
        img.save(buf, format='PNG', optimize=False)
        return buf.getvalue(), img.size[0], img.size[1]

    def save_png(self, display_id, path):
        """Render the display to a PNG file; returns dict(path, format, width, height, sha256)."""
        data, w, h = self.png_bytes(display_id)
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return dict(path=str(path), format='png', width=w, height=h, sha256=hashlib.sha256(data).hexdigest())


    # ---- touch toolkit (touch_gui screens): page layout conventions, forms, dynamic lists, keyboards, pickers, dialogs, languages
    def _tr(self, text):
        """Static display text in the instrument's current language (spec i18n tables; text without an entry is shown as written)."""
        i18n = self.spec.get('i18n')
        if not i18n or not isinstance(text, str) or not text:
            return text
        return i18n.get('tables', {}).get(self._src(i18n['language'], {}), {}).get(text, text)

    def _opts(self, f, ctx=None, filtered=True):
        """Options of an enum / bool field: static 'options' or 'options_src' (a runtime list: {items, value, text, none}), each option shown
        only while its 'visible_if' holds (filtered)."""
        if 'options_src' in f:
            o = f['options_src']
            items = [it for it in (self._src(o['items'], ctx or {}) or []) if self._cond(o.get('where'), dict(ctx or {}, row=it))]
            opts = ([dict(value=None, text=o['none'])] if o.get('none') else []) + [
                dict(value=_dig(it, o.get('value', 'id')), text=str(_dig(it, o.get('text', 'name')))) for it in items]
        else:
            opts = self._options(f)
        return [x for x in opts if not filtered or self._cond(x.get('visible_if'), ctx or {})]

    def _label(self, f, ctx):
        """Label of a form field: static text (in the current language) or a text binding (e.g. an ID description set in a method)."""
        lab = f.get('label', f['id'])
        return ''.join(c for c, _ in self._text_chars(lab, ctx)) if isinstance(lab, dict) else self._tr(lab)

    def _select_keyboard(self, name):
        """Keyboard layout setting: spec keyboard_select {name: source} picks the layout used for that keyboard (e.g. QWERTZ)."""
        sel = self.spec.get('keyboard_select', {}).get(name)
        if sel is None:
            return name
        chosen = self._src(sel, {})
        return chosen if chosen in self.spec.get('keyboards', {}) or chosen in KEYBOARDS else name

    def _touch_layout(self):
        lay = dict(TOUCH_LAYOUT, **self.spec.get('touch_layout', {}))
        lay.update((self._screen().get('nav') or {}).get('layout', {}))
        return lay

    def _touch_size(self):
        return self.spec['displays'][self._screen()['nav']['display']]['size']

    def _layout_page(self, page, ctx=None):
        """Visible widgets of a page with their rects: the absolute 'widgets' as written, then the templated parts that touch screens share
        (layout conventions, sizes from touch_layout): 'header' buttons right-aligned in the title bar, 'sidebar' buttons stacked in a left
        column, 'content' stacked top to bottom in the remaining area ({'row': [widgets]} side by side by 'w' weights; 'h' in px, 'flex' takes
        the rest), 'actions' spread over the bottom action bar by 'w' weights.  Hidden widgets leave no gap."""
        ctx = ctx or {}
        vis = lambda w: self._cond(w.get('visible_if'), ctx)                      # noqa: E731
        out = [w for w in page.get('widgets', []) if vis(w)]
        if not any(k in page for k in TEMPLATE_PARTS):
            return out
        W, H = self._touch_size()
        L = self._touch_layout()
        m, g = L['margin'], L['gap']
        top = L['header_h'] if (page.get('title') is not None or page.get('header')) else 0
        bottom = H - (L['action_h'] if 'actions' in page else 0)
        x = W - m
        for w in reversed([w for w in page.get('header', []) if vis(w)]):
            ww = w.get('w', 110)
            x -= ww
            out.append(dict(w, rect=[x, 4, ww, max(8, top - 8)]))
            x -= g
        left = m
        if 'sidebar' in page:
            sw = page.get('sidebar_w', L['sidebar_w'])
            y = top + m
            for w in [w for w in page['sidebar'] if vis(w)]:
                hh = w.get('h', L['row_h'])
                out.append(dict(w, rect=[m, y, sw - m, hh]))
                y += hh + g
            left = sw + m
        cw = W - left - m
        entries = [e for e in page.get('content', []) if vis(e)]
        fixed = sum(e.get('h', L['row_h']) for e in entries if e.get('h') != 'flex') + g * max(0, len(entries) - 1)
        flex_h = max(L['row_h'], bottom - top - 2 * m - fixed)
        y = top + m
        for e in entries:
            hh = flex_h if e.get('h') == 'flex' else e.get('h', L['row_h'])
            if isinstance(e.get('row'), list):
                cells = [w for w in e['row'] if vis(w)]
                tot = float(sum(w.get('w', 1) for w in cells)) or 1.0
                x = left
                for w in cells:
                    ww = int(round((cw - g * (len(cells) - 1)) * w.get('w', 1) / tot))
                    out.append(dict(w, rect=[x, y, ww, hh]))
                    x += ww + g
            else:
                out.append(dict(e, rect=[left, y, cw, hh]))
            y += hh + g
        acts = [w for w in page.get('actions', []) if vis(w)]
        tot = float(sum(w.get('w', 1) for w in acts)) or 1.0
        x = 0.0
        for w in acts:
            ww = W * w.get('w', 1) / tot
            out.append(dict(w, rect=[int(round(x)), bottom, int(round(x + ww)) - int(round(x)), H - bottom]))
            x += ww
        return out

    def _touch_home(self, nav):
        """Hardware Home key of a touch screen: back to the start page from any level (open keyboards, dialogs and forms are discarded)."""
        self.ui.update(page=nav['start'], history=[], overlay=None, overlays=[], forms={}, form_order=[], focus=None)
        return True

    def _touch_back(self):
        if self.ui['overlay'] is not None:
            return self._pop_overlay()
        return self._act(dict(do='page_back'), {})

    # forms: a buffer of field values edited row by row (settings pages, wizards, entity editors) and written on Save
    def _form(self, fid):
        try:
            return self.spec['forms'][fid]
        except KeyError:
            raise PanelError(f'unknown form {fid!r}') from None

    def _form_field(self, fid, field_id):
        for f in self._form(fid)['fields']:
            if f['id'] == field_id:
                return f
        raise PanelError(f'unknown field {field_id!r} of form {fid!r}')

    def _form_initial(self, f, ctx):
        if 'initial' in f:
            return copy.deepcopy(f['initial'])
        v = self._src(f['read'], ctx) if f.get('read') else None
        t = f.get('type')
        if t in ('enum', 'bool'):
            opts = [o['value'] for o in self._opts(f, ctx)]
            return v if v in opts or f.get('nullable') or not opts else opts[0]
        if t == 'multi':
            return list(v) if isinstance(v, (list, tuple)) else []
        if t in ('text', 'password', 'date', 'time'):
            return '' if v is None else v
        return copy.deepcopy(v)

    def _form_buffer(self, fid, ctx=None):
        """The open form's buffer, else a live view of the field reads (a form used as a list of settings that apply at once)."""
        e = self.ui['forms'].get(fid)
        if e is not None:
            return e['buffer']
        buf = {}
        c = dict(ctx or {}, buffer=buf)
        for f in self._form(fid)['fields']:
            if f.get('type') not in ('link', 'action'):
                buf[f['id']] = self._form_initial(f, c)
        return buf

    def _form_open(self, a, ctx):
        fid = a['form']
        form = self._form(fid)
        buf = {k: self._src(s, ctx) for k, s in (a.get('set') or {}).items()}
        c = dict(ctx, buffer=buf)
        for f in form['fields']:
            if f.get('type') not in ('link', 'action') and f['id'] not in buf:
                buf[f['id']] = self._form_initial(f, c)
        self.ui['forms'][fid] = dict(buffer=buf)
        self.ui['form_order'] = [x for x in self.ui['form_order'] if x != fid] + [fid]
        self._run(form.get('on_open', []), c)
        if a.get('page'):
            self._act(dict(do='page', page=a['page']), ctx)
        return True

    def _form_close(self, fid):
        self.ui['forms'].pop(fid, None)
        self.ui['form_order'] = [x for x in self.ui['form_order'] if x != fid]

    def _form_id(self, a):
        fid = a.get('form') or (self.ui['form_order'][-1] if self.ui['form_order'] else None)
        if fid is None:
            raise PanelError('no open form')
        return fid

    def _form_save(self, a, ctx):
        """Save: required fields must be filled (else the form's 'invalid' indication), then the form's write; on success the form closes
        and its on_save actions run, a refusal keeps it open (the runtime's indication from the errors table)."""
        fid = self._form_id(a)
        if fid not in self.ui['forms']:
            return False
        form = self._form(fid)
        buf = self.ui['forms'][fid]['buffer']
        fctx = dict(ctx, buffer=buf)
        for f in form['fields']:
            if f.get('required') and self._cond(f.get('visible_if'), fctx) and buf.get(f['id']) in (None, '', []):
                self._accepted = False
                self._run(form.get('invalid', []), dict(fctx, field=f['id']))
                return False
        if not self._write(form.get('write'), fctx):
            return False
        self._form_close(fid)
        return self._run(form.get('on_save', []), fctx)

    def _form_cancel(self, a, ctx):
        fid = self._form_id(a)
        if fid not in self.ui['forms']:
            return False
        buf = self.ui['forms'][fid]['buffer']
        self._form_close(fid)
        return self._run(self._form(fid).get('on_cancel', []), dict(ctx, buffer=buf))

    def _form_set(self, a, ctx):
        fid = self._form_id(a)
        return self._commit(dict(form=fid, field=a['field']), self._src(a['value'], ctx), ctx.get('ev') or {})

    def _form_rows(self, w, ctx):
        """Visible fields of a form widget (its 'fields' subset in that order, else all), each with its context."""
        fid = w['form']
        form = self._form(fid)
        fctx = dict(ctx, buffer=self._form_buffer(fid, ctx), form=fid)
        defs = {f['id']: f for f in form['fields']}
        fields = [defs[i] for i in w['fields']] if w.get('fields') else form['fields']
        return [f for f in fields if not f.get('hidden') and self._cond(f.get('visible_if'), fctx)], fctx

    def _form_value_text(self, f, v, ctx):
        t = f.get('type')
        if f.get('value_text') is not None:
            return ''.join(c for c, _ in self._text_chars(f['value_text'], dict(ctx, value=v)))
        if t == 'link':
            return '>'
        if t == 'action':
            return ''
        if t == 'password':
            return '*' * len(v or '')
        if t == 'multi':
            if f.get('ordered'):                                              # ordered check lists: in the selected order
                texts = {o['value']: self._tr(o.get('text', _key(o['value']))) for o in self._opts(f, ctx, filtered=False)}
                return ', '.join(texts[x] for x in (v or []) if x in texts)
            return ', '.join(self._tr(o.get('text', _key(o['value']))) for o in self._opts(f, ctx, filtered=False) if o['value'] in (v or []))
        if t in ('enum', 'bool'):                                             # an option may stand for 'not set' (value None)
            for o in self._opts(f, ctx, filtered=False):
                if o['value'] == v:
                    return self._tr(o.get('text', _key(v)))
        if v is None or v == '':
            return self._tr(f.get('placeholder', ''))
        if t in ('date', 'time'):
            fmt = {k: self._src(p, ctx) for k, p in (f.get('fmt') or {}).items()}
            return _format(v, fmt) if fmt else str(v)
        if t == 'number' and f.get('fmt'):
            txt = _format(v, f['fmt'])
        else:
            txt = self._field_text(f, v)
        return txt + (' ' + f['unit'] if f.get('unit') and t == 'number' else '')

    def _open_field(self, fid, f, ctx):
        """Tap on a form row: links and actions run their 'do'; unsupported rows show the device's own response; values open their editor
        (choice list, keyboard, date / time picker, check list) on top of the screen."""
        t = f.get('type')
        if not self._cond(f.get('enabled_if'), ctx):
            return False
        if f.get('guard') and self._cond(f['guard'].get('when'), ctx):      # as for widgets: a lock shows the device's warning instead
            return self._run(f['guard'].get('do', []), ctx)
        if f.get('unsupported') is not None:
            return self._run(f.get('do', [dict(do='unsupported', note=f['unsupported'])]), ctx)
        if t in ('link', 'action'):
            return self._run(f.get('do', []), ctx)
        if t == 'info' or f.get('readonly'):
            return False
        v = self._form_buffer(fid, ctx).get(f['id'])
        title = self._label(dict(f, label=f['title']) if f.get('title') is not None else f, ctx)   # 'title': editor heading (e.g. with the allowed range)
        ov = dict(target=dict(form=fid, field=f['id']), title=title)
        if t in ('enum', 'bool'):
            ov.update(kind='select', value=v)
        elif t in ('number', 'text', 'password'):
            layout = self._select_keyboard(f.get('keyboard') or ('numeric' if t == 'number' else 'alpha'))
            buf = '' if v is None or t == 'password' else (self._field_text(dict(f, unit=None), v) if t == 'number' else str(v))
            ov.update(kind='keyboard', layout=layout, mode=self._keyboard_modes(layout)[0], buffer=buf, error=None, selected=bool(buf),
                      password=t == 'password')
        elif t in ('date', 'time'):
            ov.update(kind='spinner', type=t, parts=_parse_datetime(t, v))
        elif t == 'multi':
            ov.update(kind='checklist', selected=list(v or []))
        else:
            return False
        return self._push_overlay(ov)

    def _target_def(self, target):
        if 'widget' in target:
            return self._widget(self._screen(), target['widget'])
        return self._form_field(target['form'], target['field'])

    def _commit(self, target, value, ev):
        """A value chosen in an editor: into the open form's buffer (written on Save), or through the field's / widget's own write when the
        form is not open (settings that apply at once) or is immediate; then the field's on_change actions."""
        ctx = dict(ev=ev, value=value)
        if 'widget' in target:
            return self._write(self._target_def(target).get('write'), ctx)
        fid = target['form']
        f = self._form_field(fid, target['field'])
        opened = fid in self.ui['forms']
        if f.get('write') and (not opened or self._form(fid).get('immediate')):
            if not self._write(f['write'], dict(ctx, buffer=dict(self._form_buffer(fid), **{f['id']: value}))):
                return False
        if opened:
            self.ui['forms'][fid]['buffer'][f['id']] = value
        return self._run(f.get('on_change', []), dict(ctx, buffer=self._form_buffer(fid)))

    # overlays (keyboards, pickers, dialogs) stack on top of the page; the top one takes the touches
    def _push_overlay(self, ov):
        if self.ui['overlay'] is not None:
            self.ui['overlays'].append(self.ui['overlay'])
        self.ui['overlay'] = ov
        return True

    def _pop_overlay(self):
        self.ui['overlay'] = self.ui['overlays'].pop() if self.ui['overlays'] else None
        return True

    def _keyboard(self, name):
        kb = self.spec.get('keyboards', {}).get(name) or KEYBOARDS.get(name)
        if kb is None:
            raise PanelError(f'unknown keyboard {name!r}')
        return kb

    def _keyboard_modes(self, name):
        return list(self._keyboard(name)['modes'])

    def _dialog_open(self, a, ctx):
        if a['dialog'] not in self.spec.get('dialogs', {}):
            raise PanelError(f'unknown dialog {a["dialog"]!r}')
        row = ctx.get('row')
        return self._push_overlay(dict(kind='dialog', dialog=a['dialog'], row=copy.deepcopy(row) if row is not None else None,
                                       row_index=ctx.get('row_index')))

    def _ov_ctx(self, ov):
        return dict(row=ov.get('row'), row_index=ov.get('row_index')) if ov.get('kind') == 'dialog' else {}

    def _ov_layout(self, ov):
        """Geometry and content of an overlay (the same structure is drawn, hit-tested and shown in the frame): rect, title, text lines,
        the edited text, an error line and buttons [{id, label, rect, selected, enabled, style}]."""
        W, H = self._touch_size()
        L = self._touch_layout()
        k = ov['kind']
        title = ''.join(c for c, _ in self._text_chars(ov.get('title', ''), {}))
        btn = lambda bid, label, rect, **kw: dict(dict(id=bid, label=label, rect=[int(round(v)) for v in rect], selected=False, enabled=True), **kw)  # noqa: E731
        bh = L['row_h']
        if k == 'keyboard':
            kb = self._keyboard(ov['layout'])
            rows = kb['modes'][ov['mode']]
            kh = int(H * kb.get('height', 0.5))
            top = H - kh
            rh = kh / len(rows)
            buttons = []
            for r, row in enumerate(rows):
                keys = [x if isinstance(x, list) else [x, 1] for x in row]
                tot = float(sum(wt for _, wt in keys))
                x = 0.0
                for tok, wt in keys:
                    ww = W * wt / tot
                    lbl = self._tr(kb.get('labels', {}).get(tok, KEY_LABELS.get(tok, tok)))
                    buttons.append(btn('key:' + tok, lbl, [x, top + r * rh, ww, rh], selected=tok == '{shift}' and ov['mode'] == 'upper'))
                    x += ww
            shown = '*' * len(ov['buffer']) if ov.get('password') else ov['buffer']
            box = [0, top - 2 * bh - 8, W, kh + 2 * bh + 8]
            buttons.insert(0, btn('field', shown, [8, top - bh - 4, W - 16, bh], selected=bool(ov.get('selected'))))
            return dict(kind=k, rect=box, title=title, lines=[], field=shown, cursor=not ov.get('selected'), error=ov.get('error'), buttons=buttons,
                        mode=ov['mode'])
        if k == 'select':
            f = self._target_def(ov['target'])
            opts = self._opts(f)
            n = len(opts)
            ncol = 1 if n <= 6 else 2 if n <= 14 else 3
            nrow = max(1, -(-n // ncol))
            w = min(W - 16, 280 * ncol)
            avail = H - 3 * bh - 32
            rh = min(bh, max(18, avail // nrow - 4))
            h = bh + 8 + nrow * (rh + 4) + bh + 12
            x0, y0 = (W - w) // 2, max(4, (H - h) // 2)
            cw = (w - 16 - 4 * (ncol - 1)) / ncol
            buttons = []
            for i, o in enumerate(opts):
                c, r = i // nrow, i % nrow
                buttons.append(btn(f'opt:{i}', self._tr(o.get('text', _key(o['value']))), [x0 + 8 + c * (cw + 4), y0 + bh + 8 + r * (rh + 4), cw, rh],
                                   selected=o['value'] == ov.get('value')))
            buttons.append(btn('cancel', self._tr('Cancel'), [x0 + 8, y0 + h - bh - 6, w - 16, bh]))
            return dict(kind=k, rect=[x0, y0, w, h], title=title, lines=[], buttons=buttons)
        if k == 'spinner':
            cols = _spinner_columns(ov['type'], ov['parts'])
            cw = 150
            w = cw * len(cols) + 16
            h = bh + 8 + 3 * (bh + 4) + bh + 12
            x0, y0 = (W - w) // 2, max(4, (H - h) // 2)
            buttons, lines = [], []
            for i, (name, lo, hi, text) in enumerate(cols):
                x = x0 + 8 + i * cw
                buttons.append(btn(f'inc:{i}', '+', [x + 2, y0 + bh + 8, cw - 4, bh]))
                buttons.append(btn(f'val:{i}', self._tr(text), [x + 2, y0 + 2 * bh + 12, cw - 4, bh], enabled=False, style='value'))
                buttons.append(btn(f'dec:{i}', '-', [x + 2, y0 + 3 * bh + 16, cw - 4, bh]))
            half = (w - 20) / 2
            buttons.append(btn('cancel', self._tr('Cancel'), [x0 + 8, y0 + h - bh - 6, half, bh]))
            buttons.append(btn('ok', self._tr('OK'), [x0 + 12 + half, y0 + h - bh - 6, half, bh], style='primary'))
            return dict(kind=k, rect=[x0, y0, w, h], title=title, lines=lines, buttons=buttons, value=_join_datetime(ov['type'], ov['parts']))
        if k == 'checklist':
            f = self._target_def(ov['target'])
            opts = self._opts(f)
            n = len(opts)
            ncol = 1 if n <= 7 else 2
            nrow = max(1, -(-n // ncol))
            w = min(W - 16, 320 * ncol)
            rh = min(bh, max(18, (H - 4 * bh - 40) // nrow - 4))
            h = bh + 8 + nrow * (rh + 4) + 2 * (bh + 4) + 12
            x0, y0 = (W - w) // 2, max(4, (H - h) // 2)
            cw = (w - 16 - 4 * (ncol - 1)) / ncol
            buttons = []
            for i, o in enumerate(opts):
                c, r = i // nrow, i % nrow
                on = o['value'] in ov['selected']
                mark = ('%2d ' % (ov['selected'].index(o['value']) + 1) if on else ' - ') if f.get('ordered') else ('[x] ' if on else '[ ] ')
                buttons.append(btn(f'opt:{i}', mark + self._tr(o.get('text', _key(o['value']))),       # ordered lists show the position number
                                   [x0 + 8 + c * (cw + 4), y0 + bh + 8 + r * (rh + 4), cw, rh], selected=on))
            half = (w - 20) / 2
            yb = y0 + h - 2 * bh - 10
            if f.get('std') is not None:                                      # [STD]: back to the factory selection (instead of Select all)
                buttons.append(btn('std', self._tr('STD'), [x0 + 8, yb, half, bh]))
            else:
                buttons.append(btn('all', self._tr('Select all'), [x0 + 8, yb, half, bh]))
            buttons.append(btn('none', self._tr('Deselect all'), [x0 + 12 + half, yb, half, bh]))
            buttons.append(btn('cancel', self._tr('Cancel'), [x0 + 8, yb + bh + 4, half, bh]))
            buttons.append(btn('ok', self._tr('OK'), [x0 + 12 + half, yb + bh + 4, half, bh], style='primary'))
            return dict(kind=k, rect=[x0, y0, w, h], title=title, lines=[], buttons=buttons)
        if k == 'dialog':
            dlg = self.spec['dialogs'][ov['dialog']]
            ctx = self._ov_ctx(ov)
            title = ''.join(c for c, _ in self._text_chars(dlg.get('title', ''), ctx))
            s = self.spec['displays'][self._screen()['nav']['display']].get('text_scale', 2)
            lines = []
            for b in dlg.get('lines', []):
                lines += ''.join(c for c, _ in self._text_chars(b, ctx)).split('\n') if self._cond(b.get('visible_if') if isinstance(b, dict) else None, ctx) else []
            rows, fctx = self._form_rows(dict(form=dlg['form'], fields=dlg.get('fields')), ctx) if dlg.get('form') else ([], ctx)
            btns = [b for b in dlg.get('buttons', []) if self._cond(b.get('visible_if'), ctx)]
            column = dlg.get('buttons_layout') == 'column'                    # menu-like dialogs: one button per row
            w = dlg.get('w', int(W * 0.7))
            lh = 9 * s + 4
            h = bh + 8 + len(lines) * lh + len(rows) * (bh + 4) + ((bh + 4) * len(btns) + 12 if column else (bh + 16 if btns else 8)) + 8
            x0, y0 = (W - w) // 2, max(4, (H - h) // 2)
            buttons = []
            y = y0 + bh + 8 + len(lines) * lh + 4
            for f in rows:
                val = self._form_value_text(f, fctx['buffer'].get(f['id']), fctx)
                buttons.append(btn('row:' + f['id'], self._label(f, fctx), [x0 + 8, y, w - 16, bh], value=val, style='row',
                                   enabled=bool(self._cond(f.get('enabled_if'), fctx)) and f.get('type') != 'info'))
                y += bh + 4
            n = len(btns)
            bw = (w - 16 - 4 * max(0, n - 1)) / max(1, n)
            for i, b in enumerate(btns):
                rect = [x0 + 8, y + 4 + i * (bh + 4), w - 16, bh] if column else [x0 + 8 + i * (bw + 4), y0 + h - bh - 8, bw, bh]
                buttons.append(btn(f'btn:{i}', ''.join(c for c, _ in self._text_chars(b.get('label', ''), ctx)), rect, style=b.get('style'),
                                   enabled=bool(self._cond(b.get('enabled'), ctx))))
            return dict(kind=k, dialog=ov['dialog'], rect=[x0, y0, w, h], title=title, lines=lines, buttons=buttons, style=dlg.get('style'))
        raise PanelError(f'unknown overlay {k!r}')

    def _touch_overlay2(self, scr, ov, ev):
        if ev.get('to') is not None and _swipe(ev):
            return False                                                          # overlays are modal and do not scroll
        lay = self._ov_layout(ov)
        for b in lay['buttons']:
            x, y, w, h = b['rect']
            if x <= ev['x'] < x + w and y <= ev['y'] < y + h:
                return bool(b['enabled']) and self._ov_button(ov, b['id'], ev)
        return False

    def _ov_button(self, ov, bid, ev):
        k = ov['kind']
        if bid == 'cancel':
            return self._pop_overlay()
        if k == 'keyboard':
            if bid == 'field':                                                    # tap in the input field: keep the text, cursor to its end
                ov['selected'] = False
                return True
            tok = bid[4:]
            return self._keyboard_key(ov, tok, ev)
        if k == 'select':
            v = self._opts(self._target_def(ov['target']))[int(bid[4:])]['value']
            self._pop_overlay()
            return self._commit(ov['target'], v, ev)
        if k == 'spinner':
            if bid == 'ok':
                self._pop_overlay()
                return self._commit(ov['target'], _join_datetime(ov['type'], ov['parts']), ev)
            ov['parts'] = _spin(ov['type'], ov['parts'], int(bid[4:]), 1 if bid.startswith('inc') else -1)
            return True
        if k == 'checklist':
            fdef = self._target_def(ov['target'])
            opts = [o['value'] for o in self._opts(fdef)]
            if bid == 'ok':
                self._pop_overlay()
                return self._commit(ov['target'], [v for v in (ov['selected'] if fdef.get('ordered') else opts) if v in (opts if fdef.get('ordered') else ov['selected'])], ev)
            if bid in ('all', 'none'):
                ov['selected'] = list(opts) if bid == 'all' else []
                return True
            if bid == 'std':
                ov['selected'] = [v for v in fdef['std'] if v in opts]
                return True
            v = opts[int(bid[4:])]
            if v not in ov['selected'] and fdef.get('max_items') is not None and len(ov['selected']) >= fdef['max_items']:
                self._accepted = False                                        # a full list (e.g. max. 4 information fields) takes no more
                return True
            ov['selected'] = [x for x in ov['selected'] if x != v] if v in ov['selected'] else ov['selected'] + [v]
            return True
        if k == 'dialog':
            dlg = self.spec['dialogs'][ov['dialog']]
            ctx = dict(self._ov_ctx(ov), ev=ev)
            if bid.startswith('row:'):
                return self._open_field(dlg['form'], self._form_field(dlg['form'], bid[4:]), dict(ctx, buffer=self._form_buffer(dlg['form'])))
            b = [b for b in dlg.get('buttons', []) if self._cond(b.get('visible_if'), ctx)][int(bid[4:])]
            if not b.get('keep'):
                self._pop_overlay()
            return self._run(b.get('do', []), ctx)
        return False

    def _keyboard_key(self, ov, tok, ev):
        """Keyboard keys: characters ({space} a blank), {bksp}, {clear}, {shift} (upper / lower case), {sym} / {abc} (character pages),
        {cancel} (discard), {ok} / {enter} (confirm).  Text shown selected on opening is replaced by the first character typed."""
        modes = self._keyboard_modes(ov['layout'])
        if tok == '{bksp}':
            ov['buffer'] = '' if ov.get('selected') else ov['buffer'][:-1]
        elif tok == '{clear}':
            ov['buffer'] = ''
        elif tok == '{shift}':
            ov['mode'] = 'upper' if ov['mode'] == 'lower' and 'upper' in modes else 'lower' if 'lower' in modes else ov['mode']
            return True
        elif tok in ('{sym}', '{abc}'):
            ov['mode'] = 'sym' if tok == '{sym}' and 'sym' in modes else modes[0]
            return True
        elif tok == '{cancel}':
            return self._pop_overlay()
        elif tok in ('{ok}', '{enter}'):
            return self._keyboard_ok(ov, ev)
        else:
            ch = ' ' if tok == '{space}' else tok
            f = self._target_def(ov['target'])
            base = '' if ov.get('selected') else ov['buffer']
            if len(base) >= f.get('max_chars', 64 if f.get('type') != 'number' else 16):
                return False
            ov['buffer'] = base + ch
        ov['selected'], ov['error'] = False, None
        return True

    def _keyboard_ok(self, ov, ev):
        f = self._target_def(ov['target'])
        text = ov['buffer']
        if f.get('type') == 'number':
            try:
                v = float(text)
            except ValueError:
                v = None
            if v is None and f.get('nullable') and text == '':
                pass
            elif v is None or not math.isfinite(v):
                ov['error'] = self._tr(f.get('error_text', 'Invalid value'))
                self._accepted = False
                return False
            elif not (f.get('min', -math.inf) - 1e-12 <= v <= f.get('max', math.inf) + 1e-12):
                ov['error'] = self._tr(f.get('range_text', 'Out of range'))
                self._accepted = False
                return False
            if v is not None and f.get('step') and f.get('snap_typed', True) and 'min' in f and 'max' in f:
                v = self._snap_value(f, v)
            if v is not None and (f.get('integer') or (float(f.get('step', 0.5)).is_integer() and float(v).is_integer())):
                v = int(round(v))
        else:
            v = text
            if len(text) < f.get('min_chars', 0):
                ov['error'] = self._tr(f.get('error_text', 'Invalid value'))
                self._accepted = False
                return False
        self._pop_overlay()
        return self._commit(ov['target'], v, ev)

    # dynamic lists: rows from a runtime list ('items'), each row with its own context ({'row': path}, {'row_index': true})
    def _list_items(self, w, ctx):
        items = self._src(w['items'], ctx)
        if isinstance(items, dict):
            items = [dict(v, id=k) if isinstance(v, dict) and 'id' not in v else v for k, v in items.items()]
        items = list(items) if isinstance(items, (list, tuple)) else []
        if w.get('row', {}).get('visible_if') is not None:
            items = [it for i, it in enumerate(items) if self._cond(w['row']['visible_if'], dict(ctx, row=it, row_index=i))]
        return items

    def _touch_rows(self, w, ev, n_rows, run_row):
        """Drag scrolls a row widget (dynamic list or form) by whole rows; a tap runs the row under the finger."""
        row_h = w.get('row_h', 40)
        visible = max(1, w['rect'][3] // row_h)
        if ev.get('to') is not None:
            off = self.ui['scroll'].get(w['id'], 0) - int(round((ev['to'][1] - ev['y']) / row_h))
            self.ui['scroll'][w['id']] = min(max(off, 0), max(0, n_rows - visible))
            return True
        k = self.ui['scroll'].get(w['id'], 0) + (ev['y'] - w['rect'][1]) // row_h
        return 0 <= k < n_rows and run_row(k)

    def _touch_widget2(self, scr, w, ev):
        ctx = dict(ev=ev)
        t = w['type']
        if t == 'list':
            items = self._list_items(w, ctx)
            rdef = w.get('row', {})

            def run(k):
                rctx = dict(ctx, row=items[k], row_index=k)
                return bool(self._cond(rdef.get('enabled'), rctx)) and self._run(rdef.get('do', []), rctx)
            return self._touch_rows(w, ev, len(items), run)
        if t == 'form':
            rows, fctx = self._form_rows(w, ctx)
            return self._touch_rows(w, ev, len(rows), lambda k: self._open_field(w['form'], rows[k], fctx))
        if t in ('number', 'text'):
            cur = self._src(w['read'], ctx) if w.get('read') else None
            buf = '' if cur is None else (_format(cur, w.get('fmt') or {}) if t == 'number' else str(cur))
            return self._push_overlay(dict(kind='keyboard', target=dict(widget=w['id']), title=w.get('label', w['id']),
                                           layout=self._select_keyboard(w['keyboard']),
                                           mode=self._keyboard_modes(self._select_keyboard(w['keyboard']))[0], buffer=buf, error=None, selected=bool(buf),
                                           password=bool(w.get('password'))))
        return False

    def _resolve_touch2(self, w, ent, ctx):
        """Frame entries of the toolkit's widget options: style / scale / align / fill / selected / color, gauges, xy targets, dynamic lists
        (rows, cells, selected rows) and form rows (label and value cells)."""
        t = w['type']
        for k in ('style', 'scale', 'align', 'fill', 'wrap', 'valign'):
            if k in w:
                ent[k] = self._src(w[k], ctx) if isinstance(w[k], dict) else w[k]
        if 'selected' in w:
            ent['selected'] = bool(self._cond(w['selected'], ctx))
        if 'color' in w:
            ent['color'] = self._src(w['color'], ctx) if isinstance(w['color'], dict) else w['color']
        if t == 'gauge':
            v = self._src(w['value'], ctx)
            ent['fraction'] = None if v is None else min(max(float(v), 0.0), 1.0)
            ent['marks'] = [min(max(float(m), 0.0), 1.0) for m in (self._src(x, ctx) for x in w.get('marks', [])) if m is not None]
        if t == 'xy':
            xy = [self._src(w.get(k), ctx) for k in ('x', 'y')]
            ent['xy'] = None if None in xy else [min(max(float(v), -1.0), 1.0) for v in xy]
            ent['ok_radius'] = w.get('ok_radius', 0.25)
        if t == 'plot':
            ent.update(self._plot_entry(w, ctx))
        if t == 'image':
            ent.update(self._image_entry(w, ctx))
        if t == 'list' and 'items' in w:
            items = self._list_items(w, ctx)
            rdef = w.get('row', {})
            off = self.ui['scroll'].get(w['id'], 0)
            n = max(1, w['rect'][3] // w.get('row_h', 40))
            cells, sel, dis = [], [], []
            for i, it in enumerate(items[off:off + n]):
                rctx = dict(ctx, row=it, row_index=off + i)
                cs = rdef.get('cells') or [rdef.get('text', {'src': {'row': True}})]
                cells.append([''.join(c for c, _ in self._text_chars(b, rctx)) for b in cs])
                if self._cond(rdef.get('selected', False), rctx):
                    sel.append(i)
                if not self._cond(rdef.get('enabled'), rctx):
                    dis.append(i)
            ent.update(rows=['  '.join(c) for c in cells], cells=cells, selected_rows=sel, disabled_rows=dis, scroll=off, count=len(items),
                       col_w=w.get('col_w') or rdef.get('col_w'), row_h=w.get('row_h', 40))
            if not items and w.get('empty_text'):
                ent['empty_text'] = ''.join(c for c, _ in self._text_chars(w['empty_text'], ctx))
        if t == 'form':
            rows, fctx = self._form_rows(w, ctx)
            off = self.ui['scroll'].get(w['id'], 0)
            n = max(1, w['rect'][3] // w.get('row_h', 40))
            cells, dis = [], []
            for i, f in enumerate(rows[off:off + n]):
                cells.append([self._label(f, fctx), self._form_value_text(f, fctx['buffer'].get(f['id']), fctx)])
                if not self._cond(f.get('enabled_if'), fctx) or f.get('readonly'):
                    dis.append(i)
            ent.update(rows=['  '.join(c) for c in cells], cells=cells, row_ids=[f['id'] for f in rows[off:off + n]], disabled_rows=dis,
                       scroll=off, count=len(rows), col_w=w.get('col_w', [0.55, 0.45]), row_h=w.get('row_h', 40), form=w['form'])
        return ent

    def _image_entry(self, w, ctx):
        """Frame of an 'image' widget (a camera view or captured picture drawn by the device): src is a list of rows of grey levels 0-255 or
        [r, g, b] triples; rows longer than max_px [w, h] (default 160 x 120) are thinned evenly.  Entry: pixels, pw, ph."""
        rows = self._src(w['src'], ctx)
        rows = [r for r in rows if isinstance(r, (list, tuple))] if isinstance(rows, (list, tuple)) else []
        mw, mh = w.get('max_px', [160, 120])
        if not rows or not rows[0]:
            return dict(pixels=[], pw=0, ph=0)
        ph, pw = len(rows), min(len(r) for r in rows)
        ys = [int(i * ph / min(ph, mh)) for i in range(min(ph, mh))]
        xs = [int(j * pw / min(pw, mw)) for j in range(min(pw, mw))]
        px = [[(list(rows[y][x]) if isinstance(rows[y][x], (list, tuple)) else int(rows[y][x])) for x in xs] for y in ys]
        return dict(pixels=px, pw=len(xs), ph=len(ys))

    def _plot_entry(self, w, ctx):
        """Frame of a 'plot' widget (trend chart): series [{src: [[x, y], ...], axis 'left' | 'right', color, label, visible_if}] shown over an x
        window of length 'span' (source or number) ending at 'x_end' (source; default the newest x, never before x_min + span); y range per axis
        from 'y_max' / 'y_min' ({'left' | 'right': source or number}; missing or None = automatic from the shown points); at most 'max_points'
        points per series (evenly thinned).  Entry: series with their shown points, x_range, y_range {axis: [lo, hi]}, x_fmt."""
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)     # noqa: E731
        series, xs = [], []
        for s in w.get('series', []):
            if not self._cond(s.get('visible_if'), ctx):
                continue
            pts = [[float(p[0]), float(p[1])] for p in (self._src(s['src'], ctx) or []) if isinstance(p, (list, tuple)) and len(p) >= 2
                   and num(p[0]) and num(p[1])]
            series.append(dict(label=self._tr(s.get('label', '')), axis=s.get('axis', 'left'), color=s.get('color', 'accent'), points=pts))
            xs += [p[0] for p in pts]
        span = float(self._src(w.get('span', 60.0), ctx) or 60.0)
        x_min = float(w.get('x_min', 0.0))
        end = self._src(w['x_end'], ctx) if w.get('x_end') is not None else (max(xs) if xs else None)
        x1 = max(float(end) if num(end) else x_min, x_min + span)
        x0 = x1 - span
        n_max = int(w.get('max_points', 600))
        for s in series:
            pts = [p for p in s['points'] if x0 - 1e-9 <= p[0] <= x1 + 1e-9]
            s['points'] = pts if len(pts) <= n_max else [pts[int(i * (len(pts) - 1) / (n_max - 1))] for i in range(n_max)]
        y_range = {}
        for axis in ('left', 'right'):
            vals = [p[1] for s in series if s['axis'] == axis for p in s['points']]
            if not vals and axis not in (w.get('y_max') or {}) and not any(s['axis'] == axis for s in series):
                continue
            hi = self._src((w.get('y_max') or {}).get(axis), ctx)
            lo = self._src((w.get('y_min') or {}).get(axis), ctx)
            auto_hi = not num(hi)
            lo = float(lo) if num(lo) else min([0.0] + vals)
            hi = float(hi) if num(hi) else (max(vals) if vals else lo + 1.0)
            if hi - lo < 1e-12:
                hi = lo + (abs(lo) * 0.1 or 1.0)
            elif auto_hi:
                hi += 0.05 * (hi - lo)
            y_range[axis] = [lo, hi]
        out = dict(series=series, x_range=[x0, x1], y_range=y_range, x_fmt=w.get('x_fmt') or {'decimals': 2})
        if w.get('draw') is not None:                                         # draw style (source or literal): 'line' | 'marker' | 'both'
            out['draw'] = self._src(w['draw'], ctx) if isinstance(w['draw'], dict) else w['draw']
        return out

    def _touch_act(self, a, ctx):
        """Toolkit actions: form_open / form_save / form_cancel / form_set, dialog / dialog_close / overlay_close, home, back."""
        k = a['do']
        if k == 'form_open':
            return self._form_open(a, ctx)
        if k == 'form_save':
            return self._form_save(a, ctx)
        if k == 'form_cancel':
            return self._form_cancel(a, ctx)
        if k == 'form_set':
            return self._form_set(a, ctx)
        if k == 'form_edit':                                                  # a field's editor opened by a button (e.g. a value tapped on a graph)
            fid = self._form_id(a)
            return self._open_field(fid, self._form_field(fid, a['field']), dict(ctx, buffer=self._form_buffer(fid, ctx), form=fid))
        if k == 'dialog':
            return self._dialog_open(a, ctx)
        if k in ('dialog_close', 'overlay_close'):
            if self.ui['overlay'] is not None and (k == 'overlay_close' or self.ui['overlay']['kind'] == 'dialog'):
                self._pop_overlay()
            return True
        if k == 'home':
            return self._touch_home(self._screen()['nav'])
        if k == 'back':
            return self._touch_back()
        if k == 'scroll':                                                     # scroll arrows beside a list (no swipe on the device)
            return self._scroll_rows(a, ctx)
        raise PanelError(f'unknown action {k!r}')

    def _scroll_rows(self, a, ctx):
        """{'do': 'scroll', 'widget': id, 'rows': n}: move a list or form widget of the shown page by n whole rows (negative: up), within its
        rows - the up / down arrow buttons of touch screens that take no drag gestures."""
        page = ((self._screen().get('nav') or {}).get('pages') or {}).get(self.ui['page'])
        w = next((x for x in self._layout_page(page, ctx) if x.get('id') == a['widget']), None) if page else None
        if w is None or w['type'] not in ('list', 'form'):
            return False
        if w['type'] == 'form':
            n = len(self._form_rows(w, ctx)[0])
        else:
            n = len(self._list_items(w, ctx)) if 'items' in w else len(w.get('rows', []))
        visible = max(1, w['rect'][3] // w.get('row_h', 40))
        off = self.ui['scroll'].get(w['id'], 0) + int(a.get('rows', 1))
        self.ui['scroll'][w['id']] = min(max(off, 0), max(0, n - visible))
        return True

    # ---- text dialogs (arrow menus on graphic / character LCDs, e.g. the Metrohm 899 / 914 dialogs): a function bar on the bottom line
    # (nav.function_bar [{id, label, do, visible_if}]: left / right select, OK executes; drawn with the selected entry inverted), items shown
    # but not selectable (enabled_if), numbers that may be switched off (none_text, e.g. 'off'), enum options from runtime lists
    # (options_src), BACK accepting an edit (nav.back_confirms), selection lists edited with up = previous option (item / field edit_style
    # 'list'), line parts drawn inverted ({'inverse': cond} on a part), and text / number entry on a character grid (field entry 'grid',
    # grid: a spec char_grids entry {rows: [[token]], labels}: the arrows select a character or function, OK enters it; tokens are text to
    # insert, {'label', 'insert'} or functions '{accept}' '{cancel}' '{clear}' '{bksp}' '{left}' '{right}' '{grid:NAME}' '{set:KEY}' (the
    # field's 'specials' value, e.g. off)).  Sources {'menu': 'title' | 'path_title' | 'item' | 'label' | 'data' | 'index' | 'count' |
    # 'field' | 'function'}.
    GRID_LABELS = {'accept': 'Accept', 'cancel': 'Cancel', 'clear': 'Clear', 'bksp': '[<-]', 'left': '<-|', 'right': '|->'}

    def _menu_info(self, what, ctx):
        if what == 'function':
            fb = self._fbar_items()
            return self._fbar_label(fb[min(self.ui.get('fbar', 0), len(fb) - 1)], ctx) if fb else None
        if what == 'field':
            ed = self.ui['editor']
            return None if ed is None else self._item_label(self._current_field(ed), ctx)
        if self._screen().get('style') not in MENU_STYLES or not self.ui['path']:
            return None
        if what in ('title', 'path_title'):
            items = self._path_items()[:-1]
            if what == 'path_title' or not items:
                return ' > '.join(self._item_label(x) for x in items) or self._menu().get('label', '')
            return items[-1].get('title') or self._item_label(items[-1])     # 'title': the dialog name of a submenu
        items = self._list_at(self.ui['path'])
        vis = self._visible_indices(items)
        if what == 'count':
            return len(vis)
        if what == 'index':
            return vis.index(self.ui['path'][-1]) if self.ui['path'][-1] in vis else None
        it = ctx.get('item') or self._current_item()[0]
        if what == 'item':
            return it['id']
        if what == 'label':
            return self._item_label(it)
        if what == 'data':
            d = it.get('data')
            return self._src(d, dict(ctx, item=it)) if isinstance(d, dict) else d
        raise PanelError(f'unknown menu source {what!r}')

    def _item_enabled(self, it, ctx=None):
        return it is None or it.get('enabled_if') is None or bool(self._cond(it['enabled_if'], dict(ctx or {}, item=it)))

    def _menu_settle(self):
        """Put the cursor of a list on its first visible, selectable item (lists whose entries come and go with the runtime)."""
        items = self._list_at(self.ui['path'])
        vis = [i for i in self._visible_indices(items) if self._item_enabled(items[i])]
        if vis and self.ui['path'][-1] not in vis:
            self.ui['path'][-1] = vis[0]

    def _fbar_items(self, scr=None):
        scr = scr or self._screen()
        return [fb for fb in (scr.get('nav') or {}).get('function_bar') or [] if self._cond(fb.get('visible_if'), {})]

    def _fbar_label(self, fb, ctx=None):
        lab = fb.get('label', fb.get('id', ''))
        return ''.join(c for c, _ in self._text_chars(lab, ctx or {})) if isinstance(lab, dict) else self._tr(lab)

    def _fbar_event(self, nav, ev):
        """left / right select a function of the bar, OK (nav.enter) runs it with the item under the cursor as context; None: not a bar key."""
        fbs = self._fbar_items()
        i = ev['input']
        if not fbs or self.ui['editor'] is not None:
            return None
        k = min(self.ui.get('fbar', 0), len(fbs) - 1)
        if i in (nav.get('left'), nav.get('right')):
            self.ui['fbar'] = min(max(k + (1 if i == nav.get('right') else -1), 0), len(fbs) - 1)
            return True
        if i in _aslist(nav.get('enter') or []):
            item, ref = self._current_item() if self.ui['path'] and self._screen().get('style') in MENU_STYLES else (None, None)
            if fbs[k].get('needs_item') and (item is None or not self._cond(item.get('visible_if'), {}) or not self._item_enabled(item)):
                self._accepted = False
                return True
            self.ui['fbar'] = k
            return self._run(fbs[k].get('do', []), dict(ev=ev, item=item, item_ref=ref))
        return None

    def _fbar_line(self, cols):
        """The function bar as a line: labels separated by blanks, the selected one inverted; a window that keeps it in view ('<' / '>' show
        more entries)."""
        fbs = self._fbar_items()
        if not fbs:
            return None
        labels = [self._fbar_label(fb) for fb in fbs]
        k = min(self.ui.get('fbar', 0), len(fbs) - 1)
        first = 0
        while first < k and (2 if first else 0) + sum(len(x) + 1 for x in labels[first:k + 1]) - 1 > cols:
            first += 1
        parts, used = ([{'text': '< '}], 2) if first else ([], 0)
        for j in range(first, len(labels)):
            need = len(labels[j]) + (1 if j > first else 0)
            if j > k and used + need + (2 if j < len(labels) - 1 else 0) > cols:
                parts.append({'text': ' >'})
                break
            if j > first:
                parts.append({'text': ' '})
            parts.append({'text': labels[j], 'inverse': j == k})
            used += need
        return {'parts': parts}

    def _dialog_event(self, scr, ev):
        """Keys of the text dialogs on an arrow-menu screen; None leaves the key to the arrow-menu rules."""
        nav, ed, i = scr['nav'], self.ui['editor'], ev['input']
        if ed is not None:
            f = self._current_field(ed)
            if f.get('entry') == 'grid':
                return self._grid_event(nav, ev)
            if i in _aslist(nav.get('back') or []) and nav.get('back_confirms'):
                return self._nav('confirm', dict(ev=ev))
            item = self._current_item()[0] if self.ui['path'] and ed['ref'][0] == 'item' else None
            if f['type'] in ('enum', 'bool') and 'list' in ((item or {}).get('edit_style'), f.get('edit_style')) and i in (nav.get('up'), nav.get('down')):
                return self._nav('change', {}, dict(n=-1 if i == nav.get('up') else 1))     # a selection list: up = the option above
            return None
        if nav.get('function_bar') is not None and i in (nav.get('left'), nav.get('right'), *_aslist(nav.get('enter') or [])):
            return self._fbar_event(nav, ev)
        return None

    # character-grid entry
    def _grid_rows(self, f):
        g = self.spec.get('char_grids', {})[self.ui['editor'].get('grid_name') or f['grid']]
        rows = []
        for row in g['rows']:
            toks = row['tokens'] if isinstance(row, dict) else row
            out = []
            for t in toks:
                if isinstance(t, dict):
                    out.append(dict(label=t.get('label', t.get('insert', '')), insert=t.get('insert'), fn=t.get('fn'), long=t.get('long')))
                elif len(t) > 2 and t[0] == '{' and t[-1] == '}':
                    fn = t[1:-1]
                    label = g.get('labels', {}).get(fn) or self.GRID_LABELS.get(fn) or fn.split(':', 1)[-1]
                    out.append(dict(label=label, insert=None, fn=fn))
                else:
                    out.append(dict(label=t, insert=t, fn=None))
            sep = row.get('sep') if isinstance(row, dict) and 'sep' in row else ('' if all(len(x['label']) == 1 for x in out) else ' ')
            rows.append(dict(tokens=out, sep=sep))
        return rows, g

    def _grid_init(self, ed, f):
        v = ed['buffer'].get(f['id'])
        text = '' if v is None else (self._field_text(dict(f, none_text=''), v) if f['type'] == 'number' else str(v))
        ed.update(typed=text, digit=len(text), grid=[0, 0], grid_name=None)

    def _grid_event(self, nav, ev):
        ed, i = self.ui['editor'], ev['input']
        f = self._current_field(ed)
        rows, g = self._grid_rows(f)
        r, c = ed['grid']
        if i in (nav.get('up'), nav.get('down')):
            r = min(max(r + (1 if i == nav.get('down') else -1), 0), len(rows) - 1)
            ed['grid'] = [r, min(c, len(rows[r]['tokens']) - 1)]
            return True
        if i in (nav.get('left'), nav.get('right')):
            ed['grid'] = [r, min(max(c + (1 if i == nav.get('right') else -1), 0), len(rows[r]['tokens']) - 1)]
            return True
        if i in _aslist(nav.get('back') or []):
            return self._grid_accept() if nav.get('back_confirms', True) else self._nav('cancel', {})
        if i not in _aslist(nav.get('enter') or []):
            return None
        tok = rows[r]['tokens'][c]
        text, pos = ed['typed'] or '', ed['digit']
        if tok['fn'] is None:
            ins = tok['insert'] or ''
            if ev.get('long'):                                               # held OK: the character's variant (grid 'long' table or token)
                ins = tok.get('long') or g.get('long', {}).get(ins, ins)
            limit = f.get('max_len', f.get('max_chars', 30))
            if len(text) + len(ins) > limit:
                self._accepted = False
                return True
            ed['typed'], ed['digit'] = text[:pos] + ins + text[pos:], pos + len(ins)
            if g.get('return_after') and ed.get('grid_name'):                  # a sub-list (e.g. Var) goes back after one entry
                ed.update(grid_name=None, grid=[0, 0])
            return True
        fn = tok['fn']
        if fn == 'accept':
            return self._grid_accept()
        if fn == 'cancel':
            self._close_editor()
            return True
        if fn == 'clear':
            ed['typed'], ed['digit'] = '', 0
        elif fn == 'bksp':
            if pos > 0:
                ed['typed'], ed['digit'] = text[:pos - 1] + text[pos:], pos - 1
        elif fn in ('left', 'right'):
            ed['digit'] = min(max(pos + (1 if fn == 'right' else -1), 0), len(text))
        elif fn.startswith('grid:'):
            ed.update(grid_name=fn[5:], grid=[0, 0])
        elif fn.startswith('set:'):
            ed['buffer'][f['id']] = (f.get('specials') or {}).get(fn[4:])
            ed['typed'] = None
            return self._edit_confirm()
        return True

    def _grid_accept(self):
        """Accept: numbers are parsed and range-checked (refused: the entry indication, the editor stays open with the text), texts checked
        against an optional pattern; then the field's write and the next field as for any confirmed field."""
        import re
        ed = self.ui['editor']
        f = self._current_field(ed)
        text = ed['typed'] or ''
        ok = True
        if f['type'] == 'number':
            try:
                v = float(text)
                ok = math.isfinite(v) and f['min'] - 1e-9 <= v <= f['max'] + 1e-9
            except ValueError:
                ok = False
            if ok:
                v = int(round(v)) if f.get('integer') else (self._snap_value(f, v) if f.get('snap_typed', False) else v)
        else:
            v = text
            ok = not f.get('pattern') or re.fullmatch(f['pattern'], text) is not None
        if not ok:
            self._accepted = False
            self._run(f.get('on_invalid', self.spec.get('errors', {}).get('entry', {}).get('indication', [])), {})
            return False
        ed['buffer'][f['id']], ed['typed'] = v, None
        return self._edit_confirm()

    def _grid_lines(self, rows, cols, f):
        """Entry screen: the field label, the input line (inverted, '_' at the cursor), then the character rows and the function rows with the
        selected entry inverted; the rows scroll to keep the selection in view."""
        ed = self.ui['editor']
        grid, _ = self._grid_rows(f)
        text = ed['typed'] or ''
        shown = text[:ed['digit']] + '_' + text[ed['digit']:]
        lines = [{'text': self._item_label(f)[:cols]}, {'text': shown[-cols:].ljust(cols), 'inverse': True}]
        r_sel, c_sel = ed['grid']
        n = max(1, rows - 2)
        top = min(max(0, r_sel - n + 1), max(0, len(grid) - n))
        for r in range(top, min(len(grid), top + n)):
            parts = []
            for c, tok in enumerate(grid[r]['tokens']):
                if c:
                    parts.append({'text': grid[r]['sep']})
                parts.append({'text': tok['label'], 'inverse': r == r_sel and c == c_sel})
            lines.append({'parts': parts})
        return lines

    def _inverse_spans(self, b, ctx, cols, key='inverse'):
        """Column ranges [start, end) of the parts of a line drawn inverted (a part with {'inverse': condition}); key 'strike': the parts drawn
        crossed out (a part with {'strike': condition}, graphic displays)."""
        while isinstance(b, dict) and 'cases' in b:
            b = next(({k: v for k, v in case.items() if k != 'when'} for case in b['cases'] if self._cond(case.get('when'), ctx)), None)
        if not isinstance(b, dict) or not b.get('parts') or not self._cond(b.get('show'), ctx):
            return []
        spans, pos = [], 0
        for p in b['parts']:
            n = len(self._text_chars(p, ctx))
            if isinstance(p, dict) and p.get(key) is not None and n and self._cond(p[key], ctx) and pos < cols:
                spans.append([pos, min(pos + n, cols)])
            pos += n
        return spans


def _swipe(ev):
    """Direction of a drag ('left' / 'right' / 'up' / 'down'), None for a tap (under 20 px)."""
    if ev.get('event') != 'touch' or ev.get('to') is None:
        return None
    dx, dy = ev['to'][0] - ev['x'], ev['to'][1] - ev['y']
    if max(abs(dx), abs(dy)) < 20:
        return None
    if abs(dx) >= abs(dy):
        return 'right' if dx > 0 else 'left'
    return 'down' if dy > 0 else 'up'


def _merge(dst, src):
    for k, v in src.items():
        if isinstance(v, dict) and isinstance(dst.get(k), dict) and k in ('fields', 'icons'):
            dst[k].update(v)
        else:
            dst[k] = copy.deepcopy(v)
    return dst


def _mask_digits(v, f):
    """A digit-wise number with 'places' ([place, radix] per digit) written into its 'mask' ('D' = a digit), e.g. seconds as 'DD:DD:DD.D'."""
    digits = iter(str(int(math.floor(abs(v) / p + 1e-9)) % r) for p, r in f['places'])
    return ''.join(next(digits) if c == 'D' else c for c in f['mask'])


def _digit_at(text, f, digit):
    """Text position of the digit-wise cursor: the digit-th digit of the shown value, skipping '.', ':' and a minus sign (the leading sign
    of a 'signed' field is the first position)."""
    pos = [j for j, c in enumerate(text) if c.isdigit() or (j == 0 and c in '+-' and f.get('signed'))]
    return pos[digit] if 0 <= digit < len(pos) else digit


def _format(v, fmt):
    """Number / text formatting for displays: {decimals}, {pattern} (str.format), {duration[, overflow: [{at_s, duration}]]} with the keys
    H (total hours), M, S, D (days), h (hours of the day)."""
    if ('date' in fmt or 'time' in fmt) and isinstance(v, str):
        return _format_datetime(v, fmt)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if 'duration' in fmt:
        t = int(v)
        pattern = fmt['duration']
        for o in fmt.get('overflow', []):
            if t >= o['at_s']:
                pattern = o['duration']
        return pattern.format(H=t // 3600, M=t % 3600 // 60, S=t % 60, D=t // 86400, h=t % 86400 // 3600, m=t // 60)
    if 'pattern' in fmt:
        return fmt['pattern'].format(v)
    if 'decimals' in fmt:
        n = int(fmt['decimals'])
        x = math.floor(abs(v) * 10 ** n + 0.5 + 1e-9) / 10 ** n            # half away from zero, as a display rounds
        return f'{math.copysign(x, v) if x else 0.0:.{n}f}'
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


# ---------------------------------------------------------------- drawing (PIL; deterministic, no system fonts)
def _rgb(c, default):
    return tuple(c) if c else default


def _text(dr, x, y, text, scale, color):
    for i, ch in enumerate(text):
        cols = _glyph(ch)
        for cx, bits in enumerate(cols):
            for cy in range(8):
                if bits >> cy & 1:
                    x0, y0 = x + (i * 6 + cx) * scale, y + cy * scale
                    dr.rectangle([x0, y0, x0 + scale - 1, y0 + scale - 1], fill=color)


def text_width(text, scale):
    return max(0, len(text) * 6 * scale - scale)


def _hseg(dr, x0, x1, yc, t, color):
    h = t / 2.0
    dr.polygon([(x0, yc), (x0 + h, yc - h), (x1 - h, yc - h), (x1, yc), (x1 - h, yc + h), (x0 + h, yc + h)], fill=color)


def _vseg(dr, xc, y0, y1, t, color):
    h = t / 2.0
    dr.polygon([(xc, y0), (xc + h, y0 + h), (xc + h, y1 - h), (xc, y1), (xc - h, y1 - h), (xc - h, y0 + h)], fill=color)


def _digit(dr, x, y, w, h, t, segs, color):
    g = max(1.0, t * 0.15)
    xl, xr, yt, ym, yb = x + t / 2.0, x + w - t / 2.0, y + t / 2.0, y + h / 2.0, y + h - t / 2.0
    for s in segs:
        if s == 'a':
            _hseg(dr, xl + g, xr - g, yt, t, color)
        elif s == 'g':
            _hseg(dr, xl + g, xr - g, ym, t, color)
        elif s == 'd':
            _hseg(dr, xl + g, xr - g, yb, t, color)
        elif s == 'b':
            _vseg(dr, xr, yt + g, ym - g, t, color)
        elif s == 'c':
            _vseg(dr, xr, ym + g, yb - g, t, color)
        elif s == 'e':
            _vseg(dr, xl, ym + g, yb - g, t, color)
        elif s == 'f':
            _vseg(dr, xl, yt + g, ym - g, t, color)


def _draw_field(dr, f, text, color, ghost):
    """One seven-segment field: cells at x + i (cell_w + gap) (+ colon_gap at the printed colons), decimal points, colons."""
    n, cw, ch, gap = f['cells'], f['cell_w'], f['cell_h'], f.get('gap', 6)
    colons, cgap = f.get('colons', []), f.get('colon_gap', 0)
    t = f.get('thickness', max(2.0, cw * 0.18))
    cells, col = seven_segment_cells([(c, False) for c in text], n, colons, f.get('align', 'right'))
    xs = []
    for i in range(n):
        xs.append(f['x'] + i * (cw + gap) + cgap * sum(1 for b in colons if b <= i))
    for i, c in enumerate(cells):
        if ghost:
            _digit(dr, xs[i], f['y'], cw, ch, t, 'abcdefg', ghost)
        _digit(dr, xs[i], f['y'], cw, ch, t, c['segments'], color)
        if c['dp']:
            r = max(1.5, t * 0.6)
            cx, cy = xs[i] + cw + gap / 2.0, f['y'] + ch - r
            dr.rectangle([cx - r, cy - r, cx + r, cy + r], fill=color)
    for b in col:
        cx = xs[b] - (gap + cgap) / 2.0
        r = max(1.5, t * 0.5)
        for fy in (0.3, 0.7):
            cy = f['y'] + ch * fy
            dr.rectangle([cx - r, cy - r, cx + r, cy + r], fill=color)


def _draw_dot_field(dr, f, text, color, ghost):
    """A dot-matrix text field of a segment display (field 'font': '5x7'): 'cells' characters of 5x7 dots at x, y ('scale' px per dot),
    left-aligned unless 'align' is 'right'; the ghost colour draws the unlit cell strip behind them."""
    n, s = f['cells'], f.get('scale', 2)
    text = text[:n] if f.get('align', 'left') != 'right' else text[-n:].rjust(n)
    if ghost:
        dr.rectangle([f['x'] - s, f['y'] - s, f['x'] + n * 6 * s - 1, f['y'] + 8 * s - 1], fill=ghost)
    _text(dr, f['x'], f['y'], text, s, color)


def _icon(dr, ic, st, color, bg):
    k, x, y = ic['kind'], ic['x'], ic['y']
    w, h = ic.get('w', 16), ic.get('h', 16)
    if k == 'text':
        _text(dr, x, y, ic['text'], ic.get('scale', 2), color)
    elif k == 'label_box':
        s = ic.get('scale', 2)
        dr.rectangle([x, y, x + w - 1, y + h - 1], fill=color)
        tw = text_width(ic['text'], s)
        _text(dr, x + (w - tw) // 2, y + (h - 7 * s) // 2, ic['text'], s, bg)
    elif k == 'heater':
        for i in range(3):
            xc = x + w * (0.25 + 0.25 * i)
            pts = [(xc + (3 if j % 2 else -3), y + j * (h - 8) / 6.0) for j in range(7)]
            dr.line(pts, fill=color, width=2)
        dr.rectangle([x, y + h - 4, x + w, y + h - 1], fill=color)
    elif k == 'ring':
        box = [x, y, x + w, y + h]
        for i in range(8):
            a0 = i * 45 + 6
            dr.arc(box, a0, a0 + 33, fill=color, width=max(2, w // 8))
        cx, d = x + w / 2.0, (1 if st.get('direction', 'cw') == 'cw' else -1)
        dr.polygon([(cx + d * 6, y), (cx - d * 2, y - 5), (cx - d * 2, y + 5)], fill=color)
    elif k == 'key':
        r = h / 2.0
        dr.ellipse([x, y, x + h, y + h], outline=color, width=2)
        dr.rectangle([x + h, y + r - 1, x + w, y + r + 1], fill=color)
        for tx in (x + w - 5, x + w - 1):
            dr.rectangle([tx - 1, y + r, tx, y + h], fill=color)
    elif k == 'usb':
        yc = y + h / 2.0
        dr.ellipse([x, yc - 3, x + 6, yc + 3], fill=color)
        dr.line([(x + 6, yc), (x + w - 5, yc)], fill=color, width=2)
        dr.polygon([(x + w, yc), (x + w - 6, yc - 4), (x + w - 6, yc + 4)], fill=color)
        dr.line([(x + w * 0.35, yc), (x + w * 0.5, y + 1), (x + w * 0.65, y + 1)], fill=color, width=2)
        dr.line([(x + w * 0.45, yc), (x + w * 0.6, y + h - 1), (x + w * 0.72, y + h - 1)], fill=color, width=2)
    elif k == 'probe':
        dr.line([(x + 2, y + h - 2), (x + w - 2, y + 2)], fill=color, width=max(3, w // 4))
        dr.ellipse([x, y + h - 6, x + 6, y + h], fill=color)
    elif k == 'dot':
        dr.rectangle([x, y, x + w - 1, y + h - 1], fill=color)
    elif k == 'rect':
        dr.rectangle([x, y, x + w - 1, y + h - 1], outline=color, width=2)
    elif k == 'disc':
        dr.ellipse([x, y, x + w - 1, y + h - 1], fill=color)
    elif k == 'bar':                                                          # level bar: attrs fraction 0..1
        frac = min(max(float(st.get('fraction') or 0.0), 0.0), 1.0)
        dr.rectangle([x, y, x + w - 1, y + h - 1], outline=color, width=1)
        dr.rectangle([x + 2, y + 2, x + 2 + int((w - 5) * frac), y + h - 3], fill=color)


def draw_display(d, res):
    """PIL image of one resolved display (see PanelUI.frame for the resolved structure)."""
    from PIL import Image, ImageDraw
    kind = d['kind']
    cols = d.get('colors', {})
    if kind == 'led':
        size = tuple(d.get('size', [32, 32]))
        img = Image.new('RGB', size, _rgb(cols.get('background'), (40, 40, 40)))
        dr = ImageDraw.Draw(img)
        c = ((_role(cols, res['color']) if res.get('color') else _rgb(cols.get('on'), (90, 220, 90))) if res['shown']
             else _rgb(cols.get('off'), (70, 76, 70)))
        dr.ellipse([2, 2, size[0] - 3, size[1] - 3], fill=c)
        return img
    if kind in ('segment_lcd', 'seven_segment'):
        bg, fg = _rgb(cols.get('background'), (206, 214, 200)), _rgb(cols.get('segment'), (34, 38, 42))
        ghost = _rgb(cols.get('ghost'), None) if cols.get('ghost') else None
        img = Image.new('RGB', tuple(d['size']), bg)
        dr = ImageDraw.Draw(img)
        for fid, f in d.get('fields', {}).items():
            if f.get('font') == '5x7':                                        # dot-matrix text line of a segment LCD (e.g. BINDER R4)
                _draw_dot_field(dr, f, res['fields'][fid]['shown'], fg, ghost)
            else:
                _draw_field(dr, f, res['fields'][fid]['shown'], fg, ghost)
        for iid, ic in d.get('icons', {}).items():
            st = res['icons'][iid]
            if st['shown']:
                _icon(dr, ic, st, fg, bg)
        return img
    if kind == 'char_lcd':
        cw, ch = d.get('cell', [12, 18])
        px = d.get('dot', 2)
        m = d.get('margin', 8)
        size = (m * 2 + d['cols'] * cw, m * 2 + d['rows'] * ch)
        bg, fg = _rgb(cols.get('background'), (120, 170, 60)), _rgb(cols.get('text'), (20, 40, 10))
        dim = _rgb(cols.get('ghost'), None) if cols.get('ghost') else None
        img = Image.new('RGB', size, bg)
        dr = ImageDraw.Draw(img)
        spans = res.get('inverse_spans') or []
        for r, line in enumerate(res['shown']):
            for c in range(d['cols']):
                inv = (res['inverse'][r] if r < len(res['inverse']) else False) or any(a <= c < b for a, b in (spans[r] if r < len(spans) else []))
                chx = line[c] if c < len(line) else ' '
                x0, y0 = m + c * cw, m + r * ch
                if inv:
                    dr.rectangle([x0, y0, x0 + cw - 1, y0 + ch - 1], fill=fg)
                glyph = FONT5X7.get(chx, FONT5X7['?'])
                for gx, bits in enumerate(glyph):
                    for gy in range(8):
                        on = bits >> gy & 1
                        if on or dim:
                            col = (bg if inv else fg) if on else dim
                            xx, yy = x0 + 1 + gx * (px + 0), y0 + 1 + gy * px
                            dr.rectangle([xx, yy, xx + px - 1, yy + px - 1], fill=col)
        return img
    # graphic LCD / TFT / touch TFT: text lines and flat widgets
    size = tuple(d['size'])
    mono = kind == 'graphic_lcd'
    bg = _rgb(cols.get('background'), (200, 210, 190) if mono else (245, 246, 248))
    fg = _rgb(cols.get('text'), (25, 30, 25) if mono else (30, 34, 40))
    acc = _rgb(cols.get('accent'), fg if mono else (40, 110, 200))
    panel_c = _rgb(cols.get('panel'), bg if mono else (225, 229, 235))
    img = Image.new('RGB', size, bg)
    dr = ImageDraw.Draw(img)
    s = d.get('text_scale', 2)
    lines = res.get('shown') if isinstance(res.get('shown'), list) else res.get('lines', [])
    inverse = res.get('inverse') or [False] * len(lines)
    y = 2
    spans = res.get('inverse_spans') or []
    for i, line in enumerate(lines or []):
        if inverse[i] if i < len(inverse) else False:
            dr.rectangle([0, y - 1, size[0] - 1, y + 8 * s], fill=fg)
            _text(dr, 2, y, line, s, bg)
        else:
            _text(dr, 2, y, line, s, fg)
            for a, b in (spans[i] if i < len(spans) else []):                 # inverted parts (text-dialog function bar, status field)
                dr.rectangle([2 + a * 6 * s - 1, y - 1, 2 + b * 6 * s - 1, y + 8 * s], fill=fg)
                _text(dr, 2 + a * 6 * s, y, line[a:b], s, bg)
        for a, b in ((res.get('strike_spans') or [])[i] if i < len(res.get('strike_spans') or []) else []):   # crossed-out parts
            inv = (inverse[i] if i < len(inverse) else False) or any(p <= a < q for p, q in (spans[i] if i < len(spans) else []))
            dr.rectangle([2 + a * 6 * s - 1, y + 3 * s, 2 + b * 6 * s - 2, y + 4 * s - 1], fill=bg if inv else fg)
        y += 9 * s
    if res.get('title') and res.get('header_h'):                              # title bar of a templated page
        dr.rectangle([0, 0, size[0] - 1, res['header_h'] - 1], fill=_role(cols, 'header', acc))
        _text_box(dr, [6, 0, size[0] - 12, res['header_h']], res['title'], s, _role(cols, 'header_text', bg))
    elif res.get('title'):
        dr.rectangle([0, 0, size[0] - 1, 10 * s], fill=acc)
        _text(dr, 4, s, res['title'], s, bg if not mono else bg)
    for w in res.get('widgets', []):
        x0, y0, ww, hh = w['rect']
        t = w['type']
        enabled = w.get('enabled', True)
        tc = fg if enabled else _rgb(cols.get('disabled'), (150, 150, 150))
        if _draw_touch_widget(dr, w, s, cols, fg, bg, acc, panel_c):
            pass
        elif t == 'button':
            dr.rectangle([x0, y0, x0 + ww - 1, y0 + hh - 1], fill=panel_c, outline=tc, width=1)
            _text(dr, x0 + (ww - text_width(w['text'], s)) // 2, y0 + (hh - 7 * s) // 2, w['text'], s, tc)
        elif t == 'toggle':
            on = bool(w.get('value'))
            _text(dr, x0 + 2, y0 + (hh - 7 * s) // 2, w['text'], s, tc)
            sw = [x0 + ww - 4 * 7 * s // 2 - 4, y0 + 4, x0 + ww - 4, y0 + hh - 5]
            dr.rectangle(sw, fill=acc if on else panel_c, outline=tc)
            kx = sw[2] - (sw[3] - sw[1]) if on else sw[0]
            dr.rectangle([kx, sw[1], kx + (sw[3] - sw[1]), sw[3]], fill=bg, outline=tc)
        elif t in ('number', 'text', 'choice'):
            _text(dr, x0 + 2, y0 + 2, w['text'], s, tc)
            dr.rectangle([x0, y0 + 9 * s, x0 + ww - 1, y0 + hh - 1], fill=bg, outline=tc)
            _text(dr, x0 + 4, y0 + 9 * s + (hh - 9 * s - 7 * s) // 2, w.get('value_text', ''), s, tc)
        elif t == 'slider':
            frac = w.get('fraction') or 0.0
            yc = y0 + hh // 2
            dr.rectangle([x0, yc - 2, x0 + ww - 1, yc + 2], fill=panel_c, outline=tc)
            dr.rectangle([x0, yc - 2, x0 + int((ww - 1) * frac), yc + 2], fill=acc)
            kx = x0 + int((ww - 1) * frac)
            dr.ellipse([kx - hh // 3, yc - hh // 3, kx + hh // 3, yc + hh // 3], fill=bg, outline=tc, width=2)
        elif t == 'list':
            row_h = max(1, hh // max(1, len(w.get('rows', [])) or 1))
            for j, r in enumerate(w.get('rows', [])):
                dr.line([(x0, y0 + (j + 1) * row_h - 1), (x0 + ww - 1, y0 + (j + 1) * row_h - 1)], fill=panel_c)
                _text(dr, x0 + 4, y0 + j * row_h + (row_h - 7 * s) // 2, r, s, tc)
        else:
            _text(dr, x0, y0, w['text'], s, tc)
        if w.get('focus'):
            dr.rectangle([x0 - 2, y0 - 2, x0 + ww + 1, y0 + hh + 1], outline=acc, width=2)
    for o in res.get('overlays', []):
        _draw_overlay2(dr, o, s, cols, fg, bg, panel_c)
    ov = res.get('overlay')
    if ov and 'keys' in ov:
        kx, ky, kw, kh = ov['rect']
        dr.rectangle([kx, ky, kx + kw - 1, ky + kh - 1], fill=panel_c, outline=fg, width=2)
        head = (ov.get('title') or '') + (': ' + (ov['buffer'] or '') + '_' if ov['kind'] == 'keypad' else '')
        _text(dr, kx + 6, ky + 6, head, s, fg)
        if ov.get('error'):
            _text(dr, kx + kw - 6 - text_width(ov['error'], s), ky + 6, ov['error'], s, (200, 40, 40) if not mono else fg)
        keys = ov['keys']
        top = ky + 30
        for r, row in enumerate(keys):
            rh = (ky + kh - top) // len(keys)
            for c, key in enumerate(row):
                cw = kw // len(row)
                bx, by = kx + c * cw, top + r * rh
                dr.rectangle([bx + 2, by + 2, bx + cw - 3, by + rh - 3], fill=bg, outline=fg)
                _text(dr, bx + (cw - text_width(key, s)) // 2, by + (rh - 7 * s) // 2, key, s, fg)
    return img


def _blanked(chars):
    """Characters of a field in the off phase of its blink: flashing characters go dark (cells blank, their points and colons off); the
    characters that do not flash stay (e.g. the colons between a flashing hh and the steady mm)."""
    out = []
    for c, b in chars:
        if b:
            out.append(('' if c in '.:' else ' ', False))
        else:
            out.append((c, False))
    return [(c, b) for c, b in out if c != '']


# ---------------------------------------------------------------- touch toolkit: shared constants, date / time pickers, drawing
TOUCH_LAYOUT = dict(header_h=40, action_h=52, margin=6, gap=4, row_h=40, sidebar_w=190)    # page layout conventions (touch_layout overrides)
TEMPLATE_PARTS = ('header', 'sidebar', 'content', 'actions')
FIELD_TYPES = ('enum', 'bool', 'number', 'text', 'password', 'date', 'time', 'multi', 'info', 'link', 'action')
TOUCH_ACTIONS = ('form_open', 'form_save', 'form_cancel', 'form_set', 'dialog', 'dialog_close', 'overlay_close', 'home', 'back', 'form_edit', 'scroll')
KEY_LABELS = {'{bksp}': 'Del', '{shift}': 'Shift', '{sym}': '12@', '{abc}': 'abc', '{space}': 'Space', '{ok}': 'OK', '{enter}': 'Enter',
              '{cancel}': 'Cancel', '{clear}': 'C'}
KEYBOARDS = {                                                                # spec 'keyboards' may add or replace layouts (same structure)
    'alpha': {'height': 0.45, 'modes': {
        'lower': [list('qwertyuiop') + ['{bksp}'], ['{shift}'] + list('asdfghjkl') + ['{cancel}'], ['{sym}'] + list('zxcvbnm') + ['.', '-', ['{space}', 2], '{ok}']],
        'upper': [list('QWERTYUIOP') + ['{bksp}'], ['{shift}'] + list('ASDFGHJKL') + ['{cancel}'], ['{sym}'] + list('ZXCVBNM') + ['.', '-', ['{space}', 2], '{ok}']],
        'sym': [list('1234567890') + ['{bksp}'], ['$', '@', '#', '%', '&', '/', '(', ')', ':', ';', '{cancel}'],
                ['{abc}', '_', '+', '=', "'", '"', '!', '?', ',', ['{space}', 2], '{ok}']]}},
    'numeric': {'height': 0.5, 'modes': {'num': [['7', '8', '9', '{bksp}'], ['4', '5', '6', '{clear}'], ['1', '2', '3', '{cancel}'], ['-', '0', '.', '{ok}']]}},
}
ROLE_COLORS = dict(background=(245, 246, 248), text=(30, 34, 40), accent=(40, 110, 200), panel=(225, 229, 235), disabled=(150, 150, 150),
                   header=(40, 110, 200), header_text=(255, 255, 255), selection=(40, 110, 200), selection_text=(255, 255, 255), dialog=(255, 255, 255),
                   ok=(40, 160, 70), warn=(225, 165, 0), error=(200, 40, 40), muted=(110, 116, 126), primary=(70, 175, 60), danger=(200, 40, 40),
                   green=(40, 170, 60), yellow=(240, 190, 0), red=(210, 40, 40), blue=(40, 110, 200), white=(255, 255, 255), black=(0, 0, 0),
                   neutral=(150, 150, 150))
_MONTHS = ('January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December')


def _page_defs(page):
    """Every widget definition of a page: absolute 'widgets', then header, sidebar, content (rows flattened) and action-bar widgets."""
    out = list(page.get('widgets', [])) + list(page.get('header', [])) + list(page.get('sidebar', [])) + list(page.get('actions', []))
    for e in page.get('content', []):
        out += e['row'] if isinstance(e.get('row'), list) else [e]
    return out


def _dig(v, path):
    if path is True or path is None:
        return v
    for k in (path if isinstance(path, list) else str(path).split('.')):
        k = str(k)
        if isinstance(v, dict):
            v = v.get(k)
        elif isinstance(v, (list, tuple)) and k.lstrip('-').isdigit() and -len(v) <= int(k) < len(v):
            v = v[int(k)]
        else:
            return None
    return v


def _keyed(v, key, field='id'):
    """The element of an entity collection (dict by id, or list of dicts) whose id is key."""
    if isinstance(v, dict):
        return v.get(key, v.get(str(key)))
    if isinstance(v, (list, tuple)):
        return next((x for x in v if isinstance(x, dict) and x.get(field) == key), None)
    return None


def _days_in_month(y, m):
    if m == 2:
        return 29 if (y % 4 == 0 and y % 100 != 0) or y % 400 == 0 else 28
    return 30 if m in (4, 6, 9, 11) else 31


def _parse_datetime(t, v):
    try:
        if t == 'date':
            y, m, d = (int(x) for x in str(v).split('-'))
            return [d, m, y]
        h, mi = (int(x) for x in str(v).split(':')[:2])
        return [h, mi]
    except (ValueError, TypeError):
        return [1, 1, 2026] if t == 'date' else [0, 0]


def _spinner_columns(t, p):
    if t == 'date':
        return [('day', 1, _days_in_month(p[2], p[1]), f'{p[0]:02d}'), ('month', 1, 12, _MONTHS[p[1] - 1]), ('year', 2000, 2099, str(p[2]))]
    return [('hour', 0, 23, f'{p[0]:02d}'), ('minute', 0, 59, f'{p[1]:02d}')]


def _spin(t, parts, i, n):
    """+ / - on one column of a date or time picker (wraps; the day is clamped to the month)."""
    p = list(parts)
    _, lo, hi, _ = _spinner_columns(t, p)[i]
    p[i] = lo + (p[i] - lo + n) % (hi - lo + 1)
    if t == 'date':
        p[0] = min(p[0], _days_in_month(p[2], p[1]))
    return p


def _join_datetime(t, p):
    return f'{p[2]:04d}-{p[1]:02d}-{p[0]:02d}' if t == 'date' else f'{p[0]:02d}:{p[1]:02d}'


def _format_datetime(v, fmt):
    """ISO date 'YYYY-MM-DD[THH:MM[:SS]]' / time 'HH:MM[:SS]' by pattern tokens YYYY MMMM MMM MM M DD D (date), HH hh mm ss A (time)."""
    import re
    s = str(v)
    date, _, time = s.partition('T') if 'T' in s else ((s, '', '') if '-' in s else ('', '', s))
    try:
        y, mo, d = (int(x) for x in date.split('-')) if date else (2000, 1, 1)
        hms = [int(x) for x in time.split(':')] if time else [0, 0, 0]
    except ValueError:
        return s
    h, mi, se = (hms + [0, 0, 0])[:3]
    tok = dict(YYYY=f'{y:04d}', MMMM=_MONTHS[mo - 1], MMM=_MONTHS[mo - 1][:3], MM=f'{mo:02d}', M=str(mo), DD=f'{d:02d}', D=str(d), HH=f'{h:02d}',
               hh=f'{(h % 12) or 12:02d}', mm=f'{mi:02d}', ss=f'{se:02d}', A='AM' if h < 12 else 'PM')
    out = []
    for pat in (fmt.get('date'), fmt.get('time')):
        if pat:
            out.append(''.join(tok.get(t, t) for t in re.findall(r'YYYY|MMMM|MMM|MM|M|DD|D|HH|hh|mm|ss|A|.', pat)))
    return ' '.join(out)


def _glyph(ch):
    """5x7 glyph of a character; accented letters fall back to their base letter, anything else outside the font to '?'."""
    g = FONT5X7.get(ch)
    if g is None:
        import unicodedata
        g = FONT5X7.get(unicodedata.normalize('NFKD', ch)[:1], FONT5X7['?'])
    return g


def _role(cols, name, default=None):
    if isinstance(name, (list, tuple)):
        return tuple(name)
    if name in cols:
        return tuple(cols[name])
    return ROLE_COLORS.get(name, default if default is not None else ROLE_COLORS['text'])


def _dim(img, pct):
    """Screen brightness (percent of full) applied to a rendered image."""
    if pct is None or pct >= 100:
        return img
    f = max(0.0, float(pct)) / 100.0
    return img.point(lambda v: int(v * f))


def _text_box(dr, rect, text, scale, color, align='left', valign='center', pad=4, wrap=False):
    """Text inside a rect: lines split at newlines (and wrapped at blanks when wrap), clipped to the width, aligned left / center / right."""
    x0, y0, w, h = rect
    n = max(1, (w - 2 * pad + scale) // (6 * scale))
    lines = []
    for para in str(text).split('\n'):
        if not wrap or len(para) <= n:
            lines.append(para)
            continue
        cur = ''
        for word in para.split(' '):
            if cur and len(cur) + 1 + len(word) > n:
                lines.append(cur)
                cur = word
            else:
                cur = (cur + ' ' + word) if cur else word
        lines.append(cur)
    lh = 9 * scale
    y = y0 + (h - lh * len(lines) + 2 * scale) // 2 if valign == 'center' else y0 + pad
    for line in lines:
        line = line[:n]
        tw = text_width(line, scale)
        x = x0 + pad if align == 'left' else x0 + (w - tw) // 2 if align == 'center' else x0 + w - pad - tw
        _text(dr, x, y, line, scale, color)
        y += lh


def _draw_rows(dr, w, s, cols, fg, bg, panel_c):
    x0, y0, ww, hh = w['rect']
    row_h = w.get('row_h', 40)
    col_w = w.get('col_w') or []
    for j, cells in enumerate(w.get('cells', [])):
        ry = y0 + j * row_h
        sel = j in w.get('selected_rows', [])
        dis = j in w.get('disabled_rows', [])
        if sel:
            dr.rectangle([x0, ry, x0 + ww - 1, ry + row_h - 2], fill=_role(cols, 'selection'))
        dr.line([(x0, ry + row_h - 1), (x0 + ww - 1, ry + row_h - 1)], fill=panel_c)
        tc = _role(cols, 'selection_text') if sel else _role(cols, 'disabled') if dis else _role(cols, w['color']) if w.get('color') else fg   # color: rows on a dark area
        fr = col_w if len(col_w) == len(cells) else [1.0 / len(cells)] * len(cells)
        x = x0
        for c, f in zip(cells, fr):
            cw = int(ww * f)
            _text_box(dr, [x, ry, cw, row_h], c, s, tc, align='left')
            x += cw
    if not w.get('cells') and w.get('empty_text'):
        _text_box(dr, [x0, y0, ww, row_h], w['empty_text'], s, _role(cols, 'muted'))
    n = max(1, hh // row_h)
    if w.get('count', 0) > n:                                                 # scroll bar
        frac0, frac1 = w.get('scroll', 0) / w['count'], min(1.0, (w.get('scroll', 0) + n) / w['count'])
        dr.rectangle([x0 + ww - 4, y0 + int(hh * frac0), x0 + ww - 1, y0 + int(hh * frac1) - 1], fill=_role(cols, 'muted'))


def _draw_touch_widget(dr, w, s, cols, fg, bg, acc, panel_c):
    """Toolkit widgets (lamp, gauge, xy, dynamic lists, form rows) and the style / scale / align / fill / selected / color options of labels and
    buttons.  Returns False for widgets the classic drawing handles."""
    t = w['type']
    if t == 'plot':
        _draw_plot(dr, w, s, cols, fg, bg, panel_c)
        return True
    if t == 'image':
        _draw_image(dr, w, cols, fg)
        return True
    if not (t in ('lamp', 'gauge', 'xy', 'form') or (t == 'list' and 'cells' in w) or
            any(k in w for k in ('style', 'scale', 'align', 'fill', 'selected', 'color', 'wrap', 'valign'))):
        return False
    x0, y0, ww, hh = w['rect']
    enabled = w.get('enabled', True)
    sc = w.get('scale') or s
    style = w.get('style')
    tc = fg if enabled else _role(cols, 'disabled')
    if t in ('list', 'form'):
        _draw_rows(dr, w, s, cols, fg, bg, panel_c)
    elif t == 'button':
        filled = w.get('selected') or style in ('primary', 'accent', 'danger', 'ok', 'warn', 'error')
        fill = _role(cols, 'selection') if w.get('selected') else _role(cols, style) if filled else _role(cols, w['fill']) if w.get('fill') else panel_c
        if not enabled:
            fill = panel_c
        dr.rectangle([x0, y0, x0 + ww - 1, y0 + hh - 1], fill=fill, outline=_role(cols, 'muted') if not filled else fill)
        txt = _role(cols, 'selection_text') if (filled and enabled) else tc
        _text_box(dr, w['rect'], w.get('text', ''), sc, txt, align=w.get('align', 'center'))
    elif t == 'lamp':
        d = min(hh, ww) - 4
        dr.ellipse([x0 + 2, y0 + (hh - d) // 2, x0 + 2 + d, y0 + (hh - d) // 2 + d], fill=_role(cols, w.get('color') or 'neutral'))
        if w.get('text'):
            _text_box(dr, [x0 + d + 6, y0, ww - d - 6, hh], w['text'], sc, tc)
    elif t == 'gauge':
        dr.rectangle([x0, y0, x0 + ww - 1, y0 + hh - 1], fill=panel_c, outline=_role(cols, 'muted'))
        if w.get('fraction') is not None:
            dr.rectangle([x0 + 1, y0 + 1, x0 + 1 + int((ww - 3) * w['fraction']), y0 + hh - 2], fill=_role(cols, w.get('color') or 'accent'))
        for m in w.get('marks', []):
            mx = x0 + int((ww - 1) * m)
            dr.rectangle([mx - 1, y0 - 3, mx + 1, y0 + hh + 2], fill=fg)
        if w.get('text'):
            _text_box(dr, w['rect'], w['text'], sc, fg, align=w.get('align', 'center'))
    elif t == 'xy':
        r = min(ww, hh) // 2 - 4
        cx, cy = x0 + ww // 2, y0 + hh // 2
        dr.ellipse([cx - r, cy - r, cx + r, cy + r], outline=fg, width=2)
        ro = int(r * w.get('ok_radius', 0.25))
        dr.ellipse([cx - ro, cy - ro, cx + ro, cy + ro], outline=_role(cols, 'ok'), width=2)
        dr.line([(cx - r, cy), (cx + r, cy)], fill=panel_c)
        dr.line([(cx, cy - r), (cx, cy + r)], fill=panel_c)
        if w.get('xy') is not None:
            px, py = cx + int(w['xy'][0] * (r - 6)), cy + int(w['xy'][1] * (r - 6))
            dr.ellipse([px - 6, py - 6, px + 6, py + 6], fill=_role(cols, w.get('color') or 'accent'))
    else:                                                                     # label (and other passive widgets) with options
        if w.get('fill'):
            dr.rectangle([x0, y0, x0 + ww - 1, y0 + hh - 1], fill=_role(cols, w['fill']))
        color = _role(cols, w['color']) if w.get('color') else _role(cols, style) if style else tc
        _text_box(dr, w['rect'], w.get('text', ''), sc, color, align=w.get('align', 'left'), valign=w.get('valign', 'center'), wrap=bool(w.get('wrap')))
    return True


def _image_box(rect, ent):
    """Where an image widget draws its bitmap: the largest whole-pixel scale that fits the rect, centred (x, y, w, h)."""
    x0, y0, ww, hh = rect
    pw, ph = ent.get('pw') or 1, ent.get('ph') or 1
    k = max(1, min(ww // pw, hh // ph))
    return [x0 + (ww - k * pw) // 2, y0 + (hh - k * ph) // 2, k * pw, k * ph]


def _draw_image(dr, w, cols, fg):
    """Bitmap of an image widget (PanelUI._image_entry), nearest-neighbour scaled into its rect on the 'fill' role (default black)."""
    x0, y0, ww, hh = w['rect']
    dr.rectangle([x0, y0, x0 + ww - 1, y0 + hh - 1], fill=_role(cols, w.get('fill') or 'black'))
    if not w.get('pixels'):
        return
    bx, by, bw, bh = _image_box(w['rect'], w)
    k = bw // w['pw']
    for r, row in enumerate(w['pixels']):
        for c, p in enumerate(row):
            col = tuple(p) if isinstance(p, list) else (p, p, p)
            dr.rectangle([bx + c * k, by + r * k, bx + c * k + k - 1, by + r * k + k - 1], fill=col)


def _draw_plot(dr, w, s, cols, fg, bg, panel_c):
    """Trend chart (PanelUI._plot_entry): frame, axis limits as text (left / right axis, x window below), a grid and one polyline per series."""
    x0, y0, ww, hh = w['rect']
    dr.rectangle([x0, y0, x0 + ww - 1, y0 + hh - 1], fill=_role(cols, w['fill']) if w.get('fill') else bg, outline=_role(cols, 'muted'))
    yr = w.get('y_range') or {}
    short = lambda v: f'{v:.4g}'                                                  # noqa: E731
    lw = max([len(short(v)) for v in yr.get('left', [])] + [0]) * 6 * s + 6
    rw = max([len(short(v)) for v in yr.get('right', [])] + [0]) * 6 * s + 6
    ax0, ay0, ax1, ay1 = x0 + lw, y0 + 4 * s, x0 + ww - 1 - rw, y0 + hh - 1 - 10 * s
    if ax1 - ax0 < 8 or ay1 - ay0 < 8:
        return
    for i in range(5):                                                            # grid
        gy = ay0 + (ay1 - ay0) * i // 4
        dr.line([(ax0, gy), (ax1, gy)], fill=panel_c)
        gx = ax0 + (ax1 - ax0) * i // 4
        dr.line([(gx, ay0), (gx, ay1)], fill=panel_c)
    dr.rectangle([ax0, ay0, ax1, ay1], outline=fg)
    for axis, x in (('left', x0 + 3), ('right', ax1 + 3)):
        if axis in yr:
            _text(dr, x, ay0, short(yr[axis][1]), s, fg)
            _text(dr, x, ay1 - 7 * s, short(yr[axis][0]), s, fg)
    xr = w.get('x_range') or [0.0, 1.0]
    _text(dr, ax0, ay1 + 2 * s, _format(xr[0], w.get('x_fmt') or {}), s, fg)
    last = _format(xr[1], w.get('x_fmt') or {})
    _text(dr, ax1 - text_width(last, s), ay1 + 2 * s, last, s, fg)
    dx = (xr[1] - xr[0]) or 1.0
    for ser in w.get('series', []):
        lo, hi = yr.get(ser['axis'], [0.0, 1.0])
        pts = [(ax0 + (p[0] - xr[0]) / dx * (ax1 - ax0), ay1 - (min(max(p[1], lo), hi) - lo) / ((hi - lo) or 1.0) * (ay1 - ay0)) for p in ser['points']]
        color = _role(cols, ser.get('color') or 'accent')
        draw = w.get('draw') if w.get('draw') in ('line', 'marker', 'both') else 'line'
        if len(pts) > 1 and draw != 'marker':
            dr.line(pts, fill=color, width=2)
        elif pts and draw == 'line':
            dr.rectangle([pts[0][0] - 1, pts[0][1] - 1, pts[0][0] + 1, pts[0][1] + 1], fill=color)
        if draw in ('marker', 'both'):                                        # data points shown as markers (e.g. Keithley 2450 Draw Style)
            m = 2 * s
            for px, py in pts:
                dr.rectangle([px - m, py - m, px + m, py + m], fill=color)


def _draw_overlay2(dr, ov, s, cols, fg, bg, panel_c):
    """Keyboards, choice lists, pickers, check lists and dialogs (the structure of PanelUI._ov_layout)."""
    x0, y0, w, h = ov['rect']
    bar = 9 * s + 12
    dr.rectangle([x0, y0, x0 + w - 1, y0 + h - 1], fill=_role(cols, 'dialog'), outline=fg, width=2)
    head = _role(cols, 'error') if ov.get('style') == 'error' else _role(cols, 'warn') if ov.get('style') == 'warn' else _role(cols, 'header')
    dr.rectangle([x0, y0, x0 + w - 1, y0 + bar], fill=head)
    _text_box(dr, [x0 + 4, y0, w - 8, bar], ov.get('title', ''), s, _role(cols, 'header_text'))
    if ov.get('error'):
        _text_box(dr, [x0 + 4, y0, w - 8, bar], ov['error'], s, _role(cols, 'header_text'), align='right')
    y = y0 + bar + 6
    for line in ov.get('lines', []):
        _text_box(dr, [x0 + 8, y, w - 16, 9 * s + 4], line, s, fg)
        y += 9 * s + 4
    for b in ov.get('buttons', []):
        bx, by, bw, bh = b['rect']
        st = b.get('style')
        if b['id'] == 'field':                                               # keyboard input field
            dr.rectangle([bx, by, bx + bw - 1, by + bh - 1], fill=bg, outline=fg)
            if b.get('selected') and b['label']:
                tw = text_width(b['label'][:max(1, (bw - 8) // (6 * s))], s)
                dr.rectangle([bx + 3, by + 4, bx + 6 + tw, by + bh - 5], fill=_role(cols, 'selection'))
                _text_box(dr, b['rect'], b['label'], s, _role(cols, 'selection_text'))
            else:
                _text_box(dr, b['rect'], b['label'] + ('_' if ov.get('cursor', True) else ''), s, fg)
            continue
        if st == 'value':
            _text_box(dr, b['rect'], b['label'], s, fg, align='center')
            continue
        filled = b.get('selected') or st in ('primary', 'danger', 'accent')
        fill = _role(cols, 'selection') if b.get('selected') else _role(cols, st) if filled else panel_c
        if not b.get('enabled', True) and st != 'row':
            fill = panel_c
        dr.rectangle([bx + 1, by + 1, bx + bw - 2, by + bh - 2], fill=fill, outline=_role(cols, 'muted'))
        tc = _role(cols, 'selection_text') if filled and b.get('enabled', True) else fg if b.get('enabled', True) else _role(cols, 'disabled')
        if st == 'row':
            _text_box(dr, [bx, by, bw // 2, bh], b['label'], s, tc)
            _text_box(dr, [bx + bw // 2, by, bw - bw // 2, bh], b.get('value', ''), s, tc, align='right')
        else:
            _text_box(dr, b['rect'], b['label'], s, tc, align='center')


def _check_touch(spec):
    """Spec checks of the touch toolkit: form field types, options and evidence; form, dialog and keyboard references; widget ids."""
    err = []
    forms, dialogs = spec.get('forms', {}), spec.get('dialogs', {})
    kbs = set(KEYBOARDS) | set(spec.get('keyboards', {}))
    for fid, f in forms.items():
        for i, fld in enumerate(f.get('fields', [])):
            where = f'forms.{fid}.fields[{i}]'
            if fld.get('type') not in FIELD_TYPES:
                err.append(f'{where}.type {fld.get("type")!r}')
            if fld.get('type') in ('enum', 'multi') and not fld.get('options') and not fld.get('options_src'):
                err.append(f'{where}: options missing')
            if not fld.get('evidence') and not fld.get('assumption'):
                err.append(f'{where}: evidence missing')
            if fld.get('keyboard') and fld['keyboard'] not in kbs:
                err.append(f'{where}: keyboard {fld["keyboard"]!r}')
    for did, d in dialogs.items():
        if d.get('form') and d['form'] not in forms:
            err.append(f'dialogs.{did}.form {d["form"]!r}')
    for sid, s in spec.get('screens', {}).items():
        for pid, page in ((s.get('nav') or {}).get('pages') or {}).items():
            for j, w in enumerate(_page_defs(page)):
                where = f'screens.{sid}.nav.pages.{pid}.widgets[{j}]'
                if not w.get('id'):
                    err.append(f'{where}: id missing')
                if w.get('type') == 'form' and w.get('form') not in forms:
                    err.append(f'{where}: form {w.get("form")!r}')
                if w.get('keyboard') and w['keyboard'] not in kbs:
                    err.append(f'{where}: keyboard {w["keyboard"]!r}')
    return err

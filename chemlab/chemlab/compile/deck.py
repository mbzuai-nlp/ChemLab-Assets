"""Named locations on a deck: LABWARE:WELL (plate:A1), lists (plate:A1,plate:B1), ranges (plate:A1-A6, down each column as
Opentrons orders wells), trash, home.

The registry is lab-config data (the operator's description of the deck: which labware is in which slot), turned into wells,
joint targets and capacities with the instrument's contract geometry.  It never holds liquid volumes.  The same registry is
used on both sides: the trusted host's scene registers tips and containers with the runtime from it, and the class adapter
turns names into positions with it.

Lab config entry (the liquid class's Flex):

    "deck": {"labware": [{"name": "plate", "type": "corning_96_wellplate_360ul_flat", "slot": "C2"}, ...],
             "trash": "A1", "home_m": {"x_m": 0.24, "y_m": 0.21, "z_m": 0.22}, "command_s": 1.0}

Labware types are the Opentrons load names of the pilot (chemlab/backend.py LABWARE_TYPES).
"""
import copy
import difflib
import re

from ..backend import DECK_SCALE, LABWARE_TYPES, LIMITS, ROW_NAMES, TRASH_Z_M, well_names
from .errors import invalid


class DeckError(Exception):
    pass


def _target(geometry, slot, row, col, lw):
    sx, sy, _ = geometry[slot]['position_m']
    pos = {'x_m': round(DECK_SCALE * sx + (col - (lw['cols'] - 1) / 2.0) * lw['pitch_m'], 6),
           'y_m': round(DECK_SCALE * sy - (row - (lw['rows'] - 1) / 2.0) * lw['pitch_m'], 6),
           'z_m': lw['z_m']}
    for k, (lo, hi) in LIMITS.items():
        if not lo <= pos[k] <= hi:
            raise DeckError('well %s%d of a labware on %s is outside the travel box' % (ROW_NAMES[row], col + 1, slot))
    return pos


def build_registry(deck, contract):
    """{'labware': [...], 'trash': {...}, 'home_m', 'limits_m', 'command_s'} from a deck spec and the contract geometry."""
    geometry = contract['extensions']['chemistry_lab.geometry_contract']['slots']
    labware, used = [], {}
    for spec in deck.get('labware', []):
        name, slot, ltype = spec['name'], spec['slot'], spec['type']
        if ltype not in LABWARE_TYPES:
            raise DeckError('unknown labware type %r' % ltype)
        if slot in used or geometry.get(slot, {}).get('kind') != 'working':
            raise DeckError('slot %r is taken or not a working slot' % slot)
        used[slot] = name
        lw = LABWARE_TYPES[ltype]
        entry = {'name': name, 'type': ltype, 'display': lw['display'], 'kind': lw['kind'], 'slot': slot, 'rows': lw['rows'], 'cols': lw['cols'],
                 'wells': {}}
        present = None
        if lw['kind'] == 'tiprack':
            entry['tip_capacity_m3'] = lw['tip_capacity_m3']
            p = spec.get('tips', 'all')
            present = set(well_names(lw['rows'], lw['cols'])) if p == 'all' else set(p)
        else:
            entry['well_capacity_m3'] = lw['well_capacity_m3']
        for n in well_names(lw['rows'], lw['cols']):
            row, col = ROW_NAMES.index(n[0]), int(n[1:]) - 1
            w = {'well': n, 'position_m': _target(geometry, slot, row, col, lw)}
            if lw['kind'] == 'tiprack':
                w['tip_id'] = '%s_%s' % (name, n) if n in present else None
            else:
                w['container_id'] = '%s_%s' % (name, n)
                w['capacity_m3'] = lw['well_capacity_m3']
            entry['wells'][n] = w
        labware.append(entry)
    trash_slot = deck.get('trash', 'A1')
    sx, sy, _ = geometry[trash_slot]['position_m']
    trash = {'slot': trash_slot, 'position_m': {'x_m': round(DECK_SCALE * sx, 6), 'y_m': round(DECK_SCALE * sy, 6), 'z_m': TRASH_Z_M}}
    return {'labware': labware, 'trash': trash, 'home_m': dict(deck.get('home_m') or {'x_m': 0.24, 'y_m': 0.21, 'z_m': 0.22}),
            'limits_m': copy.deepcopy(LIMITS), 'command_s': float(deck.get('command_s', 1.0))}


class Loc:
    def __init__(self, kind, name, position, labware=None, well=None, container_id=None, capacity_m3=None, slot=None):
        self.kind, self.name, self.position = kind, name, position
        self.labware, self.well, self.container_id, self.capacity_m3, self.slot = labware, well, container_id, capacity_m3, slot


class Deck:
    """Name -> location lookups on a registry (agent side; no volumes)."""
    KINDS = {'liquid': 'a well of a reservoir, plate or tube rack (e.g. plate:A1)', 'tip': 'a tip-rack well (e.g. tiprack1000:A1)',
             'trash': 'trash', 'home': 'home'}

    def __init__(self, registry, inst='flex'):
        self.reg, self.inst = registry, inst
        self.labware = {lw['name']: lw for lw in registry['labware']}
        self.by_id = {}
        for lw in registry['labware']:
            for w in lw['wells'].values():
                for key in ('tip_id', 'container_id'):
                    if w.get(key):
                        self.by_id[w[key]] = (lw['name'], w['well'])

    def well_span(self, name):
        lw = self.labware[name]
        return 'A1-%s%d' % (ROW_NAMES[lw['rows'] - 1], lw['cols'])

    def parse(self, text, allow, what='location'):
        t = (text or '').strip()
        wanted = ' or '.join(self.KINDS[k] for k in allow)
        if t in ('trash', 'home'):
            if t not in allow:
                raise invalid('WRONG_LOCATION_KIND', '%s cannot be %s here.' % (what, t), 'give %s.' % wanted)
            if t == 'trash':
                return Loc('trash', 'trash', self.reg['trash']['position_m'], slot=self.reg['trash']['slot'])
            return Loc('home', 'home', self.reg['home_m'])
        name, sep, well = t.partition(':')
        names = ', '.join(sorted(self.labware))
        if not sep:
            if t in self.labware:
                raise invalid('WELL_MISSING', '%r is a labware; name one of its wells.' % t, 'write %s:A1 (wells %s).' % (t, self.well_span(t)))
            raise invalid('UNKNOWN_LOCATION', 'unknown %s %r.' % (what, t), 'locations are LABWARE:WELL, trash or home; labware here: %s.' % names)
        lw = self.labware.get(name)
        if lw is None:
            close = difflib.get_close_matches(name, list(self.labware), 1)
            raise invalid('UNKNOWN_LABWARE', 'there is no labware %r on the deck.' % name,
                          ('did you mean %s? ' % close[0] if close else '') + 'labware here: %s (see `chemlab run %s labware`).' % (names, self.inst))
        w = lw['wells'].get(well.upper())
        if w is None:
            raise invalid('UNKNOWN_WELL', '%s has no well %r.' % (name, well), '%s has wells %s.' % (name, self.well_span(name)))
        kind = 'tip' if lw['kind'] == 'tiprack' else 'liquid'
        if kind not in allow:
            raise invalid('WRONG_LOCATION_KIND', '%s:%s is %s, but %s must be %s.' % (name, w['well'], self.KINDS[kind].split(' (')[0], what, wanted),
                          'give %s.' % wanted)
        return Loc(kind, '%s:%s' % (name, w['well']), w['position_m'], labware=lw, well=w['well'], container_id=w.get('container_id'),
                   capacity_m3=w.get('capacity_m3'), slot=lw['slot'])

    def parse_many(self, text, allow, what):
        out = []
        for part in [p for p in (text or '').split(',') if p.strip()]:
            m = re.fullmatch(r'\s*([A-Za-z0-9_.-]+):([A-Ha-h])(\d{1,2})-([A-Ha-h])(\d{1,2})\s*', part)
            if m:
                lw, r1, c1, r2, c2 = m.group(1), ROW_NAMES.index(m.group(2).upper()), int(m.group(3)), ROW_NAMES.index(m.group(4).upper()), int(m.group(5))
                for c in range(min(c1, c2), max(c1, c2) + 1):                   # Opentrons order: down each column
                    for r in range(min(r1, r2), max(r1, r2) + 1):
                        out.append(self.parse('%s:%s%d' % (lw, ROW_NAMES[r], c), allow, what))
            else:
                out.append(self.parse(part, allow, what))
        if not out:
            raise invalid('MISSING_VALUE', '%s is empty.' % what, 'give one or more locations, e.g. plate:A1,plate:A2 or plate:A1-A6.')
        return out

    def name_of_id(self, object_id):
        loc = self.by_id.get(object_id)
        return '%s:%s' % loc if loc else None

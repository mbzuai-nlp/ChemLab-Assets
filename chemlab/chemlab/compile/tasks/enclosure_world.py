"""Host-side world for the enclosure class (what the lab operator and the building provide), as lab-config entries.

* Inert PureLab HE glovebox: the trusted glovebox scene (scenes/glovebox.py) - the shared lab vacuum line on the antechamber,
  the antechamber cycle end points (evacuate to 3 mbar, refill to 950 mbar), the box at +8 mbar gauge with 0.8 ppm O2 and
  0.5 ppm H2O, and the antechamber pressure model.  The default lab has none of this (no readings, no antechamber cycle).
* Esco biosafety cabinet and fume hood: the room / exhaust declarations are already in all203.json (plant.configure,
  exhaust.configure); nothing else is needed.
"""
import copy

from ...labbackend import load_config

GLOVEBOX = {'o2_ppm': 0.8, 'h2o_ppm': 0.5, 'box_gauge_pa': 800.0, 'evacuation_pa': 300.0, 'backfill_pa': 95000.0,
            'vacuum_source': 'lab_vacuum_line'}


def config(base=None, glovebox=None):
    """all203.json (or ``base``) with the enclosure world added."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug == 'inert_purelab_he_2gb':
            e['scene'] = 'chemlab.compile.scenes.glovebox:GloveboxScene'
            e['glovebox'] = dict(GLOVEBOX, **(glovebox or {}))
    return cfg

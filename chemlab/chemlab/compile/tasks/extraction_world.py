"""Host-side world for the extraction class (what the lab has done before the agent starts), as lab-config set-up steps.

* BUCHI SpeedExtractor: "loaded" = the lab's method, the heating block preheated to it (15 min), shield up, drawer out, the
  sample cell inserted (evaluator sample.configure), drawer in, shield down.  "empty" = the release bring-up only.
* Biotage Extrahera: the external vacuum source connected, vacuum / compressed air / nitrogen ready, solvent reservoirs filled
  (positions load / wash / elute and inlets S1-S3; host initialize_liquids) and the deck configured by the lab (cartridges).
* SFT-110: "loaded" = method, vessel opened, the sample in the vessel, closed, then switched on (the oven heats to the method
  temperature); "empty" = the release bring-up (method, power on, no sample).
* Syrris Asia FLLEX: "loaded" = tray out / separator open, membrane fitted, the lab's method, closed, the feeds connected (sample);
  "empty" = the membrane only.
* Sartorius Vivaflow 200: "loaded" = cassette, method, the feed attached; "empty" = the cassette only.
* PCCell ED 64002: "loaded" = membranes, method, the loops filled (sample); "empty" = the membranes only.
"""
import copy

from ...labbackend import load_config


def with_copies(base=None, copies=None):
    """A deep copy of the config with extra entries {new id: slug} (same asset type, own id; adapters match by asset type)."""
    cfg = copy.deepcopy(base or load_config())
    for new, slug in (copies or {}).items():
        src = next(e for e in cfg['instruments'] if e['asset_type_id'] == 'instrument.' + slug)
        e = copy.deepcopy(src)
        e['id'] = new
        e.pop('instance_id', None)
        cfg['instruments'].append(e)
    return cfg

EV = 'task world (extraction): set up by the lab before the agent starts'
EVAL = {'evaluator': True}


def step(cap, args='contract_example', evaluator=True):
    return dict({'capability': cap, 'arguments': args}, **(EVAL if evaluator else {}))


SPEEDEX = [step('method.configure', evaluator=False), step('thermal.preheat', {}, evaluator=False), {'advance_s': 900.0},
           step('shield.set_lift', {'lift_m': 0.18}, False), step('drawer.set_extension', {'extension_m': 0.16}, False),
           step('sample.configure'), step('drawer.set_extension', {'extension_m': 0.0}, False), step('shield.set_lift', {'lift_m': 0.0}, False)]


def extrahera(iid):
    return [{'host': 'connect_vacuum_source', 'kwargs': {'instance_id': iid, 'source_instance_id': 'vacuum_line_1'}},
            {'host': 'set_utilities', 'kwargs': {'instance_id': iid, 'vacuum_ready': True, 'compressed_air_ready': True, 'nitrogen_ready': True,
                                                 'evidence': EV + ': vacuum, air and nitrogen supplies open'}},
            {'host': 'initialize_liquids', 'kwargs': {'instance_id': iid, 'sources_m3': {'load': 5e-05, 'wash': 5e-05, 'elute': 5e-05, 'S1': 2e-04,
                                                                                          'S2': 2e-04, 'S3': 2e-04}, 'evidence': EV + ': reservoirs filled'}},
            step('deck.configure', {'format': 'cartridges24', 'cartridge_volume_m3': 3e-06, 'collection_plate': 'collection_a',
                                    'solvent_positions': ['load', 'wash', 'elute']}, False)]


SFT_LOADED = [step('plant.configure'), step('method.configure'), step('door.set_angle', {'angle_rad': 1.2}), step('cap.set_lift', {'lift_m': 0.07}),
              step('sample.configure'), step('cap.set_lift', {'lift_m': 0.0}), step('door.set_angle', {'angle_rad': 0.0}),
              {'capability': 'panel.switch', 'arguments': {'input_id': 'power_switch', 'position': 'on'}}, {'advance_s': 5.0}]
SYRRIS_MEMBRANE = [step('tray.set_extension'), step('separator.set_opening'), step('membrane.configure'),
                   step('separator.set_opening', {'opening_m': 0.0}), step('tray.set_extension', {'extension_m': 0.0})]
SYRRIS_LOADED = [step('tray.set_extension'), step('separator.set_opening'), step('membrane.configure'), step('method.configure'),
                 step('separator.set_opening', {'opening_m': 0.0}), step('tray.set_extension', {'extension_m': 0.0}), step('sample.configure')]
SARTORIUS = {'loaded': [step('cassette.configure'), step('method.configure'), step('sample.configure')], 'empty': [step('cassette.configure')]}
PCCELL = {'loaded': [step('membrane.configure'), step('method.configure'), step('sample.configure')], 'empty': [step('membrane.configure')]}


def config(base=None, variants=None, copies=None):
    """all203.json (or ``base``) with the extraction world added; ``variants``: {entry id or slug: 'loaded' | 'empty'} (default
    loaded); ``copies``: {new id: slug} adds more entries of a member (e.g. an "empty" one beside the loaded one)."""
    cfg = with_copies(base, copies)
    variants = variants or {}
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        iid = e.get('instance_id') or e['id']
        v = variants.get(e['id'], variants.get(slug, 'loaded'))
        if v == 'empty':
            e['default_world'] = False        # the empty variant: without the default lab's loaded state
        setup = list(e.get('setup') or [])
        if slug == 'buchi_speedextractore' and v == 'loaded':
            setup += SPEEDEX
        elif slug == 'biotage_extrahera_classic':
            setup += extrahera(iid)
        elif slug == 'sft_sft110' and v == 'loaded':
            setup = SFT_LOADED
        elif slug == 'syrris_asiafllex':
            setup += SYRRIS_LOADED if v == 'loaded' else SYRRIS_MEMBRANE
        elif slug == 'sartorius_vivaflow200':
            setup += SARTORIUS[v]
        elif slug == 'pccell_ed64002':
            setup += PCCELL[v]
        e['setup'] = setup
    return cfg

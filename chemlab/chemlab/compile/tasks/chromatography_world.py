"""Host-side world for the chromatography class (what the lab operator has done before the agent starts), as lab-config set-up
steps.  The default lab leaves most separation instruments without a sample, a column, a vial or gas; these steps provide them.

* 8890 GC: gas cylinders on (host set_gas_supply) and a registered sample vial "mix1" (host register_sample: fixture peaks).
* 1260 LC: a registered sample "mix1" (host register_sample).
* 1260 SFC: CO2 supply, modifier bottle B1, a column at position 1, a vial at drawer 1 position 1 (evaluator), modules on and the
  CO2 chiller cold (15 min of lab time).
* 1290 GPC: powered, a method and the polymer standard vial (the runtime takes a vial only with a method and the drawer open).
* Eclipse AF4: a blind sample "unknown_1" provided by the evaluator; switched on.
* PA 800: switched on, buffer vials (borate) at BI/BO A1 and B1, sample vial SI:A1 (benzoic acid), cartridge installed.
* TLC Scanner 4: switched on, a developed plate (two tracks of two substances) placed on the stage, door closed.
* Selekt: rack, column and sample of channel 1 (evaluator).
* Mini-PROTEAN: lid off and leads unplugged, gel "gel_a" (comb out) at position 1, buffer dam at 2, buffers filled, sample "lysate_1"
  prepared on the bench.
The GC 7890B, IC, GC-IMS and CPC are ready in the default lab (gases, method, sample).
"""
import copy

from ...labbackend import load_config

E = lambda cap, args='contract_example': {'capability': cap, 'arguments': args, 'evaluator': True}   # noqa: E731
A = lambda cap, args=None: {'capability': cap, 'arguments': {} if args is None else args}            # noqa: E731
BUFFER = {'boric_acid': 50.0, 'sodium_ion': 25.0}


def _vial(pos, comp, label, vol=1.5e-6):
    return E('vial.place', {'position': pos, 'volume_m3': vol, 'composition_mol_m3': comp, 'label': label})


WORLD = {
    'agilent_gc8890b': ([{'host': 'set_gas_supply', 'kwargs': {'carrier': True, 'hydrogen': True, 'air': True, 'makeup': True}}],
                        [{'host': 'register_sample', 'kwargs': {'sample_id': 'mix1'}}]),
    'agilent_hplc1260': ([], [{'host': 'register_sample', 'kwargs': {'sample_id': 'mix1'}}]),
    'agilent_1260infinity': ([E('supply.configure'), E('solvent.configure'), E('mct_door.set', {'angle_rad': 1.5}), E('column.install'),
                              E('mct_door.set', {'angle_rad': 0.0}), E('drawer_1.set', {'position_m': 0.3}), E('sample.register_vial'),
                              E('drawer_1.set', {'position_m': 0.0}), A('power.set', {'enabled': True}), {'advance_s': 900.0}], []),
    'agilent_1290infinity': ([], [A('power.set', {'enabled': True}), A('method.configure', 'contract_example'),
                                  A('drawer.set', {'position_m': 0.2}), E('sample.load'), A('drawer.set', {'position_m': 0.0})]),
    'wyatt_eclipseaf4': ([], [E('sample.provide'), A('power.set', {'on': True})]),
    'sciex_pa800': ([A('power.set', {'enabled': True}), A('sample_cover.set_position', {'angle_rad': 1.5707963267948966}),
                     _vial('BI:A1', BUFFER, 'rinse buffer'), _vial('BO:A1', BUFFER, 'waste', 6e-7), _vial('BI:B1', BUFFER, 'run buffer in'),
                     _vial('BO:B1', BUFFER, 'run buffer out', 1e-6), _vial('SI:A1', {'benzoic_acid': 0.5}, 'sample 1'),
                     A('cartridge_cover.set_position', {'angle_rad': 1.4}), A('insertion_bar.set_position', {'angle_rad': 1.0}),
                     A('cartridge.install', 'contract_example'), A('insertion_bar.set_position', {'angle_rad': 0.0}),
                     A('cartridge_cover.set_position', {'angle_rad': 0.0}), A('sample_cover.set_position', {'angle_rad': 0.0})], []),
    'camag_tlcscanner': ([], [A('power.set', {'enabled': True}), A('door.set', {'opened': True}), {'advance_s': 2.0}, A('stage.load'),
                              {'advance_s': 15.0}, E('plate.register'), E('plate.place', {}), A('stage.home'), {'advance_s': 15.0},
                              A('door.set', {'opened': False}), {'advance_s': 5.0}]),
    'biotage_selekt': ([], [E('tray.set_extension', {'extension_m': 0.12}), E('rack.configure'), E('tray.set_extension', {'extension_m': 0.0}),
                            E('column.configure'), E('sample.configure')]),
    'biorad_miniprotean': ([], [A('leads.disconnect'), A('lid.remove'),
                                A('gel.provide', {'gel_id': 'gel_a', 'gel_type': 'tgx_4_20_10w_50ul'}),
                                A('gel.prepare', {'gel_id': 'gel_a', 'remove_comb': False, 'remove_tape': True, 'rinse_wells': False}),
                                A('gel.install', {'item': 'gel_a', 'position': 1}), A('gel.install', {'item': 'buffer_dam', 'position': 2}),
                                A('gel.prepare', {'gel_id': 'gel_a', 'remove_comb': True, 'remove_tape': False, 'rinse_wells': True}),
                                A('buffer.fill', {'chamber': 'inner', 'buffer_id': 'tris_glycine_sds_1x', 'volume_m3': 0.00015, 'temperature_k': 295.15}),
                                A('buffer.fill', {'chamber': 'outer', 'buffer_id': 'tris_glycine_sds_1x', 'volume_m3': 0.0006, 'temperature_k': 295.15}),
                                A('sample.prepare', {'sample_id': 'lysate_1', 'proteins_kg': {'bsa': 2e-09, 'ovalbumin': 2e-09, 'lysozyme_hen': 1e-09},
                                                     'marker': None, 'marker_volume_m3': 0.0, 'sample_buffer': 'laemmli_2x_reducing',
                                                     'heat': '95C_5min', 'volume_m3': 2e-08})]),
}


def config(base=None, only=None):
    """all203.json (or ``base``) with the chromatography world steps added (before / after each entry's own set-up)."""
    cfg = copy.deepcopy(base or load_config())
    for e in cfg['instruments']:
        slug = e['asset_type_id'].split('.', 1)[1]
        if slug in WORLD and (only is None or slug in only):
            pre, post = WORLD[slug]
            steps = []
            for st in pre + post:
                st = copy.deepcopy(st)
                if 'host' in st and 'instance_id' in st.get('kwargs', {}):
                    st['kwargs']['instance_id'] = e.get('instance_id') or e['id']
                steps.append(st)
            e['setup'] = steps[:len(pre)] + list(e.get('setup') or []) + steps[len(pre):]
    return cfg

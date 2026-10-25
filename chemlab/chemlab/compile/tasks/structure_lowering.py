"""Lowering table of the structure class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a usage error, an unsupported verb, a dry run); '#wait N' = lab time passes on both labs.  World:
tasks/structure_world.py (samples on the benches, the MiniFlex scene with its chiller and powder holders)."""
import math

from .structure_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

DEG = math.pi / 180.0
SURVEY = {'name': 'survey', 'start_be_ev': 0.0, 'end_be_ev': 1200.0, 'step_ev': 1.0, 'dwell_s': 0.1, 'sweeps': 1}
C1S = {'name': 'C1s', 'start_be_ev': 280.0, 'end_be_ev': 296.0, 'step_ev': 0.1, 'dwell_s': 0.1, 'sweeps': 2}

CASES = {
    'rigaku_miniflex600_external_cooling': {'steps': [
        ('status', []),
        ('scan', []),                                                           # NO_SAMPLE
        ('load quartz_holder --dry-run', []),
        ('load quartz_holder', [('access.set_door_angle', {'angle_rad': 1.5}), ('slot.set_sample', {'connection_id': 'quartz_holder'}),
                                ('access.set_door_angle', {'angle_rad': 0.0})]),
        ('load calcite_holder', []),                                            # OCCUPIED
        ('scan', []),                                                           # SOURCE_NOT_SET
        ('source on --kv 70kV', []),                                            # OUT_OF_RANGE (20-40 kV)
        ('source on --kv 40kV --ma 15mA', [('xray.configure', {'voltage_v': 40000.0, 'current_a': 0.015})]),
        ('scan bogus=1', []),                                                   # UNKNOWN_SETTING
        ('scan from=20deg to=30deg step=0.05deg time=0.5s', [('xrd.set_slit', {'slit_id': 'standard_divergence_slit'}),
                                                              ('xrd.configure_scan', {'start_rad': 20 * DEG, 'stop_rad': 30 * DEG, 'step_rad': 0.05 * DEG,
                                                                                      'integration_time_s': 0.5}),
                                                              ('xrd.start_scan', {})]),
        ('#wait 120', None),
        ('result', [('xrd.read_pattern', {})]),
        ('analyze', []),                                                        # not supported
        ('align', []),                                                          # not supported
        ('source off', []),                                                     # nothing to switch (X-rays only while scanning)
        ('unload', [('access.set_door_angle', {'angle_rad': 1.5}), ('slot.set_sample', {'connection_id': None}),
                    ('access.set_door_angle', {'angle_rad': 0.0})]),
    ]},
    'rigaku_xtalabsynergy': {'steps': [
        ('scan', []),                                                           # NO_SAMPLE
        ('load nope', []),                                                      # NO_SUCH_SAMPLE
        ('load aspirin_01', [('door.open', {'door': 'left', 'angle_rad': 1.5}), ('motion_enable.hold', {'held': True}),
                             ('goniometer.mount_position', {}), ('crystal.mount', {'sample_id': 'aspirin_01'})]),
        ('align --wait', [('crystal.center', {})]),
        ('scan', []),                                                           # SOURCE_NOT_READY
        ('source on --wait', [('motion_enable.hold', {'held': False}), ('door.close', {'door': 'left'}), ('safety.doors_ok', {}),
                              ('xray.set_key', {'angle_rad': 1.5707963}), ('xray.on', {'voltage_v': 40000.0, 'current_a': 0.03})]),
        ('scan width=20deg', []),                                               # OUT_OF_RANGE (frame width up to 5 deg)
        ('scan exposure=1s --wait --max 2h', [('experiment.pre_experiment', {'exposure_s': 1.0, 'frame_width_rad': 0.5 * DEG, 'distance_m': 0.045})]),
        ('result', [('measurement.result', {'record_id': 'pre_0001', 'max_values': 4096})]),
        ('analyze what=index', [('processing.index', {'record_id': 'pre_0001', 'threshold_i_sigma': 5})]),
        ('analyze what=bogus', []),                                             # UNKNOWN_ANALYSIS
        ('vacuum on', []),                                                      # not supported
        ('source off', [('xray.off', {})]),
    ]},
    'antonpaar_saxspace': {'steps': [
        ('scan', []),                                                           # NO_SAMPLE
        ('load lysozyme_5', [('chamber.set_door', {'open': True}), ('sample.load', {'sample_id': 'lysozyme_5', 'holder': 'capillary_1mm'}),
                             ('chamber.set_door', {'open': False})]),
        ('load lysozyme_5 --holder paste_cell', []),                            # OCCUPIED
        ('align', []),                                                          # SOURCE_NOT_READY
        ('vacuum on --wait', [('vacuum.set_pump', {'on': True})]),
        ('source on --kv 40kV --ma 50mA', [('generator.set_power', {'voltage_v': 40000.0, 'current_a': 0.05}), ('generator.set_key', {'on': True}),
                                           ('generator.xray_on', {})]),
        ('#wait 30', None),
        ('align --wait', [('stage.auto_align', {})]),
        ('scan exposure=60s frames=2 --wait', [('acquisition.start', {'exposure_s': 60.0, 'frames': 2})]),
        ('result', [('acquisition.result', {'record_id': 'sx-1'})]),
        ('analyze thickness=1mm', [('data.reduce', {'record_id': 'sx-1', 'background_record_id': None, 'calibration_id': None, 'thickness_m': 0.001,
                                                    'desmear': False})]),
        ('unload', []),                                                         # NOT_VENTED
        ('source off', [('generator.xray_off', {})]),
        ('vacuum off --wait', [('vacuum.set_pump', {'on': False})]),
        ('unload', [('chamber.set_door', {'open': True}), ('sample.unload', {}), ('chamber.set_door', {'open': False})]),
    ]},
    'kratos_axissupra': {'steps': [
        ('scan', []),                                                           # NO_SAMPLE
        ('load sio2_on_si --wait --max 1h', [('holder.mount_sample', {'holder_id': 'H1', 'sample_id': 'sio2_on_si', 'x_m': 0.0, 'y_m': 0.0}),
                                              ('flexilock.set_lid', {'open': True}), ('flexilock.load_holder', {'holder_id': 'H1', 'slot': 1}),
                                              ('flexilock.set_lid', {'open': False}), ('flexilock.pump', {}), ('transfer.to_stage', {'slot': 1})]),
        ('align', []),                                                          # SOURCE_NOT_READY
        ('source on --ma 10mA --wait', [('xray.set_source', {'on': True, 'emission_a': 0.01})]),
        ('align --wait', [('stage.auto_height', {})]),
        ('scan region=survey,C1s --wait --max 1h', [('acquisition.spectrum', {'regions': [SURVEY, C1S], 'mode': 'scanned'})]),
        ('scan region=C1s:280 mode=bogus', []),                                 # BAD_CHOICE (mode)
        ('result', [('acquisition.result', {'record_id': 'SP0001'})]),
        ('analyze regions=C1s', [('data.quantify', {'record_id': 'SP0001', 'regions': ['C1s'], 'shift_ev': 0})]),
        ('unload', []),                                                         # MISSING_SAMPLE (usage)
        ('source off', [('xray.set_source', {'on': False, 'emission_a': 0.01})]),
        ('unload sio2_on_si --wait', [('transfer.to_magazine', {'slot': 1}), ('flexilock.vent', {}), ('flexilock.set_lid', {'open': True}),
                                      ('flexilock.unload_holder', {'slot': 1}), ('flexilock.set_lid', {'open': False}),
                                      ('holder.unmount_sample', {'holder_id': 'H1', 'sample_id': 'sio2_on_si'})]),
    ]},
    'ja_alphase': {'steps': [
        ('scan', []),                                                           # NO_SAMPLE
        ('load pmma_on_si', [('sample.place', {'sample_id': 'pmma_on_si', 'offset_x_m': 0.0, 'offset_y_m': 0.0}), ('vacuum.set', {'on': True})]),
        ('scan mode=superfast', []),                                            # BAD_CHOICE
        ('scan mode=fast --wait', [('measure', {'mode': 'fast', 'alignment': 'standard', 'fixed_height_m': None, 'model_id': None})]),
        ('result', [('measurement.result', {'record_id': 'SE0001'})]),
        ('analyze thickness=110nm', [('analysis.fit', {'record_ids': ['SE0001'], 'model_id': 'cauchy_on_si', 'parameters': {'film.thickness_m': 1.1e-07},
                                                       'fit': ['film.thickness_m', 'film.A', 'film.B'], 'thickness_prefit': True})]),
        ('align', []),                                                          # not supported (part of every measurement)
        ('source on', []),                                                      # not supported
        ('vacuum off', [('vacuum.set', {'on': False})]),
        ('unload', [('sample.remove', {'sample_id': 'pmma_on_si'})]),
    ]},
}

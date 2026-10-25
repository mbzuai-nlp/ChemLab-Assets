"""Lowering table of the imaging class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a usage error, an unsupported verb, a dry run); '#wait N' = lab time passes on both labs.  The world is
tasks/imaging_world.py (specimens on the benches, instruments switched on)."""
from .imaging_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

NO_REGION = {'channel': None, 'direction': None, 'region_px': None, 'max_values': 4096}

CASES = {
    'jeol_jsmit200': {'steps': [
        ('status', []),
        ('acquire', []),                                                        # VACUUM_NOT_READY (class check)
        ('load nope', []),                                                      # NO_SUCH_SAMPLE
        ('load fe_si_polished --dry-run', []),
        ('load fe_si_polished', [('chamber.draw_out', {}), ('specimen.load', {'specimen_id': 'fe_si_polished'}), ('chamber.draw_in', {})]),
        ('load fe_si_polished', []),                                            # OCCUPIED (checked before the bench)
        ('source on', []),                                                      # VACUUM_NOT_READY
        ('vacuum pump', [('vacuum.evacuate', {})]),                             # job (pump-down)
        ('#wait 240', None),
        ('source on --kv 10kV --wait', [('beam.set_voltage', {'voltage_v': 10000.0}), ('beam.observation_on', {})]),
        ('focus auto', [('optics.autofocus', {})]),
        ('focus 10mm', [('optics.set_focus', {'focus_wd_m': 0.01})]),
        ('zoom 2000x', [('scan.set_magnification', {'magnification': 2000.0})]),
        ('zoom 500000x', []),                                                   # OUT_OF_RANGE (5x..300000x)
        ('zoom --objective 2', []),                                             # NO_OBJECTIVES
        ('move 2mm -1mm --z 3mm', [('stage.move_xy', {'x_m': 0.002, 'y_m': -0.001}), ('stage.set_z', {'z_lift_m': 0.003})]),
        ('move 90mm 0mm', []),                                                  # OUT_OF_RANGE (x +-40 mm)
        ('acquire --pixels 100x100', []),                                       # BAD_CHOICE
        ('acquire --pixels 640x480 --dwell 20us --wait', [('image.acquire', {'pixels': '640x480', 'dwell_s': 2e-05})]),
        ('result', [('image.result', {'image_id': 'img-1'})]),
        ('approach', []),                                                       # not supported (not a scanning-probe microscope)
        ('source off', [('beam.observation_off', {})]),
        ('vacuum vent --wait', [('vacuum.vent', {})]),
        ('unload', [('chamber.draw_out', {}), ('specimen.unload', {}), ('chamber.draw_in', {})]),
    ]},
    'jeol_jem1400flash': {'steps': [
        ('acquire', []),                                                        # NO_SAMPLE
        ('load au_np_on_carbon --dry-run', []),
        ('load au_np_on_carbon', [('holder.load_grid', {'holder_id': 'quick_change', 'specimen_id': 'au_np_on_carbon'}),
                                  ('holder.insert_to_airlock', {'holder_id': 'quick_change'}), ('airlock.set_pump', {'pump': True}),
                                  ('holder.insert', {})]),
        ('acquire', []),                                                        # BEAM_NOT_READY
        ('source on --kv 100kV', [('ht.set_voltage', {'voltage_v': 100000.0}), ('ht.set_on', {'on': True}), ('filament.set_on', {'on': True})]),
        ('#wait 30', None),
        ('zoom 50000x', [('optics.set_magnification', {'magnification': 50000.0})]),
        ('focus auto', [('screen.set', {'up': True}), ('optics.auto_focus', {})]),
        ('focus 1um', [('optics.set_defocus', {'defocus_m': 1e-06})]),
        ('focus 50um', []),                                                     # OUT_OF_RANGE (+-20 um)
        ('move 100um -50um', [('stage.move_xy', {'x_m': 0.0001, 'y_m': -5e-05})]),
        ('acquire --exposure 0.5s --binning 2 --wait', [('image.acquire', {'exposure_s': 0.5, 'binning': 2})]),
        ('result', [('image.result', {'image_id': 'img-1'})]),
        ('vacuum pump', []),                                                    # not supported (column always under vacuum)
        ('unload', []),                                                         # BEAM_ON (filament on)
        ('source off', [('filament.set_on', {'on': False})]),
        ('unload', [('stage.neutral', {}), ('holder.withdraw', {}), ('airlock.set_pump', {'pump': False}), ('holder.remove', {}),
                    ('holder.unload_grid', {'holder_id': 'quick_change'})]),
    ]},
    'keyence_vkx3000': {'steps': [
        ('focus auto', []),                                                     # NO_SAMPLE
        ('load sio2_step_on_si', [('sample.place', {'sample_id': 'sio2_step_on_si', 'x_m': 0.0, 'y_m': 0.0})]),
        ('focus auto', []),                                                     # LENS_NOT_FOR_MEASURING (2.5x)
        ('zoom --objective 10x', [('lens.select', {'slot': 1})]),
        ('focus auto --wait', [('focus.auto', {})]),
        ('zoom 2x', [('zoom.set', {'zoom': 2.0})]),
        ('zoom 10x', []),                                                       # OUT_OF_RANGE (zoom 1..8)
        ('acquire --wait --max 30min', [('measurement.auto_range', {}), ('measurement.start', {})]),
        ('result', [('measurement.result', {'record_id': 'HM0001'})]),
        ('move 1mm 0mm', [('stage.move', {'x_m': 0.001, 'y_m': 0.0})]),
        ('move --z 1mm', []),                                                   # OPTION_NOT_USED
        ('focus 31mm', [('focus.move', {'z_m': 0.031})]),
        ('source on', []),                                                      # not supported
        ('unload', [('sample.remove', {'sample_id': 'sio2_step_on_si'})]),
    ]},
    'evident_bx53p': {'steps': [
        ('load quartz_section', [('sample.place', {'sample_id': 'quartz_section'})]),
        ('zoom --objective 40x', [('objective.select', {'index': 2})]),
        ('zoom --objective 20x', []),                                           # NO_SUCH_OBJECTIVE
        ('zoom 40x', []),                                                       # NO_MAGNIFICATION
        ('focus auto', []),                                                     # NO_AUTOFOCUS
        ('focus -4.9mm', [('focus.move', {'stage_z_m': -0.0049})]),
        ('source off', [('illumination.set_level', {'level': 0.0})]),
        ('acquire', []),                                                        # LAMP_OFF
        ('source on --level 70%', [('illumination.set_level', {'level': 0.7})]),
        ('acquire --exposure 50ms --wait', [('camera.configure', {'exposure_s': 0.05, 'gain': 1.0}), ('camera.acquire', {})]),
        ('acquire --pixels 640x480', []),                                       # OPTION_NOT_USED
        ('result', [('measurement.result', {'record_id': 'IM0001'})]),
        ('move 1mm 1mm', []),                                                   # not supported (rotating stage only)
        ('unload', [('sample.remove', {'sample_id': 'quartz_section'})]),
    ]},
    'park_nx10': {'steps': [
        ('approach', []),                                                       # NO_SAMPLE
        ('load grating', [('sample.place', {'sample_id': 'grating', 'offset_x_m': 0.0, 'offset_y_m': 0.0})]),
        ('approach', []),                                                       # LASER_OFF
        ('source on', [('beam.set', {'on': True})]),
        ('approach --setpoint 20nm', []),                                       # WRONG_QUANTITY (contact mode takes a force)
        ('approach --setpoint 2nN --wait', [('approach', {'setpoint_force_n': 2e-09, 'setpoint_amplitude_m': None, 'z_servo_gain': 1.0})]),
        ('acquire --size 5um --pixels 64 --rate 2Hz --wait',
         [('scan.start', {'size_m': 5e-06, 'pixels_x': 64, 'pixels_y': 64, 'rate_hz': 2.0, 'angle_rad': 0.0, 'offset_x_m': 0.0, 'offset_y_m': 0.0,
                          'setpoint_force_n': 2e-09, 'setpoint_amplitude_m': None, 'z_servo_gain': 1, 'save_retrace': False, 'retract_after': False})]),
        ('result', [('measurement.result', dict(NO_REGION, record_id='IMG0001'))]),
        ('unload', []),                                                         # TIP_ENGAGED
        ('retract', [('retract', {'distance_m': 0.003})]),
        ('move 1mm 1mm', [('stage.move_xy', {'x_m': 0.001, 'y_m': 0.001})]),
        ('focus 7mm', [('focus.move', {'position_m': 0.007})]),
        ('zoom 2x', []),                                                        # not supported (scan size)
        ('source off', [('beam.set', {'on': False})]),
        ('unload', [('sample.remove', {'sample_id': 'grating'})]),
    ]},
    'nanosurf_naiostm': {'steps': [
        ('load hopg', [('sample.mount', {'sample_id': 'hopg'}), ('cover.lift', {}), ('holder.insert', {'position_m': 0.006, 'angle_rad': 0.0}),
                       ('cover.place', {})]),
        ('acquire', []),                                                        # NOT_ENGAGED
        ('approach', [('approach.start', {'move_speed_percent': 100.0, 'max_steps': 200000})]),
        ('#wait 320', None),
        ('acquire --size 10nm --points 64 --line-time 0.2s --wait',
         [('scan.start', {'image_size_m': 1e-08, 'time_per_line_s': 0.2, 'points': 64, 'rotation_rad': 0.0, 'offset_x_m': 0.0, 'offset_y_m': 0.0,
                          'slope_x_rad': 0.0, 'slope_y_rad': 0.0})]),
        ('acquire --size 1um', []),                                             # OUT_OF_RANGE (up to 500 nm)
        ('result', [('measurement.result', dict(NO_REGION, record_id='IMG0001'))]),
        ('unload', []),                                                         # TIP_ENGAGED
        ('retract', [('approach.retract', {'duration_s': 2.0})]),
        ('move 1mm 1mm', []),                                                   # not supported
        ('unload', [('cover.lift', {}), ('holder.remove', {}), ('sample.unmount', {}), ('cover.place', {})]),
    ]},
    'bruker_x4poseidon': {'steps': [
        ('load alumina_pellet', [('door.open', {}), ('sample.place', {'sample_id': 'alumina_pellet', 'offset_x_m': 0.0, 'offset_y_m': 0.0}),
                                 ('door.close', {}), ('stage.go_to', {'position': 'center'})]),
        ('move 1mm 2mm', []),                                                   # NO_LATERAL_STAGE (usage)
        ('move --z 397mm', [('stage.move', {'sod_m': 0.2, 'chuck_z_m': 0.397})]),
        ('acquire', []),                                                        # SOURCE_NOT_READY
        ('source on --kv 80kV --current 100uA', [('xray.set', {'voltage_v': 80000.0, 'current_a': 0.0001}), ('xray.enable', {'enabled': True})]),
        ('#wait 200', None),
        ('zoom 3x', [('stage.move', {'sod_m': 0.15, 'chuck_z_m': 0.397})]),
        ('zoom 50x', []),                                                       # OUT_OF_RANGE (source-object distance)
        ('acquire --exposure 0.1s --binning 4x4',
         [('scan.configure', {'range_deg': '180', 'step_rad': 0.006981317007977318, 'exposure_s': 0.1, 'averaging': 2, 'binning': '4x4',
                              'random_movement': True}),
          ('stage.move', {'sod_m': 0.15, 'chuck_z_m': 0.25}), ('calibration.flat_field', {}), ('stage.move', {'sod_m': 0.15, 'chuck_z_m': 0.397}),
          ('scan.start', {})]),
        ('#wait 300', None),
        ('result', [('measurement.result', {'record_id': 'PJ0001'})]),
        ('unload', []),                                                         # SOURCE_ON (door locked)
        ('source off', [('xray.enable', {'enabled': False})]),
        ('unload', [('door.open', {}), ('stage.go_to', {'position': 'load'}), ('sample.remove', {'sample_id': 'alumina_pellet'}), ('door.close', {})]),
    ]},
    'thermo_ibrightfl1500': {'steps': [
        ('acquire', []),                                                        # NO_SAMPLE
        ('load gel1', [('drawer.open', {}), ('sample.place', {'sample_id': 'gel1', 'skew_rad': 0.0}), ('drawer.close', {})]),
        ('acquire', []),                                                        # NO_MODE
        ('acquire --mode nucleic_acid_gels --wait', [('mode.select', {'mode': 'nucleic_acid_gels'}), ('capture.run', {'hdr': False})]),
        ('result', [('measurement.result', {'image_id': 'IMG0001'})]),
        ('zoom 2x', [('camera.set', {'binning': 2, 'zoom': 2.0, 'auto_focus': True, 'lock': False})]),
        ('focus auto', [('camera.set', {'binning': 2, 'zoom': 2.0, 'auto_focus': True, 'lock': False})]),
        ('focus 3mm', []),                                                      # NO_MANUAL_FOCUS
        ('source on', []),                                                      # not supported
        ('acquire --hdr --wait', []),                                           # HDR_CHEMI_ONLY (class check)
        ('unload', [('drawer.open', {}), ('sample.remove', {'sample_id': 'gel1'}), ('drawer.close', {})]),
    ]},
}

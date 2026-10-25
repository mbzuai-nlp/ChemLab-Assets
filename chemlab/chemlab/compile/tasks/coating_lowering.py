"""Lowering table of the coating class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a read, or a wait); None = not checked.  A call the runtime refuses is still listed (it was sent).  Shared verbs
(power, open, close, estop) appear only where a step needs them."""
from .coating_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
RPM = 0.10471975511965977          # rad/s per rpm
QUORUM_DEFAULTS = {'working_pressure_pa': 1.0, 'carbon_current_a': 46.0, 'carbon_ramp_a_s': 1.0, 'preclean_s': 2.0, 'rotation_rpm': 12.0,
                   'stage_lift_m': 0.0, 'tooling': 1.0}
ALD_DEFAULTS = {'door_k': 523.15, 'manifold_k': 373.15, 'pulse_s': [0.2, 0.2], 'exposure_s': [0.5, 0.5], 'purge_s': [12.0, 12.0],
                'precursor_mol_s': [0.0002, 0.0003], 'carrier_std_m3_s': 1.6666666666666667e-06, 'rf_w': 0.0}

CASES = {
    'ossila_spincoater': {'steps': [
        ('status', []),
        ('method speed=3000rpm time=30s', [('method.configure', {'steps': [{'speed_rad_s': 3000 * RPM, 'duration_s': 30.0}]})]),
        ('method program=500rpm@5s,3000rpm@30s', [('method.configure', {'steps': [{'speed_rad_s': 500 * RPM, 'duration_s': 5.0},
                                                                                   {'speed_rad_s': 3000 * RPM, 'duration_s': 30.0}]})]),
        ('method speed=8000rpm time=30s', []),                                  # OUT_OF_RANGE (6000 rpm)
        ('method speed=3000rpm', []),                                           # MISSING_SETTING
        ('move 10mm', []),                                                      # not supported
        ('open lid', [('lid.set_angle', {'angle_rad': 1.7})]),
        ('#wait 2', None),
        ('start', []),                                                          # LID_OPEN
        ('close lid', [('lid.set_angle', {'angle_rad': 0})]),
        ('start', []),                                                          # LID_MOVING
        ('#wait 2', None),
        ('start --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('method speed=1000rpm time=5s', []),                                   # BUSY
        ('stop', [('acquisition.stop', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('result', [('coating.measure', {})]),
        ('vacuum pump', []),                                                    # not supported
    ]},
    'ossila_dipcoater': {'steps': [
        ('method length=40mm dip-speed=10mm/s withdraw=1mm/s', []),             # MISSING_SETTING (cycles)
        ('method length=40mm dip-speed=10mm/s withdraw=1mm/s withdraw-final=2mm/s cycles=1 dwell=10s', [('method.configure', {
            'coating_length_m': 0.04, 'immersion_speed_m_s': 0.01, 'withdrawal_initial_m_s': 0.001, 'withdrawal_final_m_s': 0.002,
            'cycles': 1, 'dwell_s': 10.0, 'dry_s': 0.5, 'drying_gap_m': 0.005})]),
        ('start', [('acquisition.start', {})]),                                 # refused: the clamp does not hold the slide
        ('gap 15mm', []),                                                       # OUT_OF_RANGE (12 mm)
        ('gap 1mm', [('clamp.set_gap', {'gap_m': 0.001})]),
        ('home', []),                                                           # SUBSTRATE_IN
        ('move 100mm --speed 200mm/s', []),                                     # OUT_OF_RANGE (125 mm/s)
        ('start', [('acquisition.start', {})]),
        ('move 100mm', []),                                                     # BUSY
        ('#wait 300', None),
        ('result', [('coating.measure', {})]),
        ('move 150mm --speed 20mm/s', [('stage.move_to', {'position_m': 0.15, 'speed_m_s': 0.02})]),
        ('move 160mm --job', [('stage.move_to', {'position_m': 0.16, 'speed_m_s': 0.02})]),
        ('#wait 5', None),
        ('stop', []),                                                           # NOT_RUNNING
        ('set-temp 50C', []),                                                   # not supported
    ]},
    'ossila_doctorblade': {'steps': [
        ('gap 100.5um', []),                                                    # BAD_STEP (whole µm)
        ('gap 20mm', []),                                                       # OUT_OF_RANGE (10 mm)
        ('method distance=200mm', []),                                          # MISSING_SETTING (speed)
        ('method distance=200mm speed=10mm/s', [('method.configure', {'distance_m': 0.2, 'speed_m_s': 0.01})]),
        ('start', []),                                                          # NO_GAP
        ('gap --left 100um --right 120um --dry-run', []),
        ('gap 100um', [('gap.set', {'left_m': 0.0001, 'right_m': 0.0001})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 60', None),
        ('result', [('coating.measure', {})]),
        ('home', [('stage.home', {})]),                                         # refused: wet film on the bed
        ('estop', [('emergency.stop', {})]),
        ('move 10mm', []),                                                      # ESTOP_LATCHED
        ('set-temp 50C', []),                                                   # not supported
    ]},
    'ossila_slotdie': {'steps': [
        ('method distance=120mm speed=10mm/s flow=1.2mL/min', [('method.configure', {'distance_m': 0.12, 'speed_m_s': 0.01, 'flow_m3_s': 2e-08,
                                                                                    'stage_delay_s': 0.2})]),
        ('prime 60uL', []),                                                     # NO_GAP
        ('gap 100um', [('gap.set', {'left_m': 0.0001, 'right_m': 0.0001})]),
        ('start', [('acquisition.start', {})]),                                 # refused NOT_PRIMED
        ('prime 70uL --flow 1mL/min', [('pump.dispense', {'volume_m3': 7e-08, 'flow_m3_s': 1.6666666666666667e-08})]),
        ('set-temp 200C', []),                                                  # OUT_OF_RANGE (140 °C)
        ('set-temp 50C', [('thermal.set_temperature', {'temperature_k': K0 + 50}), ('thermal.set_enabled', {'enabled': True})]),
        ('set-temp 45C', [('thermal.set_temperature', {'temperature_k': K0 + 45})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 60', None),
        ('result', [('coating.measure', {})]),
        ('set-temp off', [('thermal.set_enabled', {'enabled': False})]),
        ('vacuum pump', []),                                                    # not supported
        ('move 10mm', [('stage.move_to', {'position_m': 0.01, 'speed_m_s': 0.02})]),   # refused: wet bead under a narrow gap
        ('stop', []),                                                           # NOT_RUNNING
    ]},
    'quorum_q150tes': {'steps': [
        ('method current=50mA', []),                                            # MISSING_SETTING (time or thickness)
        ('method current=50mA time=60s', [('method.configure', dict(QUORUM_DEFAULTS, sputter_current_a=0.05, duration_s=60.0, rotation_enabled=True,
                                                                     ftm_enabled=True, thickness_endpoint=False, endpoint_m=2e-08))]),
        ('method current=50mA thickness=5nm rotation=off', [('method.configure', dict(QUORUM_DEFAULTS, sputter_current_a=0.05, duration_s=30.0,
                                                                                      rotation_enabled=False, ftm_enabled=True, thickness_endpoint=True,
                                                                                      endpoint_m=5e-09))]),
        ('vacuum pump', [('vacuum.pump', {'enabled': True})]),
        ('vacuum off', [('vacuum.pump', {'enabled': False})]),
        ('vacuum vent', [('vacuum.vent', {})]),
        ('#wait 30', None),
        ('start', [('acquisition.start', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('result', [('coating.measure', {})]),
        ('open chamber', [('chamber.set', {'opened': True})]),
        ('start', []),                                                          # CHAMBER_OPEN
        ('vacuum pump', []),                                                    # CHAMBER_OPEN
        ('move 1mm', []),                                                       # not supported
    ]},
    'laboao_cvdltf12p50': {'steps': [
        ('set-temp 600C', [('controller.set_sv', {'target_k': K0 + 600})]),     # refused PROGRAM_MODE
        ('method temp=600C rate=5K/min hold=10min flow=200,0,0 mode=fixed', [('method.configure', {
            'curve_slot': 0, 'mode': 'fixed', 'segments': [{'target_k': K0 + 600, 'ramp_k_s': 5 / 60, 'hold_s': 600.0}],
            'flow_std_m3_s': [200e-6 / 60, 0.0, 0.0], 'pressure_pa': 30.0, 'pump_mode': 'manual', 'manual_valve_fraction': 1.0, 'purge_s': 1.0})]),
        ('method temp=600C hold=10min rate=100K/min', []),                      # OUT_OF_RANGE (20 K/min)
        ('set-temp 650C', [('controller.set_sv', {'target_k': K0 + 650})]),
        ('set-temp 2000C', []),                                                 # OUT_OF_RANGE (1200 °C)
        ('vacuum pump', []),                                                    # FLANGE_OPEN
        ('close flange', [('flange.set', {'opened': False})]),
        ('vacuum pump --pressure 30Pa', [('vacuum.set', {'enabled': True, 'mode': 'auto', 'pressure_pa': 30.0, 'manual_valve_fraction': 1.0})]),
        ('vacuum off', [('vacuum.set', {'enabled': False, 'mode': 'manual', 'pressure_pa': 30.0, 'manual_valve_fraction': 1.0})]),
        ('start', []),                                                          # FURNACE_OPEN
        ('close furnace', [('furnace.set', {'opened': False})]),
        ('start --mode bake', []),                                              # BAD_CHOICE
        ('start --mode purge', [('acquisition.start', {'mode': 'purge'})]),
        ('#wait 120', None),
        ('result', [('process.measure', {})]),
        ('vacuum vent', [('vacuum.vent', {})]),
        ('stop', []),                                                           # NOT_RUNNING
        ('gap 1mm', []),                                                        # not supported
    ]},
    'arradiance_gemstarxt': {'steps': [
        ('method cycles=5', []),                                                # MISSING_SETTING (temp)
        ('method cycles=5 temp=250C', [('method.configure', dict(ALD_DEFAULTS, cycles=5, reactor_k=K0 + 250))]),
        ('method cycles=5 temp=200C', [('method.configure', dict(ALD_DEFAULTS, cycles=5, reactor_k=K0 + 200))]),   # refused: not the chemistry's temperature
        ('start', []),                                                          # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('vacuum pump', []),                                                    # DOOR_OPEN
        ('close door', [('door.set', {'opened': False})]),
        ('vacuum pump', [('vacuum.set', {'enabled': True})]),
        ('start', [('acquisition.start', {})]),
        ('vacuum vent', []),                                                    # BUSY
        ('#wait 1200', None),
        ('result', [('process.measure', {})]),
        ('vacuum vent', [('vacuum.vent', {})]),
        ('set-temp 250C', []),                                                  # not supported (recipe key)
    ]},
    'fujifilm_dmp2850': {'steps': [
        ('start', []),                                                          # NO_METHOD
        ('method spacing=20um voltage=24V', [('method.configure', {
            'drop_spacing_m': 2e-05, 'voltage_v': 24.0, 'gap_m': 0.001, 'nozzles': [0, 5, 11], 'rise_s': 2e-06, 'dwell_s': 4e-06, 'fall_s': 2e-06,
            'frequency_hz': 1000.0, 'clean_every_drops': 0, 'clean_duration_s': 0.05})]),
        ('start', []),                                                          # NO_PATTERN
        ('method pattern=010/111/010', []),                                     # MISSING_SETTING (at)
        ('method pattern=010/111/010 at=50mm,50mm pattern-name=cross', [('pattern.configure', {'name': 'cross', 'rows': ['010', '111', '010'],
                                                                                                'x_m': 0.05, 'y_m': 0.05})]),
        ('set-temp 40C', [('thermal.set_temperature', {'platen_k': K0 + 40, 'cartridge_k': K0 + 25}), ('thermal.set_enabled', {'enabled': True})]),
        ('set-temp 30C --zone cartridge', [('thermal.set_temperature', {'platen_k': K0 + 40, 'cartridge_k': K0 + 30})]),
        ('set-temp 30C --zone head', []),                                       # UNKNOWN_ZONE
        ('move 10mm,20mm', []),                                                 # BAD_POSITION
        ('move 10mm,20mm,2mm', [('axis.move_to', {'x_m': 0.01, 'y_m': 0.02, 'z_m': 0.002})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 120', None),
        ('result', [('printing.measure', {})]),
        ('set-temp off', [('thermal.set_enabled', {'enabled': False})]),
        ('home', []),                                                           # not supported
    ]},
}

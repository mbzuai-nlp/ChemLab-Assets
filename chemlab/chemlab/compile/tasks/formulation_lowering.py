"""Lowering table of the formulation class: per member, verbs in order and the native calls (capability, arguments in the unit each
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check refused,
a read, a wait, a dry run or an unsupported verb); a listed call may itself be refused by the instrument (then it is still sent)."""
import math

from .formulation_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
RPM = 2 * math.pi / 60
ML_MIN = 1e-6 / 60

P11 = [('set-temp 180C --zone %d' % (z + 1), [('thermal.set_zone', {'zone': z, 'temperature_k': K0 + 180})]) for z in range(7)]

CASES = {
    'thermo_process11': {'steps': [
        ('status', []),
        ('set-temp 180C', []),                                                   # MISSING_ZONE
        *P11,
        ('set-temp 180C --zone die', [('die.set_temperature', {'temperature_k': K0 + 180})]),
        ('set-temp 400C --zone 2', []),                                          # OUT_OF_RANGE (max 350 °C)
        ('set-temp 180C --zone 9', []),                                          # BAD_CHOICE
        ('heat on --zone 1', []),                                                # NOT_PER_ZONE: heating is for the whole barrel
        ('heat on', [('thermal.set_enabled', {'enabled': True})]),
        ('wait-temp --max 1h', []),                                              # every zone and the die
        ('temp', []),
        ('method time=30s speed=150rpm feed=0.5g/min die-radius=1mm die-length=5mm mixing=1', [('method.configure', {
            'duration_s': 30.0, 'speed_rad_s': 150 * RPM, 'feed_kg_s': 0.5e-3 / 60, 'die_radius_m': 0.001, 'die_length_m': 0.005,
            'mixing_factor': 1.0})]),
        ('speed 100rpm', [('drive.set_speed', {'speed_rad_s': 100 * RPM})]),
        ('speed on', [('drive.set_enabled', {'enabled': True})]),
        ('method time=20s die-radius=1mm die-length=5mm mixing=1', []),          # DRIVE_ON: a method only with the screws stopped
        ('feed 0.5g/min', [('feed.set_rate', {'mass_rate_kg_s': 0.5e-3 / 60})]),
        ('feed on --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('wait --max 5min', []),
        ('result', [('extrusion.measure', {})]),
        ('speed off', []),                                                       # already off: the run stops the screws at its end
        ('speed on', [('drive.set_enabled', {'enabled': True})]),
        ('speed off', [('drive.set_enabled', {'enabled': False})]),
        ('heat off', [('thermal.set_enabled', {'enabled': False})]),
        ('vibration 100Hz', []),                                                 # not supported
    ]},
    'linari_rtadvanced': {'steps': [
        ('set-temp 30C', [('climate.set_targets', {'temperature_k': K0 + 30, 'humidity': 0.4})]),
        ('humidity 35%', [('climate.set_targets', {'temperature_k': K0 + 30, 'humidity': 0.35})]),
        ('humidity 90%', []),                                                    # OUT_OF_RANGE (20..70 %)
        ('heat on', [('climate.set', {'enabled': True})]),
        ('humidity on', []),                                                     # already on (one climate control)
        ('wait-temp --max 30min', []),
        ('start', []),                                                           # NO_METHOD
        ('method voltage=15kV,15kV flows=0.2mL/h,0,0,0,0,0,0,0 rpm=600rpm needle-low=50mm,50mm needle-high=450mm,450mm gap=40mm,40mm '
         'time=10s', [('method.configure', {'voltage_v': [15000.0, 15000.0], 'flows_m3_s': [0.2e-6 / 3600] + [0.0] * 7, 'rpm': 600.0,
                                            'needle_low_m': [0.05, 0.05], 'needle_high_m': [0.45, 0.45], 'gap_m': [0.04, 0.04],
                                            'duration_s': 10.0})]),
        ('start', []),                                                           # HV_OFF
        ('hv 20kV', []),                                                         # NO_VALUE: the voltage is a method setting
        ('hv on', [('hv.set', {'enabled': True})]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 5min', []),
        ('result', [('spinning.measure', {})]),
        ('clean --head 1', []),                                                  # HV_ON
        ('hv off', [('hv.set', {'enabled': False})]),
        ('clean', []),                                                           # MISSING_HEAD
        ('#wait 60', None),
        ('clean --head 1', [('needle.clean', {'group': 0})]),
        ('speed 100rpm', []),                                                    # not supported (method rpm)
        ('heat off', [('climate.set', {'enabled': False})]),
    ]},
    'buchi_encapsulatorb': {'steps': [
        ('status', []),
        ('start', []),                                                           # NO_METHOD
        ('method volume=5mL flow=6mL/min', [('method.configure', {'accept_host_observer_only': True, 'target_volume_m3': 5e-6,
                                                                   'target_flow_m3_s': 6 * ML_MIN})]),
        ('vibration 1000Hz', [('vibration.set_frequency', {'frequency_hz': 1000.0})]),
        ('vibration 10kHz', []),                                                 # OUT_OF_RANGE (40..6000 Hz)
        ('vibration on', [('vibration.toggle', {})]),
        ('vibration on', []),                                                    # already on: no toggle
        ('hv 1500V', [('electrostatic.set_voltage', {'voltage_v': 1500.0})]),
        ('hv on', [('electrostatic.toggle', {})]),
        ('set-temp 40C', [('thermal.set_temperature', {'temperature_k': K0 + 40})]),
        ('heat on', [('thermal.toggle', {})]),
        ('start', [('acquisition.start', {})]),
        ('feed 300mbar', [('air.set_pressure', {'gauge_pa': 30000.0}), ('valve.set_opening', {'fraction': 1.0})]),
        ('wait --max 30min', []),
        ('result', [('encapsulation.measure', {})]),
        ('feed off', [('valve.set_opening', {'fraction': 0.0}), ('air.set_pressure', {'gauge_pa': 0.0})]),
        ('hv off', [('electrostatic.toggle', {})]),
        ('vibration off', [('vibration.toggle', {})]),
        ('heat off', [('thermal.toggle', {})]),
        ('temp', []),                                                            # not supported (no nozzle reading)
        ('speed 100rpm', []),                                                    # not supported
    ]},
    'cytiva_nanoassemblrignite': {'steps': [
        ('set-temp 37C', [('heating.set', {'enabled': False, 'targets_k': [K0 + 37, K0 + 37]})]),
        ('heat on', []),                                                         # BLOCK_OPEN
        ('close block', [('block.set', {'opened': False})]),                     # shared verb
        ('heat on', [('heating.set', {'enabled': True, 'targets_k': [K0 + 37, K0 + 37]})]),
        ('set-temp 50C --channel right', [('heating.set', {'enabled': True, 'targets_k': [K0 + 37, K0 + 50]})]),
        ('set-temp 90C', []),                                                    # OUT_OF_RANGE (max 75 °C)
        ('temp', []),
        ('wait-temp --max 30min', []),                                           # both channels
        ('start', []),                                                           # NO_METHOD
        ('method flow=4mL/min ratio=3 volume=2mL dilution=1 waste-start=0.2mL waste-end=0.05mL', [('method.configure', {
            'flow_m3_s': 4 * ML_MIN, 'flow_ratio_c_r': 3.0, 'volume_m3': 2e-6, 'dilution_ratio': 1.0, 'start_waste_equiv_m3': 2e-7,
            'end_waste_equiv_m3': 5e-8})]),
        ('prime', []),                                                           # LID_OPEN
        ('close lid', [('lid.set', {'opened': False})]),                         # shared verb
        ('prime', [('cartridge.prime', {})]),
        ('wait --max 10min', []),
        ('start', [('acquisition.start', {})]),
        ('wait --max 10min', []),
        ('result', [('formulation.measure', {})]),
        ('start', []),                                                           # CARTRIDGE_USED
        ('heat off', [('heating.set', {'enabled': False, 'targets_k': [K0 + 37, K0 + 50]})]),
        ('feed 1mL/min', []),                                                    # not supported (method flow)
    ]},
    'glatt_miniglatt': {'steps': [
        ('status', []),
        ('air on', []),                                                          # POWER_OFF (off in the default lab)
        ('power on', [('power.set', {'enabled': True})]),                        # shared verb
        ('air on', []),                                                          # NO_METHOD (the set points edit the method)
        ('method time=2min spray-time=90s', [('method.configure', {
            'operation': 'granulation', 'spray_position': 'top', 'air_normal_m3_s': 12 / 3600, 'temperature_k': K0 + 60,
            'spray_m3_s': 3.5 * ML_MIN, 'duration_s': 120.0, 'spray_duration_s': 90.0})]),
        ('air on', []),                                                          # TOWER_OPEN
        ('close tower', [('tower.set', {'opened': False})]),                     # shared verb
        ('air 240L/min', [('fluidization.set_flow', {'flow_normal_m3_s': 240e-3 / 60})]),
        ('air on', [('air.set', {'enabled': True})]),
        ('set-temp 60C', [('thermal.set_temperature', {'temperature_k': K0 + 60})]),
        ('heat on', [('heating.set', {'enabled': True})]),
        ('wait-temp --max 30min', []),
        ('feed 3.5mL/min', [('spray.set_rate', {'flow_m3_s': 3.5 * ML_MIN})]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 10min', []),
        ('result', [('granulation.measure', {})]),
        ('clean', [('filter.clean', {})]),
        ('clean', []),                                                           # BLOWBACK_RUNNING
        ('feed off', [('spray.set_rate', {'flow_m3_s': 0.0})]),
        ('hv on', []),                                                           # not supported
    ]},
}

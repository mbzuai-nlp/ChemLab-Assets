"""Lowering table of the evaporation class: per member, verbs in order and the native calls (capability, arguments in the unit
the argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a usage error, an unsupported verb, a read); None = not checked.  "<slug>_empty" entries are a second copy of a member
without the lab's charge (methods can be set there; they are fixed once a sample is charged)."""
import math

from .evaporation_world import config as world

K0 = 273.15
RPM = 2 * math.pi / 60


def config():
    return world(variants={'genevac_mivac2_empty': 'empty', 'pilodist_pilodist104_empty': 'empty', 'uic_kdl1basic_empty': 'empty'},
                 copies={'genevac_mivac2_empty': 'genevac_mivac2', 'pilodist_pilodist104_empty': 'pilodist_pilodist104',
                         'uic_kdl1basic_empty': 'uic_kdl1basic'})


CASES = {
    'ika_rv10_digital_v': {'steps': [
        ('status', []),
        ('set-temp 15C', [('thermal.set_temperature', {'temperature_k': K0 + 15})]),      # the runtime refuses: below room temperature
        ('set-temp 200C', []),                                                             # OUT_OF_RANGE (max 180 °C)
        ('set-temp 40C --heat', [('thermal.set_temperature', {'temperature_k': K0 + 40}), ('thermal.start', {})]),
        ('set-temp 45C --heat', [('thermal.set_temperature', {'temperature_k': K0 + 45})]),   # heater already on
        ('rotate 2rpm', []),                                                               # below 5 rpm
        ('rotate 120rpm', [('rotation.set_speed', {'speed_rad_s': 120 * RPM}), ('rotation.start', {})]),
        ('rotate 150rpm', [('rotation.set_speed', {'speed_rad_s': 150 * RPM})]),           # already rotating
        ('method time=2min interval=10s', [('timer.set_duration', {'duration_s': 120.0}), ('interval.set', {'interval_s': 10})]),
        ('method foo=1', []),
        ('lift up --wait', [('lift.set_position', {'position_m': 0.14})]),
        ('lift 30mm', [('lift.set_position', {'position_m': 0.03})]),
        ('lift 20cm', []),
        ('vacuum 100mbar', []),                                                            # not supported: external pump
        ('endpoint schedule', []),
        ('stop', [('rotation.stop', {}), ('thermal.stop', {})]),
        ('method time=3min', [('timer.set_duration', {'duration_s': 180.0})]),
        ('start --dry-run', []),
        ('start', [('thermal.start', {}), ('rotation.start', {})]),                        # job: the timer ends the rotation
        ('#wait 60', None),
        ('result', []),
        ('set-temp off', [('thermal.stop', {})]),
        ('rotate off', [('rotation.stop', {})]),
        ('power off', [('power.set', {'enabled': False})]),
        ('start', []),                                                                     # POWER_OFF
        ('rotate 60rpm', []),                                                              # POWER_OFF
    ]},
    'buchi_lyovapor_l250_pro': {'steps': [
        ('status', []),
        ('start', []),                                                                     # NO_RECIPE
        ('method freeze=1min', []),                                                        # MISSING_SETTING: no stored recipe
        ('method freeze=1min primary=2min secondary=1min shelf=-30C',
         [('lyophilization.set_recipe', {'recipe': {'pre_freeze_s': 60.0, 'primary_drying_s': 120.0, 'secondary_drying_s': 60.0}}),
          ('thermal.set_temperature', {'temperature_k': K0 - 30})]),
        ('method primary=3min', [('lyophilization.set_recipe', {'recipe': {'pre_freeze_s': 60.0, 'primary_drying_s': 180.0, 'secondary_drying_s': 60.0}})]),
        ('vacuum 5Pa', []),                                                                # OUT_OF_RANGE (10-100 Pa)
        ('vacuum 0.3mbar', [('vacuum.set_pressure', {'pressure_pa': 30.0})]),
        ('set-temp -10C', [('thermal.set_temperature', {'temperature_k': K0 - 10})]),
        ('set-temp off', []),                                                              # NO_OFF
        ('endpoint moisture --max-moisture 2%', [('lyophilization.set_endpoint', {'mode': 'model_moisture', 'moisture_threshold': 0.02})]),
        ('endpoint schedule', [('lyophilization.set_endpoint', {'mode': 'schedule_only', 'moisture_threshold': 0.02})]),
        ('open chamber', [('chamber.set_open', {'open': True})]),                          # refused by the runtime: under vacuum
        ('start --dry-run', []),
        ('start', [('lyophilization.start', {})]),
        ('set-temp -5C', []),                                                              # CYCLE_RUNNING
        ('method freeze=2min', []),                                                        # CYCLE_RUNNING
        ('#wait 100', None),
        ('stop', [('lyophilization.stop', {})]),
        ('result', []),
        ('rotate 10rpm', []),
        ('fraction 1', []),
    ]},
    'buchi_minispray': {'steps': [
        ('status', []),
        ('set-temp 160C --heat', []),                                                      # NO_AIRFLOW
        ('start', []),                                                                     # NOT_PREHEATED
        ('set-temp 160C', [('heating.set', {'inlet_target_k': K0 + 160})]),
        ('set-temp 250C', []),                                                             # OUT_OF_RANGE (max 220 °C)
        ('method inlet=150C feed=4mL/min aspirator=400L/min spray=10L/min',
         [('heating.set', {'inlet_target_k': K0 + 150}), ('pump.set', {'feed_m3_s': 4e-6 / 60}),
          ('drying_gas.set', {'drying_gas_m3_s': 0.4 / 60}), ('dispersion_gas.set', {'spray_gas_m3_s': 0.01 / 60})]),
        ('feed 50mL/min', []),                                                             # OUT_OF_RANGE (max 30 mL/min)
        ('preheat on --wait', [('drying.prepare', {})]),
        ('set-temp 150C --heat', [('heating.set', {'inlet_target_k': K0 + 150, 'enabled': True})]),
        ('start', [('acquisition.start', {})]),
        ('feed 3mL/min', [('pump.set', {'feed_m3_s': 3e-6 / 60})]),
        ('#wait 60', None),
        ('stop', [('acquisition.stop', {})]),
        ('result', [('drying.measure', {})]),
        ('set-temp off', [('heating.set', {'enabled': False})]),
        ('preheat off', [('drying.shutdown', {})]),
        ('vacuum 100mbar', []),
        ('rotate 10rpm', []),
    ]},
    'tousimis_autosamdri815': {'steps': [
        ('status', []),
        ('start', []),                                                                     # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('start', []),                                                                     # NO_METHOD
        ('method purge=5min', []),                                                         # MISSING_SETTING bleed
        ('method purge=10min bleed=14364', [('method.configure', {'purge_time_s': 600.0, 'bleed_rate_pa_s': 14364.0})]),
        ('method bleed=20000', []),                                                        # OUT_OF_RANGE
        ('method bleed=15000', [('method.configure', {'purge_time_s': 600.0, 'bleed_rate_pa_s': 15000.0})]),
        ('set-temp 40C', []),
        ('vacuum 10mbar', []),
        ('open lid', [('lid.set_lift', {'lift_m': 0.065})]),
        ('start', []),                                                                     # LID_OPEN
        ('close lid', [('lid.set_lift', {'lift_m': 0})]),
        ('start --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('#wait 120', None),
        ('stop', [('acquisition.stop', {})]),
        ('result', [('cpd.measure', {})]),
        ('method purge=8min bleed=15000', [('method.configure', {'purge_time_s': 480.0, 'bleed_rate_pa_s': 15000.0})]),   # refused: after a run
    ]},
    'genevac_mivac2': {'steps': [
        ('status', []),
        ('method temp=50C', []),                                                           # METHOD_LOCKED: samples loaded
        ('set-temp 45C', [('temperature.set', {'temperature_k': K0 + 45})]),
        ('set-temp 90C', []),                                                              # OUT_OF_RANGE (max 80 °C)
        ('preheat on --wait', [('preheat.set', {'on': True})]),
        ('open lid', [('lid.set_open', {'open': True})]),
        ('start', []),                                                                     # LID_OPEN
        ('close lid', [('lid.set_open', {'open': False})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 60', None),
        ('stop', [('acquisition.stop', {})]),
        ('result', [('evaporation.measure', {})]),
        ('preheat off', [('preheat.set', {'on': False})]),
        ('vacuum 10mbar', []),
        ('rotate 100rpm', []),
    ]},
    'genevac_mivac2_empty': {'steps': [
        ('method temp=50C', []),                                                           # MISSING_SETTING
        ('method temp=50C heat_time=5min time=20min timeout=1h program=wine pressure=null endpoint=0', []),   # BAD_CHOICE
        ('method temp=50C heat_time=5min time=20min timeout=1h program=alcohol pressure=null endpoint=0',
         [('method.configure', {'temperature_k': K0 + 50, 'heating_time_s': 300.0, 'duration_s': 1200.0, 'timeout_s': 3600.0, 'program': 'alcohol',
                                'target_pressure_pa': None, 'endpoint_remaining_fraction': 0.0})]),
        ('start', []),                                                                     # NO_SAMPLE
    ]},
    'pilodist_pilodist104': {'steps': [
        ('status', []),
        ('method temp=90C', []),                                                           # METHOD_LOCKED
        ('set-temp 95C', [('temperature.set', {'temperature_k': K0 + 95})]),
        ('set-temp 300C', []),                                                             # OUT_OF_RANGE (max 250 °C)
        ('reflux 4', [('reflux.set', {'ratio': 4.0, 'total_reflux': False})]),
        ('reflux total', [('reflux.set', {'ratio': 4.0, 'total_reflux': True})]),
        ('reflux 2000', []),
        ('fraction 1', [('fraction.select', {'index': 1})]),
        ('fraction 7', []),
        ('start', []),                                                                     # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 30', None),
        ('fraction 2', [('fraction.select', {'index': 2})]),
        ('result', [('distillation.measure', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('vacuum 300mbar', []),
        ('rotate 10rpm', []),
    ]},
    'pilodist_pilodist104_empty': {'steps': [
        ('set-temp 95C', []),                                                              # NO_METHOD
        ('method temp=90C', []),                                                           # MISSING_SETTING
        ('method temp=90C pressure=300mbar reflux=2 total_reflux=off cut=100mL time=2min timeout=3min min_boiler=50mL',
         [('method.configure', {'bath_target_k': K0 + 90, 'pressure_pa': 30000.0, 'reflux_ratio': 2.0, 'total_reflux': False, 'cut_volume_m3': 1e-4,
                                'duration_s': 120.0, 'timeout_s': 180.0, 'min_boiler_volume_m3': 5e-5})]),
        ('fraction 1', []),                                                                # NO_SAMPLE
        ('set-temp 92C', [('temperature.set', {'temperature_k': K0 + 92})]),
    ]},
    'uic_kdl1basic': {'steps': [
        ('status', []),
        ('set-temp 60C', [('temperature.set', {'temperature_k': K0 + 60})]),
        ('vacuum 0.05mbar', [('vacuum.set', {'pressure_pa': 5.0})]),
        ('vacuum 0.01mbar', []),                                                           # OUT_OF_RANGE (min 5 Pa)
        ('rotate 300rpm', [('wiper.set', {'speed_rad_s': 300 * RPM})]),
        ('rotate 3000rpm', []),
        ('condenser 15C', [('condenser.set', {'temperature_k': K0 + 15})]),
        ('method time=3min', []),                                                          # MISSING_SETTING feed, timeout
        ('method time=3min timeout=5min feed=1.5g/min',
         [('method.configure', {'wall_temperature_k': K0 + 60, 'condenser_temperature_k': K0 + 15, 'pressure_pa': 5.0, 'feed_rate_kg_s': 2.5e-5,
                                'wiper_speed_rad_s': 300 * RPM, 'duration_s': 180.0, 'timeout_s': 300.0})]),
        ('rotate off', [('wiper.set', {'speed_rad_s': 0})]),
        ('start', []),                                                                     # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('start', []),                                                                     # WIPER_OFF
        ('rotate 250rpm', [('wiper.set', {'speed_rad_s': 250 * RPM})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 30', None),
        ('stop', [('acquisition.stop', {})]),
        ('result', [('distillation.measure', {})]),
        ('lift up', []),
        ('preheat on', []),
    ]},
    'uic_kdl1basic_empty': {'steps': [
        ('set-temp 60C', []),                                                              # NO_METHOD
        ('method temp=60C', []),                                                           # MISSING_SETTING
        ('method temp=120C condenser=20C pressure=1mbar feed=2g/min speed=200rpm time=10min timeout=20min',
         [('method.configure', {'wall_temperature_k': K0 + 120, 'condenser_temperature_k': K0 + 20, 'pressure_pa': 100.0, 'feed_rate_kg_s': 2e-3 / 60,
                                'wiper_speed_rad_s': 200 * RPM, 'duration_s': 600.0, 'timeout_s': 1200.0})]),
        ('start', []),                                                                     # POWER_OFF
    ]},
}

"""Lowering table of the thermal class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a read, or a wait); None = not checked."""
from .thermal_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15

CASES = {
    'julabo_corio_cd200f': {'steps': [
        ('status', []),
        ('set-temp 40C', []),                                                   # REMOTE_OFF (class requirement)
        ('remote on', [('interface.set_remote', {'enabled': True})]),
        ('set-temp 40C', [('thermal.set_temperature', {'temperature_K': K0 + 40})]),
        ('wait-temp --max 5min', []),                                           # HEATING_OFF: circulation not running
        ('heat on', [('circulation.set_running', {'running': True})]),
        ('wait-temp --max 30min', []),
        ('temp', []),
        ('safety-limit 30C', []),                                               # BELOW_SET_POINT
        ('safety-limit 100C', [('safety.set_cutoff', {'temperature_K': K0 + 100})]),
        ('set-temp 120C', []),                                                  # ABOVE_SAFETY_LIMIT
        ('set-temp 200C', []),                                                  # OUT_OF_RANGE (usage)
        ('stir 100rpm', []),                                                    # not supported
        ('set-temp 25C --dry-run', []),
        ('heat off', [('circulation.set_running', {'running': False})]),
        ('power off', [('power.set', {'enabled': False})]),
        ('power on', [('power.set', {'enabled': True})]),
    ]},
    'ika_plate_rctdigital': {'steps': [
        ('set-temp 80C', [('heating.set_temperature', {'temperature_k': K0 + 80})]),
        ('heat on', [('heating.set', {'on': True})]),
        ('stir 300rpm', [('stirring.set_speed', {'speed_rad_s': 31.41592653589793}), ('stirring.set', {'on': True})]),
        ('stir 500rpm', [('stirring.set_speed', {'speed_rad_s': 52.35987755982988})]),
        ('wait-temp --within 1K --max 20min', []),
        ('safety-limit 100C', [('safety.set_limit', {'limit_k': K0 + 100})]),
        ('set-temp 120C', []),                                                  # ABOVE_SAFETY_LIMIT
        ('set-temp 400C', []),                                                  # OUT_OF_RANGE
        ('stir off', [('stirring.set', {'on': False})]),
        ('heat off', [('heating.set', {'on': False})]),
        ('remote on', []),                                                      # not supported
        ('power off', [('power.set', {'on': False})]),
        ('heat on', []),                                                        # POWER_OFF
        ('power on', [('power.set', {'on': True})]),
    ]},
    'binder_fd056': {'steps': [
        ('set-temp 105C', [('temperature.set', {'temperature_k': K0 + 105})]),
        ('heat on', []),                                                        # not supported (controls whenever on)
        ('wait-temp --max 1h', []),
        ('safety-limit 120C', [('safety.set_limit', {'limit_k': K0 + 120})]),
        ('set-temp 130C', []),                                                  # ABOVE_SAFETY_LIMIT
        ('temp --save-to fd056.json', []),
        ('power off', [('power.set', {'on': False})]),
        ('power on', [('power.set', {'on': True})]),
    ]},
    'binder_vd56': {'steps': [
        ('set-temp 60C', []),                                                   # POWER_OFF (off in the default lab)
        ('power on', [('power.set', {'enabled': True})]),
        ('set-temp 60C', [('thermal.set_temperature', {'temperature_K': K0 + 60})]),
        ('heat off', [('thermal.set_control', {'enabled': False})]),
        ('wait-temp --max 10min', []),                                          # HEATING_OFF
        ('heat on', [('thermal.set_control', {'enabled': True})]),
        ('safety-limit 80C', [('safety.configure', {'mode': 'limit', 'limit_K': K0 + 80})]),
        ('wait-temp --max 2h', []),
    ]},
    'nabertherm_l9': {'steps': [
        ('set-temp 500C', [('thermal.set_temperature', {'temperature_K': K0 + 500})]),
        ('wait-temp --max 2h', []),
        ('set-temp 2000C', []),                                                 # OUT_OF_RANGE
        ('heat on', []),                                                        # not supported
        ('set-temp 25C', [('thermal.set_temperature', {'temperature_K': K0 + 25})]),
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'carbolite_tf1_12_60_300': {'steps': [
        ('set-temp 300C', [('thermal.set_temperature', {'temperature_K': K0 + 300})]),
        ('wait-temp --max 2h', []),
        ('temp', []),
        ('safety-limit 400C', []),                                              # not supported
        ('power off', [('power.set', {'enabled': False})]),
    ]},
    'eppendorf_cellxpertc170i': {'steps': [
        ('set-temp 37.05C', []),                                                # BAD_STEP (usage)
        ('set-temp 60C', []),                                                   # OUT_OF_RANGE (22..50 °C)
        ('set-temp 39C', [('temperature.set', {'setpoint_k': K0 + 39})]),
        ('wait-temp --max 1h', []),
        ('set-temp 37C', [('temperature.set', {'setpoint_k': K0 + 37})]),
    ]},
    'binder_kbf115': {'steps': [
        ('set-temp 30C', [('setpoint.set', {'temperature_k': K0 + 30})]),
        ('wait-temp --within 0.3K --max 1h', []),
        ('safety-limit 45C', [('safety.configure', {'mode': 'limit', 'value_k': K0 + 45})]),
        ('safety-limit 20C', []),                                               # BELOW_SET_POINT
        ('power off', [('power.set', {'on': False})]),
    ]},
    'eppendorf_cryocubef570n': {'steps': [
        ('set-temp -75C', [('settings.configure', {'set_point_k': K0 - 75})]),
        ('set-temp -20C', []),                                                  # OUT_OF_RANGE (-86..-50 °C)
        ('temp', []),
        ('heat on', []),                                                        # not supported
        ('power off', [('power.set', {'on': False})]),
    ]},
    'ika_ks4000': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('set-temp 37C', [('thermal.set_temperature', {'temperature_k': K0 + 37})]),
        ('heat on', [('thermal.set_enabled', {'enabled': True})]),
        ('safety-limit 60C', []),                                               # HEATING_ON (member requirement)
        ('heat off', [('thermal.set_enabled', {'enabled': False})]),
        ('safety-limit 60C', [('safety.configure', {'temperature_limit_k': K0 + 60, 'speed_limit_rad_s': 52.35987755982988})]),
        ('stir 200rpm', []),                                                    # NO_FIXTURE (member requirement)
        ('heat on', [('thermal.set_enabled', {'enabled': True})]),
        ('wait-temp --max 1h', []),
    ]},
    'elma_elmasonics60h': {'steps': [
        ('power on', [('power.set', {'enabled': True})]),
        ('set-temp 50C', [('thermal.set_temperature', {'temperature_k': K0 + 50})]),
        ('heat on', [('thermal.set_enabled', {'enabled': True})]),
        ('wait-temp --max 2h', []),
        ('stir 100rpm', []),                                                    # not supported
    ]},
}

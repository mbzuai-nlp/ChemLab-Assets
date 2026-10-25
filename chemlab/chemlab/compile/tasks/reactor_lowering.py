"""Lowering table of the reactor class: per member, verbs in order and the native calls (capability, arguments in the unit each
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check refused,
a read, a wait, a dry run or an unsupported verb); a listed call may itself be refused by the instrument (then it is still sent)."""
import math

from .reactor_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
RPM = 2 * math.pi / 60
ML_MIN = 1e-6 / 60
COLLECT = '\'collect=[{"start_s":30,"end_s":100,"slot":0}]\''
HC = {'liquid_flow_m3_s': 3.3333333333333334e-08, 'hydrogen_reference_flow_m3_s': 7e-07, 'temperature_k': 313.15, 'pressure_abs_pa': 4e6,
      'duration_s': 4.0}


def hc(**kw):
    out = dict(HC)
    out.update(kw)
    HC.update(kw)                                   # the H-Cube keeps the last method: the next call carries it
    return ('method.configure', out)


ES = {'mode': 'constant_current', 'current_a': 0.01, 'cell_voltage_v': 4.0, 'voltage_limit_v': 10.0, 'stop_by': 'time', 'duration_s': 2.0,
      'charge_target_c': 0.02}


def es(**kw):
    ES.update(kw)
    return ('method.configure', dict(ES))


SAFE = {'tsafe_k': 298.15, 'tr_min_k': 268.15, 'tr_max_k': 393.15, 'tj_min_k': 263.15, 'tj_max_k': 423.15, 'tdiff_max_k': 50.0, 'rsafe_rpm': None,
        'rmin_rpm': 0.0, 'rmax_rpm': 850.0, 'tc_max_k': 313.15}

CASES = {
    'antonpaar_monowave400': {'steps': [
        ('status', []),
        ('start', []),                                                           # LID_NOT_LOCKED (the world leaves the lid unlocked)
        ('close lid', [('lock.set_travel', {'travel_m': 0.01})]),                # the lid is down already: only the lock
        ('method temp=80C', []),                                                 # MISSING_SETTING ramp, hold, cool (not shown by the instrument)
        ('method temp=150C ramp=1min hold=2min cool=50C', [('method.configure', {'target_k': K0 + 150, 'ramp_s': 60.0, 'hold_s': 120.0,
                                                                                  'cool_to_k': K0 + 50, 'stir_rpm': 600.0, 'power_limit_w': 850.0,
                                                                                  'pressure_limit_pa': 3e6})]),   # refused: outside the ethanol model
        ('method temp=80C ramp=1min hold=2min cool=50C stir=800rpm', [('method.configure', {
            'target_k': K0 + 80, 'ramp_s': 60.0, 'hold_s': 120.0, 'cool_to_k': K0 + 50, 'stir_rpm': 800.0, 'power_limit_w': 850.0,
            'pressure_limit_pa': 3e6})]),
        ('start --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('wait --max 10min', []),
        ('result', [('reaction.measure', {})]),
        ('open lid', []),                                                        # PRESSURISED (the vial is still warm)
        ('#wait 1800', None),
        ('open lid', [('lock.set_travel', {'travel_m': 0.0}), ('lid.set_lift', {'lift_m': 0.15})]),
        ('set-temp 80C', []),                                                    # not supported: temperature is a programme setting
        ('stir 300rpm', []),                                                     # not supported
        ('temp', []),
    ]},
    'ika_electrasyn2': {'steps': [
        ('stir 400rpm', [('stir.set_speed', {'speed_rad_s': 400 * RPM})]),
        ('current 10mA', [es(mode='constant_current', current_a=0.01)]),
        ('voltage 3V', [es(mode='constant_cell_voltage', cell_voltage_v=3.0)]),
        ('method mode=constant_current time=1min', [es(mode='constant_current', duration_s=60.0)]),
        ('current 200mA', []),                                                   # OUT_OF_RANGE (0..100 mA)
        ('open cap', [('cap.set_lift', {'lift_m': 0.08})]),                      # shared verb (part "cap")
        ('start', []),                                                           # CAP_OPEN
        ('close cap', [('cap.set_lift', {'lift_m': 0})]),
        ('start', [('acquisition.start', {})]),
        ('voltage 4V', []),                                                      # BUSY (class check)
        ('stop', [('acquisition.stop', {})]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 5min', []),
        ('result --save-to es.json', [('electrochem.measure', {})]),
        ('stir off', [('stir.set_speed', {'speed_rad_s': 0.0})]),
        ('set-temp 30C', []),                                                    # not supported
    ]},
    'hepatochem_photoredoxbox': {'steps': [
        ('set-temp 20C', [('temperature.set', {'target_k': K0 + 20})]),          # refused: BELOW_AMBIENT (fan cooling only)
        ('set-temp 35C', [('temperature.set', {'target_k': K0 + 35})]),
        ('light 80%', [('led.set_intensity', {'intensity': 0.8})]),
        ('light 150%', []),                                                      # OUT_OF_RANGE
        ('stir 600rpm', [('stir.set_speed', {'speed_rad_s': 600 * RPM})]),
        ('method time=10min', [('method.configure', {'duration_s': 600.0})]),
        ('open lid', [('lid.set_lift', {'lift_m': 0.12})]),                      # shared verb
        ('start', []),                                                           # LID_OPEN
        ('close lid', [('lid.set_lift', {'lift_m': 0})]),
        ('start', [('acquisition.start', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 20min', []),
        ('result', [('irradiation.measure', {})]),
        ('temp', []),
        ('wait-temp --max 1min', []),
        ('light off', [('led.set_intensity', {'intensity': 0.0})]),
        ('heat on', []),                                                         # not supported
    ]},
    'parr_4591': {'steps': [
        ('set-temp 120C', [('thermal.set_temperature', {'temperature_K': K0 + 120})]),
        ('set-temp 250C', []),                                                   # OUT_OF_RANGE (max 225 °C)
        ('heat on', [('heater.set_enabled', {'enabled': True})]),
        ('heat on', []),                                                         # already on
        ('stir 500rpm', [('stirrer.set_speed', {'speed_rad_s': 500 * RPM})]),
        ('wait-temp --max 1h', []),
        ('open lid', []),                                                        # HEATER_ON
        ('stop', [('heater.set_enabled', {'enabled': False}), ('stirrer.set_speed', {'speed_rad_s': 0.0})]),
        ('wait-temp --to 45C --max 3h', []),
        ('open lid', []),                                                        # PRESSURISED (below ambient after cooling)
        ('open vent', [('vent.set_open', {'open': True})]),
        ('#wait 30', None),
        ('open lid', [('lid.open', {})]),
        ('heat on', []),                                                         # LID_OPEN
        ('stir 300rpm --dry-run', []),
        ('close lid', [('lid.close', {})]),
        ('close vent', [('vent.set_open', {'open': False})]),
        ('result', []),
        ('pressure 10bar', []),                                                  # not supported
        ('start', []),                                                           # not supported (manual controller)
    ]},
    'syrris_asiadiscovery': {'steps': [
        ('set-temp 60C --wait', [('temperature.set', {'target_k': K0 + 60})]),
        ('pressure 5bar', [('pressure.set', {'backpressure_pa': 500000.0})]),
        ('flow 1mL/min', []),                                                    # MISSING_CHANNEL
        ('flow 1mL/min --channel 1', [('pump.set_flow', {'channel': 'P1', 'flow_m3_s': ML_MIN})]),
        ('#wait 30', None),
        ('flow off --channel 1', [('pump.set_flow', {'channel': 'P1', 'flow_m3_s': 0.0})]),
        ('method flows=1mL/min,1mL/min time=2min', []),                          # MISSING_SETTING inject, collect
        ('method flows=1mL/min,1mL/min time=2min inject=0s,0s ' + COLLECT, [('method.configure', {
            'flow_rates_m3_s': [ML_MIN, ML_MIN], 'duration_s': 120.0, 'injection_times_s': [0.0, 0.0],
            'collection_windows': [{'start_s': 30, 'end_s': 100, 'slot': 0}]})]),
        ('start', [('acquisition.start', {})]),
        ('wait --max 10min', []),
        ('result', [('flow.measure', {})]),
        ('start', [('acquisition.start', {})]),
        ('stop', [('acquisition.stop', {})]),
        ('heat off', [('temperature.enable', {'enabled': False})]),
        ('pressure off', [('pressure.enable', {'enabled': False})]),
        ('pressure on', [('pressure.enable', {'enabled': True})]),
        ('stir 100rpm', []),                                                     # not supported
    ]},
    'thalesnano_hcube': {'steps': [
        ('set-temp 60C', [hc(temperature_k=K0 + 60)]),
        ('flow 0.5mL/min', [hc(liquid_flow_m3_s=0.5 * ML_MIN)]),
        ('pressure 30bar', [hc(pressure_abs_pa=3e6)]),
        ('method time=5min', [hc(duration_s=300.0)]),
        ('flow 5mL/min', []),                                                    # OUT_OF_RANGE (0.3..3 mL/min)
        ('flow off', []),                                                        # NEEDS_VALUE: the pump runs only in a run
        ('open water_lid', [('water_lid.set_lift', {'lift_m': 0.03})]),          # shared verb
        ('start', []),                                                           # WATER_LID_OPEN
        ('close water_lid', [('water_lid.set_lift', {'lift_m': 0})]),
        ('start', [('acquisition.start', {})]),
        ('set-temp 40C', []),                                                    # BUSY
        ('wait --max 10min', []),
        ('result', [('hydrogenation.measure', {})]),
        ('set-temp 150C', [('method.configure', dict(HC, temperature_k=K0 + 150))]),   # refused: outside the carrier model
        ('wait-temp --max 5min', []),
        ('temp', []),
        ('heat on', []),                                                         # not supported
    ]},
    'radleys_mya4': {'steps': [
        ('set-temp 60C', []),                                                    # MISSING_ZONE
        ('set-temp 60C --zone 1', [('thermal.set_temperature', {'zone_index': 0, 'temperature_K': K0 + 60})]),
        ('heat on --zone 1', [('thermal.set_enabled', {'zone_index': 0, 'enabled': True})]),
        ('stir 500rpm --zone 1', [('stirring.set_speed', {'zone_index': 0, 'angular_speed_rad_s': 500 * RPM}),
                                  ('stirring.set_enabled', {'zone_index': 0, 'enabled': True})]),
        ('stir 600rpm --zone 1', [('stirring.set_speed', {'zone_index': 0, 'angular_speed_rad_s': 600 * RPM})]),
        ('stir 50rpm --zone 1', []),                                             # OUT_OF_RANGE (100..1000 rpm)
        ('wait-temp --zone 1 --max 30min', []),
        ('temp', []),
        ('program 50C@2min,30C@0s --zone 2', [('thermal.configure_program', {'zone_index': 1, 'segments': [
            {'temperature_K': K0 + 50, 'hold_s': 120.0}, {'temperature_K': K0 + 30, 'hold_s': 0.0}]})]),
        ('start --zone 3', []),                                                  # NO_PROGRAM
        ('start --zone 2', [('thermal.start_program', {'zone_index': 1})]),
        ('set-temp 40C --zone 2', []),                                           # PROGRAM_RUNNING
        ('wait --max 1h', []),
        ('result --zone 2', []),
        ('stop --zone 2', [('thermal.stop_program', {'zone_index': 1})]),
        ('stir off --zone 1', [('stirring.set_enabled', {'zone_index': 0, 'enabled': False})]),
        ('heat off --zone 1', [('thermal.set_enabled', {'zone_index': 0, 'enabled': False})]),
        ('method temp=50C', []),                                                 # not supported (program / set-temp instead)
        ('set-temp 200C --zone 3', []),                                          # OUT_OF_RANGE
        ('heat on --zone 4 --dry-run', []),
    ]},
    'mt_rc1mxhfcal': {'steps': [
        ('status', []),
        ('stir 20rpm', []),                                                      # OUT_OF_RANGE (0 or 30..Rmax)
        ('stir 300rpm', [('stirrer.set', {'rpm': 300.0})]),
        ('set-temp 130C', []),                                                   # ABOVE_SAFETY_LIMIT (Tr max 120 °C)
        ('safety-limit 140C', [('safety.configure', dict(SAFE, tr_max_k=K0 + 140))]),
        ('set-temp 50C --ramp 10min', [('thermostat.set_tr', {'end_k': K0 + 50, 'ramp_s': 600.0, 'controller': 'aqueous'})]),
        ('start --name acetylation', [('experiment.start', {'name': 'acetylation'})]),
        ('wait-temp --max 1h', []),
        ('dose acetic_anhydride --rate 9g/min', []),                             # MISSING_OPTION --mass
        ('dose acetic_anhydride --rate 9g/min --mass 102g', [('dosing.start', {'substance_id': 'acetic_anhydride', 'rate_kg_s': 0.00015,
                                                                               'total_mass_kg': 0.102, 'feed_temperature_k': None})]),
        ('dose water --rate 1g/min --mass 1g', []),                              # DOSING_ACTIVE
        ('wait --max 30min', []),
        ('stop --cool-to 25C', [('experiment.end', {'end_mode': 'cool_to', 'end_temperature_k': K0 + 25})]),
        ('result', [('experiment.data', {'experiment_id': 'exp-1'})]),
        ('set-temp 40C --jacket', [('thermostat.set_tj', {'end_k': K0 + 40, 'ramp_s': 0.0})]),
        ('safety-limit 30C --jacket', []),                                       # BELOW_SET_POINT (Tj set point 40 °C)
        ('stop', []),                                                            # nothing recording
        ('heat on', []),                                                         # not supported
        ('stir off', [('stirrer.set', {'rpm': 0.0})]),
    ]},
}

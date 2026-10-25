"""Lowering table of the property_meter class: per member (and staged lab entry of the same instrument type), verbs in order and
the native calls each must make - capability and arguments in the unit the argument declares, written from the contracts.
[] = no native call (a class check refused, an unsupported verb, a dry run); query calls (X.measure) are native calls too.
The world (samples, staged cells and tubes, the 914 beaker) is property_meter_world.config()."""
from .property_meter_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
DEG = 0.017453292519943295
JASCO_METHOD = {'lamp': 'Na', 'wavelength_m': 5.89e-07, 'integration_s': 1.0, 'repeats': 3, 'declared_concentration_kg_m3': 100.0,
                'declared_path_m': 0.1}
KNAUER_METHOD = {'sample_volume_m3': 1.5e-07, 'freeze_k': K0 - 6, 'cooling_limit_k': K0 - 15, 'sample_interval_s': 1.0}

CASES = {
    'antonpaar_dma5002': {'steps': [
        ('status', []),
        ('check --reference 0.7893g/cm3', []),                                   # NO_READING (no finished density yet)
        ('result', [('density.measure', {})]),                                   # NO_RESULT (the query is made)
        ('set-temp 120C', []),                                                   # OUT_OF_RANGE (usage)
        ('set-temp 20C --wait', [('thermal.set_temperature', {'temperature_k': K0 + 20})]),
        ('measure', [('acquisition.start', {})]),
        ('measure', []),                                                         # BUSY (class check)
        ('#wait 40', None),
        ('result', [('density.measure', {})]),
        ('check --reference 0.7893g/cm3', [('calibration.check', {'reference_density_kg_m3': 789.3, 'reference_temperature_k': K0 + 20})]),
        ('method mode=density', [('method.configure', {'mode': 'density'})]),
        ('method speed=3', []),                                                  # UNKNOWN_SETTING
        ('measure --wait', [('acquisition.start', {}), ('density.measure', {})]),
        ('calibrate --standard 0.99820g/cm3', []),                               # not supported
        ('zero', []),                                                            # not supported
        ('clean --volume 500mL', []),                                            # OUT_OF_RANGE
        ('clean --volume 5mL --wait', [('cleaning.start', {'volume_m3': 5e-06})]),
        ('measure', []),                                                         # NO_SAMPLE (the cleaning emptied the U-tube)
        ('set-temp 25C --dry-run', []),
        ('set-temp 25C', [('thermal.set_temperature', {'temperature_k': K0 + 25})]),
        ('#wait 5', None),
    ]},
    'antonpaar_abbematadvanced': {'steps': [
        ('status', []),
        ('measure --wait', [('acquisition.start', {}), ('refractometry.measure', {})]),
        ('calibrate --standard 2.0', []),                                        # OUT_OF_RANGE (nD)
        ('calibrate', []),                                                       # MISSING_STANDARD
        ('calibrate --standard 1.3614 ethanol', [('adjustment.start', {'type': 'one_point', 'standards': [{'name': 'ethanol', 'temperature_c': 20.0, 'nd': 1.3614}]}),
                                                 ('adjustment.decide', {'apply': True})]),
        ('calibrate --standard 1.3330', [('adjustment.start', {'type': 'one_point', 'standards': [{'name': 'Standard', 'temperature_c': 20.0, 'nd': 1.333}]}),
                                         ('adjustment.decide', {'apply': False})]),     # CALIBRATION_REJECTED (the instrument recommends Reject)
        ("check 'Water Check'", [('check.start', {'check': 'Water Check'})]),
        ('check Watr', []),                                                      # UNKNOWN_NAME
        ('method mode=calibrated_concentration', [('method.configure', {'mode': 'calibrated_concentration'})]),
        ('method mode=refractive_index', [('method.configure', {'mode': 'refractive_index'})]),
        ('set-temp 25C --wait', [('thermal.set_temperature', {'temperature_k': K0 + 25})]),
        ('measure', [('acquisition.start', {})]),
        ('#wait 30', None),
        ('result --save-to abbemat.json', [('refractometry.measure', {})]),
        ('clean', []),                                                           # LID_CLOSED
        ('zero', []),                                                            # not supported
        ('calibrate --standard 1.3614 --dry-run', []),
    ]},
    'jasco_p4200': {'steps': [                                                   # blank cell in place
        ('status', []),
        ('zero', []),                                                            # NO_METHOD
        ('method integration=1s repeats=3 conc=100g/L', []),                     # MISSING_SETTING path
        ('method integration=1s repeats=3 conc=100g/L path=100mm', [('method.configure', JASCO_METHOD)]),
        ('zero --dry-run', []),
        ('zero', [('lamp.set', {'enabled': True}), ('acquisition.start', {'kind': 'blank'})]),
        ('measure', []),                                                         # NOT_CALIBRATED
        ('calibrate --standard 10deg', [('lamp.set', {'enabled': True}), ('acquisition.start', {'kind': 'quartz'})]),   # WRONG_CELL
        ('check', []),                                                           # not supported
        ('set-temp 50C', []),                                                    # OUT_OF_RANGE (15..40 °C)
        ('clean', []),                                                           # not supported
    ]},
    'jasco_p4200_quartz': {'steps': [                                            # method, blank read, quartz plate in
        ('calibrate', []),                                                       # MISSING_STANDARD
        ('calibrate --standard 10deg', [('acquisition.start', {'kind': 'quartz'}),
                                        ('calibration.quartz', {'certified_angle_rad': 10 * DEG, 'accept_simulated_standard': True})]),
        ('measure', [('lamp.set', {'enabled': True}), ('acquisition.start', {'kind': 'sample'})]),   # WRONG_CELL (the plate is still in)
    ]},
    'jasco_p4200_sample': {'steps': [                                            # + quartz calibration, sample cell in
        ('status', []),
        ('measure --wait', [('acquisition.start', {'kind': 'sample'}), ('process.measure', {})]),
        ('result', [('process.measure', {})]),
        ('measure', [('lamp.set', {'enabled': True}), ('acquisition.start', {'kind': 'sample'})]),
        ('#wait 30', None),
        ('set-temp 25C', [('thermal.set_temperature', {'temperature_k': K0 + 25})]),
        ('measure', []),                                                         # BLANK_REQUIRED (a new temperature clears blank and quartz)
    ]},
    'metrohm_914': {'steps': [                                                   # probe in a pH 6.86 phosphate buffer (scene)
        ('status', []),
        ('measure', []),                                                         # NO_ENDPOINT
        ('method hold=5s', []),                                                  # MISSING_SETTING tolerance
        ('method hold=5s tolerance=0.1%', [('electrochemistry.configure_endpoint', {'hold_s': 5.0, 'tolerance_fraction': 0.001})]),
        ('calibrate --standard 6.864', []),                                      # UNSTABLE (class requirement)
        ('measure --wait', [('electrochemistry.reading', {})]),
        ('calibrate --standard 6.875', [('electrochemistry.calibrate', {'slot': 'ph', 'standard_value': 6.875})]),   # refused: outside ±0.002 pH
        ('calibrate --standard 6.865', [('electrochemistry.calibrate', {'slot': 'ph', 'standard_value': 6.865})]),   # accepted (914 1.2.4)
        ('calibrate --standard 6.864', []),                                      # UNSTABLE: a calibration resets the endpoint
        ('measure --wait', [('electrochemistry.reading', {})]),
        ('calibrate --standard 6.864', [('electrochemistry.calibrate', {'slot': 'ph', 'standard_value': 6.864})]),   # accepted (within ±0.002)
        ('#wait 10', None),
        ('result', [('electrochemistry.reading', {})]),
        ('method temp=25C', [('electrochemistry.configure_temperature', {'mode': 'manual', 'manual_temperature_k': K0 + 25})]),
        ('method temp=auto interval=2s', [('electrochemistry.configure_temperature', {'mode': 'automatic', 'manual_temperature_k': None}),
                                          ('acquisition.set_interval', {'interval_s': 2.0})]),
        ('method bogus=1', []),                                                  # UNKNOWN_SETTING (no passthrough)
        ('set-temp 25C', []),                                                    # not supported
        ('check', []),                                                           # not supported
    ]},
    'aqualab_4te': {'steps': [                                                   # NaCl sample in, lid closed, lever at READ
        ('status', []),
        ('calibrate --standard 0.880', []),                                      # NO_READING
        ('measure --wait', [('acquisition.start', {}), ('water_activity.measure', {})]),
        ('check --reference 0.880', []),                                         # WARMING_UP
        ('calibrate --standard 0.880', [('calibration.configure', {'offset_aw': -0.0021, 'source': 'chemlab calibrate on aqualab_4te (standard 0.880)'})]),
        ('calibrate --standard 0.880', []),                                      # NO_READING (a new offset clears the reading)
        ('#wait 900', None),
        ('measure --wait', [('acquisition.start', {}), ('water_activity.measure', {})]),
        ('check --reference 0.880', [('calibration.verify', {'reference_aw': 0.88})]),
        ('check --reference 2', []),                                             # OUT_OF_RANGE
        ('method mode=continuous', []),                                          # MISSING_SETTING
        ('method mode=single stability=0.0005 tolerance=0.5K readings=1', [('method.configure', {'mode': 'single', 'stability_delta': 0.0005,
                                                                                                  'temperature_tolerance_k': 0.5, 'consecutive_readings': 1})]),
        ('set-temp 30C', [('thermal.set_temperature', {'temperature_k': K0 + 30})]),
        ('measure', [('acquisition.start', {})]),                                # refused: the set point is not the sample's temperature
        ('zero', []),
        ('clean', []),
    ]},
    'hach_tl2300epa': {'steps': [                                                # 20 NTU StablCal standard in, 30 min warm-up done
        ('status', []),
        ('measure --wait', [('acquisition.start', {}), ('turbidity.measure', {})]),
        ('calibrate', []),                                                       # WARMING_UP (60 min)
        ('check --reference 20NTU', []),                                         # WARMING_UP
        ('#wait 1800', None),
        ('calibrate --standard 10NTU', []),                                      # WRONG_STANDARD (usage)
        ('calibrate', [('calibration.rapid', {})]),
        ('check --reference 20NTU', [('calibration.verify', {'reference_ntu': 20.0})]),
        ('method ratio=off average=10', [('method.configure', {'ratio_enabled': False, 'average_count': 10, 'mode': 'single'})]),
        ('method average=5', []),                                                # BAD_CHOICE
        ('set-temp 20C', []),
        ('zero', []),
        ('clean', []),
    ]},
    'knauer_k7400s': {'steps': [                                                 # no tube, no method
        ('status', []),
        ('method freeze=-6C', []),                                               # MISSING_SETTING
        ('method volume=150uL freeze=-6C cool=-15C interval=1s', [('method.configure', KNAUER_METHOD)]),
        ('measure', []),                                                         # NO_SAMPLE
        ('calibrate --standard 0', []),                                          # NO_SAMPLE
        ('set-temp 20C', []),
        ('check', []),
        ('zero', []),
    ]},
    'knauer_k7400s_water': {'steps': [                                           # method set, water tube on the head
        ('calibrate --standard 0.03', []),                                       # BAD_STANDARD (50 mOsmol/kg grid)
        ('calibrate --standard 0', [('acquisition.start', {'purpose': 'calibration'}), ('calibration.accept', {'known_osmolality_mol_kg': 0.0})]),
        ('measure', []),                                                         # NOT_CALIBRATED (one point)
        ('method volume=50uL freeze=-6C cool=-15C interval=1s', []),             # TUBE_ON_HEAD
    ]},
    'knauer_k7400s_ready': {'steps': [                                           # water + 400 mOsmol/kg calibration, 0.1 mol/kg NaCl on the head
        ('status', []),
        ('measure --wait', [('acquisition.start', {'purpose': 'sample'}), ('process.measure', {})]),
        ('result', [('process.measure', {})]),
        ('calibrate --standard 400mOsmol/kg --dry-run', []),
    ]},
    'mt_hx204': {'steps': [                                                      # 5 g sample (0.5 g water) on the pan
        ('status', []),
        ('set-temp 105.5C', []),                                                 # BAD_STEP
        ('set-temp 105C --wait', []),                                            # NO_WAIT
        ('set-temp 110C', [('thermal.set_temperature', {'temperature_k': K0 + 110})]),
        ('method time=5min', [('method.configure', {'mode': 'standard', 'duration_s': 300.0, 'basis': 'wet'})]),
        ('method basis=dry mode=rapid', [('method.configure', {'mode': 'rapid', 'duration_s': 300.0, 'basis': 'dry'})]),
        ('check', [('procedure.start', {'kind': 'weight_test_internal'})]),     # PAN_NOT_CLEAR
        ('calibrate bogus', []),                                                 # UNKNOWN_NAME
        ('measure --wait', [('acquisition.start', {}), ('moisture.measure', {})]),
        ('result', [('moisture.measure', {})]),
        ('zero', []),
        ('clean', []),
    ]},
    'mt_hx204_empty': {'steps': [                                                # empty pan
        ('measure', []),                                                         # NO_SAMPLE
        ('check', [('procedure.start', {'kind': 'weight_test_internal'})]),
        ('calibrate', [('procedure.start', {'kind': 'weight_adjustment_internal'})]),
        ('check weight --dry-run', []),
    ]},
    'mt_mp70': {'steps': [                                                       # benzoic acid in capillaries 1 and 2
        ('status', []),
        ('measure', []),                                                         # NO_METHOD
        ('method start=110C end=130C rate=1K/min', []),                          # MISSING_SETTING criterion
        ('method start=110C end=130C rate=1K/min criterion=transmission', [
            ('method.configure', {'method_id': 'chemlab', 'start_k': K0 + 110, 'end_k': K0 + 130, 'ramp_k_s': 1 / 60.0, 'criterion': 'transmission'}),
            ('method.select', {'method_id': 'chemlab'})]),
        ('measure --wait --max 3h', [('acquisition.start', {})] + [('melting.measure', {'channel': c}) for c in range(4)]),
        ('result --channel 2', [('melting.measure', {'channel': 1})]),
        ('result --channel 9', []),                                              # UNKNOWN_CHANNEL
        ('set-temp 50C', [('thermal.set_temperature', {'temperature_k': K0 + 50})]),
        ('calibrate', []),
        ('check', []),
        ('zero', []),
        ('clean', []),
    ]},
}

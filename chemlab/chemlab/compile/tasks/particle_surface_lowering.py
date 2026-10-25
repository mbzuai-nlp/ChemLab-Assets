"""Lowering table of the particle_surface class: per member, verbs in order and the native calls (capability, arguments in the
unit each argument declares) each must make - written from the contracts (capability ids, argument names, the contract example
methods the class starts from, the positions at the argument limits), not derived from the mapping.

[] = no native call (a class check refused, a usage error, an unsupported verb, a dry run or a show); None = not checked.
'#wait N' = lab time passes on both labs.  FROM_RUN stands for data the evaluation takes from the last result record (the
measured curves): the capability and every other argument are checked, the data must be a non-empty list.  'prep' is the
host-side world (tasks/particle_surface_world.py), run on the lab and on its twin.
"""
import math

from . import particle_surface_world as W

K0 = 273.15
RAD = math.pi / 180


class _FromRun:
    """Matches the measured data an evaluation takes from the result record (a non-empty list, or None for an optional value)."""

    def __init__(self, optional=False):
        self.optional = optional

    def __eq__(self, other):
        return (isinstance(other, list) and len(other) > 0) or (self.optional and (other is None or isinstance(other, (int, float))))

    def __ne__(self, other):
        return not self.__eq__(other)

    def __repr__(self):
        return 'FROM_RUN?' if self.optional else 'FROM_RUN'

    __hash__ = None


FROM_RUN, FROM_RUN_OPTIONAL = _FromRun(), _FromRun(optional=True)

# contract example methods (the base of `method` before one was set with chemlab)
EX_DLS = {'mode': 'dls', 'angle_deg': 90, 'temperature_k': 293.15, 'entered_viscosity_pa_s': 0.001002, 'entered_refractive_index': 1.333,
          'entered_relative_permittivity': 80.2, 'henry_f': 1.5, 'field_v_m': 500.0, 'acquisition_s': 2.0, 'acquisitions': 3,
          'temperature_tolerance_k': 0.2, 'equilibration_s': 1.0, 'timeout_s': 300.0, 'regularization': 0.002}
EX_PSA = {'mode': 'wet', 'model': 'mie_hybrid', 'n635': 2.9, 'k635': 3.4, 'n830': 2.9, 'k830': 3.1, 'medium_n': 1.33, 'pump_level': 4,
          'stirrer_level': 4, 'ultrasound_enabled': True, 'air_pressure_pa': 200000.0, 'feed_fraction': 0.4, 'dispersion_s': 10.0,
          'acquisition_s': 5.0, 'regularization': 0.0001, 'timeout_s': 300.0}
EX_DIA = {'mode': 'liquid', 'magnification': 1.0, 'pixel_size_m': 3e-06, 'fps': 220.0, 'exposure_s': 1e-06, 'threshold': 0.5,
          'pump_speed_rad_s': 125.66370614359172, 'ultrasound_w': 0.0, 'feed_particles_s': 8.0, 'target_images': 32, 'duration_s': 6.0,
          'accept_shape_reconstruction': False, 'accept_inferred_source_scale': True}
EX_TURB = {'scan_start_m': 0.002, 'scan_end_m': 0.0394, 'scan_interval_s': 8.0, 'duration_s': 30.0, 'temperature_tolerance_k': 0.25,
           'tsi_channel': 'backscatter', 'front_channel': 'backscatter', 'front_threshold_fraction': 0.05}
EX_ASORB = {'station': 0, 'gas': 'N2', 'temperature_k': 77.0, 'relative_pressures': [0.002, 0.005, 0.01, 0.02, 0.03, 0.05, 0.07, 0.1, 0.15, 0.2,
                                                                                      0.25, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9],
            'equilibration_s': 2.0, 'pressure_tolerance_pa': 0.5, 'max_drift_pa_s': 0.05, 'point_timeout_s': 600.0, 'entered_mass_kg': 0.0001}
EX_PMAST = {'stage': 'low', 'pressures_pa': [20684.271879504, 68947.57293168, 206842.71879503998, 344737.8646584], 'pressure_rate_pa_s': 50000.0,
            'equilibration_s': 2.0, 'pressure_tolerance_pa': 15.0, 'max_volume_drift_m3_s': 1e-12, 'point_timeout_s': 180.0,
            'entered_mass_kg': 6.55e-05, 'closure_m3_kg': 0.0, 'surface_tension_n_m': 0.48, 'advancing_angle_rad': 2.443460952792061,
            'receding_angle_rad': 2.443460952792061}
EX_PLUX = {'mode': 'wet_dry', 'pressure_steps_pa': [5000.0, 15000.0, 24000.0, 31000.0, 43000.0, 60000.0], 'minimum_dwell_s': 0.5,
           'maximum_dwell_s': 3.0, 'stability_window_s': 0.3, 'pressure_tolerance_pa': 100.0, 'flow_slope_tolerance_m3_s2': 1e-08,
           'bubble_fraction': 0.05}
EX_UPYC = {'cell': 'medium', 'reference': 'auto', 'direction': 'powder_protect', 'preparation': 'none', 'flow_m3_s': 1e-06, 'preparation_s': 60.0,
           'purge_pulses': 3, 'target_gauge_pa': 100000.0, 'max_runs': 3, 'runs_to_average': 3, 'automatic': False, 'finish_rsd': 0.0001,
           'stage_timeout_s': 600.0, 'equilibration_s': 0.5, 'mode': 'measure', 'leak_hold_s': 20.0, 'leak_limit_pa_s': 1.0}
EX_ACHEM = {'mode': 'tpd', 'segments': [{'target_k': 313.15, 'ramp_k_s': 1.6666666666666667, 'hold_s': 1.0}], 'sample_interval_s': 0.5,
            'maximum_duration_s': 60.0, 'pulse_count': 3, 'pulse_interval_s': 5.0, 'pulse_volume_ref_m3': 1e-07}
EX_DVS = {'temperature_k': 298.15, 'relative_humidity_steps': [0.1, 0.4], 'cycles': 1, 'minimum_dwell_s': 2.0, 'maximum_dwell_s': 10.0,
          'window_s': 2.0, 'dm_dt_kg_s': 1e-10, 'sample_interval_s': 0.2}
EX_SURP = {'ph_targets': [6.0, 5.0, 4.0, 3.0], 'start_pressure_pa': 60000.0, 'stop_pressure_pa': 20000.0, 'cycles': 2, 'fill_volume_m3': 0.0001,
           'mode': 'zeta', 'ramp_timeout_s': 120.0}
EX_SIGMA = {'speed_m_s': 0.008, 'immersion_m': 0.002, 'hold_s': 3.0, 'sample_interval_s': 0.1, 'plate_width_m': 0.0195, 'plate_thickness_m': 0.00012,
            'ring_radius_m': 0.00955, 'analysis_contact_rad': 0.0, 'analysis_density_difference_kg_m3': 791.01243, 'ring_correction': 0.85,
            'accept_ring_proxy': False}
EX_THETA = {'mode': 'static', 'field_width_m': 0.0035, 'frame_rate_hz': 5.0, 'frames': 5, 'flow_m3_s': 1e-10, 'stage_speed_m_s': 0.004,
            'fit': 'polynomial'}
EX_MEDL = {'mode': 'compression', 'target_area_m2': 0.016, 'barrier_speed_m_s': 0.0045, 'target_pressure_n_m': 0.02, 'pressure_tolerance_n_m': 0.0005,
           'duration_s': 40.0, 'sample_interval_s': 0.1, 'dipper_positions_m': [0.073, 0.053], 'dipping_speed_m_s': 0.0018,
           'maximum_solvent_fraction': 0.001, 'blank_leak_area_rate_m2_s': 0.0}
EX_OXTC = {'cell': 'A', 'test_mode': 'convergence', 'cycles': 10, 'exam_minutes': 30.0, 'conditioning_hours': 0.5, 'individual_zero': 'end',
           'convergence_percent': 1.0, 'convergence_count': 3, 'max_cycles': 60, 'report_basis': 'area', 'declared_area_m2': 0.005,
           'declared_thickness_m': 0.0001, 'temperature_tolerance_k': 0.2}
EX_OXINST = {'permeant_concentration_percent': 100.0, 'rezero_enabled': True, 'rezero_frequency': 2, 'rezero_exam_minutes': 30.0,
             'barometer_mode': 'auto', 'manual_barometer_pa': None}
ASORB_VCAL = ('warm_temperature_k=308.15 warm_dosed_mol=0.00039030457583950266 warm_pressure_pa=50000 cold_dosed_mol=0.0009761416063934054 '
              'cold_pressure_pa=50000')
ASORB_VCAL_ARGS = {'warm_temperature_k': 308.15, 'warm_dosed_mol': 0.00039030457583950266, 'warm_pressure_pa': 50000.0,
                   'cold_dosed_mol': 0.0009761416063934054, 'cold_pressure_pa': 50000.0}
ON = [('power.set', {'enabled': True})]


def ex(d, **kw):
    out = dict(d)
    out.update(kw)
    return out


def config():
    """The lowering harness's lab: all203.json with the members bare (the cases' prep applies the class world)."""
    from ...labbackend import load_config
    return W.bare(load_config())


CASES = {
    W.DLS: {'prep': lambda b: W.dls(b), 'steps': [
        ('status', []),
        ('load', []),                                                           # POWER_OFF (member requirement: holder moves only when on)
        ('start', []),                                                          # POWER_OFF
        ('power on', ON),                                                       # shared verb
        ('start', []),                                                          # WARMING_UP (laser 6 min)
        ('set-temp 25C', []),                                                   # not supported (method key)
        ('prepare', []),                                                        # not supported
        ('analyze', []),                                                        # not supported
        ('method', []),                                                         # shows the method, sends nothing
        ('method temp=25C time=3s', [('method.configure', ex(EX_DLS, temperature_k=K0 + 25, acquisition_s=3.0))]),
        ('method angle=45deg', []),                                             # OUT_OF_RANGE (enum 15/90/175)
        ('method runs=5', [('method.configure', ex(EX_DLS, temperature_k=K0 + 25, acquisition_s=3.0, acquisitions=5))]),
        ('calibrate', []),                                                      # WHICH_CALIBRATION (usage)
        ('calibrate size reference=100nm measured=100nm', [('dls.calibrate', {'reference_m': 1e-7, 'measured_m': 1e-7})]),
        ('calibrate temperature reference=20,40 measured=20,40', [('temperature.calibrate', {'reference_k': [K0 + 20, K0 + 40],
                                                                                              'measured_k': [K0 + 20, K0 + 40]})]),
        ('calibrate size reference=100 measured=1', []),                        # OUT_OF_RANGE: bare numbers are mm here
        ('atmosphere gas=on', [('purge.set', {'enabled': True})]),              # refused by the runtime: no purge gas (NO_PURGE_GAS)
        ('unload --dry-run', []),
        ('unload', [('lid.release', {}), ('lid.set', {'angle_rad': 1.7}), ('cuvette.set', {'lift_m': 0.12}), ('cell_cap.set', {'lift_m': 0.022})]),
        ('unload', []),                                                         # already open: nothing to move
        ('start', []),                                                          # ACCESS_OPEN
        ('load', [('cell_cap.set', {'lift_m': 0.0}), ('cuvette.set', {'lift_m': 0.0}), ('lid.set', {'angle_rad': 0.0})]),
        ('#wait 360', None),
        ('start', [('acquisition.start', {})]),
        ('#wait 15', None),
        ('result', [('process.measure', {})]),
        ('result --cell 1', []),                                                # NO_CELL (usage)
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
    ]},
    W.PSA: {'prep': lambda b: W.psa(b, background=False, sample=False, power=False), 'steps': [
        ('prepare', []),                                                        # POWER_OFF
        ('power on', ON),
        ('prepare', []),                                                        # WARMING_UP (60 s)
        ('#wait 61', None),
        ('method time=4s', [('method.configure', ex(EX_PSA, acquisition_s=4.0))]),
        ('start', []),                                                          # NO_BACKGROUND (member requirement)
        ('prepare', [('background.start', {})]),
        ('#wait 70', None),
        ('start', [('acquisition.start', {})]),                                 # refused by the runtime: NO_SAMPLE
        ('unload', [('tank_lid.set', {'slide_m': 0.17})]),
        ('start', []),                                                          # ACCESS_OPEN
        ('load', [('tank_lid.set', {'slide_m': 0.0})]),
        ('calibrate reference=10um measured=10.2um', [('calibration.size', {'reference_m': 1e-5, 'observed_m': 1.02e-5})]),
        ('set-temp 25C', []),                                                   # not supported
        ('atmosphere gas=on', []),                                              # not supported
        ('analyze', []),                                                        # not supported
        ('start --dry-run', []),
        ('result', [('acquisition.result', {})]),
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
    ]},
    W.DIA: {'prep': lambda b: W.dia(b, background=False, sample=False, power=True), 'steps': [
        ('start', []),                                                          # NO_BACKGROUND
        ('method time=4s', [('method.configure', ex(EX_DIA, duration_s=4.0))]),
        ('prepare', [('background.capture', {})]),
        ('unload', [('lid.set', {'angle_rad': 1.55})]),
        ('start', []),                                                          # ACCESS_OPEN
        ('load', [('lid.set', {'angle_rad': 0.0})]),
        ('start', [('acquisition.start', {})]),                                 # refused by the runtime: NO_SAMPLE
        ('calibrate reference=300um measured=100', [('calibration.pixel', {'reference_length_m': 3e-4, 'observed_pixels': 100.0})]),
        ('calibrate pixel reference=300um', []),                                # MISSING_SETTING (usage)
        ('result', [('acquisition.result', {})]),
        ('set-temp 20C', []),                                                   # not supported
        ('analyze', []),                                                        # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
    ]},
    W.TURB: {'prep': lambda b: W.turbiscan(b), 'steps': [
        ('start', []),                                                          # POWER_OFF
        ('power on', ON),
        ('start', []),                                                          # ACCESS_OPEN (lid open, vial up)
        ('load', [('lid.set', {'opened': False}), ('sample.set_position', {'distance_m': 0.0})]),
        ('set-temp 70C', []),                                                   # OUT_OF_RANGE (5..60 °C)
        ('set-temp 30C --wait --max 30min', [('thermal.set_temperature', {'temperature_k': K0 + 30})]),
        ('method time=40s interval=8s', [('method.configure', ex(EX_TURB, duration_s=40.0, scan_interval_s=8.0))]),
        ('calibrate temperature_zero_k=0 transmission_gain=1 transmission_zero_fraction=0 backscatter_gain=1 backscatter_zero_fraction=0',
         [('sensor.calibrate', {'temperature_zero_k': 0.0, 'transmission_gain': 1.0, 'transmission_zero_fraction': 0.0, 'backscatter_gain': 1.0,
                                'backscatter_zero_fraction': 0.0})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 45', None),
        ('result', [('acquisition.result', {})]),
        ('analyze tsi', [('acquisition.result', {}), ('analysis.tsi', {'transmission_fraction': FROM_RUN, 'backscatter_fraction': FROM_RUN,
                                                                         'channel': 'backscatter'})]),
        ('analyze two-flux absorption_m_inv=10 scattering_m_inv=100 path_m=0.025',
         [('analysis.two_flux', {'absorption_m_inv': 10.0, 'scattering_m_inv': 100.0, 'path_m': 0.025})]),
        ('analyze', []),                                                        # WHICH_MODEL (usage)
        ('atmosphere rh=50%', []),                                              # not supported
        ('prepare', []),                                                        # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('unload', [('sample.set_position', {'distance_m': 0.105}), ('lid.set', {'opened': True})]),
    ]},
    W.ASORB: {'prep': lambda b: W.autosorb(b, stations=(0, 1), degas_slot=0), 'steps': [
        ('start', []),                                                          # ACCESS_OPEN
        ('load', [('sample0.set', {'drop_m': 0.0}), ('sample1.set', {'drop_m': 0.0}), ('guard.set', {'angle_rad': 0.0}),
                  ('dewar.set', {'lift_m': 0.29})]),
        ('start --cell 3', []),                                                 # NO_SAMPLE on station 3 (member requirement)
        ('start --cell 4', []),                                                 # UNKNOWN_CELL (usage)
        ('start --cell 1', [('acquisition.start', {'stations': [0]})]),         # refused by the runtime: not calibrated, no method
        ('calibrate pressure --cell 1 reference=0,1000mbar measured=0,1000mbar',
         [('pressure.calibrate', {'station': 0, 'reference_pa': [0.0, 100000.0], 'measured_pa': [0.0, 100000.0]})]),
        ('calibrate volume --cell 1 ' + ASORB_VCAL, [('volume.calibrate', ex(ASORB_VCAL_ARGS, station=0))]),
        ('calibrate pressure --cell 2 reference=0,1000mbar measured=0,1000mbar',
         [('pressure.calibrate', {'station': 1, 'reference_pa': [0.0, 100000.0], 'measured_pa': [0.0, 100000.0]})]),
        ('calibrate volume --cell 2 ' + ASORB_VCAL, [('volume.calibrate', ex(ASORB_VCAL_ARGS, station=1))]),
        ('method --cell 1 mass=100mg', [('method.configure', ex(EX_ASORB, station=0, entered_mass_kg=1e-4))]),
        ('method --cell 2 mass=100mg steps=0.05,0.1,0.2,0.3', [('method.configure', ex(EX_ASORB, station=1, entered_mass_kg=1e-4,
                                                                                          relative_pressures=[0.05, 0.1, 0.2, 0.3]))]),
        ('method --cell 2 station=2', []),                                      # FIXED_SETTING (usage: --cell sets it)
        ('start --cell 1,2', [('acquisition.start', {'stations': [0, 1]})]),
        ('#wait 700', None),
        ('result --cell 1', [('process.measure', {})]),
        ('analyze bjh --cell 1', [('process.measure', {}), ('analysis.bjh', {'points': FROM_RUN, 'branch': 'unspecified'})]),
        ('set-temp 77K', []),                                                   # not supported (LN2 bath)
        ('atmosphere flow=10mL/min', []),                                       # not supported
        ('unload --cell 1', [('dewar.set', {'lift_m': 0.0}), ('sample.backfill', {'station': 0}), ('guard.set', {'angle_rad': 1.5}),
                             ('sample0.set', {'drop_m': 0.18})]),
        ('prepare --temp 120C --hold 2min', [('degas.start', {'steps': [{'temperature_k': K0 + 120, 'hold_s': 120.0}], 'ramp_k_s': 1.0,
                                                              'remaining_fraction_limit': 0.01})]),
        ('#wait 400', None),
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('load --dry-run', []),
    ]},
    W.PMAST: {'prep': lambda b: W.poremaster(b), 'steps': [
        ('start', []),                                                          # ACCESS_OPEN (guard of port 1 open)
        ('load', [('guard0.set', {'angle_rad': 0.0})]),
        ('start', [('acquisition.start', {})]),                                 # refused by the runtime: not calibrated
        ('calibrate', []),                                                      # WHICH_CALIBRATION
        ('calibrate pressure range=0 reference=0,50psi measured=0,50psi',
         [('pressure.calibrate', {'range_index': 0, 'reference_pa': [0.0, 344737.8646584], 'measured_pa': [0.0, 344737.8646584]})]),
        ('calibrate volume reference=0,0.2mL measured=1pF,11pF', [('volume.calibrate', {'reference_volume_m3': [0.0, 2e-7],
                                                                                         'measured_capacitance_f': [1e-12, 1.1e-11]})]),
        ('calibrate blank pressures_pa=0,413685437.59 volumes_m3=0,0', [('blank.calibrate', {'pressures_pa': [0.0, 413685437.59],
                                                                                              'volumes_m3': [0.0, 0.0]})]),
        ('method mass=65.5mg', [('method.configure', ex(EX_PMAST, entered_mass_kg=6.55e-5))]),
        ('start', [('acquisition.start', {})]),
        ('#wait 60', None),
        ('result', [('process.measure', {})]),
        ('prepare', []),                                                        # not supported
        ('set-temp 25C', []),                                                   # not supported
        ('analyze', []),                                                        # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('unload', [('guard0.set', {'angle_rad': 1.4})]),
    ]},
    W.PLUX: {'prep': lambda b: W.porolux(b), 'steps': [
        ('prepare', []),                                                        # POWER_OFF
        ('power on', ON),
        ('prepare', [('sample.wet', {})]),
        ('start', []),                                                          # ACCESS_OPEN (holder open)
        ('load', [('holder.set', {'opened': False})]),
        ('prepare', []),                                                        # HOLDER_CLOSED (member requirement)
        ('start', []),                                                          # GAS_CLOSED
        ('atmosphere gas=on', [('gas.open', {'enabled': True})]),
        ('atmosphere rh=50%', []),                                              # UNKNOWN_SETTING (usage)
        ('calibrate pressure_zero=0 flow_zero=0', [('sensor.calibrate', {'pressure_zero_pa': 0.0, 'flow_zero_m3_s': 0.0})]),
        ('method mode=wet_dry steps=50mbar,150mbar,240mbar,310mbar,430mbar,600mbar', [('method.configure', ex(EX_PLUX))]),
        ('start', [('acquisition.start', {})]),
        ('#wait 40', None),
        ('result', [('acquisition.result', {})]),
        ('analyze porometry tension=16mN/m angle=0 bubble=0.05',
         [('acquisition.result', {}), ('analysis.porometry', {'wet_pressure_pa': FROM_RUN, 'wet_flow_m3_s': FROM_RUN, 'dry_pressure_pa': FROM_RUN,
                                                                'dry_flow_m3_s': FROM_RUN, 'surface_tension_n_m': 0.016, 'contact_angle_rad': 0.0,
                                                                'bubble_fraction': 0.05, 'accept_monotone_projection': False})]),
        ('analyze porometry', []),                                              # MISSING_SETTING (tension, angle, bubble)
        ('set-temp 25C', []),                                                   # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('unload', [('holder.set', {'opened': True})]),
    ]},
    W.UPYC: {'prep': lambda b: W.ultrapyc(b), 'steps': [
        ('start', []),                                                          # ACCESS_OPEN
        ('load', [('cup.set', {'lift_m': 0.0}), ('lid.set', {'lift_m': 0.0}), ('lock.set', {'angle_rad': 1.1}), ('seal.set', {'angle_rad': 1.4})]),
        ('start', []),                                                          # NOT_CALIBRATED (volume)
        ('calibrate volume blank_ratio=1 sphere_ratio=0.8 sphere_volume_m3=1e-5',
         [('volume.calibrate', {'blank_ratio': 1.0, 'sphere_ratio': 0.8, 'sphere_volume_m3': 1e-5})]),
        ('start', []),                                                          # NO_MASS
        ('method mass=50g', [('sample.set_mass', {'mass_kg': 0.05})]),
        ('method runs=3 mode=measure', [('method.configure', ex(EX_UPYC))]),    # base: the method the instrument shows
        ('method --show', []),
        ('set-temp 60C', []),                                                   # OUT_OF_RANGE (3..50 °C)
        ('set-temp 30C', [('thermal.set_temperature', {'temperature_k': K0 + 30})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 70', None),
        ('result', [('process.measure', {})]),
        ('prepare', []),                                                        # SAMPLE_PRESENT (leak test needs the empty cell)
        ('atmosphere gas=on', []),                                              # not supported
        ('analyze', []),                                                        # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('unload', [('seal.set', {'angle_rad': 0.0}), ('lock.set', {'angle_rad': 0.0}), ('lid.set', {'lift_m': 0.13}), ('cup.set', {'lift_m': 0.12})]),
    ]},
    W.ACHEM: {'prep': lambda b: W.autochem(b), 'steps': [
        ('load', [('sample.set_position', {'distance_m': 0.0}), ('sample.release', {'angle_rad': 0.0}), ('furnace.set_open', {'opened': False})]),
        ('start', []),                                                          # POWER_OFF
        ('power on', ON),
        ('atmosphere flow=30mL/min', [('gas.set_flow', {'reference_m3_s': 5e-7})]),
        ('atmosphere flow=200mL/min', []),                                      # OUT_OF_RANGE (0..100 mL/min)
        ('set-temp 40C --ramp 100K/min', [('thermal.set_temperature', {'temperature_k': K0 + 40, 'ramp_k_s': 100 / 60})]),
        ('set-temp 50C', [('thermal.set_temperature', {'temperature_k': K0 + 50, 'ramp_k_s': 100 / 60})]),   # ramp: the furnace's current one
        ('method mode=tpd temp=40C rate=100K/min hold=1s time=60s',
         [('method.configure', ex(EX_ACHEM, segments=[{'target_k': K0 + 40, 'ramp_k_s': 100 / 60, 'hold_s': 1.0}], maximum_duration_s=60.0))]),
        ('start', []),                                                          # NOT_CALIBRATED (TCD)
        ('calibrate detector reference=1umol,2umol measured=0.1,0.2', [('detector.calibrate', {'known_moles_mol': [1e-6, 2e-6],
                                                                                               'observed_areas_v_s': [0.1, 0.2]})]),
        ('start', []),                                                          # GAS_CLOSED
        ('atmosphere gas=on', [('gas.open', {'enabled': True})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 15', None),
        ('result', [('acquisition.result', {})]),
        ('analyze dispersion metal_mass=10mg molar_mass=58.69g/mol stoichiometry=1',
         [('acquisition.result', {}), ('analysis.dispersion', {'adsorbed_mol': FROM_RUN_OPTIONAL, 'metal_mass_kg': 1e-5,
                                                                 'metal_molar_mass_kg_mol': 0.05869, 'adsorbate_per_surface_atom': 1.0})]),
        ('prepare', []),                                                        # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('#wait 300', None),
        ('unload', [('gas.open', {'enabled': False}), ('furnace.set_open', {'opened': True}), ('sample.release', {'angle_rad': 0.6}),
                    ('sample.set_position', {'distance_m': 0.12})]),
    ]},
    W.DVS: {'prep': lambda b: W.dvs(b, tared=True), 'steps': [
        ('start', []),                                                          # POWER_OFF
        ('power on', ON),
        ('prepare', []),                                                        # SAMPLE_ON_PAN (tare with the empty pan)
        ('start', []),                                                          # ACCESS_OPEN
        ('load', [('chamber.set_position', {'distance_m': 0.0}), ('reservoir.open', {'distance_m': 0.0}), ('door.set', {'opened': False})]),
        ('start', []),                                                          # GAS_CLOSED
        ('set-temp 25C', [('thermal.set_temperature', {'temperature_k': K0 + 25})]),
        ('atmosphere rh=10% flow=100mL/min', [('humidity.set', {'relative_humidity': 0.1}), ('gas.set_flow', {'reference_m3_s': 1e-4 / 60})]),
        ('atmosphere rh=120%', []),                                             # OUT_OF_RANGE (0..98 %)
        ('method steps=10%,40% time=10s', [('method.configure', ex(EX_DVS))]),
        ('atmosphere gas=on', [('gas.open', {'enabled': True})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 20', None),
        ('result', [('acquisition.result', {})]),
        ('analyze hysteresis adsorption_rh=0.1,0.5,0.9 adsorption_uptake_kg_kg=0.01,0.1,0.2 desorption_rh=0.1,0.5,0.9 '
         'desorption_uptake_kg_kg=0.02,0.13,0.22',
         [('analysis.hysteresis', {'adsorption_rh': [0.1, 0.5, 0.9], 'adsorption_uptake_kg_kg': [0.01, 0.1, 0.2], 'desorption_rh': [0.1, 0.5, 0.9],
                                   'desorption_uptake_kg_kg': [0.02, 0.13, 0.22]})]),
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('unload', [('gas.open', {'enabled': False}), ('door.set', {'opened': True}), ('chamber.set_position', {'distance_m': 0.135})]),
        ('calibrate reference=0,0.1g measured=0,0.1g', [('balance.calibrate', {'reference_masses_kg': [0.0, 1e-4], 'observed_masses_kg': [0.0, 1e-4]})]),
    ]},
    W.SURP: {'prep': lambda b: W.surpass(b), 'steps': [
        ('start', []),                                                          # POWER_OFF
        ('power on', ON),
        ('start', []),                                                          # NO_METHOD
        ('method steps=6,5,4 runs=1', [('method.configure', ex(EX_SURP, ph_targets=[6.0, 5.0, 4.0], cycles=1))]),
        ('start', []),                                                          # NOT_CALIBRATED (pH)
        ('calibrate ph reference=4,7 measured=-0.23664,-0.41412', [('ph.calibrate', {'buffer_ph': [4.0, 7.0], 'measured_v': [-0.23664, -0.41412]})]),
        ('calibrate conductivity reference=1.413mS/cm measured=1.413mS/cm', [('conductivity.calibrate', {'reference_s_m': 0.1413,
                                                                                                          'indicated_s_m': 0.1413})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 150', None),
        ('result', [('process.measure', {})]),
        ('load', []),                                                           # already closed and clamped
        ('unload', []),                                                         # ELECTROLYTE_IN (member requirement)
        ('prepare', []),                                                        # not supported (rinse is a method mode)
        ('set-temp 25C', []),                                                   # not supported
        ('analyze', []),                                                        # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
    ]},
    W.SIGMA: {'prep': lambda b: W.sigma(b), 'steps': [
        ('start', []),                                                          # POWER_OFF
        ('power on', ON),
        ('start', []),                                                          # ACCESS_OPEN (door open)
        ('load', [('door.set', {'angle_rad': 0.0})]),
        ('start', []),                                                          # NOT_TARED
        ('calibrate reference=0,0.1N measured=0,0.1N', [('balance.calibrate', {'reference_forces_n': [0.0, 0.1], 'observed_raw_forces_n': [0.0, 0.1]})]),
        ('prepare', [('balance.tare', {})]),                                    # waits for a steady force reading first
        ('method probe=ring', [('probe.select', {'probe': 'ring'})]),           # refused by the runtime: door closed (DOOR_CLOSED)
        ('method depth=2mm speed=8mm/s', [('method.configure', ex(EX_SIGMA))]),
        ('set-temp 300C', []),                                                  # OUT_OF_RANGE (-20..200 °C)
        ('set-temp 20C', [('thermal.set_temperature', {'temperature_k': K0 + 20})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 35', None),
        ('result', [('acquisition.result', {})]),
        ('analyze cmc concentrations_kg_m3=0.001,0.002,0.004,0.008,0.016,0.032,0.064,0.128 tensions=70mN/m,65mN/m,60mN/m,55mN/m,50mN/m,50mN/m,50mN/m,50mN/m',
         [('analysis.cmc', {'concentrations_kg_m3': [0.001, 0.002, 0.004, 0.008, 0.016, 0.032, 0.064, 0.128],
                            'tensions_n_m': [0.07, 0.065, 0.06, 0.055, 0.05, 0.05, 0.05, 0.05]})]),
        ('atmosphere rh=50%', []),                                              # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('unload', [('door.set', {'angle_rad': 2.35})]),
    ]},
    W.THETA: {'prep': lambda b: W.theta(b), 'steps': [
        ('load', []),                                                           # POWER_OFF (member requirement)
        ('power on', ON),
        ('start', []),                                                          # NOT_CALIBRATED (camera)
        ('calibrate camera reference=1mm measured=566.8571428571428', [('camera.calibrate', {'known_length_m': 0.001,
                                                                                             'observed_pixels': 566.8571428571428})]),
        ('load --volume 20uL', []),                                             # OUT_OF_RANGE (up to 10 µL)
        ('load', [('stage.move', {'x_m': 0.04, 'y_m': 0.02, 'z_m': 0.025}), ('dispenser.move', {'position_m': 0.02}),
                  ('liquid.transfer', {'volume_m3': 2e-10}), ('dispenser.move', {'position_m': 0.0})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 3', None),
        ('result', [('acquisition.result', {})]),
        ('method frames=10 rate=10Hz', [('method.configure', ex(EX_THETA, frames=10, frame_rate_hz=10.0))]),
        ('analyze hysteresis advancing=80,82 receding=30,31', [('analysis.hysteresis', {'advancing_rad': [80 * RAD, 82 * RAD],
                                                                                         'receding_rad': [30 * RAD, 31 * RAD]})]),
        ('prepare', []),                                                        # not supported
        ('set-temp 25C', []),                                                   # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
        ('unload', [('stage.move', {'x_m': 0.0, 'y_m': 0.0, 'z_m': 0.0})]),
    ]},
    W.MEDL: {'prep': lambda b: W.mediuml(b), 'steps': [
        ('load', []),                                                           # NOT_TARED (spreading needs the clean-subphase tare)
        ('calibrate force_gain_correction=1 force_zero_n=0 assumed_contact_cosine=1',
         [('balance.calibrate', {'force_gain_correction': 1.0, 'force_zero_n': 0.0, 'assumed_contact_cosine': 1.0})]),
        ('prepare', [('probe.move', {'distance_m': 0.0}), ('balance.tare', {})]),
        ('load --volume 50uL', [('film.spread', {'volume_m3': 5e-8})]),
        ('#wait 4', None),
        ('method mode=compression area=140cm2 time=60s', [('method.configure', ex(EX_MEDL, target_area_m2=0.014, duration_s=60.0))]),
        ('method pressure=25mN/m', [('method.configure', ex(EX_MEDL, target_area_m2=0.014, duration_s=60.0, target_pressure_n_m=0.025))]),
        ('start', [('acquisition.start', {})]),
        ('#wait 25', None),
        ('result', [('acquisition.result', {})]),
        ('analyze isotherm', [('acquisition.result', {}), ('analysis.isotherm', {'area_m2': FROM_RUN, 'surface_pressure_n_m': FROM_RUN,
                                                                                   'constant_molecule_number': True})]),
        ('unload', []),                                                         # not supported (cleaning is the operator's)
        ('set-temp 25C', []),                                                   # not supported
        ('stop', [('acquisition.stop', {})]),                                   # refused: NOT_RUNNING
    ]},
    W.OXTR: {'prep': lambda b: W.oxtran(b), 'steps': [
        ('start', []),                                                          # MISSING_CELL (usage)
        ('start --cell C', []),                                                 # UNKNOWN_CELL (usage)
        ('start --cell A', []),                                                 # ACCESS_OPEN (cell A not clamped)
        ('load --cell A', [('cell.load_unload', {'cell': 'A'})]),
        ('start --cell A', []),                                                 # NO_METHOD
        ('method --cell A mode=standard runs=3 time=30min conditioning=0h',
         [('instrument.configure', EX_OXINST),
          ('test.configure', ex(EX_OXTC, test_mode='standard', cycles=3, exam_minutes=30.0, conditioning_hours=0.0))]),
        ('set-temp 25C', [('thermal.set_temperature', {'target_k': K0 + 25})]),
        ('atmosphere rh=50%', [('humidity.set', {'rh_fraction': 0.5})]),
        ('start --cell A', [('test.start', {'cell': 'A'})]),
        ('#wait 60000', None),
        ('result --cell A', [('test.result', {'cell': 'A'})]),
        ('analyze otr --cell A pressure=101325Pa thickness=100um convergence=1 window=3',
         [('test.result', {'cell': 'A'}), ('analysis.otr', {'readings_mol_m2_s': FROM_RUN, 'individual_zero_mol_m2_s': FROM_RUN_OPTIONAL,
                                                            'driving_partial_pressure_pa': 101325.0, 'thickness_m': 1e-4, 'convergence_percent': 1.0,
                                                            'window': 3})]),
        ('calibrate adjustment --cell A name=cert1 reference=0.05',
         [('sensor.adjustment_create', {'cell': 'A', 'name': 'cert1', 'reference_value': 0.05})]),   # refused by the runtime: GAIN_OUT_OF_RANGE
        ('prepare', []),                                                        # not supported
        ('stop --cell A', [('test.stop', {'cell': 'A'})]),                      # refused: NOT_RUNNING
        ('unload --cell A', [('cell.load_unload', {'cell': 'A'})]),
    ]},
}

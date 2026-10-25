"""Lowering table of the gas_fluid class: per member, verbs in order and the native calls (capability, arguments in the unit the
argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a read, a wait, or a verb this member does not have); None = not checked.  The labs carry the class world
(tasks/gas_fluid_world.py).  Shared verbs (power) appear only where a member must be switched on first."""
from .gas_fluid_world import config  # noqa: F401  (the lowering harness builds its labs from this config)

K0 = 273.15
PSI = 6894.757293168
SCCM = 1e-6 / 60.0


def _eval(iid, cap, args, rid):
    def prep(backend):
        r = backend.evaluator_invoke(iid, cap, args, rid)
        assert r.get('status') == 'succeeded', (cap, r)
    return prep


CASES = {
    'alicat_mc_1slpm_rev6': {'steps': [
        ('status', []),
        ('setpoint 500sccm', []),                                                 # NO_GAS (no gas selected)
        ('select nitrogen', [('gas.select', {'gas_id': 'gas_nitrogen'})]),
        ('select nitrogen', []),                                                  # already selected
        ('zero', [('flow.zero', {})]),
        ('setpoint 500sccm --wait --max 1min', [('flow.set_setpoint', {'flow_rate_m3_s': 500 * SCCM})]),
        ('setpoint 2slpm', []),                                                   # OUT_OF_RANGE (1 SLPM full scale)
        ('select argon', []),                                                     # FLOWING
        ('zero', []),                                                             # FLOWING
        ('read', []),
        ('setpoint 0sccm', [('flow.set_setpoint', {'flow_rate_m3_s': 0.0})]),
        ('select argon', [('gas.select', {'gas_id': 'gas_argon'})]),
        ('select unobtainium', [('gas.select', {'gas_id': 'gas_unobtainium'})]),  # refused by the runtime: GAS_NOT_AVAILABLE
        ('start', []),                                                            # not supported (continuous controller)
    ]},
    'bronkhorst_p702cv_5bar_gauge_n2': {'steps': [
        ('status', []),
        ('setpoint 0.5bar', []),                                                  # OUT_OF_RANGE (1..5 bar g)
        ('setpoint 3bar', [('pressure.set_setpoint', {'pressure_Pa': 300000.0}), ('control.set_mode', {'mode': 'automatic'})]),
        ('setpoint 2.5bar --wait --max 20min', [('pressure.set_setpoint', {'pressure_Pa': 250000.0})]),   # already automatic
        ('read', []),
        ('zero', [('sensor.calibrate_zero', {})]),
        ('start', []),                                                            # not supported (continuous controller)
        ('vent', []),                                                             # not supported
        ('setpoint 2bar --dry-run', []),
    ]},
    'bronkhorst_cemw': {'steps': [
        ('status', []),
        ('setpoint 500sccm', []),                                                 # POWER_OFF (off in the default lab)
        ('power on', [('power.set', {'on': True})]),                              # shared verb: switch it on
        ('setpoint 500', []),                                                     # UNIT_NEEDED (flow, mass flow and temperature)
        ('setpoint 500sccm', [('gas.set_flow', {'setpoint_m3_s': 500 * SCCM})]),
        ('setpoint 12slpm', []),                                                  # OUT_OF_RANGE (10 L/min full scale)
        ('setpoint 140C', [('heater.set_temperature', {'setpoint_k': K0 + 140})]),
        ('setpoint 3.6g/h --wait --max 20min', [('liquid.set_flow', {'setpoint_kg_s': 1e-6})]),
        ('vent', []),                                                             # LIQUID_FLOWING
        ('setpoint 0g/h', [('liquid.set_flow', {'setpoint_kg_s': 0.0})]),
        ('vent', [('bleed.set', {'open': True})]),
        ('setpoint 1g/h', []),                                                    # BLEED_OPEN
        ('vent off', [('bleed.set', {'open': False})]),
        ('read', []),
        ('zero', []),                                                             # not supported
        ('select argon', []),                                                     # not supported (plumbing)
    ]},
    'vici_euta_2sd6mwe': {'steps': [
        ('status', []),
        ('select 3 --wait', [('valve.select_position', {'position': 3})]),
        ('select 3', []),                                                         # already there
        ('select 9', []),                                                         # UNKNOWN_POSITION (6 positions)
        ('select 5', [('valve.select_position', {'position': 5})]),
        ('select 2', []),                                                         # BUSY (still moving)
        ('#wait 2', None),
        ('read', []),
        ('setpoint 1bar', []),                                                    # not supported
    ]},
    'vacuubrand_pc3001_vario_select': {'prep': _eval('vacuubrand_pc3001_vario_select', 'vacuum.set_mode', {'mode': 'pump_down'}, 'prep-mode'), 'steps': [
        ('status', []),
        ('setpoint 50mbar --wait --max 5min', []),                                # NOT_RUNNING (pump off)
        ('setpoint 50mbar', [('vacuum.set_pressure', {'pressure_pa': 5000.0}), ('vacuum.set_mode', {'mode': 'pressure_control'})]),
        ('start', [('vacuum.start', {})]),
        ('setpoint 30mbar --wait --max 10min', [('vacuum.set_pressure', {'pressure_pa': 3000.0})]),
        ('calibrate reference=1013mbar', []),                                     # NOT_AT_REFERENCE (it reads 30 mbar)
        ('vent', [('vent.control', {'action': 'atmosphere'})]),
        ('#wait 300', None),
        ('calibrate reference=1013mbar', [('sensor.adjust', {'reference_pa': 101300.0})]),
        ('vent off', []),                                                         # already closed (it closes itself at atmosphere)
        ('start', [('vacuum.start', {})]),
        ('stop', [('vacuum.stop', {})]),
        ('setpoint 1mbar', []),                                                   # OUT_OF_RANGE (2 mbar minimum)
        ('read', []),
        ('zero', []),                                                             # not supported
    ]},
    'peak_precisionhydrogen': {'steps': [
        ('status', []),
        ('setpoint 60psi', [('settings.configure', {'pressure_set_pa': 60 * PSI})]),
        ('setpoint 120psi', []),                                                  # OUT_OF_RANGE (100 psi)
        ('start --wait', [('cycle.start', {})]),
        ('vent', []),                                                             # RUNNING (depressurise only in standby)
        ('stop', [('cycle.stop', {})]),
        ('vent', [('output.depressurise', {})]),
        ('vent off', []),                                                         # no vent off
        ('read', []),
        ('select hydrogen', []),                                                  # not supported
    ]},
    'elga_purelab_flex3': {'steps': [
        ('status', []),
        ('dispense 500mL', []),                                                   # NO_RATE
        ('dispense 500mL --rate 1L/min --wait', [('water.set_volume', {'volume_m3': 0.0005}), ('water.set_flow_rate', {'flow_m3_s': 1e-3 / 60}),
                                                 ('water.start', {})]),
        ('setpoint 0.5L/min', [('water.set_flow_rate', {'flow_m3_s': 0.5e-3 / 60})]),
        ('setpoint 3L/min', []),                                                  # OUT_OF_RANGE (2 L/min)
        ('dispense 200mL', [('water.set_volume', {'volume_m3': 0.0002}), ('water.start', {})]),
        ('dispense 100mL', []),                                                   # BUSY (dispensing)
        ('stop', [('water.stop', {})]),
        ('#wait 5', None),
        ('start', [('water.start', {})]),                                         # the volume and rate set before
        ('stop', [('water.stop', {})]),
        ('calibrate enter', []),                                                  # MISSING_SETTING (usage)
        ('read', []),
        ('vent', []),                                                             # not supported
    ]},
    'eco_ncld822': {'prep': _eval('eco_ncld822', 'range.select', {'index': 3}, 'prep-range'), 'steps': [
        ('status', []),
        ('zero', []),                                                             # NO_ZERO_GAS (ambient air on the inlet)
        ('select zero_air', [('inlet.connect_standard', {'standard_id': 'zero_air'})]),
        ('start', [('measurement.start', {})]),
        ('#wait 60', None),
        ('zero', [('calibration.zero', {})]),
        ('#wait 60', None),
        ('calibrate span=2954ppm', []),                                           # NO_SPAN_GAS (zero air on the inlet)
        ('select srm_2631a', [('inlet.connect_standard', {'standard_id': 'srm_2631a'})]),
        ('#wait 60', None),
        ('calibrate span=2954ppm', [('calibration.configure', {'span_no_mol_mol': 0.002954, 'span_nox_mol_mol': 0.002954, 'window_s': 30.0}),
                                    ('calibration.span', {})]),
        ('#wait 60', None),
        ('select sample', [('inlet.disconnect', {})]),
        ('read', []),
        ('stop', [('measurement.stop', {})]),
        ('setpoint 1bar', []),                                                    # not supported
    ]},
    'inficon_ul1000fab': {'steps': [
        ('status', []),
        ('zero', []),                                                             # NOT_MEASURING
        ('start --wait', [('measure.start', {})]),
        ('zero', [('zero.set', {'enabled': True})]),
        ('zero off', [('zero.set', {'enabled': False})]),
        ('read', []),
        ('stop', [('measure.stop', {})]),
        ('calibrate external', []),                                               # MISSING_SETTING (usage)
        ('calibrate', [('calibration.start', {'kind': 'internal'})]),
        ('#wait 120', None),
        ('vent', [('measure.stop', {'hold_s': 3.0})]),
        ('setpoint 1bar', []),                                                    # not supported
    ]},
    'inert_purelab_he_2gb': {'steps': [
        ('status', []),
        ('setpoint 6mbar', [('pressure.set', {'pressure_pa': 600.0})]),
        ('setpoint 20mbar', []),                                                  # OUT_OF_RANGE (+-15 mbar)
        ('start', [('purification.set', {'enabled': True})]),
        ('start', []),                                                            # already running
        ('stop', [('purification.set', {'enabled': False})]),
        ('read', []),                                                             # (owned by the enclosure part of this composite)
        ('vent', []),                                                             # not supported
    ]},
}

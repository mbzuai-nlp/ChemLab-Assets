"""Lowering table of the extraction class: per member, verbs in order and the native calls (capability, arguments in the unit
the argument declares) each must make - written from the contracts, not from the mapping.  [] = no native call (a class check
refused, a usage error, an unsupported verb, a read); None = not checked.  "<slug>_empty" entries are a second copy of a member
before the lab loads the sample (methods can be set there); "biotage_extrahera_classic_ready" has its pressure manifold lowered
(prep, the same native call on both labs)."""
from .extraction_world import config as world

K0 = 273.15
MLMIN = 1e-6 / 60
SE_NAMES = ['Acetone', 'Ethanol', 'Methanol', 'Hexane']
EXH_READY = 'biotage_extrahera_classic_ready'


def config():
    copies = {'buchi_speedextractore_empty': 'buchi_speedextractore', 'sft_sft110_empty': 'sft_sft110', 'syrris_asiafllex_empty': 'syrris_asiafllex',
              'sartorius_vivaflow200_empty': 'sartorius_vivaflow200', 'pccell_ed64002_empty': 'pccell_ed64002', EXH_READY: 'biotage_extrahera_classic'}
    return world(variants={k: 'empty' for k in copies if k.endswith('_empty')}, copies=copies)


def lower_manifold(backend):
    r = backend.invoke('agent', EXH_READY, 'manifold.set_position', {'z_m': 0}, 'lowering-prep-manifold')
    assert r.get('status') == 'succeeded', r


def spe(reps=2, vol=1e-6, flow=6 * MLMIN, drain=6 * MLMIN):
    return {'steps': [{'phase': ph, 'solvent_position': ph, 'volume_m3': vol, 'flow_m3_s': flow, 'drain_flow_m3_s': drain, 'repetitions': reps}
                      for ph in ('load', 'wash', 'elute')]}


def se_method(**kw):
    m = {'temperature_k': K0 + 100, 'pressure_gauge_pa': 1e7, 'cycles': 1, 'hold_s': 120.0, 'discharge_s': 120.0, 'pump_total_m3_s': 50 * MLMIN,
         'solvent_fractions': [1.0, 0.0, 0.0, 0.0], 'solvent_flush_s': 60.0, 'nitrogen_flush_s': 180.0, 'vial': '240', 'cell_volume_m3': 2e-05,
         'solvent_names': SE_NAMES}
    m.update(kw)
    return m


CASES = {
    'buchi_speedextractore': {'steps': [
        ('status', []),
        ('method temp=120C', []),                                                          # METHOD_LOCKED: cells loaded
        ('pressure 120bar', []),                                                           # METHOD_LOCKED
        ('set-temp 110C', []),                                                             # CELLS_LOADED
        ('flow 2mL/min', []),                                                              # not supported
        ('vent', []),                                                                      # not supported
        ('open shield', [('shield.set_lift', {'lift_m': 0.18})]),
        ('start', []),                                                                     # SHIELD_OPEN
        ('close shield', [('shield.set_lift', {'lift_m': 0})]),
        ('start --dry-run', []),
        ('start', [('acquisition.start', {})]),
        ('#wait 600', None),
        ('result', [('extraction.measure', {})]),
        ('stop', [('acquisition.stop', {})]),                                              # the run is over: the runtime refuses
        ('prime --time 30s', [('flush.configure', {'solvent_s': 30.0}), ('flush.start', {})]),
        ('prime --inlet S1', []),                                                          # NOT_FOR_THIS
    ]},
    'buchi_speedextractore_empty': {'steps': [
        ('method temp=120C cycles=3', [('method.configure', se_method(temperature_k=K0 + 120, cycles=3))]),
        ('method solvents=0.5,0.6,0,0', []),                                               # fractions do not add up to 1
        ('method solvents=0.5,0.5,0,0', [('method.configure', se_method(temperature_k=K0 + 120, cycles=3, solvent_fractions=[0.5, 0.5, 0.0, 0.0]))]),
        ('pressure 120bar', [('method.configure', se_method(temperature_k=K0 + 120, cycles=3, solvent_fractions=[0.5, 0.5, 0.0, 0.0], pressure_gauge_pa=1.2e7))]),
        ('pressure 30bar', []),                                                            # OUT_OF_RANGE (50-150 bar)
        ('set-temp 120C', [('thermal.preheat', {'temperature_k': K0 + 120, 'enabled': True})]),
        ('set-temp off', [('thermal.preheat', {'enabled': False})]),
        ('prime', [('flush.start', {})]),                                                  # the runtime: no position selected
        ('start', []),                                                                     # NO_SAMPLE
    ]},
    'biotage_extrahera_classic': {'steps': [
        ('status', []),
        ('prime --inlet S1', []),                                                          # NO_SOLVENT
        ('prime --inlet S9', []),                                                          # UNKNOWN_INLET
        ('prime --inlet S1 --solvent water', [('solvent.lock', {'locks': {'S1': 'sol-002'}}), ('run.prime', {'inlet': 'S1', 'volume_m3': 1.5e-05})]),
        ('prime --inlet S1 --volume 10mL', [('run.prime', {'inlet': 'S1', 'volume_m3': 1e-05})]),
        ('prime --inlet S1 --volume 30mL', []),                                            # OUT_OF_RANGE (max 25 mL)
        ('method load=1mL wash=1mL elute=1mL flow=6mL/min', []),                           # MISSING_SETTING drain
        ('method load=2mL wash=1mL elute=1mL flow=6mL/min drain=6mL/min', []),             # OUT_OF_RANGE (1 mL per stroke)
        ('method load=1mL wash=1mL elute=1mL flow=6mL/min drain=6mL/min reps=2', [('method.configure', spe())]),
        ('method load=1mL wash=1mL elute=1mL flow=6mL/min drain=6mL/min elute.solvent=eluent', []),   # UNKNOWN_POSITION
        ('start', []),                                                                     # MANIFOLD_UP
        ('pressure 1.05bar', []),                                                          # NOT_ON_STEP
        ('pressure 1bar', []),                                                             # MANIFOLD_UP
        ('set-temp 30C', []),
        ('vent', []),
        ('flow 1mL/min', []),
    ]},
    EXH_READY: {'prep': lower_manifold, 'steps': [
        ('pressure 1bar', [('pressure.set', {'pressure_pa': 100000.0})]),
        ('start', []),                                                                     # NO_METHOD
        ('method load=1mL wash=0.5mL elute=1mL flow=6mL/min drain=3mL/min',
         [('method.configure', {'steps': [dict(s, drain_flow_m3_s=3 * MLMIN, repetitions=1, volume_m3=0.5e-6 if s['phase'] == 'wash' else 1e-6)
                                          for s in spe()['steps']]})]),
        ('start', [('method.start', {})]),
        ('pressure 2bar', []),                                                             # BUSY
        ('#wait 200', None),
        ('result', []),
        ('stop', [('method.stop', {})]),
        ('pressure off', [('pressure.set', {'pressure_pa': 0})]),
    ]},
    'sft_sft110': {'steps': [
        ('status', []),
        ('method temp=50C', []),                                                           # METHOD_LOCKED
        ('set-temp 45C', [('setpoint.set', {'setpoint': 'temperature_k', 'value': K0 + 45})]),   # outside the sample's solubility data
        ('set-temp 300C', []),                                                             # OUT_OF_RANGE (5-200 °C)
        ('pressure 250bar', [('setpoint.set', {'setpoint': 'pressure_gauge_pa', 'value': 2.5e7})]),
        ('pressure 50bar', []),                                                            # OUT_OF_RANGE (75-689 bar)
        ('flow 1mL/min', []),
        ('prime', []),
        ('start', [('acquisition.start', {})]),
        ('vent', []),                                                                      # RUNNING
        ('#wait 300', None),
        ('stop', [('acquisition.stop', {})]),
        ('vent', [('pressure.vent', {})]),
        ('#wait 120', None),
        ('result', [('extraction.measure', {})]),
    ]},
    'sft_sft110_empty': {'steps': [
        ('method temp=50C', []),                                                           # MISSING_SETTING
        ('method temp=45C pressure=200bar flow=10mL/min static=1min dynamic=2min restrictor=80C timeout=1h modifier=0',
         [('method.configure', {'temperature_k': K0 + 45, 'pressure_gauge_pa': 2e7, 'liquid_flow_m3_s': 10 * MLMIN, 'static_s': 60.0, 'dynamic_s': 120.0,
                                'restrictor_temperature_k': K0 + 80, 'timeout_s': 3600.0, 'modifier_fraction': 0.0})]),
        ('set-temp 50C', [('setpoint.set', {'setpoint': 'temperature_k', 'value': K0 + 50})]),
        ('start', []),                                                                     # NO_SAMPLE
    ]},
    'syrris_asiafllex': {'steps': [
        ('status', []),
        ('method bpr=3bar', []),                                                           # METHOD_LOCKED
        ('pressure 3.05bar', []),                                                          # NOT_ON_STEP
        ('pressure 3bar', [('pressure.setpoint', {'setpoint': 'main', 'value_pa': 300000.0})]),
        ('pressure 100mbar --channel cmp', [('pressure.setpoint', {'setpoint': 'cmp', 'value_pa': 10000.0})]),
        ('pressure 1bar --channel xyz', []),                                               # BAD_CHOICE
        ('set-temp 30C', []),
        ('vent', []),                                                                      # NOTHING_TO_VENT
        ('start', [('acquisition.start', {})]),
        ('vent', []),                                                                      # RUNNING
        ('#wait 200', None),
        ('result', [('separation.measure', {})]),
        ('vent', [('pressure.release', {})]),
        ('#wait 60', None),
        ('pressure off', [('pressure.setpoint', {'setpoint': 'main', 'value_pa': 0})]),
        ('stop', [('acquisition.stop', {})]),                                              # nothing running: the runtime refuses
    ]},
    'syrris_asiafllex_empty': {'steps': [
        ('method aqueous=0.1mL/min organic=0.1mL/min bpr=2bar cmp=100mbar time=2min timeout=30min stop_on_breakthrough=on',
         [('method.configure', {'aqueous_flow_m3_s': 0.1 * MLMIN, 'organic_flow_m3_s': 0.1 * MLMIN, 'bpr_gauge_pa': 2e5, 'cross_membrane_pa': 1e4,
                                'duration_s': 120.0, 'timeout_s': 1800.0, 'stop_on_breakthrough': True})]),
        ('method aqueous=1mL/min', []),                                                    # OUT_OF_RANGE (max 0.5 mL/min)
        ('start', []),                                                                     # NO_SAMPLE
    ]},
    'sartorius_vivaflow200': {'steps': [
        ('status', []),
        ('method tmp=1bar', []),                                                           # METHOD_LOCKED
        ('pressure 1bar', []),
        ('flow 300mL/min', []),
        ('start', [('acquisition.start', {})]),
        ('#wait 120', None),
        ('stop', [('acquisition.stop', {})]),
        ('result', [('filtration.measure', {})]),
        ('start', [('acquisition.start', {})]),                                            # a stopped run resumes
        ('stop', [('acquisition.stop', {})]),
        ('power on', []),                                                                  # no power switch
    ]},
    'sartorius_vivaflow200_empty': {'steps': [
        ('method mode=concentrate circulation=300mL/min tmp=2bar retentate=50mL diavolumes=5 timeout=2h',
         [('method.configure', {'mode': 'concentrate', 'circulation_m3_s': 300 * MLMIN, 'tmp_pa': 2e5, 'target_retentate_m3': 5e-5,
                                'target_diavolumes': 5.0, 'timeout_s': 7200.0})]),
        ('method mode=filter', []),                                                        # BAD_CHOICE
        ('start', []),                                                                     # NO_SAMPLE
    ]},
    'pccell_ed64002': {'steps': [
        ('status', []),
        ('method voltage=10V', []),                                                        # METHOD_LOCKED
        ('flow 1mL/min', [('flow.set_rate', {'flow_m3_s': 1 * MLMIN})]),
        ('flow 10L/min', []),                                                              # OUT_OF_RANGE (max 6 L/min)
        ('start', []),                                                                     # POWER_OFF
        ('power on', [('power.set', {'enabled': True})]),
        ('start', [('acquisition.start', {})]),
        ('#wait 60', None),
        ('stop', [('acquisition.stop', {})]),
        ('result', [('electrodialysis.measure', {})]),
        ('pressure 1bar', []),
        ('set-temp 25C', []),
        ('prime', []),
        ('vent', []),
    ]},
    'pccell_ed64002_empty': {'steps': [
        ('flow 1mL/min', []),                                                              # NO_METHOD
        ('method mode=constant_current voltage=20V current=10mA flow=0.6L/min time=2min timeout=150s endpoint=0',
         [('method.configure', {'mode': 'constant_current', 'voltage_v': 20.0, 'current_a': 0.01, 'flow_m3_s': 1e-05, 'duration_s': 120.0,
                                'timeout_s': 150.0, 'endpoint_conductivity_s_m': 0.0})]),
        ('flow 1mL/min', [('flow.set_rate', {'flow_m3_s': 1 * MLMIN})]),
        ('start', []),                                                                     # POWER_OFF
    ]},
}

"""Formulation class ("formulation"): instruments that turn a material into particles, fibres, beads or extrudate.

Members (configs/adapters/formulation.json): Thermo Fisher Process 11 (twin-screw extruder, 7 barrel zones + die), Linari RT
Advanced (electrospinning), BUCHI Encapsulator B-390 (vibrating nozzle), Cytiva NanoAssemblr Ignite (microfluidic nanoparticles),
Glatt Mini-Glatt (fluid-bed granulator / coater).

Verbs: set-temp T [--zone N | --channel C] [--wait], heat on|off, temp, wait-temp (every zone when no --zone on the extruder),
speed RATE|on|off (screw speed), vibration FREQ|on|off (nozzle vibration), feed RATE|on|off (extruder feeder, spray rate, or the
encapsulator's feed pressure), air FLOW|on|off (fluidising air), hv VOLTAGE|on|off (high voltage / electrostatic dispersion),
humidity RH|on|off (spinning chamber climate), prime (cartridge priming, a job), clean [--head N] (needle or filter cleaning),
method KEY=VALUE ..., start / stop / result / wait, status.  power, open / close (barrel, door, lid, block, tower), ack and estop
come from the shared layer.

Method keys (class dictionary): time, speed, feed, die-radius, die-length, mixing (extruder); voltage, flows, rpm, needle-low,
needle-high, gap (electrospinning); volume, flow (encapsulator target volume / flow; nanoparticle total flow and volume), ratio,
dilution, waste-start, waste-end (nanoparticles); operation, spray-position, air, temp, spray, spray-time (fluid bed).
"""
from .reactor_common import SetpointAdapter

REFS = {   # manual page references per member (Chemistry-Sim/references/manuals/<slug>/manifest.json key sections; no manual text)
    'thermo_process11': 'Process 11 operating manual pp.29-40 (operational sequence), pp.87-98 (temperature input, start-up), pp.99-120 '
                        '(feeder, temperature profile, recipes); spec sheet p.2 (10-1000 rpm, RT-350 °C)',
    'linari_rtadvanced': 'RT Advanced product page: architecture (two 60 kV groups, needle cleaning), technical specifications (RH 10-70 %, '
                         'up to 50 °C, 0-5000 rpm), safety (chamber blocked while high voltage is present)',
    'buchi_encapsulatorb': 'Encapsulator B-390 operation manual p.15 (technical data), pp.16-18 (vibration, electrostatic dispersion), '
                           'pp.31-34 (operation, manual air pressure), pp.35-45 (practice runs)',
    'cytiva_nanoassemblrignite': 'NanoAssemblr Ignite operating instructions pp.55-74 (flow rate ratio, total flow, waste), pp.75-104 '
                                 '(preheat, prepare cartridge, load syringes, run)',
    'glatt_miniglatt': 'Mini-/Midi-Glatt brochure pp.2-5 (processes, top / bottom spray, technical data); Glatt laboratory units brochure pp.6-7',
}

KEYS = {
    'time': ('duration', 'run time'), 'speed': ('rotation', 'screw speed, e.g. 200rpm'), 'feed': ('mass_flow', 'feed rate, e.g. 0.5g/min'),
    'die-radius': ('length', 'die radius'), 'die-length': ('length', 'die length'), 'mixing': (None, 'mixing factor'),
    'voltage': ('voltage', 'needle voltages per group, comma separated (15kV,15kV)'), 'flows': ('flow', 'pump flows, 8 values comma separated'),
    'rpm': ('rotation', 'collector speed'), 'needle-low': ('length', 'needle travel low ends'), 'needle-high': ('length', 'needle travel high ends'),
    'gap': ('length', 'needle-collector gaps'), 'volume': ('volume', 'target / total volume'), 'flow': ('flow', 'target / total flow'),
    'ratio': (None, 'flow rate ratio aqueous : organic'), 'dilution': (None, 'dilution ratio'), 'waste-start': ('volume', 'start waste volume'),
    'waste-end': ('volume', 'end waste volume'), 'operation': (None, 'drying, granulation or coating'), 'spray-position': (None, 'top or bottom'),
    'air': ('flow', 'fluidising air (normal)'), 'temp': ('temperature', 'inlet air temperature'), 'spray': ('flow', 'spray rate'),
    'spray-time': ('duration', 'spray time'),
}


class FormulationAdapter(SetpointAdapter):
    CLASS = 'formulation'
    TITLE = 'formulation and particle / fibre processing'
    ABOUT = ('Make particles, fibres, beads or extrudate: heat the barrel / chamber / nozzle / inlet air (per zone or channel), set the '
             'process knobs (screw speed, nozzle vibration, feed, fluidising air, high voltage, chamber humidity), prime or clean, '
             'set a recipe with class keys, and start / stop / result with job ids.')
    STANDARDS = 'MTP / VDI 2658 and ISA-88 (procedure states start / stop / hold); OPC UA LADS functional units; no class-specific command standard'
    ALWAYS = ('status', 'wait')
    UNIT_KINDS = ('temperature', 'temperature_delta', 'duration', 'rotation', 'frequency', 'mass_flow', 'flow', 'pressure', 'voltage',
                  'fraction', 'volume', 'length')
    PATH_GROUPS = (('start', 'stop', 'result'),)
    KEYS = KEYS
    VIEW = (('temp', 'temperature', 'temperature'), ('temp2', 'bed', 'temperature'), ('set', 'set point', 'temperature'),
            ('heat', 'heating', 'bool'), ('speed', 'screws', 'rotation'), ('drive', 'drive', 'bool'), ('torque', 'torque per shaft (N·m)', 'plain'),
            ('die_pressure', 'die pressure', 'pressure'), ('feed_mass', 'feed', 'mass_flow'), ('feed_flow', 'spray', 'flow'),
            ('feed_pressure', 'feed pressure', 'pressure'), ('flow', 'total flow', 'flow'), ('air', 'air', 'flow'), ('air_on', 'air', 'bool'), ('hv', 'high voltage', 'bool'),
            ('voltage', 'voltage', 'voltage'), ('vibration', 'vibration', 'frequency'), ('vib_on', 'vibration', 'bool'),
            ('humidity', 'humidity', 'fraction'), ('climate', 'climate control', 'bool'), ('fluidized', 'bed fluidised', 'bool'),
            ('phase', 'run', 'text'), ('stage', 'stage', 'text'), ('fault', 'fault', 'text'), ('warning', 'warning', 'text'),
            ('cartridge', 'cartridge', 'text'))
    CODES = {
        'COLD_MATERIAL': ('COLD_BARREL', 'a barrel zone of {inst} is below the material\'s processing temperature.',
                          'heat every zone to the processing temperature first: `chemlab run {inst} set-temp 180C --zone N` for each zone, '
                          '`chemlab run {inst} heat on`, `chemlab run {inst} wait-temp`.'),
        'RESIDUAL_ENERGY': ('RESIDUAL_ENERGY', 'the high voltage of {inst} is on or not yet discharged.',
                            'switch it off (`chemlab run {inst} hv off`), wait a few seconds, then retry.'),
        'SYRINGE_EMPTY': ('SYRINGE_EMPTY', 'a syringe of {inst} does not hold enough liquid for this run.', 'ask the lab operator to load more, or reduce the volume.'),
        'TUBE_CAPACITY': ('TUBE_FULL', 'the collection tubes of {inst} cannot take this run.', 'ask the lab operator to change the tubes.'),
        'RESET_REQUIRED': ('RESET_REQUIRED', 'the pushers of {inst} are not at home after the last run.', 'reset it: `chemlab call {inst} cycle.reset`.'),
        'CARTRIDGE_TOPOLOGY': ('WRONG_CARTRIDGE', 'the fitted cartridge of {inst} has no dilution channel.', 'set dilution=0 in the method.'),
        'BUSY': ('BUSY', '{inst} is running and takes no new settings now.', 'wait for the run (`chemlab run {inst} wait`) or stop it.'),
    }

    def build_verbs(self):
        REF = REFS.get(self.asset)
        out = self.temp_verbs(REF, 'start or stop heating (barrel, chamber climate, nozzle heater, inlet air)')
        out['speed'] = self.knob_verb('speed', 'set the screw speed (on / off runs or stops the screws)', 'rotation', 'speed, e.g. 200rpm, or on / off',
                                      example='chemlab run INST speed 200rpm', reference=REF)
        out['vibration'] = self.knob_verb('vibration', 'set the nozzle vibration frequency (on / off switches it)', 'frequency',
                                          'frequency, e.g. 1000Hz, or on / off', example='chemlab run INST vibration 1000Hz', reference=REF)
        out['feed'] = self.knob_verb('feed', 'set the feed (extruder feeder, spray rate, or the encapsulator\'s feed pressure); on / off', 'flow',
                                     'rate (g/min, mL/min) or pressure (mbar), or on / off', example='chemlab run INST feed 0.5g/min', reference=REF,
                                     details='the unit depends on the instrument: a mass rate for the extruder feeder (g/min), a volume rate for '
                                             'the fluid-bed spray (mL/min), a gauge pressure for the encapsulator\'s pressure-driven feed (mbar).')
        out['air'] = self.knob_verb('air', 'set the fluidising air flow (on / off switches the air)', 'flow', 'flow, e.g. 240L/min, or on / off',
                                    example='chemlab run INST air on', reference=REF)
        out['hv'] = self.knob_verb('hv', 'set or switch the high voltage (electrospinning, electrostatic dispersion)', 'voltage',
                                   'voltage, e.g. 1500V, or on / off', example='chemlab run INST hv on', reference=REF)
        out['humidity'] = self.knob_verb('humidity', 'set the relative humidity of the spinning chamber (on / off switches the climate control)',
                                         'fraction', 'relative humidity, e.g. 40%, or on / off', example='chemlab run INST humidity 40%', reference=REF)
        out['prime'] = self.action_verb('prime', 'prime the cartridge / fluid path (a job)', reference=REF)
        out['clean'] = self.action_verb('clean', 'clean the needle tips (--head N) or blow back the filter', reference=REF)
        out['method'] = self.method_verb(REF)
        out.update(self.run_verbs(REF))
        return out
